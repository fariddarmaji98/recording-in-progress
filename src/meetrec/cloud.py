"""rclone: find it or install it, and keep meetrec's own list of cloud accounts ("remotes").

meetrec uses its OWN rclone config file (~/.meetrec/rclone.conf, owner-only), never ~/.config/rclone, so it neither
reads nor changes accounts you made yourself. Everything here is plain Python (no Qt) so it can be tested alone.

Paths, URLs and the config file are read from this module's globals at call time, so tests can point them elsewhere.
"""
from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import certifi

HOME = Path.home() / ".meetrec"
BIN_DIR = HOME / "bin"
CONFIG = HOME / "rclone.conf"
BASE_URL = "https://downloads.rclone.org"
EXE = "rclone.exe" if sys.platform == "win32" else "rclone"
_NO_WINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # no console flash from the GUI


# --- finding and installing rclone -------------------------------------------------------------------
def find_rclone() -> Path | None:
    """The copy meetrec installed, else one on PATH or in the usual install folders."""
    managed = BIN_DIR / EXE
    if managed.is_file() and os.access(managed, os.X_OK):
        return managed
    found = shutil.which("rclone")
    if found:
        return Path(found)
    extra = {"darwin": ["/opt/homebrew/bin", "/usr/local/bin"], "linux": ["/usr/local/bin", "/usr/bin", str(Path.home() / ".local/bin")]}
    for d in extra.get(sys.platform, []):  # a Finder-launched app does not inherit the shell's PATH
        p = Path(d) / EXE
        if p.is_file() and os.access(p, os.X_OK):
            return p
    return None


def rclone_version(exe: Path) -> str | None:
    """"v1.75.2", or None when the file does not run."""
    try:
        r = subprocess.run([str(exe), "version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, **_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"rclone (v[\w.\-+]+)", r.stdout or "")
    return m.group(1) if r.returncode == 0 and m else None


def platform_tag() -> str | None:
    """rclone's name for this OS and CPU ("osx-arm64"), or None when there is no official build we install."""
    os_name = {"Darwin": "osx", "Windows": "windows", "Linux": "linux"}.get(platform.system())
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64", "amd64": "amd64"}.get(platform.machine().lower())
    return f"{os_name}-{arch}" if os_name and arch else None


USER_AGENT = "meetrec (+https://github.com/fariddarmaji98/recording-in-progress)"


def _open(url: str):
    ctx = ssl.create_default_context(cafile=certifi.where())  # python.org builds on macOS ship without CA certs
    # downloads.rclone.org sits behind Cloudflare, which answers 403 to urllib's default "Python-urllib/3.x" agent
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, context=ctx, timeout=60)


def _parse_sums(text: str) -> dict[str, str]:
    """SHA256SUMS is PGP clear-signed: take only the '<64 hex>  <file>' lines."""
    return {m.group(2): m.group(1) for m in re.finditer(r"^([0-9a-f]{64})\s+(\S+)$", text, re.M)}


def install_rclone(progress: Callable[[float], None] | None = None) -> Path:
    """Download the official rclone zip, check its SHA-256 against the published SHA256SUMS, and put the program in
    ~/.meetrec/bin. No administrator rights. Raises RuntimeError with a readable message on any failure."""
    tag = platform_tag()
    if not tag:
        raise RuntimeError(f"Tidak ada build rclone resmi untuk {platform.system()} {platform.machine()}: pasang manual dari rclone.org")
    try:
        with _open(f"{BASE_URL}/version.txt") as r:
            ver = r.read().decode().split()[-1]  # "rclone v1.75.2"
        if not re.fullmatch(r"v\d+\.\d+\.\d+", ver):
            raise RuntimeError(f"versi rclone tidak dikenali: {ver!r}")
        name = f"rclone-{ver}-{tag}.zip"
        with _open(f"{BASE_URL}/{ver}/SHA256SUMS") as r:
            want = _parse_sums(r.read().decode("utf-8", "replace")).get(name)
        if not want:
            raise RuntimeError(f"{name} tidak ada di SHA256SUMS")
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        part = BIN_DIR / (name + ".part")
        h = hashlib.sha256()
        try:
            with _open(f"{BASE_URL}/{ver}/{name}") as r, open(part, "wb") as f:
                total, got = int(r.headers.get("Content-Length") or 0), 0
                while chunk := r.read(256 * 1024):
                    f.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    if progress and total:
                        progress(min(1.0, got / total))
            if h.hexdigest() != want:
                raise RuntimeError("Checksum rclone tidak cocok dengan SHA256SUMS: unduhan dibuang")
            tmp = BIN_DIR / (EXE + ".new")
            with zipfile.ZipFile(part) as z:
                member = next((m for m in z.namelist() if m.rsplit("/", 1)[-1] == EXE), None)
                if member is None:
                    raise RuntimeError(f"{EXE} tidak ada di dalam {name}")
                with z.open(member) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            tmp.chmod(0o755)
            if not rclone_version(tmp):
                tmp.unlink(missing_ok=True)
                raise RuntimeError("rclone hasil unduhan tidak bisa dijalankan di komputer ini")
            os.replace(tmp, BIN_DIR / EXE)
        finally:
            part.unlink(missing_ok=True)
    except RuntimeError:
        raise
    except OSError as e:  # URLError, timeouts and disk errors are all OSError
        raise RuntimeError(f"Gagal mengunduh rclone: {e}") from e
    if progress:
        progress(1.0)
    return BIN_DIR / EXE


# --- what can be connected ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Field:
    key: str                      # rclone option name
    label: str
    secret: bool = False
    required: bool = True
    default: str = ""
    choices: tuple[str, ...] = ()  # non-empty: pick from a list


@dataclass(frozen=True)
class Provider:
    key: str                      # also the default account name
    title: str
    backend: str                  # rclone backend type
    auth: str                     # "oauth": rclone opens the browser; "keys": you type the keys
    fields: tuple[Field, ...] = ()


PROVIDERS = [
    Provider("gdrive", "Google Drive", "drive", "oauth"),
    Provider("dropbox", "Dropbox", "dropbox", "oauth"),
    Provider("onedrive", "OneDrive", "onedrive", "oauth"),
    Provider("box", "Box", "box", "oauth"),
    Provider("pcloud", "pCloud", "pcloud", "oauth"),
    Provider("s3", "Amazon S3 / kompatibel S3", "s3", "keys", (
        Field("provider", "Penyedia", choices=("AWS", "Cloudflare", "Wasabi", "Minio", "Other"), default="AWS"),
        Field("access_key_id", "Access key ID"),
        Field("secret_access_key", "Secret access key", secret=True),
        Field("region", "Region", required=False, default="us-east-1"),
        Field("endpoint", "Endpoint untuk selain AWS", required=False),
    )),
    Provider("b2", "Backblaze B2", "b2", "keys", (
        Field("account", "Application key ID"),
        Field("key", "Application key", secret=True),
    )),
]


def provider(key: str) -> Provider:
    return next(p for p in PROVIDERS if p.key == key)


# --- meetrec's own rclone.conf -----------------------------------------------------------------------
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+@-]{0,39}")


def valid_name(name: str) -> bool:
    return bool(_NAME.fullmatch(name))


def read_remotes() -> dict[str, str]:
    """{account name: backend type}, read straight from the file (secrets never leave this function)."""
    out: dict[str, str] = {}
    cur = None
    try:
        lines = CONFIG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]
            out[cur] = ""
        elif cur is not None and re.match(r"type\s*=", s):
            out[cur] = s.split("=", 1)[1].strip()
    return out


def _write_private(text: str) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_name(CONFIG.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, CONFIG)


def _without(lines: list[str], name: str) -> list[str]:
    out, skipping = [], False
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            skipping = s[1:-1] == name
        if not skipping:
            out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return out


def _lines() -> list[str]:
    try:
        return CONFIG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def write_remote(name: str, values: dict[str, str]) -> None:
    """Add or replace one [name] section; every other line of the file stays exactly as it was."""
    if not valid_name(name):
        raise ValueError("Nama akun: huruf/angka/_ . + @ -, maksimal 40 karakter, tidak boleh diawali tanda")
    if any(("\n" in v or "\r" in v) for v in values.values()):
        raise ValueError("Isian tidak boleh berisi baris baru")
    lines = _without(_lines(), name)
    if lines:
        lines.append("")
    lines += [f"[{name}]"] + [f"{k} = {v}" for k, v in values.items()]
    _write_private("\n".join(lines) + "\n")


def remove_remote(name: str) -> None:
    _write_private("\n".join(_without(_lines(), name)) + "\n")


def save_keys(name: str, prov: Provider, values: dict[str, str]) -> None:
    """Store an account that is made of keys (S3, B2): checked, written to rclone.conf with owner-only permissions."""
    missing = [f.label for f in prov.fields if f.required and not values.get(f.key, "").strip()]
    if missing:
        raise ValueError("Isi dulu: " + ", ".join(missing))
    write_remote(name, {"type": prov.backend, **{f.key: values[f.key].strip() for f in prov.fields if values.get(f.key, "").strip()}})


# --- talking to rclone -------------------------------------------------------------------------------
def _tail(text: str, n: int = 3) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return " | ".join(lines[-n:]) or "rclone gagal tanpa pesan"


class OAuthLogin:
    """`rclone config create <name> <backend>`: rclone opens the browser, waits for you to allow access, and writes the
    token into meetrec's rclone.conf. run() blocks until it ends (put it in a Job); cancel() stops it."""

    def __init__(self, exe: Path, name: str, backend: str, timeout: int = 300):
        self.exe, self.name, self.backend, self.timeout = exe, name, backend, timeout
        self._proc: subprocess.Popen | None = None
        self._cancelled = False

    def run(self) -> None:
        if not valid_name(self.name):
            raise RuntimeError("Nama akun tidak valid")
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        self._proc = subprocess.Popen([str(self.exe), "--config", str(CONFIG), "config", "create", self.name, self.backend],
                                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **_NO_WINDOW)
        try:
            out, _ = self._proc.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.communicate()
            raise RuntimeError(f"Login habis waktu ({self.timeout // 60} menit). Coba lagi.")
        if self._cancelled:
            raise RuntimeError("Login dibatalkan")
        if self._proc.returncode != 0:
            raise RuntimeError(_tail(out))
        if self.name not in read_remotes():
            raise RuntimeError("rclone selesai tetapi akun tidak tersimpan")
        _write_private("\n".join(_lines()) + "\n")  # owner-only permissions on the file rclone wrote

    def cancel(self) -> None:
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


def test_remote(exe: Path, name: str, timeout: int = 180) -> str:
    """List the top level of the account. Returns a short success text; raises RuntimeError with rclone's own message.

    The text reports how many retries it took, because a Drive that is throttling still "works" — the
    difference between 1 attempt and 9 is the difference between a healthy account and a rate-limited one.
    """
    started = time.monotonic()
    try:
        # default low-level retries (10): Drive throttles even an `lsd`, and rclone backs off properly.
        # -v is needed only so the pacer's "low level retry N/10" lines reach stderr and can be counted.
        r = subprocess.run([str(exe), "--config", str(CONFIG), "lsd", f"{name}:", "--max-depth", "1",
                            "--contimeout", "15s", "--timeout", "30s", "-v"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, **_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Tidak ada jawaban dalam %d detik" % timeout)
    took = time.monotonic() - started
    tries = _retries(r.stderr or "")
    if r.returncode != 0:
        raise RuntimeError(f"{_tail(r.stderr or r.stdout)} (setelah {_tries_text(tries)}, {took:.1f} detik)")
    n = len([ln for ln in r.stdout.splitlines() if ln.strip()])
    return f"Tersambung ({n} folder/bucket di level atas, {_tries_text(tries)}, {took:.1f} detik)"


_RETRY_LINE = re.compile(r"low level retry (\d+)/\d+")
_OP_RETRY_LINE = re.compile(r"with (\d+) errors:")


def _retries(log: str) -> int:
    """How many retries rclone logged (0 = first try worked).

    `low level retry N/M` is the pacer backing off one request (what a throttled Drive produces);
    `with N errors:` is a whole-operation retry. Both counted, whichever is bigger.
    """
    low = max((int(m.group(1)) for m in _RETRY_LINE.finditer(log)), default=0)
    op = max((int(m.group(1)) for m in _OP_RETRY_LINE.finditer(log)), default=0)
    return max(low, op)


def _tries_text(n: int) -> str:
    return "tanpa coba ulang" if n == 0 else f"{n} kali coba ulang"

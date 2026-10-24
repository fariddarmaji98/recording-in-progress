"""Uploading a recording folder to the chosen cloud account with rclone (and downloading one back), with progress,
pause/resume and cancel.

Where it goes: the account picked in Pengaturan > Cloud (QSettings "cloud_account") and that account's folder
("cloud_folder/<account>", default "meetrec"). A recording lands in <account>:<folder>/<recording name>/.
`rclone copy` skips what is already there with the same size and time, so uploading again is safe.
Download is the same command the other way round: <account>:<folder>/<name> -> <recordings folder>/<name>.
Pause is the rclone process being suspended (SIGSTOP on macOS/Linux, NtSuspendProcess on Windows) and resumed.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Signal

from . import cloud

DEFAULT_FOLDER = "meetrec"
ACTIVE = ("running", "paused")


# --- what is chosen -------------------------------------------------------------------------------------
def clean_folder(text: str) -> str:
    """"Rapat/2026" style path inside the cloud account. Empty = the top level. Raises ValueError when unusable."""
    parts = [p.strip() for p in text.replace("\\", "/").split("/")]
    parts = [p for p in parts if p and p != "."]
    if any(p == ".." or ":" in p or any(ord(c) < 32 for c in p) for p in parts):
        raise ValueError("Nama folder tidak boleh memuat '..', ':' atau karakter kontrol")
    out = "/".join(parts)
    if len(out) > 200:
        raise ValueError("Nama folder terlalu panjang (maksimal 200 karakter)")
    return out


def selected_account(settings: QSettings) -> str:
    return str(settings.value("cloud_account", "") or "")


def set_selected(settings: QSettings, name: str) -> None:
    settings.setValue("cloud_account", name)


def folder_for(settings: QSettings, account: str) -> str:
    v = settings.value(f"cloud_folder/{account}", None)
    return DEFAULT_FOLDER if v is None else str(v)


def set_folder(settings: QSettings, account: str, folder: str) -> None:
    settings.setValue(f"cloud_folder/{account}", folder)


@dataclass(frozen=True)
class Target:
    account: str
    folder: str

    def remote(self, name: str) -> str:
        return f"{self.account}:{self.folder + '/' if self.folder else ''}{name}"


# --- one upload -----------------------------------------------------------------------------------------
def _suspend(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        import ctypes

        h = ctypes.windll.kernel32.OpenProcess(0x0800, False, proc.pid)  # PROCESS_SUSPEND_RESUME
        ctypes.windll.ntdll.NtSuspendProcess(h)
        ctypes.windll.kernel32.CloseHandle(h)
    else:
        os.kill(proc.pid, signal.SIGSTOP)


def _resume(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        import ctypes

        h = ctypes.windll.kernel32.OpenProcess(0x0800, False, proc.pid)
        ctypes.windll.ntdll.NtResumeProcess(h)
        ctypes.windll.kernel32.CloseHandle(h)
    else:
        os.kill(proc.pid, signal.SIGCONT)


class Upload(QObject):
    """state: running | paused | done | error | cancelled. `changed` fires on every state or progress change."""

    changed = Signal()

    def __init__(self, exe: Path, folder: Path, remote: str, extra_args: list[str] | None = None, download: bool = False):
        super().__init__()
        self.folder, self.remote, self.download = Path(folder), remote, download
        self.state, self.progress, self.speed, self.message = "running", 0.0, 0.0, ""
        src, dst = (remote, str(self.folder)) if download else (str(self.folder), remote)
        self._cmd = [str(exe), "--config", str(cloud.CONFIG), "copy", src, dst, "--use-json-log", "--stats", "1s", "-v",
                     *(extra_args or [])]
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._proc = subprocess.Popen(self._cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                      **cloud._NO_WINDOW)
        threading.Thread(target=self._read, daemon=True, name="meetrec-upload").start()

    def _set(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)
        self.changed.emit()

    def _read(self) -> None:
        proc, last_error = self._proc, ""
        for line in proc.stdout:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            st = o.get("stats")
            if st:
                total = st.get("totalBytes") or 0
                if total and self.state in ACTIVE:
                    self._set(progress=min(0.999, st.get("bytes", 0) / total), speed=float(st.get("speed") or 0))
            elif o.get("level") in ("error", "critical"):
                last_error = str(o.get("msg", "")).strip().splitlines()[0][:300] if o.get("msg") else last_error
        rc = proc.wait()
        if self.state == "cancelled":
            self._set()
        elif rc == 0:
            self._set(state="done", progress=1.0, speed=0.0, message="")
        else:
            self._set(state="error", speed=0.0, message=last_error or f"rclone berhenti dengan kode {rc}")

    def pause(self) -> None:
        if self.state == "running" and self._proc and self._proc.poll() is None:
            _suspend(self._proc)
            self._set(state="paused", speed=0.0)

    def resume(self) -> None:
        if self.state == "paused" and self._proc and self._proc.poll() is None:
            _resume(self._proc)
            self._set(state="running")

    def cancel(self) -> None:
        if self.state not in ACTIVE:
            return
        self.state = "cancelled"  # set first: the reader thread must not report this as an error
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                _resume(proc)  # a stopped process cannot act on SIGTERM
            except OSError:
                pass
            proc.terminate()
            threading.Timer(3.0, lambda: proc.poll() is None and proc.kill()).start()
        self.changed.emit()


# --- all uploads of the app -----------------------------------------------------------------------------
class UploadManager(QObject):
    """Lives as long as the app (owned by the panel), so uploads go on while windows open and close."""

    changed = Signal(str)           # recording name: an upload changed
    download_changed = Signal(str)  # recording name: a download changed

    def __init__(self, settings: QSettings):
        super().__init__()
        self.settings = settings
        self.extra_args: list[str] = []  # tests slow rclone down with these
        self._uploads: dict[str, Upload] = {}
        self._downloads: dict[str, Upload] = {}

    def target(self) -> Target | None:
        acct = selected_account(self.settings)
        if acct and acct in cloud.read_remotes():
            return Target(acct, folder_for(self.settings, acct))
        return None

    def get(self, name: str) -> Upload | None:
        return self._uploads.get(name)

    def active(self) -> list[Upload]:
        return [u for u in self._uploads.values() if u.state in ACTIVE]

    def busy(self) -> list[Upload]:
        """Uploads AND downloads that are running or paused (what quitting the app would throw away)."""
        return self.active() + [u for u in self._downloads.values() if u.state in ACTIVE]

    def start(self, folder: Path) -> str:
        """Begin uploading one recording folder. Returns "" or the reason it did not start."""
        folder = Path(folder)
        cur = self._uploads.get(folder.name)
        if cur and cur.state in ACTIVE:
            return ""
        target = self.target()
        if target is None:
            return "Belum ada tujuan upload. Buka ⚙ Pengaturan > Cloud, pilih satu akun, lalu klik tombol centangnya."
        exe = cloud.find_rclone()
        if exe is None:
            return "rclone belum terpasang. Pasang di ⚙ Pengaturan > Cloud > Setup rclone."
        up = Upload(exe, folder, target.remote(folder.name), self.extra_args)
        up.changed.connect(lambda n=folder.name: self.changed.emit(n))
        self._uploads[folder.name] = up
        try:
            up.start()
        except OSError as e:
            del self._uploads[folder.name]
            return f"rclone tidak bisa dijalankan: {e}"
        self.changed.emit(folder.name)
        return ""

    # downloads --------------------------------------------------------------------------------------
    def get_download(self, name: str) -> Upload | None:
        return self._downloads.get(name)

    def start_download(self, name: str, root: Path) -> str:
        """Fetch <account>:<folder>/<name> into <root>/<name>. Returns "" or the reason it did not start."""
        if not safe_name(name):
            return f"Nama rekaman tidak aman untuk dibuat di komputer ini: {name!r}"
        cur = self._downloads.get(name)
        if cur and cur.state in ACTIVE:
            return ""
        target = self.target()
        if target is None:
            return "Belum ada tujuan cloud. Buka ⚙ Pengaturan > Cloud, pilih satu akun, lalu klik tombol centangnya."
        exe = cloud.find_rclone()
        if exe is None:
            return "rclone belum terpasang. Pasang di ⚙ Pengaturan > Cloud > Setup rclone."
        up = Upload(exe, Path(root) / name, target.remote(name), self.extra_args, download=True)
        up.changed.connect(lambda n=name: self.download_changed.emit(n))
        self._downloads[name] = up
        try:
            up.start()
        except OSError as e:
            del self._downloads[name]
            return f"rclone tidak bisa dijalankan: {e}"
        self.download_changed.emit(name)
        return ""

    def toggle_pause_download(self, name: str) -> None:
        up = self._downloads.get(name)
        if up:
            up.pause() if up.state == "running" else up.resume()

    def cancel_download(self, name: str) -> None:
        up = self._downloads.pop(name, None)
        if up:
            up.cancel()
            self.download_changed.emit(name)

    def toggle_pause(self, name: str) -> None:
        up = self._uploads.get(name)
        if up:
            up.pause() if up.state == "running" else up.resume()

    def cancel(self, name: str) -> None:
        up = self._uploads.pop(name, None)
        if up:
            up.cancel()
            self.changed.emit(name)

    def cancel_all(self) -> None:
        """Stop every upload and forget the progress (the destination changed)."""
        for name in list(self._uploads):
            self.cancel(name)

    def cancel_everything(self) -> None:
        """Uploads and downloads (the app is closing)."""
        self.cancel_all()
        for name in list(self._downloads):
            self.cancel_download(name)


_SAFE = __import__("re").compile(r"[A-Za-z0-9_][A-Za-z0-9_.+@ -]{0,99}")


def safe_name(name: str) -> bool:
    """A recording folder name from the cloud that is safe to create under the recordings folder."""
    return bool(_SAFE.fullmatch(name)) and ".." not in name


def list_cloud_recordings(exe: Path, target: Target, timeout: int = 240) -> list[dict]:
    """Recording folders found in <account>:<folder>: [{name, size, files, modified}], newest name first.
    A missing cloud folder is an empty list (nothing uploaded yet); anything else wrong raises RuntimeError."""
    remote = f"{target.account}:{target.folder}"
    try:
        # rclone's own default retries only: a recursive walk of a Drive folder regularly answers
        # `rateLimitExceeded`, which rclone retries with the pacer's exponential backoff. Forcing
        # --retries 1 / --low-level-retries 2 turned one throttled response into a hard failure.
        r = subprocess.run([str(exe), "--config", str(cloud.CONFIG), "lsjson", remote, "--recursive", "--files-only",
                            "--contimeout", "15s", "--timeout", "30s"],
                           capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, **cloud._NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Tidak ada jawaban dari cloud dalam {timeout} detik")
    if r.returncode == 3 or "directory not found" in (r.stderr or "").lower():
        return []
    if r.returncode != 0:
        raise RuntimeError(cloud._tail(r.stderr or r.stdout))
    try:
        items = json.loads(r.stdout or "[]")
    except ValueError:
        raise RuntimeError("Jawaban rclone tidak bisa dibaca")
    groups: dict[str, dict] = {}
    for it in items:
        path = str(it.get("Path", ""))
        if "/" not in path:
            continue  # a loose file in the folder is not a recording
        name = path.split("/", 1)[0]
        if not safe_name(name):
            continue
        g = groups.setdefault(name, {"name": name, "size": 0, "files": 0, "modified": ""})
        g["size"] += max(0, int(it.get("Size", 0)))
        g["files"] += 1
        g["modified"] = max(g["modified"], str(it.get("ModTime", "")))
    return sorted(groups.values(), key=lambda g: g["name"], reverse=True)

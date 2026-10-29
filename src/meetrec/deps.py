"""Checks that what each feature needs is installed, and installs what is missing.

`ensure("record")` is called before a feature starts. When everything is there it costs a few `find_spec` calls.
When something is missing it runs `pip install` with the interpreter that runs meetrec, then checks again.

macOS has one piece pip can leave out silently: catap's native Core Audio library (libcatap_coreaudio.dylib).
It only ships in catap's wheel. If pip builds catap from source instead, the package imports fine but recording
fails when it opens the system-audio tap. `_catap_native()` loads that library, the same way catap does.

The packaged app (PyInstaller) cannot pip install; there `ensure` only reports what is missing.
"""
from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Callable

MAC = sys.platform == "darwin"
FROZEN = bool(getattr(sys, "frozen", False))
PIP_TIMEOUT = 900  # seconds; PySide6 and sherpa-onnx are large


@dataclass(frozen=True)
class Dep:
    module: str          # import name
    pip: str             # requirement as pip wants it (keep in step with pyproject.toml)
    platform: str = ""   # sys.platform prefix; empty = every platform


DEPS: dict[str, Dep] = {d.module: d for d in (
    Dep("numpy", "numpy"),
    Dep("soundfile", "soundfile"),
    Dep("soxr", "soxr"),
    Dep("certifi", "certifi"),
    Dep("soundcard", "soundcard"),
    Dep("catap", "catap", "darwin"),
    Dep("Quartz", "pyobjc-framework-Quartz", "darwin"),
    Dep("Xlib", "python-xlib", "linux"),
    Dep("mss", "mss"),
    Dep("av", "av<17"),
    Dep("imageio_ffmpeg", "imageio-ffmpeg"),
    Dep("PySide6", "PySide6"),
    Dep("mcp", "mcp"),
    Dep("sherpa_onnx", "sherpa-onnx"),
    Dep("faster_whisper", "faster-whisper"),
)}

# Feature -> modules it imports.
FEATURES: dict[str, tuple[str, ...]] = {
    "record": ("numpy", "soundcard", "catap", "Quartz", "Xlib", "soxr"),
    "screen": ("numpy", "mss", "av", "imageio_ffmpeg", "Quartz", "Xlib"),
    "analyze": ("numpy", "soundfile", "soxr", "sherpa_onnx", "certifi"),
    "transcribe": ("numpy", "soundfile", "soxr", "sherpa_onnx", "certifi", "faster_whisper"),
    "player": ("numpy", "PySide6", "av", "imageio_ffmpeg", "soundfile", "soxr"),
    "ui": ("numpy", "soundfile", "soxr", "certifi", "soundcard", "catap", "Quartz", "Xlib", "mss", "av",
           "imageio_ffmpeg", "PySide6", "mcp", "sherpa_onnx"),
}

CATAP_NATIVE = "catap-native"  # pseudo module name for the dylib check

_lock = threading.Lock()


class DepsError(RuntimeError):
    """A dependency is missing and could not be installed. The message says what to run by hand."""


@dataclass(frozen=True)
class Problem:
    name: str            # module name, or CATAP_NATIVE
    pip: str
    detail: str


def _applies(dep: Dep) -> bool:
    return not dep.platform or sys.platform.startswith(dep.platform)


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _catap_native() -> str:
    """'' when catap's Core Audio library loads, else why not."""
    try:
        from catap._native_coreaudio import load_native_coreaudio

        load_native_coreaudio()
        return ""
    except Exception as e:  # missing file, wrong architecture, ABI mismatch
        return str(e)


def find_problems(*features: str) -> list[Problem]:
    wanted: list[str] = []
    for f in features:
        for m in FEATURES[f]:
            if m not in wanted:
                wanted.append(m)
    out: list[Problem] = []
    for m in wanted:
        dep = DEPS[m]
        if _applies(dep) and not _has(m):
            out.append(Problem(m, dep.pip, "belum terpasang"))
    if MAC and "catap" in wanted and _has("catap"):
        why = _catap_native()
        if why:
            out.append(Problem(CATAP_NATIVE, "catap", f"library Core Audio native tidak bisa dimuat: {why}"))
    return out


def _pip(args: list[str], log: Callable[[str], None]) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *args]
    log("  $ " + " ".join(cmd[2:]))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=PIP_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"  pip gagal dijalankan: {e}")
        return False
    if p.returncode:
        tail = (p.stderr or p.stdout).strip().splitlines()[-6:]
        log("  pip gagal:\n    " + "\n    ".join(tail))
    return p.returncode == 0


def _ensure_pip(log: Callable[[str], None]) -> bool:
    if _has("pip"):
        return True
    log("  pip belum ada, memasang lewat ensurepip")
    try:
        r = subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"], capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return False
    importlib.invalidate_caches()
    return r.returncode == 0 and _has("pip")


def _manual_command(problems: list[Problem]) -> str:
    plain = sorted({p.pip for p in problems if p.name != CATAP_NATIVE})
    cmds = []
    if plain:
        cmds.append("pip install " + " ".join(f"'{s}'" for s in plain))
    if any(p.name == CATAP_NATIVE for p in problems):
        cmds.append("pip install --force-reinstall --no-deps --only-binary catap catap")
    return "; ".join(cmds)


def ensure(*features: str, install: bool = True, log: Callable[[str], None] | None = None) -> None:
    """Make sure `features` can run. Installs what is missing (unless install=False or the app is frozen)."""
    say = log or (lambda _m: None)
    with _lock:
        problems = find_problems(*features)
        if not problems:
            return
        for p in problems:
            say(f"Kurang: {p.name}: {p.detail}")
        if not install or FROZEN:
            why = "aplikasi ini sudah dibungkus, tidak bisa memasang paket sendiri; pasang ulang aplikasinya" if FROZEN \
                else "jalankan: " + _manual_command(problems)
            raise DepsError(f"Dependency kurang: {', '.join(p.name for p in problems)}. {why}")
        if not _ensure_pip(say):
            raise DepsError("pip tidak tersedia untuk Python ini. Jalankan: python3 -m ensurepip --upgrade")

        say("Memasang dependency yang kurang (sekali saja, bisa beberapa menit)...")
        plain = [p for p in problems if p.name != CATAP_NATIVE]
        if plain:
            args = sorted({p.pip for p in plain})
            if any(p.name == "catap" for p in plain):
                args = ["--only-binary", "catap", *args]  # the dylib only exists in the wheel
            _pip(args, say)
        importlib.invalidate_caches()

        left = find_problems(*features)
        if any(p.name == CATAP_NATIVE for p in left):
            say("Memasang ulang catap dari wheel supaya library Core Audio ikut terpasang...")
            _pip(["--force-reinstall", "--no-deps", "--only-binary", "catap", "catap"], say)
            for mod in [m for m in sys.modules if m == "catap" or m.startswith("catap.")]:
                del sys.modules[mod]
            importlib.invalidate_caches()
            left = find_problems(*features)
        if left:
            hint = ""
            if any(p.name == CATAP_NATIVE for p in left):
                hint = (" Rekam audio sistem butuh macOS 14.2+ dan Python arm64/universal2 untuk Mac Apple Silicon "
                        "(cek: python3 -c 'import platform; print(platform.machine())').")
            raise DepsError("Dependency masih kurang setelah dipasang: "
                            + "; ".join(f"{p.name} ({p.detail})" for p in left) + f". Coba manual: {_manual_command(left)}.{hint}")
        say("Dependency lengkap.")


def doctor(fix: bool, features: tuple[str, ...] | None = None) -> int:
    """Print what is present and, with fix=True, install what is not. Returns a process exit code."""
    feats = features or tuple(FEATURES)
    print(f"Python {sys.version.split()[0]} ({sys.platform}) di {sys.executable}")
    bad = find_problems(*feats)
    for f in feats:
        mods = [m for m in FEATURES[f] if _applies(DEPS[m])]
        missing = {p.name for p in bad}
        marks = [("OK  " if m not in missing else "KURANG ") + m for m in mods]
        print(f"[{f}] " + ", ".join(marks))
    for p in bad:
        if p.name == CATAP_NATIVE:
            print(f"KURANG {p.name}: {p.detail}")
    if not bad:
        print("Semua lengkap.")
        return 0
    if not fix:
        print("\nPerbaiki otomatis: meetrec doctor --fix")
        return 1
    try:
        ensure(*feats, log=print)
    except DepsError as e:
        print(f"GAGAL: {e}")
        return 1
    return 0

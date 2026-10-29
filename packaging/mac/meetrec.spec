# PyInstaller spec for the macOS app. Run it through build_mac.sh (it sets the MEETREC_* variables).
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
VERSION = os.environ["MEETREC_VERSION"]
ICON = os.environ.get("MEETREC_ICON") or None

datas = collect_data_files("faster_whisper")  # silero VAD model used by the transcript
binaries = []
for pkg in ("sherpa_onnx", "ctranslate2", "onnxruntime", "av", "soundcard", "catap"):  # catap: libcatap_coreaudio.dylib
    binaries += collect_dynamic_libs(pkg)

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=["_cffi_backend"],
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "IPython", "pytest", "PyQt5", "PyQt6",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.Qt3DCore"],
    noarchive=False,
)
# Qt pieces nothing in meetrec loads. PyInstaller pulls them in through plugins (PDF image format, virtual keyboard)
# whose libraries depend on QtPdf / QtQuick / QtQml; dropping the plugins drops about 19 MB. The check in
# build_mac.sh (--selftest, then the real window) proves the app does not need them.
# Also dropped: Qt's own translations (no QTranslator is installed) and image formats the app never reads
# (it uses JPEG for thumbnails, PNG and SVG for icons).
UNUSED = ("QtPdf", "QtQuick", "QtQml", "QtVirtualKeyboard", "libqtvirtualkeyboardplugin", "libqpdf",
          "Qt/translations", "libqmacjp2", "libqmacheif", "libqtiff", "libqwebp", "libqicns", "libqico", "libqtga",
          "libqwbmp", "libqgif")
a.binaries = [b for b in a.binaries if not any(u in b[0] for u in UNUSED)]
a.datas = [d for d in a.datas if not any(u in d[0] for u in UNUSED)]

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="meetrec", console=False, argv_emulation=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="meetrec")
app = BUNDLE(
    coll,
    name="meetrec.app",
    icon=ICON,
    bundle_identifier="com.meetrec.meetrec",
    version=VERSION,
    info_plist={
        "CFBundleName": "meetrec",
        "CFBundleDisplayName": "meetrec",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "14.2",  # Core Audio process tap (system audio) needs macOS 14.2
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": "meetrec merekam suara mikrofonmu ke file mic.wav di komputer ini.",
        "NSAudioCaptureUsageDescription": "meetrec merekam semua suara yang diputar komputer ini ke file system.wav di komputer ini.",
        "NSScreenCaptureUsageDescription": "meetrec merekam layar ke file video.mp4 di komputer ini.",
    },
)

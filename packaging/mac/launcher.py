"""Entry point of the macOS app (PyInstaller). Same as the `meetrec` command: no argument opens the panel.

`--selftest` checks that everything the packaged app needs is inside the bundle (used by build_mac.sh).
"""
import os
import sys


def selftest() -> int:
    import importlib

    problems = []
    for mod in ("meetrec.ui", "meetrec.player", "meetrec.timeline", "meetrec.transcript", "meetrec.fullmedia",
                "meetrec.meetbridge", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "sherpa_onnx", "soundcard",
                "catap", "Quartz", "objc", "mss", "av", "soxr", "soundfile", "certifi", "numpy", "faster_whisper", "ctranslate2"):
        try:
            importlib.import_module(mod)
        except Exception as e:  # report every missing piece, not only the first
            problems.append(f"import {mod}: {e!r}")
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if not os.access(exe, os.X_OK):
            problems.append(f"ffmpeg not executable: {exe}")
    except Exception as e:
        problems.append(f"ffmpeg: {e!r}")
    try:
        import certifi

        if not os.path.exists(certifi.where()):
            problems.append("certifi CA file missing")
        import faster_whisper

        silero = os.path.join(os.path.dirname(faster_whisper.__file__), "assets", "silero_vad_v6.onnx")
        if not os.path.exists(silero):
            problems.append(f"faster-whisper VAD model missing: {silero}")
    except Exception as e:
        problems.append(f"data files: {e!r}")
    for p in problems:
        print("SELFTEST FAIL", p)
    print("SELFTEST", "FAILED" if problems else "OK")
    return 1 if problems else 0


if __name__ == "__main__":
    import multiprocessing

    # In the packaged app a library's helper process (multiprocessing resource tracker) starts this same executable
    # with `-c ...`; freeze_support() answers that instead of letting the CLI parser reject it.
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(selftest())
    from meetrec.cli import main

    main()

"""Screen recording to video.mp4 (no audio; audio stays in mic.wav / system.wav).

mss grabs frames in a worker thread (no Qt, UI never blocks), PyAV encodes H.264.
Frame time = recorded time (pauses are cut out), the same convention as the wav tracks,
and offset_sec says when the first frame came after the session's t0.

Windows: GDI / Linux: X11 (Wayland shows black) / macOS: CoreGraphics via mss.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np


def screen_permission(request: bool = False) -> bool:
    """macOS needs Screen Recording permission, otherwise only the wallpaper is captured."""
    if sys.platform != "darwin":
        return True
    try:
        from Quartz import CGPreflightScreenCaptureAccess, CGRequestScreenCaptureAccess

        if CGPreflightScreenCaptureAccess():
            return True
        if request:
            CGRequestScreenCaptureAccess()
        return False
    except Exception:
        return True


def _encoders() -> list[str]:
    return (["h264_videotoolbox"] if sys.platform == "darwin" else []) + ["libx264", "libopenh264", "mpeg4"]


_ENCODER: str | None = None  # picked once per process; the panel pre-warms it at launch


def pick_encoder(w: int = 128, h: int = 128, fps: int = 8) -> str:
    """First encoder that really works on this machine (tried on one dummy frame)."""
    global _ENCODER
    if _ENCODER:
        return _ENCODER
    import av

    for name in _encoders():
        try:
            with tempfile.TemporaryDirectory() as d:
                c = av.open(f"{d}/probe.mp4", "w")
                s = c.add_stream(name, rate=fps)
                s.width, s.height, s.pix_fmt = w, h, "yuv420p"
                f = av.VideoFrame(w, h, "yuv420p")
                f.pts = 0
                for p in s.encode(f):
                    c.mux(p)
                for p in s.encode(None):
                    c.mux(p)
                c.close()
            _ENCODER = name
            return name
        except Exception:
            continue
    raise RuntimeError("tidak ada encoder H.264 yang bisa dipakai")


class ScreenRecorder(threading.Thread):
    def __init__(self, path: Path, t0: float, fps: int = 8, monitor: int = 1):
        super().__init__(daemon=True, name="rec-screen")
        self.path, self.t0, self.fps, self.monitor = Path(path), t0, fps, monitor
        self.ready = threading.Event()
        self.halt = threading.Event()
        self.paused = threading.Event()
        self.offset = 0.0
        self.frames = 0
        self.width = self.height = 0
        self.codec = ""
        self.error: str | None = None
        self._override: np.ndarray | None = None
        self._lock = threading.Lock()

    # --- control (any thread) -------------------------------------------------------------------
    def pause(self) -> None:
        self.paused.set()

    def resume(self) -> None:
        self.paused.clear()

    def set_override(self, bgra: np.ndarray | None) -> None:
        """Encode this image instead of grabbing the screen (used while the screen is frozen for drawing,
        so the floating panel is not in the video). None = grab the real screen again."""
        with self._lock:
            self._override = None if bgra is None else np.ascontiguousarray(bgra)

    def stop(self, timeout: float = 15.0) -> None:
        self.halt.set()
        if self.is_alive():
            self.join(timeout)

    def info(self) -> dict:
        return {"file": self.path.name, "offset_sec": round(self.offset, 3), "fps": self.fps,
                "width": self.width, "height": self.height, "frames": self.frames,
                "codec": self.codec, "error": self.error}

    # --- worker ---------------------------------------------------------------------------------
    def _next_frame(self, sct, mon) -> np.ndarray:
        with self._lock:
            ov = self._override
        if ov is not None:
            return ov
        shot = sct.grab(mon)
        return np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)

    def run(self) -> None:
        container = None
        try:
            import av
            import mss

            MSS = getattr(mss, "MSS", None) or mss.mss  # mss.mss is deprecated from 10.2
            with MSS() as sct:
                mon = sct.monitors[self.monitor]
                first_img = sct.grab(mon)
                w, h = first_img.width & ~1, first_img.height & ~1  # H.264 wants even sizes
                self.width, self.height = w, h
                self.codec = pick_encoder(w, h, self.fps)
                container = av.open(str(self.path), "w")
                stream = container.add_stream(self.codec, rate=self.fps)
                stream.width, stream.height, stream.pix_fmt = w, h, "yuv420p"
                if self.codec == "libx264":
                    stream.options = {"preset": "ultrafast", "crf": "30"}
                else:
                    stream.bit_rate = 3_000_000

                first = time.monotonic()
                self.offset = first - self.t0
                self.ready.set()
                paused_acc, last = 0.0, -1
                while not self.halt.is_set():
                    if self.paused.is_set():
                        began = time.monotonic()
                        while self.paused.is_set() and not self.halt.is_set():
                            time.sleep(0.05)
                        paused_acc += time.monotonic() - began
                        continue
                    due = first + paused_acc + (last + 1) / self.fps
                    wait = due - time.monotonic()
                    if wait > 0 and self.halt.wait(wait):
                        break
                    idx = max(last + 1, int((time.monotonic() - first - paused_acc) * self.fps + 0.5))
                    frame = av.VideoFrame.from_ndarray(self._next_frame(sct, mon), format="bgra")
                    frame = frame.reformat(width=w, height=h, format="yuv420p")
                    frame.pts = idx
                    for pkt in stream.encode(frame):
                        container.mux(pkt)
                    last = idx
                    self.frames += 1
                for pkt in stream.encode(None):
                    container.mux(pkt)
        except Exception as e:  # surfaced through info() / ready
            self.error = f"{type(e).__name__}: {e}"
            self.ready.set()
        finally:
            if container is not None:
                try:
                    container.close()
                except Exception:
                    pass

"""Device-level capture: mic and system audio as two separate files.

No Zoom / Meet / Teams API. Records what the OS plays and what the mic hears, so it works for any app.
  Windows : WASAPI loopback           (soundcard)
  Linux   : PulseAudio/PipeWire monitor source (soundcard)
  macOS   : Core Audio process tap, macOS 14.2+ (catap)
"""
from __future__ import annotations

import json
import sys
import threading
import time
import warnings
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

MAC = sys.platform == "darwin"


def list_devices() -> dict[str, list[str]]:
    import soundcard as sc

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return {
            "microphones": [m.name for m in sc.all_microphones(include_loopback=False)],
            "speakers": [s.name for s in sc.all_speakers()],
        }


class _WavThread(threading.Thread):
    """Reads one soundcard device in 100 ms blocks and streams it to a mono 16-bit wav."""

    def __init__(self, label: str, open_device, path: Path, rate: int, t0: float):
        super().__init__(daemon=True, name=f"rec-{label}")
        self.label, self.open_device, self.path, self.rate, self.t0 = label, open_device, path, rate, t0
        self.ready = threading.Event()
        self.halt = threading.Event()
        self.offset = 0.0
        self.peak = 0.0       # peak of the latest block (for the live meter)
        self.max_peak = 0.0   # peak over the whole run (to detect a silent track)
        self.frames = 0
        self.device_name = ""
        self.error: Exception | None = None

    def run(self) -> None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                device = self.open_device()
                self.device_name = device.name
                block_n = self.rate // 10
                with device.recorder(samplerate=self.rate, channels=None, blocksize=block_n) as rec, \
                        wave.open(str(self.path), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(self.rate)
                    self.offset = time.monotonic() - self.t0
                    self.ready.set()
                    while not self.halt.is_set():
                        block = rec.record(numframes=block_n)
                        mono = block.mean(axis=1) if block.ndim == 2 else block
                        self.peak = float(np.abs(mono).max()) if mono.size else 0.0
                        self.max_peak = max(self.max_peak, self.peak)
                        w.writeframes((np.clip(mono, -1.0, 1.0) * 32767).astype("<i2").tobytes())
                        self.frames += len(mono)
        except Exception as e:  # surfaced by Session.start()/status()
            self.error = e
            self.ready.set()


class Session:
    def __init__(self, out_root: Path, rate: int = 16_000, mic: bool = True, system: bool = True,
                 mic_name: str | None = None, speaker_name: str | None = None):
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.dir = out_root / stamp
        self.rate, self.want_mic, self.want_system = rate, mic, system
        self.mic_name, self.speaker_name = mic_name, speaker_name
        self.started_at = datetime.now()
        self.t0 = 0.0
        self._mic: _WavThread | None = None
        self._sys: _WavThread | None = None
        self._tap = None
        self._tap_offset = 0.0

    # --- device pickers -------------------------------------------------
    def _open_mic(self):
        import soundcard as sc
        return sc.get_microphone(self.mic_name) if self.mic_name else sc.default_microphone()

    def _open_loopback(self):
        import soundcard as sc
        spk = sc.get_speaker(self.speaker_name) if self.speaker_name else sc.default_speaker()
        return sc.get_microphone(spk.name, include_loopback=True)

    # --- lifecycle ------------------------------------------------------
    def start(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.t0 = time.monotonic()
        threads: list[_WavThread] = []
        if self.want_mic:
            self._mic = _WavThread("mic", self._open_mic, self.dir / "mic.wav", self.rate, self.t0)
            threads.append(self._mic)
        if self.want_system and not MAC:
            self._sys = _WavThread("system", self._open_loopback, self.dir / "system.wav", self.rate, self.t0)
            threads.append(self._sys)
        for t in threads:
            t.start()
        if self.want_system and MAC:
            from catap import record_system_audio
            self._tap = record_system_audio(str(self.dir / "system.wav"), mono=True)
            self._tap.start()
            self._tap_offset = time.monotonic() - self.t0
        for t in threads:
            t.ready.wait(timeout=10)
            if t.error:
                self.stop()
                raise RuntimeError(f"{t.label}: {t.error}") from t.error

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def status(self) -> str:
        def bar(p: float) -> str:
            n = min(10, int(p * 30))
            return "▮" * n + "·" * (10 - n)
        mm, ss = divmod(int(self.elapsed()), 60)
        parts = [f"{mm:02d}:{ss:02d}"]
        if self._mic:
            parts.append(f"mic {bar(self._mic.peak)}")
        if self._sys:
            parts.append(f"sistem {bar(self._sys.peak)}")
        if self._tap:
            parts.append("sistem (Core Audio tap)")
        for t in (self._mic, self._sys):
            if t and t.error:
                parts.append(f"ERROR {t.label}: {t.error}")
        return "  ".join(parts)

    def stop(self) -> Path:
        tracks: dict[str, dict] = {}
        for t in (self._mic, self._sys):
            if t:
                t.halt.set()
        for t in (self._mic, self._sys):
            if t:
                t.join(timeout=5)
                tracks[t.label] = {
                    "file": t.path.name, "offset_sec": round(t.offset, 3), "sample_rate": t.rate,
                    "device": t.device_name, "silent": t.max_peak < 1e-4,
                    "error": str(t.error) if t.error else None,
                }
        if self._tap is not None:
            self._tap.stop()
            fmt = getattr(self._tap, "stream_format", None)
            tracks["system"] = {
                "file": "system.wav", "offset_sec": round(self._tap_offset, 3),
                "sample_rate": getattr(fmt, "sample_rate", None), "device": "Core Audio tap (all processes)",
                "silent": bool(self._tap.captured_only_silence), "error": None,
            }
            try:
                self._tap.close()
            except Exception:
                pass
            self._tap = None
        meta = {"started_at": self.started_at.isoformat(timespec="seconds"), "platform": sys.platform,
                "duration_sec": round(self.elapsed(), 3), "tracks": tracks}
        (self.dir / "meta.json").write_text(json.dumps(meta, indent=1))
        return self.dir

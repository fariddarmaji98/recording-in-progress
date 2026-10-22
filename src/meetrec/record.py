"""Device-level capture: mic and system audio as two separate files.

No Zoom / Meet / Teams API. Records what the OS plays and what the mic hears, so it works for any app.
  Windows : WASAPI loopback           (soundcard)
  Linux   : PulseAudio/PipeWire monitor source (soundcard)
  macOS   : Core Audio process tap, macOS 14.2+ (catap)

Both tracks are written as mono 16-bit wav at the same rate. Pause drops audio from both tracks,
so they stay aligned with each other; meta.json lists each track's start offset.
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
READY_TIMEOUT = 15  # seconds a device may take to open before start() gives up


def list_devices() -> dict[str, list[str]]:
    import soundcard as sc

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return {
            "microphones": [m.name for m in sc.all_microphones(include_loopback=False)],
            "speakers": [s.name for s in sc.all_speakers()],
        }


def _to_int16(mono: np.ndarray) -> bytes:
    return (np.clip(mono, -1.0, 1.0) * 32767).astype("<i2").tobytes()


class _Track:
    """Shared bookkeeping: level meter, pause flag, wav writer."""

    def __init__(self, label: str, path: Path, rate: int, t0: float):
        self.label, self.path, self.rate, self.t0 = label, path, rate, t0
        self.paused = threading.Event()
        self.offset = 0.0
        self.peak = 0.0       # latest block (live meter)
        self.max_peak = 0.0   # whole run (silent-track detection)
        self.device_name = ""
        self.error: Exception | None = None

    def _meter(self, mono: np.ndarray) -> None:
        self.peak = float(np.abs(mono).max()) if mono.size else 0.0
        if not self.paused.is_set():
            self.max_peak = max(self.max_peak, self.peak)


class _WavThread(_Track, threading.Thread):
    """Reads one soundcard device in 100 ms blocks and streams it to a wav."""

    def __init__(self, label: str, open_device, path: Path, rate: int, t0: float):
        _Track.__init__(self, label, path, rate, t0)
        threading.Thread.__init__(self, daemon=True, name=f"rec-{label}")
        self.open_device = open_device
        self.ready = threading.Event()
        self.halt = threading.Event()

    def run(self) -> None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                device = self.open_device()
                self.device_name = device.name
                n = self.rate // 10
                with device.recorder(samplerate=self.rate, channels=None) as rec, \
                        wave.open(str(self.path), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(self.rate)
                    self.offset = time.monotonic() - self.t0
                    self.ready.set()
                    while not self.halt.is_set():
                        block = rec.record(numframes=n)
                        mono = block.mean(axis=1) if block.ndim == 2 else block
                        self._meter(mono)
                        if not self.paused.is_set():
                            w.writeframes(_to_int16(mono))
        except Exception as e:  # surfaced by Session.start()/status()
            self.error = e
            self.ready.set()


class _TapWriter(_Track):
    """macOS: receives Core Audio tap buffers (interleaved PCM) and writes the same wav format."""

    _DTYPES = {("float", 32): "<f4", ("float", 64): "<f8",
               ("signed_integer", 16): "<i2", ("signed_integer", 32): "<i4"}

    def __init__(self, path: Path, rate: int, t0: float):
        super().__init__("system", path, rate, t0)
        self.device_name = "Core Audio tap (all processes)"
        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(rate)
        self._rs = None
        self._rs_rate = None
        self._lock = threading.Lock()

    def on_buffer(self, buf) -> None:  # catap worker thread
        try:
            fmt = buf.format
            dt = self._DTYPES.get((fmt.sample_type, fmt.bits_per_sample))
            if dt is None:
                raise ValueError(f"unsupported tap format {fmt.sample_type}/{fmt.bits_per_sample}")
            x = np.frombuffer(buf.data, dtype=dt).reshape(-1, fmt.num_channels).astype(np.float32)
            if fmt.sample_type != "float":
                x /= float(2 ** (fmt.bits_per_sample - 1))
            mono = x.mean(axis=1)
            if int(fmt.sample_rate) != self.rate:
                import soxr
                if self._rs is None or self._rs_rate != fmt.sample_rate:
                    self._rs = soxr.ResampleStream(fmt.sample_rate, self.rate, 1, dtype="float32")
                    self._rs_rate = fmt.sample_rate
                mono = self._rs.resample_chunk(mono)
            self._meter(mono)
            if not self.paused.is_set():
                with self._lock:
                    if self._wav:
                        self._wav.writeframes(_to_int16(mono))
        except Exception as e:
            self.error = e

    def close(self) -> None:
        with self._lock:
            if self._wav:
                self._wav.close()
                self._wav = None


class Session:
    def __init__(self, out_root: Path, rate: int = 16_000, mic: bool = True, system: bool = True,
                 mic_name: str | None = None, speaker_name: str | None = None):
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.dir = Path(out_root) / stamp
        self.rate, self.want_mic, self.want_system = rate, mic, system
        self.mic_name, self.speaker_name = mic_name, speaker_name
        self.started_at = datetime.now()
        self.t0 = 0.0
        self.extra_meta: dict = {}  # e.g. the UI adds the video track here before stop()
        self.live_from: float | None = None  # monotonic time when every device was ready
        self._mic: _WavThread | None = None
        self._sys: _WavThread | None = None
        self._tapw: _TapWriter | None = None
        self._tap = None
        self._paused_at: float | None = None
        self._paused_total = 0.0
        self._pauses: list[dict] = []

    # --- device pickers -------------------------------------------------
    def _open_mic(self):
        import soundcard as sc
        return sc.get_microphone(self.mic_name) if self.mic_name else sc.default_microphone()

    def _open_loopback(self):
        import soundcard as sc
        spk = sc.get_speaker(self.speaker_name) if self.speaker_name else sc.default_speaker()
        return sc.get_microphone(spk.name, include_loopback=True)

    # --- lifecycle ------------------------------------------------------
    def _tracks(self) -> list[_Track]:
        return [t for t in (self._mic, self._sys, self._tapw) if t]

    def start(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.t0 = self.t0 or time.monotonic()  # the panel presets t0 so video and audio start together
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
            self._tapw = _TapWriter(self.dir / "system.wav", self.rate, self.t0)
            self._tap = record_system_audio(mono=True, on_buffer=self._tapw.on_buffer)
            self._tap.start()
            self._tapw.offset = time.monotonic() - self.t0
        for t in threads:
            if not t.ready.wait(timeout=READY_TIMEOUT):
                self.stop()
                raise RuntimeError(f"{t.label}: perangkat tidak siap dalam {READY_TIMEOUT} detik "
                                   "(CoreAudio/izin menggantung). Coba lagi.")
            if t.error:
                self.stop()
                raise RuntimeError(f"{t.label}: {t.error}") from t.error
        self.live_from = time.monotonic()

    @property
    def paused(self) -> bool:
        return self._paused_at is not None

    def pause(self) -> None:
        if self.paused:
            return
        self._paused_at = time.monotonic()
        for t in self._tracks():
            t.paused.set()

    def resume(self) -> None:
        if not self.paused:
            return
        now = time.monotonic()
        self._pauses.append({"at_sec": round(self._paused_at - self.t0, 3), "sec": round(now - self._paused_at, 3)})
        self._paused_total += now - self._paused_at
        self._paused_at = None
        for t in self._tracks():
            t.paused.clear()

    def active_seconds(self) -> float:
        """Recorded time since the devices were ready, excluding pauses."""
        if self.live_from is None:
            return 0.0
        now = self._paused_at if self._paused_at is not None else time.monotonic()
        return max(0.0, now - self.live_from - self._paused_total)

    def levels(self) -> tuple[float, float]:
        """Latest (mic, system) peak, 0..1, for the live level line."""
        mic = self._mic.peak if self._mic else 0.0
        system = (self._sys or self._tapw)
        return mic, (system.peak if system else 0.0)

    def errors(self) -> list[str]:
        """Per-track errors that happened after start (e.g. a device that died mid-recording)."""
        return [f"{t.label}: {t.error}" for t in self._tracks() if t.error]

    def status(self) -> str:
        def bar(p: float) -> str:
            n = min(10, int(p * 30))
            return "▮" * n + "·" * (10 - n)
        mm, ss = divmod(int(self.active_seconds()), 60)
        parts = [f"{mm:02d}:{ss:02d}" + (" (jeda)" if self.paused else "")]
        for t in self._tracks():
            parts.append(f"{'mic' if t.label == 'mic' else 'sistem'} {bar(t.peak)}")
            if t.error:
                parts.append(f"ERROR {t.label}: {t.error}")
        return "  ".join(parts)

    def stop(self) -> Path:
        if self.paused:
            self.resume()
        if self._tap is not None:
            try:
                self._tap.stop()
                self._tap.close()
            except Exception:
                pass
            self._tap = None
        for t in (self._mic, self._sys):
            if t:
                t.halt.set()
        for t in (self._mic, self._sys):
            if t:
                t.join(timeout=5)
        if self._tapw:
            self._tapw.close()
        tracks = {
            t.label: {"file": t.path.name, "offset_sec": round(t.offset, 3), "sample_rate": t.rate,
                      "device": t.device_name, "silent": t.max_peak < 1e-4,
                      "error": str(t.error) if t.error else None}
            for t in self._tracks()
        }
        meta = {"started_at": self.started_at.isoformat(timespec="seconds"), "platform": sys.platform,
                "ready_sec": round((self.live_from or self.t0) - self.t0, 3),
                "duration_sec": round(self.active_seconds(), 3), "pauses": self._pauses, "tracks": tracks,
                **self.extra_meta}
        (self.dir / "meta.json").write_text(json.dumps(meta, indent=1))
        return self.dir

from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

RATE = 16_000


def load_16k_mono(path: str | Path) -> np.ndarray:
    """Any wav/flac -> float32 mono at 16 kHz (what diarization wants)."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if sr != RATE:
        mono = soxr.resample(mono, sr, RATE)
    return np.ascontiguousarray(mono, dtype=np.float32)

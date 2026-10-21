from dataclasses import dataclass
from pathlib import Path

from faster_whisper import WhisperModel


@dataclass
class Word:
    text: str
    start: float
    end: float


def transcribe(wav: str | Path, language: str | None = "id", model: str = "large-v3-turbo",
               compute_type: str = "int8", vad: bool = True) -> list[Word]:
    """Word-level transcript. CPU int8 by default so it fits an 8 GB laptop."""
    m = WhisperModel(model, device="auto", compute_type=compute_type)
    segments, _ = m.transcribe(str(wav), language=language, word_timestamps=True, vad_filter=vad)
    return [Word(w.word.strip(), w.start, w.end) for s in segments for w in (s.words or []) if w.word.strip()]

"""Who said what: transcript lines tagged with the speaker, on the recording timeline.

mic.wav    -> every word is "Me".
system.wav -> every word gets the speaker active at that moment in speakers.json (a Meet name when the extension
              supplied one, else "Remote N"). Words inside an overlap are tagged "A|B" and never guessed.
Both tracks are shifted by their offset from meta.json, so the lines of the two sides interleave in the real order.
Needs the optional package faster-whisper (pip install -e ".[transcribe]"); the model is downloaded on first use.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from .diarize import Segment
from .merge import Turn, build_turns, interleave, to_markdown
from .speakerdata import DEFAULT_THRESHOLD, load_speakers, track_offsets


def transcript_turns(folder: Path, transcribe: Callable, language: str | None = "id", model: str = "small") -> list[Turn]:
    """`transcribe(wav, language, model)` -> list of Word (track time). Injected so tests need no model."""
    folder = Path(folder)
    doc = load_speakers(folder)
    if doc is None:
        raise FileNotFoundError("speakers.json belum ada: jalankan analisis pembicara dulu")
    off = track_offsets(folder)
    lists: list[list[Turn]] = []
    mic, system = folder / "mic.wav", folder / "system.wav"
    if mic.exists():
        words = _shift(transcribe(mic, language, model), off.get("mic", 0.0))
        lists.append(build_turns(words, None, fixed="Me"))
    if system.exists():
        segs = [Segment(s["speaker"], s["start"], s["end"]) for s in doc["segments"] if s.get("track") == "system"]
        words = _shift(transcribe(system, language, model), off.get("system", 0.0))
        lists.append(build_turns(words, segs))
    return interleave(*lists)


def _shift(words: list, by: float) -> list:
    return [type(w)(w.text, w.start + by, w.end + by) for w in words]


def build_transcript(folder: Path, language: str | None = "id", model: str = "small", speakers: int = -1,
                     threshold: float = DEFAULT_THRESHOLD) -> Path:
    """Writes transcript.json + transcript.md next to the recording (and speakers.json first, when missing)."""
    from .asr import transcribe  # imported here: faster-whisper is optional
    from .timeline import build_timeline

    folder = Path(folder)
    if load_speakers(folder) is None or speakers != -1:
        build_timeline(folder, speakers, threshold)
    turns = transcript_turns(folder, transcribe, language, model)
    (folder / "transcript.json").write_text(json.dumps([asdict(t) for t in turns], ensure_ascii=False, indent=1))
    out = folder / "transcript.md"
    out.write_text(to_markdown(turns))
    return out


"""Reading what the analysis wrote next to a recording (speakers.json, transcript.json) and the names you give.

Kept free of heavy imports (numpy, sherpa-onnx, faster-whisper) so the player opens fast.
"""
from __future__ import annotations

import json
from pathlib import Path

# Auto speaker count: 0.3 split two real voices into six "speakers"; 0.5 and 0.6 agreed on two (docs/FINDINGS.md).
# Lives here (not in diarize.py) so the CLI and the player can use it without loading sherpa-onnx.
DEFAULT_THRESHOLD = 0.5


def track_offsets(folder: Path) -> dict[str, float]:
    """Seconds each track starts after the recording's t0 (meta.json)."""
    meta = Path(folder) / "meta.json"
    if not meta.exists():
        return {}
    tracks = json.loads(meta.read_text()).get("tracks", {})
    return {name: float(t.get("offset_sec") or 0.0) for name, t in tracks.items()}


def load_speakers(folder: Path) -> dict | None:
    """speakers.json of a recording, or None when it is missing or broken."""
    f = Path(folder) / "speakers.json"
    try:
        doc = json.loads(f.read_text())
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc.get("segments"), list) else None


def display_name(doc: dict, speaker: str) -> str:
    """The name shown for a speaker id: the one you typed, else the id ("Me", "Remote 1" or a Meet name)."""
    return (doc.get("labels") or {}).get(speaker) or speaker


def set_label(folder: Path, speaker: str, name: str) -> None:
    """Remember a name for a speaker id ("Remote 1" -> "Budi") inside speakers.json. Empty name = back to the id.
    Only the label table changes; segments stay as the analysis wrote them."""
    f = Path(folder) / "speakers.json"
    doc = json.loads(f.read_text())
    labels = dict(doc.get("labels") or {})
    name = name.strip()[:60]
    if name and name != speaker:
        labels[speaker] = name
    else:
        labels.pop(speaker, None)
    if labels:
        doc["labels"] = labels
    else:
        doc.pop("labels", None)
    f.write_text(json.dumps(doc, ensure_ascii=False, indent=1))


def load_transcript(folder: Path) -> list[dict] | None:
    f = Path(folder) / "transcript.json"
    try:
        turns = json.loads(f.read_text())
    except (OSError, ValueError):
        return None
    return turns if isinstance(turns, list) else None

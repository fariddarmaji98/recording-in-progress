"""Per-speaker timeline of a recording folder, and cutting it into per-speaker sections.

mic.wav    -> "Me" (always you, no guessing)
system.wav -> "Remote N" (diarization; segments of different speakers may overlap in time)
Times in speakers.json are on the shared recording timeline (track offsets from meta.json applied).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from .audio import RATE, load_16k_mono
from .diarize import diarize


def _offsets(folder: Path) -> dict[str, float]:
    meta = folder / "meta.json"
    if not meta.exists():
        return {}
    tracks = json.loads(meta.read_text()).get("tracks", {})
    return {name: float(t.get("offset_sec") or 0.0) for name, t in tracks.items()}


def find_overlaps(segs: list[dict]) -> list[dict]:
    """Intervals where 2+ different speakers are active at once."""
    edges = sorted({s["start"] for s in segs} | {s["end"] for s in segs})
    out: list[dict] = []
    for a, b in zip(edges, edges[1:]):
        active = sorted({s["speaker"] for s in segs if s["start"] < b and s["end"] > a})
        if len(active) < 2:
            continue
        if out and out[-1]["speakers"] == active and abs(out[-1]["end"] - a) < 1e-6:
            out[-1]["end"] = b
        else:
            out.append({"start": a, "end": b, "speakers": active})
    return out


def build_timeline(folder: Path, remote_speakers: int = -1, threshold: float = 0.3) -> Path:
    off = _offsets(folder)
    segs: list[dict] = []
    mic, system = folder / "mic.wav", folder / "system.wav"
    if mic.exists():
        for s in diarize(mic, 1):
            segs.append({"speaker": "Me", "track": "mic", "start": s.start + off.get("mic", 0.0),
                         "end": s.end + off.get("mic", 0.0)})
    if system.exists():
        for s in diarize(system, remote_speakers, threshold):
            segs.append({"speaker": s.speaker, "track": "system", "start": s.start + off.get("system", 0.0),
                         "end": s.end + off.get("system", 0.0)})
    segs.sort(key=lambda s: (s["start"], s["end"]))
    for s in segs:
        s["start"], s["end"] = round(s["start"], 2), round(s["end"], 2)
    overlaps = find_overlaps(segs)
    for o in overlaps:
        o["start"], o["end"] = round(o["start"], 2), round(o["end"], 2)
    out = folder / "speakers.json"
    out.write_text(json.dumps({"segments": segs, "overlaps": overlaps}, ensure_ascii=False, indent=1))
    return out


def _stamp(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600:02d}h{s % 3600 // 60:02d}m{s % 60:02d}s"


def _slice(audio: np.ndarray, offset: float, start: float, end: float) -> np.ndarray:
    """Cut [start, end] (recording timeline) out of a track; zero-pad outside the track."""
    n = int(round((end - start) * RATE))
    a = int(round((start - offset) * RATE))
    out = np.zeros(n, dtype=np.float32)
    lo, hi = max(a, 0), min(a + n, len(audio))
    if hi > lo:
        out[lo - a:hi - a] = audio[lo:hi]
    return out


def split_sections(folder: Path) -> Path:
    """One wav per speaker per section, plus one mixed clip per overlap (both voices together)."""
    data = json.loads((folder / "speakers.json").read_text())
    off = _offsets(folder)
    audio = {t: load_16k_mono(folder / f"{t}.wav") for t in ("mic", "system") if (folder / f"{t}.wav").exists()}
    out = folder / "sections"
    out.mkdir(exist_ok=True)
    overlaps = data["overlaps"]
    index: list[dict] = []

    for i, s in enumerate(data["segments"], 1):
        hit = any(o["start"] < s["end"] and o["end"] > s["start"] and s["speaker"] in o["speakers"] for o in overlaps)
        d = out / s["speaker"].replace(" ", "_")
        d.mkdir(exist_ok=True)
        name = f"{i:03d}_{_stamp(s['start'])}-{_stamp(s['end'])}{'_OVERLAP' if hit else ''}.wav"
        sf.write(str(d / name), _slice(audio[s["track"]], off.get(s["track"], 0.0), s["start"], s["end"]), RATE)
        index.append({"file": f"{d.name}/{name}", **s, "has_overlap": hit})

    if overlaps:
        d = out / "_overlaps"
        d.mkdir(exist_ok=True)
        for j, o in enumerate(overlaps, 1):
            mix = sum(_slice(a, off.get(t, 0.0), o["start"], o["end"]) for t, a in audio.items())
            name = f"{j:03d}_{_stamp(o['start'])}-{_stamp(o['end'])}_{'+'.join(x.replace(' ', '') for x in o['speakers'])}.wav"
            sf.write(str(d / name), np.clip(mix, -1.0, 1.0), RATE)
            index.append({"file": f"_overlaps/{name}", "speaker": " + ".join(o["speakers"]),
                          "start": o["start"], "end": o["end"], "has_overlap": True})

    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))
    return out

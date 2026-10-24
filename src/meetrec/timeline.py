"""Per-speaker timeline of a recording folder, and cutting it into per-speaker sections.

mic.wav    -> "Me" (always you, no guessing)
system.wav -> "Remote N" (diarization; segments of different speakers may overlap in time)
Times in speakers.json are on the shared recording timeline (track offsets from meta.json applied).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import soundfile as sf

from .audio import RATE, load_16k_mono
from .diarize import diarize
from .speakerdata import DEFAULT_THRESHOLD, track_offsets as _offsets


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


MEET_MIN_OVERLAP = 2.0   # seconds a voice cluster must overlap a name before it is called by that name
MEET_MIN_RATIO = 0.35    # ... and that overlap must cover this share of the cluster's speech
MEET_MIN_SPOKEN = 3.0    # a name that spoke less than this in total is noise, not a participant
MEET_LAGS = (-0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.5)  # Meet's speaking indicator can trail the audio


def load_meet(folder: Path) -> list[dict]:
    """Other participants' speaking intervals from the Chrome extension (self excluded), or []."""
    f = folder / "meet_events.json"
    if not f.exists():
        return []
    try:
        ivs = json.loads(f.read_text(encoding="utf-8")).get("intervals", [])
    except (ValueError, OSError):
        return []
    return [i for i in ivs if not i.get("self")]


def meet_names(intervals: list[dict]) -> list[str]:
    """Participants who really spoke (enough total time), most talkative first."""
    tot: dict[str, float] = {}
    for i in intervals:
        tot[i["name"]] = tot.get(i["name"], 0.0) + (i["end"] - i["start"])
    return [n for n, v in sorted(tot.items(), key=lambda kv: -kv[1]) if v >= MEET_MIN_SPOKEN]


def _ov(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def name_clusters(remote: list[dict], intervals: list[dict]) -> tuple[dict[str, str], float, dict]:
    """Which real name belongs to which diarization cluster ("Remote 1")?

    Voting by overlap between each cluster's speech and each person's speaking intervals, at the best global
    time shift (the indicator lags). One name per cluster and one cluster per name; weak matches stay unnamed."""
    names = set(meet_names(intervals))
    ivs = [i for i in intervals if i["name"] in names]
    clusters = sorted({s["speaker"] for s in remote})
    dur = {c: sum(s["end"] - s["start"] for s in remote if s["speaker"] == c) for c in clusters}
    if not ivs or not clusters:
        return {}, 0.0, {}

    def matrix(lag: float) -> dict[tuple[str, str], float]:
        m: dict[tuple[str, str], float] = {}
        for c in clusters:
            for i in ivs:
                o = sum(_ov(s["start"], s["end"], i["start"] - lag, i["end"] - lag) for s in remote if s["speaker"] == c)
                if o > 0:
                    m[(c, i["name"])] = m.get((c, i["name"]), 0.0) + o
        return m

    def assign(m):
        used_c, used_n, out = set(), set(), {}
        for (c, n), o in sorted(m.items(), key=lambda kv: -kv[1]):
            if c in used_c or n in used_n:
                continue
            if o >= MEET_MIN_OVERLAP and o >= MEET_MIN_RATIO * dur[c]:
                out[c] = n
                used_c.add(c)
                used_n.add(n)
        return out

    best = max(((sum(m[(c, n)] for c, n in assign(m).items()), lag, m) for lag in MEET_LAGS for m in [matrix(lag)]),
               key=lambda x: (x[0], -abs(x[1])))
    _, lag, m = best
    mapping = assign(m)
    return mapping, lag, {f"{c} -> {n}": round(o, 1) for (c, n), o in m.items() if mapping.get(c) == n}


def build_timeline(folder: Path, remote_speakers: int = -1, threshold: float = DEFAULT_THRESHOLD) -> Path:
    off = _offsets(folder)
    meet = load_meet(folder)
    hint = None
    if meet and remote_speakers == -1 and len(meet_names(meet)) >= 2:
        hint = len(meet_names(meet))  # Meet saw this many different people speak: the cluster count is known
        remote_speakers = hint
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
    meet_info = None
    if meet:
        remote = [s for s in segs if s["track"] == "system"]
        mapping, lag, scores = name_clusters(remote, meet)
        for s in remote:
            s["cluster"] = s["speaker"]
            s["speaker"] = mapping.get(s["speaker"], s["speaker"])
        meet_info = {"names": meet_names(meet), "mapping": mapping, "lag_sec": lag, "overlap_sec": scores,
                     "speaker_count_hint": hint}
    overlaps = find_overlaps(segs)
    for o in overlaps:
        o["start"], o["end"] = round(o["start"], 2), round(o["end"], 2)
    out = folder / "speakers.json"
    doc = {"segments": segs, "overlaps": overlaps}
    if meet_info is not None:
        doc["meet"] = meet_info
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _safe(name: str) -> str:
    return re.sub(r"[^\w.+-]+", "_", name).strip("_") or "speaker"


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
    data = json.loads((folder / "speakers.json").read_text(encoding="utf-8"))
    off = _offsets(folder)
    audio = {t: load_16k_mono(folder / f"{t}.wav") for t in ("mic", "system") if (folder / f"{t}.wav").exists()}
    out = folder / "sections"
    out.mkdir(exist_ok=True)
    overlaps = data["overlaps"]
    index: list[dict] = []

    for i, s in enumerate(data["segments"], 1):
        hit = any(o["start"] < s["end"] and o["end"] > s["start"] and s["speaker"] in o["speakers"] for o in overlaps)
        d = out / _safe(s["speaker"])
        d.mkdir(exist_ok=True)
        name = f"{i:03d}_{_stamp(s['start'])}-{_stamp(s['end'])}{'_OVERLAP' if hit else ''}.wav"
        sf.write(str(d / name), _slice(audio[s["track"]], off.get(s["track"], 0.0), s["start"], s["end"]), RATE)
        index.append({"file": f"{d.name}/{name}", **s, "has_overlap": hit})

    if overlaps:
        d = out / "_overlaps"
        d.mkdir(exist_ok=True)
        for j, o in enumerate(overlaps, 1):
            mix = sum(_slice(a, off.get(t, 0.0), o["start"], o["end"]) for t, a in audio.items())
            name = f"{j:03d}_{_stamp(o['start'])}-{_stamp(o['end'])}_{'+'.join(_safe(x) for x in o['speakers'])}.wav"
            sf.write(str(d / name), np.clip(mix, -1.0, 1.0), RATE)
            index.append({"file": f"_overlaps/{name}", "speaker": " + ".join(o["speakers"]),
                          "start": o["start"], "end": o["end"], "has_overlap": True})

    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return out

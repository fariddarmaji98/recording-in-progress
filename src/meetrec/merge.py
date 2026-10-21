from dataclasses import dataclass

from .asr import Word
from .diarize import Segment

MAX_GAP = 1.0  # seconds of silence that still counts as the same turn
NEAR = 0.5     # a word outside every segment snaps to a speaker within this distance


@dataclass
class Turn:
    speaker: str          # "Me", "Remote 1", or "Remote 1|Remote 2" for overlapped words
    start: float
    end: float
    text: str
    overlap: bool = False


def speakers_for(word: Word, segs: list[Segment]) -> list[str]:
    """Speakers active during the word, most overlap first. >1 entry == overlapped speech."""
    cover: dict[str, float] = {}
    for s in segs:
        o = min(s.end, word.end) - max(s.start, word.start)
        if o > 0:
            cover[s.speaker] = cover.get(s.speaker, 0.0) + o
    if cover:
        return sorted(cover, key=cover.get, reverse=True)
    mid = (word.start + word.end) / 2
    best = min(segs, key=lambda s: max(s.start - mid, mid - s.end, 0.0), default=None)
    if best and max(best.start - mid, mid - best.end, 0.0) <= NEAR:
        return [best.speaker]
    return ["?"]


def build_turns(words: list[Word], segs: list[Segment] | None, fixed: str | None = None) -> list[Turn]:
    """Group words into turns. `fixed` labels every word (mic track -> "Me"); else use diarization."""
    turns: list[Turn] = []
    for w in words:
        who = [fixed] if fixed else speakers_for(w, segs or [])
        label = "|".join(sorted(who)) if len(who) > 1 else who[0]
        t = turns[-1] if turns else None
        if t and t.speaker == label and w.start - t.end <= MAX_GAP:
            t.text += " " + w.text
            t.end = w.end
        else:
            turns.append(Turn(label, w.start, w.end, w.text, overlap=len(who) > 1))
    return turns


def interleave(*tracks: list[Turn]) -> list[Turn]:
    return sorted((t for tr in tracks for t in tr), key=lambda t: t.start)


def fmt_time(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def to_markdown(turns: list[Turn]) -> str:
    return "\n".join(f"[{fmt_time(t.start)}] {t.speaker}{' (overlap)' if t.overlap else ''}: {t.text}" for t in turns) + "\n"

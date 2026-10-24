"""Who said what: transcript lines tagged with the speaker, on the recording timeline.

mic.wav    -> every word is "Me".
system.wav -> every word gets the speaker active at that moment in speakers.json (a Meet name when the extension
              supplied one, else "Remote N"). Words inside an overlap are tagged "A|B" and never guessed.
Both tracks are shifted by their offset from meta.json, so the lines of the two sides interleave in the real order.
ASR comes from the ultraman-transkrip repo's backend over HTTP (see remoteasr.py); faster-whisper is the
fallback when that backend is down and the optional package is installed.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from .merge import Turn, build_turns, interleave, to_markdown
from .remoteasr import pick_transcriber
from .speakerdata import DEFAULT_THRESHOLD, track_offsets


def transcript_turns(folder: Path, transcribe: Callable, language: str | None = "id", model: str = "small",
                     on_event: Callable | None = None) -> list[Turn]:
    """`transcribe(wav, language, model, on_event=...)` -> list of Word (track time). Injected so tests need no model.

    Tanpa diarization: setiap track langsung jadi teks. `mic.wav` = "Me",
    `system.wav` = "Remote". Kalau dua track bicara di saat yang sama, keduanya
    muncul sebagai dua giliran dengan waktu yang sama — tidak ada yang ditebak
    atau dibuang.

    `on_event({"text", "pct"})` menerima langkah tiap track; persentasenya
    diskalakan ke seluruh pekerjaan (dua track = masing-masing separuh), jadi
    jendela progres tidak melompat balik ke 0 saat pindah dari mic ke system.
    """
    folder = Path(folder)
    off = track_offsets(folder)
    lists: list[list[Turn]] = []
    mic, system = folder / "mic.wav", folder / "system.wav"
    tracks = [(name, path, who) for name, path, who in
              (("mic", mic, "Me"), ("system", system, "Remote")) if path.exists()]
    if not tracks:
        raise FileNotFoundError("tidak ada mic.wav atau system.wav di folder rekaman ini")
    for i, (name, path, who) in enumerate(tracks):
        def relay(ev: dict, i=i, name=name) -> None:
            if on_event is None:
                return
            pct = ev.get("pct")
            on_event({"text": f"{name}: {ev['text']}",
                      "pct": None if pct is None else int((i * 100 + pct) / len(tracks))})

        words = _shift(transcribe(path, language, model, on_event=relay), off.get(name, 0.0))
        lists.append(build_turns(words, None, fixed=who))
    return interleave(*lists)


def _shift(words: list, by: float) -> list:
    return [type(w)(w.text, w.start + by, w.end + by) for w in words]


def build_transcript(folder: Path, language: str | None = "id", model: str = "small", speakers: int = -1,
                     threshold: float = DEFAULT_THRESHOLD, transcribe: Callable | None = None,
                     on_event: Callable | None = None) -> Path:
    """Writes transcript.json + transcript.md next to the recording.

    Transkrip = ASR apa adanya: teks + menit tiap baris, tanpa diarization yang
    menahan proses. `speakers`/`threshold` masih diterima supaya pemanggil lama
    (CLI) tidak rusak, tapi tidak dipakai lagi.
    ponytail: diarization dicabut dari jalur transkrip; kembalikan kalau nanti
    butuh memisahkan beberapa orang remote di satu track system.
    """
    folder = Path(folder)

    def say(text: str, pct: int | None = None) -> None:
        if on_event:
            on_event({"text": text, "pct": pct})

    turns = transcript_turns(folder, transcribe or pick_transcriber(), language, model, on_event=on_event)
    (folder / "transcript.json").write_text(json.dumps([asdict(t) for t in turns], ensure_ascii=False, indent=1))
    out = folder / "transcript.md"
    out.write_text(to_markdown(turns))
    say(f"{len(turns)} baris transkrip ditulis ke transcript.md", 100)
    return out


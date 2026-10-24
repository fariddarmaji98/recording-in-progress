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

from .merge import Turn, build_turns, to_markdown
from .remoteasr import pick_transcriber
from .speakerdata import DEFAULT_THRESHOLD


def transcript_turns(folder: Path, transcribe: Callable, language: str | None = "id", model: str = "small",
                     on_event: Callable | None = None) -> list[Turn]:
    """`transcribe(path, language, model, on_event=...)` -> list of Word (recording timeline).

    **Satu media, satu rekaman di server.** Video + mic + system digabung dulu
    (`full.mp4`; tanpa video -> `mix.wav`), lalu berkas gabungan itu yang
    diunggah. Jadi arsip transkrip berisi satu entri yang punya video sekaligus
    transkripnya, bukan dua entri setengah-setengah.

    Waktu dari media gabungan dimulai di `timeline_start` folder, jadi kata
    digeser ke timeline rekaman supaya cocok dengan `speakers.json` dan player.
    """
    folder = Path(folder)
    media, t_start = _media_to_upload(folder, on_event)
    relay = None
    if on_event is not None:
        def relay(ev: dict) -> None:
            on_event({"text": f"{media.name}: {ev['text']}", "pct": ev.get("pct")})

    words = _shift(transcribe(media, language, model, on_event=relay), t_start)
    if getattr(transcribe, "segment_level", False):
        # server sudah memotong per kalimat: satu segmen = satu baris, jangan digabung lagi
        return [Turn("Mix", w.start, w.end, w.text) for w in words]
    return build_turns(words, None, fixed="Mix")


def _media_to_upload(folder: Path, on_event: Callable | None) -> tuple[Path, float]:
    """Berkas gabungan yang diunggah + detik awal timeline-nya."""
    from .fullmedia import build_full_media, build_mix_wav, is_up_to_date, timeline_start

    def say(text: str) -> None:
        if on_event:
            on_event({"text": text, "pct": 1})

    t_start = timeline_start(folder)
    if is_up_to_date(folder):
        say("memakai full.mp4 yang sudah ada (video + suara tergabung)")
        return folder / "full.mp4", t_start
    if (folder / "video.mp4").exists():
        say("menggabungkan video + mic + system jadi satu mp4")
        return build_full_media(folder), t_start
    say("menggabungkan mic + system jadi satu wav (rekaman tanpa video)")
    return build_mix_wav(folder), t_start


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
    (folder / "transcript.json").write_text(json.dumps([asdict(t) for t in turns], ensure_ascii=False, indent=1), encoding="utf-8")
    out = folder / "transcript.md"
    out.write_text(to_markdown(turns), encoding="utf-8")
    say(f"{len(turns)} baris transkrip ditulis ke transcript.md", 100)
    return out


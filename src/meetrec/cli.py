import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .merge import build_turns, interleave, to_markdown


def transcribe_dir(folder: Path, language: str | None, model: str, speakers: int, threshold: float) -> Path:
    from .asr import transcribe
    from .diarize import diarize

    turns = []
    mic, system = folder / "mic.wav", folder / "system.wav"
    if mic.exists():
        turns.append(build_turns(transcribe(mic, language, model), None, fixed="Me"))
    if system.exists():
        segs = diarize(system, speakers, threshold)
        turns.append(build_turns(transcribe(system, language, model), segs))
    merged = interleave(*turns)
    (folder / "transcript.json").write_text(json.dumps([asdict(t) for t in merged], ensure_ascii=False, indent=1))
    out = folder / "transcript.md"
    out.write_text(to_markdown(merged))
    return out


def main() -> None:
    p = argparse.ArgumentParser(prog="meetrec")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("transcribe", help="mic.wav + system.wav in a folder -> transcript.md/json")
    t.add_argument("folder", type=Path)
    t.add_argument("--lang", default="id", help="Whisper language code, 'auto' to detect")
    t.add_argument("--model", default="large-v3-turbo")
    t.add_argument("--speakers", type=int, default=-1, help="remote speaker count, -1 = auto")
    t.add_argument("--threshold", type=float, default=0.3, help="auto-count clustering threshold; lower = more speakers")
    a = p.parse_args()
    if a.cmd == "transcribe":
        lang = None if a.lang == "auto" else a.lang
        print(transcribe_dir(a.folder, lang, a.model, a.speakers, a.threshold))


if __name__ == "__main__":
    main()

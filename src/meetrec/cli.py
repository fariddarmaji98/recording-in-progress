import argparse
import json
import signal
import time
from dataclasses import asdict
from pathlib import Path

from .paths import DEFAULT_OUT


def cmd_devices(_: argparse.Namespace) -> None:
    from .record import MAC, list_devices

    d = list_devices()
    print("Mikrofon:", *d["microphones"], sep="\n  ")
    print("Speaker (suara dari sini direkam sebagai 'system'):" if not MAC else "Speaker:", *d["speakers"], sep="\n  ")
    if MAC:
        print("\nmacOS: suara sistem direkam lewat Core Audio tap (macOS 14.2+), bukan lewat speaker di atas.")


def cmd_record(a: argparse.Namespace) -> None:
    from .record import Session

    s = Session(a.out, rate=a.rate, mic=not a.no_mic, system=not a.no_system,
                mic_name=a.mic, speaker_name=a.speaker)
    s.start()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))  # kill also saves
    print(f"Merekam ke {s.dir}\nCtrl-C untuk berhenti.")
    try:
        while True:
            time.sleep(0.5)
            print("\r" + s.status() + "   ", end="", flush=True)
    except KeyboardInterrupt:
        pass
    folder = s.stop()
    print(f"\nSelesai: {folder}")
    meta = json.loads((folder / "meta.json").read_text())
    for name, t in meta["tracks"].items():
        if t["silent"]:
            print(f"PERINGATAN: track '{name}' diam sama sekali. Cek izin mikrofon / rekam audio sistem untuk aplikasi terminal ini.")


def cmd_timeline(a: argparse.Namespace) -> None:
    from .timeline import build_timeline

    out = build_timeline(a.folder, a.speakers, a.threshold)
    data = json.loads(out.read_text())
    who = sorted({s["speaker"] for s in data["segments"]})
    ov = sum(o["end"] - o["start"] for o in data["overlaps"])
    print(f"{out}\n  pembicara: {', '.join(who)}\n  segmen: {len(data['segments'])}, overlap: {len(data['overlaps'])} kali ({ov:.1f} detik)")
    if "meet" in data:
        m = data["meet"]
        print(f"  dari ekstensi Meet: nama {', '.join(m['names']) or '-'}; dipetakan: {m['mapping'] or 'tidak ada yang cukup yakin'}"
              f"{'; jumlah pembicara diset ' + str(m['speaker_count_hint']) if m['speaker_count_hint'] else ''}")


def cmd_split(a: argparse.Namespace) -> None:
    from .timeline import build_timeline, split_sections

    if not (a.folder / "speakers.json").exists():
        build_timeline(a.folder, a.speakers, a.threshold)
    print(split_sections(a.folder))


def cmd_transcribe(a: argparse.Namespace) -> None:
    from .merge import build_turns, interleave, to_markdown

    try:
        from .asr import transcribe
    except ImportError:
        raise SystemExit("Transkrip butuh paket opsional: pip install 'meetrec[transcribe]'")
    from .diarize import diarize

    lang = None if a.lang == "auto" else a.lang
    turns = []
    mic, system = a.folder / "mic.wav", a.folder / "system.wav"
    if mic.exists():
        turns.append(build_turns(transcribe(mic, lang, a.model), None, fixed="Me"))
    if system.exists():
        segs = diarize(system, a.speakers, a.threshold)
        turns.append(build_turns(transcribe(system, lang, a.model), segs))
    merged = interleave(*turns)
    (a.folder / "transcript.json").write_text(json.dumps([asdict(t) for t in merged], ensure_ascii=False, indent=1))
    out = a.folder / "transcript.md"
    out.write_text(to_markdown(merged))
    print(out)


def cmd_fullmedia(a: argparse.Namespace) -> None:
    from .fullmedia import build_full_media

    print(build_full_media(a.folder, a.out))


def cmd_player(a: argparse.Namespace) -> None:
    from .player import run

    run(a.folder)


def cmd_ui(_: argparse.Namespace) -> None:
    from .ui import run

    run()


def main() -> None:
    import sys

    if len(sys.argv) == 1:
        return cmd_ui(argparse.Namespace())  # plain `meetrec` opens the floating panel
    p = argparse.ArgumentParser(prog="meetrec")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ui", help="buka panel mengambang (default kalau tanpa argumen)").set_defaults(fn=cmd_ui)
    pl = sub.add_parser("player", help="putar rekaman (video + audio) dengan pilihan track Mix / Mic / System")
    pl.add_argument("folder", type=Path, nargs="?", help="folder rekaman (default: yang terbaru dan sudah selesai)")
    pl.set_defaults(fn=cmd_player)

    f = sub.add_parser("fullmedia", help="gabungkan video + mic + sistem jadi satu mp4 (file asli tidak dihapus)")
    f.add_argument("folder", type=Path)
    f.add_argument("--out", default="full.mp4", help="nama file hasil di dalam folder (default full.mp4)")
    f.set_defaults(fn=cmd_fullmedia)

    sub.add_parser("devices", help="daftar mikrofon dan speaker").set_defaults(fn=cmd_devices)

    r = sub.add_parser("record", help="rekam mic + suara sistem (dua file terpisah)")
    r.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT, help=f"folder induk (default {DEFAULT_OUT})")
    r.add_argument("--rate", type=int, default=16000, help="sample rate wav (default 16000)")
    r.add_argument("--mic", help="nama mikrofon (default: bawaan sistem)")
    r.add_argument("--speaker", help="nama speaker yang direkam (Windows/Linux; default: bawaan sistem)")
    r.add_argument("--no-mic", action="store_true")
    r.add_argument("--no-system", action="store_true")
    r.set_defaults(fn=cmd_record)

    def pipeline_args(x: argparse.ArgumentParser) -> None:
        x.add_argument("folder", type=Path)
        x.add_argument("--speakers", type=int, default=-1, help="jumlah pembicara remote, -1 = otomatis")
        x.add_argument("--threshold", type=float, default=0.3, help="ambang otomatis; makin kecil makin banyak pembicara")

    t = sub.add_parser("timeline", help="speakers.json: siapa aktif kapan + zona overlap")
    pipeline_args(t)
    t.set_defaults(fn=cmd_timeline)

    s = sub.add_parser("split", help="potong audio per pembicara per section (+ klip overlap)")
    pipeline_args(s)
    s.set_defaults(fn=cmd_split)

    x = sub.add_parser("transcribe", help="(opsional) transkrip teks")
    pipeline_args(x)
    x.add_argument("--lang", default="id", help="kode bahasa Whisper, 'auto' = deteksi")
    x.add_argument("--model", default="small")
    x.set_defaults(fn=cmd_transcribe)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()

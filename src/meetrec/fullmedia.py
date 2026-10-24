"""Generate full media: video.mp4 + mic.wav + system.wav -> one playable mp4.

The source files are never touched or deleted; the result is a new file (full.mp4).
Audio streams in the result: 1 = mix of everything (default), 2 = mic ("Me"), 3 = system ("Remote").
Each track is placed on the shared timeline with its offset from meta.json, so they line up.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()  # bundled binary: same on Windows, Linux, macOS
    except Exception:
        found = shutil.which("ffmpeg")
        if found:
            return found
        raise RuntimeError("ffmpeg tidak ditemukan (pip install imageio-ffmpeg)")


def is_up_to_date(folder: Path, out_name: str = "full.mp4") -> bool:
    """True when the full media exists and is newer than every source file."""
    folder = Path(folder)
    out = folder / out_name
    if not out.exists():
        return False
    sources = [folder / n for n in ("video.mp4", "mic.wav", "system.wav", "meta.json") if (folder / n).exists()]
    return all(p.stat().st_mtime <= out.stat().st_mtime for p in sources)


def timeline_start(folder: Path) -> float:
    """Recording-timeline second that is second 0 of full.mp4 (the earliest track start).

    Speaker times (speakers.json, transcript.json) are on the recording timeline, so a position in the
    player maps to timeline = position + timeline_start()."""
    folder = Path(folder)
    meta_file = folder / "meta.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    tracks = meta.get("tracks", {})
    starts = [float(tracks.get(n, {}).get("offset_sec") or 0.0) for n in ("mic", "system") if (folder / f"{n}.wav").exists()]
    return min([float((meta.get("video") or {}).get("offset_sec") or 0.0)] + starts)


def build_mix_wav(folder: Path, out_name: str = "mix.wav") -> Path:
    """mic + system -> satu wav, untuk rekaman tanpa video (jalur unggah transkrip).

    Sama seperti `full.mp4` tetapi tanpa gambar: tiap track ditunda sesuai
    offset-nya, lalu dicampur. Dihasilkan ulang tiap kali dipanggil — berkas
    turunan, bukan arsip.
    ponytail: tanpa cek basi; kalau nanti transkrip sering diulang di folder
    tanpa video, tambahkan perbandingan mtime seperti `is_up_to_date`.
    """
    folder = Path(folder)
    meta_file = folder / "meta.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    tracks = meta.get("tracks", {})
    audio = [(folder / f"{name}.wav", float(tracks.get(name, {}).get("offset_sec") or 0.0))
             for name in ("mic", "system") if (folder / f"{name}.wav").exists()]
    if not audio:
        raise FileNotFoundError("tidak ada mic.wav / system.wav untuk dicampur")
    t_start = timeline_start(folder)
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error"]
    for path, _ in audio:
        cmd += ["-i", str(path)]
    chains = [f"[{i}:a]adelay={max(0, round((off - t_start) * 1000))}:all=1[a{i}]"
              for i, (_, off) in enumerate(audio)]
    chains.append("".join(f"[a{i}]" for i in range(len(audio)))
                  + f"amix=inputs={len(audio)}:normalize=0:duration=longest[mix]")
    out = folder / out_name
    cmd += ["-filter_complex", ";".join(chains), "-map", "[mix]", "-ac", "1", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg gagal: {r.stderr.strip()[-400:]}")
    return out


def build_full_media(folder: Path, out_name: str = "full.mp4") -> Path:
    folder = Path(folder)
    meta_file = folder / "meta.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    video = folder / "video.mp4"
    if not video.exists():
        raise FileNotFoundError(f"{video} tidak ada: tidak ada video untuk digabung")

    tracks = meta.get("tracks", {})
    audio = [(name, label, folder / f"{name}.wav", float(tracks.get(name, {}).get("offset_sec") or 0.0))
             for name, label in (("mic", "Mic (Me)"), ("system", "System (Remote)")) if (folder / f"{name}.wav").exists()]
    if not audio:
        raise FileNotFoundError("tidak ada mic.wav / system.wav untuk digabung")
    v_off = float((meta.get("video") or {}).get("offset_sec") or 0.0)
    t_start = timeline_start(folder)  # output time 0 = the earliest track start

    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-itsoffset", f"{v_off - t_start:.3f}", "-i", str(video)]
    for _, _, path, _ in audio:
        cmd += ["-i", str(path)]

    chains, mix_in, split_out = [], [], []
    for i, (name, _, _, off) in enumerate(audio, start=1):
        ms = max(0, round((off - t_start) * 1000))
        chains.append(f"[{i}:a]adelay={ms}:all=1,asplit=2[{name}_mix][{name}_trk]")
        mix_in.append(f"[{name}_mix]")
        split_out.append(f"[{name}_trk]")
    if len(audio) == 1:  # nothing to mix: the single track is the "mix"
        chains = [f"[1:a]adelay={max(0, round((audio[0][3] - t_start) * 1000))}:all=1,asplit=2[{audio[0][0]}_mix][{audio[0][0]}_trk]"]
        chains.append(f"{mix_in[0]}anull[mix]")
    else:
        chains.append("".join(mix_in) + f"amix=inputs={len(audio)}:normalize=0:duration=longest[mix]")

    cmd += ["-filter_complex", ";".join(chains), "-map", "0:v", "-map", "[mix]"]
    for out in split_out:
        cmd += ["-map", out]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "128k"]
    # mp4 stores a stream's name as handler_name (it ignores "title"); set both for other containers
    for k, label in enumerate(["Mix"] + [a[1] for a in audio]):
        cmd += [f"-metadata:s:a:{k}", f"title={label}", f"-metadata:s:a:{k}", f"handler_name={label}"]
    out = folder / out_name
    cmd += ["-movflags", "+faststart", str(out)]

    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg gagal: {r.stderr.strip()[-400:]}")
    return out

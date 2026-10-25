"""Transkrip lewat repo ultraman-transkrip (backend FastAPI) — bukan faster-whisper lokal.

Alurnya: unggah wav ke `POST {base}/recordings`, tunggu job transkrip, ambil `segments` dari
`GET {base}/recordings/{id}`. `base` default `http://127.0.0.1:8000/api`, bisa ditimpa dengan
env `MEETREC_TRANSKRIP_URL`. Rekaman tetap tersimpan di repo itu (jadi arsipnya juga).

Repo mengembalikan segmen per kalimat, bukan per kata — satu segmen jadi satu `Word` dengan
rentang waktunya. Cukup untuk giliran pembicara, tapi zona overlap ditandai per kalimat.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import uuid
from pathlib import Path

from .asr import Word

DEFAULT_BASE = os.environ.get("MEETREC_TRANSKRIP_URL", "http://127.0.0.1:8000/api")
POLL_S = 0.5  # cukup rapat untuk terasa "langsung", cukup jarang untuk tidak membebani server


def transcribe(wav: str | Path, language: str | None = "id", model: str | None = None,
               base: str | None = None, timeout: float = 3600.0, on_event=None,
               recording_id: int | None = None, on_uploaded=None) -> list[Word]:
    """Unggah → minta transkrip → ikuti log server → ambil segmen. `model` diabaikan.

    `recording_id` = rekaman yang SUDAH ada di arsip untuk berkas ini: tidak
    diunggah ulang, hanya diminta transkripnya lagi. Jadi menekan Mulai dua kali
    tidak menumpuk kartu duplikat di server.

    `on_event({"text", "pct"})` dipanggil setiap ada langkah baru: baris dari server
    (lewat `GET /recordings/{id}/progress`) dan langkah yang terjadi di sisi ini
    (mengunggah, meminta transkrip). Dipakai jendela progres, jadi bentuknya sama
    untuk semua sumber. `on_uploaded(recording_id)` memberitahu id hasil unggahan
    supaya pemanggil bisa menyimpannya untuk putaran berikutnya.
    """
    def say(text: str, pct: int | None = None) -> None:
        if on_event:
            on_event({"text": text, "pct": pct})

    base = (base or DEFAULT_BASE).rstrip("/")
    path = Path(wav)
    rid = recording_id
    if rid:
        say(f"memakai rekaman #{rid} yang sudah ada di arsip transkrip", 2)
    else:
        say(f"mengunggah {path.name} ({path.stat().st_size / 1e6:.1f} MB)", 2)
        up = _upload(f"{base}/recordings", path, language or "auto")
        rid = up["recording"]["id"]
        say(f"server menerima sebagai rekaman #{rid}", 4)
        if on_uploaded:
            on_uploaded(rid)
    try:
        _post(f"{base}/recordings/{rid}/transcribe")
    except RuntimeError as e:
        if recording_id and "HTTP 404" in str(e):  # kartunya sudah dihapus di server
            say("rekaman lama tidak ada lagi — mengunggah ulang", 2)
            return transcribe(wav, language, model, base=base, timeout=timeout, on_event=on_event,
                              on_uploaded=on_uploaded)
        raise
    say("menjalankan transkrip di server", 5)

    seq = 0
    if recording_id:  # pakai ulang: lewati log job lama, mulai dari baris baru
        old = _get(f"{base}/recordings/{rid}/progress?since=0")
        seq = max((e["seq"] for e in old.get("events") or []), default=-1) + 1
    deadline = time.monotonic() + timeout
    while True:
        d = _get(f"{base}/recordings/{rid}/progress?since={seq}")
        for e in d.get("events") or []:
            seq = e["seq"] + 1
            say(e["text"], e.get("pct"))
        status = d.get("status")
        if status == "done":
            break
        if status == "failed":
            raise RuntimeError(f"transkrip gagal di repo transkrip: {d.get('error') or 'tanpa pesan'}")
        if status == "paused":
            raise RuntimeError("transkrip dijeda di server — lanjutkan dari halaman transkrip, lalu coba lagi")
        if time.monotonic() > deadline:
            raise TimeoutError(f"rekaman #{rid} belum selesai setelah {timeout:.0f} detik")
        time.sleep(POLL_S)

    segs = _get(f"{base}/recordings/{rid}")["segments"]
    say(f"{len(segs)} segmen diterima dari server", 100)
    return [Word(s["text"].strip(), s["start_ms"] / 1000, s["end_ms"] / 1000) for s in segs if s["text"].strip()]


def _multipart(path: Path, fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    ctype = "video/mp4" if path.suffix.lower() in (".mp4", ".mov", ".m4v", ".mkv", ".webm") else "audio/wav"
    out = bytearray()
    for k, v in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode()
    out += path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def _upload(url: str, path: Path, language: str) -> dict:
    body, ctype = _multipart(path, {"language": language, "title": path.parent.name})
    req = urllib.request.Request(url, data=body, headers={"Content-Type": ctype}, method="POST")
    return _json(req)


def _get(url: str) -> dict:
    return _json(urllib.request.Request(url))


def _post(url: str) -> dict:
    """POST tanpa body (server hanya butuh id di path)."""
    return _json(urllib.request.Request(url, data=b"", method="POST"))


def _json(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{req.full_url}: HTTP {e.code} {e.read()[:200].decode(errors='replace')}") from None
    except urllib.error.URLError as e:
        raise BackendDown(f"repo transkrip tidak bisa dihubungi ({req.full_url}): {e.reason} — "
                          "jalankan backend ultraman-transkrip atau set MEETREC_TRANSKRIP_URL") from None


class BackendDown(RuntimeError):
    pass


def reachable(base: str | None = None) -> bool:
    base = (base or DEFAULT_BASE).rstrip("/")
    try:
        urllib.request.urlopen(f"{base}/health", timeout=2).close()
        return True
    except (urllib.error.URLError, OSError):
        return False


def server_options(base: str | None = None) -> dict | None:
    """Bahasa + model yang tersedia di server, untuk isi dropdown setelan transkrip.

    `None` = server tidak menjawab; pemanggil memakai daftar bawaannya sendiri.
    """
    try:
        return _get(f"{(base or DEFAULT_BASE).rstrip('/')}/config")
    except Exception:  # noqa: BLE001 — isi dropdown tidak boleh menggagalkan jendela
        return None


def set_server_model(model: str, base: str | None = None) -> dict:
    """Ganti model lokal server (`PATCH /config`). 409 kalau ada transkrip berjalan."""
    url = f"{(base or DEFAULT_BASE).rstrip('/')}/config"
    req = urllib.request.Request(url, data=json.dumps({"model": model}).encode(), method="PATCH",
                                 headers={"Content-Type": "application/json"})
    return _json(req)


def pick_transcriber(base: str | None = None):
    """Satu backend untuk SEMUA track (mic + system), dipilih sekali di awal.

    Mencampur dua mesin ASR dalam satu transkrip bikin dua sisi waktu tidak sebanding, jadi
    backend diputuskan di depan, bukan per track.
    """
    from . import asr

    if reachable(base):
        def remote(path, language="id", model="small", on_event=None, recording_id=None, on_uploaded=None):
            return transcribe(path, language, base=base, on_event=on_event,
                              recording_id=recording_id, on_uploaded=on_uploaded)
        remote.segment_level = True  # satu Word = satu kalimat dari server
        return remote

    def local(wav, language="id", model="small", on_event=None):
        if on_event:
            on_event({"text": "backend transkrip tidak jalan — memakai faster-whisper lokal", "pct": None})
        try:
            return asr.transcribe(wav, language, model)
        except ImportError:
            raise SystemExit(f"repo transkrip tidak jalan di {base or DEFAULT_BASE} dan faster-whisper "
                             "tidak terpasang: jalankan backend ultraman-transkrip") from None
    local.segment_level = False  # word-level: perlu digabung jadi giliran
    return local


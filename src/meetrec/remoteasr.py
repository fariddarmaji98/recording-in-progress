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


def transcribe(wav: str | Path, language: str | None = "id", model: str | None = None,
               base: str | None = None, timeout: float = 3600.0) -> list[Word]:
    """`model` diabaikan (repo yang memilih model ASR-nya)."""
    base = (base or DEFAULT_BASE).rstrip("/")
    up = _upload(f"{base}/recordings", Path(wav), language or "auto")
    job = up["job_id"]
    deadline = time.monotonic() + timeout
    while True:
        j = _get(f"{base}/jobs/{job}")
        if j["status"] == "done":
            break
        if j["status"] == "failed":
            raise RuntimeError(f"transkrip gagal di repo transkrip: {j.get('error') or 'tanpa pesan'}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"job {job} belum selesai setelah {timeout:.0f} detik")
        time.sleep(2.0)
    segs = _get(f"{base}/recordings/{up['recording']['id']}")["segments"]
    return [Word(s["text"].strip(), s["start_ms"] / 1000, s["end_ms"] / 1000) for s in segs if s["text"].strip()]


def _multipart(path: Path, fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
            "Content-Type: audio/wav\r\n\r\n").encode()
    out += path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def _upload(url: str, path: Path, language: str) -> dict:
    body, ctype = _multipart(path, {"language": language, "title": path.parent.name})
    req = urllib.request.Request(url, data=body, headers={"Content-Type": ctype}, method="POST")
    return _json(req)


def _get(url: str) -> dict:
    return _json(urllib.request.Request(url))


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


def pick_transcriber(base: str | None = None):
    """Satu backend untuk SEMUA track (mic + system), dipilih sekali di awal.

    Mencampur dua mesin ASR dalam satu transkrip bikin dua sisi waktu tidak sebanding, jadi
    backend diputuskan di depan, bukan per track.
    """
    from . import asr

    if reachable(base):
        return lambda wav, language="id", model="small": transcribe(wav, language, base=base)

    def local(wav, language="id", model="small"):
        try:
            return asr.transcribe(wav, language, model)
        except ImportError:
            raise SystemExit(f"repo transkrip tidak jalan di {base or DEFAULT_BASE} dan faster-whisper "
                             "tidak terpasang: jalankan backend ultraman-transkrip") from None
    return local


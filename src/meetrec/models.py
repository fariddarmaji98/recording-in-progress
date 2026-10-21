import shutil
import ssl
import tarfile
import urllib.request
from pathlib import Path

import certifi

ROOT = Path.home() / ".meetrec" / "models"
_BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEG_URL = f"{_BASE}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
# the release tag really is spelled "recongition" upstream
EMB_URL = f"{_BASE}/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx"


def _fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    # python.org builds on macOS ship without CA certs; certifi works everywhere
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(url, context=ctx) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.replace(dest)


def diarization_models() -> tuple[Path, Path]:
    """(segmentation.onnx, embedding.onnx), downloaded on first use."""
    seg = ROOT / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
    if not seg.exists():
        archive = ROOT / "seg.tar.bz2"
        _fetch(SEG_URL, archive)
        with tarfile.open(archive) as t:
            t.extractall(ROOT, filter="data")
        archive.unlink()
    emb = ROOT / "wespeaker_en_voxceleb_resnet34_LM.onnx"
    if not emb.exists():
        _fetch(EMB_URL, emb)
    return seg, emb

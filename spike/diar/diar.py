"""Speaker diarization with overlap, sherpa-onnx (pyannote segmentation-3.0 + embedding model).

usage: python diar.py WAV EMB_MODEL NUM_CLUSTERS THRESHOLD [truth=system|mixed]
"""
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np
import sherpa_onnx

HERE = Path(__file__).resolve().parent
SEG = HERE / "models/seg/sherpa-onnx-pyannote-segmentation-3-0" / os.environ.get("SEG_FILE", "model.onnx")
THREADS = int(os.environ.get("THREADS", "2"))

TRUTH = {
    "system": {"B": (8.0, 15.7), "C": (12.5, 20.6)},
    "mixed": {"A": (0.0, 11.7), "B": (8.0, 15.7), "C": (12.5, 20.6)},
}


def read_wav(path):
    with wave.open(str(path)) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2
        sr = w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0, sr


def make_sd(emb_model, num_clusters, threshold, num_threads=THREADS):
    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(SEG)),
            num_threads=num_threads,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(emb_model), num_threads=num_threads
        ),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=num_clusters, threshold=threshold
        ),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    assert cfg.validate(), "bad config"
    return sherpa_onnx.OfflineSpeakerDiarization(cfg)


def diarize(wav, emb_model, num_clusters=-1, threshold=0.5):
    samples, sr = read_wav(wav)
    sd = make_sd(emb_model, num_clusters, threshold)
    assert sr == sd.sample_rate, (sr, sd.sample_rate)
    res = sd.process(samples).sort_by_start_time()
    return [(s.speaker, s.start, s.end) for s in res]


def overlaps(segs):
    """Time ranges where >=2 different speakers are active simultaneously."""
    out = []
    for i, (sa, a0, a1) in enumerate(segs):
        for sb, b0, b1 in segs[i + 1:]:
            if sa != sb:
                lo, hi = max(a0, b0), min(a1, b1)
                if hi - lo > 0.05:
                    out.append((sa, sb, lo, hi))
    return out


def purity(segs, truth):
    """Seconds of each cluster that fall inside each true speaker's span."""
    rows = {}
    for spk, s0, s1 in segs:
        for name, (t0, t1) in truth.items():
            lo, hi = max(s0, t0), min(s1, t1)
            if hi > lo:
                rows.setdefault(spk, {}).setdefault(name, 0.0)
                rows[spk][name] += hi - lo
    return rows


if __name__ == "__main__":
    wav, emb, ncl, thr = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
    key = sys.argv[5] if len(sys.argv) > 5 else Path(wav).stem
    t = time.time()
    segs = diarize(wav, emb, ncl, thr)
    print(f"# {Path(wav).name} emb={Path(emb).name} num_clusters={ncl} thr={thr} took {time.time()-t:.2f}s")
    for spk, s0, s1 in segs:
        print(f"speaker_{spk:02d} {s0:6.2f} {s1:6.2f}")
    print("n_speakers_found:", len({s for s, _, _ in segs}))
    ov = overlaps(segs)
    print("simultaneous-different-speaker regions:", [(f"spk{a}", f"spk{b}", round(lo, 2), round(hi, 2)) for a, b, lo, hi in ov] or "NONE")
    if key in TRUTH:
        print("cluster -> seconds inside true speaker span:", {f"spk{k}": {n: round(v, 1) for n, v in d.items()} for k, d in purity(segs, TRUTH[key]).items()})

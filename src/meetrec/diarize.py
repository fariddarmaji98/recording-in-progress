from dataclasses import dataclass
from pathlib import Path

import sherpa_onnx

from .audio import RATE, load_16k_mono
from .models import diarization_models
from .speakerdata import DEFAULT_THRESHOLD


@dataclass
class Segment:
    speaker: str
    start: float
    end: float


def diarize(wav: str | Path, num_speakers: int = -1, threshold: float = DEFAULT_THRESHOLD,
            threads: int = 4) -> list[Segment]:
    """Who spoke when. Segments of different speakers may overlap in time (overlapped speech).

    num_speakers=-1 -> found automatically via `threshold` (lower = more speakers).
    """
    seg_model, emb_model = diarization_models()
    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg_model)),
            num_threads=threads),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb_model), num_threads=threads),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=num_speakers, threshold=threshold),
        min_duration_on=0.3, min_duration_off=0.5)
    sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)
    samples = load_16k_mono(wav)
    assert sd.sample_rate == RATE
    out = sd.process(samples).sort_by_start_time()
    return [Segment(f"Remote {r.speaker + 1}", r.start, r.end) for r in out]

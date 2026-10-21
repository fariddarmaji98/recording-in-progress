# recording-in-progress

Perekam meeting pribadi (alternatif Meet recording berbayar): rekam layar + suara kamu + suara peserta Meet,
lalu transkrip "siapa bilang apa" termasuk saat orang bicara barengan. Python, target Windows / Linux / macOS.

**Status: WIP.** Yang jalan: transkrip + diarization dari file WAV (`meetrec transcribe`).
Yang belum ditulis: rekam audio, rekam layar, coret-coret layar.

## Rencana

| Prioritas | Bagian | Cara | Status |
|---|---|---|---|
| 1 | Transkrip siapa-bilang-apa | `sherpa-onnx` (diarization) + `faster-whisper` (ASR) | Jalan di macOS, teruji di audio sintetis |
| 2 | Rekam mic + suara Meet (2 file terpisah) | `soundcard` (Windows WASAPI loopback, Linux PulseAudio/PipeWire monitor); macOS Core Audio tap (`catap`) | Belum ditulis |
| 3 | Rekam layar | ffmpeg: `gdigrab` / `x11grab` / `avfoundation` | Belum ditulis. Wayland butuh jalur lain |
| 4 | Coret-coret layar | PySide6, window transparan | Belum ditulis |

Temuan dan batasan terukur: [docs/FINDINGS.md](docs/FINDINGS.md).

## Pakai (transkrip)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
meetrec transcribe <folder berisi mic.wav + system.wav> --lang id --model small
```

`mic.wav` = suara kamu (selalu "Me"). `system.wav` = suara peserta lain; diarization hanya jalan di file ini.
Format WAV: 16 kHz, mono, 16-bit. Model diarization diunduh otomatis ke `~/.meetrec/models` saat pertama jalan;
model Whisper diunduh oleh `faster-whisper`.

Uji dengan `testdata/` (suara TTS sintetis, kebenaran di `testdata/truth.md`).

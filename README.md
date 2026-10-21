# recording-in-progress

Perekam meeting pribadi (alternatif rekaman Meet berbayar). Merekam **apa yang masuk dan keluar di perangkatmu**
(mic + suara yang diputar sistem), tanpa API Zoom / Meet / Teams, jadi jalan untuk aplikasi apa pun.
Python, target Windows / Linux / macOS.

**Status: WIP.** Jalan: rekam audio, timeline per pembicara (termasuk overlap), potong per pembicara per section.
Belum ada: rekam layar, coret-coret layar.

## Cara kerja

| Bagian | Cara |
|---|---|
| Rekam mic → `mic.wav` | `soundcard` (semua OS). Isinya selalu "Me", tanpa tebakan. |
| Rekam suara sistem → `system.wav` | Windows: WASAPI loopback. Linux: monitor source PulseAudio/PipeWire (`soundcard`). macOS: Core Audio tap ([`catap`](https://pypi.org/project/catap/), macOS 14.2+). |
| Timeline `speakers.json` | `sherpa-onnx` (pyannote segmentation-3.0). Segmen pembicara berbeda boleh tumpang tindih; zona overlap dicatat. |
| Potong per orang `sections/` | Satu wav per pembicara per section + satu klip campuran per zona overlap. |
| Transkrip teks | Opsional (`pip install -e ".[transcribe]"`). |

Semua orang di Meet/Zoom datang ke perangkatmu sebagai **satu aliran suara**, jadi pemisahan antar peserta remote hanya
lewat diarization. Di zona overlap antar peserta remote, potongan masing-masing orang masih berisi dua suara
(klip `_overlaps/` menandai zonanya). Pemisahan suara sungguhan (speech separation) belum ada.
Pakai headphone supaya suara lawan bicara tidak ikut masuk `mic.wav`.

## Pasang

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Linux: butuh `libpulse` (PulseAudio atau `pipewire-pulse`). macOS: izinkan **Microphone** dan
**Screen & System Audio Recording** untuk aplikasi terminal yang kamu pakai.

## Pakai

```bash
.venv/bin/meetrec devices                      # cek mic dan speaker terdeteksi
.venv/bin/meetrec record                       # Ctrl-C untuk berhenti -> ~/Recordings/meetrec/<waktu>/
.venv/bin/meetrec timeline <folder>            # speakers.json
.venv/bin/meetrec split <folder>               # sections/ per pembicara + _overlaps/
.venv/bin/meetrec transcribe <folder>          # opsional, butuh extra [transcribe]
```

`record` menampilkan meter mic dan sistem tiap detik. Kalau sebuah track diam total, ada peringatan di akhir.
Format: WAV 16 kHz mono 16-bit (`--rate` untuk mengubah). `meta.json` menyimpan selisih waktu mulai tiap track.

Uji tanpa rekam: `testdata/` (suara TTS sintetis, kebenaran di `testdata/truth.md`).

Temuan terukur: [docs/FINDINGS.md](docs/FINDINGS.md).

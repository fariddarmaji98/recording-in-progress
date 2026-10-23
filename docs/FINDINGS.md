# Temuan spike (21 Okt 2026)

Mesin uji: macOS 26, Apple Silicon, RAM 8 GB, Python 3.13.3, CPU saja.
Audio uji: 3 suara TTS sintetis (`testdata/`, lihat `truth.md`). Ini bukti **jalur kerja**, bukan bukti akurasi di suara manusia asli.

## Diarization: sherpa-onnx (pyannote segmentation-3.0 + embedding wespeaker)

| Hal | Hasil |
|---|---|
| Install | `pip install sherpa-onnx` jalan di Python 3.13 macOS arm64. Wheel cp313 juga ada di PyPI untuk Windows (amd64/arm64) dan Linux (x86_64/aarch64) — belum dijalankan di sana. |
| Model | segmentation 6 MB (fp32; int8 menggabungkan A dan B jadi satu label, jangan dipakai) + embedding 26.5 MB |
| Biaya | 21 detik audio: 5–8 detik (mesin sibuk: sampai 35 detik), RSS 100–130 MB |
| Overlap | Terbaca. Segmen dua pembicara berbeda muncul bersamaan: A∩B 7.96–11.86 (benar 8.0–11.7), B∩C 12.47–15.61 (benar 12.5–15.7). Batas segmen meleset ≤0.2 detik. |
| Jumlah pembicara otomatis | Bergantung `threshold`: 0.3 menemukan 3 pembicara di `mixed.wav`, 0.4–0.5 menggabungkan A dan B jadi satu. Tapi di sini hanya satu sampel. |

## Jumlah pembicara otomatis pada rekaman nyata (22 Okt 2026)

Rekaman nyata 128 detik (`system.wav`, suara dari perangkat). `FastClusteringConfig.threshold`, jumlah klaster yang ditemukan:

| Ambang | Klaster (total detik bicara) |
|---|---|
| 0.3 | 6: 0.5 / 0.7 / 1.7 / 9.0 / 17.9 / 30.8 (9.0 + 17.9 = 26.9) |
| 0.4 | 4: 0.5 / 0.7 / 27.0 / 32.5 |
| 0.5 | 2: 33.7 / 26.9 |
| 0.6 | 2: 33.7 / 26.9 |

0.3 memecah satu klaster 26.9 detik menjadi dua, dan menambah klaster ≤1.7 detik. 0.5 dan 0.6 memberi hasil sama persis, jadi default diubah ke 0.5
(`DEFAULT_THRESHOLD`). Kebenaran jumlah orang di rekaman itu tidak diketahui (tidak didengarkan); yang terbukti hanya kestabilan 0.5–0.6.
Pada sampel TTS lama, 0.4–0.5 menggabungkan A dan B (lihat atas): untuk kasus itu pilih jumlah pembicara sendiri.

Transkrip per pembicara pada rekaman nyata yang sama (`meetrec transcribe`, model `small`, bahasa otomatis, Mac 8 GB): 222 detik, memori puncak 1,1 GB,
17 baris (Remote 1: 9, Remote 2: 6, tak jelas: 2), urut waktu. `mic.wav` menghasilkan 0 baris: dengan filter suara aktif Whisper tidak menemukan ucapan,
tanpa filter ia menulis kalimat Spanyol yang tak ada di rekaman. Diarization di `mic.wav` tetap menandai 24,8 detik sebagai "Me" (RMS segmen 0,014–0,027,
sama dengan RMS seluruh track 0,017). Penyebabnya (derau atau suara lawan yang bocor ke mic) belum dibuktikan.

## ASR: faster-whisper `large-v3-turbo`, int8, CPU

| Hal | Hasil |
|---|---|
| Install | `pip install faster-whisper` jalan di Python 3.13. **Wajib `av<17`**: `av` 19 membuat setiap `transcribe()` gagal (`TypeError: open() got an unexpected keyword argument 'metadata_errors'`). |
| Model | 1.5 GB. Load 7–11 detik. Puncak memori 1.9 GB. |
| Kecepatan | RTF 5.5–6.7 saat mesin penuh swap (16 dari 17 GB terpakai); ±3 jika hanya hitung CPU. Belum diukur di mesin kosong. |
| Satu pembicara | `mic.wav` (Indonesia) dan `system.wav` bagian tak-overlap: WER 0.00, timestamp kata meleset ≤0.1 detik di tengah kalimat, 0.2–0.4 detik di ujung. |
| **Overlap** | **Whisper tidak menulis dua suara bersamaan.** Di `system.wav` (B dan C overlap 12.5–15.7): dengan VAD, kata C ditulis semua tapi 12 kata pertamanya diberi timestamp panjang-nol di 15.57; tanpa VAD, seluruh kalimat B hilang. Pada `mixed.wav` (satu track gabungan) WER 0.51 dan kalimat A hilang. |

## Akibat pada desain

1. Mic dan suara sistem **harus dua file terpisah** (sudah begitu). Suara kamu vs lawan bicara yang overlap tetap bersih.
2. Jalankan ASR per track, jangan pada track gabungan.
3. Overlap **antar peserta remote** (dua orang di Meet bicara barengan) masih lossy: diarization tahu siapa saja yang aktif, tapi Whisper hanya memberi satu aliran kata. `merge.py` menandai kata di zona itu sebagai `Remote 1|Remote 2` (overlap) dan tidak menebak.

## Belum terbukti

- Windows dan Linux (hanya wheel yang dicek, tidak dijalankan).
- Suara manusia asli, noise, gema, bocor mic.
- Kecepatan di mesin kosong, dan model `small`.
- Rapat panjang (stabilitas label pembicara).
- Capture audio/layar dan overlay coretan versi Python (belum ditulis).

## Tes ujung ke ujung (`meetrec transcribe`, model `small`)

Input: `mic.wav` (A, Indonesia) + `system.wav` (B dan C overlap), 21 detik. Total 4 menit termasuk unduh model `small`, di Mac yang RAM-nya hampir habis.

```
[00:00:00] Me: Saya tidak setuju dengan usulan itu, biaya server akan naik dua kali lipat, ...
[00:00:07] Remote 1: But the cost is justified. Without the new cluster the checkout page will keep
[00:00:12] Remote 1|Remote 2 (overlap): timing out during every sale and customers will
[00:00:14] Remote 1: simply leave.
[00:00:14] Remote 1|Remote 2 (overlap): We should measure first,
[00:00:16] Remote 2: then decide. Let us run a one -week load test before spending anything.
```

- Suara kamu (mic) tepat 100%, termasuk saat bicara barengan dengan Remote.
- Zona overlap antar peserta remote ditandai `(overlap)`, tidak ditebak.
- Kalimat pertama Remote 2 ("I think both of you are missing the point.") **hilang**: Whisper menulis satu aliran kata saja saat dua orang bicara bersamaan. Ini batas Whisper, bukan `merge.py`.
- `large-v3-turbo` macet di Mac 8 GB yang sedang penuh (butuh ±2 GB, sisa RAM ±70 MB, swap penuh). `small` jalan. Pakai `--model small` kalau RAM sempit.

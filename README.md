# recording-in-progress

Perekam meeting pribadi (alternatif rekaman Meet berbayar). Merekam **apa yang masuk dan keluar di perangkatmu**
(mic + suara yang diputar sistem), tanpa API Zoom / Meet / Teams, jadi jalan untuk aplikasi apa pun.
Python, target Windows / Linux / macOS.

**Status: WIP.** Jalan: panel mengambang (rekam audio + layar, jeda, coret-coret layar), timeline per pembicara (termasuk overlap),
potong per pembicara per section, generate full media, video player.

## Cara kerja

| Bagian | Cara |
|---|---|
| Rekam mic → `mic.wav` | `soundcard` (semua OS). Isinya selalu "Me", tanpa tebakan. |
| Rekam suara sistem → `system.wav` | Windows: WASAPI loopback. Linux: monitor source PulseAudio/PipeWire (`soundcard`). macOS: Core Audio tap ([`catap`](https://pypi.org/project/catap/), macOS 14.2+). |
| Rekam layar → `video.mp4` | `mss` mengambil layar di thread sendiri, PyAV meng-encode H.264 (hardware encoder di macOS bila ada). **Tanpa suara**: suara ada di `mic.wav` dan `system.wav`. Jeda memotong video seperti audio. |
| Full media → `full.mp4` | Menggabungkan `video.mp4` + `mic.wav` + `system.wav` jadi satu mp4 (ffmpeg bawaan paket `imageio-ffmpeg`). File asli tidak dihapus. |
| Daftar rekaman + player | Tombol daftar di panel (atau `meetrec player`) membuka jendela sempit berisi **kartu** yang bisa digulir. Kartu: gambar dengan ikon play, nama, ukuran \| durasi \| tipe, tombol **folder**, **hapus** (dipindah ke Sampah, dengan konfirmasi), dan **buat video final**. Klik kartu membuka player (`full.mp4`): pilih track audio **Mix / Mic (Me) / System (Remote)**, kecepatan 0.5×–2×, seek, spasi = putar/jeda, ←/→ = ±5 detik. Panel utama disembunyikan selama daftar atau player terbuka, dan muncul lagi saat ditutup. Gambar kartu di-cache di `~/.meetrec/thumbs` (bukan di folder rekaman). |
| Timeline `speakers.json` | `sherpa-onnx` (pyannote segmentation-3.0). Segmen pembicara berbeda boleh tumpang tindih; zona overlap dicatat. |
| Potong per orang `sections/` | Satu wav per pembicara per section + satu klip campuran per zona overlap. |
| Transkrip teks | Opsional (`pip install -e ".[transcribe]"`). |

Semua orang di Meet/Zoom datang ke perangkatmu sebagai **satu aliran suara**, jadi pemisahan antar peserta remote hanya
lewat diarization. Di zona overlap antar peserta remote, potongan masing-masing orang masih berisi dua suara
(klip `_overlaps/` menandai zonanya). Pemisahan suara sungguhan (speech separation) belum ada.
Pakai headphone supaya suara lawan bicara tidak ikut masuk `mic.wav`.

## Ekstensi Chrome untuk Google Meet (pembeda lebih akurat)

Folder `extension/`. Ekstensi membaca halaman Meet-mu (tanpa API Meet), mencatat **siapa bicara dari menit berapa sampai berapa**,
dan mengirimnya ke aplikasi meetrec di komputer yang sama. Aplikasi memakainya untuk:

1. **Menamai suara**: "Remote 1" menjadi "Budi" (lewat tumpang tindih waktu, dengan koreksi jeda indikator Meet).
2. **Menentukan jumlah pembicara** diarization (jumlah nama yang benar-benar bicara), kecuali kamu set `--speakers` sendiri.
3. **Menamai zona overlap**: "Budi + Sari", bukan "Remote 1|Remote 2".

**Pasang:** Chrome → `chrome://extensions` → nyalakan *Developer mode* → *Load unpacked* → pilih folder `extension/`.
Lalu di aplikasi klik ⚙ Pengaturan → **Kode ekstensi Chrome** → *Salin*, dan tempel di *Opsi* ekstensi → *Tes koneksi*.

**Dua sinyal deteksi**, keduanya tidak memakai nama class Meet yang diacak:
- **Tile peserta**: tile (`[data-participant-id]`) yang atributnya terus berubah = sedang bicara (animasi indikator). Efek hover diabaikan.
- **Caption (CC)**: nama dan teks yang berubah di panel caption = sedang bicara. Menjangkau peserta yang tilenya tidak tampil, tapi caption harus menyala
  (tombol *Aktifkan caption* di popup, atau otomatis lewat Opsi).

**Popup ekstensi** menampilkan status koneksi, siapa yang terdeteksi bicara sekarang, total detik per orang, dan apakah caption terbaca,
untuk memeriksa langsung di rapat apakah deteksinya jalan.

**Keamanan:** penerima hanya mendengarkan `127.0.0.1` (bukan jaringan), setiap permintaan butuh kode, dan tidak ada header CORS, jadi halaman web tidak bisa memanggilnya.
Ekstensi tidak mengirim apa pun ke internet. Data hanya disimpan saat kamu merekam: `meet_events.json` di folder rekaman.

**Status:** logika deteksi dan penerima sudah dites (14 tes JS di DOM tiruan, 33 tes Python). **Belum diuji di rapat Meet sungguhan**: struktur halaman Meet
bukan API publik dan bisa berubah, jadi cek popup di rapat pertamamu; kalau tidak ada yang terdeteksi, nyalakan caption atau ubah *Kepekaan* di Opsi.
Tes JS: `cd extension && npm install && npm test`.

**Jendela:** panel utama = jendela biasa dengan bilah judul bawaan OS yang hanya punya **close** (menutup aplikasi) dan **minimize** (tanpa maximize, ukuran terkunci mengikuti isi), selalu di atas aplikasi lain. Daftar rekaman dan player = **close + minimize + maximize** bawaan OS.

## Pasang

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Linux: butuh `libpulse` (PulseAudio atau `pipewire-pulse`). macOS: izinkan **Microphone** dan
**Screen & System Audio Recording** untuk aplikasi terminal yang kamu pakai.

## Pakai: panel mengambang

```bash
.venv/bin/meetrec          # buka panel di pojok kanan atas
```

| Keadaan | Isi panel |
|---|---|
| Awal | dua section, atas-bawah. **Atas:** ikon folder + jalur penyimpanan **lengkap** (klik ikon atau teksnya untuk ganti folder). **Bawah:** **▶ mulai** · **daftar rekaman / player** · **⚙ pengaturan**. Tombol tutup dan minimize ada di bilah judul jendela bawaan sistem (bukan tombol buatan sendiri). |
| Klik ▶ | pilihan **Layar penuh** atau **Aplikasi** (daftar jendela yang terbuka; Zoom, Meet, Teams, dan sejenisnya diurutkan paling atas). Klik dua kali atau pilih lalu **Mulai rekam aplikasi ini**. |
| Merekam | menit berjalan · **Gambar** · **Jeda** · **Stop**. Tombol close bawaan dinonaktifkan dan jendela menolak ditutup (muncul petunjuk); minimize tetap bisa. |
| Mode gambar | + pensil / teks · 6 warna · ketebalan · urungkan · **simpan** · **hapus** |

- **Tombol:** semua tombol ikon adalah satu komponen (`IconButton`): persegi, ukuran sama persis (satu konstanta `IconButton.SIZE`), warna lewat `tone` (hijau/merah/biasa).
- **Pengaturan (⚙):** rekam layar on/off, FPS video (4/8/15/30), kualitas audio (16/24/48 kHz). Disimpan, berlaku di rekaman berikutnya.
- **Level suara:** garis bergelombang di bawah tombol saat merekam: **hijau = mikrofon, biru = suara sistem**. Kalau garis biru datar saat ada suara diputar, audio sistem tidak masuk. Garis ini hanya muncul saat merekam.
- **Rekam aplikasi:** video dipotong ke persegi panjang jendela itu dan mengikutinya kalau dipindah (ukuran video tetap sama seperti saat mulai; kalau jendela diubah ukurannya, gambar ikut diskalakan). Jendela harus tetap terlihat di depan: yang menutupinya ikut terekam. **Audio tetap seluruh suara sistem + mic**, bukan audio per aplikasi.
  macOS: daftar jendela lewat Quartz (judul jendela butuh izin Screen Recording). Windows: Win32 (ctypes). Linux: X11 lewat `python-xlib`; di Wayland daftar jendela tidak tersedia, pakai Layar penuh.
- **Gambar:** layar dibekukan dengan screenshot lalu kamu coret di atasnya. Simpan → `drawings/drawing_<jam>.png` di folder rekaman;
  hapus → dibuang. Keduanya menutup mode gambar. Rekaman audio jalan terus.
- **Esc** menutup mode gambar tanpa menyimpan (sama dengan tombol hapus). Esc saat mengetik teks hanya membatalkan teks itu.
- **Panel tetap terlihat:** selama mode gambar, panel menempel di dalam layar gambar (bisa digeser), jadi tidak tertutup coretan atau layar beku.
- **Tanpa screenshot:** kalau OS menolak screenshot (Wayland, atau izin Screen Recording macOS belum diberikan),
  layar tidak dibekukan: kamu mencoret langsung di atas layar yang hidup, dan PNG-nya hanya berisi coretan (latar transparan).
- **Layar:** direkam 8 fps ke `video.mp4`, sejajar dengan audio lewat `offset_sec` di `meta.json`. Panel disembunyikan dari rekaman
  (macOS dan Windows; di Linux panel ikut terlihat). Saat mode gambar aktif, video merekam hasil layar beku + coretan tanpa panel.
  Linux Wayland: layar tidak bisa diambil (hasilnya hitam), butuh X11. macOS: butuh izin Screen Recording; tanpa izin, audio tetap direkam dan panel memberi tahu.
- **Mulai:** perangkat dibuka di background, jadi panel tidak membeku. Timer baru berjalan setelah mic dan audio sistem siap; `meta.json` mencatat `ready_sec` dan `offset_sec` tiap track.
- **Jeda:** audio selama jeda dibuang dari kedua track, jadi keduanya tetap sejajar. `meta.json` mencatat tiap jeda.

## Pakai: baris perintah

```bash
.venv/bin/meetrec devices                      # cek mic dan speaker terdeteksi
.venv/bin/meetrec record                       # Ctrl-C untuk berhenti -> ~/Recordings/meetrec/<waktu>/
.venv/bin/meetrec timeline <folder>            # speakers.json
.venv/bin/meetrec split <folder>               # sections/ per pembicara + _overlaps/
.venv/bin/meetrec player [folder]             # putar rekaman (default: yang terbaru dan sudah selesai)
.venv/bin/meetrec fullmedia <folder>           # full.mp4: video + audio digabung (file asli tetap)
.venv/bin/meetrec transcribe <folder>          # opsional, butuh extra [transcribe]
```

`full.mp4` berisi 1 video dan 3 audio: **Mix** (semua suara, default), **Mic (Me)**, dan **System (Remote)**,
semuanya diselaraskan lewat `meta.json`.

`record` menampilkan meter mic dan sistem tiap detik. Kalau sebuah track diam total, ada peringatan di akhir.
Format: WAV 16 kHz mono 16-bit (`--rate` untuk mengubah). `meta.json` menyimpan selisih waktu mulai tiap track.

Uji tanpa rekam: `testdata/` (suara TTS sintetis, kebenaran di `testdata/truth.md`).

Temuan terukur: [docs/FINDINGS.md](docs/FINDINGS.md).

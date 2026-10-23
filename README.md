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
| Transkrip per pembicara `transcript.json` / `.md` | Opsional (`pip install -e ".[transcribe]"`, faster-whisper). Tiap baris: waktu, **siapa**, teks. Kata dari `mic.wav` = "Me"; kata dari `system.wav` = pembicara yang aktif saat itu di `speakers.json`; kata di zona overlap ditandai dua nama dan tidak ditebak. Waktu kedua track diselaraskan lewat `meta.json`. |

Semua orang di Meet/Zoom datang ke perangkatmu sebagai **satu aliran suara**, jadi pemisahan antar peserta remote hanya
lewat diarization. Di zona overlap antar peserta remote, potongan masing-masing orang masih berisi dua suara
(klip `_overlaps/` menandai zonanya). Pemisahan suara sungguhan (speech separation) belum ada.
Pakai headphone supaya suara lawan bicara tidak ikut masuk `mic.wav`.

## Pengaturan: General dan Cloud

Tombol ⚙ membuka jendela Pengaturan dengan **menu di kiri** (sidebar) dan halaman di kanan. Menu baru tinggal ditambah dengan `SidebarPages.add_page`.

| Menu | Isi |
|---|---|
| **General** | Rekam layar, FPS video, kualitas audio, ekstensi Chrome (kode + port). Disimpan dengan tombol **Simpan**. |
| **Cloud** | Dua section, langsung berlaku (tidak lewat Simpan): **Setup rclone** dan **Login platform penyimpanan cloud**. |

**Setup rclone.** Menunjukkan apakah [rclone](https://rclone.org) ditemukan (salinan meetrec di `~/.meetrec/bin`, lalu PATH, lalu folder pasang umum) beserta versinya.
**Pasang rclone** mengunduh build resmi untuk OS dan CPU ini (macOS, Windows, Linux; amd64 dan arm64) dari `downloads.rclone.org`, mencocokkan SHA-256 dengan `SHA256SUMS`,
dan menaruh programnya di `~/.meetrec/bin` tanpa hak admin. Unduhan yang checksum-nya tidak cocok dibuang.

**Login platform.** Akun tampil sebagai **kartu** (tiga baris teks: nama · platform / status / folder tujuan) dengan tiga tombol persegi: **select** (centang), **folder**, **hapus**.
Kartu **+** di bawahnya membuka **halaman baru** "Tambah koneksi cloud" (tombol **Kembali**; tombol close jendela dan Esc di halaman itu juga = kembali ke Pengaturan, seperti semua sub halaman). Pilih platform, beri nama, lalu
- Google Drive, Dropbox, OneDrive, Box, pCloud: **Login** menjalankan `rclone config create`; browser terbuka, kamu mengizinkan akses, token ditulis ke konfigurasi. **Batal login** atau menutup jendela menghentikannya.
- Amazon S3 (juga Cloudflare R2, Wasabi, Minio) dan Backblaze B2: isi kunci lalu **Simpan akun**. Kunci ditulis langsung ke file konfigurasi, tidak lewat baris perintah.

Tombol di kartu:
- **select**: menjalankan tes koneksi (daftar folder atas). Kalau lulus, akun ini menjadi **tujuan upload** (kartu berbingkai hijau; hanya satu akun yang bisa terpilih). Kalau gagal, pilihan lama tetap dan galatnya tampil di kartu.
- **folder**: mengubah folder tujuan di akun itu (default `meetrec`, boleh `Rapat/2026`, kosong = level atas).
- **hapus**: dengan konfirmasi; izin di sisi penyedia tetap ada sampai kamu mencabutnya di sana.

Pilihan akun dan folder disimpan di pengaturan aplikasi. Mengganti akun tujuan, folder akun tujuan, atau menghapus akun tujuan **saat ada upload berjalan** memunculkan konfirmasi: upload dibatalkan dan progress-nya hilang.

meetrec memakai **file konfigurasi rclone-nya sendiri**, `~/.meetrec/rclone.conf` (hanya bisa dibaca akunmu), jadi akun rclone yang sudah kamu buat di `~/.config/rclone` tidak dibaca dan tidak diubah.
Platform baru ditambah di daftar `PROVIDERS` (`cloud.py`).

## Daftar rekaman: tab Local dan Cloud

Jendela daftar rekaman punya dua tab (muncul bila aplikasi dibuka dari panel):
- **Local**: rekaman di komputer ini. Tiap kartu bisa **diunggah** (tombol upload, lihat bawah).
- **Cloud**: rekaman yang ada di tujuan cloud terpilih (`<akun>:<folder>/`, lihat Pengaturan > Cloud). Daftar dimuat di latar dari `rclone lsjson`; hanya folder rekaman yang tampil
  (berkas lepas dan nama tidak aman dilewati). Kartu menunjukkan ukuran, jumlah berkas, tanggal, dan "sudah ada di komputer" bila folder lokalnya ada.
  Tombol **unduh** menyalin folder itu ke folder rekaman lokal (`rclone copy`: berkas yang sama persis dilewati), dengan cincin progres dan jeda/lanjut yang sama seperti upload.
  Setelah selesai, rekaman muncul di tab Local. Daftar cloud tidak dimuat ulang tiap pindah tab; ia dimuat ulang oleh **Muat ulang**, saat tujuan berubah, dan setelah ada upload selesai.

Unduhan tidak ikut dibatalkan saat tujuan diganti (prosesnya sudah terikat ke akun asalnya); menutup aplikasi saat ada upload atau unduhan meminta konfirmasi.

## Upload rekaman ke cloud

Di daftar rekaman, tiap kartu punya tombol keempat: **upload** (ikon panah ke atas), setelah folder, hapus, dan video final. Satu klik mengunggah **seluruh folder rekaman** ke
`<akun>:<folder>/<nama rekaman>/` pada tujuan yang dipilih di Pengaturan > Cloud (`rclone copy`: berkas yang sama persis dilewati, jadi mengunggah ulang aman).

| Keadaan tombol | Tampilan | Klik |
|---|---|---|
| belum | ikon upload | mulai upload (tanpa tujuan terpilih: muncul penjelasan di mana memilihnya) |
| mengunggah | cincin biru berisi progres, ikon jeda | jeda |
| dijeda | cincin kuning, ikon putar | lanjut |
| selesai | hijau, centang | unggah ulang |
| gagal | merah, alasannya di tooltip | coba lagi |

Jeda = proses rclone dibekukan (`SIGSTOP` di macOS/Linux, `NtSuspendProcess` di Windows) lalu dilanjutkan. Upload berjalan terus saat jendela daftar ditutup dan dibuka lagi;
menutup aplikasi saat ada upload meminta konfirmasi, dan upload dibatalkan. Menghapus rekaman yang sedang diunggah membatalkan upload-nya.

## Siapa bicara apa (di player)

Buka rekaman di player. Di bawah video ada bagian **Pembicara**:

| Bagian | Fungsi |
|---|---|
| **Analisis pembicara** | Menjalankan diarization di komputer ini (±40 detik per 2 menit audio di Mac 8 GB, jalan di latar; file asli tidak diubah) lalu menulis `speakers.json`. |
| **Remote: otomatis ▾** | Jumlah orang di sisi lawan bicara. Otomatis bisa salah hitung (terlalu banyak atau terlalu sedikit); kalau tahu jumlahnya, pilih sendiri lalu **Analisis ulang**. |
| **Jalur pembicara** | Satu jalur per orang (Me paling atas), batang berwarna di menit orang itu bicara, jalur oranye = **tumpang tindih**, garis putih = posisi putar. Klik atau seret = loncat ke menit itu. **Klik dua kali nama** = beri nama ("Remote 1" → "Budi"); disimpan di `speakers.json` (`labels`). |
| **Sedang bicara: …** | Berubah mengikuti putaran: satu nama, dua nama + "(tumpang tindih)", atau "(hening)". |
| **Buat transkrip** | Menulis apa yang dikatakan tiap orang (pilih bahasa di sebelahnya). Pertama kali mengunduh model Whisper `small` ±480 MB. Di Mac 8 GB: 128 detik rekaman (2 track) selesai dalam 222 detik, memori puncak 1,1 GB. Track yang isinya hanya derau tidak menghasilkan baris (filter suara aktif). Klik satu baris = loncat ke menit itu; baris yang sedang diputar ditandai. |

Cek jumlah pembicara otomatis: pada satu rekaman nyata, ambang 0.3 memecah 2 suara menjadi 6 "pembicara"; ambang 0.5 dan 0.6 sama-sama
menemukan 2. Karena itu default sekarang 0.5. Pada sampel TTS lama, 0.5 menggabungkan dua suara TTS yang mirip (A dan B): pilih jumlah sendiri bila itu terjadi.

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

**Jendela:** panel utama = jendela biasa dengan bilah judul bawaan OS yang hanya punya **close** (menutup aplikasi) dan **minimize** (tanpa maximize, ukuran terkunci mengikuti isi), selalu di atas aplikasi lain. Daftar rekaman dan player = **close + minimize + maximize** bawaan OS. **Close di halaman lain = kembali ke halaman sebelumnya**, bukan menutup aplikasi: pilihan "Rekam apa?" → halaman awal, player → daftar rekaman, daftar rekaman → halaman awal. Aplikasi hanya tertutup lewat close di halaman awal (atau force quit).

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
| Klik ▶ | pilihan **Layar penuh** atau **Aplikasi** (daftar jendela yang terbuka; Zoom, Meet, Teams, dan sejenisnya diurutkan paling atas). Klik dua kali atau pilih lalu **Mulai rekam aplikasi ini**. Tidak ada tombol tutup sendiri di halaman ini; close bawaan jendela = kembali ke halaman awal. |
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

## Pasang di Mac (installer)

File: `build/meetrec-0.1.0-macos-arm64.dmg` (±95 MB). Buka, seret **meetrec.app** ke **Applications**, lalu buka dengan klik kanan → Open
(aplikasi ditandatangani ad hoc, bukan Apple Developer ID; isi "Baca saya.txt" di dalam dmg menjelaskan izin Mikrofon, Perekaman Layar,
Perekaman Audio Sistem, dan perintah `xattr` bila macOS memblokir). Hanya Apple Silicon (arm64) dan macOS 14.2+. Sudah berisi PySide6, sherpa-onnx,
faster-whisper, ffmpeg; model pembeda pembicara dan model Whisper diunduh saat pertama dipakai.

Bangun ulang (±4 menit, butuh `.venv` proyek dan PyInstaller):

```bash
.venv/bin/python -m pip install pyinstaller
packaging/mac/build_mac.sh
```

Skrip menjalankan `meetrec --selftest` di dalam aplikasi hasil build lalu membuat dmg di `build/`. Tiap build mengganti tanda tangan ad hoc, jadi macOS
bisa meminta ulang izin Mikrofon / Layar. GitHub menolak file di atas 100 MiB; dmg ini 95 MiB. Kalau lebih besar, unggah lewat GitHub Releases, bukan commit.

## Pakai: baris perintah

```bash
.venv/bin/meetrec devices                      # cek mic dan speaker terdeteksi
.venv/bin/meetrec record                       # Ctrl-C untuk berhenti -> ~/Recordings/meetrec/<waktu>/
.venv/bin/meetrec timeline <folder>            # speakers.json (--speakers N bila jumlah orang remote diketahui)
.venv/bin/meetrec split <folder>               # sections/ per pembicara + _overlaps/
.venv/bin/meetrec player [folder]             # putar rekaman (default: yang terbaru dan sudah selesai)
.venv/bin/meetrec fullmedia <folder>           # full.mp4: video + audio digabung (file asli tetap)
.venv/bin/meetrec transcribe <folder>          # transcript.json + .md per pembicara; opsional, butuh extra [transcribe]
```

`full.mp4` berisi 1 video dan 3 audio: **Mix** (semua suara, default), **Mic (Me)**, dan **System (Remote)**,
semuanya diselaraskan lewat `meta.json`.

`record` menampilkan meter mic dan sistem tiap detik. Kalau sebuah track diam total, ada peringatan di akhir.
Format: WAV 16 kHz mono 16-bit (`--rate` untuk mengubah). `meta.json` menyimpan selisih waktu mulai tiap track.

Uji tanpa rekam: `testdata/` (suara TTS sintetis, kebenaran di `testdata/truth.md`).

Temuan terukur: [docs/FINDINGS.md](docs/FINDINGS.md).

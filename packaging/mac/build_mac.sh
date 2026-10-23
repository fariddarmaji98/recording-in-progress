#!/usr/bin/env bash
# Builds the macOS installer: build/meetrec-<version>-macos-<arch>.dmg (drag meetrec.app to Applications).
#
#   packaging/mac/build_mac.sh
#
# Needs: the project's .venv (pip install -e ".[transcribe]") and PyInstaller importable by that Python
# (pip install pyinstaller, or put it on PYI_PATH). The app is built for the architecture of this Mac only.
# The app is signed ad hoc (no Apple Developer ID), see the notes in the dmg.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
export PYTHONPATH="${PYI_PATH:-}${PYI_PATH:+:}${PYTHONPATH:-}"
"$PY" -c "import PyInstaller" 2>/dev/null || { echo "PyInstaller belum ada: $PY -m pip install pyinstaller (atau set PYI_PATH)"; exit 1; }

VERSION="$("$PY" -c "import tomllib; print(tomllib.load(open('$ROOT/pyproject.toml','rb'))['project']['version'])")"
ARCH="$(uname -m)"
WORK="$(mktemp -d /private/tmp/meetrec-build.XXXXXX)"
if [ -z "${KEEP_WORK:-}" ]; then trap 'rm -rf "$WORK"' EXIT; else echo "KEEP_WORK: $WORK"; fi
OUT="$ROOT/build/meetrec-$VERSION-macos-$ARCH.dmg"
mkdir -p "$ROOT/build"

echo "== ikon"
QT_QPA_PLATFORM=offscreen "$PY" "$ROOT/packaging/mac/make_icon.py" "$WORK/meetrec.icns"

echo "== PyInstaller (beberapa menit)"
MEETREC_VERSION="$VERSION" MEETREC_ICON="$WORK/meetrec.icns" \
  "$PY" -m PyInstaller --noconfirm --clean --distpath "$WORK/dist" --workpath "$WORK/work" "$ROOT/packaging/mac/meetrec.spec"
APP="$WORK/dist/meetrec.app"

echo "== cek isi aplikasi"
"$APP/Contents/MacOS/meetrec" --selftest
codesign --verify --deep --strict "$APP"

echo "== dmg"
STAGE="$WORK/stage"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/Baca saya.txt" <<'TXT'
meetrec: pasang dan buka pertama kali

1. Seret meetrec.app ke Applications.
2. Buka meetrec dengan klik kanan, lalu Open (aplikasi ini ditandatangani ad hoc, bukan dengan Apple Developer ID,
   jadi macOS menanyakan sekali). Kalau macOS bilang aplikasi "rusak" atau diblokir, jalankan di Terminal:
     xattr -dr com.apple.quarantine /Applications/meetrec.app
3. Saat mulai merekam, izinkan Mikrofon, Perekaman Layar, dan Perekaman Audio Sistem
   (System Settings > Privacy & Security). Setelah memberi izin Perekaman Layar, buka ulang aplikasi.
4. Transkrip pertama kali mengunduh model Whisper (±480 MB). Model pembeda pembicara diunduh saat analisis pertama.

Perlu macOS 14.2 atau lebih baru. Rekaman disimpan di ~/Recordings/meetrec.
TXT
rm -f "$OUT"
hdiutil create -volname "meetrec" -srcfolder "$STAGE" -ov -format ULMO "$OUT" >/dev/null
echo "== selesai: $OUT ($(du -h "$OUT" | cut -f1))"

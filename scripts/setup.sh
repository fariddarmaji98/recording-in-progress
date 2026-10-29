#!/usr/bin/env bash
# Sets up meetrec on macOS / Linux: virtualenv, every dependency, and a check that nothing is missing.
#
#   scripts/setup.sh                 # panel + rekam + analisis
#   scripts/setup.sh --transcribe    # plus faster-whisper (opsional, besar)
#
# Safe to run again: it only installs what is missing. `meetrec doctor --fix` does the same check later.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

pick_python() {
  for c in "${PYTHON:-}" python3.13 python3.12 python3.11 python3; do
    [ -n "$c" ] && command -v "$c" >/dev/null 2>&1 || continue
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then echo "$c"; return; fi
  done
  return 1
}

PY="$(pick_python)" || { echo "Butuh Python 3.11 atau lebih baru (macOS: brew install python@3.13)."; exit 1; }

if [ "$(uname -s)" = "Darwin" ]; then
  MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
  MINOR="$(sw_vers -productVersion | cut -d. -f2)"
  if [ "$MAJOR" -lt 14 ] || { [ "$MAJOR" -eq 14 ] && [ "${MINOR:-0}" -lt 2 ]; }; then
    echo "PERINGATAN: macOS $(sw_vers -productVersion). Rekam audio sistem butuh macOS 14.2 atau lebih baru."
  fi
  # An Intel (x86_64) Python on an Apple Silicon Mac cannot load the arm64 Core Audio library.
  if [ "$(uname -m)" = "arm64" ] && [ "$("$PY" -c 'import platform; print(platform.machine())')" != "arm64" ]; then
    echo "PERINGATAN: $PY berjalan sebagai x86_64 di Mac Apple Silicon. Pakai Python arm64 (python.org universal2 atau brew)."
  fi
fi

if [ ! -x .venv/bin/python ]; then
  echo "== membuat .venv ($("$PY" --version))"
  "$PY" -m venv .venv
fi

EXTRA=""
[ "${1:-}" = "--transcribe" ] && EXTRA="[transcribe]"

echo "== memasang dependency"
.venv/bin/python -m pip install --disable-pip-version-check --upgrade pip
# catap keeps its Core Audio library (libcatap_coreaudio.dylib) only in the wheel: never let pip build it from source.
.venv/bin/python -m pip install --disable-pip-version-check --only-binary catap -e ".$EXTRA"

echo "== cek"
.venv/bin/meetrec doctor --fix

echo
echo "Selesai. Jalankan:  $ROOT/.venv/bin/meetrec"

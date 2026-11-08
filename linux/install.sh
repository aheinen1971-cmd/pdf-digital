#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"
test "$(uname -m)" = x86_64 || { echo 'Dieses Testpaket benötigt Linux x86-64.' >&2; exit 1; }
python3.12 -c 'import sys; assert sys.version_info[:2] == (3, 12)'
sha256sum -c SHA256SUMS
python3.12 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install --no-index --find-links "$ROOT/wheels" -r "$ROOT/requirements.txt"
printf '%s\n' 'Fertig. Start: ./start.sh (optional mit PDF-Dateipfad).'

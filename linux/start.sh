#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -x "$ROOT/.venv/bin/python" ]; then
    printf '%s\n' 'Bitte zuerst ./install.sh ausführen.' >&2
    exit 1
fi
exec "$ROOT/.venv/bin/python" "$ROOT/app/pdf_werkbank.py" "$@"

#!/bin/bash
# Construit l'application Mac sur cet ordinateur (architecture de ce Mac) : dist/Nasdaq100.app et un ZIP.
set -e
cd "$(dirname "$0")/.."
[ -x .venv/bin/python ] || bash installer-mac.command
.venv/bin/python -m pip install --quiet pyinstaller
.venv/bin/python -m PyInstaller packaging/nasdaq100.spec --noconfirm
ditto -c -k --keepParent dist/Nasdaq100.app "dist/Nasdaq100-Mac-$(uname -m).zip"
echo "✓ dist/Nasdaq100.app et dist/Nasdaq100-Mac-$(uname -m).zip"

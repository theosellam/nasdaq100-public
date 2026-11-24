#!/bin/bash
# Construit l'application Mac sur cet ordinateur (architecture de ce Mac) : dist/Volatilistat.app et un ZIP.
set -e
cd "$(dirname "$0")/.."
[ -x .venv/bin/python ] || bash installer-mac.command
.venv/bin/python -m pip install --quiet pyinstaller pillow
.venv/bin/python -m PyInstaller packaging/volatilistat.spec --noconfirm
ditto -c -k --keepParent dist/Volatilistat.app "dist/Volatilistat-Mac-$(uname -m).zip"
echo "✓ dist/Volatilistat.app et dist/Volatilistat-Mac-$(uname -m).zip"

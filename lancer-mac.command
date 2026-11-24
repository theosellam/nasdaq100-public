#!/bin/bash
# Lance le tableau de bord (http://localhost:8100). Fermez cette fenêtre ou Ctrl+C pour l'arrêter.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  bash ./installer-mac.command || exit 1
fi
export PYTHONWARNINGS=ignore
.venv/bin/python -W ignore ndx.py serve

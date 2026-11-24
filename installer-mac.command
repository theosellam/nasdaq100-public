#!/bin/bash
# Installation sur Mac : crée un environnement Python isolé (.venv) et installe les dépendances.
cd "$(dirname "$0")" || exit 1
echo "== Installation de Volatilistat =="

PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
if [ -z "$PY" ]; then
  echo ""
  echo "Python 3.11 ou plus récent est nécessaire (celui de macOS est trop ancien)."
  echo "Installez Python 3.13 depuis https://www.python.org/downloads/macos/ puis relancez ce fichier."
  open "https://www.python.org/downloads/macos/" 2>/dev/null
  read -r -p "Appuyez sur Entrée pour fermer…" _
  exit 1
fi
echo "Python utilisé : $PY ($("$PY" --version))"

"$PY" -m venv .venv || { echo "Échec de la création de l'environnement .venv"; read -r -p "Entrée pour fermer…" _; exit 1; }
echo "Installation des dépendances (≈ 1 min)…"
.venv/bin/python -m pip install --upgrade pip --quiet
.venv/bin/python -m pip install --quiet -r requirements.txt || { echo "Échec de l'installation des dépendances"; read -r -p "Entrée pour fermer…" _; exit 1; }
chmod +x lancer-mac.command 2>/dev/null
echo ""
if [ -n "$NDX_FROM_LAUNCHER" ]; then
  echo "✓ Installation terminée. Démarrage du tableau de bord…"
else
  echo "✓ Installation terminée. Lancez maintenant « lancer-mac.command » (double-clic)."
  read -r -p "Appuyez sur Entrée pour fermer…" _
fi

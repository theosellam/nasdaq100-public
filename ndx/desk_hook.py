"""Hook Claude Code (PreToolUse) : les limites du desk vivent hors du modèle.

Branché dans .claude/settings.json. Code de sortie 2 = l'outil est refusé et la raison est renvoyée à l'agent.
- « ndx.py desk buy … » : le risk manager contrôle l'ordre AVANT son exécution (même règles que le courtier) ;
- les fichiers du desk (limites, exécutions, registre des décisions) ne peuvent pas être modifiés par un agent :
  seules les commandes « ndx.py desk … » y écrivent.
Fonctionne avec la bibliothèque standard seule (le contrôle d'ordre relance ndx.py avec le même Python).
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

PROTECTED = re.compile(r"private[/\\]desk|fills\.csv|decisions\.jsonl")
READ_ONLY = {"cat", "head", "tail", "less", "more", "grep", "rg", "wc", "ls", "stat", "file"}


def _block(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(2)


def main() -> None:
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return
    tool, inp = ev.get("tool_name", ""), ev.get("tool_input") or {}
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = str(inp.get("file_path") or inp.get("notebook_path") or "")
        if PROTECTED.search(path):
            _block("Refusé par le desk : les limites, les exécutions et le registre des décisions ne se modifient pas "
                   "à la main. Utilisez « python3 ndx.py desk … » (les limites se changent par l'utilisateur, pas par un agent).")
        return
    if tool != "Bash":
        return
    cmd = str(inp.get("command") or "")
    if PROTECTED.search(cmd):
        first = cmd.strip().split()[0] if cmd.strip() else ""
        if first not in READ_ONLY or re.search(r"[>|]|tee|sed\s+-i", cmd):
            _block("Refusé par le desk : commande qui touche aux fichiers protégés du desk. "
                   "Lecture seule autorisée ; les écritures passent par « python3 ndx.py desk … ».")
    m = re.search(r"ndx\.py\s+desk\s+buy\b(.*)", cmd)
    if not m:
        return
    rest = re.split(r"\s*(?:&&|\|\||;|\|)\s*", m.group(1))[0]
    try:
        args = shlex.split(rest)
    except ValueError:
        _block("Refusé par le desk : ordre illisible.")
    root = Path(ev.get("cwd") or ".")
    script = root / "ndx.py"
    if not script.exists():
        script = Path(__file__).resolve().parent.parent / "ndx.py"
    try:
        r = subprocess.run([sys.executable, "-W", "ignore", str(script), "desk", "check", *args],
                           capture_output=True, text=True, timeout=90, cwd=str(script.parent))
    except Exception as e:
        _block(f"Refusé par le desk : contrôle de risque impossible ({e}). Aucun ordre sans contrôle.")
    if r.returncode == 2:
        _block("Refusé par le risk manager (code) AVANT le courtier :\n" + r.stdout[-3000:])
    if r.returncode != 0:
        _block("Refusé par le desk : le contrôle de risque a échoué, aucun ordre sans contrôle.\n" + (r.stderr or r.stdout)[-1500:])


if __name__ == "__main__":
    main()

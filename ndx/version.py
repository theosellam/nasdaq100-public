"""Version de Volatilistat et vérification d'une nouvelle version publiée sur GitHub.

Une seule requête anonyme vers l'API publique de GitHub (aucune donnée personnelle envoyée),
au démarrage puis toutes les 12 heures. Désactivable avec la variable NDX_NO_UPDATE_CHECK=1.
Quand vous publiez une nouvelle version : mettez __version__ à jour AVANT de créer l'étiquette vX.Y
(la construction automatique refuse une étiquette qui ne correspond pas)."""
from __future__ import annotations

import json
import os
import re
import ssl
import threading
import time
import urllib.request

__version__ = "1.5.1"

REPO = "theosellam/nasdaq100-public"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
_API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
_EVERY = 12 * 3600

_info: dict = {"current": __version__, "latest": None, "newer": False, "url": RELEASES_URL}
_lock = threading.Lock()


def _parse(v: str) -> tuple[int, ...]:
    """« v1.2.1 » → (1, 2, 1) ; ignore tout suffixe non numérique."""
    return tuple(int(x) for x in re.findall(r"\d+", v.split("-")[0])[:3])


def is_newer(latest: str, current: str | None = None) -> bool:
    current = current or __version__
    try:
        a, b = _parse(latest), _parse(current)
    except ValueError:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi                       # l'application autonome n'a pas toujours accès aux certificats du système
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def check_now(timeout: float = 6.0) -> dict:
    req = urllib.request.Request(_API_URL, headers={"Accept": "application/vnd.github+json",
                                                    "User-Agent": f"Volatilistat/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
            rel = json.load(r)
        tag = str(rel.get("tag_name") or "")
        if tag and not rel.get("draft") and not rel.get("prerelease"):
            with _lock:
                url = str(rel.get("html_url") or "")
                _info.update(latest=tag.lstrip("vV"), newer=is_newer(tag),
                             url=url if url.startswith(f"https://github.com/{REPO}/") else RELEASES_URL)
    except Exception as e:                   # hors ligne, GitHub indisponible… : on réessaiera plus tard
        print(f"  (vérification de mise à jour impossible : {e})")
    return info()


def info() -> dict:
    with _lock:
        return dict(_info)


def start_background_check() -> None:
    if os.environ.get("NDX_NO_UPDATE_CHECK"):
        return

    def loop() -> None:
        time.sleep(3)                        # laisse le démarrage commencer
        while True:
            res = check_now()
            if res["newer"]:
                print(f"→ Nouvelle version disponible : {res['latest']} (vous avez {res['current']}) — {res['url']}")
            time.sleep(_EVERY)

    threading.Thread(target=loop, daemon=True, name="update-check").start()

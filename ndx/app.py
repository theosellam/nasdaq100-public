"""Volatilistat, version « application » (Mac .app / Windows .exe) : une petite fenêtre qui démarre le serveur local,
ouvre le tableau de bord dans le navigateur et permet de tout arrêter proprement.

Sans terminal, les messages du serveur vont dans data/../journal.txt (bouton « Dossier des données »)."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser


def _redirect_output() -> str:
    """Application sans terminal : stdout/stderr sont absents ; on écrit dans un fichier journal."""
    from .data import DATA_DIR
    log_path = DATA_DIR.parent / "journal.txt"
    f = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = f
    return str(log_path)


def _open_folder(path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


def main() -> None:
    log_path = _redirect_output() if getattr(sys, "frozen", False) or sys.stdout is None else None
    import tkinter as tk
    from tkinter import messagebox

    from . import server, version
    from .data import DATA_DIR

    try:
        httpd, url, boot = server.start(int(os.environ.get("PORT", 8100)))
    except SystemExit as e:
        tk.Tk().withdraw()
        messagebox.showerror("Volatilistat", str(e))
        return
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    root = tk.Tk()
    root.title("Volatilistat")
    try:                                    # emblème dans la fenêtre et la barre des tâches
        from pathlib import Path
        logo = tk.PhotoImage(file=str(Path(__file__).with_name("volatilistat-128.png"))).subsample(2, 2)
        root.iconphoto(True, logo)
    except Exception:
        logo = None
    root.resizable(False, False)
    pad = {"padx": 16}
    head = tk.Frame(root)
    head.pack(anchor="w", pady=(14, 2), **pad)
    if logo is not None:
        tk.Label(head, image=logo).pack(side="left", padx=(0, 10))
    tk.Label(head, text="Volatilistat", font=("Helvetica", 20, "bold")).pack(side="left")
    tk.Label(head, text=f"  Nasdaq 100 · version {version.__version__}", fg="#555").pack(side="left")
    tk.Label(root, text=f"Le tableau de bord tourne sur cet ordinateur : {url}", fg="#555").pack(anchor="w", **pad)
    status = tk.Label(root, text="Démarrage…", justify="left", wraplength=440)
    status.pack(anchor="w", pady=(10, 4), **pad)
    tk.Label(root, text="Gardez cette fenêtre ouverte tant que vous utilisez le tableau de bord.\n"
                        "Outil d'analyse uniquement — pas un conseil en investissement.",
             fg="#777", justify="left", font=("Helvetica", 11)).pack(anchor="w", pady=(4, 10), **pad)

    upd = tk.Frame(root)                     # nouvelle version disponible (masqué tant qu'il n'y en a pas)
    upd_label = tk.Label(upd, fg="#1F6F63", font=("Helvetica", 12, "bold"), justify="left")
    upd_label.pack(side="left")
    tk.Button(upd, text="Télécharger", command=lambda: webbrowser.open(version.info()["url"])).pack(side="left", padx=8)

    bar = tk.Frame(root)
    bar.pack(fill="x", pady=(0, 14), **pad)

    def quit_app() -> None:
        try:
            httpd.shutdown()
        except Exception:
            pass
        root.destroy()

    tk.Button(bar, text="Ouvrir le tableau de bord", command=lambda: webbrowser.open(url)).pack(side="left")
    tk.Button(bar, text="Dossier des données", command=lambda: _open_folder(DATA_DIR.parent)).pack(side="left", padx=8)
    tk.Button(bar, text="Quitter", command=quit_app).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", quit_app)

    def refresh_status() -> None:
        if boot.error:
            txt = f"⚠ {boot.error}" + (f"\nDétails : {log_path}" if log_path else "")
        elif boot.state is None:
            txt = f"⏳ {boot.status}\n(premier lancement : quelques minutes ; la page s'affiche dès que c'est prêt)"
        else:
            lab = getattr(boot.state, "lab_status", "")
            txt = "✓ Données prêtes — cours rafraîchis automatiquement." + (
                f"\nLaboratoire : {lab}" if lab and lab != "prêt" else "")
        status.config(text=txt)
        u = version.info()
        if u["newer"] and not upd.winfo_ismapped():
            upd_label.config(text=f"Nouvelle version disponible : {u['latest']}")
            upd.pack(anchor="w", pady=(0, 10), before=bar, **pad)
        root.after(2000, refresh_status)

    refresh_status()
    root.mainloop()
    os._exit(0)        # arrête aussi les téléchargements encore en cours en arrière-plan

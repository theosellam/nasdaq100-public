# Recette PyInstaller de Volatilistat : application autonome (Python inclus) pour Mac (.app) et Windows (.exe).
# Construction : pyinstaller packaging/volatilistat.spec --noconfirm   (depuis la racine du projet)
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
datas = [(str(ROOT / "ndx" / "template.html"), "ndx"), (str(ROOT / "ndx" / "volatilistat-128.png"), "ndx"),
         (str(ROOT / "data" / "events.json"), "data")]
ICON = str(ROOT / "packaging" / "icon" / "volatilistat.png")   # converti en .icns / .ico par PyInstaller (Pillow)
binaries, hiddenimports = [], collect_submodules("ndx")
for pkg in ("curl_cffi", "yfinance", "tzdata", "certifi"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

a = Analysis([str(ROOT / "Volatilistat.py")], pathex=[str(ROOT)], datas=datas, binaries=binaries,
             hiddenimports=hiddenimports + ["pypdf", "openpyxl", "lxml.etree", "lxml.html"],
             excludes=["matplotlib", "IPython", "pytest", "scipy"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Volatilistat", console=False, icon=ICON,
          disable_windowed_traceback=False, argv_emulation=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Volatilistat", upx=False)
if sys.platform == "darwin":
    app = BUNDLE(coll, name="Volatilistat.app", icon=ICON, bundle_identifier="fr.volatilistat.app",
                 info_plist={"CFBundleDisplayName": "Volatilistat", "CFBundleShortVersionString": "1.0",
                             "NSHighResolutionCapable": True, "LSMinimumSystemVersion": "11.0"})

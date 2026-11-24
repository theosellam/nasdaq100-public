# Recette PyInstaller : application autonome (Python inclus) pour Mac (.app) et Windows (.exe).
# Construction : pyinstaller packaging/nasdaq100.spec --noconfirm   (depuis la racine du projet)
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
datas = [(str(ROOT / "ndx" / "template.html"), "ndx"), (str(ROOT / "data" / "events.json"), "data")]
binaries, hiddenimports = [], collect_submodules("ndx")
for pkg in ("curl_cffi", "yfinance", "tzdata", "certifi"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

a = Analysis([str(ROOT / "Nasdaq100.py")], pathex=[str(ROOT)], datas=datas, binaries=binaries,
             hiddenimports=hiddenimports + ["pypdf", "openpyxl", "lxml.etree", "lxml.html"],
             excludes=["matplotlib", "IPython", "pytest", "scipy"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Nasdaq100", console=False,
          disable_windowed_traceback=False, argv_emulation=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Nasdaq100", upx=False)
if sys.platform == "darwin":
    app = BUNDLE(coll, name="Nasdaq100.app", bundle_identifier="io.github.nasdaq100.dashboard",
                 info_plist={"CFBundleDisplayName": "Nasdaq 100", "CFBundleShortVersionString": "1.0",
                             "NSHighResolutionCapable": True, "LSMinimumSystemVersion": "11.0"})

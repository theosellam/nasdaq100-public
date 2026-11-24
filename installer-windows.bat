@echo off
rem Installation sous Windows : crée un environnement Python isolé (.venv) et installe les dépendances.
chcp 65001 >nul
cd /d "%~dp0"
echo == Installation du tableau de bord Nasdaq 100 ==
set "PY="
where py >nul 2>&1 && py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY ( where python >nul 2>&1 && python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PY=python" )
if not defined PY (
  echo.
  echo Python 3.11 ou plus recent est necessaire.
  echo Installez Python 3.13 depuis https://www.python.org/downloads/windows/
  echo en cochant "Add python.exe to PATH", puis relancez ce fichier.
  start "" "https://www.python.org/downloads/windows/"
  pause
  exit /b 1
)
echo Python utilise : %PY%
%PY% -m venv .venv || ( echo Echec de la creation de .venv & pause & exit /b 1 )
echo Installation des dependances, environ 1 min...
.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt || ( echo Echec de l installation des dependances & pause & exit /b 1 )
echo.
if defined NDX_FROM_LAUNCHER (
  echo Installation terminee. Demarrage du tableau de bord...
) else (
  echo Installation terminee. Lancez maintenant lancer-windows.bat - double-clic.
  pause
)

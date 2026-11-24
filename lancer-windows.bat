@echo off
rem Lance le tableau de bord (http://localhost:8100). Fermez cette fenetre pour l arreter.
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call installer-windows.bat
if not exist ".venv\Scripts\python.exe" exit /b 1
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe -W ignore ndx.py serve
pause

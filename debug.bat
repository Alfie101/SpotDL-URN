@echo off
setlocal
echo Script folder: %~dp0
where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
echo Using launcher: %PY%
%PY% -V
%PY% -c "import sys,tkinter as tk;print('Python OK ->', sys.executable)"
echo ---- launching GUI ----
%PY% "%~dp0spotdl_gui.py"
echo ---- process exited, code %errorlevel% ----
pause

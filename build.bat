@echo off
rem Builds dist\Foxi.exe: ONE portable file (UI, HidHide installer, cursors, icon all packed inside).
rem It asks for admin itself and keeps its profile/log in a data\ folder next to it.
rem Everything the build reads/writes stays in this folder (run setup.bat once first).
cd /d %~dp0
set PYINSTALLER_CONFIG_DIR=%~dp0.cache\pyinstaller
set TEMP=%~dp0.cache\tmp
set TMP=%~dp0.cache\tmp
if not exist "%TEMP%" mkdir "%TEMP%"
.venv\Scripts\python engine.py --test || exit /b 1
.venv\Scripts\python diag.py || exit /b 1
.venv\Scripts\python tools\make_icon.py || exit /b 1
.venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed --uac-admin --name Foxi ^
  --icon "%~dp0assets\foxi.ico" --workpath .cache\build --specpath .cache ^
  --add-data "%~dp0vendor\hidhide;vendor\hidhide" --add-data "%~dp0vendor\cursors;vendor\cursors" ^
  --add-data "%~dp0ui;ui" --add-data "%~dp0assets;assets" --exclude-module tkinter foxi.py

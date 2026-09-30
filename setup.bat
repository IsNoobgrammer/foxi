@echo off
rem One-time dev setup, fully inside this folder: uv -> tools, Python -> .python, packages -> .venv, caches -> .cache.
cd /d %~dp0
if not exist .cache mkdir .cache
set UV_CACHE_DIR=%~dp0.cache\uv
set UV_PYTHON_INSTALL_DIR=%~dp0.python
set UV_LINK_MODE=copy
if not exist tools\uv.exe (
  echo Downloading uv...
  curl -sSL -o .cache\uv.zip https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip || exit /b 1
  tar -xf .cache\uv.zip -C tools uv.exe || exit /b 1
)
if not exist vendor\hidhide\HidHide_1.5.230_x64.exe (
  echo Downloading the HidHide installer ^(MIT, bundled into Foxi.exe^)...
  curl -sSL -o vendor\hidhide\HidHide_1.5.230_x64.exe https://github.com/nefarius/HidHide/releases/download/v1.5.230.0/HidHide_1.5.230_x64.exe || exit /b 1
)
tools\uv.exe python install 3.12 || exit /b 1
if exist .venv rmdir /s /q .venv
tools\uv.exe venv .venv --python 3.12 --python-preference only-managed || exit /b 1
tools\uv.exe pip install --python .venv\Scripts\python.exe pyinstaller pywebview pillow || exit /b 1

@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Install the dependencies as described in README.md first.
  pause
  exit /b 1
)
if not exist "data\library\index.sqlite" (
  echo The private index is missing. See README.md for the demo or import instructions.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" launch.py
if errorlevel 1 pause

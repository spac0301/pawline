@echo off
setlocal
set "PYTHONNOUSERSITE=1"
set "QT_PLUGIN_PATH="
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo Create the private Windows runtime using WINDOWS.md first.
  exit /b 1
)
"%~dp0.venv\Scripts\pythonw.exe" -B "%~dp0run.py" pet %*

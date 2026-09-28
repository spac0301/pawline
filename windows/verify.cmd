@echo off
setlocal
cd /d "%~dp0.."
set "PYTHONNOUSERSITE=1"
set "QT_PLUGIN_PATH="
if not exist ".venv\Scripts\python.exe" (
  echo Create the Windows runtime using WINDOWS.md first.
  exit /b 1
)
".venv\Scripts\python.exe" -B -m unittest discover -s tests -p "test_*.py" -v
if errorlevel 1 exit /b 1
set "QT_QPA_PLATFORM=offscreen"
".venv\Scripts\python.exe" -B tests\verify_qt_ui.py verification\qt
if errorlevel 1 exit /b 1
echo Local checks passed. Complete the manual Windows checklist in WINDOWS.md.

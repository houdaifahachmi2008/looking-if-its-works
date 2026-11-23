@echo off
REM ViralBot - double-click to start the dashboard (Windows)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First start: installing ViralBot, this takes a few minutes...
  py -m venv .venv
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
echo Starting ViralBot dashboard... (close this window to stop it)
".venv\Scripts\python.exe" -m streamlit run dashboard\app.py
pause

@echo off
title Sitevo
cd /d "%~dp0"

set PY=
where py >nul 2>nul && set PY=py
if not defined PY ( where python >nul 2>nul && set PY=python )
if not defined PY (
  echo.
  echo  Python is nog niet geinstalleerd.
  echo  Download het via https://www.python.org/downloads/
  echo  BELANGRIJK: vink tijdens de installatie "Add python.exe to PATH" aan.
  echo.
  start "" https://www.python.org/downloads/
  pause
  exit /b
)

if not exist ".venv\Scripts\python.exe" (
  echo  Eerste keer opstarten: alles wordt klaargezet. Dit duurt een minuutje...
  %PY% -m venv .venv
)
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo  Installeren mislukt. Controleer je internetverbinding en probeer opnieuw.
  pause
  exit /b
)

echo.
echo  Sitevo start op. Laat dit venster open zolang je de app gebruikt.
echo  Sluit dit venster om de app te stoppen.
echo.
".venv\Scripts\python.exe" -m leadgen.app
pause

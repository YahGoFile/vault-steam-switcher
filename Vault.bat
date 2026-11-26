@echo off
REM ====================================================================
REM  Vault - one launcher for everything.
REM    Vault.bat          install (first run only) and start the app
REM    Vault.bat build    build a standalone Vault.exe into dist\
REM    Vault.bat test     run the test suite
REM ====================================================================
setlocal enableextensions
cd /d "%~dp0"
set "VENV=%~dp0.venv"
set "PY=%VENV%\Scripts\python.exe"

REM --- locate a Python 3 interpreter ----------------------------------
set "BOOT="
where py >nul 2>&1 && set "BOOT=py -3"
if not defined BOOT (
  where python >nul 2>&1 && set "BOOT=python"
)
if not defined BOOT (
  echo.
  echo   Python 3 is not installed, or not on PATH.
  echo   Install it from https://www.python.org/downloads/  ^(tick "Add to PATH"^)
  echo   then run this again.
  echo.
  pause
  exit /b 1
)

REM --- first-run setup: venv + dependencies ---------------------------
if not exist "%PY%" (
  echo.
  echo   First run - setting things up. This takes a minute...
  echo.
  %BOOT% -m venv "%VENV%" || goto :fail
  "%PY%" -m pip install --upgrade pip >nul
  "%PY%" -m pip install -r "%~dp0requirements.txt" || goto :fail
  echo   Setup complete.
  echo.
)

REM --- subcommands ----------------------------------------------------
if /i "%~1"=="test" (
  "%PY%" -m pip install pytest >nul 2>&1
  "%PY%" -m pytest -q
  pause
  exit /b 0
)

if /i "%~1"=="build" (
  echo   Building a standalone Vault.exe ^(this can take a few minutes^)...
  "%PY%" -m pip install pyinstaller >nul
  "%PY%" -m PyInstaller --noconfirm --clean --windowed --onefile ^
     --name Vault --collect-all customtkinter app.py || goto :fail
  echo.
  echo   Done:  "%~dp0dist\Vault.exe"
  echo.
  pause
  exit /b 0
)

REM --- default: launch the app ----------------------------------------
start "" "%VENV%\Scripts\pythonw.exe" "%~dp0app.py"
exit /b 0

:fail
echo.
echo   Something went wrong during setup. Scroll up for the error.
echo.
pause
exit /b 1

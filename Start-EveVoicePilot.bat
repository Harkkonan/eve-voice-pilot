@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Setting up EVE Voice Pilot. This may take a minute the first time.
    py -3 -m venv .venv
    if errorlevel 1 goto failed
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    if errorlevel 1 goto failed
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto failed
)

set "PYTHONPATH=%CD%\src"
".venv\Scripts\python.exe" -m eve_voice_pilot
goto done

:failed
echo.
echo Setup failed. Please copy the text in this window and share it with Codex.
pause

:done
endlocal

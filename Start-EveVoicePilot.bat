@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Setting up EVE Voice Pilot. This may take a minute the first time.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\setup.ps1"
    if errorlevel 1 goto failed
)

if not exist "models\vosk-model-small-en-us-0.15\conf\model.conf" (
    echo Installing local speech model. This may take a minute.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\setup.ps1"
    if errorlevel 1 goto failed
)

".venv\Scripts\python.exe" -c "import sounddevice, vosk, websocket" >nul 2>nul
if errorlevel 1 (
    echo Updating EVE Voice Pilot packages.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\setup.ps1"
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

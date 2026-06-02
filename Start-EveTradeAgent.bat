@echo off
setlocal
cd /d "%~dp0"

set "PYTHONPATH=%CD%\src"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m eve_voice_pilot.trade_agent %*
) else (
    python -m eve_voice_pilot.trade_agent %*
)

endlocal

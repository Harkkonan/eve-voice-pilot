@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_corp_intel_board.ps1" serve --watch-local --open-browser --channels "Corp,Corporation,Fleet,Alliance,Local,*Intel*"
pause

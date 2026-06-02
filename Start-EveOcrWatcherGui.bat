@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\run_ocr_watcher_gui.ps1"
if errorlevel 1 goto failed
goto done

:failed
echo.
echo OCR watcher GUI stopped with an error. Please copy the text in this window and share it with Codex.
pause

:done
endlocal

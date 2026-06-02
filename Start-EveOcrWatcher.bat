@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\run_ocr_watcher.ps1" %*
if errorlevel 1 goto failed
goto done

:failed
echo.
echo OCR watcher stopped with an error. Please copy the text in this window and share it with Codex.
pause

:done
endlocal

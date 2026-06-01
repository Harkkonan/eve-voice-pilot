$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    & "$PSScriptRoot\setup.ps1"
}

$modelConfig = Join-Path $root "models\vosk-model-small-en-us-0.15\conf\model.conf"
$needsSetup = -not (Test-Path -LiteralPath $modelConfig)
if (-not $needsSetup) {
    & ".venv\Scripts\python.exe" -c "import sounddevice, vosk, websocket" 2>$null
    $needsSetup = $LASTEXITCODE -ne 0
}
if ($needsSetup) {
    & "$PSScriptRoot\setup.ps1"
}

$env:PYTHONPATH = Join-Path $root "src"
& ".venv\Scripts\python.exe" -m eve_voice_pilot

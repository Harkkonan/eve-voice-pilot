$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    & "$PSScriptRoot\setup.ps1"
}

$env:PYTHONPATH = Join-Path $root "src"
& ".venv\Scripts\python.exe" -m eve_voice_pilot

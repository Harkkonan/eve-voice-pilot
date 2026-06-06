$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    py -3 -m venv .venv
}

$env:PYTHONPATH = Join-Path $root "src"
& ".venv\Scripts\python.exe" -m eve_voice_pilot.intel_pet @args

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    py -3 -m venv .venv
}

& ".venv\Scripts\python.exe" -c "import jwt, cryptography" 2>$null
if ($LASTEXITCODE -ne 0) {
    & ".venv\Scripts\python.exe" -m pip install --upgrade pip
    & ".venv\Scripts\python.exe" -m pip install -r requirements.txt
}

$env:PYTHONPATH = Join-Path $root "src"
& ".venv\Scripts\python.exe" -m eve_voice_pilot.corp_market @args

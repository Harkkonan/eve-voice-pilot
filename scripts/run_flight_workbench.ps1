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

$bridgeUserEnvNames = @(
    "CORP_MARKET_SSO_CLIENT_ID",
    "CORP_MARKET_SSO_CLIENT_SECRET",
    "EVE_SSO_CLIENT_ID",
    "EVE_SSO_CLIENT_SECRET",
    "CORP_MARKET_ADMIN_TOKEN",
    "CORP_MARKET_DISCORD_WEBHOOK_URL",
    "CORP_MARKET_DISCORD_FORUM_TAG_IDS",
    "CORP_MARKET_DISCORD_FORUM_TAG_MAP",
    "CORP_MARKET_PUBLIC_BASE_URL",
    "CORP_MARKET_SSO_CALLBACK_URL",
    "CORP_MARKET_ALLOWED_CORPORATION_IDS",
    "CORP_MARKET_ALLOWED_ALLIANCE_IDS",
    "CORP_MARKET_PUBLIC_HOSTING_MODE",
    "CORP_MARKET_TRUSTED_MEMBERS_CAN_WRITE_MARKET"
)

foreach ($name in $bridgeUserEnvNames) {
    if (-not [Environment]::GetEnvironmentVariable($name, "Process")) {
        $userValue = [Environment]::GetEnvironmentVariable($name, "User")
        if ($userValue) {
            Set-Item -Path "Env:$name" -Value $userValue
        }
    }
}

$env:PYTHONPATH = Join-Path $root "src"
& ".venv\Scripts\python.exe" -m eve_voice_pilot.flight_workbench @args

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Corp Intel Board has been retired."
Write-Host "This script no longer starts a dashboard server or remote upload agent."
Write-Host "See docs\retired_features.md for the retirement note."
exit 1

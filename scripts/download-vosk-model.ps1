param(
    [ValidatePattern("^[A-Za-z0-9_.-]+$")]
    [string]$ModelName = "vosk-model-small-en-us-0.15"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$modelsRoot = Join-Path $root "models"
$modelName = $ModelName
$modelDir = Join-Path $modelsRoot $modelName
$modelConfig = Join-Path $modelDir "conf\model.conf"
$zipPath = Join-Path $modelsRoot "$modelName.zip"
$url = "https://alphacephei.com/kaldi/models/$modelName.zip"

New-Item -ItemType Directory -Force -Path $modelsRoot | Out-Null

if (Test-Path -LiteralPath $modelConfig) {
    Write-Host "Local speech model is already installed at $modelDir"
    exit 0
}

if ((Test-Path -LiteralPath $modelDir) -and -not (Test-Path -LiteralPath $modelConfig)) {
    throw "A partial model folder exists at $modelDir. Delete that folder and run this script again."
}

if (-not (Test-Path -LiteralPath $zipPath)) {
    Write-Host "Downloading local speech model. This may take a minute."
    Invoke-WebRequest -Uri $url -OutFile $zipPath
}

Write-Host "Extracting local speech model."
Expand-Archive -LiteralPath $zipPath -DestinationPath $modelsRoot -Force

if (-not (Test-Path -LiteralPath $modelConfig)) {
    throw "Model download finished, but the expected model files were not found at $modelDir."
}

Write-Host "Local speech model is ready at $modelDir"

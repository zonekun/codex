# setup_codex_uv_min.ps1 - Minimal Codex uv environment recovery.
# Usage:
#   cd C:\Users\zonekun\Documents\codex\investment-agent
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup_codex_uv_min.ps1

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = "C:\Users\zonekun\Documents\codex\investment-agent"
$CodexVenvPath = Join-Path $ProjectRoot ".venv-codex"
$CodexUvCache = Join-Path $ProjectRoot ".uv-cache"
$CodexUvPython = Join-Path $ProjectRoot ".uv-python"
$Requirements = Join-Path $ProjectRoot "requirements-codex-min.txt"
$PythonRequest = "3.12"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv command not found in PATH."
}

if (-not (Test-Path -LiteralPath $Requirements)) {
    throw "Missing requirements file: $Requirements"
}

Set-Location $ProjectRoot

New-Item -ItemType Directory -Path $CodexUvCache -Force | Out-Null
New-Item -ItemType Directory -Path $CodexUvPython -Force | Out-Null

$env:UV_PROJECT_ENVIRONMENT = $CodexVenvPath
$env:UV_CACHE_DIR = $CodexUvCache
$env:UV_PYTHON_INSTALL_DIR = $CodexUvPython
$env:PYTHONUTF8 = "1"

Write-Host "=== Codex minimal uv bootstrap ===" -ForegroundColor Cyan
Write-Host "Project root : $ProjectRoot" -ForegroundColor DarkGray
Write-Host "Venv         : $CodexVenvPath" -ForegroundColor DarkGray
Write-Host "UV cache     : $CodexUvCache" -ForegroundColor DarkGray
Write-Host "UV python    : $CodexUvPython" -ForegroundColor DarkGray
Write-Host "Requirements : $Requirements" -ForegroundColor DarkGray

Write-Host "[1/4] Ensuring Python $PythonRequest is available..." -ForegroundColor Yellow
& uv python install $PythonRequest
if ($LASTEXITCODE -ne 0) {
    throw "uv python install failed with exit code $LASTEXITCODE"
}

Write-Host "[2/4] Recreating dedicated Codex venv..." -ForegroundColor Yellow
if (Test-Path -LiteralPath $CodexVenvPath) {
    Remove-Item -LiteralPath $CodexVenvPath -Recurse -Force
}
& uv venv --python $PythonRequest $CodexVenvPath
if ($LASTEXITCODE -ne 0) {
    throw "uv venv failed with exit code $LASTEXITCODE"
}

Write-Host "[3/4] Installing minimal packages..." -ForegroundColor Yellow
& uv pip install --python (Join-Path $CodexVenvPath "Scripts\python.exe") -r $Requirements
if ($LASTEXITCODE -ne 0) {
    throw "uv pip install failed with exit code $LASTEXITCODE"
}

Write-Host "[4/4] Verifying dedicated Codex venv..." -ForegroundColor Yellow
& (Join-Path $CodexVenvPath "Scripts\python.exe") --version
if ($LASTEXITCODE -ne 0) {
    throw "Dedicated Codex python verification failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "=== Codex minimal uv setup completed ===" -ForegroundColor Cyan
Write-Host "Use these env vars in Codex sessions:" -ForegroundColor Green
Write-Host "  UV_PROJECT_ENVIRONMENT=$CodexVenvPath"
Write-Host "  UV_CACHE_DIR=$CodexUvCache"
Write-Host "  UV_PYTHON_INSTALL_DIR=$CodexUvPython"
Write-Host "  PYTHONUTF8=1"

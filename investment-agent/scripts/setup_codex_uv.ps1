# setup_codex_uv.ps1 - Codex 専用 uv 環境セットアップ
# 実行: PowerShell で
#   cd C:\Users\zonekun\Documents\codex\investment-agent
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup_codex_uv.ps1

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = "C:\Users\zonekun\Documents\codex\investment-agent"
$CodexVenvPath = Join-Path $ProjectRoot ".venv-codex"
$CodexUvCache = Join-Path $ProjectRoot ".uv-cache"
$CodexUvPython = Join-Path $ProjectRoot ".uv-python"
$PythonRequest = "3.12"
$PythonCandidates = @(
    "C:\Program Files\Python312\python.exe",
    "C:\Users\zonekun\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Python312\python.exe"
)

function Resolve-ExistingPython {
    param(
        [string[]]$Candidates
    )

    foreach ($candidate in $Candidates) {
        try {
            if (Test-Path -LiteralPath $candidate) {
                return $candidate
            }
        } catch {
            continue
        }
    }

    return $null
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv command not found in PATH."
}

Set-Location $ProjectRoot

New-Item -ItemType Directory -Path $CodexUvCache -Force | Out-Null
New-Item -ItemType Directory -Path $CodexUvPython -Force | Out-Null

$env:UV_PROJECT_ENVIRONMENT = $CodexVenvPath
$env:UV_CACHE_DIR = $CodexUvCache
$env:UV_PYTHON_INSTALL_DIR = $CodexUvPython
$env:PYTHONUTF8 = "1"

Write-Host "=== Codex uv bootstrap ===" -ForegroundColor Cyan
Write-Host "Project root : $ProjectRoot" -ForegroundColor DarkGray
Write-Host "Venv         : $CodexVenvPath" -ForegroundColor DarkGray
Write-Host "UV cache     : $CodexUvCache" -ForegroundColor DarkGray
Write-Host "UV python    : $CodexUvPython" -ForegroundColor DarkGray

$pythonExe = Resolve-ExistingPython -Candidates $PythonCandidates
if ($null -ne $pythonExe) {
    Write-Host "Python       : $pythonExe" -ForegroundColor DarkGray
    Write-Host "[1/4] Using existing local Python..." -ForegroundColor Yellow
} else {
    Write-Host "Python req   : $PythonRequest (uv-managed)" -ForegroundColor DarkGray
    Write-Host "[1/4] Ensuring uv-managed Python is available..." -ForegroundColor Yellow
    & uv python install $PythonRequest
    if ($LASTEXITCODE -ne 0) {
        throw "uv python install failed with exit code $LASTEXITCODE"
    }
    $pythonExe = $PythonRequest
}

Write-Host "[2/4] Recreating dedicated Codex venv..." -ForegroundColor Yellow
if (Test-Path -LiteralPath $CodexVenvPath) {
    Remove-Item -LiteralPath $CodexVenvPath -Recurse -Force
}
& uv venv --python $PythonRequest $CodexVenvPath
if ($LASTEXITCODE -ne 0) {
    throw "uv venv failed with exit code $LASTEXITCODE"
}

Write-Host "[3/4] Syncing dependencies into dedicated Codex venv..." -ForegroundColor Yellow
& uv sync
if ($LASTEXITCODE -ne 0) {
    throw "uv sync failed with exit code $LASTEXITCODE"
}

Write-Host "[4/4] Verifying dedicated Codex venv..." -ForegroundColor Yellow
& (Join-Path $CodexVenvPath "Scripts\python.exe") --version
if ($LASTEXITCODE -ne 0) {
    throw "Dedicated Codex python verification failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "=== Codex uv setup completed ===" -ForegroundColor Cyan
Write-Host "Use these env vars in Codex sessions:" -ForegroundColor Green
Write-Host "  UV_PROJECT_ENVIRONMENT=$CodexVenvPath"
Write-Host "  UV_CACHE_DIR=$CodexUvCache"
Write-Host "  UV_PYTHON_INSTALL_DIR=$CodexUvPython"
Write-Host "  PYTHONUTF8=1"

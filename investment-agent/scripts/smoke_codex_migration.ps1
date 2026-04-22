# smoke_codex_migration.ps1 - Codex 移行 smoke チェック
# 前提:
#   - .venv-codex が作成済み
#   - PYTHONUTF8=1

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = "C:\Users\zonekun\Documents\codex\investment-agent"
$CodexVenvPath = Join-Path $ProjectRoot ".venv-codex"
$PythonExe = Join-Path $CodexVenvPath "Scripts\python.exe"
$PytestExe = Join-Path $CodexVenvPath "Scripts\pytest.exe"
$ScriptsToCheck = @(
    "scripts\download_monthly.py",
    "scripts\tdnet_load_parallel.py",
    "scripts\update_monthly_adapters.py",
    "scripts\extract_monthly_data.py",
    "scripts\monitor_backfill.py"
)

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [scriptblock]$Action
    )

    Write-Host ""
    Write-Host "=== $Label ===" -ForegroundColor Cyan
    & $Action
}

Set-Location $ProjectRoot
$env:PYTHONUTF8 = "1"
$env:UV_PROJECT_ENVIRONMENT = $CodexVenvPath
$env:UV_CACHE_DIR = Join-Path $ProjectRoot ".uv-cache"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $ProjectRoot ".uv-python"

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Codex python not found: $PythonExe"
}

Invoke-Step -Label "Python Version" -Action {
    & $PythonExe --version
    if ($LASTEXITCODE -ne 0) {
        throw "python --version failed with exit code $LASTEXITCODE"
    }
}

Invoke-Step -Label "Primary Script Help" -Action {
    foreach ($script in $ScriptsToCheck) {
        Write-Host "[HELP] $script" -ForegroundColor Yellow
        & $PythonExe $script --help *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "--help failed: $script (exit code $LASTEXITCODE)"
        }
    }
}

if (Test-Path -LiteralPath $PytestExe) {
    Invoke-Step -Label "Pytest Smoke" -Action {
        & $PytestExe -x --tb=short tests  *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "pytest smoke failed with exit code $LASTEXITCODE"
        }
    }
} else {
    Write-Host ""
    Write-Host "pytest.exe not found, skipping pytest smoke." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Codex migration smoke completed." -ForegroundColor Green

# setup_machine.ps1 - 新端末セットアップ（1回だけ実行）
# 実行: PowerShell で cd C:\gdrive\claude\investment-agent && .\setup_machine.ps1

$ErrorActionPreference = "Stop"
$VENV_PATH = "C:\venvs\investment-agent"

Write-Host "=== investment-agent 端末初期セットアップ ===" -ForegroundColor Cyan

# UV_PROJECT_ENVIRONMENT をユーザー環境変数に永続化
[Environment]::SetEnvironmentVariable("UV_PROJECT_ENVIRONMENT", $VENV_PATH, "User")
$env:UV_PROJECT_ENVIRONMENT = $VENV_PATH
Write-Host "[1/3] UV_PROJECT_ENVIRONMENT=$VENV_PATH を設定しました" -ForegroundColor Green

# venv 作成（なければ）
if (-not (Test-Path "$VENV_PATH\Scripts\python.exe")) {
    Write-Host "[2/3] venv を作成します..." -ForegroundColor Yellow
    & uv venv --python "C:\Program Files\Python312\python.exe" $VENV_PATH
} else {
    Write-Host "[2/3] venv は既に存在します: $VENV_PATH" -ForegroundColor Green
}

# uv sync
Write-Host "[3/3] uv sync を実行します..." -ForegroundColor Yellow
Set-Location "C:\gdrive\claude\investment-agent"
& uv sync

Write-Host ""
Write-Host "=== セットアップ完了 ===" -ForegroundColor Cyan
Write-Host "次回から claude を起動すれば自動で venv が使われます。"
Write-Host "環境変数を反映するため、このターミナルを一度閉じて再起動してください。"

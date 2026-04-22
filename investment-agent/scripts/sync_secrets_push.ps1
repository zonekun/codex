# sync_secrets_push.ps1
# ローカル（Windows）の非gitファイルをGCSにプッシュする
# 使い方: .\scripts\sync_secrets_push.ps1

$env:PATH += ";C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin"
$BUCKET = "gs://stock_data_1930932/config/investment-agent"
$PROJECT_ROOT = "$PSScriptRoot\.."

Write-Host "=== secrets push: local → GCS ==="

# .env
$envFile = Join-Path $PROJECT_ROOT ".env"
if (Test-Path $envFile) {
    cmd /c "gcloud storage cp `"$envFile`" $BUCKET/.env --project=gmailpj-357912"
    Write-Host "[OK] .env"
} else {
    Write-Host "[SKIP] .env not found"
}

# keys/gcp-service-account.json
$keyFile = Join-Path $PROJECT_ROOT "keys\gcp-service-account.json"
if (Test-Path $keyFile) {
    cmd /c "gcloud storage cp `"$keyFile`" $BUCKET/keys/gcp-service-account.json --project=gmailpj-357912"
    Write-Host "[OK] keys/gcp-service-account.json"
} else {
    Write-Host "[SKIP] keys/gcp-service-account.json not found"
}

# data/logs/ (会話ログ・ツールトレース)
$logsDir = Join-Path $PROJECT_ROOT "data\logs"
if (Test-Path $logsDir) {
    cmd /c "gcloud storage cp -r `"$logsDir\*`" $BUCKET/data/logs/ --project=gmailpj-357912"
    Write-Host "[OK] data/logs/"
} else {
    Write-Host "[SKIP] data/logs/ not found"
}

Write-Host "=== push complete ==="

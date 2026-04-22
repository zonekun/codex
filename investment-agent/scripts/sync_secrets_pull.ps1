# sync_secrets_pull.ps1
# GCSからWindowsローカルに非gitファイルをプルする
# 使い方: .\scripts\sync_secrets_pull.ps1

$env:PATH += ";C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin"
$BUCKET = "gs://stock_data_1930932/config/investment-agent"
$PROJECT_ROOT = "$PSScriptRoot\.."
$TMP = "$env:TEMP\inv_sync_pull"

Write-Host "=== secrets pull: GCS → local ==="

New-Item -ItemType Directory -Force -Path $TMP | Out-Null
New-Item -ItemType Directory -Force -Path "$PROJECT_ROOT\keys" | Out-Null
New-Item -ItemType Directory -Force -Path "$PROJECT_ROOT\data\logs" | Out-Null

# .env
cmd /c "gcloud storage cp $BUCKET/.env $TMP\.env --project=gmailpj-357912" 2>$null
if (Test-Path "$TMP\.env") {
    Copy-Item "$TMP\.env" "$PROJECT_ROOT\.env" -Force
    Write-Host "[OK] .env"
} else { Write-Host "[SKIP] .env not found in GCS" }

# keys/gcp-service-account.json
cmd /c "gcloud storage cp $BUCKET/keys/gcp-service-account.json $TMP\gcp-service-account.json --project=gmailpj-357912" 2>$null
if (Test-Path "$TMP\gcp-service-account.json") {
    Copy-Item "$TMP\gcp-service-account.json" "$PROJECT_ROOT\keys\gcp-service-account.json" -Force
    Write-Host "[OK] keys/gcp-service-account.json"
} else { Write-Host "[SKIP] keys/gcp-service-account.json not found in GCS" }

# data/logs/
New-Item -ItemType Directory -Force -Path "$TMP\logs" | Out-Null
cmd /c "gcloud storage cp -r $BUCKET/data/logs/* $TMP\logs\ --project=gmailpj-357912" 2>$null
$logFiles = Get-ChildItem "$TMP\logs" -ErrorAction SilentlyContinue
if ($logFiles) {
    Copy-Item "$TMP\logs\*" "$PROJECT_ROOT\data\logs\" -Force
    Write-Host "[OK] data/logs/"
} else { Write-Host "[SKIP] data/logs/ not found in GCS" }

Remove-Item $TMP -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "=== pull complete ==="

#!/bin/bash
# TDNET 2024年バックフィル #5-#23 Cloud Scheduler 一括作成
# 3.5時間間隔で submit モードを発火。ポーラーが自動resume。
# 使用後: bash scripts/cleanup_backfill_schedulers.sh で全削除

set -euo pipefail

PROJECT="gmailpj-357912"
LOCATION="us-west1"
SA="bq-loader@gmailpj-357912.iam.gserviceaccount.com"
API_URL="https://run.googleapis.com/v2/projects/${PROJECT}/locations/${LOCATION}/jobs/tdnet-load-parallel:run"

# バッチ定義: batch_id ticker_from ticker_to cron_utc(minute hour day month)
# 開始: 2026-04-08 22:00 JST = 13:00 UTC, 3.5h=210min間隔
BATCHES=(
  "5  2915 3221 0,13,8,4"
  "6  3222 3547 30,16,8,4"
  "7  3548 3891 0,20,8,4"
  "8  3892 4194 30,23,8,4"
  "9  4196 4488 0,3,9,4"
  "10 4489 4814 30,6,9,4"
  "11 4816 5255 0,10,9,4"
  "12 5256 5938 30,13,9,4"
  "13 5939 6247 0,17,9,4"
  "14 6248 6573 30,20,9,4"
  "15 6574 6957 0,0,10,4"
  "16 6958 7242 30,3,10,4"
  "17 7244 7610 0,7,10,4"
  "18 7611 7949 30,10,10,4"
  "19 7950 8334 0,14,10,4"
  "20 8336 8958 30,17,10,4"
  "21 8960 9284 0,21,10,4"
  "22 9285 9719 30,0,11,4"
  "23 9720 9997 0,4,11,4"
)

echo "=== TDNET Backfill 2024 Scheduler Setup ==="
echo "Batches: #5-#23 (19 total)"
echo "Interval: 3.5 hours"
echo "Start: 2026-04-08 22:00 JST"
echo "End:   2026-04-11 13:00 JST + resume ~3h"
echo ""

created=0
failed=0

for entry in "${BATCHES[@]}"; do
  read -r batch_id ticker_from ticker_to cron_parts <<< "$entry"

  # cron_parts: minute,hour,day,month
  IFS=',' read -r cmin chour cday cmonth <<< "$cron_parts"
  cron_expr="${cmin} ${chour} ${cday} ${cmonth} *"

  scheduler_name="tdnet-backfill-batch${batch_id}"

  body="{\"overrides\":{\"containerOverrides\":[{\"env\":[{\"name\":\"DATE_FROM\",\"value\":\"20240101\"},{\"name\":\"DATE_TO\",\"value\":\"20241231\"},{\"name\":\"RUN_MODE\",\"value\":\"submit\"},{\"name\":\"TICKER_FROM\",\"value\":\"${ticker_from}\"},{\"name\":\"TICKER_TO\",\"value\":\"${ticker_to}\"}]}]}}"

  # JST表示用
  jst_hour=$(( (chour + 9) % 24 ))
  jst_day=$cday
  if [ $((chour + 9)) -ge 24 ]; then
    jst_day=$((cday + 1))
  fi
  printf "Creating #%-2s  ticker %-4s-%-4s  04/%02d %02d:%02d JST ... " \
    "$batch_id" "$ticker_from" "$ticker_to" "$jst_day" "$jst_hour" "$cmin"

  if gcloud scheduler jobs create http "${scheduler_name}" \
    --project="${PROJECT}" \
    --location="${LOCATION}" \
    --schedule="${cron_expr}" \
    --uri="${API_URL}" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body="${body}" \
    --oauth-service-account-email="${SA}" \
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
    --time-zone="UTC" \
    --quiet 2>/dev/null; then
    echo "OK"
    ((created++))
  else
    echo "FAILED"
    ((failed++))
  fi
done

echo ""
echo "=== Result: ${created} created, ${failed} failed ==="
echo ""
echo "Cleanup command (after all complete):"
echo "  bash scripts/cleanup_backfill_schedulers.sh"

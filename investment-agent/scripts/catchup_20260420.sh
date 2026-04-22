#!/bin/bash
# 2026-04-20 月曜日データの catch-up（scheduler PAUSED 期間の skipped run 救済）
# 日次 scheduler が 2026-04-21 02:00 JST で本来処理するはずだった 04-20 分を手動実行
set -u

REGION=us-west1
BUCKET=stock_data_1930932
LOGFILE=/tmp/catchup_20260420.log
NOTIFY="C:/venvs/investment-agent/Scripts/python.exe scripts/notify.py ntfy --priority high --title"
DATE_FROM=20260420
DATE_TO=20260420
MAX_GEMMA_RETRIES=4

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a $LOGFILE; }
notify_fail() { $NOTIFY "[catchup-0420] $1" "$2"; }

log "=== catchup 2026-04-20 start ==="

# Phase 1: load
log "Phase 1: tdnet-load-daily 起動"
gcloud run jobs execute tdnet-load-daily \
  --region $REGION \
  --update-env-vars "DATE_FROM=$DATE_FROM,DATE_TO=$DATE_TO,JOB_MODE=load" \
  --wait > /tmp/catchup0420_p1.log 2>&1
rc=$?
log "Phase 1 exit $rc"
if [ "$rc" -ne 0 ]; then
  tail -10 /tmp/catchup0420_p1.log | tee -a $LOGFILE
  notify_fail "Phase 1 load failed" "exit=$rc" || true
  exit 1
fi

# Phase 2: ai-prepare
RUN_ID=$(python -c "import uuid; print(uuid.uuid4())")
log "Phase 2: ai-prepare RUN_ID=$RUN_ID"
gcloud run jobs execute tdnet-ai-prepare \
  --region $REGION \
  --update-env-vars "DATE_FROM=$DATE_FROM,DATE_TO=$DATE_TO,RUN_ID=$RUN_ID" \
  --wait > /tmp/catchup0420_p2.log 2>&1
rc=$?
log "Phase 2 exit $rc"
if [ "$rc" -ne 0 ]; then
  tail -10 /tmp/catchup0420_p2.log | tee -a $LOGFILE
  notify_fail "Phase 2 ai-prepare failed" "exit=$rc, RUN_ID=$RUN_ID" || true
  exit 2
fi

# state.json 確認（0 件で終了していれば早期 exit）
if ! gcloud storage ls "gs://$BUCKET/ai_job/$RUN_ID/state.json" > /dev/null 2>&1; then
  log "state.json 未生成 → 04-20 pending doc なし（土日祝日だった可能性）完了扱い"
  log "=== catchup 2026-04-20 DONE (no pending docs) ==="
  $NOTIFY "[catchup-0420] DONE" "04-20 pending なし（TDnet 開示なしの可能性）" --priority default || true
  exit 0
fi

# Phase 3: Gemma with retry
log "Phase 3: Gemma（最大 $MAX_GEMMA_RETRIES 回）"
for try in $(seq 1 $MAX_GEMMA_RETRIES); do
  log "Gemma try $try/$MAX_GEMMA_RETRIES"
  gcloud run jobs execute tdnet-gemma-runner \
    --region $REGION \
    --update-env-vars "RUN_ID=$RUN_ID,BUCKET=$BUCKET,CALLBACK_URL=" \
    --wait > /tmp/catchup0420_gemma_try${try}.log 2>&1
  rc=$?
  log "try $try exit $rc"
  if gcloud storage ls "gs://$BUCKET/ai_job/$RUN_ID/_SUCCESS" > /dev/null 2>&1; then
    log "_SUCCESS 発見 → Phase 3 完了"
    break
  fi
  lines=$(gcloud storage cat "gs://$BUCKET/ai_job/$RUN_ID/gemma_CURRENT.jsonl" 2>/dev/null | wc -l)
  log "try $try 終了時 gemma_CURRENT=$lines 行"
  if [ "$try" -eq "$MAX_GEMMA_RETRIES" ]; then
    notify_fail "Phase 3 Gemma all retries failed" "RUN_ID=$RUN_ID" || true
    exit 3
  fi
  sleep 60
done

# Phase 4: ai-finalize
log "Phase 4: ai-finalize"
gcloud run jobs execute tdnet-ai-finalize \
  --region $REGION \
  --update-env-vars "DATE_FROM=$DATE_FROM,DATE_TO=$DATE_TO,RUN_ID=$RUN_ID" \
  --wait > /tmp/catchup0420_p4.log 2>&1
rc=$?
log "Phase 4 exit $rc"
if [ "$rc" -ne 0 ]; then
  tail -20 /tmp/catchup0420_p4.log | tee -a $LOGFILE
  notify_fail "Phase 4 ai-finalize failed" "exit=$rc, RUN_ID=$RUN_ID" || true
  exit 5
fi

log "=== catchup 2026-04-20 SUCCESS ==="
$NOTIFY "[catchup-0420] DONE" "2026-04-20 catch-up 完了" --priority default || true

#!/bin/bash
# 2023 バックフィル ticker 3691-6366（E/F/G 範囲）
# code-review P0 反映版 image（digest 3b2d8240）を使用
# 異常時は LINE 通知（ntfy high priority）、stall 検知付き
set -u

REGION=us-west1
BUCKET=stock_data_1930932
LOGFILE=/tmp/backfill_2023_efg.log
NOTIFY="C:/venvs/investment-agent/Scripts/python.exe scripts/notify.py ntfy --priority high --title"
DATE_FROM=20230101
DATE_TO=20231231
TICKER_FROM=3691
TICKER_TO=6366
MAX_GEMMA_RETRIES=4

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a $LOGFILE; }
notify_fail() { $NOTIFY "[backfill-efg] $1" "$2"; }

log "=== 2023 backfill E/F/G start ($DATE_FROM-$DATE_TO, ticker $TICKER_FROM-$TICKER_TO) ==="

# ============================================================
# Phase 1: load (tdnet-load-daily で --job-mode=load 動作)
# ============================================================
log "Phase 1: tdnet-load-daily 起動"
PHASE1_START=$(date +%s)
gcloud run jobs execute tdnet-load-daily \
  --region $REGION \
  --update-env-vars "DATE_FROM=$DATE_FROM,DATE_TO=$DATE_TO,TICKER_FROM=$TICKER_FROM,TICKER_TO=$TICKER_TO,JOB_MODE=load" \
  --wait > /tmp/efg_phase1.log 2>&1
rc=$?
PHASE1_END=$(date +%s)
PHASE1_MIN=$(( (PHASE1_END - PHASE1_START) / 60 ))
log "Phase 1 exit $rc（所要 ${PHASE1_MIN}分）"

if [ "$rc" -ne 0 ]; then
  log "Phase 1 失敗"
  tail -10 /tmp/efg_phase1.log | tee -a $LOGFILE
  notify_fail "Phase 1 load failed" "exit=$rc, elapsed=${PHASE1_MIN}min, log=/tmp/efg_phase1.log" || true
  exit 1
fi

# stall 警告（想定 2h、4h 超で警告）
if [ "$PHASE1_MIN" -gt 240 ]; then
  notify_fail "Phase 1 slow" "Phase 1 took ${PHASE1_MIN}min（想定 2h）" || true
fi

# ============================================================
# Phase 2: ai-prepare
# ============================================================
RUN_ID=$(python -c "import uuid; print(uuid.uuid4())")
log "Phase 2: tdnet-ai-prepare RUN_ID=$RUN_ID"
PHASE2_START=$(date +%s)
gcloud run jobs execute tdnet-ai-prepare \
  --region $REGION \
  --update-env-vars "DATE_FROM=$DATE_FROM,DATE_TO=$DATE_TO,TICKER_FROM=$TICKER_FROM,TICKER_TO=$TICKER_TO,RUN_ID=$RUN_ID" \
  --wait > /tmp/efg_phase2.log 2>&1
rc=$?
PHASE2_END=$(date +%s)
PHASE2_MIN=$(( (PHASE2_END - PHASE2_START) / 60 ))
log "Phase 2 exit $rc（所要 ${PHASE2_MIN}分）"

if [ "$rc" -ne 0 ]; then
  log "Phase 2 失敗"
  tail -10 /tmp/efg_phase2.log | tee -a $LOGFILE
  notify_fail "Phase 2 ai-prepare failed" "exit=$rc, RUN_ID=$RUN_ID, elapsed=${PHASE2_MIN}min" || true
  exit 2
fi

if ! gcloud storage ls "gs://$BUCKET/ai_job/$RUN_ID/state.json" > /dev/null 2>&1; then
  log "state.json が生成されていない"
  notify_fail "Phase 2 no state.json" "RUN_ID=$RUN_ID" || true
  exit 3
fi

# ============================================================
# Phase 3: Gemma (preempt retry)
# ============================================================
log "Phase 3: tdnet-gemma-runner（最大 $MAX_GEMMA_RETRIES 回）"
for try in $(seq 1 $MAX_GEMMA_RETRIES); do
  log "Gemma try $try/$MAX_GEMMA_RETRIES"
  GEMMA_TRY_START=$(date +%s)
  gcloud run jobs execute tdnet-gemma-runner \
    --region $REGION \
    --update-env-vars "RUN_ID=$RUN_ID,BUCKET=$BUCKET,CALLBACK_URL=" \
    --wait > /tmp/efg_gemma_try${try}.log 2>&1
  rc=$?
  GEMMA_TRY_END=$(date +%s)
  GEMMA_TRY_MIN=$(( (GEMMA_TRY_END - GEMMA_TRY_START) / 60 ))
  log "Gemma try $try exit $rc（所要 ${GEMMA_TRY_MIN}分）"

  if gcloud storage ls "gs://$BUCKET/ai_job/$RUN_ID/_SUCCESS" > /dev/null 2>&1; then
    log "_SUCCESS 発見 → Phase 3 完了"
    break
  fi

  lines=$(gcloud storage cat "gs://$BUCKET/ai_job/$RUN_ID/gemma_CURRENT.jsonl" 2>/dev/null | wc -l)
  log "try $try 終了時 gemma_CURRENT=$lines 行"

  if [ "$try" -eq "$MAX_GEMMA_RETRIES" ]; then
    log "Gemma MAX retry 到達 → abort"
    notify_fail "Phase 3 Gemma all retries failed" "RUN_ID=$RUN_ID, lines=$lines" || true
    exit 4
  fi
  log "preempt 疑い、60s sleep → retry"
  sleep 60
done

# ============================================================
# Phase 4: ai-finalize
# ============================================================
log "Phase 4: tdnet-ai-finalize"
PHASE4_START=$(date +%s)
gcloud run jobs execute tdnet-ai-finalize \
  --region $REGION \
  --update-env-vars "DATE_FROM=$DATE_FROM,DATE_TO=$DATE_TO,TICKER_FROM=$TICKER_FROM,TICKER_TO=$TICKER_TO,RUN_ID=$RUN_ID" \
  --wait > /tmp/efg_phase4.log 2>&1
rc=$?
PHASE4_END=$(date +%s)
PHASE4_MIN=$(( (PHASE4_END - PHASE4_START) / 60 ))
log "Phase 4 exit $rc（所要 ${PHASE4_MIN}分）"

if [ "$rc" -ne 0 ]; then
  log "Phase 4 失敗"
  tail -20 /tmp/efg_phase4.log | tee -a $LOGFILE
  notify_fail "Phase 4 ai-finalize failed" "exit=$rc, RUN_ID=$RUN_ID, elapsed=${PHASE4_MIN}min" || true
  exit 5
fi

TOTAL_MIN=$(( (PHASE4_END - PHASE1_START) / 60 ))
log "=== backfill E/F/G SUCCESS （累計 ${TOTAL_MIN}分） ==="
$NOTIFY "[backfill-efg] DONE" "2023 backfill E/F/G 完了。所要 ${TOTAL_MIN}min" --priority default || true

#!/bin/bash
# batch#5-#11 復旧 + 2026-01-19〜02-27 gap recovery 一括チェーン
# 2026-04-20 TPU preempt による Gemma 未完了を resume で完遂させる
set -u

# RUN_IDs (stuck 時と同じ、gemma_CURRENT.jsonl / state.json 存在)
RUN_A="7a2ecbed-952d-40fc-a05b-880140482a80"  # batch A (3691-6366, 19,165 doc 目標、1,906 完了済)
RUN_B="33bcc50e-6b31-440f-a5af-17c04e914a1e"  # batch B (6367-9997, 26,933 doc 目標、3,805 完了済)

BUCKET="stock_data_1930932"
REGION="us-west1"
PYTHON="C:/venvs/investment-agent/Scripts/python.exe"
MAX_GEMMA_RETRY=5      # preempt 想定、複数回 retry で段階的完遂
STALL_THRESHOLD_H=4    # ジョブ1回あたり 4時間超えたら stall 判定

notify() { PYTHONUTF8=1 "$PYTHON" scripts/notify.py ntfy --title "$1" --priority "$2" "$3" >/dev/null 2>&1 || true; }
log() { echo "[$(date -u +%FT%TZ)] $*"; }

# ── helpers ────────────────────────────
check_success() {
  # $1 = RUN_ID
  gcloud storage ls "gs://${BUCKET}/ai_job/$1/_SUCCESS" 2>/dev/null | grep -q _SUCCESS
}

gemma_current_count() {
  # $1 = RUN_ID
  gcloud storage cat "gs://${BUCKET}/ai_job/$1/gemma_CURRENT.jsonl" 2>/dev/null | wc -l
}

run_gemma_with_retry() {
  local run_id="$1"
  local label="$2"
  for try in $(seq 1 ${MAX_GEMMA_RETRY}); do
    if check_success "$run_id"; then
      log "[$label] _SUCCESS already present, skipping gemma-runner"
      return 0
    fi
    local before=$(gemma_current_count "$run_id")
    log "[$label] try=$try, current done=$before, launching tdnet-gemma-runner"
    notify "$label Gemma try=$try" "default" "done=$before, 起動"
    local exec_start=$(date +%s)
    gcloud run jobs execute tdnet-gemma-runner --region "$REGION" \
      --update-env-vars "RUN_ID=$run_id,BUCKET=$BUCKET,CALLBACK_URL=" \
      --wait >/tmp/gemma_runner_${run_id}.log 2>&1 || true
    tail -5 /tmp/gemma_runner_${run_id}.log || true
    local exec_end=$(date +%s)
    local elapsed_h=$(( (exec_end - exec_start) / 3600 ))
    local after=$(gemma_current_count "$run_id")
    log "[$label] try=$try done, docs $before → $after (elapsed ${elapsed_h}h)"
    if check_success "$run_id"; then
      notify "$label Gemma 完遂" "default" "$after doc / try $try"
      return 0
    fi
    if [ "$after" -le "$before" ]; then
      log "[$label] WARNING: no progress (stall suspected)"
      notify "$label 進捗停止" "high" "try $try で doc 増えず ($before → $after)"
    fi
  done
  notify "$label Gemma 限界" "high" "retry ${MAX_GEMMA_RETRY} 回で完遂せず"
  return 1
}

run_ai_finalize() {
  local run_id="$1"
  local label="$2"
  log "[$label] launching tdnet-ai-finalize"
  notify "$label ai-finalize 起動" "default" "run_id=$run_id"
  # pipe to tail は exit code を握りつぶすので、redirect + 別行 tail で対処
  gcloud run jobs execute tdnet-ai-finalize --region "$REGION" \
    --update-env-vars "RUN_ID=$run_id" --wait >/tmp/ai_finalize_${run_id}.log 2>&1
  local rc=$?
  tail -5 /tmp/ai_finalize_${run_id}.log || true
  if [ "$rc" -eq 0 ]; then
    notify "$label ai-finalize ✓" "default" "run_id=$run_id"
    return 0
  else
    notify "$label ai-finalize 失敗" "high" "run_id=$run_id rc=$rc"
    return 1
  fi
}

# ── main flow ──────────────────────────
log "=== recovery chain start ==="
notify "TDnet 復旧チェーン起動" "default" "batch#5-11 Gemma resume → ai-finalize → gap recovery"

# Batch A
log "--- Batch A (3691-6366) ---"
if ! run_gemma_with_retry "$RUN_A" "batchA"; then
  log "Batch A Gemma 失敗 → チェーン中断"
  exit 1
fi
if ! run_ai_finalize "$RUN_A" "batchA"; then
  log "Batch A ai-finalize 失敗 → チェーン中断"
  exit 1
fi

# Batch B
log "--- Batch B (6367-9997) ---"
if ! run_gemma_with_retry "$RUN_B" "batchB"; then
  log "Batch B Gemma 失敗 → チェーン中断"
  exit 1
fi
if ! run_ai_finalize "$RUN_B" "batchB"; then
  log "Batch B ai-finalize 失敗 → チェーン中断"
  exit 1
fi

# Gap recovery (2026-01-19〜02-27)
log "--- Gap recovery (2026-01-19〜02-27) ---"
notify "gap recovery 起動" "default" "2026-01-19〜02-27 漏れ~2,900件"
PYTHONUTF8=1 "$PYTHON" scripts/monitor_backfill.py config/backfill/2026_gap_recovery_jan_feb.yaml

log "=== recovery chain done ==="
notify "TDnet 復旧チェーン完遂" "default" "全工程完了"

#!/bin/bash
# TDnet バッチ#1 load→workflows 自動オーケストレーション + LINE通知
# 用途: 2023 バックフィル #1（ticker 1301-1909）の通し検証
# 参照: docs/plans/20260417_091112_tdnet_load_ai_split.md

set -u
LOAD_EXEC="tdnet-load-daily-w96bz"
REGION="us-west1"
WF_LOCATION="us-central1"
WF_NAME="ai_processing_flow"
PYTHON="C:/venvs/investment-agent/Scripts/python.exe"

notify() {
  local priority="$1"
  local title="$2"
  local message="$3"
  PYTHONUTF8=1 "$PYTHON" scripts/notify.py ntfy \
    --title "$title" --priority "$priority" "$message" >/dev/null 2>&1 || true
}

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $*"
}

# ── Phase 1: load 完了待機 ──
log "load 監視開始: $LOAD_EXEC"
while :; do
  COMP=$(gcloud run jobs executions describe "$LOAD_EXEC" --region "$REGION" \
    --format="value(status.completionTime)" 2>/dev/null | tr -d '\r')
  if [ -n "$COMP" ]; then
    SUCC=$(gcloud run jobs executions describe "$LOAD_EXEC" --region "$REGION" \
      --format="value(status.succeededCount)" 2>/dev/null | tr -d '\r')
    FAIL=$(gcloud run jobs executions describe "$LOAD_EXEC" --region "$REGION" \
      --format="value(status.failedCount)" 2>/dev/null | tr -d '\r')
    log "load 完了: succeeded=$SUCC failed=$FAIL"
    if [ "${SUCC:-0}" = "1" ]; then
      notify "default" "TDnet batch#1 load成功" "load完了。workflows起動します。exec=$LOAD_EXEC"
      break
    else
      notify "high" "TDnet batch#1 load失敗" "exec=$LOAD_EXEC failed=$FAIL"
      log "load 失敗、監視終了"
      exit 1
    fi
  fi
  sleep 120
done

# ── Phase 2: workflows 起動 ──
log "workflows 起動"
WF_EXEC=$(gcloud workflows execute "$WF_NAME" --location="$WF_LOCATION" \
  --data='{"date_from":"20230101","date_to":"20231231","ticker_from":"1301","ticker_to":"1909"}' \
  --format="value(name)" 2>&1 | tr -d '\r')
WF_EXEC_ID=$(basename "$WF_EXEC")
log "workflows exec: $WF_EXEC_ID"
notify "default" "TDnet batch#1 workflows起動" "exec=$WF_EXEC_ID"

# ── Phase 3: workflows 完了待機 ──
while :; do
  STATE=$(gcloud workflows executions describe "$WF_EXEC_ID" \
    --workflow="$WF_NAME" --location="$WF_LOCATION" \
    --format="value(state)" 2>/dev/null | tr -d '\r')
  case "$STATE" in
    SUCCEEDED)
      log "workflows 成功"
      notify "default" "TDnet batch#1 完遂" "workflows SUCCEEDED。exec=$WF_EXEC_ID"
      exit 0
      ;;
    FAILED|CANCELLED)
      log "workflows $STATE"
      ERR=$(gcloud workflows executions describe "$WF_EXEC_ID" \
        --workflow="$WF_NAME" --location="$WF_LOCATION" \
        --format="value(error)" 2>/dev/null | tr -d '\r' | head -c 300)
      notify "high" "TDnet batch#1 workflows $STATE" "exec=$WF_EXEC_ID err=$ERR"
      exit 1
      ;;
    ACTIVE|"")
      sleep 300
      ;;
    *)
      log "不明な state: $STATE、継続監視"
      sleep 300
      ;;
  esac
done

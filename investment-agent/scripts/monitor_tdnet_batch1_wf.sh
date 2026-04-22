#!/bin/bash
# TDnet バッチ#1 workflows 完了監視 + LINE通知（load 完了済み想定、workflows のみ監視）
set -u
WF_LOCATION="us-central1"
WF_NAME="ai_processing_flow"
WF_EXEC_ID="${1:?usage: $0 <wf_execution_id>}"
PYTHON="C:/venvs/investment-agent/Scripts/python.exe"

notify() {
  local priority="$1"; local title="$2"; local message="$3"
  PYTHONUTF8=1 "$PYTHON" scripts/notify.py ntfy \
    --title "$title" --priority "$priority" "$message" >/dev/null 2>&1 || true
}
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $*"; }

log "workflows 監視開始: $WF_EXEC_ID"
notify "default" "TDnet batch#1 WF監視開始" "exec=$WF_EXEC_ID"

while :; do
  STATE=$(gcloud workflows executions describe "$WF_EXEC_ID" \
    --workflow="$WF_NAME" --location="$WF_LOCATION" \
    --format="value(state)" 2>/dev/null | tr -d '\r')
  log "state=$STATE"
  case "$STATE" in
    SUCCEEDED)
      notify "default" "TDnet batch#1 完遂" "workflows SUCCEEDED。exec=$WF_EXEC_ID"
      exit 0 ;;
    FAILED|CANCELLED)
      ERR=$(gcloud workflows executions describe "$WF_EXEC_ID" \
        --workflow="$WF_NAME" --location="$WF_LOCATION" \
        --format="value(error)" 2>/dev/null | tr -d '\r' | head -c 300)
      notify "high" "TDnet batch#1 workflows $STATE" "exec=$WF_EXEC_ID err=$ERR"
      exit 1 ;;
    ACTIVE|"")
      sleep 180 ;;
    *)
      log "不明 state: $STATE、継続監視"
      sleep 300 ;;
  esac
done

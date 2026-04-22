#!/bin/bash
# batch#5-#11 AI 完遂 → 2026-01-19〜02-27 漏れリカバリ 自動連鎖
# 両 WF 成功時のみ recovery 起動、失敗時は LINE 通知で停止
set -u

WF_A="7a2ecbed-952d-40fc-a05b-880140482a80"
WF_B="33bcc50e-6b31-440f-a5af-17c04e914a1e"
WF_LOC="us-central1"
WF_NAME="ai_processing_flow"
PYTHON="C:/venvs/investment-agent/Scripts/python.exe"

notify() {
  PYTHONUTF8=1 "$PYTHON" scripts/notify.py ntfy \
    --title "$1" --priority "$2" "$3" >/dev/null 2>&1 || true
}
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $*"; }

get_state() {
  gcloud workflows executions describe "$1" \
    --workflow="$WF_NAME" --location="$WF_LOC" \
    --format="value(state)" 2>/dev/null | tr -d '\r'
}

log "chain 開始: WF_A=$WF_A WF_B=$WF_B"

# 両 WF 完了待ち
while :; do
  SA=$(get_state "$WF_A")
  SB=$(get_state "$WF_B")
  log "A=$SA B=$SB"
  if [ "$SA" != "ACTIVE" ] && [ "$SB" != "ACTIVE" ] && [ -n "$SA" ] && [ -n "$SB" ]; then
    break
  fi
  sleep 300
done

log "両 WF 終了: A=$SA B=$SB"

# 成功判定
if [ "$SA" = "SUCCEEDED" ] && [ "$SB" = "SUCCEEDED" ]; then
  notify "TDnet chain 自動起動" "default" "batch#5-11 完遂確認、2026-01/02 gap recovery 起動"
  log "recovery monitor 起動"
  exec bash -c "PYTHONUTF8=1 '$PYTHON' scripts/monitor_backfill.py config/backfill/2026_gap_recovery_jan_feb.yaml"
else
  notify "TDnet chain 停止" "high" "batch#5-11 失敗 (A=$SA B=$SB) → recovery スキップ"
  log "recovery スキップ"
  exit 1
fi

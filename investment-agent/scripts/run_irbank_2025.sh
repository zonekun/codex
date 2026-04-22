#!/bin/bash
# 2025年分 irbank-tdnet-download 月次順次実行スクリプト
# 各月が完了してから次月を開始（ban 対策のため順次実行）
# Usage: bash scripts/run_irbank_2025.sh >> data/logs/irbank_2025.log 2>&1

set -e

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

run_month() {
  local from="$1"
  local to="$2"
  log "=== 開始: $from ～ $to ==="
  gcloud run jobs execute irbank-tdnet-download \
    --region us-west1 \
    --args="--from,$from,--to,$to" \
    --wait \
    2>&1
  log "=== 完了: $from ～ $to ==="
  # 月間に少し間隔を置く（ban 対策）
  sleep 30
}

log "=== 2025年分 irbank-tdnet-download 順次実行開始 ==="
log "実行期間: 2025-01-11 ～ 2025-12-31"
log "月次に分割して順次実行（各月完了後に次月開始）"

# 1月後半
run_month 20250111 20250131

# 2月
run_month 20250201 20250228

# 3月
run_month 20250301 20250331

# 4月
run_month 20250401 20250430

# 5月
run_month 20250501 20250531

# 6月
run_month 20250601 20250630

# 7月
run_month 20250701 20250731

# 8月
run_month 20250801 20250831

# 9月
run_month 20250901 20250930

# 10月
run_month 20251001 20251031

# 11月
run_month 20251101 20251128

# 12月
run_month 20251201 20251231

log "=== 2025年分 全月 完了 ==="

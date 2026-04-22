#!/usr/bin/env bash
# 決算資料比較分析ジョブ（gemma3:12b / GPU版）トリガースクリプト
#
# 使い方:
#   bash scripts/run_earnings_compare_gemma3.sh 7203 2025-11-14
#
# 引数:
#   $1 : TICKER      - 銘柄コード（4桁）例: 7203
#   $2 : LATEST_DATE - 最新決算開示日 (YYYY-MM-DD) 例: 2025-11-14

set -euo pipefail

TICKER="${1:-}"
LATEST_DATE="${2:-}"
JOB_NAME="earnings-compare-gemma3"
REGION="us-east4"

# ──────────────────────────────────────────
# 入力チェック
# ──────────────────────────────────────────
if [[ -z "$TICKER" || -z "$LATEST_DATE" ]]; then
  echo "使い方: $0 <TICKER> <LATEST_DATE>"
  echo "例:     $0 7203 2025-11-14"
  exit 1
fi

echo "================================================"
echo "  決算資料比較分析ジョブ起動（gemma3:12b / GPU）"
echo "  TICKER      : ${TICKER}"
echo "  LATEST_DATE : ${LATEST_DATE}"
echo "  Job         : ${JOB_NAME} (${REGION})"
echo "================================================"

# ──────────────────────────────────────────
# 環境変数をジョブに設定（execute には --set-env-vars 不可のため update を先に実行）
# MODEL_NAME は jobs create 時に固定済みのため更新不要
# ──────────────────────────────────────────
echo "[1/3] 環境変数を更新中..."
gcloud run jobs update "${JOB_NAME}" \
  --region "${REGION}" \
  --update-env-vars "TICKER=${TICKER},LATEST_DATE=${LATEST_DATE}"

# ──────────────────────────────────────────
# ジョブ実行（--wait で完了まで待機）
# GPU 版は CPU 版より大幅に速い（推定 3〜5 分）
# ──────────────────────────────────────────
echo "[2/3] ジョブを実行中（完了まで待機）..."
EXECUTION_OUTPUT=$(gcloud run jobs execute "${JOB_NAME}" \
  --region "${REGION}" \
  --wait \
  --format="value(metadata.name)" 2>&1)

EXECUTION_ID=$(echo "$EXECUTION_OUTPUT" | tail -1)
echo "  実行ID: ${EXECUTION_ID}"

# ──────────────────────────────────────────
# ログ取得・表示
# ──────────────────────────────────────────
echo "[3/3] ログを取得中..."
echo ""

gcloud logging read \
  "resource.type=cloud_run_job \
   AND resource.labels.job_name=${JOB_NAME} \
   AND resource.labels.location=${REGION} \
   AND labels.\"run.googleapis.com/execution-name\"=${EXECUTION_ID}" \
  --project gmailpj-357912 \
  --limit 200 \
  --format "value(textPayload)" \
  --order asc

echo ""
echo "================================================"
echo "  完了"
echo "================================================"

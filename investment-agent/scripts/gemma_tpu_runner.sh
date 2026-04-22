#!/bin/bash
# Gemma TPU Runner — Cloud Run Job 内で動く本番版。
# PoC `scripts/poc_gemma4_phaseD_orchestrator.sh` のエッセンスを抽出し、
# Workflows Step 2 として呼ばれる想定。
#
# 入力（env）:
#   RUN_ID       Workflows execution ID（必須、TPU VM 名に使用）
#   BUCKET       GCS バケット（デフォルト stock_data_1930932）
#   CALLBACK_URL Workflows Callback URL（worker.py が完了時に叩く）
#   PROJECT      GCP project（デフォルト gmailpj-357912）
#   ZONE         TPU zone（デフォルト us-central1-b）
#
# 処理フロー:
#   1. hf-token を Secret Manager から取得
#   2. TPU v6e-4 spot VM 作成（リトライ3回、state=READY 待機）
#   3. SSH 事前 warm-up + scp で gemma_tpu_worker.py を /tmp に転送
#   4. ssh で vLLM setup（sudo docker run + transformers upgrade + vllm serve）
#   5. ssh で vLLM healthcheck（最大20分）
#   6. ssh で gemma_tpu_worker.py 実行（worker が Callback POST）
#   7. EXIT trap で TPU 削除
#
# 参照:
#   docs/knowledges/tools/013-1_ai_cost_and_gemma_poc.md
#   scripts/poc_gemma4_phaseD_orchestrator.sh

set -u
set -o pipefail

: "${RUN_ID:?RUN_ID env required}"
PROJECT="${PROJECT:-gmailpj-357912}"
BUCKET="${BUCKET:-stock_data_1930932}"
CALLBACK_URL="${CALLBACK_URL:-}"
ZONE="${ZONE:-us-central1-b}"

# PoC で動作確認済み digest（013-1 / 078 セクション 4）
VLLM_IMAGE="vllm/vllm-tpu@sha256:360eae322ff76fe0f414d9b7c1ee769d65a07447da478e69784145dbedc5743e"

# TPU VM 名: 小文字+数字+ハイフン、63文字以内
TPU_NAME="tdnet-ai-gemma-${RUN_ID}"
# ハイフンに置換 + 小文字化 + 63文字切り詰め
TPU_NAME=$(echo "${TPU_NAME}" | tr '_' '-' | tr '[:upper:]' '[:lower:]' | cut -c1-63)

log() { echo "[$(date -u +%FT%TZ)] $*"; }

emit_callback() {
  local status="$1"
  local reason="${2:-}"
  if [ -n "${CALLBACK_URL}" ]; then
    # Workflows Callback URL は workflowexecutions.googleapis.com で OAuth2 Bearer 必須
    local token
    token=$(gcloud auth print-access-token 2>/dev/null || echo "")
    if [ -n "${token}" ]; then
      curl -s -X POST "${CALLBACK_URL}" \
        -H "Authorization: Bearer ${token}" \
        -H "Content-Type: application/json" \
        -d "{\"run_id\":\"${RUN_ID}\",\"status\":\"${status}\",\"reason\":\"${reason}\"}" \
        -m 30 || true
      log "[callback] posted status=${status} reason=${reason}"
    else
      log "[callback] FAIL — no access token"
    fi
  fi
}

cleanup_tpu() {
  log "[cleanup] deleting TPU ${TPU_NAME}"
  gcloud compute tpus tpu-vm delete "${TPU_NAME}" \
    --zone="${ZONE}" --project="${PROJECT}" --quiet --async 2>&1 | tail -3 || true
}
trap 'cleanup_tpu' EXIT

log "=== gemma_tpu_runner start ==="
log "RUN_ID=${RUN_ID} TPU_NAME=${TPU_NAME} ZONE=${ZONE}"
log "VLLM_IMAGE=${VLLM_IMAGE}"

# ───────────────────────────────────────
# Step 1: HF_TOKEN 取得
# ───────────────────────────────────────
log "[phase=hf_token] fetching from Secret Manager"
HF_TOKEN=$(gcloud secrets versions access latest --secret=hf-token --project="${PROJECT}" 2>&1) || HF_TOKEN=""
if [ -z "${HF_TOKEN}" ] || echo "${HF_TOKEN}" | head -c 100 | grep -qi "error\|permission\|denied"; then
  log "[phase=hf_token] FATAL — cannot fetch"
  emit_callback "failed" "hf-token-access-denied"
  exit 1
fi
log "[phase=hf_token] OK (length=${#HF_TOKEN})"

# ───────────────────────────────────────
# Step 2: TPU v6e-4 spot VM 作成
# ───────────────────────────────────────
log "[phase=tpu_create] creating spot TPU v6e-4 ${TPU_NAME} in ${ZONE}"
CREATED=0
for try in 1 2 3; do
  if gcloud compute tpus tpu-vm create "${TPU_NAME}" \
      --zone="${ZONE}" \
      --accelerator-type=v6e-4 \
      --version=v2-alpha-tpuv6e \
      --spot \
      --service-account="bq-loader@${PROJECT}.iam.gserviceaccount.com" \
      --scopes=https://www.googleapis.com/auth/cloud-platform \
      --project="${PROJECT}" 2>&1 | tail -10; then
    log "[phase=tpu_create] try=${try} OK"
    CREATED=1
    break
  fi
  log "[phase=tpu_create] try=${try} failed, sleeping 60s"
  sleep 60
done
if [ "${CREATED}" -ne 1 ]; then
  emit_callback "failed" "tpu-create-exhausted"
  exit 1
fi

# ───────────────────────────────────────
# Step 3: state=READY 待機
# ───────────────────────────────────────
log "[phase=tpu_wait_ready] waiting for READY..."
READY=0
for i in $(seq 1 120); do
  STATE=$(gcloud compute tpus tpu-vm describe "${TPU_NAME}" \
    --zone="${ZONE}" --project="${PROJECT}" --format='value(state)' 2>/dev/null || echo "GONE")
  if [ "${STATE}" = "READY" ]; then
    log "[phase=tpu_wait_ready] READY after $((i * 10))s"
    READY=1
    break
  fi
  if [ "${STATE}" = "GONE" ] || [ "${STATE}" = "PREEMPTED" ] || [ -z "${STATE}" ]; then
    log "[phase=tpu_wait_ready] FATAL — state=${STATE}"
    emit_callback "failed" "tpu-vanished-during-bringup"
    exit 1
  fi
  sleep 10
done
if [ "${READY}" -ne 1 ]; then
  emit_callback "failed" "tpu-not-ready-in-20min"
  exit 1
fi

# ───────────────────────────────────────
# Step 4: SSH 事前 warm-up（ホストキーキャッシュ）
# ───────────────────────────────────────
log "[phase=ssh_warmup]"
# Cloud Run 内では plink 問題はないが、PoC と同じく念のため
echo "y" | gcloud alpha compute tpus tpu-vm ssh "${TPU_NAME}" \
  --zone="${ZONE}" --project="${PROJECT}" \
  --tunnel-through-iap \
  --command="echo warmup-ok; hostname" 2>&1 | tail -10

# ───────────────────────────────────────
# Step 5: worker.py を TPU に scp
# ───────────────────────────────────────
log "[phase=scp_worker]"
gcloud alpha compute tpus tpu-vm scp \
  /app/gemma_tpu_worker.py \
  "${TPU_NAME}:/tmp/gemma_tpu_worker.py" \
  --zone="${ZONE}" --project="${PROJECT}" \
  --tunnel-through-iap 2>&1 | tail -5

# ───────────────────────────────────────
# Step 6: vLLM setup（docker run + transformers upgrade + vllm serve）
# ───────────────────────────────────────
log "[phase=vllm_setup]"
SETUP_CMD=$(cat <<REMOTE
set -e
echo "[remote] host=\$(hostname) date=\$(date)"

# docker run（既存なら skip）
if ! sudo docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^vllm\$'; then
  echo "[remote] starting vllm container"
  sudo docker run -d --rm --name vllm \\
    --privileged --network host \\
    -v /tmp:/tmp -v \$HOME:/root \\
    -e HF_TOKEN='${HF_TOKEN}' \\
    -e VLLM_TPU_BACKEND=pallas \\
    --entrypoint /bin/bash \\
    ${VLLM_IMAGE} \\
    -c "sleep infinity"
else
  echo "[remote] vllm container already running"
fi
sleep 5

# transformers upgrade（4.57.6 同梱 → 4.58+ 必須 for Gemma 4）
echo "[remote] upgrading transformers"
sudo docker exec vllm pip install -U "transformers>=4.58" --quiet 2>&1 | tail -3 || true
sudo docker exec vllm bash -lc 'python -c "import vllm; print(\"vllm=\" + vllm.__version__)"' 2>&1 | head -3 || true
sudo docker exec vllm bash -lc 'python -c "import transformers; print(\"transformers=\" + transformers.__version__)"' 2>&1 | head -3 || true

# vllm serve（起動中なら skip）
if ! sudo docker exec vllm bash -c "ss -ltn 2>/dev/null | grep -q :8000 || netstat -ltn 2>/dev/null | grep -q :8000"; then
  echo "[remote] launching vllm serve"
  sudo docker exec -d vllm bash -lc "cd /tmp && nohup python -m vllm.entrypoints.openai.api_server --model google/gemma-4-31B-it --max-model-len 16384 --max-num-batched-tokens 4096 --gpu-memory-utilization 0.90 --port 8000 --tensor-parallel-size 4 > /tmp/vllm_server.log 2>&1 &"
else
  echo "[remote] vllm already listening"
fi

# healthcheck（最大20分、120 polls × 10s）
echo "[remote] waiting for /v1/models..."
for i in \$(seq 1 120); do
  if curl -s -m 5 http://localhost:8000/v1/models 2>/dev/null | grep -q gemma-4-31B-it; then
    echo "[remote] vLLM READY after \$i polls"
    exit 0
  fi
  if [ \$((i % 6)) -eq 0 ]; then
    echo "[remote] poll=\$i still waiting; last vllm_server.log tail:"
    sudo docker exec vllm bash -c "tail -8 /tmp/vllm_server.log 2>/dev/null" || true
  fi
  sleep 10
done
echo "[remote] ERROR: vLLM did not ready in 20min"
sudo docker logs vllm 2>&1 | tail -50 || true
sudo docker exec vllm bash -c "tail -120 /tmp/vllm_server.log 2>/dev/null" || true
exit 1
REMOTE
)

if ! echo "y" | gcloud alpha compute tpus tpu-vm ssh "${TPU_NAME}" \
    --zone="${ZONE}" --project="${PROJECT}" \
    --tunnel-through-iap \
    --command="${SETUP_CMD}" 2>&1 | tail -60; then
  log "[phase=vllm_setup] FAIL"
  emit_callback "failed" "vllm-setup-failed"
  exit 1
fi
log "[phase=vllm_setup] vLLM READY"

# ───────────────────────────────────────
# Step 7: gemma_tpu_worker.py 実行（worker が callback 投げる）
# ───────────────────────────────────────
log "[phase=worker_run] starting inference"
WORKER_CMD=$(cat <<REMOTE
set -e
pip install --quiet --user httpx 2>/dev/null || pip3 install --quiet --user httpx 2>/dev/null || true
cd /tmp
export RUN_ID='${RUN_ID}'
export BUCKET_NAME='${BUCKET}'
export CALLBACK_URL='${CALLBACK_URL}'
export CONCURRENCY=8
python3 -u /tmp/gemma_tpu_worker.py 2>&1 | tee /tmp/worker.log
REMOTE
)

echo "y" | gcloud alpha compute tpus tpu-vm ssh "${TPU_NAME}" \
  --zone="${ZONE}" --project="${PROJECT}" \
  --tunnel-through-iap \
  --command="${WORKER_CMD}" 2>&1 | tail -100

# _SUCCESS marker 存在確認: worker 完了検証（SSH 切断や preempt で worker が未完了でも
# この runner が exit 0 してしまう bug を防ぐ。2026-04-20 追加）
log "[phase=verify_success] checking GCS _SUCCESS marker"
if gcloud storage ls "gs://${BUCKET}/ai_job/${RUN_ID}/_SUCCESS" 2>/dev/null | grep -q _SUCCESS; then
  log "=== gemma_tpu_runner done (verified _SUCCESS) ==="
  exit 0
fi

log "[phase=verify_success] FAIL — _SUCCESS marker absent (TPU preempt or worker abort の疑い)"
emit_callback "failed" "worker-no-success-marker"
# Workflows の retry を発動させるため exit 1
exit 1

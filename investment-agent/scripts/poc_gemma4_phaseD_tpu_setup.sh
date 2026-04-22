#!/usr/bin/env bash
# Gemma 4 31B TPU vLLM セットアップ (TPU VM 上で実行).
# 起動前に gemma4_phaseD_input.jsonl を /tmp/ に転送済み前提.
set -e

HF_TOKEN="${HF_TOKEN:?HF_TOKEN must be set}"
echo "[setup] HF_TOKEN length=${#HF_TOKEN}"

# Docker コンテナ起動
if ! docker ps --format '{{.Names}}' | grep -q '^vllm$'; then
  echo "[setup] starting vllm container..."
  docker run -d --rm --name vllm \
    --privileged \
    --network host \
    -v /tmp:/tmp \
    -v "$HOME:/root" \
    -e HF_TOKEN="$HF_TOKEN" \
    -e VLLM_TPU_BACKEND=pallas \
    --entrypoint /bin/bash \
    vllm/vllm-tpu@sha256:360eae322ff76fe0f414d9b7c1ee769d65a07447da478e69784145dbedc5743e \
    -c "sleep infinity"
else
  echo "[setup] vllm container already running"
fi

# transformers upgrade (Gemma 4 必須: >=4.58)
echo "[setup] upgrading transformers in container..."
docker exec vllm pip install -U "transformers>=4.58" --quiet

# vLLM 起動 (まだ立ってなければ)
if docker exec vllm bash -c "ss -ltn | grep -q ':8000'"; then
  echo "[setup] vLLM already listening on :8000"
else
  echo "[setup] launching vllm serve..."
  docker exec -d vllm bash -c '
    cd /tmp && \
    nohup python -m vllm.entrypoints.openai.api_server \
      --model google/gemma-4-31B-it \
      --max-model-len 16384 \
      --max-num-batched-tokens 4096 \
      --gpu-memory-utilization 0.90 \
      --port 8000 \
      > /tmp/vllm_server.log 2>&1 &
  '
fi

echo "[setup] waiting for /v1/models (up to 10 min)..."
for i in $(seq 1 60); do
  if curl -s -m 5 http://localhost:8000/v1/models | grep -q 'gemma-4-31B-it'; then
    echo "[setup] vLLM ready after ${i} polls"
    exit 0
  fi
  sleep 10
done
echo "[setup] ERROR: vLLM did not become ready in 10min"
tail -80 /tmp/vllm_server.log || true
exit 1

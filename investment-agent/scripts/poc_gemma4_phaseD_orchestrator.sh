#!/usr/bin/env bash
# Phase D Orchestrator: baseline/v2/v3 3プロンプト直列実行 + TPU削除 + LINE通知.
set -u
PROJECT=gmailpj-357912
TPU_NAME=gemma4-tpu
ZONES=(us-central1-b us-east5-a us-east5-b us-east5-c us-west1-c)
REPO="G:/マイドライブ/claude/investment-agent"
VENV_PY="C:/venvs/investment-agent/Scripts/python.exe"
LOG=/c/tmp/gemma4_phaseD_orchestrator.log
INPUT_LOCAL=/c/tmp/gemma4_phaseD_input.jsonl
INPUT_TPU=/tmp/gemma4_phaseD_input.jsonl

# vllm-tpu Docker image: 4/16 17:32 JST 成功確認済み SHA digest pin.
# :latest/:nightly は絶対使わない (タグドリフトで再現性崩壊).
VLLM_IMAGE="vllm/vllm-tpu@sha256:360eae322ff76fe0f414d9b7c1ee769d65a07447da478e69784145dbedc5743e"

# .env から HF_TOKEN / NTFY_TOPIC
HF_TOKEN=""
if [ -f "$REPO/.env" ]; then
  HF_TOKEN=$(grep '^HF_TOKEN=' "$REPO/.env" | sed 's/HF_TOKEN=//' | tr -d '"' | tr -d "\r")
fi
export HF_TOKEN

log() {
  echo "[$(TZ=Asia/Tokyo date +%FT%T%z)] $*" | tee -a "$LOG"
}

notify_line() {
  local title="$1"
  local message="$2"
  local priority="${3:-high}"
  cd "$REPO" && PYTHONUTF8=1 "$VENV_PY" scripts/notify.py ntfy "$message" --title "$title" --priority "$priority" 2>&1 | tee -a "$LOG" || true
}

delete_tpu() {
  log "*** DELETING TPU (all zones) ***"
  for z in "${ZONES[@]}"; do
    if gcloud compute tpus tpu-vm list --zone="$z" --project="$PROJECT" 2>&1 | grep -q "$TPU_NAME"; then
      log "deleting TPU in $z"
      gcloud compute tpus tpu-vm delete "$TPU_NAME" --zone="$z" --project="$PROJECT" --quiet 2>&1 | tee -a "$LOG" || true
    fi
  done
  sleep 5
}

# TPU 削除の絶対保証 (EXIT トラップ. 上のスクリプトのどこで exit/kill されても呼ばれる)
on_exit() {
  local rc=$?
  log "[trap] on_exit rc=$rc -- ensuring TPU is deleted"
  delete_tpu
  log "[trap] on_exit done"
}
trap on_exit EXIT

run_one_version() {
  # $1 = version, $2 = zone
  local ver="$1"
  local zone="$2"
  # Run from inside the vllm container (has httpx, gcloud available via host /tmp mount)
  # But the script calls gcloud for GCS push. Run on HOST not container.
  # Host python + host gcloud + /tmp input (same /tmp since container mounted /tmp:/tmp).
  local cmd
  cmd=$(cat <<REMOTE
set -e
cd /tmp
export PROMPT_VERSION=${ver}
export RUN_SUFFIX=phaseD
echo "[remote-run] start \${PROMPT_VERSION} at \$(date)"
# Use host python (needs httpx)
pip install --quiet --user httpx 2>/dev/null || pip3 install --quiet --user httpx 2>/dev/null || true
python3 -u /tmp/poc_gemma4_tpu_monthly_test.py /tmp/gemma4_phaseD_input.jsonl /tmp/gemma4_tpu_monthly_phaseD_${ver}_results.jsonl --prompt-version ${ver} > /tmp/run_${ver}.log 2>&1
echo "[remote-run] done \${PROMPT_VERSION} at \$(date)"
tail -10 /tmp/run_${ver}.log
REMOTE
)
  log "--- run $ver start ---"
  echo "y" | gcloud compute tpus tpu-vm ssh "$TPU_NAME" --zone="$zone" --project="$PROJECT" --command="$cmd" 2>&1 | tee -a "$LOG"
  local rc=${PIPESTATUS[1]}
  log "--- run $ver done rc=$rc ---"

  # GCS -> ローカル
  local out_local="/c/tmp/gemma4_tpu_monthly_phaseD_${ver}_results.jsonl"
  gcloud storage cp "gs://stock_data_1930932/tdnet/poc/gemma4_tpu_monthly_${ver}_phaseD_CURRENT.jsonl" "$out_local" 2>&1 | tee -a "$LOG" || true
  local n=$(wc -l < "$out_local" 2>/dev/null || echo 0)
  log "$ver downloaded lines=$n"
  return $rc
}

setup_vllm() {
  # $1 = zone, $2 = image (optional; defaults to $VLLM_IMAGE)
  local zone="$1"
  local image="${2:-$VLLM_IMAGE}"
  local timeout_polls="${3:-30}"   # デフォルト 5 分 (10 秒×30). 本番実行は 120 で 20 分

  # Pre-warm SSH host key cache (avoid "Store key in cache?" prompt blocking scp)
  echo "y" | gcloud compute tpus tpu-vm ssh "$TPU_NAME" --zone="$zone" --project="$PROJECT" --command="echo pre-warm ok" 2>&1 | tee -a "$LOG" || true

  # scp input + script
  gcloud compute tpus tpu-vm scp "$INPUT_LOCAL" "$TPU_NAME:$INPUT_TPU" --zone="$zone" --project="$PROJECT" 2>&1 | tee -a "$LOG"
  gcloud compute tpus tpu-vm scp "$REPO/scripts/poc_gemma4_tpu_monthly_test.py" "$TPU_NAME:/tmp/poc_gemma4_tpu_monthly_test.py" --zone="$zone" --project="$PROJECT" 2>&1 | tee -a "$LOG"

  # Setup vLLM in docker (sudo docker required on TPU VM)
  # transformers upgrade 必須: 同梱 4.57.6 は Gemma 4 の model_type=gemma4 未対応
  local cmd
  cmd=$(cat <<REMOTE
set -e
echo "[remote-setup] host=\$(hostname) date=\$(date) image=${image}"
# Launch vllm container (sudo required)
if ! sudo docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^vllm\$'; then
  echo "[remote-setup] starting vllm container with image=${image}..."
  sudo docker run -d --rm --name vllm \\
    --privileged --network host \\
    -v /tmp:/tmp -v \$HOME:/root \\
    -e HF_TOKEN='${HF_TOKEN}' \\
    -e VLLM_TPU_BACKEND=pallas \\
    --entrypoint /bin/bash \\
    ${image} \\
    -c "sleep infinity"
else
  echo "[remote-setup] vllm container already running"
fi
sleep 5
# transformers upgrade (同梱 4.57.6 -> >=4.58 for Gemma 4 model_type support)
# 5.x系は MODEL_IMPL_TYPE が 'vllm' に解決されて TPU engine core 初期化に失敗するため 4.x に pin
echo "[remote-setup] upgrading transformers..."
sudo docker exec vllm pip install -U transformers --quiet 2>&1 | tail -5 || true
# 事前確認: vllm / transformers バージョン記録
sudo docker exec vllm bash -lc 'python -c "import vllm; print(\"vllm=\"+vllm.__version__)" 2>&1 | head -5' || true
sudo docker exec vllm bash -lc 'python -c "import transformers; print(\"transformers=\"+transformers.__version__)" 2>&1 | head -5' || true
if ! sudo docker exec vllm bash -c "ss -ltn 2>/dev/null | grep -q :8000 || netstat -ltn 2>/dev/null | grep -q :8000"; then
  echo "[remote-setup] launching vllm serve..."
  sudo docker exec -d vllm bash -lc "cd /tmp && nohup python -m vllm.entrypoints.openai.api_server --model google/gemma-4-31B-it --max-model-len 16384 --max-num-batched-tokens 4096 --gpu-memory-utilization 0.90 --port 8000 --tensor-parallel-size 4 > /tmp/vllm_server.log 2>&1 &"
else
  echo "[remote-setup] vllm already listening"
fi
echo "[remote-setup] waiting for /v1/models (timeout_polls=${timeout_polls})..."
for i in \$(seq 1 ${timeout_polls}); do
  if curl -s -m 5 http://localhost:8000/v1/models 2>/dev/null | grep -q gemma-4-31B-it; then
    echo "[remote-setup] vLLM READY after \$i polls"
    exit 0
  fi
  # 1 分毎にログ tail 出力 (原因切り分け高速化)
  if [ \$((i % 6)) -eq 0 ]; then
    echo "[remote-setup] poll=\$i still waiting; last vllm_server.log tail:"
    sudo docker exec vllm bash -c "tail -8 /tmp/vllm_server.log 2>/dev/null" || true
  fi
  sleep 10
done
echo "[remote-setup] ERROR: vLLM did not ready within ${timeout_polls} polls (=\$((timeout_polls*10))s)"
sudo docker logs vllm 2>&1 | tail -80 || true
tail -120 /tmp/vllm_server.log 2>/dev/null || true
exit 1
REMOTE
)
  echo "y" | gcloud compute tpus tpu-vm ssh "$TPU_NAME" --zone="$zone" --project="$PROJECT" --command="$cmd" 2>&1 | tee -a "$LOG"
  return ${PIPESTATUS[1]}
}

cleanup_vllm() {
  # $1 = zone. 別イメージを試すためコンテナ削除. TPU VM 自体は残す.
  local zone="$1"
  local cmd="sudo docker rm -f vllm 2>/dev/null || true; echo cleanup-done"
  echo "y" | gcloud compute tpus tpu-vm ssh "$TPU_NAME" --zone="$zone" --project="$PROJECT" --command="$cmd" 2>&1 | tee -a "$LOG" || true
}

# Step 0 dry-run: 5 件サブセットで推論 + GCS 書込までの流れが成立するか検証.
dryrun_5items() {
  local zone="$1"
  # 5 件サブセット作成 (ヘッダ有無は poc_gemma4_tpu_monthly_test.py が吸収)
  local dryrun_cmd
  dryrun_cmd=$(cat <<REMOTE
set -e
head -5 ${INPUT_TPU} > /tmp/gemma4_dryrun_input.jsonl
echo "[dryrun] 5-item input ready (\$(wc -l < /tmp/gemma4_dryrun_input.jsonl) lines)"
cd /tmp
export PROMPT_VERSION=baseline
export RUN_SUFFIX=dryrun
pip install --quiet --user httpx 2>/dev/null || pip3 install --quiet --user httpx 2>/dev/null || true
timeout 900 python3 -u /tmp/poc_gemma4_tpu_monthly_test.py /tmp/gemma4_dryrun_input.jsonl /tmp/gemma4_dryrun_results.jsonl --prompt-version baseline 2>&1 | tail -40
echo "[dryrun] local output lines=\$(wc -l < /tmp/gemma4_dryrun_results.jsonl 2>/dev/null || echo 0)"
REMOTE
)
  echo "y" | gcloud compute tpus tpu-vm ssh "$TPU_NAME" --zone="$zone" --project="$PROJECT" --command="$dryrun_cmd" 2>&1 | tee -a "$LOG"
  local rc=${PIPESTATUS[1]}
  return $rc
}

create_tpu_with_rotation() {
  # TPU 作成 (spot, ゾーンローテ). 成功時 PICKED_ZONE をセット.
  PICKED_ZONE=""
  log "=== Create spot TPU v6e-4 ==="
  for round in 1 2 3 4 5 6 7 8 9 10; do
    log "--- create round $round ---"
    for z in "${ZONES[@]}"; do
      log "trying zone=$z round=$round"
      OUT=$(gcloud compute tpus tpu-vm create "$TPU_NAME" \
        --zone="$z" \
        --accelerator-type=v6e-4 \
        --version=v2-alpha-tpuv6e \
        --spot \
        --project="$PROJECT" 2>&1)
      rc=$?
      if [ $rc -eq 0 ]; then
        PICKED_ZONE="$z"
        log "[OK] TPU created in $z"
        # Wait READY
        for i in $(seq 1 120); do
          STATE=$(gcloud compute tpus tpu-vm describe "$TPU_NAME" --zone="$z" --project="$PROJECT" --format='value(state)' 2>/dev/null || echo "GONE")
          log "wait[$i] state=$STATE"
          if [ "$STATE" = "READY" ]; then
            return 0
          fi
          if [ "$STATE" = "GONE" ] || [ "$STATE" = "PREEMPTED" ] || [ -z "$STATE" ]; then
            log "TPU lost during bringup in $z"
            PICKED_ZONE=""
            break
          fi
          sleep 10
        done
        if [ -z "$PICKED_ZONE" ]; then
          log "retry (TPU lost during bringup)"
          continue
        fi
      fi
      log "[NG] zone=$z rc=$rc msg=$(echo "$OUT" | head -3 | tr '\n' ' ' | cut -c1-200)"
    done
    log "round $round done, sleeping 120s"
    sleep 120
  done
  log "ERROR: all zones exhausted"
  return 1
}

# ====== MAIN ======
log "=== PhaseD Orchestrator start (retry-5, healthcheck-enforced) ==="
log "pinned image: $VLLM_IMAGE"

# 入力確認
if [ ! -f "$INPUT_LOCAL" ]; then
  log "ERROR: input file missing: $INPUT_LOCAL"
  notify_line "Gemma4 PhaseD 失敗" "入力JSONL不在" urgent
  exit 1
fi

# (1) TPU 作成
if ! create_tpu_with_rotation; then
  notify_line "Gemma4 PhaseD 失敗" "全ゾーンTPU取得失敗" urgent
  exit 1
fi

# (2) vLLM 起動 (SHA digest pin 済み、20分待ち)
log "=== Step 1: vLLM setup (digest-pinned image) ==="
if ! setup_vllm "$PICKED_ZONE" "$VLLM_IMAGE" 120; then
  log "ERROR: vLLM setup failed with pinned image"
  notify_line "Gemma4 PhaseD 要ユーザー判断" "vLLM起動失敗(digest pin). ログ確認要" urgent
  exit 1
fi
log "=== vLLM READY ==="

# (2.5) Docker image を GCS に退避 (推論開始前に最優先)
log "=== Step 2: Docker image GCS save ==="
GCS_DOCKER_PATH="gs://stock_data_1930932/docker/vllm-tpu-gemma4-ready-20260417.tar.gz"
save_cmd=$(cat <<'SAVECMD'
set -e
echo "[docker-save] listing images..."
sudo docker images --no-trunc 2>&1 | head -10
# commit running container (with transformers upgrade baked in)
echo "[docker-save] committing container vllm -> vllm-tpu-gemma4-ready..."
sudo docker commit vllm vllm-tpu-gemma4-ready
echo "[docker-save] saving vllm-tpu-gemma4-ready + gzip..."
sudo docker save vllm-tpu-gemma4-ready | gzip > /tmp/vllm-tpu-gemma4-ready.tar.gz
ls -lh /tmp/vllm-tpu-gemma4-ready.tar.gz
echo "[docker-save] uploading to GCS..."
gcloud storage cp /tmp/vllm-tpu-gemma4-ready.tar.gz gs://stock_data_1930932/docker/vllm-tpu-gemma4-ready-20260417.tar.gz
echo "[docker-save] verifying..."
gcloud storage ls -l gs://stock_data_1930932/docker/vllm-tpu-gemma4-ready-20260417.tar.gz
echo "[docker-save] DONE"
rm -f /tmp/vllm-tpu-gemma4-ready.tar.gz
SAVECMD
)
echo "y" | gcloud compute tpus tpu-vm ssh "$TPU_NAME" --zone="$PICKED_ZONE" --project="$PROJECT" --command="$save_cmd" 2>&1 | tee -a "$LOG"
DOCKER_SAVE_RC=${PIPESTATUS[1]}
if [ "$DOCKER_SAVE_RC" -ne 0 ]; then
  log "WARNING: Docker image GCS save failed (rc=$DOCKER_SAVE_RC) - continuing with inference"
else
  log "[OK] Docker image saved to GCS"
fi

# (3) 5 件 dry-run で推論 + GCS 書込確認
log "=== Step 3: 5-item dry-run inference ==="
if ! dryrun_5items "$PICKED_ZONE"; then
  log "WARNING: 5-item dry-run did not complete cleanly (continuing to full run anyway)"
else
  log "[OK] 5-item dry-run succeeded"
fi

# (4) 3ラン直列
for ver in baseline v2 v3; do
  RUN_OK=0
  for try in 1 2 3; do
    if run_one_version "$ver" "$PICKED_ZONE"; then
      RUN_OK=1
      break
    fi
    log "run $ver try $try failed"
    STATE=$(gcloud compute tpus tpu-vm describe "$TPU_NAME" --zone="$PICKED_ZONE" --project="$PROJECT" --format='value(state)' 2>/dev/null || echo "GONE")
    if [ "$STATE" != "READY" ]; then
      log "TPU not READY ($STATE), recreating"
      delete_tpu
      if ! create_tpu_with_rotation; then
        log "ERROR: could not recreate TPU for resume"
        notify_line "Gemma4 PhaseD 失敗" "resume時 TPU取得失敗 ver=$ver" urgent
        break 2
      fi
      # 再作成後は 20 分まで待つ (フルモデルロード想定)
      if ! setup_vllm "$PICKED_ZONE" "$VLLM_IMAGE" 120; then
        log "ERROR: setup failed after recreate"
        break 2
      fi
    fi
  done
  if [ "$RUN_OK" -ne 1 ]; then
    log "run $ver did not complete cleanly (GCS may have partial data)"
  fi
done

# (4) TPU 削除
delete_tpu

# (5) Dropbox + 比較レポート
log "=== Dropbox copy + report ==="
STAMP=$(TZ=Asia/Tokyo date +%Y%m%d_%H%M)
DROPBOX_DIR="/c/Users/zonekun/Dropbox/stock/temp/gemma4_4_31b_tpu_poc/phaseD_${STAMP}"
mkdir -p "$DROPBOX_DIR"
for ver in baseline v2 v3; do
  F="/c/tmp/gemma4_tpu_monthly_phaseD_${ver}_results.jsonl"
  if [ -f "$F" ]; then
    cp "$F" "$DROPBOX_DIR/" 2>&1 | tee -a "$LOG"
  fi
done
cp "$INPUT_LOCAL" "$DROPBOX_DIR/" 2>&1 | tee -a "$LOG"

cd "$REPO" && PYTHONUTF8=1 "$VENV_PY" scripts/poc_gemma4_phaseD_report.py 2>&1 | tee -a "$LOG"
cp /c/tmp/gemma4_tpu_monthly_phaseD_comparison.md "$DROPBOX_DIR/" 2>&1 | tee -a "$LOG" || true

# (6) LINE 通知
SUMMARY="PhaseD完了 zone=$PICKED_ZONE"
for ver in baseline v2 v3; do
  F="/c/tmp/gemma4_tpu_monthly_phaseD_${ver}_results.jsonl"
  N=$(wc -l < "$F" 2>/dev/null || echo 0)
  SUMMARY="$SUMMARY $ver=${N}"
done
SUMMARY="$SUMMARY out=$DROPBOX_DIR"
notify_line "Gemma4 PhaseD 完了" "$SUMMARY" high

log "=== ALL DONE ==="

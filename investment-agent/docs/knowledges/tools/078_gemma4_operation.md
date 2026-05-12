# AI モデル運用ノウハウ（Gemma TPU + Gemini Batch Prediction）

**カテゴリ**: tools
**作成日**: 2026-04-17（Gemma 専用として起票）
**更新日**: 2026-04-20（Gemini Batch Prediction 運用ノウハウ追加、スコープ拡張）
**ステータス**: 有効（本番稼働中）

本ドキュメントは **TDnet 新アーキで使用する 2 つの AI モデル運用（Gemma 4 31B TPU + Gemini Flash Batch Prediction）** を集約。セクション 1-5 が Gemma/TPU/vLLM、**セクション 6 が Gemini Batch Prediction**。

**関連ドキュメント**:
- `013_tdnet_load.md` — TDnet load 運用本体、Workflows/Cloud Run Job 構成
- `013-1_ai_cost_and_gemma_poc.md` — AI コスト分析、Gemma プロンプト採用、PoC 履歴
- `080_workflows_runbook.md` — Cloud Workflows 汎用ノウハウ

### 恒久ルール（変わらない原則）

- `:latest` / `:nightly` タグ使用禁止、必ず digest pin
- `docker pull` 直後（コンテナ起動前）に `nohup` でバックグラウンド GCS 退避
- vLLM 起動前に `pip install -U transformers`（5.x 必要）
- Docker 3段階起動（`sleep` → `pip install` → `vllm serve`）
- GCS 継続 append + resume 設計（1件完了ごとに JSONL 書込）
- 起動後 `/v1/models` ヘルスチェック必須（未応答なら推論開始しない）

**時点情報**（1か月以上経過したら `gcloud` / Docker Hub で現状確認）:
ゾーン一覧、Docker image digest、transformers バージョン、vLLM フラグ、quota/キャパ、spot 料金

---

## 1. TPU v6e-4 セットアップ

### 1-1. TPU 作成

```bash
gcloud compute tpus tpu-vm create gemma4-tpu \
  --zone=us-central1-b \
  --accelerator-type=v6e-4 \
  --version=v2-alpha-tpuv6e \
  --spot \
  --project=gmailpj-357912
```

- **spot 推奨**: on-demand ($3.11/hr) より安い ($1.60/hr)。preemption 耐性は GCS resume で担保
- **on-demand quota**: us-central1-b で quota=0 になることあり（spot は別枠）
- **ゾーンローテ**: us-central1-b → us-east5-a → us-east5-b → us-east5-c → us-west1-c（us-east4-c は非対応）

### 1-2. vLLM Docker セットアップ（3段階起動）

```bash
# ★ :latest / :nightly は使用禁止 — 必ず固定 digest or バージョンタグ
VLLM_IMAGE="vllm/vllm-tpu@sha256:360eae322ff76fe0f414d9b7c1ee769d65a07447da478e69784145dbedc5743e"
# タグ名版: vllm/vllm-tpu:nightly-20260416-c98c322-08bfedc

sudo docker pull "$VLLM_IMAGE"

# ★ pull 直後に GCS 退避（初回のみ）
if ! gcloud storage ls "gs://stock_data_1930932/docker/vllm-tpu-<tag>.tar.gz" 2>/dev/null; then
  nohup bash -c 'sudo docker save "$VLLM_IMAGE" | gzip > /tmp/vllm-tpu.tar.gz && \
    gcloud storage cp /tmp/vllm-tpu.tar.gz \
    gs://stock_data_1930932/docker/vllm-tpu-<tag>.tar.gz' &
fi

# Step 1: コンテナを bash で起動（vLLM はまだ起動しない）
sudo docker run -d --name gemma4-vllm --privileged --net=host \
  -e HF_TOKEN=$HF_TOKEN \
  "$VLLM_IMAGE" \
  bash -c 'sleep infinity'

# Step 2: コンテナ内で transformers アップグレード
# ★ transformers 4.58 は存在しない。5.x が必要
sudo docker exec gemma4-vllm pip install -U transformers

# Step 3: コンテナ内で vLLM serve 起動
# ★ --tensor-parallel-size 4 必須（tp=1 では HBM RESOURCE_EXHAUSTED）
sudo docker exec -d gemma4-vllm python -m vllm.entrypoints.openai.api_server \
  --model google/gemma-4-31B-it \
  --port 8000 \
  --max-model-len 16384 \
  --max-num-batched-tokens 4096 \
  --tensor-parallel-size 4
```

**⚠️ `docker run` で直接 vLLM serve 起動は禁止**: transformers upgrade 前に vLLM が起動し、Gemma 4 非対応のまま即クラッシュ → 全件 ConnectError で「処理済み」として GCS 書込 → resume が全件スキップという事故が起きる。

### 1-3. 起動確認

```bash
# 最大15分ポーリング（モデルロード + TPU コンパイルに時間がかかる）
for i in $(seq 1 90); do
  curl -s http://localhost:8000/v1/models && break
  sleep 10
done
```

---

## 2. 落とし穴・失敗事例集

| # | 事象 | 対処 |
|---|------|------|
| 2-1 | `:latest` タグが毎日 07:22 UTC に自動更新 → 翌日同じ構成で起動失敗 | digest pin 必須（セクション 1-2） |
| 2-2 | 成功した Docker image を退避せずに TPU 削除 → image 消失 → 再現不可 | `docker pull` 直後に `nohup` GCS 退避（初回のみ） |
| 2-3 | vllm-tpu 同梱 transformers 4.57.6 は `model_type=gemma4` 未対応。4.58 は PyPI に存在しない | 5.x が必要。`pip install -U transformers` を起動前に実行 |
| 2-4 | デフォルト `--max-num-batched-tokens 2048` が Gemma 4 multimodal encoder (2496) と衝突 | `--max-num-batched-tokens 4096` を明示 |
| 2-5 | 新 vLLM で `--disable-log-requests` が削除 → 指定するとエラー | フラグを外す |
| 2-6 | SSH 自動リトライで同一 TPU 上で vLLM が二重起動 → HBM 競合 RESOURCE_EXHAUSTED | 起動前に `docker ps | grep vllm` で確認 |
| 2-7 | spot TPU preemption で推論結果全ロスト | GCS 1件ごと append + resume 実装で冪等化（現在実装済） |
| 2-8 | `max-model-len 16384` に 30,000 文字投入 → HTTP 400 トークン超過 | `src/llm/truncation.py` の `truncate_for_model()` で Gemma=20,000 文字上限適用 |
| 2-9 | on-demand v6e-4 の quota が突然消失 | spot で回避。on-demand 必要なら GCP コンソールで quota 申請 |
| 2-10 | `--tensor-parallel-size` 未指定（tp=1）で HBM 不足 RESOURCE_EXHAUSTED | `--tensor-parallel-size 4` 必須（v6e-4 は 4チップ × 16GB = 64GB） |
| 2-11 | `docker run ... $IMAGE --model ...` で直接起動 → transformers upgrade 前に vLLM 起動 → 全件失敗 | 3段階起動（セクション 1-2）＋ main 冒頭ヘルスチェック |
| 2-12 | `docker save | gzip` 中に SSH タイムアウト | `docker pull` 直後に `nohup` バックグラウンド実行 |

---

## 4. 動作確認済み構成（2026-04-17 確定）

| 項目 | 値 |
|------|-----|
| Docker image | `vllm/vllm-tpu@sha256:360eae322ff76fe0f414d9b7c1ee769d65a07447da478e69784145dbedc5743e` |
| タグ名 | `nightly-20260416-c98c322-08bfedc` (vLLM 0.12.x) |
| transformers | **5.x**（pip upgrade 後。4.58 は存在しない） |
| モデル / vLLM フラグ | `google/gemma-4-31B-it` / `--max-model-len 16384 --max-num-batched-tokens 4096 --tensor-parallel-size 4` |
| TPU | v6e-4 spot (us-central1-b / us-east5-a)、HBM ~28GB / 64GB |
| スループット | 1.95件/秒（並列度8、4.08秒/件） |
| Docker GCS退避 | 未完了（SSH タイムアウト。digest は本ファイルに記録済み） |

---

## 5. Phase I 本番実装ノウハウ（Cloud Run Job + Cloud Workflows 統合）

**アーキテクチャ**: Workflows `ai_processing_flow` → parallel → `gemma_runner_branch`（Cloud Run Job `tdnet-gemma-runner` → `gemma_tpu_runner.sh`）＋ `callback_wait_branch`（`events.await_callback` 待機）。runner.sh が TPU 作成・worker.py 転送・vLLM 起動・推論・Callback POST・TPU 削除（trap EXIT）を一貫実行。

関連ファイル: `scripts/gemma_tpu_runner.sh` / `scripts/gemma_tpu_worker.py` / `docker/Dockerfile.tdnet-gemma-runner` / `workflows/ai_processing_flow.yaml`

### 5-2. `--tunnel-through-iap` は `gcloud alpha` 必須

```bash
# ❌ GA では `--tunnel-through-iap` 認識されない
# ✅ alpha track で有効
gcloud alpha compute tpus tpu-vm ssh VM --tunnel-through-iap
gcloud alpha compute tpus tpu-vm scp FILE VM:/dest --tunnel-through-iap
```

Cloud Run の `google/cloud-sdk:slim` コンテナはデフォルトで alpha コマンドを持つ。追加インストール不要。

### 5-3. Cloud Run Job SA に必要な IAM 権限

`roles/tpu.admin` / `roles/iap.tunnelResourceAccessor` / `roles/compute.osAdminLogin` / `roles/iam.serviceAccountUser` / `roles/secretmanager.secretAccessor` / `roles/workflows.invoker` / `roles/storage.objectAdmin` / `roles/logging.logWriter`

### 5-4. TPU VM 上からの認証トークン取得

metadata server 経由で取得（TPU VM の SA が効く）:
- **access_token**: `GET /computeMetadata/v1/instance/service-accounts/default/token` → `.json()["access_token"]`
- **OIDC ID token**: 同 `/identity?audience=<CALLBACK_URL>&format=full` → `.text.strip()`

いずれも `headers={"Metadata-Flavor": "Google"}` が必須。`http://metadata.google.internal` ベース。

### 5-5. Workflows Callback URL への POST 認証

`workflowexecutions.googleapis.com` Callback エンドポイントは **OAuth2 Bearer 必須**。SA に `roles/workflows.invoker` を付与し `access_token` を Bearer ヘッダで渡す。access_token 失敗時は OIDC ID token (audience=CALLBACK_URL) にフォールバック。

### 5-6. SSH 事前 warm-up

初回 SSH 接続はホストキー保存確認プロンプトで止まることがある: `echo "y" | gcloud alpha compute tpus tpu-vm ssh VM --tunnel-through-iap --command="echo warmup-ok"`

### 5-7. TPU VM 名の制約

小文字+数字+ハイフン、63文字以内、`_` 不可。Workflows execution ID (UUID v4, 36文字) → `tdnet-ai-gemma-{id}` = 50文字で OK:

```bash
TPU_NAME=$(echo "tdnet-ai-gemma-${RUN_ID}" | tr '_' '-' | tr '[:upper:]' '[:lower:]' | cut -c1-63)
```

### 5-8. Windows Git Bash の落とし穴

`gcloud --format="value(name)"` の出力に CR が混入: `VM=$(... | tr -d '\r')`。Cloud Run 内（Linux）では発生しない。

### 5-9. Cloud Run Job connector の timeout 指定

`googleapis.run.v2.projects.locations.jobs.run` のデフォルト polling timeout = 1800s。長時間ジョブは明示必須:

```yaml
- run_ai_prepare:
    call: googleapis.run.v2.projects.locations.jobs.run
    args:
      name: ...
      connector_params:
        timeout: 21600      # Workflows 側 LRO polling timeout (6h)
      body:
        overrides:
          timeout: "21600s" # Cloud Run Job 側 task-timeout（別概念、同値推奨）
```

両方とも必要。片方だけだとどちらかで先に打ち切られる。

### 5-10. Workflows での TPU preempt リカバリ

`gemma_runner_branch` に retry 設定。worker.py が GCS `gemma_CURRENT.jsonl` の `done_doc_ids` で resume するため冪等:

```yaml
- run_gemma_runner:
    try:
      call: googleapis.run.v2.projects.locations.jobs.run
      ...
    retry:
      max_retries: 3
      backoff:
        initial_delay: 60
        max_delay: 600
        multiplier: 2
```

---

## 6. Gemini Batch Prediction 運用

本番 Phase 3（カテゴリ判定）では Gemma 4 31B TPU が主だが、**決算短信のみ** `gemini-3-flash-preview` の Vertex AI Batch Prediction で受注高/受注残高判定を上書きしている。

### 6-1. 大量件数の並列分割（5-parallel split）

**背景**: 単一 batch に 5,000+ 件投入すると Vertex AI 側で 3h+ に長期化。1,000 件/chunk に分割して最大 5 並列投入することで wall time を ~1-2h に短縮（コスト影響ゼロ）。

**実装**（`scripts/tdnet_load_parallel.py`）: `PHASE3_PARALLEL=5` / `PHASE3_MIN_PER_CHUNK=200`。`_phase3_submit` で最大 5 chunk に分割して独立 Job 投入。`_phase3_poll_and_apply` で `ThreadPoolExecutor` 並列 poll・結果集約。chunk JSONL は `analysis_{ts}_p{N}of{M}_input.jsonl` 形式で GCS 保存。

### 6-2. 進捗確認（`completionStats` REST API）

ai-finalize が Phase 3 RUNNING で長時間停滞している時の内部進捗確認:

```bash
TOKEN=$(gcloud auth print-access-token)
curl -s "https://aiplatform.googleapis.com/v1/projects/gmailpj-357912/locations/global/batchPredictionJobs/<JOB_ID>" \
  -H "Authorization: Bearer $TOKEN" | jq '{state, completionStats, error}'
```

| フィールド | 意味 |
|-----------|------|
| `state` | QUEUED / PENDING / RUNNING / SUCCEEDED / FAILED |
| `completionStats.successfulCount` | **成功件数（進捗カウンタ）** |
| `completionStats.incompleteCount` | 未完了（実行中含む） |
| `completionStats.failedCount` | 失敗 |

**batch job ID**: ai-finalize 実行ログから「`batch job 投入: projects/.../batchPredictionJobs/<ID>`」を抽出。

**finalize lag**: `successfulCount + incompleteCount ≈ 投入数` なら実質完了。state=RUNNING のまま GCS 出力書込＋bookkeeping で 30分程度 lag → stall 判定しない。

### 6-3. 落とし穴

- **`location='global'` 混雑**: 長時間 QUEUED になる場合 `us-central1` 直指定も検討
- **token 超過 (HTTP 400)**: `src/llm/truncation.py` の `truncate_for_model` 閾値で制御
- **対応モデル**: Llama/gpt-oss/Qwen/DeepSeek のみ。Mistral/Claude/Grok は online only
- **Embedding Batch 料金**: 文字課金 $0.025/1M chars（トークン課金ではない）

### 6-4. 関連コード

- `scripts/tdnet_load_parallel.py`: `_phase3_submit` / `_phase3_poll_and_apply` / `phase_gemini_tanshin_batch`
- `src/llm/truncation.py`: `truncate_for_model` でモデル別文字数上限適用

---

## 7. Gemma ↔ Gemini 結合部アンチパターン

汎用ルールは `004_coding_conventions.md` §バッチジョブ・ETL アンチパターン集。以下は Gemma/Gemini Batch 固有の論点。

### G-1. `gemma_CURRENT.jsonl` を `download_as_text()` で一括ロード

**症状**: Gemma worker が resume 用に append し続けた JSONL を丸読み → 数百MB で OOM 予備軍。

**対策**: ストリーム読み必須:
```python
for line in blob.open("r"):
    rec = json.loads(line)
    results[rec["doc_id"]] = rec  # raw text は即捨て
```
閾値目安: JSONL 100MB 超えたらストリーム読み強制。

### G-2. Gemma 部分失敗を `sub_categories=[]` で完了扱い

**症状**: TPU preempt で途中死した doc が WARN log で流れるだけ → 後続バッチでも再処理されない。

**対策**:
- missing doc に `gemma_status = "missing"` or `needs_retry = True` を明示付与
- BQ 側は `gemma_status != "ok"` の行を次回 run で優先再処理するクエリを持つ
- `_apply_gemma_results` が `missing_count` を return し、閾値超えなら `sys.exit(1)`

### G-3. 既存 batch 関数を流用するために一時 mutation で filter する

**症状**: `phase3_analysis_batch` 流用のために全 doc の `needs_analysis` を一時書換→呼出→戻す実装 → 並列 5 batch submit と競合でスレッド安全性を損なう。

**対策**: 対象絞り込みは `filter_fn` 引数で表現: `phase3_analysis_batch(docs, ..., filter_fn=lambda d: d.category == "tanshin")`

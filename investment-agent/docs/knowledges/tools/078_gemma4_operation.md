# AI モデル運用ノウハウ（Gemma TPU + Gemini Batch Prediction）

**カテゴリ**: tools
**作成日**: 2026-04-17（Gemma 専用として起票）
**更新日**: 2026-04-20（Gemini Batch Prediction 運用ノウハウ追加、スコープ拡張）
**ステータス**: 有効（本番稼働中）

本ドキュメントは **TDnet 新アーキで使用する 2 つの AI モデル運用（Gemma 4 31B TPU + Gemini Flash Batch Prediction）** を集約。セクション 1-5 が Gemma/TPU/vLLM、**セクション 6 が Gemini Batch Prediction**。

## 📌 現況サマリ（2026-04-19）

**本番動作確認済み構成**（セクション4 詳細）:

| 項目 | 値 |
|------|-----|
| Docker image | `vllm/vllm-tpu@sha256:360eae322ff76fe0f414d9b7c1ee769d65a07447da478e69784145dbedc5743e` |
| タグ名 | `nightly-20260416-c98c322-08bfedc` |
| transformers | **5.x**（pip upgrade 後。4.58 は存在しない） |
| モデル | `google/gemma-4-31B-it` |
| TPU | v6e-4 spot (us-central1-b / us-east5-a) |
| vLLM フラグ | `--max-model-len 16384 --max-num-batched-tokens 4096 --tensor-parallel-size 4` |
| 起動手順 | 3段階（`docker run bash sleep` → `docker exec pip install` → `docker exec vllm serve`） |
| スループット | 1.95件/秒（並列度8、4.08秒/件） |

**本番統合**: TDnet Phase I で Cloud Run Job + Cloud Workflows + TPU v6e-4 spot の全自動パイプライン完遂（2026-04-18、507 doc、$2.4）。詳細はセクション 5。

**関連ドキュメント**:
- **`013_tdnet_load.md`** — TDnet load 運用本体、Workflows/Cloud Run Job 構成
- **`013-1_ai_cost_and_gemma_poc.md`** — AI コスト分析、Gemma プロンプト採用、2024年1月 PoC 履歴
- **`080_workflows_runbook.md`** — Cloud Workflows 汎用ノウハウ

**関連スクリプト**:
- `scripts/gemma_tpu_worker.py` — TPU VM 上で vLLM 推論 + GCS append + Callback
- `scripts/gemma_tpu_runner.sh` — Cloud Run Job 内で TPU 作成〜削除
- `scripts/poc_gemma4_tpu_monthly_test.py` — 2024年1月 PoC 用（参考、本番未使用）

### 本ファイルの読み方

**恒久ルール**（変わらない原則）:
- `:latest` タグ使用禁止、digest pin 必須
- Docker save は pull 直後に実行
- vLLM 起動前に transformers upgrade
- 3段階 Docker 起動手順
- GCS 継続 append + resume 設計
- vLLM ヘルスチェック必須

**時点情報**（使う前に現状確認）:
- ゾーン一覧・ゾーンローテ順、Docker image digest、transformers バージョン、`--tensor-parallel-size` 等の vLLM フラグ、quota/キャパ、spot 料金

記載が古い（目安 1か月以上）場合は `gcloud` / Docker Hub で現状確認。新構成確定時はセクション4を更新。

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
- **on-demand quota 注意**: us-central1-b で on-demand v6e-4 は quota=0 になることがある（spot は別枠で空いている場合多い）
- **ゾーンローテ**: us-central1-b → us-east5-a → us-east5-b → us-east5-c → us-west1-c（us-east4-c は非対応）

### 1-2. vLLM Docker セットアップ

```bash
# ★ :latest / :nightly は使用禁止 — 必ず固定 digest or バージョンタグ
VLLM_IMAGE="vllm/vllm-tpu@sha256:360eae322ff76fe0f414d9b7c1ee769d65a07447da478e69784145dbedc5743e"
# タグ名版: vllm/vllm-tpu:nightly-20260416-c98c322-08bfedc

sudo docker pull "$VLLM_IMAGE"

# Step 1: コンテナをbashで起動（vLLM はまだ起動しない）
sudo docker run -d --name gemma4-vllm --privileged --net=host \
  -e HF_TOKEN=$HF_TOKEN \
  "$VLLM_IMAGE" \
  bash -c 'sleep infinity'

# Step 2: コンテナ内で transformers アップグレード
# ★ transformers 4.58 は存在しない。5.x が必要（2026-04-17 Phase D で確認）
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

**⚠️ 旧手順（`docker run` で直接 vLLM serve）は禁止**:
`docker run ... $IMAGE --model ...` だと transformers upgrade 前に vLLM が起動し、
Gemma 4 非対応の transformers 4.57 のまま起動 → 即クラッシュ → ConnectError で
推論全件失敗という事故が発生する（2026-04-17 Phase D retry4 で発生）。

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

### 2-1. `:latest` タグドリフト（2026-04-17 発覚）

**事象**: 4/16 成功した同じ構成で 4/17 に実行したら vLLM 起動不可。
**原因**: `vllm/vllm-tpu:latest` タグが Google により 0.12.x → 0.13.0 に push 更新。0.13.0 は `transformers<5` を要求するが Gemma 4 は `>=5.x` 必須 → 互換性崩壊。
**教訓**:
- **`:latest` / `:nightly` タグは再現性ゼロ**。毎日 07:22 UTC に自動更新される
- **必ず SHA digest または固定バージョンタグで pin**
- 成功した Docker image は **即座に GCS 退避**（後述）

### 2-2. Docker image 未退避（2026-04-17 発覚）

**事象**: 4/16 成功時の Docker image を保存せず TPU 削除 → image 消失 → タグ再現不可。
**原因**: `docker save` → GCS 退避の手順がなかった。
**教訓**: 新しい Docker image digest を**初めて使う時だけ** GCS に退避する。
同じ digest で 2回目以降の TPU 起動では不要（GCS に既にあるため）。

```bash
# 初回のみ: docker pull 直後に即実行（コンテナ起動前、TPU負荷ゼロ）
sudo docker pull "$VLLM_IMAGE"

# GCS に同じ digest が既にあるか確認
if ! gcloud storage ls "gs://stock_data_1930932/docker/vllm-tpu-<tag>.tar.gz" 2>/dev/null; then
  nohup bash -c 'sudo docker save "$VLLM_IMAGE" | gzip > /tmp/vllm-tpu.tar.gz && \
    gcloud storage cp /tmp/vllm-tpu.tar.gz \
    gs://stock_data_1930932/docker/vllm-tpu-<tag>.tar.gz' &
  # ↑ nohup でバックグラウンド実行（SSH タイムアウト対策）
  # 保存と並行して vLLM セットアップを進めてよい
fi
```

**GCS 退避先**: `gs://stock_data_1930932/docker/`
**保存頻度**: digest ごとに 1回だけ。毎回の TPU 起動で保存する必要はない。

### 2-3. transformers バージョン不整合（2026-04-17 訂正）

**事象**: vllm-tpu 同梱 transformers 4.57.6 は `model_type=gemma4` 未対応。
**対処**: `pip install -U transformers` を vLLM 起動前にコンテナ内で実行。
**⚠️ 訂正**: 当初 `transformers>=4.58` としていたが、**4.58 は PyPI に存在しない**。
実際に必要なのは **transformers 5.x**（Phase D retry5 で確認）。
以前の「5.x は tpu_inference が壊れる」は vllm-tpu 0.13.0 でのみ発生し、
**nightly-20260416 (0.12.x相当) では 5.x で正常動作する**。

### 2-4. `--max-num-batched-tokens` 衝突

**事象**: 既定値 2048 で起動すると Gemma 4 multimodal encoder（2496）と衝突して起動失敗。
**対処**: `--max-num-batched-tokens 4096` を明示。

### 2-5. `--disable-log-requests` フラグ削除

**事象**: 新 vLLM で `--disable-log-requests` が削除されており、指定すると起動エラー。
**対処**: フラグを外す。

### 2-6. vLLM 二重起動

**事象**: gcloud SSH 自動リトライで同一 TPU 上で vLLM が二重起動 → HBM 競合で RESOURCE_EXHAUSTED。
**対処**: 起動前に `docker ps | grep vllm` で既存コンテナの有無を確認し、あればスキップ or `docker rm -f`。

### 2-7. spot TPU preemption（2026-04-16 発覚）

**事象**: v1 PoC 実行中に spot TPU が 66分で preempted、推論結果全ロスト。
**原因**: GCS 継続 append が未実装だった（当時）。
**対処**: GCS 都度追記 + resume 機構を実装（現在は実装済）:
- 推論結果を 1件完了ごとに JSONL として GCS `CURRENT.jsonl` に書き込み
- 起動時に既存 CURRENT.jsonl を取得 → `done_doc_ids` 集合化 → 残件のみ処理
- 同じ CURRENT.jsonl に対して何度 resume しても doc_id 単位で重複なし

### 2-8. HTTP 400 トークン超過（2026-04-16 v1 PoC）

**事象**: `max-model-len 16384` の Gemma 4 31B に 30,000文字を投入 → 106/2,090件が HTTP 400。
**原因**: 数字密度の高い J-REIT 帳票型 PDF で digit 1桁 = 1 token のため実トークン数が爆発。
**対処**: `src/llm/truncation.py` の `truncate_for_model()` で モデル別文字数予算（Gemma=20,000文字）を適用。決算短信は無改変（チャンク分割で対応）。

### 2-9. on-demand quota 突然消失（2026-04-17 発覚）

**事象**: 4/16 に on-demand v6e-4 が us-central1-b で成功。4/17 に同じゾーンで「User does not have permission to submit requests for accelerator type v6e-4」。
**原因**: GCP のバースト/プレビュー quota が消費・期限切れした可能性。on-demand と spot は別枠 quota。
**対処**: spot で回避可能。on-demand が必要な場合は GCP コンソールで quota 申請。

### 2-10. tensor-parallel-size 未指定で RESOURCE_EXHAUSTED（2026-04-17 Phase D で発覚）

**事象**: `--tensor-parallel-size` 未指定（既定 tp=1）で vLLM 起動 → Gemma 4 31B のモデルロード時に HBM 不足で RESOURCE_EXHAUSTED。
**原因**: tp=1 だと 1チップ（16GB HBM）にモデル全体をロードしようとする。Gemma 4 31B は ~28GB 必要。
**対処**: `--tensor-parallel-size 4` を明示指定。v6e-4 は 4チップ × 16GB = 64GB なので tp=4 で分散すれば収まる。

### 2-11. Docker run で直接 vLLM serve 起動（2026-04-17 Phase D retry4 で発覚）

**事象**: `docker run ... $IMAGE --model ...` で vLLM を直接起動 → transformers upgrade 前に vLLM が走り Gemma 4 非対応 → 即クラッシュ → localhost:8000 応答なし → スクリプトが全 2,359件 ConnectError で「処理済み」として GCS 書込 → resume が全件スキップ。
**原因**: docker run のエントリポイントが vLLM serve コマンドで、pip install の余地がない。
**対処**: `docker run ... bash -c 'sleep infinity'` → `docker exec pip install` → `docker exec vllm serve` の3段階に分離。
**追加対策**: スクリプト `main()` 冒頭に vLLM ヘルスチェック（`/v1/models` 200応答確認）を追加。未応答なら推論開始しない。

### 2-12. Docker image GCS退避の SSH タイムアウト（2026-04-17 Phase D retry5）

**事象**: vLLM 起動成功後に `docker save | gzip` → GCS cp を試みたが、SSH タイムアウトで失敗。
**原因**: Docker image が大きく（数GB）、`docker save | gzip` に時間がかかりすぎて SSH セッションが切れた。
**対処**: `docker pull` 完了直後（コンテナ起動前）に `nohup` でバックグラウンド実行（2-2 参照）。vLLM 起動を待つ必要はない（`docker save` は image を保存するため、コンテナ run は不要）。
**現状**: Docker image GCS退避は未完了。成功した digest (`sha256:360eae...`) は知見MDに記録済みなので pull は再現可能だが、Docker Hub からの将来的なタグ削除リスクは残る。

---

## 3. 運用チェックリスト

### TPU 起動時

- [ ] Docker image は **固定 digest** で pull（`:latest` `:nightly` 禁止）
- [ ] `pip install -U "transformers>=4.58"` 実行（5.x は使わない）
- [ ] `--max-num-batched-tokens 4096` 指定
- [ ] `--disable-log-requests` は指定しない
- [ ] vLLM `/v1/models` 応答確認（最大15分）
- [ ] **Docker image を GCS に退避**（`docker save | gzip → gcloud storage cp`）
- [ ] 既存 vLLM コンテナの二重起動チェック

### 推論実行時

- [ ] GCS 継続 append が動作していることを確認（開始5分後に blob サイズチェック）
- [ ] `truncate_for_model()` 適用済み
- [ ] preemption 時の resume 手順を確認

### TPU 削除時

- [ ] `gcloud compute tpus tpu-vm delete ... --quiet`
- [ ] 全ゾーンで `tpu-vm list` = 0件を確認
- [ ] Docker image が GCS に退避済みであることを再確認

---

## 4. 動作確認済み構成（2026-04-17 Phase D retry5 で確定）

| 項目 | 値 |
|------|-----|
| Docker image | `vllm/vllm-tpu@sha256:360eae322ff76fe0f414d9b7c1ee769d65a07447da478e69784145dbedc5743e` |
| タグ名 | `nightly-20260416-c98c322-08bfedc` |
| vLLM version | 0.12.x |
| transformers | **5.x**（pip upgrade 後。4.58 は存在しない） |
| モデル | `google/gemma-4-31B-it` |
| max-model-len | 16384 |
| max-num-batched-tokens | 4096 |
| **tensor-parallel-size** | **4**（tp=1 は RESOURCE_EXHAUSTED） |
| 起動手順 | `docker run bash sleep` → `docker exec pip install` → `docker exec vllm serve`（3段階） |
| TPU | v6e-4 spot (us-central1-b / us-east5-a) |
| HBM 使用量 | ~28GB / 64GB（tp=4 で分散） |
| スループット | 1.95件/秒（並列度8） |
| 平均推論時間 | 4.08秒/件 |
| Docker GCS退避 | **未完了**（SSH タイムアウト、digest は記録済み） |

---

## 5. Phase I 本番実装ノウハウ（2026-04-18 完遂）

TDnet load 改修で TPU 運用を **Cloud Run Job + Cloud Workflows** に統合した際の実装ノウハウ。

### 5-1. アーキテクチャ

```
[Cloud Workflows ai_processing_flow]
  └ parallel
     ├ gemma_runner_branch: googleapis.run.v2...jobs.run で tdnet-gemma-runner 起動
     │    └ [Cloud Run Job tdnet-gemma-runner]
     │         └ scripts/gemma_tpu_runner.sh (bash, google/cloud-sdk:slim)
     │              ├ hf-token 取得（Secret Manager）
     │              ├ gcloud compute tpus tpu-vm create --spot
     │              ├ state=READY 待機
     │              ├ gcloud alpha compute tpus tpu-vm ssh --tunnel-through-iap (warm-up)
     │              ├ gcloud alpha ... scp で worker.py 転送
     │              ├ gcloud alpha ... ssh --command="vLLM setup + healthcheck"
     │              ├ gcloud alpha ... ssh --command="python3 gemma_tpu_worker.py"
     │              └ trap で TPU 削除（EXIT）
     └ callback_wait_branch: events.await_callback 待機（worker.py が POST）
```

関連ファイル:
- `scripts/gemma_tpu_runner.sh` — Cloud Run Job 内で動く本番 runner
- `scripts/gemma_tpu_worker.py` — TPU VM 上で vLLM 推論 + GCS continuous append + Callback
- `docker/Dockerfile.tdnet-gemma-runner` — google/cloud-sdk:slim + bash + httpx
- `cloudbuild/cloudbuild.tdnet-gemma-runner.yaml`
- `workflows/ai_processing_flow.yaml`

### 5-2. `--tunnel-through-iap` は `gcloud alpha` 必須

```bash
# ❌ GA では `--tunnel-through-iap` 認識されない
gcloud compute tpus tpu-vm ssh VM --tunnel-through-iap

# ✅ alpha track で有効
gcloud alpha compute tpus tpu-vm ssh VM --tunnel-through-iap
gcloud alpha compute tpus tpu-vm scp FILE VM:/dest --tunnel-through-iap
```

Cloud Run のコンテナ（`google/cloud-sdk:slim`）はデフォルトで alpha コマンドを持つ。追加インストール不要。

### 5-3. Cloud Run Job SA に必要な IAM 権限

TPU 操作する SA（`bq-loader@...` 等）に以下を付与:

| ロール | 用途 |
|-------|------|
| `roles/tpu.admin` | TPU create / delete |
| `roles/iap.tunnelResourceAccessor` | IAP tunnel 経由 SSH |
| `roles/compute.osAdminLogin` | OS Login 経由 SSH (TPU VM対象) |
| `roles/iam.serviceAccountUser` | TPU VM の SA actAs |
| `roles/secretmanager.secretAccessor` | hf-token 取得 |
| `roles/workflows.invoker` | Workflows Callback URL への POST |
| `roles/storage.objectAdmin` | GCS state/_SUCCESS/gemma_CURRENT.jsonl 操作 |
| `roles/logging.logWriter` | Workflows sys.log 等 |

### 5-4. TPU VM 上からの認証トークン取得

TPU VM の metadata server 経由で access_token / OIDC ID token 取得（TPU VM の SA 認証が効く）:

```python
# access_token
httpx.get(
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
    headers={"Metadata-Flavor": "Google"},
).json()["access_token"]

# OIDC ID token (audience=Callback URL)
httpx.get(
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity",
    params={"audience": CALLBACK_URL, "format": "full"},
    headers={"Metadata-Flavor": "Google"},
).text.strip()
```

### 5-5. Workflows Callback URL への POST 認証

`workflowexecutions.googleapis.com` Callback エンドポイントは **OAuth2 Bearer 必須**。

```python
# ❌ 認証なし → 401 UNAUTHENTICATED
httpx.post(CALLBACK_URL, json=body)

# ❌ SA に workflows.invoker 無し → 403 Forbidden
httpx.post(CALLBACK_URL, json=body, headers={"Authorization": f"Bearer {access_token}"})

# ✅ SA に roles/workflows.invoker + access_token
# access_token 失敗時は OIDC ID token (audience=CALLBACK_URL) にフォールバック
```

### 5-6. SSH 事前 warm-up（ホストキーキャッシュ）

初回 SSH 接続は対話プロンプト（ホストキー保存の確認）で止まる可能性。特に PuTTY/plink 系は `FATAL ERROR: Network error` で失敗。前処理:

```bash
echo "y" | gcloud alpha compute tpus tpu-vm ssh VM \
  --tunnel-through-iap --command="echo warmup-ok"
```

### 5-7. TPU VM 名の制約

- 小文字 + 数字 + ハイフン、63文字以内
- `_` 使用不可 → UUID v4 の `-` 形式ならそのまま使える
- Workflows execution ID（UUID v4、36文字）→ `tdnet-ai-gemma-{execution_id}` = 50文字で OK

```bash
TPU_NAME="tdnet-ai-gemma-${RUN_ID}"
TPU_NAME=$(echo "${TPU_NAME}" | tr '_' '-' | tr '[:upper:]' '[:lower:]' | cut -c1-63)
```

### 5-8. Windows Git Bash 環境の落とし穴

`gcloud --format="value(name)"` の出力に CR が混入する（Windows の改行コード）:

```bash
# ❌ そのまま使うと "TPU-NAME\r" が RFC 3986 violation で TPU delete に失敗
VM=$(gcloud compute tpus tpu-vm list ... --format="value(name)")
gcloud compute tpus tpu-vm delete "$VM" ...  # FAIL

# ✅ tr -d '\r' で除去
VM=$(gcloud compute tpus tpu-vm list ... --format="value(name)" | tr -d '\r')
```

Cloud Run 内（Linux）では発生しない。**Windows ローカル実行時のみ**問題。

### 5-9. Cloud Run Job run connector の timeout 指定

Workflows の `googleapis.run.v2.projects.locations.jobs.run` は Long Running Operation (LRO) を polling。**デフォルト polling timeout = 1800s (30分)**。長時間ジョブは明示必須:

```yaml
- run_ai_prepare:
    call: googleapis.run.v2.projects.locations.jobs.run
    args:
      name: ...
      connector_params:
        timeout: 21600      # ← Workflows 側 LRO polling timeout (6h)
      body:
        overrides:
          timeout: "21600s" # ← Cloud Run Job 側 task-timeout（別概念、同値推奨）
```

両方とも必要。片方だけだとどちらかで先に打ち切られる。

### 5-10. Workflows での TPU preempt リカバリ

`gemma_runner_branch` 側に retry 設定。worker.py が GCS `gemma_CURRENT.jsonl` の `done_doc_ids` で resume するため冪等:

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

### 5-11. 本番実測（2026-04-18 Phase I 検証）

| 項目 | 値 |
|------|-----|
| 入力件数 | 507 doc (2023-01-04〜06) |
| TPU | v6e-4 spot (us-central1-b) |
| TPU 稼働時間 | ~35分（vLLM setup ~13分 + 推論 ~22分） |
| 推論時間 | 507 件で ~22分（1件/秒ペース） |
| preemption | 発生せず（retry 未発動） |
| Gemma 成功率 | 507 / 507 = 100% |
| Callback | access_token http=200（OIDC fallback 不要） |
| TPU コスト | $4/hr × 0.58h ≈ **$2.32** |
| Cloud Run (runner) | ~35分稼働、~$0.05 |
| Workflows | 1 execution、~$0 |
| **全体** | **~$2.4**（507 doc 処理） |

### 5-12. 関連知見

- TDnet 統合アーキ全体: `013_tdnet_load.md` 「新アーキ（BQロード/AI判定分離）」セクション
- Workflows 総合: `080_workflows_runbook.md`（Phase I 完遂後に作成）
- BQ Load Job 使い分け: `api/002_bigquery.md`

---

## 6. Gemini Batch Prediction 運用（2026-04-20 追加）

本番 Phase 3（カテゴリ判定）では Gemma 4 31B TPU が主だが、**決算短信のみ** `gemini-3-flash-preview` の Vertex AI Batch Prediction で受注高/受注残高判定を上書きしている。2026-04-20 にその Gemini 運用で重要な知見が蓄積されたため本セクションで集約。

### 6-1. 大量件数の並列分割実装

**背景**: 単一 Gemini Flash Batch に 5,000+ 件投入すると Vertex AI 側の compute が 3h+ に長期化（batch A 5,446件 = 3h 経過時点で compute 完了）。1 batch ≒ 1,000 件で分割して並列投入することで wall time を短縮。

**実装**（`scripts/tdnet_load_parallel.py`）:

```python
PHASE3_PARALLEL = 5              # 最大並列 batch 数
PHASE3_MIN_PER_CHUNK = 200       # 1 chunk あたり最低件数
```

- `_phase3_submit`: 対象件数に応じて最大 5 chunk に分割、各 chunk 独立の Batch Prediction Job として投入
- `_phase3_poll_and_apply`: `concurrent.futures.ThreadPoolExecutor` で並列 poll、結果集約
- 各 chunk の JSONL は `analysis_{ts}_p{N}of{M}_input.jsonl` 形式で GCS に保存

**コスト影響**: **ゼロ**。per-token 課金なので batch 数は無関係。batch 管理 API は無料、GCS 一時ファイルは秒単位削除可能で実質無視可。

**効果**: batch A (5,446件) で単一 3h+ → 5 並列で ~1-2h。ただし Vertex AI 側の internal parallelism が 1 batch あたり下がる傾向、全体 throughput は 2-3 倍止まりで純 5x にはならない。

**Vertex AI 側の同時実行制約**: project-level で 5-10 concurrent 可能（region 別、公式明記なし）。`global` location は混雑しがち、`us-central1` へ分散も一手。

### 6-2. 進捗確認（`completionStats` REST API）

ai-finalize が Phase 3 RUNNING で長時間停滞している時の内部進捗確認方法:

```bash
TOKEN=$(gcloud auth print-access-token)
curl -s "https://aiplatform.googleapis.com/v1/projects/gmailpj-357912/locations/global/batchPredictionJobs/<JOB_ID>" \
  -H "Authorization: Bearer $TOKEN" | jq '{state, completionStats, error}'
```

**返却フィールド**:

| フィールド | 意味 |
|-----------|------|
| `state` | QUEUED / PENDING / RUNNING / SUCCEEDED / FAILED |
| `completionStats.successfulCount` | **成功件数（進捗カウンタ相当）** |
| `completionStats.incompleteCount` | 未完了（実行中含む） |
| `completionStats.failedCount` | 失敗 |

**batch job ID の取得**: ai-finalize 実行ログから「`batch job 投入: projects/.../batchPredictionJobs/<ID>`」を抽出。

**実例**（2026-04-20 batch A 58hnk）: 5,446件投入後 3h 経過で `successfulCount=5,442, incompleteCount=4`。compute は実質完了だが、**state=RUNNING のまま Vertex AI 側 finalize 処理（GCS 出力書込＋bookkeeping）で 30分程度 lag**。`successfulCount + incompleteCount ≈ 投入数` なら state 遷移待ち、ただ待てばよい（stall 判定しない）。

### 6-3. 落とし穴

- **finalize lag**: 上記のとおり `successfulCount + incompleteCount = 投入数` ≒ 実質完了、state 遷移まで数分〜30分。Python 側 poll は延々続く（`_poll_batch_job` は state しか見ない）
- **location='global' 混雑**: 長時間 QUEUED になる場合 `us-central1` 直指定も検討
- **token 超過 (HTTP 400)**: 決算短信は 30,000 文字まで切り詰め済だが、数字密度高い J-REIT 帳票型等は別途対応必要。`src/llm/truncation.py` の `truncate_for_model` 閾値で制御
- **Vertex AI Batch Prediction 対応モデル**: partner models のうち Llama/gpt-oss/Qwen/DeepSeek のみ対応。Mistral/Claude/Grok は online only（50% 割引無、`013-1` も参照）
- **Embedding Batch (`text-embedding-004`) 料金**: 文字課金 $0.025/1M chars（トークン課金誤認に注意）

### 6-4. 関連コード

- `scripts/tdnet_load_parallel.py`:
  - `_phase3_submit` / `_phase3_poll_and_apply` — 並列投入・poll
  - `_build_merged_prompt` — 月次判定 + サブカテゴリ抽出の統合プロンプト
  - `phase_gemini_tanshin_batch` — 決算短信のみ受注判定用ラッパー（ai-finalize で使用）
- `src/llm/truncation.py` — `truncate_for_model` でモデル別文字数上限適用

---

## 7. Gemma ↔ Gemini 結合部アンチパターン

**出所**: 2026-04-20 の TDnet load 系ジョブ事故レビュー。ai-prepare / Gemma worker / ai-finalize の結合部で顕在化した設計課題。004_coding_conventions.md の汎用アンチパターン集を前提に、Gemma/Gemini Batch 固有の論点のみ記載する。

### G-1. Gemma worker → ai-finalize の JSONL を `download_as_text()` で一括ロード

**該当**: `scripts/tdnet_load_parallel.py::_load_gemma_results`

**症状**: `gemma_CURRENT.jsonl`（Gemma worker が GCS に continuous append）を `blob.download_as_text()` で丸読み。実測 数百MB に達しており、TDnet 件数増 or カテゴリ拡張で OOM 予備軍。

**Gemma/Gemini Batch 固有の事情**:
- Gemma worker は **resume 用に GCS へ append し続ける**（セクション 3 設計）。finalize 時点で「過去全件分」が1ファイルに溜まっている
- ai-finalize は Cloud Run Job で **メモリ 2-4GB** 程度の割当。プロセス内に全件保持すると他フェーズ（Gemini Batch 投入、BQ MERGE）と競合

**対策**:
```python
for line in blob.open("r"):
    rec = json.loads(line)
    # 即 dict に投入、raw text は捨てる
    results[rec["doc_id"]] = rec
```
- 004 の C-2 を Gemma/Gemini 結合部で**必ず**適用
- 閾値目安: JSONL 100MB 超えたらストリーム読み強制

### G-2. Gemma 部分失敗を `sub_categories=[]` で完了扱い

**該当**: `scripts/tdnet_load_parallel.py::_apply_gemma_results`

**症状**: Gemma worker が TPU preempt 等で途中死した doc は出力 JSONL に現れない。ai-finalize 側は `missing` を WARN log で流すだけで下流（BQ / Gemini Batch）への信号がなく、**後続バッチでも再処理されない**。

**Gemma/Gemini Batch 固有の事情**:
- TPU spot は preempt が日常（セクション 1）。worker 単位では callback で検知するが、**doc 粒度の部分失敗は worker 完了通知からは見えない**
- Gemini Batch と違い Gemma は自前 worker のため、プラットフォーム側の successfulCount / failedCount が無い（completionStats 的なものが無い）

**対策**:
- missing doc に `gemma_status = "missing"` or `needs_retry = True` を明示付与
- BQ 側は `gemma_status != "ok"` の行を次回 run で優先再処理するクエリを持つ
- `_apply_gemma_results` が `missing_count` を return し、main が `errors += missing_count` してこれが閾値超えなら `sys.exit(1)`（004 の A-1）

### G-3. 既存 batch 関数を流用するために一時 mutation で filter する

**該当**: `scripts/tdnet_load_parallel.py::phase_gemini_tanshin_batch` → `phase3_analysis_batch` 呼出

**症状**: `phase3_analysis_batch` は「`needs_analysis=True` の doc のみ処理」仕様。決算短信限定の `phase_gemini_tanshin_batch` がこれを流用するため、一時的に全 doc の `needs_analysis` を書換→呼出→戻す形で実装。**グローバル状態 mutation でスレッド安全性・可読性を損なう**。

**Gemma/Gemini Batch 固有の事情**:
- Phase 3 は並列 5 batch submit（6-1）。共有 state の一時書換は並列投入と競合する可能性
- 決算短信のみ Gemini 切替（Gemma 苦手カテゴリの切り分け、013-1 参照）のような **対象絞り込み** は今後も増える想定（中計のみ / 受注のみ 等）

**対策**:
```python
# ❌ 一時 mutation
orig = {d.doc_id: d.needs_analysis for d in docs}
for d in docs: d.needs_analysis = (d.category == "tanshin")
phase3_analysis_batch(docs, ...)
for d in docs: d.needs_analysis = orig[d.doc_id]

# ✅ filter_fn を引数化
phase3_analysis_batch(docs, ..., filter_fn=lambda d: d.category == "tanshin")
```
- 対象絞り込みは **filter_fn / predicate 引数** で表現
- 004 の E-2（mutable global）の Gemma/Gemini Batch 版。Phase 3 並列投入と併用するため特に重要

### 関連
- 004_coding_conventions.md §バッチジョブ・ETL アンチパターン集 — 汎用ルール（本節はこれに依拠）
- 013_tdnet_load.md — ai-prepare / gemma-worker / ai-finalize の役割分担

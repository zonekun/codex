# Cloud Workflows 運用ノウハウ

**カテゴリ**: tools
**作成日**: 2026-04-18
**ステータス**: 有効
**関連プロジェクト**:
- `workflows/ai_processing_flow.yaml` — TDnet AI処理オーケストレーション
- `docs/knowledges/tools/013_tdnet_load.md` — 新アーキ全体（Workflows 部分）
- `docs/knowledges/tools/078_gemma4_operation.md` セクション 5 — Gemma TPU + Workflows 連携

## 概要

Cloud Workflows は Google Cloud の**サーバーレスオーケストレーション**。複数の Cloud Run Job / Cloud Functions / Google API 呼出を YAML で順次/並列実行できる。

TDnet 新アーキ Phase I 実装時にハマった問題と解決策を集約。

---

## 1. parallel branch の shared 変数（最重要）

### 落とし穴: `result:` は local scope

`result:` は assign と違って「local scope への書き込み」。`result: gemma_callback` と書いても shared 変数には反映されない（null のまま）。公式: "A variable in the shared field must be declared outside of the parallel step."

shared 変数に反映するには**別途 `assign` step が必要**。症状: shared 変数が init 値のまま / `${shared_var.field}` 参照が `KeyError` で FAILED（Workflows state は SUCCEEDED）。

### 正しい書き方

```yaml
- init:
    assign:
      - gemma_callback: {}  # shared 変数は parallel 外で宣言

- run_parallel:
    parallel:
      shared: [gemma_callback]
      branches:
        - branch1:
            steps:
              - wait:
                  try:
                    call: events.await_callback
                    args: ...
                    result: local_callback   # ← 一旦 local に受ける
                  except:
                    as: e
                    steps:
                      - fallback:
                          assign:
                            - local_callback: {"status": "timeout"}
              - assign_shared:             # ← 明示的に shared に代入
                  assign:
                    - gemma_callback: ${local_callback}

- done:
    return:
      value: ${map.get(gemma_callback, "received")}  # 防御的に map.get 使う
```

---

## 2. `connector_params.timeout` で LRO polling timeout を延ばす

**落とし穴**: `googleapis.run.v2...jobs.run` はデフォルト polling timeout 1800s (30分)。step level `timeout:` は "unexpected entry" で deploy 失敗。**`args.connector_params.timeout` に書く**:


```yaml
- run_job:
    call: googleapis.run.v2.projects.locations.jobs.run
    args:
      name: ...
      connector_params:
        timeout: 21600          # ← Workflows LRO polling timeout（秒）
      body:
        overrides:
          timeout: "21600s"     # ← Cloud Run Job 側 task-timeout（別概念、string "<N>s" 形式）
```

**両方を同値に揃えるのが推奨**。片方だけだと先に打ち切られた方で失敗。

### `workflowexecutions.v1.executions.create` も LRO 扱い

子 Workflow を起動する `googleapis.workflowexecutions.v1.projects.locations.workflows.executions.create` は、REST API としては standard call（HTTP 201 即返し）だが、**Cloud Workflows connector は LRO として自動ポーリングする**。したがって `connector_params.timeout` で子 Workflow の完了待ち時間を制御できる。

```yaml
- trigger_child_workflow:
    call: googleapis.workflowexecutions.v1.projects.locations.workflows.executions.create
    args:
      parent: ${"projects/" + project_id + "/locations/" + location + "/workflows/" + child_workflow}
      connector_params:
        timeout: 43200    # 子Workflowの完了を最大12h待つ
      body:
        argument: '{"key": "value"}'
```

**根拠**: `tdnet_daily_pipeline` 初回デプロイ時、デフォルト 1800s で子 Workflow（52min 所要）が LRO タイムアウトした。即座に返る standard call なら 1800s タイムアウトは発生しない。connector が内部で execution 完了をポーリングしている証拠。

---

## 3. Callback URL 認証

**Callback URL 生成**:
```yaml
- create_callback:
    call: events.create_callback_endpoint
    args:
      http_callback_method: "POST"
    result: callback_details
# callback_details.url は workflowexecutions.googleapis.com の HTTPS エンドポイント
```

### 外部から Callback URL へ POST する際の認証

**OAuth2 Bearer token 必須**（公開エンドポイントではない）。SA に `roles/workflows.invoker` が必要。

| 試す順 | 方法 | 備考 |
|-------|------|------|
| 1 | **access_token**（metadata server） | 同プロジェクト SA でシンプル |
| 2 | **OIDC ID token** (audience=CALLBACK_URL) | access_token で 401/403 の場合 |

```bash
# access_token（GCE/TPU VM metadata server）
TOKEN=$(curl -s -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# OIDC ID token
curl -s -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=${CALLBACK_URL}&format=full"
```

```python
# Python（httpx）
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
r = httpx.post(CALLBACK_URL, json=body, headers=headers, timeout=30)
```

**失敗パターン**: 401 → Authorization ヘッダなし。403 → SA に `roles/workflows.invoker` 未付与。

---

## 4. retry の predicate と backoff

```yaml
- run_step:
    try:
      call: some.api
      args: ...
    retry:
      max_retries: 3
      backoff:
        initial_delay: 60    # 秒
        max_delay: 600
        multiplier: 2
```

`predicate:` 未指定だと **全エラー retry**。選択的 retry したい場合は `${http.default_retry_predicate}` 等を使う。

**冪等性の前提**: retry は同じ引数で同じステップを再実行する。Cloud Run Job の場合、Job 側で冪等に設計する（例: GCS `done_ids` で resume）。

---

## 5. shared 変数 null 安全 + 文字列・リテラル落とし穴

**shared 変数**: parallel 外で宣言必須。`default()` 第二引数への `{}` / `[]` 直書きは構文エラー → init で空変数化:
```yaml
- init:
    assign:
      - empty_map: {}
# ❌ ${default(var.key, {})}  → ✅ ${default(var.key, empty_map)}
```

**API Connector に無い API**（TPU など）は `http.post` + OAuth2 で直叩き:
```yaml
- call_rest:
    call: http.post
    args:
      url: ${"https://tpu.googleapis.com/v2/projects/" + project_id + "/..."}
      auth:
        type: OAuth2
        scopes: [https://www.googleapis.com/auth/cloud-platform]
      body: {key: value}
```

**文字列リテラル**:
- `text.replace` は存在しない → `text.replace_all` を使う
- YAML 値にコロン `:`・`=` を含む式はシングルクオートで囲む: `'${"status: " + s}'`
- 式内 `{}` / `[]` リテラルは不可 → init で変数化してから参照

---

## 6. 起動・デバッグ

```bash
# 手動実行
gcloud workflows execute ai_processing_flow \
  --location=us-central1 \
  --data='{"date_from":"20230104","date_to":"20230106"}'

# Cloud Scheduler 登録
gcloud scheduler jobs create http ai_processing_flow_daily \
  --location=us-central1 --schedule="0 15 * * 1-5" \
  --time-zone="Asia/Tokyo" \
  --uri="https://workflowexecutions.googleapis.com/v1/projects/${PROJECT}/locations/us-central1/workflows/ai_processing_flow/executions" \
  --http-method=POST \
  --oauth-service-account-email="bq-loader@${PROJECT}.iam.gserviceaccount.com"

# state 確認
gcloud workflows executions describe EXEC_ID \
  --workflow ai_processing_flow --location us-central1 \
  --format="value(state,error,status)"
```

- `FAILED` 時は `--format=json` で `error.context` / `error.stackTrace` を取得（step 名 + 行番号）
- `sys.log` を仕込むと Cloud Logging に出る（`workflows.logEntries.create` 権限必要）
- Eventarc / Pub/Sub / Callback いずれも **at-least-once**。冪等性担保は呼び先の責任

---

## 7. IAM 権限まとめ

Workflows 実行 SA（例: `bq-loader@...`）に必要:

| ロール | 用途 |
|-------|------|
| `roles/workflows.invoker` | Workflows 実行 + Callback URL POST |
| `roles/logging.logWriter` | sys.log 等 |
| `roles/run.developer` または `run.invoker` | Cloud Run Job を `googleapis.run.v2...jobs.run` で呼ぶ |
| 呼び先 API ごとの個別ロール | bigquery.jobUser, tpu.admin, etc. |

---

## 8. 次ステップ gate 設計（Cloud Run exit code を単独で信じない）

### 落とし穴

Cloud Run Job 側に「exit 0 嘘」（例外を握って errors=1 にしても `sys.exit()` せず exit 0 で終わる）が潜んでいると、Workflows から見て `SUCCEEDED` で返る。state だけで次ステップ発火条件を書いていると、**実データが入っていないまま下流が発火する**。

### 対策: 複合 gate（state + 実測データ）

```yaml
- check_rows:
    call: googleapis.bigquery.v2.jobs.query
    args:
      projectId: ${project}
      body:
        query: "SELECT COUNT(*) AS cnt FROM `proj.dataset.table` WHERE dt = @date AND status = 'completed'"
        queryParameters:
          - name: date
            parameterType: {type: DATE}
            parameterValue: {value: ${target_date}}
    result: rows
- gate:
    switch:
      - condition: ${int(rows.rows[0].f[0].v) > 0}
        next: start_next_batch
      - condition: true
        raise: "SUCCEEDED but 0 completed rows — aborting"
```

他の gate 候補: GCS `_SUCCESS` sentinel blob / 出力 blob 数 / 次段 input key set。

**連携ルール**: 上流 Cloud Run Job 側で `sys.exit(1)` を徹底（`004_coding_conventions.md §A-1`）＋ Workflows 側で複合 gate。片方だけでは「嘘の成功」を検知できない。

---

## 9. 参照

- 公式ドキュメント: https://cloud.google.com/workflows/docs
- parallel shared 変数: https://cloud.google.com/workflows/docs/samples/workflows-parallel-shared-variables
- connector_params: https://cloud.google.com/workflows/docs/reference/googleapis
- Callback endpoint: https://cloud.google.com/workflows/docs/creating-callback-endpoints
- **`004_coding_conventions.md §A-8`** — 上流 exit code だけに依存しない gate 設計（本節の汎用版）

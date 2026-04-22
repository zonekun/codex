# Cloud Workflows 運用ノウハウ

**カテゴリ**: tools
**作成日**: 2026-04-18
**ステータス**: 有効
**関連プロジェクト**:
- `workflows/ai_processing_flow.yaml` — TDnet AI処理オーケストレーション（Phase I 完遂）
- `docs/knowledges/tools/013_tdnet_load.md` — 新アーキ全体（Workflows 部分）
- `docs/knowledges/tools/078_gemma4_operation.md` セクション 5 — Gemma TPU + Workflows 連携

## 概要

Cloud Workflows は Google Cloud の**サーバーレスオーケストレーション**。複数の Cloud Run Job / Cloud Functions / Google API 呼出を YAML で順次/並列実行できる。

TDnet 新アーキで **Phase I 実装時にハマった問題と解決策**を集約。同じ罠を他プロジェクトで踏まないためのノウハウ集。

---

## 1. parallel branch の shared 変数（最重要）

### 落とし穴: `result:` は local scope

```yaml
# ❌ これだと gemma_callback が外で参照できない（null になる）
- run_parallel:
    parallel:
      shared: [gemma_callback]
      branches:
        - branch1:
            steps:
              - wait:
                  call: events.await_callback
                  args: ...
                  result: gemma_callback   # ← local scope にしか書かれない

- done:
    return:
      value: ${gemma_callback.received}  # KeyError: key not found: received
```

公式ドキュメント明記:
> "A variable in the shared field must be declared outside of the parallel step. The variable can then be **modified within the parallel step**."

`result:` は assign と違って「local scope への書き込み」。shared 変数に反映するには**別途 `assign` step が必要**。

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

### 発生症状

- `gemma_summary: null` のように **shared 変数が init 値のまま**
- done step で `${shared_var.field}` 参照が `KeyError: key not found` で FAILED
- Workflows state は **SUCCEEDED** になる（parallel 自体は完了）のに、shared が空

---

## 2. `connector_params.timeout` で LRO polling timeout を延ばす

### 落とし穴: 30 分で打ち切り

`googleapis.run.v2.projects.locations.jobs.run` は Long Running Operation (LRO) を内部で polling。**デフォルト polling timeout は 1800s (30分)**。6時間かかる Cloud Run Job を呼ぶと 30分で打ち切られる。

### step level `timeout:` は受け付けない

```yaml
# ❌ "unexpected entry 'timeout'" で deploy 失敗
- run_job:
    call: googleapis.run.v2.projects.locations.jobs.run
    timeout: 21600  # ← step レベルには書けない
    args: ...
```

### 正しい書き方: `args.connector_params.timeout`

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

---

## 3. Callback URL 認証

### events.create_callback_endpoint の Callback URL

```yaml
- create_callback:
    call: events.create_callback_endpoint
    args:
      http_callback_method: "POST"
    result: callback_details
# callback_details.url は workflowexecutions.googleapis.com の HTTPS エンドポイント
```

### 外部から Callback URL へ POST する際の認証

**OAuth2 Bearer token 必須**（公開エンドポイントではない）。

| 試す順 | 方法 | 必要 IAM | 備考 |
|-------|------|---------|------|
| 1 | **access_token** | `roles/workflows.invoker` | 同プロジェクト SA でシンプル |
| 2 | **OIDC ID token** (audience=CALLBACK_URL) | 同上 | access_token で 401/403 の場合のフォールバック |

#### GCE/TPU VM から access_token 取得

```bash
# metadata server 経由（VM の default SA）
curl -s -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
# → {"access_token": "...", "expires_in": 3599, ...}
```

#### GCE/TPU VM から OIDC ID token 取得

```bash
curl -s -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=${CALLBACK_URL}&format=full"
```

#### Python（httpx）での投げ方

```python
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
r = httpx.post(CALLBACK_URL, json=body, headers=headers, timeout=30)
```

### 失敗パターン

| エラー | 原因 | 対処 |
|--------|------|------|
| 401 UNAUTHENTICATED `Expected OAuth 2 access token` | 認証なしで POST | Authorization ヘッダ付与 |
| 403 Forbidden `workflowexecutions.googleapis.com` | SA に `roles/workflows.invoker` なし | ロール付与 |

---

## 4. retry の predicate と backoff

### 書き方

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

### 冪等性の前提

retry は **同じ引数で同じステップを再実行**する。呼び先が冪等でないと副作用が重複する。Cloud Run Job の場合、Job 側で冪等に設計する（例: GCS `done_ids` で resume）。

---

## 5. shared 変数の init + null 安全

shared 変数は parallel 外で宣言が必要だが、**未代入のまま後続 step で参照すると KeyError**。init で空 dict / null 相当を入れておく + `map.get(var, "key")` で防御的に参照:

```yaml
- init:
    assign:
      - gemma_callback: {}
      - empty_map: {}
      - timeout_callback:
          received:
            status: "timeout"

# ... parallel 後 ...

- done:
    return:
      value: ${map.get(gemma_callback, "received")}  # null 安全
```

**`default()` の第二引数に `{}` リテラル直書きは構文エラー**になるため、init で別変数に入れてから参照する:

```yaml
# ❌ 構文エラー
- done:
    return:
      value: ${default(gemma_callback.received, {})}

# ✅ 先に init で empty_map = {} 定義しておいて
- done:
    return:
      value: ${default(gemma_callback.received, empty_map)}
```

---

## 6. API Connector の制限

### TPU API は Connector 未対応

`googleapis.tpu.v2.projects.locations.nodes` などの Connector は**存在しない**（2026-04 時点）。TPU 操作するには `http.post` + OAuth2 で TPU REST API を直接叩く必要あり:

```yaml
- create_tpu:
    call: http.post
    args:
      url: ${"https://tpu.googleapis.com/v2/projects/" + project_id + "/locations/" + zone + "/nodes?nodeId=" + node_id}
      auth:
        type: OAuth2
        scopes:
          - https://www.googleapis.com/auth/cloud-platform
      body:
        acceleratorType: v6e-4
        runtimeVersion: v2-alpha-tpuv6e
        schedulingConfig:
          preemptible: true
        serviceAccount:
          email: "..."
          scope: ["https://www.googleapis.com/auth/cloud-platform"]
        metadata:
          ...
```

ただし TDnet 新アーキでは **Cloud Run Job からの TPU 操作に切り替え**（`gcloud` CLI 経由）。Workflows から直接 TPU 操作しない。理由は startup-script が TPU v6e で動作しない事象（→ `078_gemma4_operation.md` セクション 5 参照）。

---

## 7. 文字列置換・リテラル

### `text.replace_all` を使う

```yaml
# ❌ text.replace は存在しない（古いドキュメントの誤記を見ることあり）
- step:
    assign:
      - safe_id: ${text.replace(run_id, "_", "-")}

# ✅
- step:
    assign:
      - safe_id: ${text.replace_all(run_id, "_", "-")}
```

### string 内のコロン・`=` はシングルクオート必要

```yaml
# ❌ `:` でパース失敗
- log:
    call: sys.log
    args:
      text: ${"status: " + status}

# ✅ 全体をシングルクオート
- log:
    call: sys.log
    args:
      text: '${"status: " + status}'
```

### dict / list リテラル

`${default(x, {})}` のような**式内の `{}` / `[]` リテラルは不可**。init で変数化して使う。

---

## 8. 起動方法

### 手動

```bash
gcloud workflows execute ai_processing_flow \
  --location=us-central1 \
  --data='{"date_from":"20230104","date_to":"20230106"}'
```

### Cloud Scheduler 自動

```bash
gcloud scheduler jobs create http ai_processing_flow_daily \
  --location=us-central1 \
  --schedule="0 15 * * 1-5" \
  --time-zone="Asia/Tokyo" \
  --uri="https://workflowexecutions.googleapis.com/v1/projects/${PROJECT}/locations/us-central1/workflows/ai_processing_flow/executions" \
  --http-method=POST \
  --oauth-service-account-email="bq-loader@${PROJECT}.iam.gserviceaccount.com"
```

---

## 9. デバッグ

### state 確認

```bash
gcloud workflows executions describe EXEC_ID \
  --workflow ai_processing_flow --location us-central1 \
  --format="value(state,error,status)"
```

### エラー詳細

- `FAILED` 時は `describe --format=json` で error.context / error.stackTrace を取得
- stackTrace には step 名 + 行番号が入る
- step 内で `sys.log` を適切に仕込むと当該 execution の Cloud Logging に出る（`workflows.logEntries.create` 権限必要）

### at-least-once 配信

Eventarc / Pub/Sub / Callback いずれも **at-least-once**。冪等性担保は呼び先の責任。

---

## 10. IAM 権限まとめ

Workflows 実行 SA（例: `bq-loader@...`）に必要:

| ロール | 用途 |
|-------|------|
| `roles/workflows.invoker` | Workflows 実行 + Callback URL POST |
| `roles/logging.logWriter` | sys.log 等 |
| `roles/run.developer` または `run.invoker` | Cloud Run Job を `googleapis.run.v2...jobs.run` で呼ぶ |
| 呼び先 API ごとの個別ロール | bigquery.jobUser, tpu.admin, etc. |

---

## 11. 実測（TDnet Phase I, 2026-04-18）

| 指標 | 値 |
|------|-----|
| 1 execution の step 数 | ~15（2 parallel branches 含む） |
| 所要時間 | ~40分（うち Cloud Run Job 同期待機 ~35分） |
| Workflows コスト | ~$0（内部 step 5,000/月 無料枠内） |
| 成功率 | 1/1（Phase I 完遂テスト） |

---

## 12. 次ステップ gate 設計（Cloud Run exit code を単独で信じない）

### 落とし穴: `result.state == "SUCCEEDED"` だけで次段を発火

Cloud Run Job 側に「exit 0 嘘」（`004 A-1` — 例外を握って errors=1 にしても `sys.exit()` せず exit 0 で終わる）が潜んでいると、Workflows から見て `SUCCEEDED` で返ってくる。次ステップの起動条件を state だけで書いていると、**実データが入っていないまま下流が発火する**。

過去事故: 2026-04-20 TDnet ai-finalize 7bd8f — Phase 5 `NameError` で BQ INSERT ゼロ、コンテナは exit 0 → chain script が次 batch の gemma-runner を起動して誤検知連鎖（詳細 `013-2 #1 #3`）。

### 対策: 複合 gate

次ステップ発火条件を **state + 実測データ** の複合にする:

```yaml
- check_finalize:
    call: googleapis.bigquery.v2.jobs.query
    args:
      projectId: ${project}
      body:
        query: |
          SELECT COUNT(*) AS cnt
          FROM `${project}.STOCK.TDNET_DOCUMENTS_ENHANCED`
          WHERE SUBMISSION_DATE = @date
            AND AI_STATUS = 'completed'
        queryParameters:
          - name: date
            parameterType: {type: DATE}
            parameterValue: {value: ${target_date}}
    result: completed_rows

- gate:
    switch:
      - condition: ${int(completed_rows.rows[0].f[0].v) > 0}
        next: start_next_batch
      - condition: true
        raise: "ai-finalize SUCCEEDED but 0 completed rows — aborting chain"
```

他の gate 候補:
- **GCS `_SUCCESS` マーカー**: 上流 job が成功パスでのみ置く sentinel blob
- **出力 blob 数**: `bucket.list_blobs(prefix=output_prefix)` のカウント
- **next-input key set**: 次段の processing 対象が1件以上存在するか

### 連携

- 上流 Cloud Run Job 側: `004 A-1` で `sys.exit(1)` を徹底
- Workflows / chain script 側: 本節の複合 gate
- 片方だけでは不十分。両方揃えて初めて「嘘の成功」が検知できる

---

## 13. 参照

- 公式ドキュメント: https://cloud.google.com/workflows/docs
- parallel shared 変数: https://cloud.google.com/workflows/docs/samples/workflows-parallel-shared-variables
- connector_params: https://cloud.google.com/workflows/docs/reference/googleapis
- Callback endpoint: https://cloud.google.com/workflows/docs/creating-callback-endpoints
- **`004_coding_conventions.md §A-8`** — 上流 exit code だけに依存しない gate 設計（本節の汎用版）

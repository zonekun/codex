# Gemma TPU プリエンプション耐性改善

**作成日時**: 2026-05-04 20:53 JST
**対象ファイル**: `workflows/ai_processing_flow.yaml`（268行, commit 0d04fd8 時点）、`scripts/gemma_tpu_worker.py`（497行）、`scripts/gemma_tpu_runner.sh`（260行）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: Spot TPU プリエンプション発生時の自動復旧を強化し、手動介入なしでバックフィルを完走させる。スコープ: retry 回数 + cross-execution checkpoint。非スコープ: TPU ゾーンフェイルオーバー、オンデマンド TPU 切替

**分類**: (b) 継続改修型
**親知見 MD**: `docs/knowledges/tools/013_tdnet_load.md`

---

## 前提サマリ

- 既存実装: workflow `retry: max_retries: 3`（デプロイ済み）、worker.py に GCS checkpoint（`gemma_CURRENT.jsonl` 逐次 append + `done_ids` resume）
- 問題: 2021-H1 で Q1×4 exec + Q2a×2 exec = 計6回 FAILED、~13hロス（068事故レビュー参照）
- 実機検証: 本番（2021-H1 バックフィルで実際にプリエンプション耐性を検証済み。retry は発動したが回数不足で exhausted）
- 関連 incident: `docs/reviews/068_mr_backfill_monitor_false_claim.md`

---

## 優先度の定義

- **P0**: 2021-H2 バックフィル投入前に必須（手動介入ゼロで完走を担保）
- **P1**: 望ましいが H2 投入前必須ではない（現状でも手動で対処可能）
- **P2**: 余力で

---

## 指摘項目

### P0-1. cross-execution checkpoint が引き継がれない 🚨

**症状**: workflow execution が FAILED（retry exhausted）→ 手動再投入 → 新 execution ID → 新 GCS パス → 前回の推論結果が全消失。Q1（14,671 docs、推論 ~5h）で何度もゼロからやり直しになった

**該当**: `scripts/gemma_tpu_worker.py:L40-L53` / GCS パス定義

```python:L40-L53
RUN_ID = os.environ.get("RUN_ID")
if not RUN_ID:
    raise SystemExit("RUN_ID env var required")

BUCKET = os.environ.get("BUCKET_NAME", "stock_data_1930932")
CALLBACK_URL = os.environ.get("CALLBACK_URL") or ""
CONCURRENCY = int(os.environ.get("CONCURRENCY", "8"))
TEXT_LIMIT = int(os.environ.get("TEXT_LIMIT", "20000"))
PUSH_INTERVAL_SEC = 60
PUSH_TRIGGER_EVERY_N = 100

GCS_STATE_BLOB = f"ai_job/{RUN_ID}/state.json"
GCS_CURRENT_BLOB = f"ai_job/{RUN_ID}/gemma_CURRENT.jsonl"
GCS_SUCCESS_BLOB = f"ai_job/{RUN_ID}/_SUCCESS"
```

**根本原因**: GCS 出力パスが `ai_job/{RUN_ID}/` で、`RUN_ID` = workflow execution ID。retry 内（同一 execution）では同じ RUN_ID なので checkpoint 有効。しかし execution FAILED → 手動再投入 → 新 execution ID → 新パス → 既存 checkpoint 参照不可。アンチパターン B-1（成功確認なしの state 消失）の変形: state 自体は消えないが、新 execution がそれを見つけられない

**修正方針**: workflow input に `resume_run_id` パラメータを追加。指定時は旧 execution の checkpoint を新 execution のパスにコピーしてから処理開始

```yaml
# before (ai_processing_flow.yaml L28-L41)
    - init:
        assign:
          - run_id: ${sys.get_env("GOOGLE_CLOUD_WORKFLOW_EXECUTION_ID")}
          # ... (other vars)

# after
    - init:
        assign:
          - run_id: ${sys.get_env("GOOGLE_CLOUD_WORKFLOW_EXECUTION_ID")}
          - resume_run_id: ""
          # ... (other vars)

    # extract_ticker_range の直後（L66の後）に追加
    - extract_resume_run_id:
        switch:
          - condition: ${input != null and "resume_run_id" in input}
            assign:
              - resume_run_id: ${input.resume_run_id}
```

```bash
# before (gemma_tpu_runner.sh L29-L33)
: "${RUN_ID:?RUN_ID env required}"
PROJECT="${PROJECT:-gmailpj-357912}"
BUCKET="${BUCKET:-stock_data_1930932}"
CALLBACK_URL="${CALLBACK_URL:-}"
ZONE="${ZONE:-us-central1-b}"

# after
: "${RUN_ID:?RUN_ID env required}"
: "${RESUME_RUN_ID:-}"
PROJECT="${PROJECT:-gmailpj-357912}"
BUCKET="${BUCKET:-stock_data_1930932}"
CALLBACK_URL="${CALLBACK_URL:-}"
ZONE="${ZONE:-us-central1-b}"
```

```bash
# before (gemma_tpu_runner.sh L231-L239 WORKER_CMD)
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

# after
WORKER_CMD=$(cat <<REMOTE
set -e
pip install --quiet --user httpx 2>/dev/null || pip3 install --quiet --user httpx 2>/dev/null || true
cd /tmp
export RUN_ID='${RUN_ID}'
export RESUME_RUN_ID='${RESUME_RUN_ID}'
export BUCKET_NAME='${BUCKET}'
export CALLBACK_URL='${CALLBACK_URL}'
export CONCURRENCY=8
python3 -u /tmp/gemma_tpu_worker.py 2>&1 | tee /tmp/worker.log
REMOTE
)
```

```yaml
# before (run_gemma_runner step, env vars)
                                - env:
                                    - name: RUN_ID
                                      value: ${run_id}
                                    - name: BUCKET
                                      value: ${bucket}
                                    - name: CALLBACK_URL
                                      value: ${callback_details.url}

# after
                                - env:
                                    - name: RUN_ID
                                      value: ${run_id}
                                    - name: RESUME_RUN_ID
                                      value: ${resume_run_id}
                                    - name: BUCKET
                                      value: ${bucket}
                                    - name: CALLBACK_URL
                                      value: ${callback_details.url}
```

```python
# before (gemma_tpu_worker.py L37-L53)
RUN_ID = os.environ.get("RUN_ID")
# ...
GCS_CURRENT_BLOB = f"ai_job/{RUN_ID}/gemma_CURRENT.jsonl"

# after (gemma_tpu_worker.py)
RUN_ID = os.environ.get("RUN_ID")
RESUME_RUN_ID = os.environ.get("RESUME_RUN_ID") or ""
# ...
GCS_CURRENT_BLOB = f"ai_job/{RUN_ID}/gemma_CURRENT.jsonl"
# (resume ロジックは main() の冒頭に追加)
```

```python
# before (gemma_tpu_worker.py main() L437-L438)
    # 2. Resume: 既存 gemma_CURRENT.jsonl を取得 → 処理済 doc_id 集合化
    resume_lines = gcs_download_current(LOCAL_OUT)

# after
    # 2a. Cross-execution resume: 旧 execution の checkpoint を取得
    if RESUME_RUN_ID:
        resume_blob = f"ai_job/{RESUME_RUN_ID}/gemma_CURRENT.jsonl"
        resume_uri = f"gs://{BUCKET}/{resume_blob}"
        rc, _ = _gcs_cp(resume_uri, str(LOCAL_OUT))
        if rc == 0:
            print(f"[resume] copied checkpoint from {RESUME_RUN_ID}", flush=True)
            # 新 RUN_ID のパスにも push して以降の追記先を統一
            gcs_push_current(LOCAL_OUT)
        else:
            print(f"[resume] no checkpoint found for {RESUME_RUN_ID}, starting fresh", flush=True)

    # 2b. Resume: 既存 gemma_CURRENT.jsonl（新パスに push 済み or 元々存在）→ 処理済 doc_id 集合化
    resume_lines = gcs_download_current(LOCAL_OUT)
    # NOTE: RESUME_RUN_ID 使用時、2a で LOCAL_OUT に書き込み→push 済みなので
    # gcs_download_current は同一ファイルを再取得する（冗長だがコードパス統一のため許容）

    # 2c. 整合性チェック: resume_run_id 指定時に state.json の doc_id と checkpoint の
    # done_ids の重複率を検証。重複率 < 50% なら WARNING（誤った resume_run_id の疑い）
    if RESUME_RUN_ID and done_ids:
        state_doc_ids = {d.get("doc_id") for d in docs_all if d.get("doc_id")}
        overlap = done_ids & state_doc_ids
        overlap_ratio = len(overlap) / len(done_ids) if done_ids else 1.0
        if overlap_ratio < 0.5:
            print(f"[resume][WARNING] low overlap: {len(overlap)}/{len(done_ids)} "
                  f"({overlap_ratio:.1%}) — resume_run_id may be wrong", flush=True)
```

**呼び出し側への波及**:
- `workflows/ai_processing_flow.yaml:L28` — init に `resume_run_id: ""` 追加
- `workflows/ai_processing_flow.yaml:L66の後` — `extract_resume_run_id` ステップ追加（`extract_ticker_range` の直後）
- `workflows/ai_processing_flow.yaml:L199` — gemma-runner env に `RESUME_RUN_ID` 追加
- `scripts/gemma_tpu_runner.sh:L30` — `: "${RESUME_RUN_ID:-}"` 追加（env 宣言部）
- `scripts/gemma_tpu_runner.sh:L235` — WORKER_CMD に `export RESUME_RUN_ID='${RESUME_RUN_ID}'` 追加
- 呼び出しコマンド変化なし（`resume_run_id` はオプショナル。通常実行では影響ゼロ）

**検証**: 
1. `resume_run_id` なし（通常）→ 既存動作と同一であることを確認
2. `resume_run_id` に直前の FAILED execution ID を指定 → checkpoint が引き継がれ、done_ids がスキップされることを確認

**ロールバック**: workflow YAML を git revert + `gcloud workflows deploy` で旧版に戻す。worker.py は Docker rebuild + deploy。データ破壊なし（append-only 設計のため）

---

### P1-1. retry 回数が不十分 ⚠️

**症状**: `max_retries: 3`（計4回試行）では連続プリエンプション時に exhausted。Q1 は 1 execution 内の全 retry が失敗し、4 execution × ~4h = ~16h で計 12+ 回のプリエンプションを経験

**該当**: `workflows/ai_processing_flow.yaml:L207-L214` / retry ブロック

```yaml:L207-L214
                      retry:
                        # TPU spot preemption / 一時的失敗 を 3回まで retry
                        # worker.py は GCS gemma_CURRENT.jsonl から resume するので冪等
                        max_retries: 3
                        backoff:
                          initial_delay: 60
                          max_delay: 600
                          multiplier: 2
```

**根本原因**: Spot TPU は需給逼迫時に連続プリエンプトされる。max_retries: 3 + backoff 60-600s では、各 retry が ~1h（TPU作成 + vLLM setup + 推論開始まで）かかる上にすぐプリエンプトされると、4回で ~4h 消費して exhausted。アンチパターン D-1 の亜種（timeout/retry 上限が実態に合っていない）

**修正方針**: max_retries を 7 に増加（計8回試行）。backoff max_delay を 900s に延長（TPU quota 回復待ち）

**backoff シーケンス**（initial=60, multiplier=2, max=900）:

| retry # | backoff (s) | 累積待機 (s) |
|---------|-------------|-------------|
| 1 | 60 | 60 |
| 2 | 120 | 180 |
| 3 | 240 | 420 |
| 4 | 480 | 900 |
| 5 | 900 | 1800 |
| 6 | 900 | 2700 |
| 7 | 900 | 3600 |

最悪ケース合計待機: **3600s (1h)**。`connector_params.timeout: 14400` (4h) に対しマージン 3h。各 retry でジョブ実行 (~1h) が加わっても 8h < 14.4h で timeout 内に収まる

```yaml
# before
                      retry:
                        max_retries: 3
                        backoff:
                          initial_delay: 60
                          max_delay: 600
                          multiplier: 2

# after
                      retry:
                        max_retries: 7
                        backoff:
                          initial_delay: 60
                          max_delay: 900
                          multiplier: 2
```

**呼び出し側への波及**: 無し（retry 設定は workflow 内部。外部 I/F 変更なし）

**検証**: 通常時は 1 回で成功するため影響なし。プリエンプション時のみ追加 retry が発動。checkpoint と組み合わせれば、後半の retry は短時間で完了するはず

**ロールバック**: workflow YAML の数値変更のみ。revert + deploy で即戻し可能

---

### P1-2. デプロイ ⚠️

**症状**: コード変更だけではGCP上のリソースに反映されない

**該当**: デプロイ手順

**修正方針**: 

```bash
# 1. workflow deploy
gcloud workflows deploy ai_processing_flow \
  --location=us-central1 \
  --source=workflows/ai_processing_flow.yaml

# 2. gemma-runner container rebuild + deploy
# 参照: docs/knowledges/tools/078_gemma4_operation.md §デプロイ手順
gcloud builds submit --config cloudbuild/cloudbuild.tdnet-gemma-runner.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source .

# 3. Cloud Run Job 自動更新（cloudbuild で実施済みの場合不要）
```

**検証**: `gcloud workflows describe ai_processing_flow --location=us-central1 --format="value(updateTime)"` で更新確認

**ロールバック**: git revert → 再デプロイ

---

## 対応アンチパターン

| plan ID | 004 | T-x | G-x |
|---|---|---|---|
| P0-1 | B-1 (state 参照不能の変形) | — | — |
| P1-1 | D-1 (retry 上限が実態に不適合) | — | — |

---

## 検証戦略

1. **smoke test**: `resume_run_id` なしで小範囲（1日分）の workflow 実行 → 既存動作と同一であることを確認。`resume_run_id` 付きで空の旧 execution ID を指定 → エラーなく fallback して正常動作
2. **dev 実機**: 2021-H2 Q3（~14,365 docs）を 1 四半期分実行。P0-1 の検証は、成功した execution の ID を `resume_run_id` に指定して新 execution を実行 → done_ids で全スキップ → 即完了を確認
3. **本番適用判断基準**: smoke + dev の両方で以下が PASS:
   - `resume_run_id` なし: 既存動作と差異なし
   - `resume_run_id` あり: checkpoint 引継ぎ + done_ids スキップが動作
   - retry 増加: 通常実行時に余計な retry が発動しないこと
4. **回収手順**: workflow YAML revert + deploy + Docker rebuild で即戻し。データは append-only のため破壊なし。万一不正な推論結果が混入した場合は `AI_STATUS='pending_gemma'` に戻して再実行

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/tools/013_tdnet_load.md`（親知見、TDnet ETL 全体）
- 知見 MD: `docs/knowledges/tools/078_gemma4_operation.md`（Gemma TPU 運用ノウハウ）
- 知見 MD: `docs/knowledges/tools/087_backfill_execution_metrics.md`（バックフィル実測値）
- バックフィルプラン: `docs/plans/20260427_140000_tdnet_2017_2022_backfill.md` §改善事項
- 関連 incident: `docs/reviews/068_mr_backfill_monitor_false_claim.md`（監視虚偽報告 + 2重実行事故）
- 関連 commit: `97e2365` — workflow に recent_only + 2020-H2 対応追加（retry は既存）
- フォーマット正本: `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット

---

## 提出前セルフチェック（必須）

- [x] 冒頭に基準 commit hash があるか
- [x] 全項目が 7 フィールド（症状/該当/根本原因/修正方針/呼び出し側波及/検証/ロールバック）を揃えているか
- [x] 修正方針に before/after の両方があるか
- [x] 呼び出し側への波及が行番号リストで明示されているか（「影響あり」等の曖昧表現は不可）
- [x] 対応アンチパターン表が末尾にあるか（該当なしでも「該当なし」テーブルを明示）
- [x] 検証戦略が smoke / dev / 本番適用判断基準 / 回収手順の 4 段を網羅しているか
- [x] ロールバック手順があるか（破壊的修正時は必須）
- [x] 「既に〜がある」系の前提を実コードで Read 確認したか

---

## レビュー追記: 2026-05-05 07:37 JST — code-reviewer

→ `docs/reviews/070_cr_gemma_preemption_resilience.md`

# レビュー依頼: TDnet パイプライン運用事故 2件（2026-05-07）

- レビューパターン: パターン 1（新規レビュー）— 運用事故の改善案提出
- 対象ファイル:
  - `workflows/tdnet_daily_pipeline.yaml` — パイプライン Workflows 定義
  - `workflows/ai_processing_flow.yaml` — AI処理 Workflows 定義
  - `scripts/tdnet_load_parallel.py` — Load / AI-prepare / AI-finalize 共通スクリプト
  - `docs/knowledges/tools/013_tdnet_load.md` — TDnet ETL 知見ファイル

## 事故概要

### 事故1: tdnet-daily-pipeline の trigger_ai タイムアウト

- **日時**: 2026-05-07 20:03〜20:40 JST
- **状況**: 決算特別スケジュール期間中。パイプライン（DL→Load→AI）が 20:03 JST に起動
- **症状**: trigger_ai ステップが 1800秒タイムアウトで FAILED
- **原因**: 同時刻に 2022年バックフィル用の `tdnet-load-daily` ジョブ（DATE_FROM=20220101, DATE_TO=20220630）が 6時間タイムアウトまで走っており、パイプラインの Load ステップが完了を待ちきれなかった
- **影響**: 5/7 の日次 DL→Load→AI チェーンが不完全に終了

### 事故2: ai_processing_flow が 5/7 分をカバーしなかった

- **日時**: 2026-05-07 20:10〜21:03 JST
- **状況**: `ai_processing_flow` が `recent_only: true` で SUCCEEDED
- **症状**: 処理結果は `date_from=20260401, date_to=20260430`（4月分）。5/7 の pending 275行（199銘柄）は未処理のまま
- **原因推定**: `resolve_date_range` ステップが「直近14日以内の最古 pending 月」を選択する仕様で、4月分の pending が残っていたため4月が優先された。5/7 分は「5月の pending」として後回しになった
- **影響**: 5/7 投入分 275行が AI_STATUS='pending' のまま残存。手動で再実行するまで AI 判定が完了しない

## 改善案の提出を求める観点

1. **バックフィルと日次パイプラインの競合防止**: バックフィル実行中に日次パイプラインが走った場合のガード・排他制御
2. **ai_processing_flow の日付選択ロジック**: `recent_only` モードで当日分が後回しになる問題の改善
3. **パイプライン失敗時の自動リカバリ**: trigger_ai タイムアウト後の pending 自動回収メカニズム
4. **監視・アラート**: パイプライン失敗やpending滞留の検知

## 補足

- 5/7分の AI 処理は手動で `ai_processing_flow` を再投入済み（execution: 33577166-aceb-40d2-878a-bdd67becb7dc）
- バックフィル（2022年H1）は意図的な実行であり、Load ジョブ自体は問題ない
- 問題は「バックフィルと日次パイプラインの共存設計」にある

---

# コードレビュー: TDnet パイプライン運用事故 — 競合防止・日付選択・自動リカバリ改善

- 日時: 2026-05-08 00:36 JST
- 対象: `workflows/tdnet_daily_pipeline.yaml`, `workflows/ai_processing_flow.yaml`, `scripts/tdnet_load_parallel.py`, `docs/knowledges/tools/013_tdnet_load.md`
- パターン: 1 (新規)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: 2026-05-07 に発生した2件の運用事故（パイプライン trigger_ai タイムアウト + ai_processing_flow 日付選択による当日分スキップ）に対する改善案の分析・提出
- 品質評価: B — 日次パイプラインと AI 処理フローの基本設計は堅実だが、バックフィルとの共存設計に構造的な隙がある。事故1は connector_params.timeout の事前修正で既に対処済み（43200s）だが、事故2の `resolve_date_range` ロジックは根本的な設計課題を残している
- 主要リスク:
  1. `resolve_date_range` の「最古 pending 月」選択が、月跨ぎ投入で当日分を無期限に後回しにする
  2. Cloud Run Job `tdnet-load-daily` がバックフィルと日次で共有されており、同時実行時に Workflows の LRO polling が前の execution を誤追跡する可能性
  3. パイプライン失敗時の pending 滞留を検知・自動回収する仕組みが存在しない

---

## 【重大な指摘】（即修正）

### #1 `resolve_date_range` の「最古 pending 月」選択が当日分をスキップする構造的欠陥

- 箇所: `workflows/ai_processing_flow.yaml:86-100`
- 事象: `recent_only: true` のクエリ（L92-99）は `MIN(SUBMISSION_DATE)` で直近14日以内の最古 pending を探し、その月全体を処理対象とする。4月の pending が1件でも残っていれば `dfrom=20260401, dto=20260430` が返り、5月の pending は完全にスキップされる。
- トリガー: 以下の全てが同時に成立するとき発火する:
  - (a) 月初〜14日以内（5/7 など）に日次パイプラインが走る
  - (b) 前月（4月）に `AI_STATUS='pending'` の行が残存している（リカバリ漏れ、ai-finalize 部分失敗、text 取得失敗の pending_* 保持 etc.）
  - (c) `recent_only: true` で `ai_processing_flow` が起動される
- 影響: 当日投入分 275 行が `AI_STATUS='pending'` のまま滞留し、手動再投入まで AI 判定が完了しない。決算繁忙期に発生すると即日の分析・判定に致命的な遅延が生じる
- 根拠: SQL の `MIN(SUBMISSION_DATE)` は全 pending 行から最古の1件を選び、その月全体を範囲とする。月を跨ぐ pending が存在すると、新しい月は1回の実行では到達不能。Workflows 内にループ機構がないため、1実行=1月分しか処理できない
- 推奨対応: `resolve_date_range` を「最古 pending 月」ではなく「当日を含む直近 pending 全体」に変更する。具体的には以下の2案のいずれか:
  - **案A（推奨）**: `recent_only: true` 時のクエリを月単位でなく日単位に変更し、`SUBMISSION_DATE >= CURRENT_DATE() - 14` の pending 全件を対象にする。`dfrom` = 最古 pending 日、`dto` = 最新 pending 日（月単位の TRUNC/LAST_DAY を廃止）。ai-prepare/ai-finalize は日付範囲ベースで動くため、月跨ぎでも問題ない
  - **案B**: Workflows 内にループを設け、`no_pending` が返るまで `resolve_date_range → run_ai_prepare → ... → run_ai_finalize` を繰り返す（月単位を維持しつつ複数月を処理）。ただし TPU 起動オーバーヘッドが月数分かかるため案A が効率的

### #2 Cloud Run Job `tdnet-load-daily` のバックフィル/日次同時実行による競合

- 箇所: `workflows/tdnet_daily_pipeline.yaml:41-52`（run_load ステップ）
- 事象: `run_load` は `googleapis.run.v2.projects.locations.jobs.run` で `tdnet-load-daily` を起動する。Cloud Run Jobs は同一 Job に対して複数の execution を同時に起動できるが、Workflows の LRO polling コネクタは `run` の戻り値（operation ID）で特定の execution を追跡する。バックフィルが先に走っている場合、日次パイプラインの `run_load` は**新しい execution** を起動して**その execution** の完了を待つ。つまり Cloud Run Job 側の execution 自体は分離されている。
- トリガー: 2022 バックフィル（6h タイムアウト）と日次パイプライン（20:03 JST）が同時に走る。事故1の直接原因は `trigger_ai` の LRO timeout が旧デフォルト 1800s だったこと（修正済み: 現在 43200s）。
- 影響: 事故1の直接原因（1800s タイムアウト）は既に修正済み（`connector_params.timeout: 43200`）。ただし、Cloud Run Job の同時実行には以下の残存リスクがある:
  - (a) `tdnet-load-daily` の Cloud Run Job 自体の `task-timeout` がバックフィル用に 21600s に拡大されている場合、日次 Load（通常数分）も同じ長い timeout を継承する（Job レベルの設定は全 execution に適用される）
  - (b) バックフィル Load が大量の GCS blob を走査中に、日次 Load が同じ BQ テーブルに streaming insert / Load Job を発行し、データ面での競合はないが BQ Load Job のリソース消費は増加する
- 根拠: Cloud Run Jobs の `googleapis.run.v2.projects.locations.jobs.run` は呼び出しごとに新しい execution を生成する（execution ID は異なる）。Workflows の LRO polling はその execution を追跡するため、バックフィル execution とは直接衝突しない。事故1の本質は LRO timeout が不足していたことであり、既に修正済み
- 推奨対応:
  - (a) **日次専用の Load ジョブを分離する**（`tdnet-load-daily-pipeline` 等の別 Job を作成）。バックフィルと日次で `task-timeout` や `--update-env-vars` を独立管理できる。ただし Docker イメージの同期が二重管理になるトレードオフがある
  - (b) **パイプライン冒頭にバックフィル実行中チェックを追加する**: `run_load` の前に BQ で `SELECT COUNT(*) FROM ... WHERE AI_STATUS='pending' AND SUBMISSION_DATE < CURRENT_DATE() - 30` を実行し、大量の古い pending が存在する場合はバックフィル実行中と判断してパイプライン自体を遅延/スキップする（Workflows の `sys.sleep` + retry で実現可能）
  - (c) **最小限**: 013 知見ファイルの「決算特別スケジュール」セクションに「**決算特別スケジュール期間中のバックフィル実行は、日次パイプラインの Load ステップと Cloud Run Job を共有するため、パイプライン起動時刻（20:03 JST）前にバックフィル Load が完了する見込みがない場合は投入を避ける**」を明文化する。これは対症療法だが即座に実施可能

### #3 パイプライン失敗後の pending 自動回収メカニズムの不在

- 箇所: `workflows/tdnet_daily_pipeline.yaml:57-65`（trigger_ai ステップ）、`workflows/ai_processing_flow.yaml` 全体
- 事象: `trigger_ai` がタイムアウトや失敗で FAILED になった場合、日次パイプラインは `done` に到達せず Workflows execution 全体が FAILED で終了する。失敗した実行の pending は翌週土曜の `tdnet-ai-weekly`（7:00 JST）まで放置される。決算繁忙期は日次投入が連続するため、月曜の失敗分が土曜まで待つのは許容できない
- トリガー: trigger_ai タイムアウト（修正済みだが他の原因でも発生しうる: ai-prepare OOM, TPU preemption 8回超, Gemma FAILED, ai-finalize 6h 超過等）
- 影響: pending が 5-6 日滞留し、AI 判定のタイムリネスが大幅に劣化する
- 根拠: `tdnet_daily_pipeline.yaml` に retry / catch / 失敗時フォールバックの定義がない。`trigger_ai` の `connector_params.timeout: 43200` は十分長いが、子 Workflows（`ai_processing_flow`）内部の失敗（TPU 8 回 preempt 等）では `trigger_ai` 自体が FAILED を返す
- 推奨対応:
  - **案A（推奨）**: `tdnet_daily_pipeline.yaml` の `trigger_ai` に `try/except` を追加し、失敗時に `ai_processing_flow` を `date_from` / `date_to` を明示指定して再起動する。或いは、翌日のパイプライン実行時に前日の pending を含むよう `run_load` 後に pending 件数をチェックして `trigger_ai` に渡す日付範囲を動的に決定する
  - **案B**: Cloud Scheduler に `tdnet-ai-daily-catchup` を追加（毎日 06:00 JST）。`recent_only: true` で `ai_processing_flow` を起動し、前日までの取りこぼしを回収する。週次（土 7:00）と日次（6:00）の二段構え。コスト増は TPU 空振り時 $0（`check_pending_gemma` ガードで TPU 起動回避）
  - **案C**: `tdnet_daily_pipeline.yaml` の `trigger_ai` 失敗時にのみ ntfy 通知を送信し、手動対応を促す（`sys.get_env("GOOGLE_CLOUD_WORKFLOW_EXECUTION_ID")` + HTTP POST to ntfy）。最もシンプルだが自動化度は最低

### #4 `resolve_date_range` が `recent_only: true` でも月単位の TRUNC を適用する設計の不整合

- 箇所: `workflows/ai_processing_flow.yaml:93-96`（find_recent_pending クエリ）
- 事象: `recent_only: true` のクエリは `SUBMISSION_DATE >= CURRENT_DATE() - 14` でフィルタした後、`DATE_TRUNC(MIN(SUBMISSION_DATE), MONTH)` で月初に切り詰める。これにより、14日ガードの意図（「直近のみ」）と矛盾する挙動が発生する。例: 5/7 実行で 4/24 に pending がある場合、14日以内の条件（`>= 4/23`）を満たすため `dfrom=20260401` になり、4月全体が対象になる。4/1〜4/22 の pending_gemma 行（ai-prepare 済み）も再度 ai-prepare の対象となる可能性がある（ただし `_load_pending_docs_from_bq` が `AI_STATUS='pending'` のみを取得するため実害は限定的）
- トリガー: 月初〜14日目にパイプラインが実行され、前月最終14日以内に pending が存在するとき
- 影響: 意図した「直近14日分」ではなく「直近14日以内に pending がある月の全体」が処理対象になる。処理量の増加（BQ スキャン範囲の拡大）と #1 のスキップ問題を引き起こす
- 根拠: `DATE_TRUNC(..., MONTH)` が月初に、`LAST_DAY(...)` が月末に丸めるため、14日ガードの精密さが月単位の粗さで上書きされる
- 推奨対応: #1 の案A と統合して解決する。`recent_only: true` 時は `DATE_TRUNC` / `LAST_DAY` を廃止し、`MIN(SUBMISSION_DATE)` / `MAX(SUBMISSION_DATE)` を直接 `dfrom` / `dto` とする。ai-prepare / ai-finalize は日付範囲の BETWEEN で処理するため、月境界に依存しない

---

## 【改善提案】（可読性・保守性）

### #1 `tdnet_daily_pipeline.yaml` に失敗通知ステップを追加

- 箇所: `workflows/tdnet_daily_pipeline.yaml:57-65`
- 現状: `trigger_ai` が FAILED になった場合、Workflows execution が FAILED で終了するのみ。Cloud Logging には記録されるが、能動的な通知はない
- 提案: `trigger_ai` を `try/except` で囲み、except ブロックで HTTP POST による ntfy 通知（`scripts/notify.py` 相当の機能を Workflows の HTTP call で直接実現）を送信する。パイプライン全体の成否を即時に把握できるようにする。記載先: `013_tdnet_load.md` の決算特別スケジュール §関連リソース に通知設定の有無を記載

### #2 013 知見ファイルにバックフィル/日次競合リスクのセクションを新設

- 箇所: `docs/knowledges/tools/013_tdnet_load.md` — 「バックフィル運用」セクション付近
- 現状: バックフィル投入手順・監視ツールの記述はあるが、決算特別スケジュール中のバックフィル実行に関する注意・制約が明文化されていない
- 提案: 「バックフィル運用」セクションに以下のサブセクションを追加:
  ```
  ### バックフィルと日次パイプラインの共存制約
  - `tdnet-load-daily` は日次パイプラインとバックフィルで共有
  - 決算特別スケジュール期間中（パイプライン 20:03 JST 起動）のバックフィル Load は 19:00 JST 前に完了が見込めない場合は投入を避ける
  - ai_processing_flow の resolve_date_range は月単位選択のため、前月 pending が残っていると当月分がスキップされる
  - バックフィル AI は `date_from`/`date_to` 明示指定で投入し、resolve_date_range を経由しない
  ```
  記載先: `docs/knowledges/tools/013_tdnet_load.md` §バックフィル運用

### #3 `ai_processing_flow` に pending 残存チェック + 複数月ループを検討

- 箇所: `workflows/ai_processing_flow.yaml:78-128`
- 現状: 1回の実行で1月分（or 1日付範囲）のみ処理
- 提案: `run_ai_finalize` 完了後に `resolve_date_range` に戻るループを追加し、`no_pending` が返るまで繰り返す。ただし TPU 起動コスト（$1/回）との兼ね合いで、ループ回数上限（例: 3回）を設ける。#1 の案A（日単位化）が実装されればループは不要になるため、優先度は案A > ループ

### #4 `tdnet_daily_pipeline.yaml` の `run_load` ステップで DATE_MODE を環境変数で明示渡しする設計の明文化

- 箇所: `workflows/tdnet_daily_pipeline.yaml:46-51`
- 現状: `DATE_MODE: "t"` が env で渡されており正しく動作している。ただし、バックフィルが `DATE_FROM`/`DATE_TO` 環境変数で同じ Job を起動する場合、Job のデフォルト環境変数とパイプラインの override の優先順位がコード上明確でない
- 提案: `013_tdnet_load.md` の「決算特別スケジュール §関連リソース」に「`run_load` は `DATE_MODE=t` で当日分のみ。バックフィルは `DATE_FROM`/`DATE_TO` で range 指定。両方同時に走っても BQ テーブルの書き込み範囲は重複しない（dedup キー `DOC_ID` で保護）」を追記する

---

## 【修正例】（必要な箇所のみ）

#### #1 に対する修正案 — `resolve_date_range` の日単位化

```yaml
# before: workflows/ai_processing_flow.yaml:86-100
                    - condition: ${recent_only}
                      steps:
                        - find_recent_pending:
                            call: googleapis.bigquery.v2.jobs.query
                            args:
                              projectId: ${project_id}
                              body:
                                useLegacySql: false
                                query: |
                                  SELECT
                                    FORMAT_DATE('%Y%m%d', DATE_TRUNC(MIN(SUBMISSION_DATE), MONTH)) AS dfrom,
                                    FORMAT_DATE('%Y%m%d',
                                      LAST_DAY(DATE_TRUNC(MIN(SUBMISSION_DATE), MONTH))) AS dto
                                  FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
                                  WHERE AI_STATUS = 'pending'
                                    AND SUBMISSION_DATE >= CURRENT_DATE() - 14

# after
                    - condition: ${recent_only}
                      steps:
                        - find_recent_pending:
                            call: googleapis.bigquery.v2.jobs.query
                            args:
                              projectId: ${project_id}
                              body:
                                useLegacySql: false
                                query: |
                                  SELECT
                                    FORMAT_DATE('%Y%m%d', MIN(SUBMISSION_DATE)) AS dfrom,
                                    FORMAT_DATE('%Y%m%d', MAX(SUBMISSION_DATE)) AS dto
                                  FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
                                  WHERE AI_STATUS = 'pending'
                                    AND SUBMISSION_DATE >= CURRENT_DATE() - 14
```

**波及確認**: `ai-prepare` の `_load_pending_docs_from_bq` (L1551-1610) は `SUBMISSION_DATE BETWEEN` で日付範囲フィルタする。月跨ぎの日付範囲（例: `20260424`〜`20260507`）でも正常に動作する。`ai-finalize` の `_delete_pending_gemma_rows` (L1908-1942) も `SUBMISSION_DATE BETWEEN` で partition prune するため月跨ぎに対応済み。Gemma runner / worker は日付範囲に依存しない（run_id ベース）。

#### #3 に対する修正案 — trigger_ai 失敗時フォールバック

```yaml
# before: workflows/tdnet_daily_pipeline.yaml:57-65
    - trigger_ai:
        call: googleapis.workflowexecutions.v1.projects.locations.workflows.executions.create
        args:
          parent: ${"projects/" + project_id + "/locations/" + ai_workflow_location + "/workflows/ai_processing_flow"}
          connector_params:
            timeout: 43200
          body:
            argument: '{"recent_only": true}'
        result: ai_execution

# after
    - trigger_ai:
        try:
          call: googleapis.workflowexecutions.v1.projects.locations.workflows.executions.create
          args:
            parent: ${"projects/" + project_id + "/locations/" + ai_workflow_location + "/workflows/ai_processing_flow"}
            connector_params:
              timeout: 43200
            body:
              argument: '{"recent_only": true}'
          result: ai_execution
        except:
          as: ai_error
          steps:
            - notify_ai_failure:
                call: http.post
                args:
                  url: "https://ntfy.sh/stock-agent-notify"
                  headers:
                    Title: "ATP: tdnet-daily-pipeline trigger_ai FAILED"
                  body:
                    topic: "stock-agent-notify"
                    message: ${"trigger_ai failed: " + ai_error.message}
            - set_ai_failed:
                assign:
                  - ai_execution: null
```

---

## 【確認できなかった事項】

- Cloud Run Jobs の `tdnet-load-daily` で、日次パイプラインの execution とバックフィルの execution が同時に起動された場合の BQ Load Job の挙動。理論上は別 execution が別の Load Job を発行するため衝突しないが、同一テーブルへの同時 Load Job 投入でスロットリングが発生するかは実測未確認
- 事故1で trigger_ai が 1800s タイムアウトと報告されているが、現行 YAML は 43200s に修正済み。事故発生時点の YAML バージョンが旧版（デフォルト 1800s）だったのか、それとも別の原因で 1800s になったのかはgit history を確認する必要がある
- `resolve_date_range` の日単位化（#1 案A）を適用した場合、ai-prepare が 4/24〜5/7 のような長日付範囲で state.json を生成し、それが Gemma runner / ai-finalize で正常に処理されるかの実測。コード上は日付範囲に依存しない設計だが、state.json のサイズ増加（2週間分の pending が混在）による OOM リスクは未検証
- `tdnet-ai-weekly`（土 7:00）が事故2のような pending 滞留をカバーできるかの確認。weekly scheduler は `recent_only: true` で起動するため、#1 と同じ「最古月スキップ」問題が weekly にも波及する

---

## 【採用/見送り判定】（2026-05-08）

| # | 種別 | 内容 | 判定 | 理由 |
|---|------|------|------|------|
| 重大#1 | 重大 | `resolve_date_range` 日単位化 | **採用済** | ai_processing_flow.yaml を MIN/MAX に変更・デプロイ完了 |
| 重大#2 | 重大 | Cloud Run Job バックフィル/日次競合 | **採用済** | 013知見ファイルに共存制約セクション新設 |
| 重大#3 | 重大 | パイプライン失敗後の pending 自動回収 | **見送り** | 週次AI + 日単位化で実用上十分 |
| 重大#4 | 重大 | `resolve_date_range` の月単位TRUNC不整合 | **採用済** | 重大#1と統合して解決 |
| 改善#1 | 改善 | 失敗通知ステップ追加 | **見送り** | |
| 改善#2 | 改善 | 013に競合リスクセクション新設 | **採用済** | 重大#2と統合 |
| 改善#3 | 改善 | 複数月ループ検討 | **見送り** | 日単位化で不要 |
| 改善#4 | 改善 | DATE_MODE明示渡しの明文化 | **見送り** | |

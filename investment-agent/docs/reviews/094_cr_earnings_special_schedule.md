# コードレビュー: 決算特別スケジュール導入 + 2022バックフィル分割 + 監視改善

- 日時: 2026-05-07 21:15 JST
- 対象: commit `69ddcec` — workflows/tdnet_daily_pipeline.yaml, workflows/tdnet_schedule_revert.yaml, config/backfill/2022_full_run1_h1.yaml, docs/knowledges/tools/013_tdnet_load.md, docs/knowledges/tools/093_monitoring_obligation.md, docs/knowledges/tools/013-2_monitor_backfill.md, CLAUDE.md, docs/knowledges/INDEX.md
- パターン: 1 (新規)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: 決算繁忙期に通常の個別スケジューラ（DL/Load分離）をチェーンパイプライン（DL→Load→AI直列）に切り替えるCloud Workflows 2本を新規作成。併せて2022-H1バックフィルconfigを4分割に改修し、監視義務MDにタイムアウト予防チェックを追加。
- 品質評価: **B** — 全体的に実用的だが、trigger_aiステップの非同期実行（子Workflow完了を待たない）が最大の構造的リスク。復帰Workflowの冪等性・安全性は良好。
- 主要リスク:
  1. `tdnet_daily_pipeline.yaml` の trigger_ai ステップが子Workflow完了前に「succeeded」を返す可能性
  2. `tdnet_schedule_revert.yaml` の復帰スケジューラが毎年発火する構造（one-shot設計だが cron 式が年指定を持たない）
  3. `tdnet_daily_pipeline.yaml` の run_download ステップに環境変数オーバーライドがなく、日付制御が暗黙的

---

## 【重大な指摘】（即修正）

### #1 trigger_ai が子Workflow完了を待たずに done ステップへ進む可能性

- 箇所: `workflows/tdnet_daily_pipeline.yaml:57-65`
- 事象: `googleapis.workflowexecutions.v1.projects.locations.workflows.executions.create` は Workflow execution を**作成**するAPI。Cloud Run Job の `googleapis.run.v2.projects.locations.jobs.run`（LROコネクタ）とは異なり、execution 作成直後に HTTP 201 で応答し、子Workflowの完了を自動的にポーリングしない。`connector_params.timeout: 43200` は HTTP リクエスト自体のタイムアウトであり、LRO polling timeout ではない。
- トリガー: 毎回の日次パイプライン実行時。trigger_ai は即座に完了し、done ステップが `status: "succeeded"` を返すが、ai_processing_flow はまだ実行中。
- 影響: パイプライン上は「全完了」だが AI 処理は非同期で進行中。パイプライン自体の成否判定が意味をなさない。ただし AI 処理自体は独立して動くため**データ欠損は起きない**。問題は「パイプライン全体の成否」という情報が不正確になること。
- 根拠: Cloud Workflows の connector は `googleapis.run.v2.projects.locations.jobs.run` では内部で `operations.get` を自動ポーリングするが（LROパターン）、`workflowexecutions.v1...executions.create` は standard REST call であり LRO パターンではない。Google 公式ドキュメント: "The connector automatically polls long-running operations."は LRO API にのみ適用。
- 推奨対応: 2つの選択肢がある:
  - (A) **`executions.create` → `executions.get` ポーリングループ追加**: 子Workflow完了を明示的に待つ。最も正確だが YAML が複雑化。
  - (B) **done ステップの return に `ai: "triggered_async"` と明示**: 同期待ちが不要な運用判断なら、少なくとも戻り値で非同期であることを明示し、「completed」と嘘を書かない。
  - 注: 運用実績（2026-05-07初回: DL+Load+AI=合計59min）を見る限り AI は正常完了しているため、データ面の実害は無い。ただし将来的に「パイプラインSUCCEEDED = AI完了」の前提でアラートや後続処理を組む場合に問題になる。

### #2 tdnet_daily_pipeline の run_download に overrides（環境変数）がない

- 箇所: `workflows/tdnet_daily_pipeline.yaml:29-35`
- 事象: `run_download` ステップには `body.overrides.containerOverrides` がなく、`tdnet-download` Cloud Run Job のデフォルト環境変数に依存している。`run_load` ステップでは `DATE_MODE=t` を明示的に渡しており、設計の非対称性がある。
- トリガー: `tdnet-download` Job のデフォルト設定が変更された場合（例: デバッグ目的で DATE 範囲を限定した後にリセットし忘れ）。
- 影響: DL対象期間が想定外になり、Load ステップで当日分が欠損する可能性。
- 根拠: `run_load` では `DATE_MODE=t` を明示渡ししている点と対比して、DLステップに同様の明示性がない。
- 推奨対応: `tdnet-download` Job が「引数なしで当日分をDLする」設計であることを確認の上、問題なければコメントで明示。引数制御が必要なら `containerOverrides` で日付を渡す。

### #3 run_download の connector_params.timeout が短い

- 箇所: `workflows/tdnet_daily_pipeline.yaml:33-34`
- 事象: `connector_params.timeout: 7200`（2h）だが、ヘッダコメントに「DL ~30min」とある。2hは通常運用では十分だが、TDnetサーバーの遅延や決算集中日の大量開示時にDLが長引く可能性がある。一方 `run_load` は `timeout: 21600`（6h）を設定。
- トリガー: 決算集中日（例: 5月上旬の一斉開示日）にTDnetサーバーが輻輳し、DLに2h以上かかった場合。
- 影響: Workflow timeout エラーでパイプライン中断。Load/AI は起動されない。
- 根拠: DL所要時間は通常30分だが、繁忙期限定スケジュールの運用目的上、繁忙期特有のDL遅延こそ想定すべき。
- 推奨対応: `timeout: 7200` → `timeout: 10800`（3h）程度に拡大。過剰に見えるが繁忙期用途なので安全マージンは多めがよい。

---

## 【改善提案】（可読性・保守性）

### #1 tdnet_schedule_revert.yaml のスケジューラ名 typo リスク

- 箇所: `workflows/tdnet_schedule_revert.yaml:34`
- 現状: `tdnet-load-daily-daily` というスケジューラ名を使用している。これは実在する名前だが（`034_data_load_jobs.md` L22で確認）、`daily` が2回続く命名は人為的なtypoに見えやすく、将来の手動操作時に `tdnet-load-daily` と取り違えるリスクがある。
- 提案: リネームは既存システムへの影響が大きいため現状維持でよいが、復帰Workflow内にコメントで「スケジューラ名は `tdnet-load-daily-daily`（daily が2回で正しい）」と注記を追加すると安全。

### #2 tdnet_daily_pipeline.yaml のヘッダコメントに記載のスケジュール時刻が不正確

- 箇所: `workflows/tdnet_daily_pipeline.yaml:11`
- 現状: コメントに `20:00 JST Mon-Fri` と記載されているが、013_tdnet_load.md の関連リソース表（L73）では `月〜金 20:03 JST` と記載。現況サマリ（L98）でも 20:03 JST。
- 提案: コメントを `20:03 JST Mon-Fri` に修正して整合させる。

### #3 013_tdnet_load.md の自動復帰スケジューラ作成コマンドに cron 年指定がない

- 箇所: `docs/knowledges/tools/013_tdnet_load.md:42`
- 現状: `--schedule="0 0 <翌営業日> <月> *"` の最終フィールドが `*`（曜日ワイルドカード）。Cloud Scheduler の cron 式は年フィールドを持たないため、翌年の同月同日にも発火する。
- 提案: 既に手順内で「復帰後、使い終わった復帰スケジューラを削除」（L65）と明記されているため、運用で回避可能。しかし削除忘れのリスクがある。対策案:
  - (a) 復帰Workflowの最後に自分のSchedulerを削除するステップを追加（自動化）
  - (b) 013_tdnet_load.md に「復帰後の削除を忘れると翌年同日に誤発火する」警告を赤字で追記
  - (c) 復帰Workflow実行後に `pause` ステップを自身に対して追加（冪等な安全弁）

### #4 tdnet_schedule_revert.yaml の冪等性確認

- 箇所: `workflows/tdnet_schedule_revert.yaml:19-35`
- 現状: PAUSE済みのジョブを PAUSE、RESUME済みのジョブを RESUME する場合の挙動が未確認。Cloud Scheduler API は同一状態への遷移をエラーにしない（冪等）であるが、明示的にこの前提をコメントに記録すべき。
- 提案: YAML冒頭のコメントに「冪等: 既にPAUSE/RESUME済みのジョブに対しても安全に実行可能」と明記。

### #5 2022_full_run1_h1.yaml の日付レンジの隙間確認

- 箇所: `config/backfill/2022_full_run1_h1.yaml:8-31`
- 現状: 4分割の日付レンジ: 0101-0209 / 0210-0331 / 0401-0512 / 0513-0630。隙間なく連続しており問題なし。ただし分割境界がWorkflows側の分割（L38-55: 同じ4レンジ）と一致しており、設計対称性ルール（013-2_monitor_backfill.md の新規追加ルール）に適合。
- 提案: なし（良好な設計）。

### #6 093_monitoring_obligation.md の新セクション配置

- 箇所: `docs/knowledges/tools/093_monitoring_obligation.md:63-72`
- 現状: 「タイムアウト予防チェック」セクションが「ジョブの進捗確認手法」内のサブセクションとして追加されている。一方「定期チェック時の必須4点確認」（L74-82）は独立セクション。両方が相互参照しており、構造は適切。
- 提案: 4点確認の項目4がタイムアウト予防を参照する循環参照になっているが、上位→下位の参照であり問題ない。

---

## 【確認できなかった事項】

1. **`googleapis.workflowexecutions.v1.projects.locations.workflows.executions.create` の実際のLRO挙動**: Google Cloud Workflows の connector がこのAPIをLROとして扱うかどうかは、実行時のconnectorバージョンに依存する可能性がある。2026-05-07の運用実績で「DL+Load+AI=合計59min」との記載があり、パイプラインが全ステップ完了を待った実績がある場合は #1 の指摘は誤りとなる。ただし「パイプラインは先に完了し、AIは非同期で59min後に完了した」可能性も排除できない。**実際のWorkflow execution ログで trigger_ai ステップの開始〜完了時刻を確認することで判定可能**。
2. **Cloud Scheduler の pause/resume API の冪等性**: 公式ドキュメントでは「既にPAUSED状態のジョブをpauseしてもエラーにならない」と記載があるが、バージョンによる挙動変更の可能性は排除できない。
3. **tdnet-download Cloud Run Job のデフォルト環境変数**: Job定義を直接確認していないため、引数なしで当日分をDLする動作が保証されているかは未検証。
4. **復帰スケジューラ（tdnet-schedule-revert-YYYYMMDD）の認証設定**: 013_tdnet_load.md L46-47 で `bq-loader` SAと `cloud-platform` scopeを使用。このSAが `workflows.executions.create` 権限を持つかは未検証（`workflows.invoker` ロールが必要）。

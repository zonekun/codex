# 投資AIエージェント - CLAUDE.md

## 1. 本ファイルの方針

本ファイルには**原則・禁止事項・導線（ポインタ）のみ**記載する。
手順・チェックリスト・事故記録は知見ファイル（`docs/knowledges/`）に委譲する。
マシン固有パスは `.claude.local.md` を参照（git管理外・端末毎に異なる）。

**追加してよいもの**: 原則・禁止事項（1-3行）、知見MDへのポインタ（1行）、高頻度参照エントリ（1行）
**追加禁止**: 手順・フロー、事故番号（MR-XXX）、適用例・トリガー語彙の列挙 → 全て知見MDへ
**行数上限**: 200行。超過時は知見MDへの委譲を検討する。

---

## 2. プロジェクト概要

株式トレードのための自律型AIエージェントシステム。外部ソースから投資アイディアを抽出し、統計分析・バックテスト・デモトレード・本運用まで一貫して自動化する。**モデルベースアプローチ**（定量的な統計結果に基づく短期投資）を採用。

- アーキテクチャ・技術スタック・開発ロードマップ → `docs/architecture.md`
- コマンド集 → `docs/commands.md`
- Codex 分業ワークフロー → `docs/knowledges/tools/083_codex_collaboration.md`

**Codex 引継ぎチェック**: セッション開始時、`C:\Users\zonekun\Documents\codex\investment-agent\docs\codex-to-claude-handoff.md` に未処理エントリがあればユーザーに取り込み作業を提案すること。

---

## 3. データ基盤

| 層 | ストレージ | 用途 |
|----|-----------|------|
| a | Google BigQuery | 構造化データ（株価・銘柄マスタ等） |
| b | Google Cloud Storage | 非構造化データ（EDINET・e-STAT・日証金等） |
| c | ローカルCSV (data/csv/) | BQ/GCS一部コピー。分析出力・キャッシュ（git不要） |
| c' | マスタCSV (data/master/) | 参照・マスタデータ（**git管理必須**） |
| d | 外部API + キャッシュ (data/cache/) | J-Quants, yfinance等 |

**データ参照ルール**: テーブル名・カラム名・ファイル名は推測ファースト厳禁。最初に `data_catalog.md` でテーブル名を確認し、スキーマは `docs/data_catalog/*.md` を Read する。
**データ取得フォールバック**: ①ローカルCSV → ②BQ/GCS/API → ③J-Quants MCP → ④FRED MCP → ⑤discovery
**BQオフロード原則**: フィルタ・集計・JOIN・ウィンドウ関数はBQ SQL。統計検定・可視化はPython → `docs/knowledges/api/002_bigquery.md`

---

## 4. 基本原則

### 4.1 確認優先
事実確認が可能なことを、確認せずに発言・報告しない。
- データ属性はBQ・CSV等で裏取り。日付・時刻に言及する前は必ず確認ツール（Get-Date / python date等）で実値確認。推測・記憶で発言禁止
- 不在断定禁止: 知見MDで永続化先を全て確認してから判断
- ログは症状。原因は中間ログ→コード→手動テスト差異→1社分詳細の順で追跡
- 知見ファイルは最後まで読む（制約は後半の「注意事項」に集約）
- 知見MDのRead結果を待ってから次の探索手段を判断せよ。結果待たず並列調査NG

### 4.2 ユーザー指示優先
ユーザー指示は、AIの技術的判断・プランMDの構造・慣習的実行単位より常に優先する。
指定された手順・参照先・手段・ツールを、自分の判断で省略/改変してはならない。

- 適用判定: 「指定された手段Xを省略/変更しようとしている」と認識した時点で発火
- 異常報告を受けたら外部要因より先に自分を疑う: ユーザーが「動いていない」「おかしい」「壊れている」「アホ」「違う」「欠落」「ない」「来ていない」「入っていない」等を報告したら、外部要因の前に**自分の直前の実行内容を知見MDの記載手順と照合**して自己検証する。自己検証で問題なしと確認した場合のみ外部要因を検討（MR-136/196: 外部帰責で自コードバグを見逃した事故）
- ユーザーが同じ指示を2回繰り返したら、前回の実装を0ベースで再評価
- 破壊的な結果を招く場合のみ実行リスクを提示

→ 移植/手動処理/検証の詳細: `004_coding_conventions.md` §既存コード移植 / §AI直接処理

### 4.3 永続化・記録
- N件（N>=3）の反復処理は1件1永続化。コンテキストメモリに溜めない → `004_coding_conventions.md` §逐次永続化
- ジョブ完了検知 → 知見MD更新 + プランMD更新 + memory更新 + コミット（中断なし即実行）
- 新規規約の導入は文書化とアトミック（「適用」だけ行い「文書化」を後回しにすることを禁止）
- 調査結果・再発防止策のメモリ保存は厳禁 → `docs/knowledges/` または `docs/plans/` に記載
- プロジェクト全体の取り決めもメモリNG。判定: 「別端末でも必要か?」Yes→プロジェクトdocs
- **feedbackライフサイクル**: 新規 feedback は CLAUDE.md 反映後にメモリから削除。project memory は次アクション未定義になった時点で削除し、完了宣言時は必ず削除する

### 4.4 破壊的操作
dry-run → 小範囲10件確認 → 全件展開。全件一発実行禁止。承認された範囲のみ。
プロセス名指定の操作（taskkill /IM・Stop-Process -Name 等）は全同名プロセスkill → /PID 特定必須。他セッション確認なしに /IM 使用禁止。
→ 定義・詳細・事故事例: `004_coding_conventions.md` §破壊的操作

### 4.5 マルチターン待機
明示的GO（OK/進めて等）がなければ作業開始しない。デフォルトは**待機**。
- **GOシグナル**: 「OK」「進めて」「やって」「GO」「お願い」等の実行指示
- **非GO（追加入力待ち）**: 「続き」「続きあり」「あと」「それと」「補足」、体言止め・名詞句の羅列
- 判定基準: 「未完了シグナルの不在」ではなく「GOシグナルの存在」で判定する。GOシグナルは会話状態に依存しない — 待機中・確認中を問わず即トリガー

---

## 5. 長時間運用

**LINE = ntfy通知**（`scripts/notify.py`）。`--sender ATP --task "<作業名>"` 必須 → `docs/knowledges/tools/068_line_ntfy_push.md`
**「覚えて」= ファイル書き出し指示**。書き出し前の「覚えました」は禁止。
**「リカバリログ」= セッションログ** → `docs/knowledges/tools/085_crash_recovery.md`

### 監視
「監視して」指示 → 実プロセス起動 + 動作検証 + ScheduleWakeup併用 + 圧縮後棚卸し。テキストで「監視開始」と返すだけは禁止。
→ 詳細: `docs/knowledges/tools/093_monitoring_obligation.md`

### LINE会話モード
`send_ntfy_and_wait()` 双方向ループ。send_ntfy一方通行は禁止。
**圧縮後の復旧義務**: memory `line_conversation_mode.md` を**必ずチェック**。`active: true` なら `068_line_ntfy_push.md` §LINE会話モード運用ルールに従いファーストトリガーを自発送信して復旧。
CLI構成は068 §② のテンプレートを参照（記憶ベース構成禁止）。
→ 詳細フロー・例外処理: `068_line_ntfy_push.md` §LINE会話モード運用ルール

### クラッシュ復旧・圧縮後復元
コンテキスト圧縮後、作業文脈が不明な場合はセッションログを読んで復元する。
→ `docs/knowledges/tools/085_crash_recovery.md`

### 端末移管
セッション開始時: `docs/terminal-relay.md` を確認。pending エントリがあればユーザーに報告。
→ `docs/knowledges/tools/086_terminal_handoff.md`

---

## 6. 実行環境

- **「LLM使用/LLMパワー」趣旨の指示**: Claude Code自身（Opus/Sonnet）を指す。Gemini / Anthropic API ではない。Claude CodeのPython自動生成も意味しない。
- **Gemini**: 明示指示ある場合を除いて 使用禁止。
- **OS**: Windows 10+ / Git Bash（`python3` 不可 → `python` を使う。`mkdir -p` は `-p` フラグ不可 → `mkdir` 単体か `New-Item -ItemType Directory -Force` を使う）
- **Python管理**: uv（Google Drive上禁止）。**venvパスはMD直書き禁止**。`.claude.local.md` の `<python>` / `venv_dir` を参照（端末固有）
- **パス**: `C:\gdrive\claude\investment-agent`（ジャンクション経由ASCIIパス）
- **スクリプト実行**: 必ず `PYTHONUTF8=1` を付ける
- **ファイルopen**: `encoding=` 必ず明示。ソースコード/JSON/YAML/MD → `utf-8`、CSV出力（外部共有）→ `shift_jis`、外部ファイル → 実際のエンコーディングに合わせる
- **Bash パス表記**: Windows パス `C:\...` はBashで使えない（`\` がエスケープ）。フォワードスラッシュ `C:/...` または `/c/...` を使う
- **Cloud Build**: `docs/knowledges/` 内の該当ドキュメントからコマンドをコピー。手打ち禁止。Windows + Google Drive 環境では一時ビルドディレクトリ必須（`005_cloudrun_job_deploy.md §⑥` 参照）
- **Cloud Scheduler / Cloud Run Job**: `--location=us-west1` 統一
- **時間指定ローカル実行**: `CronCreate` 第一選択。100%トークン到達見込み時は Windows タスクスケジューラ第二選択 → `102_scheduled_execution.md`
- **ファイルコピー**: 単純コピー・移動・リネームでは Read/Write/Edit を使わない。`copy`/`xcopy`/`robocopy`/`move` を使う。内容解析・編集が必要な場合のみ Read/Write を許可
- **ファイルDL**: WebFetchは要約してしまうためファイル取得に使わない。`Invoke-WebRequest` で直接DL
- **ダウンロード先**: 検証用DLは `C:\tmp\` を使う。Google Drive（`C:\gdrive\` / `G:\`）への保存禁止（容量逼迫・他端末同期の問題）。`C:\tmp\` に置くものは **台帳MD** を `Read 103_tmp_folder_registry.md limit=3` で更新ルール確認→ `printf >>` で末尾追記。作りっぱなし放置禁止
- **セキュリティ**: APIキーは.envで管理。絶対にコミットしない
→ その他の規約: `004_coding_conventions.md` §GCS非git同期 / §ディスク管理 / §その他

---

## 7. コーディング規約

- **型ヒント必須**・**docstring必須**（Google style）
- **ロギング**: print禁止。structlogを使用
- **データモデル**: Pydantic v2
- **GCP認証**: `service_account.Credentials` を明示的に構築
- **Gemini**: `google-genai` 統一（`google-generativeai` / `google-cloud-aiplatform` 禁止）
- **日時**: タイムゾーン非明示の日時取得・表示・出力は禁止（`datetime.now()` / `date.today()` 等）。JST指定必須 → 詳細: `004_coding_conventions.md` §日付時刻ルール
- **Git コミット**: `feat:` / `fix:` / `refactor:` / `docs:` / `test:` プレフィックス
- **知見ファイル整合義務**: スクリプト追加／変更時・プランのONE STEP完了時・GCPリソース追加／変更時に対応知見MDを最新化

→ 全規約: `docs/knowledges/tools/004_coding_conventions.md`

---

## 8. スキル・エージェント

正本: `skills/*.md`（編集はここだけ）、ラッパー: `.claude/commands/*.md`（ポインタのみ）
- **Skill**（会話内実行）: `/planning`, `/idea-pipeline`, `/backtest-design`, `/ai-engineer`, `/twitter-reader`, `/owner-judge-commander`
- **Agent**（独立実行→結果返却）: `/code-reviewer`, `/md-reviewer`, `/structure-optimizer`, `/monthly-error-autofix`, `/owner-judge-soldier`
実行時冒頭に `🎯 [<スキル名>] <タスク概要>` を出力。

---

## 9. プラン管理

計画が必要な場合は `/planning` を使用。ファイルは `docs/plans/YYYYMMDD_HHMMSS_<slug>.md`。
改修・バグ修正は `docs/plans/_template_refactor.md` をコピー。`/code-reviewer` が前提フォーマットでレビュー。
プラン実行時はステップの全手順要素の実施を照合してから完了マーキング。
→ 詳細: `004_coding_conventions.md` §ステップ完了検証義務

---

## 10. 知見索引

タスクを受けたら**まず下記タスクテーブルを確認**し、意味的に該当すれば即座にそのファイルを Read する。該当なしなら `docs/knowledges/INDEX.md` で特定。glob/grep 探索はINDEX.mdでも見つからなかった場合の最後の手段。

> **タスクテーブル**
| タスク | ファイル |
|--------|----------------|
| BigQueryを使うコードを書く | `docs/knowledges/api/002_bigquery.md` |
| BQリソース（テーブル/VIEW/TVF）を作成・変更 | `docs/knowledges/api/002_bigquery.md` |
| スクリプトを新規作成・改修 | `docs/knowledges/tools/004_coding_conventions.md` |
| Cloud Run Jobにデプロイ | `docs/knowledges/tools/005_cloudrun_job_deploy.md` |
| Cloud Workflows | `docs/knowledges/tools/080_workflows_runbook.md` |
| TDnet ETL（GCS PDF → BQ） | `013_tdnet_load.md`, `003_tdnet_download.md` |
| 決算特別シフト | `013_tdnet_load.md` §決算特別シフト |
| 月次開示パイプライン全体像 | `042_monthly_disclosure_master.md` |
| 月次開示エラー全自動修復 | `/monthly-error-autofix`、パターンDB: `042-1_monthly_error_fix_patterns.md` |
| データ取り込み・更新タスク | `data_catalog.md` |
| 銘柄属性・会社名・業種 | `docs/data_catalog/bq_stock_code_list.md` |
| 決算反応モデル EDA・予測 | `059_earnings_model_eda.md` |
| 決算答え合わせ・反省会 | `059_earnings_model_eda.md` §反省会の運用手順、ログ: `059-1_hanseikai_log.md` |
| 決算実績ロード（earnings-actual-load） | `docs/knowledges/tools/101_earnings_actual_load.md` |
| ザラ場ツール | `066_zaraba_tool.md` |
| 最新決算表示・XBRL四半期推移 | `099_xbrl_lookup.md` |
| PS1 / PSメニュー | `023_powershell_menu.md` |
| GCP MCPサーバー | `046_gcp_mcp_server.md` |
| 投資アイデアを受けた | `/idea-pipeline` |
| レビュー系エージェント（3種） | `/code-reviewer`, `/md-reviewer`, `/structure-optimizer` |
| 外部記事・論文・ツイート取り込み | `092_reference_management.md`、ToC: `docs/references/README.md` |
| レビューMD作成・提出 | `097_review_submission_guide.md` |
| 決算じっくり分析 | `093_earnings_deep_analysis.md` |
| TOB ML予測モデル | `007_tob_ml_prediction.md` |
| EDINET遅延TOBスクリーニング | `008_edinet_delay_tob_screening.md` |
| 事故・事故報告 | `/md-reviewer` パターン2/4 で `docs/reviews/` に記録 |
| サブエージェント中断・ハング | `docs/knowledges/tools/100_agent_stuck_recovery.md` |
| ディスク掃除 / ガベージ / cleanup | `docs/knowledges/tools/077_cleanup_disk.md` |
| Dropbox / ドロップボックス（UL/DL/temp置き場） | `docs/knowledges/tools/016_dropbox.md` |
| スクリーナー/裁量ツール一覧 | `docs/knowledges/tools/trading_tools_index.md` |
| `.claude.json` 破損・復旧 | `docs/knowledges/tools/claudecode_recovery.md` |

> **全索引**: `docs/knowledges/INDEX.md`

---

## 11. 設計思想

- アイディアソース優先順位・ステータス遷移 → `skills/idea_pipeline.md`

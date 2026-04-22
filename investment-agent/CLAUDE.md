# 投資AIエージェント - CLAUDE.md

## プロジェクト概要

株式トレードのための自律型AIエージェントシステム。外部ソースから投資アイディアを抽出し、統計分析・バックテスト・デモトレード・本運用まで一貫して自動化する。**モデルベースアプローチ**（定量的な統計結果に基づく短期投資）を採用。

- アーキテクチャ・技術スタック・開発ロードマップ → `docs/architecture.md`
- コマンド集 → `docs/commands.md`

---

## 起動時メニュー

**セッション開始時（ユーザーの最初のメッセージが新規タスク指示でない場合）、以下のメニューを表示すること。**
クラッシュ再開モード時はメニュー表示を省略する。

```
=== 随時実行メニュー ===
1. BB_債券履歴_new 取り込み更新
   → PYTHONUTF8=1 <python> scripts/menu_bond_update.py

2. テールリスクシグナル 直近10日チェック（SKEW×VIX×F&G）
   → PYTHONUTF8=1 <python> scripts/menu_signal_check.py

3. 株式市場4局面判定（金融/業績/逆金融/逆業績）
   → jupyter lab scripts/menu_phase_analyzer.ipynb
```

> `<python>` は `C:\venvs\investment-agent\Scripts\python.exe`
> ユーザーが番号を選択したらそのまま実行する。

---

## 「監視する／見張る」の定義

ユーザーから「監視して」「見張っておいて」「watch」「モニター」等の指示を受けた場合、**必ず以下のいずれかのプロセスを立ち上げてから返答する**。テキストで「待機します」「監視開始」等と返すだけで実プロセスを立ち上げないのは**嘘**であり禁止。

- `Bash(run_in_background=true)` でポーリングスクリプトを起動
- `Agent(run_in_background=true)` でサブエージェントに監視委任
- `Monitor` で長時間プロセスの stdout を購読
- `ScheduleWakeup` で自分を指定秒後に叩き起こす（単発）
- `CronCreate` で定期 trigger 登録（セッション跨ぎ永続）
- `RemoteTrigger` で GitHub 系定期処理

**理由**: Claude に常時ポーリング能力は無い。新メッセージかシステム通知イベントが飛ばない限り動かない。背景プロセス無しに「見張る」と約束すると、完了検知も失敗通知も発生せず、ユーザーが再度 ping するまで放置になる。

**返答テンプレート**:
> 「○○を監視する背景プロセスを立ち上げました（PID: xxx）。完了時に `<task-notification>` で起動、失敗時は LINE 通知。」

**LINE通知**: デフォルトは使用しない。ユーザーから指示あった場合のみ併用。

### ⚠️ stall 検知義務（長時間ジョブ監視時は必須、2026-04-20 追加）

**背景**: 2026-04-19 の TDnet batch#5-#11 監視で、chain スクリプトと monitor_backfill.py は「WF SUCCEEDED/FAILED 終局状態」しか検知せず、Workflows が **ACTIVE のまま 15時間 stuck** してもイベントが発火せず見落とした（TPU preempt で worker が callback 投げずに終了、WF 永久待機）。「見張ってます」と言いながら見張れていない失態。

**恒久ルール**:

1. **stall 判定は「進捗メトリクスの不変時間」で行う（想定所要に依存しない）**
   - 進捗メトリクス = **処理済レコード件数 / doc 数 / BQ row count / 出力ログ行数 / step_id / state** のうちジョブに合う複数値
   - **いずれか 1 つでも動いていれば進捗中** とみなす（state=ACTIVE のままでも doc count が増えていれば OK）
   - **全メトリクスが N 分不変 → stall**（非零 exit で終了、通知手段はデフォルト手順に従う）
   - N は**絶対値でジョブ種別ごとに決める**（例: Workflow step 15分、Gemma doc count 30分、BQ insert 10分）。想定所要の倍率で決めない
2. **時間ベースは slow warning のみ（止めない）**
   - 実績 > 想定 × 1.5 → warning を記録するだけ。機械的に abort しない
   - 予実は外れることがあるので、進捗が動いているなら何倍かかっても許容
3. **サブ完了の整合性チェックを入れる**
   - 例: Gemma 完了したのに ai-finalize が 15 分以内に起動しない → stall
   - 例: step X の所要が想定の 1/4 以下 → データ不整合の疑いで doc 数等を検証
4. **`ScheduleWakeup` で自分を定期起こし**（1-2 時間おき）— 背景プロセスが異常を emit しない場合の最終防衛線
5. **完了時刻の見積もりを過ぎたら必ず中間状態を確認**
   - 「まだ処理中だろう」と都合よく解釈しない

**失敗した監視ロジックの例（避けるべき）**:
```bash
while :; do
  STATE=$(... describe --format=value(state))
  case "$STATE" in
    SUCCEEDED) exit 0 ;;   # 終局のみ検知
    FAILED|CANCELLED) exit 1 ;;
    *) sleep 300 ;;        # state の不変も進捗メトリクスも見ない ← 失敗
  esac
done
```



---

## 時刻表示ルール

- **あらゆる時刻はJST（UTC+9）で表示**。ログ・API・BQ・gcloud・MCP・GCS・Colab等、出所を問わず変換すること。UTC出力はNG
- **表示だけでなく判断にも適用**: ファイルのタイムスタンプ・`created_at` 等を読んで「いつ実行されたか」を判断する際も、必ずJSTに変換してから思考すること。UTC のまま判断すると時系列を誤る

---

## 実行環境・重要な注意事項

- **Gemini API**: ユーザーの明示的な指示がない限り Gemini API（個人キー・Vertex AI 問わず）を呼び出さないこと。使用したい場合は必ず事前に確認する
- **OS**: Windows 10+ / Git Bash（`python3` 不可 → `python` を使う）
- **Python管理**: uv（venv: `C:\venvs\investment-agent`、Google Drive上禁止）
- **パス**: `C:\gdrive\claude\investment-agent`（ジャンクション経由のASCIIパス）を常に使う
- **スクリプト実行**: 必ず `PYTHONUTF8=1` を付ける（日本語の UnicodeEncodeError 防止）
- **ファイルopen**: 必ず `encoding="utf-8"` を明示する（Windows デフォルトは cp932）
- **gcloud**: Git Bash から直接実行する（PowerShell経由はクォートエスケープで頻発エラー）
- **グラフ**: JupyterLab ノートブック（.ipynb）で実装・実行する。`matplotlib.use("Agg")`+PNG保存は使わない
- **ローカルDL保存先**: `C:\tmp\`（Google Drive ・ Dropbox 禁止）。検証後は削除する。Dropbox はクラウドラン本番運用のスクリプトが ローカル実行時のテスト出力先として使うのも禁止（PC間同期・容量・規約漏洩リスクのため）
- **長時間バッチジョブのディスク管理義務** (2026-04-20 追加): 多件DLバッチ（EDINET XBRL/TDnet PDF/GCS fetch 等）は以下必須。`load_shareholder_from_index.py` で 8.8GB 枯渇（5,580ディレクトリ放置）。
  1. **逐次削除**: 1件処理（DL→parse→BQ insert）毎に `shutil.rmtree()`。全件分溜めない
  2. **ディスク監視**: N件毎に `shutil.disk_usage("C:\\tmp").free` チェック → 5GB未満で警告、1GB未満で abort
  3. **レジューム**: 起動時に `(key1, key2) IN BQ既存` で処理済スキップ
  4. **ピーク = 並列数 × 1ファイル**: 10 worker なら瞬間 ~10 ZIP 分のみ
  5. **起動前**: `df C:\\tmp` で 10GB 以上空き確認

  **禁止パターン**: 一括DL→最後に削除 / `except: pass` でゴミ残し / `--dry-run` キャッシュ放置
- **Cloud Build**: `gcloud builds submit` は `docs/knowledges/` 内の該当ジョブのドキュメントからコマンドをコピーして使う。手打ちで組み立て禁止
- **時間指定ローカル実行**: `CronCreate` ツールを第一選択（Windows）。Linux VMは必要時のみ別途提案。`RemoteTrigger` はGCP認証不要な純粋GitHub操作タスクのみ

---

## GCS 非git同期の原則

端末間同期対象は以下の3種類のみ。`sync_push.sh` / `sync_pull.sh` で管理。

| 同期対象 | 理由 |
|---------|------|
| `.env` | APIキー類。git管理禁止 |
| `keys/gcp-service-account.json` | GCP認証キー。git管理禁止 |
| `claude-memory/`（GCS上） | Claude Codeメモリ（`~/.claude/projects/.../memory/`）。端末間で共有 |

> **注意**: 会話ログ・ツールトレースは `C:\tmp\claude_logs\<session_id>\` にローカル保管。GCS同期対象外。

**同期禁止**: `scripts/`, `src/`, `.venv/`, `.claude/`, `.mcp.json`, `data/cache/`

```bash
# ❌ 絶対禁止
gsutil rsync -r . gs://stock_data_1930932/config/investment-agent/project/
```

---

## 端末間の作業移管

### 引き継ぎボード（`docs/handoff.md`）

別端末から作業依頼がある場合、このファイルに `status: pending` のエントリが存在する。

- **セッション開始時に必ず確認する**。pending エントリがあればユーザーに報告する
- 送り出し側: エントリを追記して git push
- 受け取り側: git pull 後に確認 → 実行 → `status: done` に更新して git push
- 不要になったエントリは削除してよい

### 共通手順（送り出し側）

```bash
# Step 0: 受け取り側に未pushの変更がないか確認（コンフリクト防止）
# Step 1: git commit & push
git status && git diff --stat
git add -u
git add <新規ファイル>   # .claude/ data/ は除外
git commit -m "..."
git push origin master

# Step 2: 非gitファイルをGCSにプッシュ
bash scripts/sync_push.sh
```

### 受け取り側コマンド

- **Windows**: `cd /c/gdrive/claude/investment-agent && git pull origin master && bash scripts/sync_pull.sh`
- **Linux VM**: `cd ~/project/claude/investment-agent && git pull origin master && bash scripts/sync_pull.sh`

### ⚠️ 複数端末同時編集の注意

- **別端末にpush指示を出す前に、ローカルの未コミット変更を先にcommit & pushする**
- ローカルに未コミット変更がある状態で `git pull` すると**コンフリクトが発生する**（特に知見MDは複数エージェントが触りやすい）
- やむを得ず未コミット変更がある場合: `git stash → git pull → git stash pop`（コンフリクト時は手動マージ）

### ⚠️ 同期確認の手順（誤判断防止）

`git pull` が "Already up to date" を返した場合、**「相手がpushしていない」とは限らない。既に取り込み済みの可能性がある**。同期状況の確認は必ず `git log --oneline -3` でHEADのコミットハッシュを確認してから判断すること。

```bash
# NG: pull結果だけで判断
git pull origin master  # → "Already up to date" → 「まだpushされてない」は誤り

# OK: logで実際のHEADを確認してから判断
git log --oneline -3    # → HEADが期待のコミットか確認
git pull origin master  # → 差分があれば取り込み、なければ確認済み
```

---

## クラッシュ後の作業再開

ユーザーが「クラッシュした。再開モード発動」と指示したら：

1. **セッション一覧を表示**:
   ```bash
   PYTHONUTF8=1 python scripts/list_claude_sessions.py
   ```
   - 各セッションの最終更新時刻・User発話数・初回/直近プロンプトが並ぶ
   - **複数件ある場合のみ**ユーザーに番号を選ばせる。勝手に最新を選ばない（複数コンソール同時作業時の誤復元防止）
   - **1件しかない場合は確認を省略してstep 2に進む**（リカバリを早く始めるため）
2. 選択されたセッションのログを読む:
   - `C:\tmp\claude_logs\<session_id>\conversation.log` — 対話履歴
   - `C:\tmp\claude_logs\<session_id>\tool_trace.log` — ツール呼び出し追跡（**最後の作業を正確に特定する**）
3. active_jobs.md を自動更新 + gcloudで最新状況を確認:
   ```bash
   PYTHONUTF8=1 python scripts/check_jobs.py --update-active
   PYTHONUTF8=1 python scripts/check_jobs.py --limit 5
   ```
4. 状況をユーザーに報告し、**指示があるまで前セッションの続きを勝手に実行しない**

### ⚠️ リカバリ手順の落とし穴

- **step 1 で止まるな**: 「復元しますか？」と確認してユーザーの返事を待つのは❌。1件なら即 step 2 へ、複数件なら選択を聞いた直後に step 2〜4 を連続実行する
- **ログ探索先は `C:\tmp\claude_logs\<id>\` のみ**: `.claude/projects/*.jsonl` を grep するのは❌（手順外）。`list_claude_sessions.py` が出したパスだけ使う
- **ユーザーの「違う」で手順を外れるな**: 曖昧な否定を受けても、まず step 2〜4 を実行してから判断する。ログを読む前に推測で別方向に走ると余計に混乱する
- **「1件しかないから」と確認省略時の例外**: ユーザーが別端末のセッションを復元したい可能性がある場合のみ、一覧表示後に明示的に聞く

> **ログの仕組み**: `scripts/claude_logger.py` が hooks として動作（`--session-id=$PPID` でセッション分離）。
> 保管先: `C:\tmp\claude_logs\<session_id>\`（ローカル、Google Drive外）
> 保持期間: conversation.log / tool_trace.log / console.log = いずれも24時間。古いセッションディレクトリも24時間で削除。
> **ユーザーの明示的な指示がない限りこれらのログは読まない。**

### バックグラウンドジョブ管理（active_jobs.md）

- `gcloud run jobs execute` 実行時、PostToolUseフックが `data/logs/active_jobs.md` に🔄エントリを**自動追記**する
- `check_jobs.py --update-active` で🔄エントリのステータスをgcloud結果で**自動更新**する
- 手動での更新は不要（備考の追記等は任意）

---

## 設計思想

- **分析結果でそのまま使えるものは少数**。有意な結果が出ないことは普通にある
- **とにかく色々やらせてみることが大事**。試行回数を増やすことで期待値が上がる
- **知見の蓄積が最大の資産**。やればやるほどワークスペースが「育つ」
- **繰り返し使うデータは事前にクレンジングしてローカル保存**。毎回生データからLLMに計算させるのはコンテキストの無駄
- **参考コードを渡す**: `reference_code/` の分析コードを参考として与えると自分好みの結果が得られる

### アイディアソース優先順位
1. **X (Twitter)**: ブックマーク → 投資アイディアとして食わせる
2. **YouTubeライブ配信**: 字幕 + コメント両方取得
3. **論文 (ArXiv等)**: 数式・GitHubコードで再現性が高い

### アイディアのステータス遷移
```
NEW → QUEUED → ANALYZING → ANALYZED_PASS / ANALYZED_FAIL
ANALYZED_PASS → BACKTESTING → BACKTEST_PASS / BACKTEST_FAIL
BACKTEST_PASS → CANDIDATE → TRADER / RETIRED
```

---

## データストア（4層構造）

| 層 | ストレージ | 用途 |
|----|-----------|------|
| a | Google BigQuery | 構造化データ（株価・銘柄マスタ等） |
| b | Google Cloud Storage | 非構造化データ（EDINET・e-STAT・日証金等） |
| c | ローカルCSV (data/csv/) | BQ/GCS一部コピー。分析出力・キャッシュ（git不要） |
| c' | マスタCSV (data/master/) | 参照・マスタデータ（**git管理必須**） |
| d | 外部API + キャッシュ (data/cache/) | J-Quants, yfinance等 |

**データカタログ** (`data_catalog.md`): どのストレージに何があるかを記録。データ取り込み前に必ず参照。更新追加も忘れずに実施。

**データ取得フォールバック順序**: ①ローカルCSV → ②BQ/GCS/APIキャッシュ → ③J-Quants MCPサーバー → ④FRED MCPサーバー → ⑤データ自動探索（discovery）

**BigQuery クエリ作成ルール**: SQLを書く前に `data_catalog.md` のスキーマ定義でカラム名を確認する。推測でカラム名を書かない。

**BigQuery オフロード原則**: フィルタ・集計・JOIN・ウィンドウ関数は BigQuery SQL で行う。統計検定・時系列モデル・可視化は Python。詳細は `docs/knowledges/api/002_bigquery.md`。

---

## 作業計画の管理

`/plan` は使用しない。計画が必要な場合は `skills/planning.md` スキルを使用。
計画ファイルは `docs/plans/YYYYMMDD_HHMMSS_<slug>.md` に保存される。

**既存コードの改修・バグ修正・リファクタリングのプランを書く場合**は、`docs/plans/_template_refactor.md` をコピーして使う。フォーマット定義は `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット（基準 commit hash、7 フィールド構成、アンチパターン対応表、4 段検証戦略など）。code-reviewer スキルが本フォーマットを前提にレビューする。

---

## 知見管理（docs/knowledges/）

新しいタスクを受けたら `docs/knowledges/` 内を優先スキャンして既存知見を活用する。

### タスク別の必読ファイル

| タスク | 必ず読むファイル |
|--------|----------------|
| **ad-hoc BQ/GCS/Logging**（コード不要） | GCP MCP: `bq_query` / `gcs_read` / `logging_job` → `docs/knowledges/tools/046_gcp_mcp_server.md` |
| BigQueryを使うコードを書く | `docs/knowledges/api/002_bigquery.md` |
| J-Quants APIを使う | `docs/knowledges/api/001_jquants_api.md` |
| yfinanceでインデックス取得 | `docs/knowledges/api/004_yfinance_index_tickers.md` |
| Vertex AI Gemini モデル利用可能状況 | `docs/knowledges/api/005_vertex_ai_gemini_models.md` |
| TDnet適時開示を取得・改修 | `docs/knowledges/api/003_tdnet_official_scraping.md`, `tools/003_tdnet_download.md` |
| TDnet過去データ（irbank.net） | `docs/knowledges/tools/021_irbank_tdnet_download.md` |
| 楽天証券コンセンサス取得を改修 | `docs/knowledges/tools/022_conse_rakuten.md` |
| PowerShellローカル実行メニュー改修 | `docs/knowledges/tools/023_powershell_menu.md` |
| TDnet ETL（GCS PDF → BQ） | `tools/013_tdnet_load.md`, `tools/003_tdnet_download.md` |
| EDINET パイプライン取得・改修 | `tools/009_edinet_download.md`, `tools/012_edinet_load.md` |
| EDINET API（有報大株主抽出・日付スキャン最適化） | `docs/knowledges/api/006_edinet_api.md` |
| TDnetカテゴリ分類を変更 | `docs/knowledges/tools/006_tdnet_category_classification.md` |
| 休日スキップロジック | `docs/knowledges/tools/019_holiday_handling.md` |
| 月次第N営業日スケジュール | `docs/knowledges/tools/020_nth_business_day_scheduler.md` |
| 投資アイデアを受けた | `skills/idea_pipeline.md`, `tools/053_prediction_market_bot_ideas.md` |
| バックテストを設計・実装 | `skills/backtest_design.md`, `tools/039_backtest_daily_pnl_model.md`, `tools/045_backtest_evaluation_metrics.md` |
| ポジションサイジング・リスクチェック | `docs/knowledges/strategies/001_kelly_criterion_position_sizing.md` |
| ローリングIC戦略棄却（Rolling IC Kill Switch） | `docs/knowledges/strategies/002_rolling_ic_strategy_kill_switch.md` |
| 米国-日本セクターETF リードラグ LS 日次オペレーション | `docs/knowledges/strategies/003_us_japan_sector_leadlag_operation.md` |
| 新しい分析アイデアを受けた | `docs/knowledges/analysis/` 内の関連ファイル |
| SKEW/VIX/F&Gテールリスク判定 | `docs/knowledges/analysis/003_skew_vix_fg_tail_risk.md` |
| EDINET遅延報告TOBスクリーニング | `docs/knowledges/analysis/008_edinet_delay_tob_screening.md` |
| 決算スケジュール減衰（Earnings Schedule Decay） | `docs/knowledges/analysis/009_earnings_schedule_decay.md` |
| スクリプトを新規作成・改修 | `docs/knowledges/tools/004_coding_conventions.md` |
| コードレビュー不備の蓄積ログ・傾向分析 | `docs/knowledges/tools/004-1_code_review_findings_log.md` |
| Cloud Run Jobにデプロイ | `docs/knowledges/tools/005_cloudrun_job_deploy.md` |
| Cloud Run Job 実行状況を監視 | `docs/knowledges/tools/024_cloudrun_job_monitoring.md` |
| tdnet-load + edinet-load シーケンシャル実行 | `docs/knowledges/tools/025_load_sequential.md` |
| 配当データ取得・更新 | `docs/knowledges/tools/026_dividend_date_load.md` |
| J-Quants MCPサーバー確認 | `docs/knowledges/tools/027_jquants_mcp_server.md` |
| FRED MCPサーバー確認 | `docs/knowledges/tools/028_fred_mcp_server.md` |
| 月次データロード（TDnet BQ→GCS） | `docs/knowledges/tools/030_monthly_data_load.md` |
| 月次構造収集・buffett-codeスクレイピング | `docs/knowledges/tools/031_monthly_structure_buffett.md` |
| 過去データバックフィル | `docs/knowledges/tools/032_backfill_sequential.md` |
| Cloud Runジョブ実行状況確認（check_jobs.py） | `docs/knowledges/tools/033_check_jobs.md` |
| データロードジョブ・Cloud Functionsスケジューラ改修 | `docs/knowledges/tools/034_data_load_jobs.md` |
| matplotlib グラフ表示（Windowsローカル） | `docs/knowledges/tools/035_matplotlib_chart_display.md` |
| Cloud Run 特定実行IDのログ確認 | `docs/knowledges/tools/036_check_exec_log.md` |
| 清原スクリーニング | `docs/knowledges/tools/037_kiyohara_screening.md` |
| EDINET XBRLから現金・有価証券抽出 | `docs/knowledges/tools/038_edinet_xbrl_extractor.md` |
| irbank.net 月次開示検証 | `docs/knowledges/tools/040_monthly_irbank_verification.md` |
| 四季報データ読み込み | `docs/knowledges/tools/041_shikiho_reader.md` |
| 月次開示パイプライン全体像・adapter.json仕様 | `docs/knowledges/tools/042_monthly_disclosure_master.md` |
| BC突合NGの自律修正エージェント（fy_corr/yoy+100/Gemini切替/bc_ignore パターン A-I） | `docs/knowledges/tools/042-1_bc_match_agent.md` |
| JPX上場銘柄一覧Excel取得 | `docs/knowledges/tools/043_jpx_stock_list_excel.md` |
| GCP MCPサーバー設定・改修 | `docs/knowledges/tools/046_gcp_mcp_server.md` |
| tdnet-load-parallel Quotaエラーリカバリ | `docs/knowledges/tools/047_tdnet_load_recovery.md` |
| Google検索（ローカルChrome・curl_cffi） | `docs/knowledges/tools/048_google_search_local_chrome.md` |
| Google検索レートリミット回避 | `docs/knowledges/tools/049_google_search_scraping_rate_limit.md` |
| WindowsからgcloudでGCP VM操作 | `docs/knowledges/tools/050_gcp_vm_windows_setup.md` |
| Windows↔Linux VM 双方向同期・SSH・セットアップ | `docs/knowledges/tools/051_windows_linux_vm_guide.md` |
| GitHubリポジトリ構造確認 | `docs/knowledges/tools/052_git_repo_structure.md` |
| クロスプラットフォームPython（Colab/CloudRun/Local） | `docs/knowledges/tools/007_cross_platform_python.md` |
| Dropboxにファイルアップロード | `docs/knowledges/tools/016_dropbox_upload.md` |
| Claude Codeフックで自動処理設定 | `docs/knowledges/tools/017_claude_code_hooks_logger.md` |
| **データ取り込み・更新タスクを受けた** | `data_catalog.md`（BQテーブル名・GCSパス・スキーマ・更新方法が記載）|
| TDnet開示書類をAI検索・月次アダプター設計 | `docs/knowledges/tools/054_jlens_disclosure_search.md` |
| extract_adapter.json の row_label_regex 設計・修正 | `docs/knowledges/tools/055_extract_adapter_design_patterns.md` |
| バフェットコード月次突合を実行・改修 | `docs/knowledges/tools/056_compare_monthly_buffett.md` |
| JPX上場廃止銘柄スクレイピング・IS_TOB_MBO判定 | `docs/knowledges/tools/058_scrape_jpx_delisted.md` |
| TOB公告詳細抽出（EDINET公開買付届出書 docTypeCode=240 → DELISTED_STOCKS拡張カラム） | `docs/knowledges/api/006_edinet_api.md`（「TOB公告情報抽出」セクション）, `scripts/fetch_tob_announcements.py` |
| 決算反応モデル EDA・予測・答え合わせ（ノートブック構成・スコアリング・GCS保存） | `docs/knowledges/tools/059_earnings_model_eda.md` |
| 決算答え合わせ（`earnings_model_predict.ipynb`） | `docs/knowledges/tools/059_earnings_model_eda.md`（「予測 & 答え合わせノートブック」セクション） |
| 決算答え合わせ反省会（データ一括DL + 銘柄分析） | `scripts/earnings_model/download_review_data.py` → `docs/knowledges/tools/059_earnings_model_eda.md`（「反省会の運用」セクション） |
| 決算予測・答え合わせの複数日バッチ再実行（BQクエリ7本共通化・04/14等を保全） | `scripts/earnings_model/batch_rerun_predict.py` → `docs/knowledges/tools/059_earnings_model_eda.md`（「バッチ再実行」セクション） |
| 決算反応モデル学習データ除外管理 | `docs/knowledges/tools/076_earnings_exclusion_mechanism.md` |
| ディスク容量クリーンアップ（data/logs + ~/.claude/ キャッシュ・stale projects） | `docs/knowledges/tools/077_cleanup_disk.md` |
| 決算資料比較分析 Cloud Run Job（Ollama + Qwen2.5 7B + GCS FUSE） | `docs/knowledges/tools/060_earnings_compare_ollama.md` |
| Google Colab（無料枠）開発ノウハウ | `docs/knowledges/tools/061_colab_free_tier_knowhow.md` |
| TDnet/EDINET PDF処理戦略（ライブラリ・LLMルーティング） | `docs/knowledges/tools/062_pdf_processing_strategy.md` |
| 逆日歩買い戦略 | `docs/knowledges/analysis/001_gyakuhibu_buyer_strategy.md` |
| 野菜価格→業績予測分析 | `docs/knowledges/analysis/002_yasai_price_earnings_prediction.md` |
| VWAPトレンド戦略 | `docs/knowledges/analysis/004_vwap_trend_strategy.md` |
| UKI予測モデル（高値安値20日） | `docs/knowledges/analysis/005_uki_predictor_high_low_20d.md` |
| 騰落率ショックエントリー戦略 | `docs/knowledges/analysis/006_breadth_ratio_shock_entry.md` |
| TOB ML予測モデル | `docs/knowledges/analysis/007_tob_ml_prediction.md` |
| ファクターモデル残差相関（銘柄グループ構造分析） | `docs/knowledges/analysis/010_factor_model_residual_corr.md` |
| 部分空間正則化PCAリードラグ 応用アイデア集 | `docs/knowledges/analysis/011_subspace_pca_leadlag_applications.md` |
| 011-1: overnight→daytime × クラスタ（**FAIL**、個人1日ラグで崩壊） | `docs/knowledges/analysis/011-1_cluster_overnight_daytime_leadlag.md` |
| 011-2: 大型→中小型リードラグ（大型定義の多重検証） | `docs/knowledges/analysis/011-2_size_leadlag_multidef.md` |
| 011-3: クラスタ間クロスリードラグ（バリエーション3、**FAIL**） | `docs/knowledges/analysis/011-3_cluster_cross_leadlag.md` |
| 011-4: 米国セクターETF→日本セクターETF（論文再現） | `docs/knowledges/analysis/011-4_us_japan_sector_leadlag.md` |
| データカタログ日付ポリシー | `docs/knowledges/data/001_data_catalog_date_policy.md` |
| EDINET 2024ダウンロード状況 | `docs/knowledges/data/002_edinet_2024_download_status.md` |
| 日証金データ ソース切替（MARGIN_BALANCE/SHINA_RATES、2025-09-26境界） | `docs/knowledges/data/003_taisyaku_source_switch.md` |
| 債券履歴変換（BB_債券履歴_new） | `docs/knowledges/tools/001_convert_bond_history.md` |
| 時系列相関アルゴリズム | `docs/knowledges/tools/002_timeseries_correlation_algorithm.md` |
| J-Quants財務サマリー取得 | `docs/knowledges/tools/008_jquants_fin_summary.md` |
| メール通知（notify.py） | `docs/knowledges/tools/010_notify.md` |
| スクレイパー共通基盤 | `docs/knowledges/tools/011_scraper.md` |
| Colab Enterprise デプロイ | `docs/knowledges/tools/014_colab_enterprise_deploy.md` |
| curl_cffi ブラウザ偽装 | `docs/knowledges/tools/015_curl_cffi_impersonation.md` |
| EDINET遅延レポート取得 | `docs/knowledges/tools/018_edinet_delay.md` |
| AWS MCPサーバー（pricing/documentation） | `docs/knowledges/tools/029_aws_mcp_servers.md` |
| 日本語パス・venv問題の回避 | `docs/knowledges/tools/044_japanese_path_venv_issue.md` |
| extract_adapter修正バックログ・フィードバック | `docs/knowledges/tools/057_extract_adapter_feedback_backlog.md` |
| アクティビスト検出（四季報フラグ・EDINETスキャン・エイリアス生成） | `docs/knowledges/tools/063_activist_detection.md` |
| browser-use CLI 2.0（JSレンダリングページ取得） | `docs/knowledges/tools/064_browser_use_cli.md` |
| nodriver + Google Chrome + Xvfb（Linux 1GB RAM スクレイピング） | `docs/knowledges/tools/069_nodriver_chrome_linux.md` |
| 決算発表予定スクレイピング・BQロード | `docs/knowledges/tools/065_earnings_schedule_load.md` |
| ザラ場ツール（決算リアルタイム監視・スコアリング） | `docs/knowledges/tools/066_zaraba_tool.md` |
| Cloud Run 2重トリガー検出 | `docs/knowledges/tools/067_check_duplicate_triggers.md` |
| 月次NG銘柄 詳細調査手法（PDF+pdfplumber+Gemini画像分析） | `docs/knowledges/tools/070_monthly_ng_investigation.md`, `scripts/investigate_monthly_ng.py` |
| LINE通知（ntfy プッシュ通知） | `docs/knowledges/tools/068_line_ntfy_push.md` |
| XBRL→J-Quants形式変換（EDINET XBRL→fin_summary） | `docs/knowledges/tools/071_xbrl_to_jquants.md` |
| 20日β計算ジョブ（beta-calc） | `docs/knowledges/tools/072_beta_calc.md` |
| Chrome リモートデバッグ（ユーザーブラウザ操作追跡） | `docs/knowledges/tools/073_chrome_remote_debugging.md` |
| AI コスト分析 & Gemma 4 PoC（Phase 3 コスト削減、プロンプト採用、Jaccard 0.558 実測、2024年1月 PoC 履歴） | `docs/knowledges/tools/013-1_ai_cost_and_gemma_poc.md`（旧 074 / 074-1 統合） |
| Jupyter Notebook (.ipynb) プログラム的編集ノウハウ | `docs/knowledges/tools/075_ipynb_editing.md` |
| AI モデル運用ノウハウ（Gemma TPU + Gemini Batch Prediction / Phase I / Gemini 5並列分割 / completionStats API） | `docs/knowledges/tools/078_gemma4_operation.md` |
| Cloud Workflows 運用ノウハウ（parallel shared変数 / connector_params / Callback認証 / retry） | `docs/knowledges/tools/080_workflows_runbook.md` |
| バックフィル監視汎用ツール（Cloud Run Job + Workflows を YAML 宣言で連鎖実行、LINE通知） | `docs/knowledges/tools/013-2_monitor_backfill.md` |
| 株主構成データ抽出（EDINET有報XBRL→SHAREHOLDER_COMPOSITION / アクティビスト判定） | `docs/knowledges/tools/081_shareholder_composition.md`, `scripts/fetch_shareholder_composition.py` |
| Gemma 4 PoC比較スクリプト（TDnet分類精度検証） | `scripts/poc_gemma4_comparison.py` |
> ルール: `docs/knowledges/` にファイルを追加・更新したら本一覧も更新する（詳細: `docs/knowledges/README.md`）

### 恒久ルール vs 時点情報の区分（全知見ファイル共通）

知見ファイルには**恒久ルール**（原則として変わらないもの）と**時点情報**（将来変わり得るもの）が混在する。**時点情報を正解として鵜呑みにしない**。

- **恒久ルール**の例: 設計原則、禁止事項、手順のフロー構造、「〜は使わない」等のポリシー
- **時点情報**の例: バージョン番号、ゾーン一覧、料金、quota、API仕様の詳細、特定の digest/タグ

**時点情報を使う際は**:
1. 記載日付を確認する（ファイルの `作成日` やセクション内の日付）
2. 古い場合（目安1か月以上）は実機・API・公式ドキュメントで現状確認してから使う
3. 確認の結果更新が必要なら知見ファイルを更新する

### 知見ファイル命名規則
```
docs/knowledges/<category>/NNN_<slug>.md
```

### 知見ファイルのテンプレート
テンプレートは `src/knowledge/templates/<category>.md` を使用する。

**analysis カテゴリの必須記載項目（★）:**

| 必須セクション | 内容 |
|-------------|------|
| **★ 使用データ** | データソース（BQテーブル名/GCSパス）・対象銘柄・粒度・期間・前処理 |
| **★ 使用アルゴリズム** | アルゴリズム名と**選択理由**（代替手法と比較して） |
| 結果 | 数値（p値・相関係数・CAR等）を表形式で記載 |
| 判定と理由 | ANALYZED_PASS/FAILの根拠を数値で示す |
| **★ バックテスト結果** | 手法・期間・年率・Sharpe・MaxDD・インデックス比較・ステータス遷移経緯 |

### 外部リファレンス管理（docs/references/）

外部ソースの原文をそのまま保管するフォルダ。保存ルール・フォルダ一覧は `docs/references/README.md` を参照。

| ソース種別 | 保存先 |
|-----------|--------|
| ツイート・X投稿 | `docs/references/tweets/YYYYMMDD_アカウント名_スラッグ.md` |
| Webページ引用 | `docs/references/web/YYYYMMDD_スラッグ.md` |
| 書籍・文書の画像 | `docs/references/{トピック名}/pageN.jpg` |
| GitHub/Qiita等のツール・ライブラリ | `docs/references/{ツール名}/README.md` + 原文ファイル群 |
| 動画（YouTube等） | URLのみ記録（本体は保存しない） |

---

## コーディング規約

- **型ヒント必須**・**docstring必須**（Google style）
- **ロギング**: print禁止。structlogを使用
- **外部API**: 必ずtry/exceptで囲む。リトライはtenacityを使用
- **設定値**: ハードコーディング禁止。config/以下のYAMLまたは環境変数で管理
- **データモデル**: アプリケーションロジックには Pydantic v2 を使用。データストア層は固定しない
- **GCP認証**: `settings.google_application_credentials` からキーファイルパスを取得して `service_account.Credentials` を明示的に構築する
- **Gemini ライブラリ**: `google-generativeai` は非推奨。新規コードは **`google-genai`** を使う（`from google import genai`）。**`google-cloud-aiplatform` も禁止**（Batch Prediction 含むすべての Vertex AI 操作は `google-genai` で行う）
- **Gemini 応答安定化**: 応答がlist/dict混在等で不安定な場合、コード側のtry/exceptで吸収せず**プロンプトとresponse_schemaを修正**して安定させる。`response_schema`でOBJECT型・プロパティ型を明示し、プロンプトは構造化（タスク/ルール/出力形式を箇条書き）する
- **BQ SQL発行前**: `data_catalog.md` でテーブル名・カラム名を確認してからSQLを書く。推測でカラム名を書かない
- **日時はJST**: `datetime.now()` は禁止。`datetime.now(tz=ZoneInfo('Asia/Tokyo'))` を使う。ファイル名・created_at・ログ等すべてJSTで統一。Colabデフォルト（UTC）のまま保存するとファイル名ソート順≠時系列順になりバグの原因になる
- **並列での `requests.Session`**: `requests.Session` は厳密にはスレッドセーフでない（内部 `CookieJar` 等で race 可能性）。`ThreadPoolExecutor` 並列化時は **`threading.local()` でスレッド毎にインスタンス分離**すること。共有 Session を idx % workers で割り当てる方式は race の温床になる
- **Git コミット**: `feat:` / `fix:` / `refactor:` / `docs:` / `test:` プレフィックス

---

## 破壊的操作は必ず dry-run 先行（2026-04-20 追加）

### 「破壊的操作」の定義

「間違えた時にアンドゥコストが高い or 他者影響がある」= 破壊的。具体的には:

- **上書き・削除**: 既存 blob/ファイルへの書き込み、`blob.delete()`, `rm -rf`, `Path.unlink()` など
- **DB 破壊系 DML**: `DROP`, `TRUNCATE`, 広範囲 `DELETE`, `ALTER ... DROP COLUMN`
- **Git 破壊系**: `push --force`, `reset --hard`, `branch -D`, `clean -fd`
- **他者に見える変更**: PR/Issue/Slack/email/LINE 送信、Cloud Run/Workflows 実行
- **bucket/quota 系**: `gsutil rsync -d`, jobs delete, secrets 削除

### 必須ルール

1. **破壊的スクリプトは `--dry-run` を実装**。既存処理に無ければ `--limit 1` 等で小範囲テストから始める
2. **本番前に dry-run / 小範囲で 10 件目視確認 → 正しければ残りに展開**（全件一発実行禁止）
3. **上書き前に内容種別の整合性を検証**（詳細: `docs/knowledges/tools/004_coding_conventions.md`）
4. **承認された破壊的操作はその範囲のみ**。同種の他オブジェクトへ拡大してはいけない

### 事故事例（2026-04-20）

`sync_latest_adapters_bg.py` がローカル extract adapter を GCS URL adapter に誤マッピングで上書き → **245 本破壊**。原因: dry-run 未実装・内容種別検証無し・全銘柄一発実行。`monthly_adapter_index.csv` から再構築で復旧。

---

## 注意事項

- **調査結果・分析結果・再発防止策の記録先**: `docs/knowledges/` または `docs/plans/`、または既存の関連MDファイルへの追記で記載する。Claude Codeメモリ（`~/.claude/projects/.../memory/`）には書かない。メモリは行動ルール・ユーザー情報・プロジェクト進捗の概要ポインタのみ。ユーザーが「記憶して」「記録して」「MDに記載」と指示した場合も、調査結果・再発防止策は知見ファイルに書くこと。**調査結果・分析結果・再発防止策のメモリ保存は厳禁**（再発防止策は事故元のツール・機能の知見ファイルに「落とし穴」セクションを追記する）
- **再発防止策策定時にコード例を作らない**: 判定関数・テンプレート関数・サンプル実装などの具体コードを再発防止ドキュメントに書かない。コード例は読み手の実装自由度を奪い、コピペ前提の硬直した運用を誘発する。原則・判定基準・チェックリストなど**ルール記述に留める**。実装は各スクリプトの文脈に合わせて個別に行う
- **プロジェクト全体に適用される取り決めもメモリNG**: 「全バックテスト共通の前提」「プロジェクト標準」「全〇〇共通ルール」のように**端末横断でプロジェクト全体に適用される取り決め**も、端末ローカルのメモリではなく `CLAUDE.md` / `skills/*.md` / `docs/knowledges/` に書くこと。理由: メモリは端末ローカルで git 同期されないため、複数端末・複数セッションで共有すべきプロジェクト前提を置くと端末間で挙動が割れる。**判定の目安**: 「この情報は別端末で作業する自分にも必要か?」が Yes ならメモリではなくプロジェクトdocsに書く。「自分の今の端末・個人のクセに閉じた話か?」が Yes ならメモリで可
- **推測で発言してから確認するな。確認してから発言せよ**: ticker と会社の紐付け・上場廃止・データの所属先など、事実確認が可能なことを推測で発言しない。発言前に BQ・CSV・BC データ・上場情報等で確認する。ローカルデータで事実確認が取れない場合は WebSearch 等で調査する。「これは別の会社では？」「このデータは XX のものでは？」等の疑問が浮かんだら、まずデータを見る。推測を先に口にしてユーザーに訂正させるのは無駄
- **スクリプト実行結果のログを鵜呑みにしない**: 「0件」「対象なし」「スキップ」等のサマリーログは**症状であって原因ではない**。報告前に以下を必ず確認すること:
  1. **中間ログを読む**: サマリーだけでなく、最初の数社分の詳細ログ（HTTPステータス・例外・WARNING）を確認する
  2. **失敗パスをコードで確認する**: `except` で握りつぶされて正常系と同じ出力に合流するパターンを把握する
  3. **手動テストとの差異を疑う**: 同じURLで手動（curl_cffi等）では成功したのにスクリプトで失敗した場合、HTTPクライアント・ヘッダ・タイムアウト等の実装差異を特定する
  4. **バッチ実行前に1社分を詳細検証**: 全ログを読み、期待通りの動作をしていることを確認してから残りを流す
- **データ取り込み・更新の指示を受けたら**: `data_catalog.md` 定義の方法に従う。データソースの自動探索は行わない（分析・バックテスト時は除く）
- **`data_catalog.md` の常時最新化**: Cloud Run Job新規作成・スケジュール変更・新データソース追加・テーブルTRUNCATE/廃止の都度更新する
- **知見ファイルは最後まで読む**: `docs/knowledges/` のファイルを参照する際、コード片や基本パターンが見つかった時点で読むのを止めない。ルール・注意事項・落とし穴は後半に書かれていることが多い。**特にパス・保存先・命名規則等の制約は「注意事項」セクションに集約されている**ため、実行前にファイル全体を確認すること
- **セキュリティ**: APIキーは.envファイルで管理し、絶対にコミットしない

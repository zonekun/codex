# 知見索引（docs/knowledges/）

> **知見ファイル・スキル・参照構造を追加・変更したら、本索引の該当セクションも更新すること。ファイル追加・移動・リネーム時は §導線チェックリスト で全更新箇所を確認。**

## タスク・トピック別 知見索引

| タスク | 必ず読むファイル |
|--------|----------------|
| **ad-hoc BQ/GCS/Logging**（コード不要） | GCP MCP: `bq_query` / `gcs_read` / `logging_job` → `docs/knowledges/tools/046_gcp_mcp_server.md` |
| BigQueryを使うコードを書く | `docs/knowledges/api/002_bigquery.md` |
| J-Quants APIを使う | `docs/knowledges/api/001_jquants_api.md` |
| yfinanceでインデックス取得 | `docs/knowledges/api/004_yfinance_index_tickers.md` |
| 生成AI利用ガイド（Gemini モデル一覧・location・SDK・事故事例） | `docs/knowledges/api/005_vertex_ai_gemini_models.md` |
| TDnet適時開示を取得・改修 | `docs/knowledges/api/003_tdnet_official_scraping.md`, `tools/003_tdnet_download.md` |
| TDnet過去データ（irbank.net） | `docs/knowledges/tools/021_irbank_tdnet_download.md` |
| コンセンサス取得（RAKU/IFIS）を改修 | `docs/knowledges/tools/022_consensus_load.md` |
| PS1 / PowerShellローカル実行メニュー改修 | `docs/knowledges/tools/023_powershell_menu.md` |
| TDnet ETL（GCS PDF → BQ、決算特別スケジュール） | `tools/013_tdnet_load.md`, `tools/003_tdnet_download.md` |
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
| 決算じっくり分析（セグメント構成変化等、中長期視点の深掘り） | `docs/knowledges/analysis/093_earnings_deep_analysis.md` |
| FY弱気ガイダンス反復スクリーニング | `docs/knowledges/analysis/014_fy_conservative_guidance_screener.md` |
| スクリプトを新規作成・改修 | `docs/knowledges/tools/004_coding_conventions.md` |
| コードレビュー不備の蓄積ログ・傾向分析（※メインエージェントは閲覧のみ。追記は Agent ツールで起動した reviewer サブエージェントの責務） | `docs/knowledges/tools/004-1_code_review_findings_log.md` |
| MDレビュー（AI可読性レビュー） | `/md-reviewer`（正本: `skills/md-reviewer.md`） |
| 計画MD / プランの設計レビュー（内容妥当性）※AI可読性は /md-reviewer | `/code-reviewer` パターン4 |
| レビュー結果統合・構造最適化（/code-reviewer + /md-reviewer の指摘をトークン効率×AI安定性で再評価） | `/structure-optimizer` |
| Cloud Run Jobにデプロイ | `docs/knowledges/tools/005_cloudrun_job_deploy.md` |
| Cloud Scheduler 操作（pause/resume/run/delete） | `docs/knowledges/tools/034_data_load_jobs.md` |
| Cloud Run Job 実行状況を監視 | `docs/knowledges/tools/024_cloudrun_job_monitoring.md`, `tools/084_stall_detection_obligation.md` |
| tdnet-load + edinet-load シーケンシャル実行 | `docs/knowledges/tools/025_load_sequential.md` |
| 配当データ取得・更新 | `docs/knowledges/tools/026_dividend_date_load.md` |
| J-Quants MCPサーバー確認 | `docs/knowledges/tools/027_jquants_mcp_server.md` |
| FRED MCPサーバー確認 | `docs/knowledges/tools/028_fred_mcp_server.md` |
| 月次データロード（TDnet BQ→GCS） | `docs/knowledges/tools/030_monthly_data_load.md` |
| 月次構造収集・buffett-codeスクレイピング | `docs/knowledges/tools/031_monthly_structure_buffett.md` |
| 過去データバックフィル | `docs/knowledges/tools/032_backfill_sequential.md` |
| バックフィル実行メトリクス（フェーズ別実測値・見積もり） | `docs/knowledges/tools/087_backfill_execution_metrics.md` |
| Cloud Runジョブ実行状況確認（check_jobs.py） | `docs/knowledges/tools/033_check_jobs.md` |
| データロードジョブ・Cloud Functionsスケジューラ改修 | `docs/knowledges/tools/034_data_load_jobs.md` |
| matplotlib グラフ表示（Windowsローカル） | `docs/knowledges/tools/035_matplotlib_chart_display.md` |
| Cloud Run 特定実行IDのログ確認 | `docs/knowledges/tools/036_check_exec_log.md` |
| 清原スクリーニング | `docs/knowledges/tools/037_kiyohara_screening.md` |
| EDINET XBRLから現金・有価証券抽出 | `docs/knowledges/tools/038_edinet_xbrl_extractor.md` |
| irbank.net 月次開示検証 | `docs/knowledges/tools/040_monthly_irbank_verification.md` |
| 四季報データ読み込み | `docs/knowledges/tools/041_shikiho_reader.md` |
| 月次開示パイプライン全体像・adapter.json仕様 | `docs/knowledges/tools/042_monthly_disclosure_master.md` |
| 月次開示エラー修復パターンDB（Layer 1即答用） | `docs/knowledges/tools/042-1_monthly_error_fix_patterns.md` |
| 月次エラー全自動修復スキル設計リファレンス（3層アーキ・ガードレール・判断フロー） | `docs/knowledges/tools/042-1-1_monthly_error_autofix_skill_design.md` |
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
| Dropboxファイル操作（UL/DL） | `docs/knowledges/tools/016_dropbox.md` |
| Claude Codeフックで自動処理設定 | `docs/knowledges/tools/017_claude_code_hooks_logger.md` |
| **データ取り込み・更新タスクを受けた** | `data_catalog.md`（インデックス）→ `docs/data_catalog/*.md`（BQテーブル名・GCSパス・スキーマ・更新方法が記載）|
| TDnet開示書類をAI検索・月次アダプター設計 | `docs/knowledges/tools/054_jlens_disclosure_search.md` |
| extract_adapter.json の row_label_regex 設計・修正 | `docs/knowledges/tools/055_extract_adapter_design_patterns.md` |
| バフェットコード月次突合を実行・改修 | `docs/knowledges/tools/056_compare_monthly_buffett.md` |
| JPX上場廃止銘柄スクレイピング・IS_TOB_MBO判定 | `docs/knowledges/tools/058_scrape_jpx_delisted.md` |
| TOB公告詳細抽出（EDINET公開買付届出書 docTypeCode=240 → DELISTED_STOCKS拡張カラム） | `docs/knowledges/api/006_edinet_api.md`（「TOB公告情報抽出」セクション）, `scripts/fetch_tob_announcements.py` |
| 決算反応モデル EDA・予測・答え合わせ（ノートブック構成・スコアリング・GCS保存） | `docs/knowledges/tools/059_earnings_model_eda.md` |
| 決算答え合わせ（`earnings_model_predict.ipynb`） | `docs/knowledges/tools/059_earnings_model_eda.md`（「予測 & 答え合わせノートブック」セクション） |
| 決算答え合わせ反省会（データ一括DL + 銘柄分析） | `scripts/earnings_model/download_review_data.py` → `docs/knowledges/tools/059_earnings_model_eda.md`（「反省会の運用」セクション） |
| 決算反応モデル反省会ログ（銘柄別ログ・改善方針の時系列記録） | `docs/knowledges/tools/059-1_hanseikai_log.md` |
| 決算予測・答え合わせの複数日バッチ再実行（BQクエリ7本共通化・04/14等を保全） | `scripts/earnings_model/batch_rerun_predict.py` → `docs/knowledges/tools/059_earnings_model_eda.md`（「バッチ再実行」セクション） |
| 決算反応モデル学習データ除外管理 | `docs/knowledges/tools/076_earnings_exclusion_mechanism.md` |
| ディスク容量クリーンアップ（data/logs + ~/.claude/ キャッシュ・stale projects） | `docs/knowledges/tools/077_cleanup_disk.md` |
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
| 月次開示→決算予測 ミスプライシングスクリーニング | `docs/knowledges/analysis/012_monthly_disclosure_earnings_screening.md` |
| サプライチェーン決算連鎖（先行好決算→後攻決算またぎ） | `docs/knowledges/analysis/013_supply_chain_earnings_cascade.md` |
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
| ザラ場ツール反省会ログ（066サブファイル） | `docs/knowledges/tools/066-1_zaraba_retrospective.md` |
| 最新決算表示・XBRL四半期推移ツール（TDnet XBRL+BQ fin_summary） | `docs/knowledges/tools/099_xbrl_lookup.md` |
| 決算未発表会社一覧（BQ予定 vs TDNet実績突合） | `docs/knowledges/tools/100_earnings_undisclosed.md` |
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
| TDnet バックフィル実績・再利用ナレッジ（全年完了サマリ、整合性SQL、OOM分割、preemption回避、Phase I設計） | `docs/knowledges/tools/013-3_tdnet_backfill_archive.md` |
| 株主構成データ抽出（EDINET有報XBRL→SHAREHOLDER_COMPOSITION / アクティビスト判定） | `docs/knowledges/tools/081_shareholder_composition.md`, `scripts/fetch_shareholder_composition.py` |
| Codex 分業ワークフロー（ブランチ・引継ぎメモ・取り込みフロー） | `docs/knowledges/tools/083_codex_collaboration.md` |
| Gemma 4 PoC比較スクリプト（TDnet分類精度検証） | `scripts/poc_gemma4_comparison.py` |
| クラッシュ後の作業再開 | `docs/knowledges/tools/085_crash_recovery.md` |
| 端末間の作業移管 | `docs/knowledges/tools/086_terminal_handoff.md` |
| stall検知義務（ジョブ監視） | `docs/knowledges/tools/084_stall_detection_obligation.md` |
| 四半期開示パイプライン（受注・受注残高抽出・GCS構成・スキーマ定義） | `docs/knowledges/tools/089_quarterly_disclosure_master.md` |
| okikusan/stock_skills リファレンス（Claude Code Skills + yfinance 株スクリーニング OSS） | `docs/knowledges/tools/090_okikusan_stock_skills.md` |
| BC月次KPIダウンロード（download_bc_kpi.py 仕様） | `docs/knowledges/tools/091_download_bc_kpi.md` |
| 外部リファレンス管理（保存ルール・付随資料・ToC登録） | `docs/knowledges/tools/092_reference_management.md` |
| 監視・見張りの義務と設計パターン（動作検証・ScheduleWakeup併用・圧縮後棚卸し・長時間待機手段選択） | `docs/knowledges/tools/093_monitoring_obligation.md` |
| QUICKコンセンサス取得（松井証券リサーチネット経由） | `docs/knowledges/tools/095_consensus_quick.md` |
| スクレイピング新規開発ガイド（ツール選定・フレーム攻略・認証移植・bot対策） | `docs/knowledges/tools/096_scraping_development_guide.md` |
| レビュー提出・返却・苦情申し立て（提出側ワークフロー統合） | `docs/knowledges/tools/097_review_submission_guide.md` |
| 株主優待情報取得（松井証券リサーチネット経由・Codex連携） | `docs/knowledges/tools/098_yutai_scraper.md` |
| 株主優待 構造化加工プロンプト（JSONL→Excel変換指示） | `docs/knowledges/tools/098_yutai_format_prompt.md` |
| **裁量トレーディングツール索引**（スクリーナー/ザラバ/シグナル/深掘り一覧） | `docs/knowledges/tools/trading_tools_index.md` |

---

## スキル・エージェント索引（skills/ → .claude/commands/）

> 正本は `skills/` 配下。`.claude/commands/` はスラッシュコマンド登録用の薄いラッパー。

### スキル（会話内で実行）

| コマンド | 正本 | 起動条件 | 別名 |
|---------|------|---------|------|
| `/planning` | `skills/planning.md` | 計画が必要な場合 | — |
| `/idea-pipeline` | `skills/idea_pipeline.md` | 投資アイデアを受けた時 | — |
| `/backtest-design` | `skills/backtest_design.md` | バックテストを設計・実装する時 | — |
| `/ai-engineer` | `skills/ai_engineer.md` | ML/AIモデル開発タスク | — |
| `/twitter-reader` | `skills/twitter_reader.md` | ツイートURL読み取り指示 | — |

### エージェント（独立実行→結果返却）

| コマンド | 正本 | 起動条件 | 別名 |
|---------|------|---------|------|
| `/code-reviewer` | `skills/code-reviewer.md` | 「コードレビューして」指示 | — |
| `/md-reviewer` | `skills/md-reviewer.md` | 「MDレビューして」指示 | — |
| `/structure-optimizer` | `skills/structure-optimizer.md` | レビュー結果統合 / MD構造分析 | リフォーム / 再開発 / MD最適化 / 構造改善 |

> スキル・エージェント追加時は本テーブル + CLAUDE.md 高頻度参照テーブルも更新すること。

---

## MD群の参照構造（正本の所在）

| 情報カテゴリ | 正本 | ポインタ元 |
|------------|------|-----------|
| プロジェクト方針・制約・禁止事項 | `CLAUDE.md` | — |
| 知見ファイル索引 | `docs/knowledges/INDEX.md` §タスク別索引 | CLAUDE.md §知見管理 |
| スキル索引 | `docs/knowledges/INDEX.md` §スキル索引 | CLAUDE.md 高頻度参照テーブル |
| データストア定義 | `CLAUDE.md` §データストア + `data_catalog.md`（インデックス）+ `docs/data_catalog/*.md`（詳細） | — |
| 作業計画 | `docs/plans/` 個別ファイル | CLAUDE.md §作業計画の管理 |
| レビュー結果 | `docs/reviews/` 個別ファイル | — |
| 不備蓄積ログ | `docs/knowledges/tools/004-1_code_review_findings_log.md` | 各レビューエージェント内 |
| 端末間引継ぎ | `docs/terminal-relay.md` | CLAUDE.md §端末間の作業移管 |

> 新規情報カテゴリの正本を定義した場合、本テーブルに追加すること。
> 「何がどこにあるか」は INDEX.md に集約。CLAUDE.md は「どのように使うか」の方針のみ保持。

---

## 導線チェックリスト（ファイル追加・移動・名前変更時）

新規MDファイルを追加、または既存MDを移動・リネームした場合、以下を確認:

1. [ ] **知見ファイル** (`docs/knowledges/`): INDEX.md §タスク別索引に追加したか
2. [ ] **スキル/エージェント** (`skills/` + `.claude/commands/`): INDEX.md §スキル・エージェント索引に追加 + `.claude/commands/` にラッパー作成したか
3. [ ] **高頻度タスク**: 週1回以上参照される**見込み**があるなら CLAUDE.md 高頻度参照テーブルに追加したか。**新設ファイルの場合**: 同じテーマ・同じキーワードで引かれる既存エントリが高頻度参照テーブルにあるなら、新設ファイルも追加する
4. [ ] **別名・類義語**: ユーザーが使いそうな別名があるなら、スキルMD冒頭 + INDEX.md スキル索引 + CLAUDE.md 高頻度参照テーブルに追加したか
5. [ ] **参照元のポインタ**: 他MDからこのファイルを参照しているなら、パスが正しいか
6. [ ] **data_catalog.md**: データ取り込み関連なら data_catalog.md にスキーマ・テーブル名を追加したか
7. [ ] **正本の定義変更**: 新カテゴリの正本を定義したなら INDEX.md §MD参照構造に追加したか
8. [ ] **既存スクリプト改修**: 改修したスクリプトに紐づく知見ファイルの記述が改修後の動作と整合しているか

---

## 恒久ルール vs 時点情報の区分（全知見ファイル共通）

知見ファイルには**恒久ルール**（原則として変わらないもの）と**時点情報**（将来変わり得るもの）が混在する。**時点情報を正解として鵜呑みにしない**。

- **恒久ルール**の例: 設計原則、禁止事項、手順のフロー構造、「〜は使わない」等のポリシー
- **時点情報**の例: バージョン番号、ゾーン一覧、料金、quota、API仕様の詳細、特定の digest/タグ

**時点情報を使う際は**:
1. 記載日付を確認する（ファイルの `作成日` やセクション内の日付）
2. 古い場合（目安1か月以上）は実機・API・公式ドキュメントで現状確認してから使う
3. 確認の結果更新が必要なら知見ファイルを更新する

## 知見ファイル命名規則
```
docs/knowledges/<category>/NNN_<slug>.md
```

## 知見ファイルのテンプレート
テンプレートは `src/knowledge/templates/<category>.md` を使用する。

**analysis カテゴリの必須記載項目（★）:**

| 必須セクション | 内容 |
|-------------|------|
| **★ 使用データ** | データソース（BQテーブル名/GCSパス）・対象銘柄・粒度・期間・前処理 |
| **★ 使用アルゴリズム** | アルゴリズム名と**選択理由**（代替手法と比較して） |
| 結果 | 数値（p値・相関係数・CAR等）を表形式で記載 |
| 判定と理由 | ANALYZED_PASS/FAILの根拠を数値で示す |
| **★ バックテスト結果** | 手法・期間・年率・Sharpe・MaxDD・インデックス比較・ステータス遷移経緯 |

## 外部リファレンス管理（docs/references/）

運用ルール（保存先・命名規則・付随資料・ToC管理）→ `docs/knowledges/tools/092_reference_management.md`
目録（ToC）→ `docs/references/README.md`

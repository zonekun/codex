# docs/references/ — 外部知見リファレンス（目録）

> 運用ルール（保存先・命名規則・付随資料・ToC管理）→ `docs/knowledges/tools/092_reference_management.md`

## フォルダ一覧

| フォルダ | 内容 | ソース |
|----------|------|--------|
| [tweets/](tweets/) | X投稿の保存 | Twitter/X |
| [web/](web/) | Webページ記事の保存 | Qiita, ブログ, 論文等 |
| [prediction_market_trading_bot/](prediction_market_trading_bot/) | 予測市場トレーディングボット（書籍画像） | 書籍 |
| [xbrl-reader/](xbrl-reader/) | EDINET XBRL→CSV変換ツール（teatime77） | GitHub + Qiita |
| [jpx-prediction/](jpx-prediction/) | JPX東証株価予測Kaggleコンペ上位10モデル（J-Quants） | GitHub |
| [japan_us_sector_leadlag_pca/](japan_us_sector_leadlag_pca/) | 部分空間正則化付きPCAによる日米セクターETFリードラグ戦略（中川ら, SIG-FIN-036-13） | 論文PDF |

## Table of Contents

| # | File | Description | Cited in |
|---|------|-------------|----------|
| | **tweets/** | | |
| 1 | [20260323_aiba_algorithm_backtest_overfit.md](tweets/20260323_aiba_algorithm_backtest_overfit.md) | バックテストの9割はオーバーフィット。指標の予測力を散布図+回帰で事前確認すべき | `045_backtest_evaluation_metrics.md` |
| | **web/** | | |
| 2 | [20260402_sig_fin_036_llm_strategy_feedback.md](web/20260402_sig_fin_036_llm_strategy_feedback.md) | LLM投資戦略自動生成のフィードバック設計実証（東大・松尾研）。モデル選択が支配的（Claude > Gemini > GPT） | — |
| 3 | [20260502_rakuscan_plugin_architecture.md](web/20260502_rakuscan_plugin_architecture.md) | RakuScan: Pythonプラグインアーキテクチャ投資分析。1手法=1ファイル、PluginResult共通IF、YAML動的ロード、Python定量+Claude統合 | — |
| 3a | [Faber2007_SSRN-id962461.pdf](web/Faber2007_SSRN-id962461.pdf) | Faber (2007) "A Quantitative Approach to Tactical Asset Allocation". 10ヶ月SMA(≒200日MA)で攻め/守り判定。RakuScan regime プラグインの根拠論文 | #3 regime |
| 3b | [Asness2013_SSRN-id2174501.pdf](web/Asness2013_SSRN-id2174501.pdf) | Asness et al. (2013) "Value and Momentum Everywhere". モメンタム×バリュー×クオリティ複合ファクターの根拠論文 | #3 factor_composite |
| 4 | [20260502_okikusan_stock_skills.md](web/20260502_okikusan_stock_skills.md) | okikusan/stock_skills: Claude Code Skills+Agents+yfinanceで株スクリーニング自動化。3層構造、7エージェント、4エンジン | `090_okikusan_stock_skills.md` |
| | **jpx-prediction/** | | |
| 5 | [README.md](jpx-prediction/README.md) | JPXコンペ上位10モデル総覧 + 横断的な発見7項目 | — |
| 6 | [1st_linear_regression.md](jpx-prediction/1st_linear_regression.md) | 1st (0.381) LinearRegression。High/Low/Mean 3特徴量が支配的 | — |
| 7 | [2nd_lightgbm_regression.md](jpx-prediction/2nd_lightgbm_regression.md) | 2nd (0.356) LightGBM 14特徴量。60日MA乖離率が最重要 | — |
| 8 | [3rd_decision_tree.md](jpx-prediction/3rd_decision_tree.md) | 3rd (0.352) DecisionTree OHLC 4特徴量のみ | — |
| 9 | [4th_rule_based.md](jpx-prediction/4th_rule_based.md) | 4th (0.347) ML不使用。1日リターン順+配当落ち回避 | — |
| 10 | [5th_lgbm_alpha_prediction.md](jpx-prediction/5th_lgbm_alpha_prediction.md) | 5th (0.339) LightGBM アルファ予測。テール学習+銘柄コードカテゴリカル | `005_uki_predictor` TODO#4 |
| 11 | [6th_lgbm_single_feature.md](jpx-prediction/6th_lgbm_single_feature.md) | 6th (0.308) LightGBM 特徴量1個（前日終値差分） | — |
| 12 | [7th_lgbm_hierarchical_sector.md](jpx-prediction/7th_lgbm_hierarchical_sector.md) | 7th (0.301) 33セクター別LightGBM。GroupedTimeSeriesSplit | — |
| 13 | [8th_lgbm_simple.md](jpx-prediction/8th_lgbm_simple.md) | 8th (0.289) シンプルLightGBM。銘柄コードカテゴリカル+LB無視 | `005_uki_predictor` TODO#4 |
| 14 | [9th_intraday_return_ranking.md](jpx-prediction/9th_intraday_return_ranking.md) | 9th (0.281) ML不使用。日中リターンでランキング | — |
| 15 | [10th_monte_carlo_simulation.md](jpx-prediction/10th_monte_carlo_simulation.md) | 10th (0.280) 10万回モンテカルロ。Sharpe最大化 | — |
| | **xbrl-reader/** | | |
| 16 | [README.md](xbrl-reader/README.md) | teatime77 XBRL→CSV変換ツール概要 | `069_xbrl_to_jquants.md` |
| 17 | [github_teatime77_xbrl_reader_README.md](xbrl-reader/github_teatime77_xbrl_reader_README.md) | GitHub原文 | — |
| 18 | [qiita_teatime77_xbrl_csv.md](xbrl-reader/qiita_teatime77_xbrl_csv.md) | Qiita記事原文 | — |
| | **prediction_market_trading_bot/** | | |
| 19 | page1.jpg, page2.jpg | 予測市場トレーディングボット書籍画像 | `053_prediction_market_bot_ideas.md` |
| | **japan_us_sector_leadlag_pca/** | | |
| 20 | [README.md](japan_us_sector_leadlag_pca/README.md) | 部分空間正則化PCAで米国セクターETF→日本セクターETF翌日Open-to-Closeを予測。Carhart4 α=22.23%/yr、R/R=2.22、MDD=9.58% | — |
| 21 | [20260513_customer_momentum_supply_chain.md](web/20260513_customer_momentum_supply_chain.md) | Customer Momentum論文サーベイ。Cohen & Frazzini (2008)原典+再検証6本+実務拡張2本。サプライチェーン決算連鎖戦略の学術的根拠 | `013_supply_chain_earnings_cascade.md` |
| 21a | [CohenFrazzini2008_economic_links.pdf](web/CohenFrazzini2008_economic_links.pdf) | Cohen & Frazzini (2008) "Economic Links and Predictable Returns". 顧客モメンタムL/S月次150bps。Limited Attention仮説 | #21 原典 |
| | **backtest-expert/** | | |
| 22 | [SKILL.md](backtest-expert/SKILL.md) | backtest-expert スキル本体。"壊れにくい戦略を探す"哲学、ワークフロー6ステップ、ストレステスト手法 | `skills/backtest_design.md` |
| 23 | [methodology.md](backtest-expert/methodology.md) | Seven Sins of Quantitative Investing + 方法論詳細（Regime Analysis, Walk-Forward, Slippage） | `skills/backtest_design.md` |
| 24 | [failed_tests.md](backtest-expert/failed_tests.md) | 失敗パターン6類型 + Case Study Framework + Red Flags Checklist | `skills/backtest_design.md` |
| 25 | [evaluate_backtest.py](backtest-expert/evaluate_backtest.py) | 5次元スコアリングCLI（Sample Size/Expectancy/Risk Mgmt/Robustness/Exec Realism、100点満点） | `045_backtest_evaluation_metrics.md` |


# docs/references/ — 外部知見リファレンス

外部ソース（Web記事・論文・ツイート・書籍等）の原文をそのまま保管し、必要時に参照するためのフォルダ。
`docs/knowledges/` が自分たちの知見・ノウハウであるのに対し、こちらは**外部の一次情報をそのまま保存**する場所。

## 保存ルール

| ソース種別 | 保存先 | 命名規則 |
|-----------|--------|----------|
| ツイート・X投稿 | `tweets/` | `YYYYMMDD_アカウント名_スラッグ.md` |
| Webページ引用 | `web/` | `YYYYMMDD_スラッグ.md` |
| 書籍・文書の画像 | `{トピック名}/` | `pageN.jpg` |
| GitHub/Qiita等のツール・ライブラリ | `{ツール名}/` | `README.md`（概要）+ 原文ファイル群 |
| 動画（YouTube等） | URLのみ記録（本体は保存しない） |

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
| | **jpx-prediction/** | | |
| 3 | [README.md](jpx-prediction/README.md) | JPXコンペ上位10モデル総覧 + 横断的な発見7項目 | — |
| 4 | [1st_linear_regression.md](jpx-prediction/1st_linear_regression.md) | 1st (0.381) LinearRegression。High/Low/Mean 3特徴量が支配的 | — |
| 5 | [2nd_lightgbm_regression.md](jpx-prediction/2nd_lightgbm_regression.md) | 2nd (0.356) LightGBM 14特徴量。60日MA乖離率が最重要 | — |
| 6 | [3rd_decision_tree.md](jpx-prediction/3rd_decision_tree.md) | 3rd (0.352) DecisionTree OHLC 4特徴量のみ | — |
| 7 | [4th_rule_based.md](jpx-prediction/4th_rule_based.md) | 4th (0.347) ML不使用。1日リターン順+配当落ち回避 | — |
| 8 | [5th_lgbm_alpha_prediction.md](jpx-prediction/5th_lgbm_alpha_prediction.md) | 5th (0.339) LightGBM アルファ予測。テール学習+銘柄コードカテゴリカル | `005_uki_predictor` TODO#4 |
| 9 | [6th_lgbm_single_feature.md](jpx-prediction/6th_lgbm_single_feature.md) | 6th (0.308) LightGBM 特徴量1個（前日終値差分） | — |
| 10 | [7th_lgbm_hierarchical_sector.md](jpx-prediction/7th_lgbm_hierarchical_sector.md) | 7th (0.301) 33セクター別LightGBM。GroupedTimeSeriesSplit | — |
| 11 | [8th_lgbm_simple.md](jpx-prediction/8th_lgbm_simple.md) | 8th (0.289) シンプルLightGBM。銘柄コードカテゴリカル+LB無視 | `005_uki_predictor` TODO#4 |
| 12 | [9th_intraday_return_ranking.md](jpx-prediction/9th_intraday_return_ranking.md) | 9th (0.281) ML不使用。日中リターンでランキング | — |
| 13 | [10th_monte_carlo_simulation.md](jpx-prediction/10th_monte_carlo_simulation.md) | 10th (0.280) 10万回モンテカルロ。Sharpe最大化 | — |
| | **xbrl-reader/** | | |
| 14 | [README.md](xbrl-reader/README.md) | teatime77 XBRL→CSV変換ツール概要 | `069_xbrl_to_jquants.md` |
| 15 | [github_teatime77_xbrl_reader_README.md](xbrl-reader/github_teatime77_xbrl_reader_README.md) | GitHub原文 | — |
| 16 | [qiita_teatime77_xbrl_csv.md](xbrl-reader/qiita_teatime77_xbrl_csv.md) | Qiita記事原文 | — |
| | **prediction_market_trading_bot/** | | |
| 17 | page1.jpg, page2.jpg | 予測市場トレーディングボット書籍画像 | `053_prediction_market_bot_ideas.md` |
| | **japan_us_sector_leadlag_pca/** | | |
| 18 | [README.md](japan_us_sector_leadlag_pca/README.md) | 部分空間正則化PCAで米国セクターETF→日本セクターETF翌日Open-to-Closeを予測。Carhart4 α=22.23%/yr、R/R=2.22、MDD=9.58% | — |

## 検索のヒント

各トピックフォルダの `README.md` にキーワードセクションを設けているので、`grep -r` で横断検索できる。

```bash
# 例: EDINET関連のリファレンスを探す
grep -rl "EDINET" docs/references/
```

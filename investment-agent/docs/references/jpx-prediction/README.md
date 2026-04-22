# JPX Tokyo Stock Exchange Prediction — Kaggle コンペ上位モデル集

> **ソース**: https://github.com/J-Quants/JPXTokyoStockExchangePrediction
> **主催**: JPX総研（J-Quants プロジェクト）
> **開催**: 2022-04-04〜（Kaggle）
> **コンペURL**: https://www.kaggle.com/competitions/jpx-tokyo-stock-exchange-prediction

## コンペ概要

東京証券取引所の上場銘柄について、株価リターンのランキングを予測するコンペティション。
日本市場の金融データ（株価・財務・オプション等）を用いたクオンツモデルの構築が求められた。

## キーワード

JPX, 東証, 株価予測, リターン予測, ランキング予測, Kaggle, J-Quants,
線形回帰, LightGBM, XGBoost, ニューラルネット, 特徴量エンジニアリング,
株価, 出来高, 財務データ, 日本株, クオンツ, コンペティション

## 上位10モデル一覧

| 順位 | Private LB Score | モデル概要 | リファレンスファイル |
|:----:|:----------------:|-----------|-------------------|
| 1st | 0.381 | LinearRegression（sklearn） | [1st_linear_regression.md](1st_linear_regression.md) |
| 2nd | 0.356 | LightGBM Regression（14特徴量） | [2nd_lightgbm_regression.md](2nd_lightgbm_regression.md) |
| 3rd | 0.352 | DecisionTreeRegressor（4特徴量） | [3rd_decision_tree.md](3rd_decision_tree.md) |
| 4th | 0.347 | ルールベース（ML不使用） | [4th_rule_based.md](4th_rule_based.md) |
| 5th | 0.339 | LightGBM Alpha予測（8特徴量） | [5th_lgbm_alpha_prediction.md](5th_lgbm_alpha_prediction.md) |
| 6th | 0.308 | LightGBM（特徴量1個のみ） | [6th_lgbm_single_feature.md](6th_lgbm_single_feature.md) |
| 7th | 0.301 | LightGBM セクター別（33モデル） | [7th_lgbm_hierarchical_sector.md](7th_lgbm_hierarchical_sector.md) |
| 8th | 0.289 | LightGBM シンプル（※訓練コード未公開） | [8th_lgbm_simple.md](8th_lgbm_simple.md) |
| 9th | 0.281 | 日中リターンランキング（ML不使用） | [9th_intraday_return_ranking.md](9th_intraday_return_ranking.md) |
| 10th | 0.280 | モンテカルロシミュレーション | [10th_monte_carlo_simulation.md](10th_monte_carlo_simulation.md) |

## 取り込み状況

全10モデルのリファレンスファイル作成完了（2026-04-09）。
各ファイルは「要点まとめ（日本語）+ README原文（英語）」の構成。

## 横断的な発見（上位10モデル共通パターン）

### 1. シンプルさが勝つ
- 上位10中 **アンサンブルを使ったモデルはゼロ**
- 特徴量1個で6位（前日終値差分）、4特徴量で3位（OHLC）、ルールベースで4位・9位
- 複雑なニューラルネットは上位にいない

### 2. ML不使用が2モデル（4位・9位）
- 4位: return_1day 降順 + 配当落ち回避
- 9位: 日中リターン（始値→終値）でランキング
- 「市場の分布は変化するため、関係性を見つけても予測に有用とは限らない」（4位）

### 3. LightGBM が圧倒的（6/10）
- 2nd, 5th, 6th, 7th, 8th が LightGBM。1st は LinearRegression、3rd は DecisionTree
- XGBoost は上位10に不在（5位が試したが LGBM に劣後）
- カテゴリカル特徴量の扱いやすさが一因か

### 4. 銘柄コードのカテゴリカル特徴量が最重要（5位・8位が独立に発見）
- 銘柄コード = セクター・時価総額・ボラ特性・流動性を暗黙的に集約する「埋め込み」
- 明示的な特徴量では代替不可能な情報を1変数で補完
- **→ UKI Predictor への適用を TODO に追加済み**（`005_uki_predictor_high_low_20d.md` 次ステップ #4）

### 5. Public LB は信頼できない → ローカル CV 重視
- 7位・8位が独立に「LBを見ない方がいい」と結論
- 金融時系列はノイズが大きく、短期間のスコアはランダム要素が支配的
- Walk-forward CV / GroupedTimeSeriesSplit が推奨

### 6. テール学習（5位の独自手法）
- Winsorize（外れ値除去）の逆で、上位250+下位250銘柄のみで学習
- 予測対象をアルファ（超過リターン）にすることと組み合わせて有効
- ボラティリティ上昇期には効果が低下する可能性

### 7. 配当落ちの悪用（4位）
- ExpectedDividend > 0 の銘柄を最下位にする → スコアブースト
- コンペ特有のハック的手法だが、配当落ちのアノマリー自体は実運用でも重要

## 備考

- 8位の訓練コードのみ未公開（GitHub上の注記）
- コンペで使用されたデータの一部は J-Quants API で取得可能
- 前回コンペの上位モデルは [JPX公式ページ](https://www.jpx.co.jp/corporate/news/news-releases/0010/20210813-01.html) にまとめあり

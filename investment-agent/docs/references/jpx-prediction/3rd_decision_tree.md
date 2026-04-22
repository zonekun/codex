# 3rd Place — Decision Tree Regressor Model

> **順位**: 3rd / Private LB Score: 0.352
> **ソース**: https://github.com/J-Quants/JPXTokyoStockExchangePrediction/tree/master/winner-models/3rd
> **実行時間**: 学習111s + 予測0.277s

---

## 要点まとめ

### モデル
sklearn `DecisionTreeRegressor` 1本のみ。アンサンブルなし。

### 使用した特徴量（4個のみ）

| 特徴量 | 意味 |
|--------|------|
| Open | 始値 |
| High | 高値 |
| Low | 安値 |
| Close | 終値 |

追加の特徴量エンジニアリングなし。バリデーションセットでハイパーパラメータ最適化。

### 知見
- **たった4つの生の価格特徴量で3位**。複雑なアンサンブル不要
- 1位（線形回帰）と同様、シンプルさが勝因
- 「モデルの複雑さよりシンプルさを優先する顧客は多い」

---

## README 原文

# Summary

The analysis employed a DecisionTreeRegressor trained on four stock market features: Open, High, Low, and Close prices. The optimal model was obtained by comparing the scores on the divided validation set during hyperparameter optimization. Training required approximately 111 seconds, while prediction generation took 277 milliseconds.

# Features Selection / Engineering

The model utilized only the four base price features with no additional engineering or transformations applied.

# Training Method(s)

DecisionTreeRegressor with hyperparameter optimization on validation set.

# Simple Features and Methods

The simplified model achieved a score of 0.352, representing the complete feature set without any reduction. Many customers are happy to trade off model performance for simplicity.

# Model Execution Time

Training: ~111 seconds. Prediction: ~277 milliseconds.

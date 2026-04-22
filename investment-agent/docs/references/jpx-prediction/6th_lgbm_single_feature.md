# 6th Place — LightGBM Single Feature Model

> **順位**: 6th / Private LB Score: 0.308
> **ソース**: https://github.com/J-Quants/JPXTokyoStockExchangePrediction/tree/master/winner-models/6th
> **実行時間**: 148.3s

---

## 要点まとめ

### モデル
LightGBM Regressor。アンサンブルなし。

### 使用した特徴量（1個のみ）

| 特徴量 | 意味 |
|--------|------|
| 前日終値との差分 | Difference from previous day's closing price |

移動平均・ボラティリティ等の特徴量も作成したが、最終的にこの1つだけが最良スコア。

### 知見
- **特徴量1個で6位**。「普通に考えたらハイスコアは無理」と思うことを試す姿勢
- 特徴量を減らして1つにする判断がポイント
- 参考書籍: 『Kaggle で勝つデータ分析の技術』

---

## README 原文

# Summary

- LightGBM
- LGBMRegressor
- Kaggle Notebooks
- 148.3s

# Features Selection / Engineering

- What were the most important features?
  Difference from previous day's closing price
- How did you select features?
  After creating the main features such as moving averages and historical volatility, we left the combinations with good evaluations.
- Did you make any important feature transformations?
  No
- Did you find any interesting interactions between features?
  No
- Did you use external data? (if permitted)
  No

# Training Method(s)

- What training methods did you use?
  LightGBM
- Did you ensemble the models?
  No

# Interesting findings

- What was the most important trick you used?
  I decided to reduce the number of features to one.
- What do you think set you apart from others in the competition?
  I tried something that I thought would be impossible for a high score if I thought about it normally.
- Did you find any interesting relationships in the data that don't fit in the sections above?
  No, it's still hard to read the stock market.

# Simple Features and Methods

No subset of features would get 90-95% of final performance.

# Model Execution Time

148.3s

# References

Books: Kaggle で勝つデータ分析の技術

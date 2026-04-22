# 4th Place — Rule-Based Model (No ML)

> **順位**: 4th / Private LB Score: 0.347
> **ソース**: https://github.com/J-Quants/JPXTokyoStockExchangePrediction/tree/master/winner-models/4th
> **実行時間**: 71.0s

---

## 要点まとめ

### モデル
**機械学習なし**。ルールベースのみ。

### ロジック
1. `return_1day`（1日リターン）を降順にランク付け
2. `ExpectedDividend > 0`（配当予想あり）の銘柄を最下位にする

### 知見
- **MLなしで4位**。著者自身「運が良かった」と認めている
- 配当落ち日の銘柄をショートすることでスコアブースト（他の参加者も指摘）
- 「市場の分布は変化するため、関係性を見つけても予測に有用とは限らない」
- timeseries-APIによる評価方式が実運用に近い点を評価

### 参考Discussion
- [Could this competition have a lucky winner?](https://www.kaggle.com/competitions/jpx-tokyo-stock-exchange-prediction/discussion/320323)
- [Shorting dividend days = easy score boost](https://www.kaggle.com/competitions/jpx-tokyo-stock-exchange-prediction/discussion/320836)

---

## README 原文

# Summary

- Rule-based approach without machine learning.
  - Rank return_1day in descending order.
  - If ExpectedDividend is greater than 0, make it the lowest.
- We posted the following discussion.
  - https://www.kaggle.com/competitions/jpx-tokyo-stock-exchange-prediction/discussion/359151

# Features Selection / Engineering

SKIP(Because it does not use machine learning)

# Training Method(s)

SKIP(Because it does not use machine learning)

# Interesting findings

- What was the most important trick you used?
  - Luck!
- What do you think set you apart from others in the competition?
  - The timeseries-API allows models to be evaluated in a way that is close to real-world problems.
- Did you find any interesting relationships in the data that don't fit in the sections above?
  - No, even if we did find them, there is no guarantee that they would be useful for forecasting because market distributions change.

# Simple Features and Methods

SKIP(Because it does not use machine learning)

# Model Execution Time

- How long does it take to generate predictions using your model? 71.0s

# References Discussion

- Could this competition have a lucky winner?,
  https://www.kaggle.com/competitions/jpx-tokyo-stock-exchange-prediction/discussion/320323
- Shorting dividend days = easy score boost,
  https://www.kaggle.com/competitions/jpx-tokyo-stock-exchange-prediction/discussion/320836

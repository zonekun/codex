# 9th Place — Intraday Return Ranking (No ML)

> **順位**: 9th / Private LB Score: 0.281
> **ソース**: https://github.com/J-Quants/JPXTokyoStockExchangePrediction/tree/master/winner-models/9th
> **実行時間**: 不明

---

## 要点まとめ

### モデル
**機械学習なし**。ルールベースのみ（4位と同じく非MLアプローチ）。

### ロジック
$$\text{Intraday Return} = \frac{C^{adj}_t - O^{adj}_t}{O^{adj}_t}$$

日中リターン（始値→終値の変化率）を計算し、その値で全銘柄をランク付け。pandas のみで完結。

### 使用した特徴量（2個のみ）

| 特徴量 | 意味 |
|--------|------|
| Open | 始値（調整済み） |
| Close | 終値（調整済み） |

### 知見
- 「モデルを訓練していないので、Training Method は省略」
- 4位のルールベースと合わせ、**上位10中2つが非ML**
- 日中リターンのランキングだけで0.281のスコアを達成

---

## README 原文

# Summary

Before modeling, we first adjust the price and volume information of each stock. Then, the intraday return is derived, which is defined as:
$Intraday Return = \frac{C^{adj}_t - O^{adj}_t}{O^{adj}_t}$

This indicator is based on the price change from opening of a trading day (i.e., opening price) to the close (i.e., closing price). Finally, stocks can be ranked by this return feature.
All the derivation is done with pandas package.

# Feature Selection/Engineering

Our method only requires 'Open' and 'Close' features to compute the 'IntradayReturn'. For each date stock input, we compute the 'IntradayReturn' by the formula above for each stock, then 'Rank' all stocks by 'IntradayReturn' as our submission.

# SUBMISSION MODEL

We didn't train any models, so I guess this part can be omitted.

# 7th Place — LightGBM Hierarchical Sector Model

> **順位**: 7th / Private LB Score: 0.301
> **ソース**: https://github.com/J-Quants/JPXTokyoStockExchangePrediction/tree/master/winner-models/7th
> **実行時間**: 学習28.1s（GPU）/ 予測0.311s

---

## 要点まとめ

### モデル
**33セクター別の LightGBM Regressor**。各セクターごとに独立モデルを学習。ハイパーパラメータは全セクター共通（Optuna + GroupedTimeSeriesSplit で最適化）。

### 使用した特徴量

| 特徴量 | 重要度 |
|--------|--------|
| Volume | **最重要** |
| Open | 高 |
| Close | 高 |
| High | 中 |
| Low | 中 |
| 17SectorName | 影響なし（セクター分割で定数化） |
| AdjustmentFactor | ほぼ影響なし（≈1） |
| SupervisionFlag | ほぼ影響なし（≈0） |

追加の特徴量エンジニアリングなし。17SectorName/AdjustmentFactor/SupervisionFlag を除いても同スコア。

### 学習方法
- GroupedTimeSeriesSplit: 時系列の順序を維持したCV（5チャンク、訓練-検証間にギャップ）
- Optuna でハイパーパラメータ探索
- 予測対象: 変化率（rate of change）

### 知見
- **セクター別モデルが最大の工夫**。同セクター銘柄は価格相関が高い（例: 海運セクター）
- Public LBはノイズが大きく信頼できない → ローカルCV重視が正解だった
- セクター個別のハイパーパラメータチューニングは過学習リスクのため実施せず
- ハードウェア: Intel E5-2620v4 + Nvidia GTX 1080 TI

---

## ModelSummary 原文

# Summary

Our model consists of several LightGBM regressors. For each sector in 33SectorName, one LightGBM regressor was trained. Depending on the sector of the stock, our model selects the corresponding regressor for the prediction. Our prediction target was the rate of change. The most important features were volume, open and close.

Tools: 1. Scikit-Learn 2. LightGBM 3. Optuna 4. Pandas 5. Numpy
Training took 28.1 seconds.

# Feature Selection / Engineering

The most important features were volume, open and close. The features 17SectorName and AdjustmentFactor are not relevant for the prediction, and the SuperVisionFlag has almost no influence. The feature 17SectorName has no influence, as it is regarded as a constant due to our partitioning into sector-specific regressors. The SupervisionFlag is almost exclusively 0. The AdjustmentFactor almost always is 1.

We encountered difficulties in selecting new features, since the public leaderboard score could only serve as a vague and maybe even wrong indication of future predictive performance. We used GroupedTimeSeriesSplit for hyper-parameter optimization. In the end, we decided not to introduce any new features, but to create a robust model with the existing features.

# Training Method(s)

We trained every sector-specific regressor separately, while optimizing the hyper-parameters over all regressors. Every LightGBM regressor uses the same hyper-parameters. We optimized using Optuna and GroupedTimeSeriesSplit. This method splits a time series into cross-validation groups while keeping the natural order.

# Interesting findings

By far the most important trick was to train individual models for each sector. Stocks in the same sector correlate with each other (e.g., marine transportation sector).

# Simple Features and Methods

The features 17SectorName, AdjustmentFactor and SupervisionFlag can be removed with no impact on score.

# Model Execution Time

- Training (GPU): 28.1 seconds
- Prediction (GPU): 0.311 seconds per day (2000 stocks)
- Simplified training (GPU): 20.6 seconds
- Simplified prediction (GPU): 0.293 seconds

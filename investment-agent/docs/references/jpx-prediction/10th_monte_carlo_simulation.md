# 10th Place — Monte Carlo Simulation Model

> **順位**: 10th / Private LB Score: 0.280
> **ソース**: https://github.com/J-Quants/JPXTokyoStockExchangePrediction/tree/master/winner-models/10th
> **実行時間**: 20-25分（Kaggle CPU）

---

## 要点まとめ

### モデル
**モンテカルロシミュレーション**ベース。Sharpe ratio 最大化を目的とする。

### アプローチ
1. 過去データから各日100,000回のポートフォリオシミュレーションを実行
2. スプレッド値とSharpe ratioの関係を分析 → 最適スプレッド ≈ 0.4
3. 予測時:
   - 3-7日のラグで過去データを取得
   - 各ラグで1次多項式を学習し2日先を予測
   - R²に基づく加重平均で最終予測
   - 10,000回のシミュレーションでスプレッド0.4に最も近い配分を選択

### 使用ツール
- Python 3.7.12
- 標準Kaggle CPUのみ（GPU不要）
- notebooks/simulations: シミュレーション実行
- notebooks/submission: コンペ提出用

### 知見
- 従来の予測モデルではなく**シミュレーションベースのアロケーション**
- 確率的手法のため完全な再現性は保証されない
- 予測モデルの改善やシミュレーション回数増加で結果改善の余地あり
- 実行時間は長い（20-25分）が、Kaggle CPU制限内

---

## README 原文

The repository contains three main components:
- documentation/ Model Documentation - JPX.pdf: high-level solution overview
- notebooks/simulations: analysis simulation notebooks
- notebooks/submission: Kaggle submission notebook

# HARDWARE

Standard Kaggle Notebook Environment (with CPU only).

# SOFTWARE

Python 3.7.12 with additional package dependencies listed in requirements.txt.

# DATA SETUP

This notebooks needs to run inside Kaggle Competition environment, no additional data setup is required.

# NOTEBOOKS

The simulations.ipynb generates 100,000 daily portfolio simulations stored in jpx-solution/notebooks/simulations/days_montecarlo_simulations.

The simulation_aggregation.ipynb aggregates daily simulations and identifies optimal spread values to maximize historical Sharpe ratio.

The submission-notebook.ipynb contains complete model logic for use within the Kaggle competition environment.

---

## ModelSummary 原文

This approach prioritizes the Sharpe ratio metric through a simulation-based methodology.

The foundational work involved analyzing the relationship between spread values and Sharpe ratio performance. 100,000 stock allocation simulations with the corresponding spread were run for each day. A target spread of approximately 0.4 optimizes historical Sharpe ratios.

The prediction mechanism:
1. Historical data spanning varying lag periods (3-7 days) precedes each prediction
2. First-degree polynomials trained on each lag generate two-day forecasts with R² error calculations
3. Predictions across lags receive weights based on their respective R² performance
4. Running 10,000 simulations identifies the spread allocation closest to the 0.4 target

Complete process requires approximately 20-25 minutes on standard Kaggle CPU infrastructure. The stochastic nature means exact reproducibility isn't guaranteed across runs.

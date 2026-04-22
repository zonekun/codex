# 8th Place — Simple LightGBM Model

> **順位**: 8th / Private LB Score: 0.289
> **ソース**: https://github.com/J-Quants/JPXTokyoStockExchangePrediction/tree/master/winner-models/8th
> **実行時間**: 不明（※訓練コード未公開）

---

## 要点まとめ

### モデル
LightGBM（シンプルな構成）。非線形性を活かしつつ極力シンプルに。

### 使用した特徴量
- 銘柄コード（カテゴリカル、**最重要**）
- リターン移動平均（モメンタム系）
- 生の市場データ（Open, High, Low, Close, Volume）
- ファイナンス理論ベースの指標（ボラティリティ、移動平均等）

特徴量同士の高い相関はLightGBMに処理を任せる方針。

### 学習方法
- Optuna でハイパーパラメータ最適化
- Walk-forward CV（5チャンク、訓練-検証間にギャップ）
- **Public LBを一切見ずにローカルCVのみで判断**

### 知見
- ノイズが大きいためLBは信頼できない → ローカル重視が過学習防止の鍵
- 「心理的に難しいがLBを無視すべき」（7位と同じ結論）
- 銘柄コードのカテゴリカル特徴量が最重要（5位と同じ結論）
- 特徴量セットの拡張も検討可能なほどシンプル

### 参考
- Submission notebook: https://www.kaggle.com/code/vuk1998/jpx-submission-template/notebook?scriptVersionId=95590449
- Discussion: https://www.kaggle.com/competitions/jpx-tokyo-stock-exchange-prediction/discussion/361127
- 依存: numpy, pandas, lightgbm のみ

---

## README 原文

# Summary

Generally this was a rather simple LGBM model. The key was to use a non-linear model but to keep it simple. The features were raw as well as some features based on finance theory – volatility, moving averages etc. Popular lightgbm python library was used and the training was rather quick as the dataset was not massive.

# Features Selection / Engineering

![figure](https://raw.githubusercontent.com/J-Quants/JPXTokyoStockExchangePrediction/master/winner-models/8th/images/figure.png)

The most important feature was the actual stock code. This is somewhat sensible as it aggregates a lot of the data about the stock we don't have access to.
After that return moving averages, therefore; we can probably conclude that the model learns something related to some kind of momentum strategy.
The feature selection process was not really extensive. I wanted to try out various things but due to lack of time I just stuck with something that was simple and sensible to use.
Obviously, these features are highly correlated and there are a lot of interesting techniques that could be used to combat that. However, I left it to lgbm to deal with that.

# Training Method(s)

Locally, I used optuna to find the best hyper parameters. It was important to properly set a cross validation scheme. For this, I used walk-forward cross-validation with 5 chunks and a small gap between training and validation set in each split.

# Interesting findings

An important thing is that due to large noise it is often not really important to look at the leaderboard. I focused on having locally good results and actually never cared about the leaderboard at all. This is psychologically hard to do but as it turns out pays off as regularisation towards overfitting!

# Simple Features and Methods

Generally, I believe that this model is simple enough. There is really very low performance-simplicity trade off. If not, I would even consider extending the feature set.

# Model Execution Time

Generally the model is quick to train. The execution time is also negligible compared to the overall setup of trading the stock only once a day.

# References

Submission notebook: https://www.kaggle.com/code/vuk1998/jpx-submission-template/notebook?scriptVersionId=95590449
Discussion: https://www.kaggle.com/competitions/jpx-tokyo-stock-exchange-prediction/discussion/361127
Required packages: numpy, pandas and lightgbm.

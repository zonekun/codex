# 5th Place — LightGBM Alpha Prediction Model

> **順位**: 5th / Private LB Score: 0.339
> **ソース**: https://github.com/J-Quants/JPXTokyoStockExchangePrediction/tree/master/winner-models/5th
> **実行時間**: 学習・予測ともに1分未満

---

## 要点まとめ

### モデル
LightGBM Regression。アンサンブルなし。

### 核心的アプローチ
- **生リターンではなくアルファ（超過リターン）を予測**。市場全体のリターンを差し引く
- 予測対象: $(P_{t+2}/P_{t+0} - \sum(P_{t+2}/P_{t+0})/n)$
- 株式分割未調整の価格データをTarget値から再構成

### 使用した特徴量（8個）

| 特徴量 | 意味 |
|--------|------|
| SecurityCode | 銘柄コード（カテゴリカル特徴量として、**最重要**） |
| Close_rank | 株価のクロスセクションランキング |
| mrsignal | 2日間の短期ミーンリバージョン |
| momsignal | 131日モメンタム（25日ラグ） |
| volsignal | 231日のリターン標準偏差 |
| adv_rank | 11日間平均出来高のランキング |
| ave_momsignal | momsignalのクロスセクション平均 |
| ave_mrsignal | mrsignalのクロスセクション平均 |

### 学習方法
- Expanding windowで初期1年、1ヶ月先をOOS予測
- ハイパーパラメータはOOS累積リターン最大化で最適化
- **訓練データは上位250銘柄+下位250銘柄のみ**（テール学習、Winsorizeの逆）

### 知見
- **行動バイアス（ミーンリバージョン・モメンタム）がファンダメンタルより有効**
- テール（上下250銘柄）でのみ学習する非常識なアプローチが大幅にスコア向上
- ボラティリティ上昇期にはテール学習の効果が低下する可能性
- インプライドボラティリティデータは探索したが最終的に除外（実装複雑性のため）
- 長期OOSメトリック ≈ 0.2（3-4年間）
- 実運用では取引コスト・回転率で非経済的だが、月次/四半期の構築なら有望

---

## README 原文

# Summary

Broadly, financial market prediction, from a signaling perspective is considered an extremely weak process, with dependencies often inconsistent and highly variable, changing market conditions as well as various idiosyncratic risks pervasive in financial market time series make prediction of returns extremely challenging. It is generally my preference to de-trend return data by subtracting some element of broad market return for observational periods as there appears to be better predictability in alpha (excess return) than in beta (pure return). Even though the problem is extremely variable there are biases that exist that tend allow some prediction on alpha, these include behavioral, fundamental and other idiosyncratic biases; in this particular case behavioral drivers were found to be the predominate features compared with fundamental aspects. I was very excited to explore the detailed implied volatility data, especially since this should be an excellent market barometer in terms of the change in out-the-money vols giving a view on future expectations of equity market returns. To this end I explored as much as possible in terms of both the term structure as well as the moneyness for both puts and calls. Whilst it turned out that there was a small prediction value and improvement on the out of sample competition metric, in using the near dated average implied vol as a feature, the additional complexity of folding this into the notoriously tricky Kaggle time series methodology and the time that I had to implement, I deemed this better excluded. It would have been really great to have looked rather at single stock vol data rather than what I assume was for the broad market beta.

Whilst the prediction requirement of the competition was presented as predicting $P|_{t+2} / P|_{t+1} - 1$ for each stock (here P is stock price), I looked at various alternative prediction objectives which subtracted the daily average return of the entire stock universe from each stock, defined say as the alpha of each stock. I continued however to optimize on the return metric pertinent to the competition. I found that $(P|_{t+2} / P|_{t+0} - \sum^n_{k=0}(P|_{t+2}/P|_{t+0})/n)$ gave the best optimized competition metric.

I made some adjustments to the data as it seemed to me that the price data was not adjusted for corporate actions or stock splits, however the target return data had been thus I reconstructed all individual stock price time series using the adjusted target values.

In terms of the machine learning algorithm, I tend to prefer the data partitioning philosophy of the gradient boosted decision trees, this in my experience tends to give better results in financial time series compared with the activation function in the neural network space which I see as too constrained, I did try TabNet as well as some DNN structures but abandoned these after it was clear that Light GBM regression model worked best. Typically I try to ensure that the model shows some consistency in prediction over long periods of out-of-sample testing and thus typically use an expanding window with a one month look ahead prediction out-of-sample for metric optimization. This allows for hyperparameter optimization as a function of maximum out of sample return. To this end the machine learning parameters of LGBM tend to ensure that overfitting is minimized that that models tend to be as simple as possible. This in my experience to this point gives more consistent results in terms of generating positive return in market neutral constructs such as is the case in this particular problem definition. Long term monthly out of sample metric values where closer to 0.2 over the three or four years tested.

The most important feature by a significant measure was the previous two day return of each stock which exhibited significant mean reversion of alpha in the next two day period. Other features which increased the long term out of sample return metric included longer term momentum on price returns with a typical exhibited lag component, historical observed standard deviation of returns, a measure of average daily volume as a ranking, the overall price level as a ranked variable and the inclusion of various cross-section averages of the mean reversion and momentum metrics. I also found that defining each stock across a categorical feature inclusion increased the long term objective function. Many of the fundamental features do exhibit a bias although my suspicion is these would likely be better utilized for longer term return expectations. Unfortunately none of their inclusion improved the long term out of sample back test.

Due to the model being extremely simple and not requiring out of fold testing optimization on the data set the model trains extremely quickly.

# Features Selection / Engineering

- What were the most important features?
  As alluded to above the most important features were largely behavioral and included mean reversion, momentum and observed historic volatility values. Fascinatingly, for this specific problem two aspects significantly increased the out of sample metric this being defining the security code as a categorical feature and training the dataset on the outliers (I found that including only the top and bottom 250 returns across the data added significantly to the objective function). In the final submission I used only eight features:
  - SecuritiesCode: the unique identifier for each stock as a cat features
  - Close_rank: A cross sectional ranking of the stock prices
  - mrsignal: a short term (2 day) historic price return for each stock
  - momsignal: a longer term (131 trading day historic price return with a 25 day lag)
  - volsignal: a standard deviation of daily returns for the previous 231 trading days
  - adv_rank: a cross sectional ranking of average daily volume of the previous 11 trading days
  - ave_momsignal: a cross sectional average of momsignal
  - ave_mrsignal: a cross sectional average of mrsignal

![figure](https://raw.githubusercontent.com/J-Quants/JPXTokyoStockExchangePrediction/master/winner-models/5th/images/figure.png)

The training of these factors only on the top and bottom 250 performing stocks at each daily interval had a significant impact on the score.

# Training Method(s)

- An expanding window is utilized with the initial period corresponding to roughly one year of data.
- The Light GBM regression model is fitted to the data
- The next one month period was predicted, ranked and scored according to the competition metric
- This month is then included into the next window and run until a complete set of out of sample scores are generated
- The combined score for a given run is calculated as the mean of the one month results of the complete out of sample set
- Running multiple feature inclusions as well as parameter change generates the final model with best average score across the universe
- Ensemble methods did not show marked improvement and were excluded.

# Interesting findings

- The unconventional step of training the data on the tails rather than Winsorizing was a really interesting trick that made a significant impact on the score.
- The 4th place model utilized the dividend yield which likely as a value proxy assisted in performance.
- Inclusion of good quality single stock vol skews would have been amazing to look at.

# Simple Features and Methods

The methodology employed is designed to be as simple as possible. Trading costs and turnover would likely make the implementation non-economically viable. However longer term prediction windows designed as a portfolio construction around market capitalization benchmark would have merit.

# Model Execution Time

- Training: Less than 1 minute
- Prediction: Less than 1 minute

# References

All work, use of models, feature engineering and methodology are work of my own design and experience.

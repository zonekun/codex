# 1st Place — Linear Regression Model

> **順位**: 1st / Private LB Score: 0.381
> **ソース**: https://github.com/J-Quants/JPXTokyoStockExchangePrediction/tree/master/winner-models/1st
> **実行時間**: 68.2s（学習: 45.5s）

---

## 要点まとめ

### モデル
sklearn `LinearRegression` 1本のみ。アンサンブルなし。ドメイン知識なしで1位。

### 使用した特徴量

**そのまま使った列**:

| 特徴量 | 意味 |
|--------|------|
| SecuritiesCode | 銘柄コード |
| Open | 始値 |
| High | 高値 |
| Low | 安値 |
| Close | 終値 |
| Volume | 出来高 |
| AdjustmentFactor | 株式分割等の調整係数 |
| ExpectedDividend | 予想配当 |
| SupervisionFlag | 監理銘柄フラグ（0/1） |

**自分で作った派生特徴量**:

| 特徴量 | 計算式 | 意味 |
|--------|--------|------|
| Daily_Range | Close - Open | 日中の値動き幅 |
| Mean | (High + Low) / 2 | 高安の中間値 |

### 前処理
- ExpectedDividend の欠損 → 0埋め
- Open/High/Low/Close の欠損 → ffill → bfill
- 全数値特徴量を `scipy.stats.zscore` で標準化
- 訓練/テスト分割は `2022-04-01` で時系列分割

### 偏回帰係数の結果
- **重要だったのは High, Low, Mean の3つだけ**
- Mean は High/Low と相関 r=0.99 のため偏回帰係数が負に出ている（多重共線性）
- 著者自身「この3特徴量だけでも精度維持できるのでは」と仮説提示

### 実用上の注意
当日の株価データ（Open/High/Low/Close等）が入力として渡され、当日のリターンランキングを予測する構造。つまり**当日の高値・安値が判明した後に予測する形**であり、実トレードでそのまま使えるロジックではない。Kaggle APIの評価方式（当日データ→ランク提出）に特化した設計。

複雑なモデルより特徴量の選び方とシンプルさが効いた例。

---

## Summary

I conducted a regression analysis using a linear regression model. The tool used was "LinearRegression", which is included in Python's linear models in Scikit-learn. Running time was 68.2s. According to the partial regression coefficients, the important features may be the high and low stock prices and their averages.

## Features Selection / Engineering

Since I have no domain knowledge of stock trading, I focused on data files related to stock prices and selected data that I thought could be used as features in the training.
These are "SecuritiesCode", "Open", "High", "Low", " Close", "Volume", "AdjustmentFactor", "ExpectedDividend", and "SupervisionFlag".
Among these features, the missing values of "ExpectedDividend" are filled with 0, and the missing values of Open, High, Low, Close are filled with the values before and after. In addition, I calculated and added features called "Daily_Range" and "Mean" from these features. And standardization was performed before learning.
The partial regression coefficients obtained from the training results indicate that three features are important: High, Low, and Mean. Figure 1 shows a graph of the partial regression coefficients. Table1 shows the correlations of the features used. It can be seen that the features values related to prices (Open, High, Low, Close, Mean) each show a high correlation.

Table1
![Table1](https://raw.githubusercontent.com/J-Quants/JPXTokyoStockExchangePrediction/master/winner-models/1st/images/table1.png)

Figure1
![Figure1](https://raw.githubusercontent.com/J-Quants/JPXTokyoStockExchangePrediction/master/winner-models/1st/images/figure1.png)

## Training Method

I used for training LinearRegression, a linear regression model included in Python's Scikit-learn. The partial regression coefficients obtained as a result of the training are shown in Table 2. The partial correlation coefficient of the Mean is negative against the High and Low, which is thought to be since the Mean feature has a strong correlation with the High and Low ($r_{high} = 0.99$, $r_{row}=0.99$ ).

Table2
![Table2](https://raw.githubusercontent.com/J-Quants/JPXTokyoStockExchangePrediction/master/winner-models/1st/images/table2.png)

## Interesting Findings

Stock prices change due to a variety of factors. Therefore, it is very difficult to understand how all of them affect stock prices. So, I think it is possible that the regression score is improved by reducing the number of features as shown in the results of this study.

## Simple Features and Method

Although this model is considered simple enough at this time, there is a possibility that the learning time can be reduced while maintaining sufficient accuracy if only the three features mentioned above (High, Low, Mean) are used. (Note that this is a hypothesis, as I have not been able to test it.)

## Model Execution Time

The model training time is 45.5s, which is the time displayed when submitting my public Kaggle Note.

---

## 依存ライブラリ

```
numpy == 1.21.6
pandas == 1.3.5
sklearn == 1.0.2
Scipy == 1.7.3
```

## ノートブック全コード

```python
# === Cell 1: imports ===
import numpy as np
import pandas as pd
import os
for dirname, _, filenames in os.walk('/kaggle/input'):
    for filename in filenames:
        print(os.path.join(dirname, filename))

# === Cell 2: model imports ===
from sklearn.linear_model import LinearRegression
import joblib
from scipy import stats
import jpx_tokyo_market_prediction

# === Cell 3: load csv files ===
stock_prices = pd.read_csv("../input/jpx-tokyo-stock-exchange-prediction/train_files/stock_prices.csv")
secondary_stock_prices = pd.read_csv("../input/jpx-tokyo-stock-exchange-prediction/train_files/secondary_stock_prices.csv")
supplemental_prices = pd.read_csv("../input/jpx-tokyo-stock-exchange-prediction/supplemental_files/stock_prices.csv")
supplemental_secondary_stock_prices = pd.read_csv("../input/jpx-tokyo-stock-exchange-prediction/supplemental_files/secondary_stock_prices.csv")
stock_prices = stock_prices.append(secondary_stock_prices)
stock_prices = stock_prices.append(supplemental_prices)
stock_prices = stock_prices.append(supplemental_secondary_stock_prices)

# === Cell 4: featuring for train data ===
def featuring_train(data):
    data['Date'] = pd.to_datetime(data['Date'])
    data['ExpectedDividend'] = data['ExpectedDividend'].fillna(0)
    data['Target'] = data['Target'].fillna(0)
    data["SupervisionFlag"] = data["SupervisionFlag"].astype(int)

    cols = ['Open', 'High', 'Low', 'Close']
    data.loc[:,cols] = data.loc[:,cols].ffill()
    data.loc[:,cols] = data.loc[:,cols].bfill()

    data['Daily_Range'] = data['Close'] - data['Open']
    data['Mean'] = (data['High']+data['Low']) / 2
    data['Mean'] = data['Mean'].astype(int)

    data['Open'] = stats.zscore(data['Open'])
    data['High'] = stats.zscore(data['High'])
    data['Low'] = stats.zscore(data['Low'])
    data['Close'] = stats.zscore(data['Close'])
    data['Volume'] = stats.zscore(data['Volume'])
    data['Daily_Range'] = stats.zscore(data['Daily_Range'])
    data['Mean'] = stats.zscore(data['Mean'])

    data = data.drop(['RowId'], axis=1)
    return data

# === Cell 5: apply featuring ===
data = featuring_train(stock_prices)

# === Cell 6: split data ===
data_train = data[data['Date']<'2022-04-01']
data_test = data[data['Date']>'2022-04-01']
data_test = data_test.reset_index(drop=True)
data_train = data_train.drop(['Date'], axis=1)
data_test = data_test.drop(['Date'], axis=1)

# === Cell 7: separate features and target ===
X_train = data_train.drop(['Target'], axis=1)
y_train = data_train['Target']
X_test = data_test.drop(['Target'], axis=1)
y_test = data_test['Target']

# === Cell 8: train ===
model = LinearRegression()
model.fit(X_train, y_train)

# === Cell 9: model detail ===
print(model.coef_)
print(model.intercept_)
print(model.get_params())
print(model.predict(X_test))
print(model.score(X_test, y_test))

# === Cell 10: save model ===
joblib.dump(model, 'regression_model.learn')

# === Cell 11: featuring for test data ===
def featuring_test(data):
    data['ExpectedDividend'] = data['ExpectedDividend'].fillna(0)
    data["SupervisionFlag"] = data["SupervisionFlag"].astype(int)

    cols = ['Open', 'High', 'Low', 'Close']
    data.loc[:,cols] = data.loc[:,cols].ffill()
    data.loc[:,cols] = data.loc[:,cols].bfill()

    data['Daily_Range'] = data['Close'] - data['Open']
    data['Mean'] = (data['High']+data['Low']) / 2
    data['Mean'] = data['Mean'].astype(int)

    data['Open'] = stats.zscore(data['Open'])
    data['High'] = stats.zscore(data['High'])
    data['Low'] = stats.zscore(data['Low'])
    data['Close'] = stats.zscore(data['Close'])
    data['Volume'] = stats.zscore(data['Volume'])
    data['Daily_Range'] = stats.zscore(data['Daily_Range'])
    data['Mean'] = stats.zscore(data['Mean'])

    data = data.drop(['RowId', 'Date'], axis=1)
    return data

# === Cell 12: make API environment ===
env = jpx_tokyo_market_prediction.make_env()
iter_test = env.iter_test()

# === Cell 13: prediction loop ===
for (prices, options, financials, trades, secondary_prices, sample_prediction) in iter_test:
    prices.head()
    x_test = featuring_test(prices)
    y_pred = model.predict(x_test)
    sample_prediction['Target'] = y_pred
    sample_prediction = sample_prediction.sort_values(by="Target", ascending=False)
    sample_prediction['Rank'] = np.arange(len(sample_prediction.index))
    sample_prediction = sample_prediction.sort_values(by="SecuritiesCode", ascending=True)
    sample_prediction.drop(["Target"], axis=1)
    submission = sample_prediction[["Date", "SecuritiesCode", "Rank"]]
    env.predict(submission)

# === Cell 14: check submission ===
print(submission)
```

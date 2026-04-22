# バックテスト：日次PnLモデル（案C）設計パターン

**カテゴリ**: tools
**作成日**: 2026-03-10
**ステータス**: 有効
**関連ファイル**: `scripts/backtest_datr_long.py`

## 概要

保有期間N日のバックテストで正しい日次P&Lを計算する設計パターン。
「N日リターンを日次として複利計算」する誤実装（案A）と、
正しい「日次PnLモデル（案C）」の対比を記録する。

---

## ❌ よくある誤実装（案A）: 重複期間を独立トレードとして複利計算

```python
# NG: 毎日 ret_20d（20日後の終値リターン）を計算し複利
price["ret_20d"] = price.groupby("TICKER")["CLOSE"].shift(-20) / price["CLOSE"] - 1
# 日T と日T+1 の ret_20d は 19/20 日が重複しているのに独立トレートとして累積
cum = (1 + daily_result["ret_20d"]).cumprod()
```

### 問題: ボラティリティドラッグ（Volatility Drag）

- 20日リターンの標準偏差 σ ≈ 10% とすると、幾何平均 ≈ 算術平均 − σ²/2 = −0.5%/期
- 600期（営業日）× −0.5% = **約−300%の人工的損失**が発生
- 実際の平均リターン ≈ 0 でも累積リターン −53.8%、MaxDD −97.8% という異常値になる

---

## ✅ 正しい実装（案C）: 日次PnLモデル

### 考え方

シグナル日S に選定した銘柄を S+1〜S+20 の間保有。
任意の日Dのポートフォリオ = **D-20〜D-1 に選定された銘柄群の等金額合算**。
各銘柄の日次リターンを集計することで、真の日次P&Lを計算する。

```python
# 日次リターンを先に計算
test["daily_ret"] = test.groupby("TICKER")["CLOSE"].pct_change()
daily_ret_piv = test.pivot(index="YEARDATE", columns="TICKER", values="daily_ret")
signal_piv    = test.pivot(index="YEARDATE", columns="TICKER", values="signal_col")

# 各日の選定銘柄を記録
selected_by_date = {}
for dt in dates:
    row = signal_piv.loc[dt].dropna()
    cutoff = row.quantile(threshold)
    selected_by_date[dt] = set(row[row >= cutoff].index)

# 日次PnL: 日Dの保有銘柄 = D-HOLD_DAYS〜D-1 に選定された銘柄
port_daily = []
for i, dt in enumerate(dates_list):
    held = set()
    for lag in range(1, HOLD_DAYS + 1):
        si = i - lag
        if si >= 0 and dates_list[si] in selected_by_date:
            held |= selected_by_date[dates_list[si]]
    rets = daily_ret_piv.loc[dt, list(held)].dropna()
    rets = rets[(rets > -0.30) & (rets < 0.30)]  # 極端な外れ値除去
    port_daily.append({"date": dt, "ret": rets.mean(), "n_stocks": len(rets)})
```

### メリット

- 重複保有を正しく扱う（同一銘柄の複数エントリーを等金額でカウント）
- 日次リターン系列が得られるため、標準的なSharpe/MaxDD計算が正確
- サンプル数が多い（非重複サンプリング案A:約30回 vs 案C:全営業日分）

---

## ベンチマーク比較の必須化

バックテスト結果は単体では評価できない。**必ずインデックスと比較する**こと。

```python
# yfinance でインデックス取得
import yfinance as yf
topix  = yf.download("1306.T", start=..., auto_adjust=True)["Close"]  # TOPIX ETF
nk225  = yf.download("^N225",  start=..., auto_adjust=True)["Close"]
```

| インデックス | yfinance ticker | 備考 |
|---|---|---|
| TOPIX | `1306.T` | NEXT FUNDS TOPIX ETF（`^TOPX` は無効） |
| 日経225 | `^N225` | 有効 |

→ 詳細は `docs/knowledges/api/004_yfinance_index_tickers.md` 参照

---

## Sharpe計算の注意点

| 手法 | 年率換算 | 備考 |
|------|---------|------|
| 日次リターン（案C） | `mean/std * sqrt(252)` | ✅ 正しい |
| 20日リターン（案A） | `mean/std * sqrt(252/20)` | ⚠️ 重複を無視しており不正確 |

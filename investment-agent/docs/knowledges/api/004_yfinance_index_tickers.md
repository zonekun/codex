# yfinance インデックスティッカー一覧

**カテゴリ**: api
**作成日**: 2026-03-10
**ステータス**: 有効

## 概要

yfinance でインデックスデータを取得する際の確認済みティッカー一覧。
特に TOPIX は `^TOPX` では取得できないことに注意。

---

## 日本株インデックス

| インデックス | ティッカー | 状態 | 備考 |
|---|---|---|---|
| TOPIX | `1306.T` | ✅ 有効 | NEXT FUNDS TOPIX連動型上場投信（ETF）。`^TOPX` は無効 |
| TOPIX（直接） | `^TOPX` | ❌ 無効 | `YFPricesMissingError: possibly delisted` |
| 日経225 | `^N225` | ✅ 有効 | 直接取得可 |

---

## 取得サンプル

```python
import yfinance as yf

# TOPIX（ETF経由）
topix_df = yf.download("1306.T", start="2023-06-01", auto_adjust=True, progress=False)
topix_close = topix_df["Close"].squeeze()  # Series化

# 日経225
nk225_df = yf.download("^N225", start="2023-06-01", auto_adjust=True, progress=False)
nk225_close = nk225_df["Close"].squeeze()

# 正規化（累積リターン比較用）
topix_norm = topix_close / topix_close.iloc[0]
nk225_norm = nk225_close / nk225_close.iloc[0]
```

---

## 注意事項

- `1306.T` のデータは TOPIX 指数そのものではなく ETF の価格。乖離は通常 0.1% 未満で実用上問題なし
- timezone: yfinance は UTC で返すため `tz_localize(None)` で統一しておくと日付インデックスのマージが楽
- `^N225` は直接指数値（円）が返る
- J-Quants API で TOPIX OHLC を取得する場合は `ClientV2.get_idx_bars_daily_topix()` を使う方が正確（→ `001_jquants_api.md`）

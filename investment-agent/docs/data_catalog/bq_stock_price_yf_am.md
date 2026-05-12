# STOCK.STOCK_PRICE_YF_AM
> 親: [`data_catalog.md`](../../data_catalog.md)

### `STOCK.STOCK_PRICE_YF_AM` — 前場スナップショット（yfinance）

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.STOCK_PRICE_YF_AM` | 前場終了時点の OHLCV + 概算売買代金 | 日次 | Cloud Run Job `stock-price-yf-am-load`（月〜金 11:45 JST、4並列タスク）で yfinance → BQ |

**`STOCK.STOCK_PRICE_YF_AM` スキーマ:**

| カラム名 | 型 | モード | 説明 |
|---------|-----|--------|------|
| DATE | DATE | REQUIRED | 取引日（パーティションキー） |
| TICKER | STRING | REQUIRED | 銘柄コード（`.T` 等サフィックス除去済み。4桁数字 + 末尾アルファベット `174A` 等含む） |
| OPEN | FLOAT64 | NULLABLE | 始値（9:00） |
| HIGH | FLOAT64 | NULLABLE | 前場高値 |
| LOW | FLOAT64 | NULLABLE | 前場安値 |
| CLOSE | FLOAT64 | NULLABLE | 前場終値（11:30） |
| VOLUME | INTEGER | NULLABLE | 前場出来高 |
| TURNOVER | FLOAT64 | NULLABLE | 概算売買代金（CLOSE × VOLUME） |
| LOADED_AT | DATETIME | NULLABLE | BQ格納日時（JST） |

**データ収集フロー:**
```
[yfinance（11:45 JST 起動、後場開始前に完了）]
  → BigQuery (STOCK.STOCK_PRICE_YF_AM) に WRITE_APPEND
```
- **収集スクリプト**: `scripts/stock_price_yf_am_load.py`
- **Cloud Run Job**: `stock-price-yf-am-load`（us-west1、4並列タスク）
- **スケジュール**: 毎週月〜金 11:45 JST（`stock-price-yf-am-load-daily`）
- **パーティション**: DATE（日次）／クラスタリング: TICKER
- **タイムアウト**: 2400s（40分）。11:45 起動 → 最悪 12:25 完了で後場開始（12:30）前に収まる設計

**注意事項:**
- TICKER は `STOCK_CODE_LIST` の全銘柄（TSE/NSE/SSE/FSE）。末尾アルファベット ticker（174A 等）も含む
- 価格は FLOAT64（`STOCK_PRICE_JQUANTS` 準拠）
- TURNOVER は `CLOSE × VOLUME` の概算値（VWAP ベースではない）
- 前場に出来高ゼロの銘柄は OHLCV が NULL になる可能性あり
- 土日・祝日・特別休日（大晦日・年始）はスクリプト内でスキップ
- 既存 `STOCK_PRICE`（大引け後 17:00 取得）との JOIN: `STOCK_PRICE.YEARDATE = STOCK_PRICE_YF_AM.DATE AND STOCK_PRICE.TICKER = STOCK_PRICE_YF_AM.TICKER`


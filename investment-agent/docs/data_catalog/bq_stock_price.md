# STOCK.STOCK_PRICE
> 親: [`data_catalog.md`](../../data_catalog.md)

**`STOCK.STOCK_PRICE` スキーマ:**

| カラム名 | 型 | モード | 説明 |
|---------|-----|--------|------|
| YEARDATE | DATE | REQUIRED | 取引日 |
| TICKER | STRING | REQUIRED | 銘柄コード（4桁、`.T`なし。例: `7203`） |
| OPEN | INTEGER | NULLABLE | 始値（円、四捨五入済み整数） |
| HIGH | INTEGER | NULLABLE | 高値（円、四捨五入済み整数） |
| LOW | INTEGER | NULLABLE | 安値（円、四捨五入済み整数） |
| CLOSE | INTEGER | NULLABLE | 終値（円、四捨五入済み整数） |
| VOLUME | INTEGER | NULLABLE | 出来高（四捨五入済み整数） |

**データ収集フロー:**
```
[yfinance] → CSV (YYYYMMDD.csv)
  → GCS (gs://stock_data_1930932/stock_price/new/)
  → BigQuery (STOCK.STOCK_PRICE) にWRITE_APPEND
  → GCS (gs://stock_data_1930932/stock_price/history/) に移動
```
- **収集スクリプト**: `scripts/stock_price_load.py`
- **Cloud Run Job**: `stock-price-load`（us-west1）
- **スケジュール**: 毎週月〜金 17:00 JST（`stock-price-load-daily`）

**注意事項:**
- TICKER は東証銘柄コード4桁のみ（ETF含む。4桁以外は収集時に除外済み）
- 価格・出来高は整数型（小数点以下は四捨五入済み）
- 数値化できない値はNULL
- 2010年〜のヒストリカルデータを保持

**データ範囲:**
- 期間（FROM）: 2010年以降（固定）
- 期間（TO）: 日次更新。実際の最新日付は BQ クエリで確認すること
  ```sql
  SELECT MAX(YEARDATE) FROM `gmailpj-357912.STOCK.STOCK_PRICE`
  ```
- 1日あたりレコード数: 約 4,400〜4,500 銘柄（上場銘柄数により変動）

**確認履歴（確認のたびに追記）:**
| 確認日 | 総行数 | 最新日付 |
|--------|--------|---------|
| 2026-02-22 | 約 24,110,124件 | 2026-02-20 |
| 2026-05-20 | 24,358,847件 | 2026-05-19 | ※2026-03-09の全銘柄二重ロード(4,439件)をdedup済み |

---


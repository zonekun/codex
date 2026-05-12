# STOCK.INDEX_PRICE
> 親: [`data_catalog.md`](../../data_catalog.md)

**`STOCK.INDEX_PRICE` スキーマ:**

| カラム名 | 型 | モード | 説明 |
|---------|-----|--------|------|
| DATE | DATE | REQUIRED | 取引日 |
| INDEX_CODE | STRING | REQUIRED | 指数コード（`0000`=TOPIX、`N225`=日経225 等。J-Quants 74コード + N225） |
| OPEN | FLOAT64 | NULLABLE | 始値 |
| HIGH | FLOAT64 | NULLABLE | 高値 |
| LOW | FLOAT64 | NULLABLE | 安値 |
| CLOSE | FLOAT64 | NULLABLE | 終値 |
| SOURCE | STRING | NULLABLE | データソース（`jquants` / `yfinance`） |
| LOADED_AT | DATETIME | NULLABLE | BQ格納日時（JST） |

**データ収集フロー:**
```
[J-Quants /v1/indices?date=YYYYMMDD（74指数）] → Python
[yfinance ^N225（日経225）]                    → Python
  → BigQuery (STOCK.INDEX_PRICE) に WRITE_APPEND（重複スキップ済み）
```
- **収集スクリプト**: `scripts/index_price_load.py`
- **更新方式**: WRITE_APPEND（INDEX_CODE='0000' の既存日チェックで重複防止）
- **更新タイミング**: Cloud Run Job `index-price-load`（平日毎日 17:30 JST）
- **履歴データ**: 2016-03-05〜（J-Quants Standard プラン最古日）/ 日経225 は yfinance 提供範囲
> ※ `equities/bars/daily` の実データ開始日は 2016-03-28（それ以前は 400 返し・実測確認済み）
- **主要コード**: `0000`=TOPIX, `N225`=日経225, その他73コード=TOPIXサブインデックス系
- **バックフィル**: `python scripts/index_price_load.py --backfill`

---


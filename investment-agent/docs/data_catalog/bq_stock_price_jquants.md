# STOCK.STOCK_PRICE_JQUANTS
> 親: [`data_catalog.md`](../../data_catalog.md)

**`STOCK.STOCK_PRICE_JQUANTS` スキーマ:**

| カラム名 | 型 | モード | 説明 |
|---------|-----|--------|------|
| DATE | DATE | REQUIRED | 取引日（パーティションキー） |
| CODE5 | STRING | REQUIRED | 銘柄コード5桁（例: `72030`）★PK |
| TICKER | STRING | REQUIRED | 銘柄コード4桁（例: `7203`）既存テーブルとのJOIN用 |
| IS_PREFERRED | BOOL | REQUIRED | 優先株等フラグ（CODE5末尾桁が`0`以外=True） |
| OPEN | FLOAT64 | NULLABLE | 始値（調整前） |
| HIGH | FLOAT64 | NULLABLE | 高値（調整前） |
| LOW | FLOAT64 | NULLABLE | 安値（調整前） |
| CLOSE | FLOAT64 | NULLABLE | 終値（調整前） |
| VOLUME | FLOAT64 | NULLABLE | 出来高（調整前） |
| TURNOVER | FLOAT64 | NULLABLE | 売買代金（円） |
| STOP_HIGH | STRING | NULLABLE | ストップ高フラグ（`0`=通常, `1`=ストップ高） |
| STOP_LOW | STRING | NULLABLE | ストップ安フラグ（`0`=通常, `1`=ストップ安） |
| ADJ_FACTOR | FLOAT64 | NULLABLE | 調整係数（株式分割1:2の場合 0.5） |
| ADJ_OPEN | FLOAT64 | NULLABLE | 調整済み始値 |
| ADJ_HIGH | FLOAT64 | NULLABLE | 調整済み高値 |
| ADJ_LOW | FLOAT64 | NULLABLE | 調整済み安値 |
| ADJ_CLOSE | FLOAT64 | NULLABLE | 調整済み終値 |
| ADJ_VOLUME | FLOAT64 | NULLABLE | 調整済み出来高 |
| LOADED_AT | DATETIME | NULLABLE | BQ格納日時（JST） |

**データ収集フロー:**
```
[J-Quants /v2/equities/bars/daily?date=YYYYMMDD（全銘柄・pagination対応）]
  → BigQuery (STOCK.STOCK_PRICE_JQUANTS) に DELETE + WRITE_APPEND（リラン安全）
```
- **収集スクリプト**: `scripts/stock_price_jquants_load.py`
- **Cloud Run Job**: `stock-price-jquants-load`（us-west1）
- **スケジュール**: 毎週月〜金 18:00 JST（`stock-price-jquants-load-scheduler`）
- **更新方式**: 対象日の既存レコード DELETE → WRITE_APPEND
- **バックフィル**: `PYTHONUTF8=1 python scripts/stock_price_jquants_load.py --backfill`
- **パーティション**: DATE（日次）／クラスタリング: TICKER
- **既存 `STOCK_PRICE`（yfinance）との関係**: 並行維持。JOIN時は `LEFT(CODE5, 4) = TICKER` または `TICKER` カラムで結合

**注意事項:**
- 東証上場銘柄のみ（地方取引所単独上場銘柄は対象外）
- 取引が存在しない日の四本値・取引高・売買代金は NULL
- 2020/10/1 は東証システム障害により全銘柄 NULL
- 調整は株式分割・併合のみ対応（その他コーポレートアクション非対応）

**確認履歴（確認のたびに追記）:**
| 確認日 | 総行数 | 最新日付 |
|--------|--------|---------|
| （未確認） | — | — |

---


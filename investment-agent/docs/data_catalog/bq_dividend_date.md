# STOCK.DIVIDEND_DATE
> 親: [`data_catalog.md`](../../data_catalog.md)

**`STOCK.DIVIDEND_DATE` スキーマ:**

| カラム名 | 型 | モード | 説明 |
|---------|-----|--------|------|
| TICKER | STRING | REQUIRED | 銘柄コード（4桁、`.T`なし。例: `7203`） |
| CUM_DATE | DATE | REQUIRED | 権利付き最終日（この日まで保有で権利取得） |
| EX_DATE | DATE | REQUIRED | 権利落ち日（CUM_DATEの翌営業日） |
| DIVIDEND | NUMERIC(10,2) | NULLABLE | 1株当たり配当金額（円、小数点以下2桁） |
| LOADED_AT | DATETIME | NULLABLE | BQ格納日時（JST） |

**データ収集フロー:**
```
[yfinance Ticker.dividends] → Python（CUM_DATE を前営業日逆算）
  → BigQuery (STOCK.DIVIDEND_DATE) に WRITE_TRUNCATE
```
- **収集スクリプト**: `scripts/dividend_date_load.py`
- **更新方式**: WRITE_TRUNCATE（全銘柄一括上書き）
- **更新タイミング**: 明示的な指示があったときのみ（または毎月第3営業日 自動）
- **トリガー構成**: Cloud Scheduler（毎日 cron）→ Cloud Functions `dividend-date-scheduler`（`functions/dividend_date_scheduler/`）→ 第3営業日のみ Cloud Run Job `dividend-date-load` を起動（詳細: `docs/knowledges/tools/034_data_load_jobs.md`）

**注意事項:**
- EX_DATE: yfinance が返す権利落ち日をそのまま使用
- CUM_DATE: EX_DATE の前営業日を jpholiday で算出
- 無配銘柄・yfinance にデータがない銘柄はレコードなし
- データ範囲は銘柄により異なる（トヨタ等大型株は 1999年〜）


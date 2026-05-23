# STOCK.EARNINGS_DISCLOSURE_CALENDAR
> 親: [`data_catalog.md`](../../data_catalog.md)

### `STOCK.EARNINGS_DISCLOSURE_CALENDAR` — 決算開示カレンダー（予定/実績）

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR` | 決算開示カレンダー（予定/実績） | 日次 | 予定: ghostrader.net、実績: fin_summary |

**用途:**
- 業績予想の発表パターンの企業別把握
- 決算発表予定時間の予測

**スキーマ:**

| カラム | 型 | NULL | 説明 |
|--------|-----|------|------|
| TICKER | STRING | NO | 銘柄コード（4桁） |
| FISCAL_YEAR_END | DATE | YES | 決算期末日（決算月不明時はNULL） |
| QUARTER | STRING | NO | `1Q` / `中間決算` / `3Q` / `本決算` |
| CATEGORY | STRING | NO | `R`=決算（Result）/ `F`=予想（Forecast） |
| RECORD_TYPE | STRING | NO | `S`=予定（Scheduled）/ `A`=実績（Actual） |
| REVISION_SEQ | INT64 | NO | 業績予想修正の連番（R=1固定、F=1,2,3...） |
| DISCLOSURE_DATE | DATE | YES | 開示日 |
| DISCLOSURE_TIME | TIME | YES | 開示時刻 |
| SOURCE | STRING | NO | `ghostrader` / `jquants` / `tdnet`（旧実績） |
| LOADED_AT | DATETIME | NO | BQ格納日時（JST） |

**論理PK:** `(TICKER, FISCAL_YEAR_END, QUARTER, CATEGORY, RECORD_TYPE, REVISION_SEQ)`

**パーティション:** `DISCLOSURE_DATE` / **クラスタリング:** `TICKER, CATEGORY, RECORD_TYPE`

**有効なレコード種別:**

| CATEGORY | RECORD_TYPE | 説明 | ソース |
|:--|:--|:--|:--|
| R | S | 決算発表予定 | ghostrader.net（日次蓄積） |
| R | A | 決算短信（実績） | fin_summary WHERE TYPE_OF_DOCUMENT LIKE '%FinancialStatements%' |
| F | A | 業績予想修正（実績） | fin_summary WHERE TYPE_OF_DOCUMENT IN ('EarnForecastRevision', 'REITEarnForecastRevision') |
| F | S | 存在しない | — |

**QUARTER マッピング:**

| ghostrader | fin_summary | 本テーブル |
|:--|:--|:--|
| 本決算 | FY | 本決算 |
| 1Q | 1Q | 1Q |
| 中間決算 | 2Q | 中間決算 |
| 3Q | 3Q | 3Q |

**データ収集フロー:**
```
■ 予定（RECORD_TYPE='S'）
  ghostrader.net（当日 + 翌営業日の2ページ）
    → 日次スクレイピング → BQ WRITE_APPEND
    ※ 翌営業日分のみ公開。毎日蓄積が必須

■ 実績（RECORD_TYPE='A'）
  STOCK.fin_summary
    WHERE TYPE_OF_DOCUMENT LIKE '%FinancialStatements%'
       OR TYPE_OF_DOCUMENT IN ('EarnForecastRevision', 'REITEarnForecastRevision')
    → CATEGORY マッピング: FinancialStatements→R / EarnForecastRevision系→F
    → Rは同一銘柄×開示日×FYでDISCLOSURE_NUMBER DESC最新1件
    → Fは同一銘柄×FY×QUARTERでDISCLOSURE_NUMBER ASC連番
    → STOCK_CODE_LISTのTSE内国株式3市場 + DELISTED_STOCKS旧東証主要市場に限定
```


**よく使うクエリ例:**

```sql
-- 来週の決算発表予定銘柄（未来日）
SELECT TICKER, DISCLOSURE_DATE, QUARTER, DISCLOSURE_TIME
FROM `gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR`
WHERE RECORD_TYPE = 'S'
  AND CATEGORY = 'R'
  AND DISCLOSURE_DATE BETWEEN '2026-05-11' AND '2026-05-15'
ORDER BY DISCLOSURE_DATE, DISCLOSURE_TIME

-- 特定銘柄の決算予定日を取得
SELECT TICKER, DISCLOSURE_DATE
FROM `gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR`
WHERE TICKER IN ('6651', '6637', '7022')
  AND RECORD_TYPE = 'S'
  AND CATEGORY = 'R'
  AND DISCLOSURE_DATE >= CURRENT_DATE()
ORDER BY DISCLOSURE_DATE
```

> **注意**: ghostrader.net は当日+翌営業日のみ公開。2日以上先の予定は蓄積済みデータのみ。直近の予定が欠落している場合はスクレイパーの実行遅延を疑う。

---


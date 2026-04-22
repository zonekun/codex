# 日証金データ ソース切替（2025-09-26 境界）— MARGIN_BALANCE / SHINA_RATES 共通

**カテゴリ**: data
**作成日**: 2026-04-02
**ステータス**: 有効
**関連ファイル**: `data_catalog.md`（MARGIN_BALANCE / SHINA_RATES セクション）, `scripts/shina_margin_balance_load.py`

## 概要

`STOCK.MARGIN_BALANCE`（貸借残高）と `STOCK.SHINA_RATES`（品貸料）は、いずれも **2025-09-26 を境にデータソースが異なり**、提供項目・レコード構成が変化する。

- **〜2025-09-25**: 日証金の過去データ検索（`https://www.taisyaku.jp/app/stock/search#search-result`）から取得
- **2025-09-26〜**: 日証金の日次CSV（`shina.csv` / `zandaka.csv`）から取得

過去データ検索で提供される項目は日次CSVより少ないため、一部カラムが期間によって NULL / 値あり に分かれる。

---

## MARGIN_BALANCE（貸借残高）

### フィールド別 値有無マトリクス

| カラム名 | 〜2025-09-25（過去データ） | 2025-09-26〜（日次CSV） | 備考 |
|---------|--------------------------|------------------------|------|
| YEARDATE | 値あり | 値あり | |
| TICKER | 値あり | 値あり | |
| NAME | 値あり | 値あり | |
| MARKET_NAME | `東証` | `東証およびＰＴＳ` / `名証` | レコード粒度が変わる |
| FIN_NEW_STOCKS | 値あり | 値あり | |
| FIN_REPAY_STOCKS | 値あり | 値あり | |
| FIN_BAL_STOCKS | 値あり | 値あり | |
| LEND_NEW_STOCKS | 値あり | 値あり | |
| LEND_REPAY_STOCKS | 値あり | 値あり | |
| LEND_BAL_STOCKS | 値あり | 値あり | |
| NET_BAL_STOCKS | 値あり | 値あり | |
| FIN_NEW_AMT | 値あり | 値あり | |
| FIN_REPAY_AMT | 値あり | 値あり | |
| FIN_BAL_AMT | 値あり | 値あり | |
| LEND_NEW_AMT | 値あり | 値あり | |
| LEND_REPAY_AMT | 値あり | 値あり | |
| LEND_BAL_AMT | 値あり | 値あり | |
| NET_BAL_AMT | 値あり | 値あり | |
| SYS_CREDIT_BUY | **NULL** | **NULL** | 両期間とも未提供 |
| SYS_CREDIT_SELL | **NULL** | **NULL** | 両期間とも未提供 |
| FIN_RIGHTS_DROP | **NULL** | 値あり（ほぼ0） | |
| LEND_RIGHTS_DROP | **NULL** | 値あり（ほぼ0） | |
| DIFF_FIN_UP | **NULL** | 値あり | |
| DIFF_FIN_DOWN | **NULL** | 値あり | |
| DIFF_LEND_DOWN | **NULL** | 値あり | |
| DIFF_LEND_UP | **NULL** | 値あり | |
| TOTAL_DAYS | **NULL** | 値あり | |
| FIN_NEW_DAYS | **NULL** | 値あり | |
| FIN_REPAY_DAYS | **NULL** | 値あり | |
| FIN_BAL_DAYS | **NULL** | 値あり | |
| LEND_NEW_DAYS | **NULL** | 値あり | |
| LEND_REPAY_DAYS | **NULL** | 値あり | |
| LEND_BAL_DAYS | **NULL** | 値あり | |

### レコード粒度の変化

| 期間 | 1日あたりレコード数 | MARKET_NAME |
|------|-------------------|-------------|
| 〜2025-09-25 | 1（銘柄あたり） | `東証` |
| 2025-09-26〜 | 2（銘柄あたり） | `東証およびＰＴＳ` + `名証` |

- 名証レコードは大半の銘柄でほぼゼロ値（残高0、回転日数0.0 等）
- 時系列分析で前後期間を結合する場合、`MARKET_NAME = '東証およびＰＴＳ'` でフィルタするか、同一日付で SUM する必要がある

---

## SHINA_RATES（品貸料）

### フィールド別 値有無マトリクス

| カラム名 | 〜2025-09-25（過去データ） | 2025-09-26〜（日次CSV） | 備考 |
|---------|--------------------------|------------------------|------|
| YEARDATE | 値あり | 値あり | |
| SETTLEMENT_DATE | **NULL** | 値あり | 決算行のみ（決済日） |
| TICKER | 値あり | 値あり | |
| NAME | 値あり | 値あり | 日次CSVの決算行は略称になる場合あり |
| MARKET_TYPE | `東証` | `東証` | 変化なし |
| SETTLEMENT_REASON | **NULL** | 値あり | 決算行のみ（例: `決算`） |
| SETTLEMENT_EVENT_DATE | **NULL** | 値あり | 決算行のみ（決算等の日付） |
| LOAN_PRICE | 値あり | 値あり | |
| EXCESS_STOCK_VOLUME | **NULL** | 値あり | 決算行のみ（貸株超過株数） |
| MAX_RATE | 値あり | 値あり | |
| DAILY_RATE | 値あり（NULLも多い） | 値あり（NULLも多い） | 逆日歩なし銘柄はNULL |
| DAILY_DAYS | 値あり | 値あり | |
| PREV_DAILY_RATE | **NULL** | 値あり | 決算行のみ（前日品貸料率） |
| REMARKS | **NULL** | 値あり | 決算行のみ（例: `満額`） |
| RESTRICTIONS | **NULL** | 値あり | 例: `注意喚起`, `停止`, `申込停止` |
| BID_RATIO_RANK | 値あり（`-` or A〜F） | 値あり（`-` or A〜F、決算行は空） | |

### レコード粒度の変化

| 期間 | 1日あたりレコード数 | 説明 |
|------|-------------------|------|
| 〜2025-09-25 | 1（銘柄あたり） | 通常行のみ |
| 2025-09-26〜 | 1〜2（銘柄あたり） | 通常行 + 決算行（決算銘柄のみ） |

- 2025-09-26〜 は**決算（権利確定日接近）銘柄に限り、同一日に2レコード**が存在する:
  - **通常行**: `SETTLEMENT_DATE` = NULL、`EXCESS_STOCK_VOLUME` = NULL
  - **決算行**: `SETTLEMENT_DATE` に決済日、`SETTLEMENT_REASON` = `決算`、`EXCESS_STOCK_VOLUME` に貸株超過株数、`REMARKS` = `満額` 等
- 決算でない銘柄は新旧とも1日1レコード
- MARKET_TYPE は前後とも `東証` のまま変化なし（MARGIN_BALANCE と異なる）

---

## 注意事項（両テーブル共通）

- 分析で 2025-09-25 以前と以後を跨ぐ場合、NULLカラムの扱いに注意
- MARGIN_BALANCE の回転日数（`TOTAL_DAYS` 等）や更新差金（`DIFF_*`）は 2025-09-25 以前は NULL のため対象外
- SHINA_RATES の決算関連情報（`SETTLEMENT_DATE`, `SETTLEMENT_REASON`, `EXCESS_STOCK_VOLUME`, `PREV_DAILY_RATE`, `REMARKS`）は 2025-09-25 以前は NULL
- `SYS_CREDIT_BUY` / `SYS_CREDIT_SELL`（MARGIN_BALANCE）は両期間とも NULL — ソース側で提供されていないカラム
- 境界日 2025-09-26 当日は新フォーマット

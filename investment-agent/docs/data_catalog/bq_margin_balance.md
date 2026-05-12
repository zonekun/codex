# STOCK.MARGIN_BALANCE
> 親: [`data_catalog.md`](../../data_catalog.md)

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.MARGIN_BALANCE` | 貸借残高（信用取引残高）日次データ | 日次 | taisyaku.jp zandaka.csv → ローカル加工 → BQロード |

**`STOCK.MARGIN_BALANCE` スキーマ:**

| カラム名 | 型 | 説明 | 元CSV列名 |
|---------|-----|------|----------|
| YEARDATE | DATE | 申込日 | 申込日 |
| TICKER | STRING | 銘柄コード | 銘柄コード |
| NAME | STRING | 銘柄名 | 銘柄名 |
| MARKET_NAME | STRING | 取引所区分名（例: 東証およびＰＴＳ） | 取引所区分名 |
| FIN_NEW_STOCKS | INTEGER | 融資新規株数 | 融資新規株数 |
| FIN_REPAY_STOCKS | INTEGER | 融資返済株数 | 融資返済株数 |
| FIN_BAL_STOCKS | INTEGER | 融資残高株数 | 融資残高株数 |
| LEND_NEW_STOCKS | INTEGER | 貸株新規株数 | 貸株新規株数 |
| LEND_REPAY_STOCKS | INTEGER | 貸株返済株数 | 貸株返済株数 |
| LEND_BAL_STOCKS | INTEGER | 貸株残高株数 | 貸株残高株数 |
| NET_BAL_STOCKS | INTEGER | 差引残高株数（融資残高 - 貸株残高） | 差引残高株数 |
| FIN_NEW_AMT | INTEGER | 融資新規金額 | 融資新規金額 |
| FIN_REPAY_AMT | INTEGER | 融資返済金額 | 融資返済金額 |
| FIN_BAL_AMT | INTEGER | 融資残高金額 | 融資残高金額 |
| LEND_NEW_AMT | INTEGER | 貸株新規金額 | 貸株新規金額 |
| LEND_REPAY_AMT | INTEGER | 貸株返済金額 | 貸株返済金額 |
| LEND_BAL_AMT | INTEGER | 貸株残高金額 | 貸株残高金額 |
| NET_BAL_AMT | INTEGER | 差引残高金額 | 差引残高金額 |
| SYS_CREDIT_BUY | INTEGER | 制度信用・買残高株数 | 制度信用・買残高株数 |
| SYS_CREDIT_SELL | INTEGER | 制度信用・売残高株数 | 制度信用・売残高株数 |
| FIN_RIGHTS_DROP | INTEGER | 融資権利落額 | 融資権利落額 |
| LEND_RIGHTS_DROP | INTEGER | 貸株権利落額 | 貸株権利落額 |
| DIFF_FIN_UP | INTEGER | 合計・更新差金 融資値上り | 合計・更新差金融資値上り |
| DIFF_FIN_DOWN | INTEGER | 合計・更新差金 融資値下り | 合計・更新差金融資値下り |
| DIFF_LEND_DOWN | INTEGER | 合計・更新差金 貸株値下り | 合計・更新差金貸株値下り |
| DIFF_LEND_UP | INTEGER | 合計・更新差金 貸株値上り | 合計・更新差金貸株値上り |
| TOTAL_DAYS | FLOAT | 総合回転日数 | 総合回転日数 |
| FIN_NEW_DAYS | FLOAT | 融資・新規回転日数 | 融資・新規回転日数 |
| FIN_REPAY_DAYS | FLOAT | 融資・返済回転日数 | 融資・返済回転日数 |
| FIN_BAL_DAYS | FLOAT | 融資・残高回転日数 | 融資・残高回転日数 |
| LEND_NEW_DAYS | FLOAT | 貸株・新規回転日数 | 貸株・新規回転日数 |
| LEND_REPAY_DAYS | FLOAT | 貸株・返済回転日数 | 貸株・返済回転日数 |
| LEND_BAL_DAYS | FLOAT | 貸株・残高回転日数 | 貸株・残高回転日数 |

**注意事項:**
- ソースURL（日次）: `https://www.taisyaku.jp/data/zandaka.csv`（日証金の貸借残高情報）
- ソースURL（過去データ）: `https://www.taisyaku.jp/app/stock/search#search-result`（日証金の銘柄別過去データ検索）
- **2025-09-25 以前のデータは過去データ検索から取得**しており、日次 zandaka.csv とは提供項目が異なる。詳細は下記「データソース切替」参照
- CSVは Shift-JIS エンコーディング。収集時に元CSVの2列目・6列目・7列目を除去済み
- YEARDATE は `YYYY/MM/DD` 形式（例: `2026/02/19`）から DATE 型に変換済み
- TICKER は4桁に限らずアルファベット混在あり（例: `130A`, `133A`, `135A`）
- 回転日数（*_DAYS）は FLOAT 型（小数あり。例: `5.5`）
- リラン対策: 同一 YEARDATE のレコードは DELETE → INSERT（WRITE_APPEND）

**データソース切替（2025-09-26 境界）— フィールド値有無の詳細は [`docs/knowledges/data/003_taisyaku_source_switch.md`](docs/knowledges/data/003_taisyaku_source_switch.md) を参照:**

| 項目 | 〜2025-09-25（過去データ検索） | 2025-09-26〜（日次 zandaka.csv） |
|------|-------------------------------|----------------------------------|
| MARKET_NAME | `東証`（1日1レコード） | `東証およびＰＴＳ` + `名証`（1日2レコード） |
| 融資/貸株 新規・返済・残高（株数・金額） | 値あり | 値あり |
| NET_BAL_STOCKS / NET_BAL_AMT | 値あり | 値あり |
| SYS_CREDIT_BUY / SYS_CREDIT_SELL | **NULL** | **NULL** |
| TOTAL_DAYS, *_DAYS（回転日数 7列） | **NULL** | 値あり |
| DIFF_FIN_UP/DOWN, DIFF_LEND_UP/DOWN | **NULL** | 値あり |
| FIN_RIGHTS_DROP / LEND_RIGHTS_DROP | **NULL** | 値あり（ほぼ0） |

**データ収集フロー:**
```
scripts/shina_margin_balance_load.py（Cloud Run Job）
  ├─ shina.csv  → https://www.taisyaku.jp/data/shina.csv
  │   → Shift-JIS読込 → 先頭3行除去 → カラムリネーム → BQ SHINA_RATES へロード
  └─ zandaka.csv → https://www.taisyaku.jp/data/zandaka.csv
      → Shift-JIS読込 → 不要列除去 → カラムリネーム → BQ MARGIN_BALANCE へロード
  ※ 処理開始・終了・エラー時にメール通知
```
- **収集スクリプト**: `scripts/shina_margin_balance_load.py`
- **Cloud Run Job**: `shina-margin-balance-load`（us-west1）
- **スケジュール**: 毎週月〜金 17:00 / 20:00 JST（`shina-margin-balance-load-17/20`）


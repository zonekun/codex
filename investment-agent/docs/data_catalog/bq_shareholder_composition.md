# STOCK.SHAREHOLDER_COMPOSITION
> 親: [`data_catalog.md`](../../data_catalog.md)

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.SHAREHOLDER_COMPOSITION` | 株主構成（年次、全上場銘柄、2013-2026） | 年次（有報提出時） | `scripts/fetch_shareholder_composition.py` で EDINET XBRL → パース → BQ |

**`STOCK.SHAREHOLDER_COMPOSITION` スキーマ:**

| カラム | 型 | モード | 説明 |
|---|---|---|---|
| TICKER | STRING | REQUIRED | 4桁銘柄コード ★PK |
| FISCAL_YEAR_END | DATE | REQUIRED | 対象事業年度末日 ★PK |
| DOC_ID | STRING | NULLABLE | 有報EDINET docID |
| SUBMIT_DATE | DATE | NULLABLE | 有報提出日 |
| TOP_SHAREHOLDER_NAME | STRING | NULLABLE | 筆頭株主名 |
| TOP_SHAREHOLDER_RATIO | FLOAT64 | NULLABLE | 筆頭株主持株比率 (0.0〜1.0) |
| TOP_SHAREHOLDER_IS_PUBLIC | BOOL | NULLABLE | 筆頭株主が国内上場企業か |
| TOP_SHAREHOLDER_TICKER | STRING | NULLABLE | 筆頭株主の ticker（上場の場合）|
| FOREIGN_RATIO | FLOAT64 | NULLABLE | 外国人持株比率 (個人+法人合計) |
| INDIVIDUAL_RATIO | FLOAT64 | NULLABLE | 個人その他持株比率（論文KOJIN） |
| FINANCIAL_INST_RATIO | FLOAT64 | NULLABLE | 金融機関持株比率（金融商品取引業者含む）|
| OTHER_CORP_RATIO | FLOAT64 | NULLABLE | その他法人持株比率 |
| TREASURY_RATIO | FLOAT64 | NULLABLE | 自己株式比率（概算: 100%-他カテゴリ）|
| TOP10_CONCENTRATION | FLOAT64 | NULLABLE | 上位10株主合計比率 |
| TOP10_NAMES_JSON | STRING | NULLABLE | 上位10株主のJSON `[{name, ratio}]` |
| HAS_ACTIVIST | BOOL | NULLABLE | TOP10にアクティビストが含まれるか |
| ACTIVIST_NAMES | STRING | NULLABLE | マッチしたアクティビスト名カンマ区切り |
| ACTIVIST_MAX_SCORE | INT64 | NULLABLE | マッチ最高スコア (0-100) |
| EXTRACTED_AT | TIMESTAMP | REQUIRED | 抽出日時（JST） |

**主キー:** `(TICKER, FISCAL_YEAR_END)` — NOT ENFORCED

**注意事項:**
- TOB予測モデル（`docs/knowledges/analysis/007_tob_ml_prediction.md`）の説明変数用
- データソース優先: GCS `edinet/{ticker}/` → EDINET API
- アクティビストマスタ: `data/master/activists.csv` + `activist_aliases.csv`
- `TOP_SHAREHOLDER_IS_PUBLIC` は正規化+Gemini判定の後処理 (`scripts/apply_shareholder_listing_flag.py`)
- 詳細: `docs/knowledges/tools/081_shareholder_composition.md`


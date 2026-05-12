# データカタログ

全ストレージのデータ定義・所在を管理するインデックスファイル。
スキーマ詳細は各テーブルの個別ファイル（`docs/data_catalog/*.md`）を参照。
データの種類を追加する際は、本ファイルのサマリテーブルに1行追加 + 個別ファイルを新規作成すること。

## ストレージ種別

| 層 | テクノロジー | 概要 |
|----|-------------|------|
| (a) | Google BigQuery | 構造化データ。SQLクエリ可能 |
| (b) | Google Cloud Storage | 大量の非構造化・半構造化データ |
| (c) | ローカル CSV (`data/csv/`) | BQ/GCS の一部をコピーして分析に使用 |
| (d) | 外部API + キャッシュ (`data/cache/`) | J-Quants, yfinance等のAPI + レスポンスキャッシュ |

## (a) BigQuery （プロジェクト: `gmailpj-357912`）

| テーブル名 | 説明 | 更新頻度 | 詳細 |
|-----------|------|---------|------|
| `STOCK.STOCK_PRICE` | 株価日次データ（OHLCV、yfinance） | 日次 | → [詳細](docs/data_catalog/bq_stock_price.md) |
| `STOCK.STOCK_PRICE_JQUANTS` | 株価四本値（J-Quants、調整済み含む） | 日次 | → [詳細](docs/data_catalog/bq_stock_price_jquants.md) |
| `STOCK.STOCK_PRICE_YF_AM` | 前場スナップショット（OHLCV + 売買代金） | 日次 | → [詳細](docs/data_catalog/bq_stock_price_yf_am.md) |
| `STOCK.INDEX_PRICE` | 株式指数日次データ（74指数 + 日経225） | 日次 | → [詳細](docs/data_catalog/bq_index_price.md) |
| `STOCK.SHINA_RATES` | 品貸料（逆日歩）日次データ | 日次 | → [詳細](docs/data_catalog/bq_shina_rates.md) |
| `STOCK.MARGIN_BALANCE` | 貸借残高（信用取引残高）日次データ | 日次 | → [詳細](docs/data_catalog/bq_margin_balance.md) |
| `STOCK.STOCK_CODE_LIST` | 上場銘柄マスタ（TICKER/STOCK_NAME/業種/市場区分） | 随時 | → [詳細](docs/data_catalog/bq_stock_code_list.md) |
| `STOCK.DELISTED_STOCKS` | 上場廃止銘柄マスタ（TOB/MBO判定・買付価格） | 随時 | → [詳細](docs/data_catalog/bq_delisted_stocks.md) |
| `STOCK.SHAREHOLDER_COMPOSITION` | 株主構成（年次、2013-2026） | 年次 | → [詳細](docs/data_catalog/bq_shareholder_composition.md) |
| `STOCK.fin_summary` | J-Quants 財務サマリー（107列） | 日次 | → [詳細](docs/data_catalog/bq_fin_summary.md) |
| `STOCK.v_fin_summary_actual_for_q_on_q` | 単独四半期P&Lビュー | ビュー | → [詳細](docs/data_catalog/bq_fin_summary.md) |
| `STOCK.TDNET_DOCUMENTS_ENHANCED` | TDnet適時開示（テキスト・Embedding・AI判定） | 日次 | → [詳細](docs/data_catalog/bq_tdnet_documents.md) |
| `STOCK.CONSENSUS` | コンセンサス（5項目）QUICK/IFIS | 随時 | → [詳細](docs/data_catalog/bq_consensus.md) |
| `STOCK.V_CONSENSUS_MERGED` | QUICK/IFISマージ済みVIEW | ビュー | → [詳細](docs/data_catalog/bq_consensus.md) |
| `STOCK.DIVIDEND_DATE` | 権利付き最終日・配当履歴 | 随時 | → [詳細](docs/data_catalog/bq_dividend_date.md) |
| `STOCK.EARNINGS_DISCLOSURE_CALENDAR` | 決算開示カレンダー（予定/実績） | 日次 | → [詳細](docs/data_catalog/bq_earnings_calendar.md) |
| `STOCK.YF_STOCK_INFO` | yfinance 銘柄属性・バリュエーション | 週次 | → [詳細](docs/data_catalog/bq_yf_stock_info.md) |
| `STOCK.SIGNAL_011_4` | 011-4戦略 日次シグナル ⛔停止中 | 日次 | → [詳細](docs/data_catalog/bq_signal_011_4.md) |
| `STOCK.PAPER_TRADE_011_4` | 011-4戦略 ペーパートレードP&L ⛔停止中 | 日次 | → [詳細](docs/data_catalog/bq_signal_011_4.md) |

## (b) Google Cloud Storage

→ [詳細](docs/data_catalog/gcs.md)

| GCSパス | 説明 | 更新頻度 |
|---------|------|---------|
| `gs://stock_data_1930932/stock_price/` | yfinance日次CSV（new/ → history/） | 日次 |
| `gs://stock_data_1930932/edinet/` | EDINET 有価証券報告書（HTML/XBRL） | 日次 |
| `gs://stock_data_1930932/edinet_xbrl/` | XBRL抽出：全銘柄の現金・有価証券 | 手動 |
| `gs://stock_data_1930932/tdnet/` | TDnet 適時開示 PDF | 日次 |
| `gs://stock_data_1930932/monthly/` | 月次開示メタ定義・抽出レコード | 手動/バッチ |
| `gs://stock_data_1930932/quarterly/` | 四半期先行指標メタ定義・レコード | 手動/バッチ |
| `gs://stock_data_1930932/monthlydata/` | TDnet月次開示データ（企業×月JSON） | 日次 |
| `gs://stock_data_1930932/earnings_model/` | 決算反応モデル関連（β/予測/精度/除外/スコア） | 随時 |
| `gs://stock_data_1930932/config/` | 月次開示収集マスタCSV | 手動 |
| `gs://stock_data_1930932/edinet_delay/` | 大量保有変更報告書 遅延提出一覧 | 一回限り |

## (c) ローカル CSV

→ [詳細](docs/data_catalog/csv.md)

| ローカルパス | 説明 | ソース |
|-------------|------|--------|
| `data/csv/stock_price.csv` | 株価日次（分析用コピー） | BQ STOCK_PRICE |
| `meta/monthly/` | 月次adapter（structure/url/extract） | GCS monthly/meta/ |
| `meta/quarterly/` | 四半期adapter（structure/extract） | GCS quarterly/meta/ |
| `data/csv/pbr_cleansed.csv` | PBR（クレンジング済み） | 事前計算 |
| `data/csv/bond_history.csv` | 債券・マクロ指標 日次 | Dropbox Excel |
| `data/master/activists.csv` | アクティビストファンド一覧 | 手動（git管理） |
| `data/master/nikkei225_constituents.csv` | 日経225構成銘柄 | Wikipedia（git管理） |
| 四季報 Excel | 東洋経済 全銘柄データ | 手動入手（四半期） |

## (d) 外部API + キャッシュ

→ [詳細](docs/data_catalog/api_cache.md)

| API / ファイル | 説明 | キャッシュ/更新 |
|---------------|------|----------------|
| J-Quants | 株価日次、財務データ | 24h TTL |
| yfinance | 株価データ（BQ補完用） | 24h TTL |
| `data/cache/yasai_price_maff.xlsx` | 農水省 野菜小売価格調査 | 週次手動 |

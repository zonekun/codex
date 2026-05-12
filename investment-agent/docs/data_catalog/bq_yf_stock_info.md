# STOCK.YF_STOCK_INFO
> 親: [`data_catalog.md`](../../data_catalog.md)

### `STOCK.YF_STOCK_INFO` — yfinance 銘柄属性・バリュエーション（週次スナップショット）

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.YF_STOCK_INFO` | yfinance Ticker.info から取得した銘柄属性・財務指標・バリュエーション | 週次 | `scripts/yf_stock_info_load.py` で WRITE_APPEND（スナップショット蓄積） |

**`STOCK.YF_STOCK_INFO` スキーマ:**

| NO | カラム名 | 型 | モード | 日本語名 | yfinanceキー |
|----|---------|-----|--------|---------|-------------|
| 1 | SYMBOL | STRING | REQUIRED | ティッカーシンボル（例: `7203.T`） | symbol |
| 2 | TICKER | STRING | REQUIRED | 証券コード4桁（`symbol` から `.T` 除去） | symbol → 加工 |
| 3 | LONG_NAME | STRING | NULLABLE | 正式社名 | longName |
| 4 | CITY | STRING | NULLABLE | 所在地（市） | city |
| 5 | ZIP | STRING | NULLABLE | 郵便番号 | zip |
| 6 | SECTOR | STRING | NULLABLE | セクター（業種） | sector |
| 7 | WEBSITE | STRING | NULLABLE | 企業公式サイト | website |
| 8 | FULL_TIME_EMPLOYEES | INTEGER | NULLABLE | 従業員数 | fullTimeEmployees |
| 9 | INDUSTRY_KEY | STRING | NULLABLE | 詳細業種用内部キー | industryKey |
| 10 | INDUSTRY_DISP | STRING | NULLABLE | 表示用の詳細業種名 | industryDisp |
| 11 | SECTOR_KEY | STRING | NULLABLE | セクター用内部キー | sectorKey |
| 12 | SECTOR_DISP | STRING | NULLABLE | 表示用のセクター名 | sectorDisp |
| 13 | IR_WEBSITE | STRING | NULLABLE | 投資家向け情報（IR）サイトURL | irWebsite |
| 14 | CURRENT_PRICE | FLOAT64 | NULLABLE | 現在値（株価） | currentPrice |
| 15 | MARKET_CAP | INTEGER | NULLABLE | 時価総額 | marketCap |
| 16 | ENTERPRISE_VALUE | INTEGER | NULLABLE | 企業価値（EV） | enterpriseValue |
| 17 | SHARES_OUTSTANDING | INTEGER | NULLABLE | 発行済株式数 | sharesOutstanding |
| 18 | BETA | FLOAT64 | NULLABLE | ベータ値（市場感応度） | beta |
| 19 | FLOAT_SHARES | INTEGER | NULLABLE | 浮動株数 | floatShares |
| 20 | IMPLIED_SHARES_OUTSTANDING | INTEGER | NULLABLE | 潜在的な発行済株式総数 | impliedSharesOutstanding |
| 21 | HELD_PERCENT_INSTITUTIONS | FLOAT64 | NULLABLE | 機関投資家の保有比率 | heldPercentInstitutions |
| 22 | TRAILING_PE | FLOAT64 | NULLABLE | PER（実績） | trailingPE |
| 23 | FORWARD_PE | FLOAT64 | NULLABLE | PER（予想） | forwardPE |
| 24 | PRICE_TO_BOOK | FLOAT64 | NULLABLE | PBR（株価純資産倍率） | priceToBook |
| 25 | PRICE_TO_SALES_TRAILING_12M | FLOAT64 | NULLABLE | PSR（株価売上高倍率） | priceToSalesTrailing12Months |
| 26 | ENTERPRISE_TO_EBITDA | FLOAT64 | NULLABLE | EV/EBITDA倍率 | enterpriseToEbitda |
| 27 | TRAILING_PEG_RATIO | FLOAT64 | NULLABLE | PEGレシオ | trailingPegRatio |
| 28 | ENTERPRISE_TO_REVENUE | FLOAT64 | NULLABLE | EV/売上高倍率 | enterpriseToRevenue |
| 29 | EBITDA | INTEGER | NULLABLE | EBITDA | ebitda |
| 30 | TOTAL_DEBT | INTEGER | NULLABLE | 有利子負債総額 | totalDebt |
| 31 | GROSS_PROFITS | INTEGER | NULLABLE | 売上総利益（粗利） | grossProfits |
| 32 | OPERATING_CASHFLOW | INTEGER | NULLABLE | 営業キャッシュフロー | operatingCashflow |
| 33 | EARNINGS_GROWTH | FLOAT64 | NULLABLE | 利益成長率（前年同期比） | earningsGrowth |
| 34 | GROSS_MARGINS | FLOAT64 | NULLABLE | 売上総利益率 | grossMargins |
| 35 | EBITDA_MARGINS | FLOAT64 | NULLABLE | EBITDAマージン | ebitdaMargins |
| 36 | OPERATING_MARGINS | FLOAT64 | NULLABLE | 営業利益率 | operatingMargins |
| 37 | PROFIT_MARGINS | FLOAT64 | NULLABLE | 純利益率 | profitMargins |
| 38 | FORWARD_EPS | FLOAT64 | NULLABLE | EPS（予想1株当たり利益） | forwardEps |
| 39 | EPS_FORWARD | FLOAT64 | NULLABLE | 予想EPS（別キー） | epsForward |
| 40 | TOTAL_REVENUE | INTEGER | NULLABLE | 売上高 | totalRevenue |
| 41 | REVENUE_GROWTH | FLOAT64 | NULLABLE | 売上高成長率 | revenueGrowth |
| 42 | RETURN_ON_ASSETS | FLOAT64 | NULLABLE | ROA（総資産利益率） | returnOnAssets |
| 43 | RETURN_ON_EQUITY | FLOAT64 | NULLABLE | ROE（自己資本利益率） | returnOnEquity |
| 44 | DEBT_TO_EQUITY | FLOAT64 | NULLABLE | 自己資本負債比率 | debtToEquity |
| 45 | TOTAL_CASH | INTEGER | NULLABLE | 現金保有額 | totalCash |
| 46 | FREE_CASHFLOW | INTEGER | NULLABLE | フリーキャッシュフロー | freeCashflow |
| 47 | BOOK_VALUE | FLOAT64 | NULLABLE | BPS（1株当たり純資産） | bookValue |
| 48 | TRAILING_EPS | FLOAT64 | NULLABLE | EPS（実績1株当たり利益） | trailingEps |
| 49 | DIVIDEND_YIELD | FLOAT64 | NULLABLE | 配当利回り | dividendYield |
| 50 | DIVIDEND_RATE | FLOAT64 | NULLABLE | 1株当たり配当金 | dividendRate |
| 51 | PAYOUT_RATIO | FLOAT64 | NULLABLE | 配当性向 | payoutRatio |
| 52 | EX_DIVIDEND_DATE | DATE | NULLABLE | 配当落日 | exDividendDate（UNIXタイムスタンプ→DATE変換） |
| 53 | LAST_DIVIDEND_VALUE | FLOAT64 | NULLABLE | 直近の配当額 | lastDividendValue |
| 54 | LAST_DIVIDEND_DATE | DATE | NULLABLE | 直近の配当落日 | lastDividendDate（UNIXタイムスタンプ→DATE変換） |
| 55 | TARGET_MEAN_PRICE | FLOAT64 | NULLABLE | アナリスト目標株価 | targetMeanPrice |
| 56 | RECOMMENDATION_KEY | STRING | NULLABLE | 推奨判断 | recommendationKey |
| 57 | OVERALL_RISK | INTEGER | NULLABLE | 総合リスクスコア | overallRisk |
| 58 | RECOMMENDATION_MEAN | FLOAT64 | NULLABLE | 推奨判断の平均（1.0=強気買い〜5.0=売り） | recommendationMean |
| 59 | NUMBER_OF_ANALYST_OPINIONS | INTEGER | NULLABLE | 分析アナリストの人数 | numberOfAnalystOpinions |
| 60 | AVERAGE_ANALYST_RATING | STRING | NULLABLE | 平均的なアナリスト評価 | averageAnalystRating |
| 61 | WEEK_52_CHANGE | FLOAT64 | NULLABLE | 過去1年間の株価変化率 | 52WeekChange |
| 62 | LAST_FISCAL_YEAR_END | DATE | NULLABLE | 直近の会計年度終了日 | lastFiscalYearEnd（UNIXタイムスタンプ→DATE変換） |
| 63 | NEXT_FISCAL_YEAR_END | DATE | NULLABLE | 次回の会計年度終了日 | nextFiscalYearEnd（UNIXタイムスタンプ→DATE変換） |
| 64 | MOST_RECENT_QUARTER | DATE | NULLABLE | 直近の四半期決算日 | mostRecentQuarter（UNIXタイムスタンプ→DATE変換） |
| 65 | EARNINGS_TIMESTAMP | DATETIME | NULLABLE | 決算発表日時（JST） | earningsTimestamp（UNIXタイムスタンプ→JST変換） |
| 66 | EARNINGS_TIMESTAMP_START | DATETIME | NULLABLE | 決算発表予想開始時刻（JST） | earningsTimestampStart（UNIXタイムスタンプ→JST変換） |
| 67 | EARNINGS_TIMESTAMP_END | DATETIME | NULLABLE | 決算発表予想終了時刻（JST） | earningsTimestampEnd（UNIXタイムスタンプ→JST変換） |
| 68 | EXCHANGE | STRING | NULLABLE | 取引所 | exchange |
| 69 | QUOTE_TYPE | STRING | NULLABLE | 銘柄タイプ | quoteType |
| - | LOADED_DATE | DATE | REQUIRED | 取込日（JST） | （自動付与） |
| - | LOADED_AT | DATETIME | REQUIRED | 取込日時（JST） | （自動付与） |

**データ収集フロー:**
```
[yfinance Ticker.info（1銘柄ずつ curl_cffi セッション経由）]
  → Python で UNIXタイムスタンプ変換・TICKER加工
  → BigQuery (STOCK.YF_STOCK_INFO) に WRITE_APPEND
```
- **収集スクリプト**: `scripts/yf_stock_info_load.py`
- **更新方式**: WRITE_APPEND（週次スナップショット蓄積。LOADED_DATE で世代管理）
- **更新タイミング**: 週次（スケジューラ設定後に記載更新）

**注意事項:**
- TICKER は `symbol`（例: `7203.T`）から `.T` を除去して4桁コード抽出
- UNIXタイムスタンプ系カラム（exDividendDate, lastFiscalYearEnd 等）は JST で DATE/DATETIME に変換
- yfinance に info がない銘柄（上場廃止・データ未対応等）はスキップ
- 同一 LOADED_DATE のデータが重複する可能性あり（リラン時）。最新スナップショットの取得は `WHERE LOADED_DATE = (SELECT MAX(LOADED_DATE) FROM ...)` で

**確認履歴（確認のたびに追記）:**
| 確認日 | 総行数 | 最新LOADED_DATE |
|--------|--------|----------------|
| （未確認） | — | — |


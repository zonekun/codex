# データカタログ

全ストレージのデータ定義・所在を管理するファイル。
データの種類を追加するたびに本ファイルを更新すること。

## ストレージ種別

| 層 | テクノロジー | 概要 |
|----|-------------|------|
| (a) | Google BigQuery | 構造化データ。SQLクエリ可能 |
| (b) | Google Cloud Storage | 大量の非構造化・半構造化データ |
| (c) | ローカル CSV (`data/csv/`) | BQ/GCS の一部をコピーして分析に使用 |
| (d) | 外部API + キャッシュ (`data/cache/`) | J-Quants, yfinance等のAPI + レスポンスキャッシュ |

## データ一覧

> ※ バリエーションは順次追加予定。以下は初期構成の例示。

### (a) BigQuery （プロジェクト: `gmailpj-357912`）

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.STOCK_PRICE` | 株価日次データ（OHLCV） | 日次 | Cloud Run Job `stock-price-load`（月〜金 17:00 JST）で yfinance → GCS → BQ |
| `gmailpj-357912.STOCK.SHINA_RATES` | 品貸料（逆日歩）日次データ | 日次 | Cloud Run Job `shina-margin-balance-load`（月〜金 17:00/20:00 JST）で taisyaku.jp → BQ |
| `gmailpj-357912.STOCK.MARGIN_BALANCE` | 貸借残高（信用取引残高）日次データ | 日次 | Cloud Run Job `shina-margin-balance-load`（月〜金 17:00/20:00 JST）で taisyaku.jp → BQ |
| `gmailpj-357912.STOCK.STOCK_CODE_LIST` | 上場銘柄マスタ（銘柄コード・業種・市場区分） | 随時 | TSE: Cloud Run Job `stock-code-list-load`（毎月第3営業日 20:00 JST）。FSE/SSE/NSE: Webスクレイピング |
| `gmailpj-357912.STOCK.fin_summary` | J-Quants 財務サマリー（/fins/summary） | 日次 | Cloud Run Job `jquants-fin-summary` で WRITE_APPEND ロード |
| `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED` | TDnet適時開示書類のテキスト・チャンク・埋め込みベクトル | 日次 | `tdnet-load-daily`（火〜土 02:00 JST）でロード、`ai_processing_flow` Workflows で AI判定 |
| `gmailpj-357912.STOCK.CONSENSUS` | 楽天証券 IFIS コンセンサス（経常利益） | 随時 | `scripts/update_conse_rakuten.py` で Selenium スクレイピング → ロード。**明示的な指示があったときのみ更新** |
| `gmailpj-357912.STOCK.DIVIDEND_DATE` | 権利付き最終日・権利落ち日・配当履歴 | 随時 | `scripts/dividend_date_load.py` で yfinance → BQ（WRITE_TRUNCATE）。**明示的な指示があったときのみ更新** |
| `gmailpj-357912.STOCK.v_fin_summary_actual_for_q_on_q` | fin_summary から実績値のみを抽出し、累積値を単独四半期値に変換したビュー | ビュー（実体なし） | `scripts/create_fin_summary_view.py` で作成。fin_summary の更新で自動反映 |
| `gmailpj-357912.STOCK.INDEX_PRICE` | 株式指数日次データ（J-Quants 全74指数 + 日経225） | 日次 | Cloud Run Job `index-price-load`（月〜金 17:30 JST）で J-Quants `/indices` + yfinance `^N225` → BQ |
| `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS` | 株価四本値（J-Quants `/v2/equities/bars/daily`、調整済み含む） | 日次 | Cloud Run Job `stock-price-jquants-load`（月〜金 18:00 JST）で J-Quants → BQ |
| `gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR` | 決算開示カレンダー（予定/実績） | 日次 | 予定: Cloud Run Job `earnings-schedule-load`（1-13日・18-31日 05:00 JST 土曜除く）、実績: TDNET_DOCUMENTS_ENHANCED から ETL |
| `gmailpj-357912.STOCK.STOCK_PRICE_YF_AM` | 前場スナップショット（OHLCV + 概算売買代金） | 日次 | Cloud Run Job `stock-price-yf-am-load`（月〜金 11:45 JST、4並列タスク）で yfinance → BQ |
| `gmailpj-357912.STOCK.YF_STOCK_INFO` | yfinance 銘柄属性・バリュエーション（週次スナップショット） | 週次 | `scripts/yf_stock_info_load.py` で yfinance Ticker.info → BQ WRITE_APPEND |

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

---

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

---

### `STOCK.STOCK_PRICE_YF_AM` — 前場スナップショット（yfinance）

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.STOCK_PRICE_YF_AM` | 前場終了時点の OHLCV + 概算売買代金 | 日次 | Cloud Run Job `stock-price-yf-am-load`（月〜金 11:45 JST、4並列タスク）で yfinance → BQ |

**`STOCK.STOCK_PRICE_YF_AM` スキーマ:**

| カラム名 | 型 | モード | 説明 |
|---------|-----|--------|------|
| DATE | DATE | REQUIRED | 取引日（パーティションキー） |
| TICKER | STRING | REQUIRED | 銘柄コード（`.T` 等サフィックス除去済み。4桁数字 + 末尾アルファベット `174A` 等含む） |
| OPEN | FLOAT64 | NULLABLE | 始値（9:00） |
| HIGH | FLOAT64 | NULLABLE | 前場高値 |
| LOW | FLOAT64 | NULLABLE | 前場安値 |
| CLOSE | FLOAT64 | NULLABLE | 前場終値（11:30） |
| VOLUME | INTEGER | NULLABLE | 前場出来高 |
| TURNOVER | FLOAT64 | NULLABLE | 概算売買代金（CLOSE × VOLUME） |
| LOADED_AT | DATETIME | NULLABLE | BQ格納日時（JST） |

**データ収集フロー:**
```
[yfinance（11:45 JST 起動、後場開始前に完了）]
  → BigQuery (STOCK.STOCK_PRICE_YF_AM) に WRITE_APPEND
```
- **収集スクリプト**: `scripts/stock_price_yf_am_load.py`
- **Cloud Run Job**: `stock-price-yf-am-load`（us-west1、4並列タスク）
- **スケジュール**: 毎週月〜金 11:45 JST（`stock-price-yf-am-load-daily`）
- **パーティション**: DATE（日次）／クラスタリング: TICKER
- **タイムアウト**: 2400s（40分）。11:45 起動 → 最悪 12:25 完了で後場開始（12:30）前に収まる設計

**注意事項:**
- TICKER は `STOCK_CODE_LIST` の全銘柄（TSE/NSE/SSE/FSE）。末尾アルファベット ticker（174A 等）も含む
- 価格は FLOAT64（`STOCK_PRICE_JQUANTS` 準拠）
- TURNOVER は `CLOSE × VOLUME` の概算値（VWAP ベースではない）
- 前場に出来高ゼロの銘柄は OHLCV が NULL になる可能性あり
- 土日・祝日・特別休日（大晦日・年始）はスクリプト内でスキップ
- 既存 `STOCK_PRICE`（大引け後 17:00 取得）との JOIN: `STOCK_PRICE.YEARDATE = STOCK_PRICE_YF_AM.DATE AND STOCK_PRICE.TICKER = STOCK_PRICE_YF_AM.TICKER`

---

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.SHINA_RATES` | 品貸料（逆日歩）日次データ | 日次 | taisyaku.jp shina.csv → ローカル加工 → BQロード |

**`STOCK.SHINA_RATES` スキーマ:**

| カラム名 | 型 | 説明 | 元CSV列名 |
|---------|-----|------|----------|
| YEARDATE | DATE | 貸借申込日 | 貸借申込日 |
| SETTLEMENT_DATE | DATE | 決済日 | 決済日 |
| TICKER | STRING | 銘柄コード | コード |
| NAME | STRING | 銘柄名 | 銘柄名 |
| MARKET_TYPE | STRING | 取引所区分（例: 東証） | 取引所区分 |
| SETTLEMENT_REASON | STRING | 決算事由（例: 決算） | 決算事由 |
| SETTLEMENT_EVENT_DATE | DATE | 決算等の日付 | 決算等 |
| LOAN_PRICE | FLOAT | 貸借値段（円） | 貸借値段（円） |
| EXCESS_STOCK_VOLUME | INTEGER | 貸株超過株数 | 貸株超過株数 |
| MAX_RATE | FLOAT | 最高料率（円） | 最高料率（円） |
| DAILY_RATE | FLOAT/STRING | 当日品貸料率（円）。`*****` = 満額で非公開 | 当日品貸料率（円） |
| DAILY_DAYS | INTEGER/STRING | 当日品貸日数。`*****` = 満額で非公開 | 当日品貸日数 |
| PREV_DAILY_RATE | FLOAT/STRING | 前日品貸料率（円）。`*****` = 満額で非公開 | 前日品貸料率（円） |
| REMARKS | STRING | 備考（例: 満額） | 備考 |
| RESTRICTIONS | STRING | 制限（例: 停止, 注意） | 制限 |
| BID_RATIO_RANK | STRING | 応札倍率ランク（例: F, D, B） | 応札倍率ランク |

**注意事項:**
- ソースURL（日次）: `https://www.taisyaku.jp/data/shina.csv`（日証金の貸借取引情報）
- ソースURL（過去データ）: `https://www.taisyaku.jp/app/stock/search#search-result`（日証金の銘柄別過去データ検索）
- **2025-09-25 以前のデータは過去データ検索から取得**しており、日次 shina.csv とは提供項目が異なる。詳細は下記「データソース切替」参照
- CSVは Shift-JIS エンコーディング。先頭3行はヘッダ前の説明行で、収集時に除去済み
- `*****` は「満額」（品貸料が上限到達）を意味し、数値変換時にNULLとなる
- DAILY_RATE, DAILY_DAYS, PREV_DAILY_RATE は満額銘柄で `*****` が入るためNULLになりうる
- 日付列 YEARDATE は `YYYYMMDD` 形式（例: `20260219`）から DATE 型に変換済み
- リラン対策: 同一 YEARDATE のレコードは DELETE → INSERT（WRITE_APPEND）

**データソース切替（2025-09-26 境界）— フィールド値有無の詳細は [`docs/knowledges/data/003_taisyaku_source_switch.md`](docs/knowledges/data/003_taisyaku_source_switch.md) を参照:**

| 項目 | 〜2025-09-25（過去データ検索） | 2025-09-26〜（日次 shina.csv） |
|------|-------------------------------|-------------------------------|
| レコード粒度 | 1日1レコード/銘柄 | 1〜2レコード/銘柄（決算銘柄は通常行+決算行） |
| SETTLEMENT_DATE / SETTLEMENT_REASON / SETTLEMENT_EVENT_DATE | **NULL** | 決算行のみ値あり |
| EXCESS_STOCK_VOLUME | **NULL** | 決算行のみ値あり |
| PREV_DAILY_RATE | **NULL** | 決算行のみ値あり |
| REMARKS | **NULL** | 決算行のみ値あり（例: `満額`） |
| RESTRICTIONS | **NULL** | 値あり（例: `注意喚起`, `停止`） |
| LOAN_PRICE / MAX_RATE / DAILY_RATE / DAILY_DAYS / BID_RATIO_RANK | 値あり | 値あり |

---

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

---

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.STOCK_CODE_LIST` | 上場銘柄マスタ | 随時 | TSE: JPX公開データ。FSE/SSE/NSE: `scripts/update_regional_codes.py` |

**`STOCK.STOCK_CODE_LIST` スキーマ:**

全カラムが STRING 型。

| カラム名 | 型 | 説明 |
|---------|-----|------|
| TICKER | STRING (REQUIRED) | 銘柄コード（4桁数字 `"1301"` またはアルファナメリック `"130A"` ） |
| EXCHANGE | STRING (REQUIRED) | 証券取引所（`TSE`, `FSE`, `SSE`, `NSE`） |
| STOCK_NAME | STRING | 銘柄名（企業名・ファンド名・ETF名など） |
| MARKET_CATEGORY | STRING | 市場・商品区分（プライム, スタンダード, グロース, 本則市場, アンビシャス, プレミア, メイン, ネクスト等） |
| INDUSTRY_33_CODE | STRING | 33業種コード（TSE 33業種分類に準拠） |
| INDUSTRY_33_CATEGORY | STRING | 33業種区分（水産・農林業, 食料品, 電気機器など） |
| INDUSTRY_17_CODE | STRING | 17業種コード（TSE 17業種分類に準拠） |
| INDUSTRY_17_CATEGORY | STRING | 17業種区分（食品, 建設・資材, IT・サービス他など） |
| SIZE_CODE | STRING | 規模コード（TSE専用。FSE/SSE/NSEは NULL） |
| SIZE_CATEGORY | STRING | 規模区分（TOPIX Core30, Large70, Mid400, Small等。FSE/SSE/NSEは NULL） |

**主キー:** `(TICKER, EXCHANGE)` — NOT ENFORCED（BigQuery上は非強制）

**注意事項:**
- すべての列が STRING 型。コード値の比較は文字列比較で行うこと（例: `TICKER = '7203'`）
- ETF・ETN等、業種が定義されていない銘柄は `INDUSTRY_*` / `SIZE_*` 列が NULL になる
- テーブル名・カラム名はすべて大文字で指定すること
- TSE のデータソース: 日本取引所グループ（JPX）公開データ。Cloud Run Job `stock-code-list-load` で毎月第3営業日 20:00 JST に自動更新（`scripts/stock_code_list_load.py`）
- **トリガー構成**: Cloud Scheduler（毎日 cron）→ Cloud Functions `stock-code-list-scheduler`（`functions/stock_code_list_scheduler/`）→ 第3営業日のみ Cloud Run Job `stock-code-list-load` を起動。cron では第N営業日を表現できないため Functions を経由する設計（詳細: `docs/knowledges/tools/034_data_load_jobs.md`）
- FSE/SSE/NSE のデータソース: 各取引所 Web サイト（Webスクレイピング）
- FSE/SSE/NSE の業種は TSE 33/17業種に変換済み。取得不可の場合は NULL
- SSE（札証）は Web 一覧ページに業種情報がないため `INDUSTRY_*` は NULL
- スクレイパー実装: `src/collector/regional_exchange.py`（ページ構造・注意事項は `docs/knowledges/tools/scraper.md` 参照）

**更新スクリプト（FSE/SSE/NSE）:**
```bash
# 全取引所を更新
uv run python scripts/update_regional_codes.py

# 個別取引所を更新
uv run python scripts/update_regional_codes.py --exchange FSE  # 福証
uv run python scripts/update_regional_codes.py --exchange SSE  # 札証
uv run python scripts/update_regional_codes.py --exchange NSE  # 名証（Playwright必須）

# ドライラン（BQ書き込みなし）
uv run python scripts/update_regional_codes.py --dry-run
```

**データ範囲:**
- 総行数: 上場・廃止により変動。実際の件数は BQ クエリで確認すること
  ```sql
  SELECT EXCHANGE, COUNT(*) FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST` GROUP BY EXCHANGE
  ```
- 市場区分（`MARKET_CATEGORY`）の種類: TSE=プライム/スタンダード/グロース/ETF・ETN/PRO Market/REIT等、FSE=本則/Q-Board/Fukuoka PRO Market、SSE=本則市場/アンビシャス、NSE=プレミア市場/メイン市場/ネクスト市場

**確認履歴（確認のたびに追記）:**
| 確認日 | 総行数 | TSE | FSE | SSE | NSE |
|--------|--------|-----|-----|-----|-----|
| 2026-02-23 | 4,541件 | 4,435件 | 29件 | 18件 | 59件 |

---

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.DELISTED_STOCKS` | 上場廃止銘柄マスタ（廃止理由・TOB/MBO判定・買付価格等） | 随時 | `scripts/scrape_jpx_delisted.py`（廃止情報）+ `scripts/fetch_tob_announcements.py`（TOB詳細をEDINETから抽出）|

**`STOCK.DELISTED_STOCKS` スキーマ:**

| カラム名 | 型 | モード | 説明 |
|---------|-----|--------|------|
| TICKER | STRING | REQUIRED | 銘柄コード4桁 ★PK |
| DELISTING_DATE | DATE | REQUIRED | 上場廃止日 ★PK |
| COMPANY_NAME | STRING | NULLABLE | 会社名 |
| MARKET_SEGMENT | STRING | NULLABLE | 市場区分（プライム/スタンダード/グロース等） |
| DELISTING_REASON | STRING | NULLABLE | 廃止理由（「他社による買収」「ＭＢＯ」「支配株主等による買収」「株式移転」等） |
| FISCAL_YEAR | INTEGER | NULLABLE | 廃止年（JPXの一覧年度） |
| IS_TOB_MBO | BOOLEAN | NULLABLE | TOB/MBO/スクイーズアウトによる廃止か（Geminiが TDNET テキスト180日分を分析して自動判定）|
| TOB_ANNOUNCEMENT_DATE | DATE | NULLABLE | TOB公告日（EDINET公開買付届出書の「公告日」） |
| TOB_PRICE | FLOAT64 | NULLABLE | 買付価格（円/普通株式1株） |
| PRICE_BEFORE_ANNOUNCEMENT | FLOAT64 | NULLABLE | 公告前営業日の終値（届出書記載値） |
| PREMIUM_RATE | FLOAT64 | NULLABLE | プレミアム率（例: 0.3412 = 34.12%） |
| TOB_TYPE | STRING | NULLABLE | `OTHER`（他社株TOB・支配株主等） / `MBO`（マネジメント・バイアウト） / `SELF`（自己株式TOB。通常本テーブルには該当なし）|
| TOB_ACQUIRER | STRING | NULLABLE | 公開買付者名（EDINET `FullNameOrNameOfFilerOfNotificationCoverPage`） |
| TOB_DOC_ID | STRING | NULLABLE | 公開買付届出書の EDINET docID（例: `S100X1E8`） |
| IS_PAPER_TOB_LABEL | BOOL | NULLABLE | 論文正解ラベル該当フラグ。条件: `TOB_PRICE IS NOT NULL AND PREMIUM_RATE >= 0.05 AND TOB_TYPE IN ('OTHER', 'MBO')`。TOB予測モデル（analysis/007）の学習データに使用。EDINET取得失敗でも手動裏取り済TOBを含める |

**主キー:** `(TICKER, DELISTING_DATE)` — NOT ENFORCED

**注意事項:**
- データ範囲: 2017年〜現在（JPX公開データ）。件数は BQ クエリで確認
- `IS_TOB_MBO = TRUE` の銘柄のみ TOB_* カラムが埋まる。FALSE（株式交換・合併・救済・テクニカル廃止）は NULL
- TOB予測モデル（`docs/knowledges/analysis/007_tob_ml_prediction.md`）の正解ラベルに利用
- `TOB_TYPE` 判定は `DELISTING_REASON` の文字列マッチで決定（XBRL本文は信頼性低いため不採用）
- 更新方法: 2段階
  1. `scripts/scrape_jpx_delisted.py` で JPX 上場廃止一覧をスクレイピング → INSERT
  2. `scripts/fetch_tob_announcements.py --missing-only` で未抽出の TOB_* を EDINET からバックフィル → UPDATE
- **バックフィル実績 (2026-04-20)**: 577件の IS_TOB_MBO=TRUE レコードに対し **299件 (51.8%) で TOB_DOC_ID 取得成功**、うち **288件が IS_PAPER_TOB_LABEL=TRUE**。失敗278件の内訳は分類のみで「TOB無関係」断定は強制廃止8件のみ。詳細は `docs/knowledges/analysis/007_tob_ml_prediction.md`

---

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

---

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.fin_summary` | J-Quants 財務サマリー（/fins/summary） | 日次 | Cloud Run Job `jquants-fin-summary` で WRITE_APPEND ロード |

**`STOCK.fin_summary` — J-Quants API `/v2/fins/summary` 仕様**

> API仕様参照: https://jpx.gitbook.io/j-quants-ja/api-reference/statements

---

**■ TYPE_OF_DOCUMENT（開示書類種別）**

書類の期間・連結区分・会計基準の組み合わせ。全45種。

| パターン | 説明 |
|---------|------|
| `{期間}FinancialStatements_{連結区分}_{会計基準}` | 定期開示（決算・四半期） |
| `DividendForecastRevision` | 配当予想修正 |
| `EarnForecastRevision` | 業績予想修正 |
| `REITDividendForecastRevision` | REIT配当予想修正 |
| `REITEarnForecastRevision` | REIT業績予想修正 |

**期間プレフィックス:** `FY`（通期）/ `1Q`〜`3Q`（四半期）/ `OtherPeriod`（変則期間）

**連結区分:** `Consolidated`（連結）/ `NonConsolidated`（単体）

**会計基準:**
| 値 | 内容 | 経常利益 |
|----|------|---------|
| `JP` | 日本基準（J-GAAP） | あり |
| `IFRS` | 国際財務報告基準 | **なし（空欄）** |
| `US` | 米国基準（US-GAAP） | **なし（空欄）** |
| `JMIS` | 修正国際基準 | あり |
| `Foreign` | 外国会計基準（外国株等） | ケースによる |
| `REIT` | J-REIT（不動産投資信託） | なし |

完全一覧（45種）:
```
FYFinancialStatements_Consolidated_JP / US / NonConsolidated_JP
1QFinancialStatements_Consolidated_JP / US / NonConsolidated_JP
2QFinancialStatements_Consolidated_JP / US / NonConsolidated_JP
3QFinancialStatements_Consolidated_JP / US / NonConsolidated_JP
OtherPeriodFinancialStatements_Consolidated_JP / US / NonConsolidated_JP
FYFinancialStatements_Consolidated_JMIS / 1Q / 2Q / 3Q / OtherPeriod
FYFinancialStatements_NonConsolidated_IFRS / 1Q / 2Q / 3Q / OtherPeriod
FYFinancialStatements_Consolidated_IFRS / 1Q / 2Q / 3Q / OtherPeriod
FYFinancialStatements_NonConsolidated_Foreign / 1Q / 2Q / 3Q / OtherPeriod
FYFinancialStatements_Consolidated_Foreign / 1Q / 2Q / 3Q / OtherPeriod
FYFinancialStatements_Consolidated_REIT
DividendForecastRevision / EarnForecastRevision
REITDividendForecastRevision / REITEarnForecastRevision
```

**■ TYPE_OF_CURRENT_PERIOD（当期種別）**

`1Q` / `2Q` / `3Q` / `4Q` / `5Q` / `FY`

---

**■ 全カラム一覧（107列）**

**【開示メタ情報】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| DISCLOSED_DATE | DiscDate | DATE | 開示日 |
| DISCLOSED_TIME | DiscTime | TIME | 開示時刻 |
| LOCAL_CODE | Code | STRING | 銘柄コード（4桁。API は5桁で返すが末尾除去済み） |
| DISCLOSURE_NUMBER | DiscNo | STRING | 開示番号（昇順ソートキー） |
| TYPE_OF_DOCUMENT | DocType | STRING | 書類種別（上記参照） |
| TYPE_OF_CURRENT_PERIOD | CurPerType | STRING | 当期種別（1Q/2Q/3Q/4Q/5Q/FY） |
| CURRENT_PERIOD_START_DATE | CurPerSt | DATE | 当期開始日 |
| CURRENT_PERIOD_END_DATE | CurPerEn | DATE | 当期終了日 |
| CURRENT_FISCAL_YEAR_START_DATE | CurFYSt | DATE | 当会計年度開始日 |
| CURRENT_FISCAL_YEAR_END_DATE | CurFYEn | DATE | 当会計年度終了日 |
| NEXT_FISCAL_YEAR_START_DATE | NxtFYSt | DATE | 次会計年度開始日（空欄あり） |
| NEXT_FISCAL_YEAR_END_DATE | NxtFYEn | DATE | 次会計年度終了日（空欄あり） |

**【連結 実績（PL・BS・CF）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| NET_SALES | Sales | INTEGER | 売上高（IFRS/US-GAAP では売上収益） |
| OPERATING_PROFIT | OP | INTEGER | 営業利益 |
| ORDINARY_PROFIT | OdP | INTEGER | 経常利益（**IFRS・US-GAAP は空欄**） |
| PROFIT | NP | INTEGER | 当期純利益（親会社株主帰属） |
| EARNINGS_PER_SHARE | EPS | FLOAT | EPS（一株当たり利益） |
| DILUTED_EARNINGS_PER_SHARE | DEPS | FLOAT | 希薄化後EPS |
| TOTAL_ASSETS | TA | INTEGER | 総資産 |
| EQUITY | Eq | INTEGER | 純資産（自己資本） |
| EQUITY_TO_ASSET_RATIO | EqAR | FLOAT | 自己資本比率（%） |
| BOOK_VALUE_PER_SHARE | BPS | FLOAT | BPS（一株当たり純資産） |
| CASH_FLOWS_FROM_OPERATING_ACTIVITIES | CFO | INTEGER | 営業CF |
| CASH_FLOWS_FROM_INVESTING_ACTIVITIES | CFI | INTEGER | 投資CF |
| CASH_FLOWS_FROM_FINANCING_ACTIVITIES | CFF | INTEGER | 財務CF |
| CASH_AND_EQUIVALENTS | CashEq | INTEGER | 現金及び現金同等物 |

**【連結 配当実績】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| RESULT_DIVIDEND_PER_SHARE_1ST_QUARTER | Div1Q | FLOAT | 一株配当（第1四半期末） |
| RESULT_DIVIDEND_PER_SHARE_2ND_QUARTER | Div2Q | FLOAT | 一株配当（第2四半期末・中間） |
| RESULT_DIVIDEND_PER_SHARE_3RD_QUARTER | Div3Q | FLOAT | 一株配当（第3四半期末） |
| RESULT_DIVIDEND_PER_SHARE_FISCAL_YEAR_END | DivFY | FLOAT | 一株配当（期末） |
| RESULT_DIVIDEND_PER_SHARE_ANNUAL | DivAnn | FLOAT | 一株配当（年間合計） |
| DISTRIBUTIONS_PER_UNIT_REIT | DivUnit | FLOAT | 一口当たり分配金（REIT） |
| RESULT_TOTAL_DIVIDEND_PAID_ANNUAL | DivTotalAnn | INTEGER | 配当金総額（年間） |
| RESULT_PAYOUT_RATIO_ANNUAL | PayoutRatioAnn | FLOAT | 配当性向（年間、%） |

**【連結 配当予想（当期）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| FORECAST_DIVIDEND_PER_SHARE_1ST_QUARTER | FDiv1Q | FLOAT | 予想一株配当（第1四半期末） |
| FORECAST_DIVIDEND_PER_SHARE_2ND_QUARTER | FDiv2Q | FLOAT | 予想一株配当（第2四半期末） |
| FORECAST_DIVIDEND_PER_SHARE_3RD_QUARTER | FDiv3Q | FLOAT | 予想一株配当（第3四半期末） |
| FORECAST_DIVIDEND_PER_SHARE_FISCAL_YEAR_END | FDivFY | FLOAT | 予想一株配当（期末） |
| FORECAST_DIVIDEND_PER_SHARE_ANNUAL | FDivAnn | FLOAT | 予想一株配当（年間合計） |
| FORECAST_DISTRIBUTIONS_PER_UNIT_REIT | FDivUnit | FLOAT | 予想一口当たり分配金（REIT） |
| FORECAST_TOTAL_DIVIDEND_PAID_ANNUAL | FDivTotalAnn | INTEGER | 予想配当金総額（年間） |
| FORECAST_PAYOUT_RATIO_ANNUAL | FPayoutRatioAnn | FLOAT | 予想配当性向（年間、%） |

**【連結 配当予想（翌期）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| NEXT_YEAR_FORECAST_DIVIDEND_PER_SHARE_1ST_QUARTER | NxFDiv1Q | FLOAT | 翌期予想一株配当（Q1末） |
| NEXT_YEAR_FORECAST_DIVIDEND_PER_SHARE_2ND_QUARTER | NxFDiv2Q | FLOAT | 翌期予想一株配当（Q2末） |
| NEXT_YEAR_FORECAST_DIVIDEND_PER_SHARE_3RD_QUARTER | NxFDiv3Q | FLOAT | 翌期予想一株配当（Q3末） |
| NEXT_YEAR_FORECAST_DIVIDEND_PER_SHARE_FISCAL_YEAR_END | NxFDivFY | FLOAT | 翌期予想一株配当（期末） |
| NEXT_YEAR_FORECAST_DIVIDEND_PER_SHARE_ANNUAL | NxFDivAnn | FLOAT | 翌期予想一株配当（年間） |
| NEXT_YEAR_FORECAST_DISTRIBUTIONS_PER_UNIT_REIT | NxFDivUnit | FLOAT | 翌期予想一口分配金（REIT） |
| NEXT_YEAR_FORECAST_PAYOUT_RATIO_ANNUAL | NxFPayoutRatioAnn | FLOAT | 翌期予想配当性向（%） |

**【連結 業績予想（当期 第2四半期）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| FORECAST_NET_SALES_2ND_QUARTER | FSales2Q | INTEGER | 予想売上高（第2四半期累計） |
| FORECAST_OPERATING_PROFIT_2ND_QUARTER | FOP2Q | INTEGER | 予想営業利益（第2四半期累計） |
| FORECAST_ORDINARY_PROFIT_2ND_QUARTER | FOdP2Q | INTEGER | 予想経常利益（第2四半期累計） |
| FORECAST_PROFIT_2ND_QUARTER | FNP2Q | INTEGER | 予想純利益（第2四半期累計） |
| FORECAST_EARNINGS_PER_SHARE_2ND_QUARTER | FEPS2Q | FLOAT | 予想EPS（第2四半期累計） |

**【連結 業績予想（翌期 第2四半期）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| NEXT_YEAR_FORECAST_NET_SALES_2ND_QUARTER | NxFSales2Q | INTEGER | 翌期予想売上高（Q2累計） |
| NEXT_YEAR_FORECAST_OPERATING_PROFIT_2ND_QUARTER | NxFOP2Q | INTEGER | 翌期予想営業利益（Q2累計） |
| NEXT_YEAR_FORECAST_ORDINARY_PROFIT_2ND_QUARTER | NxFOdP2Q | INTEGER | 翌期予想経常利益（Q2累計） |
| NEXT_YEAR_FORECAST_PROFIT_2ND_QUARTER | NxFNp2Q | INTEGER | 翌期予想純利益（Q2累計） |
| NEXT_YEAR_FORECAST_EARNINGS_PER_SHARE_2ND_QUARTER | NxFEPS2Q | FLOAT | 翌期予想EPS（Q2累計） |

**【連結 業績予想（当期 通期）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| FORECAST_NET_SALES | FSales | INTEGER | 予想売上高（通期） |
| FORECAST_OPERATING_PROFIT | FOP | INTEGER | 予想営業利益（通期） |
| FORECAST_ORDINARY_PROFIT | FOdP | INTEGER | 予想経常利益（通期） |
| FORECAST_PROFIT | FNP | INTEGER | 予想純利益（通期） |
| FORECAST_EARNINGS_PER_SHARE | FEPS | FLOAT | 予想EPS（通期） |

**【連結 業績予想（翌期 通期）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| NEXT_YEAR_FORECAST_NET_SALES | NxFSales | INTEGER | 翌期予想売上高（通期） |
| NEXT_YEAR_FORECAST_OPERATING_PROFIT | NxFOP | INTEGER | 翌期予想営業利益（通期） |
| NEXT_YEAR_FORECAST_ORDINARY_PROFIT | NxFOdP | INTEGER | 翌期予想経常利益（通期） |
| NEXT_YEAR_FORECAST_PROFIT | NxFNp | INTEGER | 翌期予想純利益（通期） |
| NEXT_YEAR_FORECAST_EARNINGS_PER_SHARE | NxFEPS | FLOAT | 翌期予想EPS（通期） |

**【会計変更フラグ（BOOLEAN）】**

| BQカラム名 | API略称 | 説明 | 備考 |
|-----------|--------|------|------|
| MATERIAL_CHANGES_IN_SUBSIDIARIES | MatChgSub | 重要な子会社の異動 | |
| SIGNIFICANT_CHANGES_IN_THE_SCOPE_OF_CONSOLIDATION | SigChgInC | 連結範囲の重要な変更 | **2024-07-22以前は空欄** |
| CHANGES_BASED_ON_REVISIONS_OF_ACCOUNTING_STANDARD | ChgByASRev | 会計基準等の改正に伴う変更 | |
| CHANGES_OTHER_THAN_ONES_BASED_ON_REVISIONS_OF_ACCOUNTING_STANDARD | ChgNoASRev | 会計方針の変更（基準改正以外） | |
| CHANGES_IN_ACCOUNTING_ESTIMATES | ChgAcEst | 会計上の見積りの変更 | |
| RETROSPECTIVE_RESTATEMENT | RetroRst | 修正再表示 | |

**【株式数】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| NUMBER_OF_ISSUED_AND_OUTSTANDING_SHARES_AT_THE_END_OF_FISCAL_YEAR_INCLUDING_TREASURY_STOCK | ShOutFY | INTEGER | 発行済株式数（自己株式含む、期末） |
| NUMBER_OF_TREASURY_STOCK_AT_THE_END_OF_FISCAL_YEAR | TrShFY | INTEGER | 自己株式数（期末） |
| AVERAGE_NUMBER_OF_SHARES | AvgSh | INTEGER | 加重平均株式数 |

**【単体 実績（PL・BS）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| NON_CONSOLIDATED_NET_SALES | NCSales | INTEGER | 単体 売上高 |
| NON_CONSOLIDATED_OPERATING_PROFIT | NCOP | INTEGER | 単体 営業利益 |
| NON_CONSOLIDATED_ORDINARY_PROFIT | NCOdP | INTEGER | 単体 経常利益 |
| NON_CONSOLIDATED_PROFIT | NCNP | INTEGER | 単体 当期純利益 |
| NON_CONSOLIDATED_EARNINGS_PER_SHARE | NCEPS | FLOAT | 単体 EPS |
| NON_CONSOLIDATED_TOTAL_ASSETS | NCTA | INTEGER | 単体 総資産 |
| NON_CONSOLIDATED_EQUITY | NCEq | INTEGER | 単体 純資産 |
| NON_CONSOLIDATED_EQUITY_TO_ASSET_RATIO | NCEqAR | FLOAT | 単体 自己資本比率（%） |
| NON_CONSOLIDATED_BOOK_VALUE_PER_SHARE | NCBPS | FLOAT | 単体 BPS |

**【単体 業績予想（当期 第2四半期・通期）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| FORECAST_NON_CONSOLIDATED_NET_SALES_2ND_QUARTER | FNCSales2Q | INTEGER | 単体 予想売上高（Q2累計） |
| FORECAST_NON_CONSOLIDATED_OPERATING_PROFIT_2ND_QUARTER | FNCOP2Q | INTEGER | 単体 予想営業利益（Q2累計） |
| FORECAST_NON_CONSOLIDATED_ORDINARY_PROFIT_2ND_QUARTER | FNCOdP2Q | INTEGER | 単体 予想経常利益（Q2累計） |
| FORECAST_NON_CONSOLIDATED_PROFIT_2ND_QUARTER | FNCNP2Q | INTEGER | 単体 予想純利益（Q2累計） |
| FORECAST_NON_CONSOLIDATED_EARNINGS_PER_SHARE_2ND_QUARTER | FNCEPS2Q | FLOAT | 単体 予想EPS（Q2累計） |
| FORECAST_NON_CONSOLIDATED_NET_SALES | FNCSales | INTEGER | 単体 予想売上高（通期） |
| FORECAST_NON_CONSOLIDATED_OPERATING_PROFIT | FNCOP | INTEGER | 単体 予想営業利益（通期） |
| FORECAST_NON_CONSOLIDATED_ORDINARY_PROFIT | FNCOdP | INTEGER | 単体 予想経常利益（通期） |
| FORECAST_NON_CONSOLIDATED_PROFIT | FNCNP | INTEGER | 単体 予想純利益（通期） |
| FORECAST_NON_CONSOLIDATED_EARNINGS_PER_SHARE | FNCEPS | FLOAT | 単体 予想EPS（通期） |

**【単体 業績予想（翌期 第2四半期・通期）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_NET_SALES_2ND_QUARTER | NxFNCSales2Q | INTEGER | 単体 翌期予想売上高（Q2累計） |
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_OPERATING_PROFIT_2ND_QUARTER | NxFNCOP2Q | INTEGER | 単体 翌期予想営業利益（Q2累計） |
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_ORDINARY_PROFIT_2ND_QUARTER | NxFNCOdP2Q | INTEGER | 単体 翌期予想経常利益（Q2累計） |
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_PROFIT_2ND_QUARTER | NxFNCNP2Q | INTEGER | 単体 翌期予想純利益（Q2累計） |
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_EARNINGS_PER_SHARE_2ND_QUARTER | NxFNCEPS2Q | FLOAT | 単体 翌期予想EPS（Q2累計） |
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_NET_SALES | NxFNCSales | INTEGER | 単体 翌期予想売上高（通期） |
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_OPERATING_PROFIT | NxFNCOP | INTEGER | 単体 翌期予想営業利益（通期） |
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_ORDINARY_PROFIT | NxFNCOdP | INTEGER | 単体 翌期予想経常利益（通期） |
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_PROFIT | NxFNCNP | INTEGER | 単体 翌期予想純利益（通期） |
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_EARNINGS_PER_SHARE | NxFNCEPS | FLOAT | 単体 翌期予想EPS（通期） |

---

**データ収集フロー:**
```
J-Quants API V2 /fins/summary（日付ループ + ページネーション）
  → scripts/jquants_get_fin_summary.py
  → Cloud Run Job: jquants-fin-summary（us-west1、毎日21:00 JST）
  → BigQuery STOCK.fin_summary（WRITE_APPEND）
```

**注意事項:**
- **経常利益は IFRS・US-GAAP では空欄**（OdP / NCOdP）。会計基準でフィルタ必須
- **ORDINARY_PROFIT が空の場合**: `TYPE_OF_DOCUMENT` に `IFRS` または `US` を含む行
- **累積値に注意**: 財務数値は当期累計（Q1=Q1、Q2=Q1+Q2、Q3=Q1+Q2+Q3）。単独四半期への変換は差分計算が必要
- **LOCAL_CODE は4桁**（API が返す5桁コードの末尾 "0" を除去済み）
- **WRITE_APPEND**: 同一日付を二重実行すると重複ロードになる。補完実行後に日次ジョブが同日をロードしないよう注意
- **欠損値は NULL**（API は空文字列 `""` で返すが preprocess で None 変換済み）
- **SIGNIFICANT_CHANGES_IN_THE_SCOPE_OF_CONSOLIDATION**: 2024-07-22以前のデータは空欄
- 詳細は `docs/knowledges/tools/008_jquants_fin_summary.md` を参照

**データ範囲:**
- 期間（FROM）: 2016-02-26（既存データの最古日付）
- 期間（TO）: 毎日21:00 JST 自動更新
  ```sql
  SELECT MAX(DISCLOSED_DATE) FROM `gmailpj-357912.STOCK.fin_summary`
  ```

**確認履歴（確認のたびに追記）:**
| 確認日 | 総行数 | 最新開示日 |
|--------|--------|----------|
| 2026-02-28 | 187,003件 | 2026-02-27 |
| 2026-03-05 | 187,045件 | 2026-03-04 |
| 2026-04-09 | — | 2026-04-09 |

**欠損補完履歴:**
| 補完日 | 欠損期間 | 原因 | 追加件数 |
|--------|---------|------|---------|
| 2026-04-09 | 2026-01-01〜2026-02-26（41日） | Cloud Run Job投入漏れ | 4,091件 |
| 2026-04-09 | 2026-03-20〜2026-03-27（6日） | Cloud Run Job投入漏れ | 122件 |

---

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.v_fin_summary_actual_for_q_on_q` | 実績値のみ抽出・累積値→単独四半期値変換ビュー | ビュー（実体なし） | `scripts/create_fin_summary_view.py` で作成 |

**`STOCK.v_fin_summary_actual_for_q_on_q` — 単独四半期 P&L ビュー**

`fin_summary` の累積P&L値をQ単独値に変換したビュー。前期同四半期比（Q on Q）や四半期トレンド分析に使用する。

**用途:**
- 各Qの単独売上高・利益を直接比較（例: 2025Q2 vs 2024Q2）
- LAG() で前期累積値を引いて単独Q値を算出済みなので分析コードが簡潔になる

**ビュー定義の方針:**
- **実績値のみ**: 予想値・配当情報は除外。`TYPE_OF_CURRENT_PERIOD IN ('1Q', '2Q', '3Q', 'FY')`
- **修正開示対応**: 同一銘柄×事業年度×期区分で最新開示（DISCLOSED_DATE + DISCLOSED_TIME 降順）のみ残す
- **FY → 4Q 変換**: `QUARTER` 列は FY を 4Q に変換（元の値は `TYPE_OF_CURRENT_PERIOD` に保持）
- **半期報告企業対応**: Q1/Q3 がない場合は LAG が NULL → `COALESCE(prev, 0)` により 2Q 単独 = 2Q 累積のまま（東証には半期報告は存在しないが念のため対応済み）

**スキーマ（出力列）:**

| カラム名 | 型 | 説明 |
|---------|-----|------|
| LOCAL_CODE | STRING | 銘柄コード（4桁） |
| DISCLOSED_DATE | DATE | 開示日 |
| DISCLOSED_TIME | TIME | 開示時刻 |
| TYPE_OF_DOCUMENT | STRING | 書類種別（`fin_summary` と同じ） |
| QUARTER | STRING | 四半期ラベル（1Q/2Q/3Q/4Q。FYは4Qに変換済み） |
| TYPE_OF_CURRENT_PERIOD | STRING | 元の当期種別（1Q/2Q/3Q/FY。デバッグ用） |
| CURRENT_PERIOD_START_DATE | DATE | 当期開始日 |
| CURRENT_PERIOD_END_DATE | DATE | 当期終了日 |
| CURRENT_FISCAL_YEAR_START_DATE | DATE | 当会計年度開始日（PARTITION相当。銘柄×会計年度の識別キー） |
| CURRENT_FISCAL_YEAR_END_DATE | DATE | 当会計年度終了日 |
| NET_SALES | INTEGER | 売上高（**単独四半期値**） |
| OPERATING_PROFIT | INTEGER | 営業利益（**単独四半期値**） |
| ORDINARY_PROFIT | INTEGER | 経常利益（**単独四半期値**。IFRS/US-GAAPは空欄） |
| PROFIT | INTEGER | 当期純利益（**単独四半期値**） |
| NON_CONSOLIDATED_NET_SALES | INTEGER | 単体 売上高（**単独四半期値**） |
| NON_CONSOLIDATED_OPERATING_PROFIT | INTEGER | 単体 営業利益（**単独四半期値**） |
| NON_CONSOLIDATED_ORDINARY_PROFIT | INTEGER | 単体 経常利益（**単独四半期値**） |
| NON_CONSOLIDATED_PROFIT | INTEGER | 単体 当期純利益（**単独四半期値**） |

**クエリ例:**

```sql
-- トヨタ (7203) の直近4Qの単独売上高・営業利益
SELECT
  QUARTER,
  CURRENT_FISCAL_YEAR_START_DATE,
  NET_SALES,
  OPERATING_PROFIT
FROM `gmailpj-357912.STOCK.v_fin_summary_actual_for_q_on_q`
WHERE LOCAL_CODE = '7203'
ORDER BY CURRENT_FISCAL_YEAR_START_DATE, CURRENT_PERIOD_END_DATE

-- 前期同四半期比（Q on Q）の計算例
SELECT
  LOCAL_CODE,
  QUARTER,
  CURRENT_FISCAL_YEAR_START_DATE,
  NET_SALES,
  LAG(NET_SALES) OVER (
    PARTITION BY LOCAL_CODE, QUARTER
    ORDER BY CURRENT_FISCAL_YEAR_START_DATE
  ) AS PREV_YEAR_NET_SALES
FROM `gmailpj-357912.STOCK.v_fin_summary_actual_for_q_on_q`
```

**ビュー作成・再作成:**

```bash
# ビュー作成（初回 or 定義変更時）
PYTHONUTF8=1 python scripts/create_fin_summary_view.py

# SQL 確認のみ（BQ 非接触）
PYTHONUTF8=1 python scripts/create_fin_summary_view.py --dry-run
```

- **作成スクリプト**: `scripts/create_fin_summary_view.py`
- **ビューID**: `gmailpj-357912.STOCK.v_fin_summary_actual_for_q_on_q`
- **ベーステーブル**: `gmailpj-357912.STOCK.fin_summary`
- **設計詳細**: `docs/knowledges/tools/008_jquants_fin_summary.md` の「ビュー設計」セクション参照

**注意事項:**
- ビューなので実体データはなし。`fin_summary` の更新（毎日21:00）で自動的に最新になる
- `ORDINARY_PROFIT` は IFRS/US-GAAP の銘柄では NULL（`fin_summary` と同様）
- 連結・非連結の両方が同一行に入っている。連結のみ使う場合は `TYPE_OF_DOCUMENT LIKE '%Consolidated%'` でフィルタ
- **金額単位は円**（百万円単位ではない）。極洋(1301)で2,000億円超の値を確認済み
- `NON_CONSOLIDATED_*` は連結のみ開示企業（Q1〜Q3）では NULL になる。FY（4Q）では開示される場合あり

**作成確認履歴:**
| 確認日 | 確認内容 |
|--------|---------|
| 2026-03-07 | トヨタ(7203) 2023年度の1Q〜4Q単独値が正しいことを確認 |
| 2026-03-29 | BQ MCP でスキーマ・DDL・サンプルデータ（極洋1301、2016〜2017）を実確認。金額単位=円を確認 |

---

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED` | TDnet適時開示書類のテキスト・チャンク・埋め込みベクトル | 日次 | `tdnet-load-daily`（火〜土 02:00 JST）で BQ投入、`ai_processing_flow` Workflows で AI判定（Gemma + Gemini） |

**`STOCK.TDNET_DOCUMENTS_ENHANCED` スキーマ:**

| カラム名 | 型 | 説明 |
|---------|-----|------|
| DOC_ID | STRING (NOT NULL) | 書類識別ID（TDnet固有） |
| TICKER | STRING (NOT NULL) | 銘柄コード（4桁） |
| FILER_NAME | STRING | 提出者名（企業名） |
| FILER_ID | STRING | 提出者ID（TDnetにはEDINETコードがないためNULL） |
| SUBMISSION_DATE | DATE | 提出日（**パーティションキー**） |
| DISCLOSURE_TIME | STRING | 開示時刻（`HH:MM`、index CSV の pubdate から取得。NULL=時刻不明） |
| MAIN_CATEGORY | STRING | メインカテゴリ（AI判定結果、load時点はNULL） |
| DOC_TITLE | STRING | 書類タイトル |
| SUB_CATEGORIES | ARRAY\<STRING\> | サブカテゴリ一覧（AI判定結果、load時点は空配列） |
| PAGE_COUNT | INT64 | ページ数 |
| TEXT_LENGTH | INT64 | テキスト全体の文字数 |
| SECTION_CATEGORY | STRING | セクション区分（チャンク単位の分類） |
| CHUNK_TEXT | STRING | チャンクテキスト（分割済みテキスト） |
| EMBEDDING | ARRAY\<FLOAT64\> | テキスト埋め込みベクトル（text-embedding-004, 768次元。3カテゴリ＝決算短信/決算説明資料/月次開示のみ付与） |
| FILE_NAME | STRING | 元ファイル名 |
| EXTRACTED_AT | TIMESTAMP | 抽出日時（DEFAULT CURRENT_TIMESTAMP()） |
| **AI_STATUS** | STRING | AI判定状態（`pending` / `pending_gemma` / `pending_finalize` / `completed`）|
| **AI_PROCESSED_AT** | TIMESTAMP | AI判定完了時刻（NULL = 未判定） |

**パーティション・クラスタリング:**
- **パーティション**: `SUBMISSION_DATE`
- **クラスタリング**: `TICKER, MAIN_CATEGORY`

**新アーキ（BQロード/AI判定分離、2026-04-17〜）:**
```
[tdnet-load-daily] Cloud Run Job CPU、02:00 JST 毎日
  --job-mode=load
  Phase 0/1/4_chunk_only/5_load
  → BQ Insert（AI_STATUS='pending', MAIN/SUB/EMBEDDING=NULL）

              ↓（独立して後刻）

[ai_processing_flow] Cloud Workflows（Scheduler or 手動）
  Step 1: tdnet-ai-prepare (Cloud Run Job CPU)
    → OCR + 正規表現月次補正 + state.json（GCS）保存
    → AI_STATUS='pending_gemma'
  Step 2: TPU v6e-4 spot VM 起動（scripts/tpu_vm_startup_gemma.sh）
          + gemma_tpu_worker.py（vLLM 推論）
    → gemma_CURRENT.jsonl continuous append + resume
  Step 3: Gemma 完了 callback 待機
  Step 4: tdnet-ai-finalize (Cloud Run Job CPU)
    → Gemini Flash Batch（MAIN='決算短信' のみ、受注マージ）
    → Embedding Batch（3カテゴリ限定）
    → BQ DELETE（pending_gemma行）+ INSERT（completed）
```

**旧 tdnet-load-parallel（段階的廃止予定）**: 新アーキ稼働3ヶ月後に `tdnet-load-parallel` / `tdnet-load-recovery` を削除。

**関連知見**:
- `docs/plans/20260417_091112_tdnet_load_ai_split.md` — 改修プランv2
- `docs/knowledges/tools/013_tdnet_load.md` — ETL詳細
- `docs/knowledges/tools/013-1_ai_cost_and_gemma_poc.md` — AI コスト分析 + TPU PoC 実測（旧 074 / 074-1 統合）

**MAIN_CATEGORY / SUB_CATEGORIES の値一覧:**

`MAIN_CATEGORY` は下表のいずれか1つ。`SUB_CATEGORIES` は同じ値のリストから0個以上を設定（1文書に複数該当可）。

| 値 | 補足 |
|----|------|
| 決算短信 | |
| TOB・MBO | |
| 業績修正 | |
| 買収防衛策 | |
| 上場廃止 | |
| 継続企業疑義(GC) | |
| 決算説明資料 | |
| 自己株式取得 | |
| 役員異動（代表クラス） | |
| 配当 | |
| 第三者割当・公募増資 | |
| 分配金 | |
| 合併・組織再編 | |
| 子会社化・買収 | |
| 主要株主異動 | |
| 新株予約権発行 | |
| 株式売出し | |
| 配当変更（増減配） | |
| 中期経営計画 | |
| 株式分割・併合 | |
| 特別損益計上 | |
| 業績予想 | |
| 事業計画（グロース） | |
| 立会外分売 | |
| 監査人異動 | |
| 訴訟・法的 | |
| インシデント（災害・事故） | |
| 自己株式消却 | |
| インシデント（セキュリティ） | |
| 転換社債(CB)発行 | |
| 行政処分 | |
| DES（債権株式化） | |
| リストラ・希望退職 | |
| 不祥事・社内調査 | |
| その他（未分類） | |
| 株主優待 | |
| 提携・協業 | |
| 月次開示 | |
| 子会社設立 | |
| 大型受注・契約 | |
| 資産売却（不動産） | |
| 事業・子会社売却 | |
| 特別利益 | |
| 特別損失 | |
| 業績の重要な先行指標 | SaaS解約率・ARPU、小売の新規出店/退店数、メーカーの販売数量・出荷台数、不動産の客室稼働率・オフィス入居率など、将来業績に直結するKPIに関する記載がある場合に適用 |
| 受注高/受注残高 | 製造業・建設業等で極めて重要なシグナル。「業績の重要な先行指標」の一部だが独立カテゴリとして扱う |

**カテゴリ適用ルール:**
- `MAIN_CATEGORY`：文書全体の主題となるカテゴリを1つ設定
- `SUB_CATEGORIES`：「決算短信」「決算説明資料」等は複数の重要情報を内包することが多い。該当するカテゴリをすべて設定
- `業績の重要な先行指標` と `受注高/受注残高` は独立したカテゴリとして扱う（後者は前者に内包されるが、製造業・建設業においての重要性から分離）

**月次開示の検索クエリ（重要）:**

月次開示文書を検索する際は `MAIN_CATEGORY = '月次開示'` だけでなく **`SUB_CATEGORIES` にも必ず含める**。
銘柄によっては `MAIN_CATEGORY` が別カテゴリで `SUB_CATEGORIES` にのみ `'月次開示'` が入るケースがある（例: 3030, 3624, 9327）。

```sql
AND (
  MAIN_CATEGORY = '月次開示'
  OR EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) AS sc WHERE sc = '月次開示')
)
```

**注意事項:**
- FILER_ID は TDnet に EDINET コードが存在しないため NULL
- EMBEDDING は Google text-embedding-004 モデルによる 768次元ベクトル
- **EMBEDDING / CHUNK_TEXT は NULL になりうる**: 決算短信・決算説明資料・月次開示のみ Embedding 対象。それ以外のカテゴリはメタデータのみ（CHUNK_TEXT=NULL, EMBEDDING=NULL の1行）
- Gemini 分析モデル: `gemini-3-flash-preview`（グローバルエンドポイント）
- CHUNK_TEXT はページ・セクション単位でテキストを分割したもの（全文ではない）
- パーティション列 SUBMISSION_DATE に基づいてクエリコストの最適化が可能
- TICKER + MAIN_CATEGORY クラスタリングにより銘柄・カテゴリ別フィルタリングが高速

**DDL（参考）:**
```sql
CREATE OR REPLACE TABLE `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED` (
    DOC_ID STRING NOT NULL,
    TICKER STRING NOT NULL,
    FILER_NAME STRING,
    FILER_ID STRING,
    SUBMISSION_DATE DATE,
    DISCLOSURE_TIME STRING,
    MAIN_CATEGORY STRING,
    DOC_TITLE STRING,
    SUB_CATEGORIES ARRAY<STRING>,
    PAGE_COUNT INT64,
    TEXT_LENGTH INT64,
    SECTION_CATEGORY STRING,
    CHUNK_TEXT STRING,
    EMBEDDING ARRAY<FLOAT64>,
    FILE_NAME STRING,
    EXTRACTED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY SUBMISSION_DATE
CLUSTER BY TICKER, MAIN_CATEGORY;
```

**設計思想:**

> 原案（以下）をベースに、カラム名・型等を若干改良して上記DDLとなっている。細部のズレは無視してよい。

*EDINETテーブルとの互換性*
- EDINET用テーブル（`ir_documents_enhanced` 相当）と `UNION ALL` で結合できるよう、カラム名を合わせた設計。EDINET側の `company_name` → `FILER_NAME`、`main_category` → `MAIN_CATEGORY` にマッピング。
- `FILER_ID`（EDINETコード）は TDnet に相当値が存在しないため NULL を格納。スキーマを合わせることで `UNION ALL` クエリがシンプルになる。
  - `WHERE FILER_ID IS NULL` → TDnet文書のみ
  - `WHERE FILER_ID IS NOT NULL` → EDINET文書のみ

*`SUB_CATEGORIES ARRAY<STRING>` 採用理由*
- カンマ区切り文字列よりも `'業績予想修正' IN UNNEST(SUB_CATEGORIES)` の方が高速・正確にフィルタリングできる。

*パーティション（`SUBMISSION_DATE`）の理由*
- IR情報は時系列性が高く「直近1ヶ月の決算短信を検索」「特定四半期の業績修正を分析」等の日付範囲クエリが頻繁に発生する。パーティションにより対象日付のみスキャン → コスト・速度を大幅改善。

*クラスタリング（`TICKER, MAIN_CATEGORY`）の理由*
- `TICKER`：企業単位での絞り込みが最頻ユースケース
- `MAIN_CATEGORY`：「決算短信だけ」「業績修正だけ」の文書種別絞り込みも頻繁
- 組み合わせることで「A社の決算関連文書」のような典型クエリが高速化。Vector Searchで類似文書を発見した後の深掘り分析にも有効

---

### (b) Google Cloud Storage

| GCSパス | 説明 | 更新頻度 | 備考 |
|---------|------|---------|------|
| `gs://stock_data_1930932/stock_price/new/` | yfinanceで取得した日次CSV（BQロード前） | 日次 | ロード後 history/ に移動 |
| `gs://stock_data_1930932/stock_price/history/` | BQロード済みの日次CSV（アーカイブ） | 日次 | |
| `gs://stock_data_1930932/edinet/{証券コード}/` | EDINET 有価証券報告書（HTML） | 日次 | 詳細は下記参照 |
| `gs://stock_data_1930932/edinet_delay/backfill.csv` | 大量保有変更報告書の遅延提出一覧（2021/01〜2025/01バックフィル） | 一回限り | `scripts/edinet_delay_backfill.py`（Cloud Run Job: `edinet-delay-backfill`）。カラム: security_code, issuer_name, filer_name, obligation_date, filing_date, delay_days。日次運用分はDropbox `Edinet遅延.xlsx` に蓄積（Cloud Run Job: `edinet-delay`） |
| `gs://stock_data_1930932/edinet_xbrl/edinet_financial_{YYYYMMDD}.tsv` | XBRL抽出：全銘柄の現金・有価証券（百万円） | 手動実行時 | `scripts/edinet_xbrl_extractor.py`（Cloud Run Job: `edinet-xbrl-extractor`）で生成。清原スクリーニング用。**四季報より鮮度が高い** |
| `gs://stock_data_1930932/tdnet/{証券コード4桁}/` | TDnet 適時開示 PDF（irbank CDN 経由） | 日次 | 詳細は下記参照 |
| `gs://stock_data_1930932/estat/` | e-STAT統計データ（景気動向指数, 鉱工業生産指数等） | 月次 | e-STAT API で取得（予定） |
| `gs://stock_data_1930932/nisshokin/` | 日証金 逆日歩・品貸料・貸借データ | 日次 | ※現在はBQ直接ロード（z_shina.py）。GCSアーカイブは将来検討 |
| `gs://stock_data_1930932/monthlydata/` | TDnet月次開示データ（企業×月ごとJSON） | 日次 | `scripts/monthly_data_load.py` / Cloud Run Job `monthly-data-load` |
| `gs://stock_data_1930932/config/monthly_disclosure_master.csv` | 月次開示収集マスタ（取得方法・取得元URL） | 手動更新 | カラム: TICKER, COMPANY_NAME, DISCLOSURE_TYPE(a/b/c), IR_URL, NOTES, UPDATED_AT。a=TDnetテキストPDF, b=TDnet画像PDF, c=独自IR開示。`scripts/verify_monthly_irbank.py` の調査結果から作成 |
| `gs://stock_data_1930932/earnings_model/beta_20d.csv` | 全銘柄20日β（TOPIX対比） | 日次 | Cloud Run Job `beta-calc`（月〜金 18:30 JST）。カラム: TICKER, beta_20d, calc_date。決算反応モデル因子9で使用 |
| `gs://stock_data_1930932/earnings_model/predictions/` | 決算反応モデル予測結果JSON | 随時 | `prediction_{YYYYMMDD}_{HHMMSS}.json`（JST） |
| `gs://stock_data_1930932/earnings_model/actuals/` | 決算反応モデル答え合わせJSON | 随時 | `actual_{SAVE_DATE}_{HHMMSS}_for_{PREDICT_DATE}.json`（JST、2026-04-15〜新規約。旧: `actual_{PREDICT_DATE}_{HHMMSS}.json`） |
| `gs://stock_data_1930932/earnings_model/accuracy/` | 決算反応モデル累積精度サマリー | 随時 | `accuracy_summary.json` |
| `gs://stock_data_1930932/earnings_model/exclusions/` | 決算反応モデル学習データ除外フラグ | 随時 | `exclusions.json`（JSON array, `{ticker, predict_date, reason, added_at, removed_at}`。`scripts/earnings_model/exclusion_manager.py` で管理。詳細は `docs/knowledges/tools/076_earnings_exclusion_mechanism.md`） |

**`gs://stock_data_1930932/edinet/{証券コード}/` 詳細:**

- **格納対象**: 有価証券報告書のみ（半期報告書・四半期報告書含む）
- **ファイル形式**: HTML と XBRL の2種類が存在する
- **パス構造**: `gs://stock_data_1930932/edinet/{証券コード}/{ファイル名}`
  - `{証券コード}`: 東証4桁銘柄コード（可変）
- **ファイル名フォーマット**:
  - HTML: `{証券コード}_{書類略称}_{提出日}_{書類種別}_{EDINET文書ID}_MERGED_REPORT.html`
  - XBRL: `{証券コード}_{書類略称}_{提出日}_{書類種別}_{EDINET文書ID}_XBRL_PublicDoc_{XBRLファイル名}.xbrl`

  例:
  ```
  gs://stock_data_1930932/edinet/7203/7203_有報四_20241113_半期報告書－第121期(2024_04_01－2025_03_31)_S100UP32_MERGED_REPORT.html
  gs://stock_data_1930932/edinet/7203/7203_有報四_20241113_半期報告書－第121期(2024_04_01－2025_03_31)_S100UP32_XBRL_PublicDoc_{XBRLファイル名}.xbrl
  ```

- **収集モジュール**: `scripts/edinet_download.py`（Cloud Run Job: `edinet-download`）
- **スケジュール**: 月〜金 23:50 JST（`edinet-download-daily`）※日次で当日分を自動取得
- **GCS取得済み日付範囲**: `2024-01-04` 〜 `2025-12-26`（43,959ファイル、482日分）※2021〜2023年は取得中（2026-03-03時点）、2026年分は未取得
- **BQ（ir_documents_enhanced）**: 0件（2026-03-03時点 TRUNCATE済み。GCSファイルから再ロード予定）
- **Embedding フィルタ**: 四半期・半期報告書はメタデータのみ（CHUNK_TEXT=NULL, EMBEDDING=NULL）。有価証券報告書は15セクション除外（財務諸表・注記・監査報告書等）、コーポレートガバナンス=半分チャンク、事業等のリスク=1/4チャンク
- **ETL スクリプト**: `scripts/edinet_load_parallel.py`（Batch Prediction アーキテクチャ、ストリーミング結果処理）

**`gs://stock_data_1930932/tdnet/{証券コード4桁}/` 詳細:**

- **格納対象**: TDnet 適時開示 PDF（カテゴリ S/A/B のもの。C=不要は除外）
- **データソース**: yanoshin 非公式 API（メタデータ） + irbank CDN（PDF本体）
- **パス構造**: `gs://stock_data_1930932/tdnet/{証券コード4桁}/{ファイル名}.pdf`
- **ファイル名フォーマット**: `{日付}_{証券コード4桁}_{会社名}_{カテゴリ}_{タイトル}_{doc_id}.pdf`
- **収集モジュール**: `scripts/tdnet_download.py`（Cloud Run Job: `tdnet-download`）
- **スケジュール**: 月〜金 23:50 JST（`tdnet-download-daily`）※日次で当日分を自動取得
- **過去データ補完**: `scripts/irbank_tdnet_download.py`（Cloud Run Job: `irbank-tdnet-download`）で過去 PDF を遡及取得
- **GCS取得済み**: 2025年全期間は取得中（2026-03-04時点）

**⚠️ GCS検索時の注意（BQ未登録の場合）:**

ファイル名先頭の `{日付}` は **書類作成日ではなく TDnet インデックス掲載日**（ジョブがダウンロードした日）。
書類作成日は `doc_id`（ファイル末尾の14桁）の先頭8桁で判別できる（例: `140120260313581851` → 作成日 `20260313`）。
MBO・上場廃止前後や年度末は開示後数日遅れてインデックスに掲載されるケースがある。

BQ（`STOCK.TDNET_DOCUMENTS_ENHANCED`）が未登録の場合は GCS MCP で検索する:
```
# 銘柄の全開示を一覧
gcs_list(prefix="tdnet/4384/")

# 特定日付ファイルを探す場合はプレフィックスで絞る
gcs_list(prefix="tdnet/4384/20260317")

# 見つからない場合は ±3営業日 の日付範囲で再検索する
# （書類作成日 ≠ インデックス掲載日のため）
```

**`gs://stock_data_1930932/monthlydata/` 詳細:**

- **格納対象**: TDnet 月次開示カテゴリ（`MAIN_CATEGORY='月次開示'`）の文書チャンクテキスト
- **データソース**: BQ `STOCK.TDNET_DOCUMENTS_ENHANCED`
- **パス構造**:
  - `monthlydata/_progress.json` — 進捗管理（loaded_keys リストで取込済み管理）
  - `monthlydata/{ticker}/{yyyy-mm}.json` — 企業×月ごとの月次データ
- **JSON スキーマ**: ticker, name, yyyymm, submission_date, doc_title, chunk_texts, monthly_items, loaded_at
- **収集モジュール**: `scripts/monthly_data_load.py`（Cloud Run Job: `monthly-data-load`）
- **スケジュール**: 毎日 JST 7:00 を予定（未設定）
- **リラン方法**: `_progress.json` の `loaded_keys` から対象 `ticker/yyyy-mm` を削除して再実行
- **知見**: `docs/knowledges/tools/030_monthly_data_load.md`

### (c) ローカル CSV

| ローカルパス | 説明 | ソース | 備考 |
|-------------|------|--------|------|
| `data/csv/stock_price.csv` | 株価日次データ（分析用コピー） | BQ `STOCK.STOCK_PRICE` からエクスポート | 期間やTICKERを絞ってコピー |
| `data/csv/pbr_cleansed.csv` | PBR（クレンジング済み） | 事前計算 | 毎回LLMに計算させない |
| `data/csv/gyakuhibu_history.csv` | 逆日歩ヒストリカル | GCS からコピー | |
| `C:\Users\zonekun\Dropbox\stock\DA_四季報_2026_1.xlsx` | 四季報 Excel（2026年1集） | 四半期ごとに手動更新 | シート名: `list`。`scripts/shikiho_reader.py` で読み込み。`scripts/kiyohara_screening.py` も参照。新しい四半期版が届いたら `SHIKIHO_EXCEL` 定数を更新すること |
| `C:\Users\zonekun\Dropbox\stock\AI分析優待\Edinet遅延.xlsx` | EDINET 大量保有変更報告書 遅延提出一覧 | Cloud Run Job `edinet-delay`（毎営業日18:30 JST自動追記） | シート: `MAIN`。カラム: A=銘柄コード(4桁), B=発行体名, C=提出者名, D=義務発生日, E=提出日, F=遅延日数。閾値60日以上。スクリーニング: `scripts/screen_edinet_delay_tob.py`。過去データ(2021〜2024): GCS `gs://stock_data_1930932/edinet_delay/backfill.csv` |
| `data/csv/bond_history.csv` | 債券・マクロ指標 日次ヒストリカル | `C:\Users\zonekun\Dropbox\stock\BB_債券履歴_new.xlsx` (シート: LIST) | **明示的な指示があったときのみ更新**。直近90日はクレンジング未完了の可能性あり（下記参照） |
| `data/csv/consensus_result.csv` | 楽天証券 IFIS コンセンサス（経常利益） | 楽天証券 IFIS iframe（Selenium スクレイピング） | `scripts/update_conse_rakuten.py` で更新。全上場銘柄対象。**明示的な指示があったときのみ更新** |
| `data/master/activists.csv` | アクティビストファンド一覧（**git管理**） | 手動作成（2026-03-29） | スキーマ: `REGION`（シンガポール/香港/米国/英国/国内）, `NAME`（ファンド名）, `CATEGORY`（アクティビスト固定）。39件→38件。**明示的な指示があったときのみ更新**。⚠️ **除外済み（復活候補）**: `キャピタル・マネジメント`（国内）→ 名称が一般的すぎて誤検知多発 + 活動不活発のため2026-03-29除外 |
| `data/master/activist_aliases.csv` | アクティビストエイリアス（**git管理**） | `scripts/generate_activist_aliases.py`（Gemini 2.5 Pro生成） | スキーマ: `ACTIVIST_NAME`（activists.csvのNAMEに対応）, `ALIAS`（別名）。300+件。`flag_activists_in_list.py` と組み合わせて使用。**明示的な指示があったときのみ更新** |
| `data/master/nikkei225_constituents.csv` | 日経平均株価 構成225銘柄（**git管理**） | `scripts/download_nikkei225_constituents.py`（Wikipedia日本語版） | スキーマ: `TICKER`(4桁), `CODE5`(5桁), `NAME`, `SOURCE`, `FETCHED_AT_JST`。225行。業種情報は `STOCK_CODE_LIST.INDUSTRY_CODE33` / `SIZE_CATEGORY` と JOIN して取得すること。**構成銘柄変更時（年1-2回）に再実行する** |
| `data/csv/shareholders_activist_flag.csv` | 四季報大株主アクティビストフラグ付きリスト | `scripts/flag_activists_in_list.py` | スキーマ: `shareholder_name`, `is_activist`(bool), `activist_name`, `activist_region`, `match_method`(exact/partial/word_start/partial_rev), `match_score`。入力: 四季報大株主テキスト（1行1株主名） |
| `C:\Users\zonekun\Dropbox\stock\DA_四季報_YYYY_Q.xlsx` | 東洋経済 四季報 全銘柄データ | 手動入手（四半期ごとに更新） | **パスは四半期ごとに変わる**（例: `DA_四季報_2026_1.xlsx` → `DA_四季報_2026_2.xlsx`）。シート `list`、1行目ヘッダ、2行目以降データ。`scripts/kiyohara_screening.py` の `SHIKIHO_EXCEL` 定数を更新して使用。**明示的な指示があったときのみ更新**。⚠️ **鮮度が落ちるため積極的に使用しない。他のデータカタログ・API（BQ・J-Quants等）で取得できない場合のみフォールバックとして使用すること** |

**`bond_history.csv` スキーマ:**

| カラム名 | 型 | 説明 | 元列 |
|---------|-----|------|------|
| DATE | DATE | 取引日 | Col 0 |
| BEI | FLOAT | 損益分岐点インフレ率（Break-Even Inflation） | Col 1 |
| US10Y | FLOAT | 米国10年国債利回り(%) | Col 2: Gbond 10y |
| US5Y | FLOAT | 米国5年国債利回り(%) | Col 3: Gbond 5y |
| US2Y | FLOAT | 米国2年国債利回り(%) | Col 4: Gbond 2y |
| SP500_DIV_YIELD | FLOAT | S&P500配当利回り(%) | Col 5 |
| AAA_YIELD | FLOAT | AAA格社債利回り(%) | Col 6 |
| AAA_SPREAD | FLOAT | AAAスプレッド（対国債） | Col 7 |
| USDJPY | FLOAT | ドル円レート（小数点以下2桁） | Col 8 |
| HYG | FLOAT | ハイイールド債ETF（HYG）価格 | Col 9 |
| USDX | FLOAT | ドル指数（小数点以下2桁） | Col 10 |
| SOX | FLOAT | フィラデルフィア半導体指数 | Col 11 |
| BADI | FLOAT | バルチック海運指数 | Col 12 |
| CRB | FLOAT | CRB商品指数 | Col 13 |
| SKEW | FLOAT | CBOE SKEW指数 | Col 14 |
| DOW | FLOAT | ダウ平均 | Col 15 |
| NASDAQ | FLOAT | NASDAQ総合指数 | Col 17 |
| VIX | FLOAT | VIX恐怖指数 | Col 19 |
| SP500 | FLOAT | S&P500指数 | Col 23 |
| JP10Y | FLOAT | 日本10年国債利回り(%) | Col 26 |
| WTI | FLOAT | WTI原油先物価格（$/バレル） | Col 29 |
| FEAR_GREED | FLOAT | CNN Fear & Greed 指数（0〜100） | Col 30 |

**除外した列（計算可能または不要）:**
- Col 16: DOW前日比(%) → 計算可能
- Col 18: NASDAQ前日比(%) → 計算可能
- Col 20: メモ列（ほぼ空）
- Col 21: Dummy Date（Excelワーク列）
- Col 22: イベントメモ（ほぼ空）
- Col 24: S&P500前日比(%) → 計算可能
- Col 25: 空列
- Col 27: JP10Y前日比(%) → 計算可能
- Col 28: 日米10年金利差（JP10Y - US10Y）→ 計算可能

**`STOCK.CONSENSUS` BQテーブルスキーマ（2026-03-24 確認、行数: 14,046）:**

| カラム名 | 型 | 説明 |
|---------|-----|------|
| DATAAT | DATE | 取得日（スクレイピング実行日。中断再開時も初回起動日で固定） |
| TICKER | STRING | 銘柄コード（4桁） |
| FY | STRING | 決算期（YYYYMM）※現状 `000000` 固定で未使用 |
| QUARTER | STRING | 四半期区分（`1Q` / `2Q` / `3Q` / `FY`） |
| PROFIT | INTEGER | 経常利益コンセンサス（百万円） |
| TARGET | STRING | `CURRENT`（当期予想）/ `NEXT`（来期予想） |

> ⚠️ **データ構成**: 1回のロードで同一 TICKER・QUARTER に対し **CURRENT と NEXT の2行**が入る。ロードの都度 WRITE_APPEND されるため、同一 TICKER・QUARTER・TARGET でも DATAAT が異なる複数行が蓄積される。
> **クエリ時は必ず `TARGET = 'CURRENT'` でフィルタし、`PARTITION BY TICKER, QUARTER ORDER BY DATAAT DESC` で最新1件を取得**すること。TARGET フィルタなしだと来期予想と今期予想が混在してコンセンサス乖離率が異常値になる（実例: セブン&アイ FY で来期予想 vs 今期実績 → +580% の虚偽乖離）。

> ⚠️ **注意**: PROFIT（経常利益予想）のみ収録。予想ROE・ROA・配当利回り・CF等は存在しない。
> TOB予測モデル等でアナリスト予想系特徴量が必要な場合は実績値（J-Quants `/fins/statements`）で代替するか省略すること。

> ⚠️ **累積値**: `PROFIT` は**累積値**（1Q=Q1単独、2Q=Q1+Q2、3Q=Q1+Q2+Q3、FY=通期）。実績の `fin_summary` と同じ構造。
> **全Qのコンセンサスが揃っているとは限らない**（欠損Qが存在する）。決算反応モデルでのQ単独変換方法は実データパターン確認後に設計する。

**`consensus_result.csv` スキーマ:**

| カラム名 | 型 | 説明 |
|---------|-----|------|
| CODE | STRING | 銘柄コード（4桁） |
| FY | STRING | 決算期（YYYYMM形式、例: 202603） |
| QUARTER | STRING | 四半期区分（1Q/2Q/3Q/FY） |
| PROFIT | INTEGER | 経常利益コンセンサス（百万円） |
| TARGET | STRING | CURRENT（当期）/ NEXT（次期） |

- ソース: 楽天証券 IFIS コンセンサスページ（iframe: stockFrame）の tbl-data-01（四半期進捗）・tbl-data-09（通期財務）
- リランは `START_CODE` を変更するか `--start 7203` 引数で特定コードから再開可能
- 出力はバッチ書き込み（BATCH_SIZE=10銘柄ごと）

**四季報 Excel 主要カラム（単位ルール）:**

| カラム名 | 単位 | 備考 |
|---------|------|------|
| コード | - | 銘柄コード（4桁整数として格納） |
| CF単位 | - | CF関連項目の単位（"百万円" または "億円"）。`現金等` のみ適用 |
| 現金等 | CF単位に依存 | CF単位=百万円→×1,000,000 / 億円→×100,000,000 |
| 有利子負債 | **百万円固定** | CF単位に関係なく常に百万円 |
| 総資産 | **百万円固定** | 同上 |
| 自己資本 | **百万円固定** | 同上 |
| 時価総額 | **億円固定** | 全行共通 |
| 自己株保有 | - | 全件空欄のため使用不可 |

> **注意**: `scripts/kiyohara_screening.py` では有利子負債・現金等を四季報から取得し清原スクリーニングに使用。新しい四半期の四季報が届いたら `SHIKIHO_EXCEL` 定数を更新すること。

---

**固定情報:**
- 期間（FROM）: 1998-01-02（固定）
- 期間（TO）: 更新のたびに変わる。実際の最新日付は CSV を読んで確認すること
- ソースファイル: Excelシート LIST、1行目ヘッダー、6行目以降データ

**更新履歴（更新のたびに追記）:**
| 更新日 | 総行数 | データ期間（TO） |
|--------|--------|----------------|
| 2026-02-23 | 7,042件 | 2026-02-20 |
| 2026-03-02 | 7,047件 | 2026-02-27 |
| 2026-03-04 | 7,048件 | 2026-03-03 | 列追加: WTI（Col 29）、FEAR_GREED（Col 30）|

**更新ルール:**
- **更新タイミング**: 明示的な指示があったときのみ更新する（自動更新しない）
- **直近90日のデータ品質**: クレンジング未完了の可能性があるため、分析に使用する際は注意が必要
  - 更新時は「実行日 - 90日」より古いデータのみを「確定済み」として扱う
  - 直近90日のデータは参考値として保持するが、統計分析・バックテストの対象期間から除外することを推奨
  - 例: 2026-02-23 に更新した場合、〜2025-11-25 より前のデータが確定済み、2025-11-25〜2026-02-23 は暫定値

### (d) 外部API + キャッシュ

#### API系

| API | エンドポイント例 | キャッシュTTL | 備考 |
|-----|----------------|-------------|------|
| J-Quants | 株価日次、財務データ | 24時間 | JQUANTS_API_KEY 必要 |
| yfinance | 株価データ | 24時間 | API Key不要。BQ補完用 |

#### ファイルダウンロード系（`data/cache/` に保管）

| ファイル | 説明 | 更新頻度 | 備考 |
|---------|------|---------|------|
| `data/cache/yasai_price_maff.xlsx` | 農林水産省 野菜小売価格調査（現在） | 週次（手動） | 2021/4〜現在 |
| `data/cache/yasai_price_maff_past.xlsx` | 農林水産省 野菜小売価格調査（過去） | 固定（更新不要） | 2017/11〜2021/3 |

**`yasai_price_maff.xlsx` / `yasai_price_maff_past.xlsx` 詳細:**

| 項目 | 内容 |
|------|------|
| ソース | 農林水産省 食品価格動向調査（野菜） |
| 現在ファイルURL | `https://www.maff.go.jp/j/zyukyu/anpo/kouri/k_yasai/` からリンクされるExcel |
| 過去ファイルURL | `https://www.maff.go.jp/j/zyukyu/anpo/kouri/k_yasai/attach/xls/y_past-1.xlsx` |
| シート構成 | `価格`（円/kg）、`前週比`（比率）、`平年比`（過去5ヶ年平均比） |
| **採用シート** | **`平年比`**（季節性補正済み。1.0=平年並み、1.15=15%高騰） |
| 非採用シート | `前週比`（季節性未補正のため不適切） |
| 対象野菜品目 | キャベツ・ねぎ・はくさい・だいこん・ほうれんそう・レタス・きゅうり・トマト等（複数列） |
| 分析での使用方法 | 全品目の平年比を行平均 → **1階差分（Δ平年比）**で定常化して使用 |
| 更新方法 | 手動ダウンロード・上書き保存（自動取得スクリプトなし） |
| 関連分析 | `docs/knowledges/analysis/002_yasai_price_earnings_prediction.md` |

**Excel読み込み実装（元号日付のパース）:**

MAFFのExcelは日付列が元号形式（例: `令和３年１月`）のため、そのままpandasで読むと文字列になる。
以下のパターンで読み込み・変換する。

```python
import pandas as pd
import re

ERA_START = {
    "令和": pd.Timestamp("2019-05-01"),
    "平成": pd.Timestamp("1989-01-08"),
    "昭和": pd.Timestamp("1926-12-25"),
}

def parse_wareki(s: str) -> pd.Timestamp | None:
    """元号+年+月の文字列 → Timestamp。例: '令和３年１月' → 2021-01-01"""
    s = s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))  # 全角数字→半角
    m = re.match(r"(令和|平成|昭和)(\d+)年(\d+)月", s)
    if not m:
        return None
    era, year, month = m.group(1), int(m.group(2)), int(m.group(3))
    base = ERA_START[era]
    western_year = base.year + year - 1
    return pd.Timestamp(f"{western_year}-{month:02d}-01")

# Excelの読み込み（平年比シート）
# ヘッダー行が複数あるため header=None で読み込み、手動でスキップ
df_raw = pd.read_excel("data/cache/yasai_price_maff.xlsx",
                        sheet_name="平年比", header=None)
# 先頭2行: タイトル行（不要）。3行目以降がデータ
df = df_raw.iloc[2:].reset_index(drop=True)
df.columns = df_raw.iloc[1].values          # 2行目を列名に
df["date"] = df.iloc[:, 0].apply(lambda x: parse_wareki(str(x)))
df = df.dropna(subset=["date"])
```

**2ファイルの結合（過去 + 現在）:**

```python
past = pd.read_excel("data/cache/yasai_price_maff_past.xlsx", sheet_name="平年比", header=None)
curr = pd.read_excel("data/cache/yasai_price_maff.xlsx",      sheet_name="平年比", header=None)

# それぞれ同様にパースして pd.concat で縦結合
# 重複日付（2021/4前後のオーバーラップ）は drop_duplicates で除去
df_all = pd.concat([df_past, df_curr]).drop_duplicates(subset=["date"]).sort_values("date")
```

**探索したが不採用のデータソース（再探索不要）:**

| ソース | 不採用理由 |
|--------|-----------|
| 東京都中央卸売市場 日報CSV | 月次集計のみ利用可・データ期間短（2025/2〜） |
| WAGRI API | 日次・2019〜で優秀だが申請制（未取得。必要なら申請を検討） |
| cultivationdata.net API | MAFFと同一データ（重複） |
| 東京青果物情報センター | 有料会員制 |

---

### `STOCK.EARNINGS_DISCLOSURE_CALENDAR` — 決算開示カレンダー（予定/実績）

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR` | 決算開示カレンダー（予定/実績） | 日次 | 予定: ghostrader.net、実績: TDNET_DOCUMENTS_ENHANCED |

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
| DISCLOSURE_NUMBER | STRING | YES | TDnet開示番号（Aのみ） |
| TYPE_OF_DOCUMENT | STRING | YES | J-Quants書類種別（Aのみ） |
| DOC_TITLE | STRING | YES | TDnet書類タイトル（Aのみ） |
| SOURCE | STRING | NO | `ghostrader` / `tdnet` |
| LOADED_AT | DATETIME | NO | BQ格納日時（JST） |

**論理PK:** `(TICKER, FISCAL_YEAR_END, QUARTER, CATEGORY, RECORD_TYPE, REVISION_SEQ)`

**パーティション:** `DISCLOSURE_DATE` / **クラスタリング:** `TICKER, CATEGORY, RECORD_TYPE`

**有効なレコード種別:**

| CATEGORY | RECORD_TYPE | 説明 | ソース |
|:--|:--|:--|:--|
| R | S | 決算発表予定 | ghostrader.net（日次蓄積） |
| R | A | 決算短信（実績） | TDNET_DOCUMENTS_ENHANCED WHERE MAIN_CATEGORY='決算短信' |
| F | A | 業績予想修正（実績） | TDNET_DOCUMENTS_ENHANCED WHERE MAIN_CATEGORY='業績予想' |
| F | S | 存在しない | — |

**QUARTER マッピング:**

| ghostrader | TDnet (fin_summary) | 本テーブル |
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
  STOCK.TDNET_DOCUMENTS_ENHANCED
    WHERE MAIN_CATEGORY IN ('決算短信', '業績予想')
    → CATEGORY マッピング: 決算短信→R / 業績予想→F
    → fin_summary と TICKER+開示日で JOIN して FISCAL_YEAR_END, QUARTER, TYPE_OF_DOCUMENT を補完
```


---

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

---

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.SIGNAL_011_4` | 011-4 戦略 日次シグナル（米国→日本セクターETFリードラグ） | 日次 | Cloud Run Job `signal-011-4-daily`（月〜金 06:30 JST） |

**`STOCK.SIGNAL_011_4` スキーマ:**

| カラム名 | 型 | 説明 |
|---------|-----|------|
| DATE | DATE | 取引日（この日の寄付でエントリー予定） |
| SIGNAL_DATE | DATETIME | シグナル計算日時（JST） |
| TICKER | STRING | 日本ETFティッカー（1617〜1633） |
| SIGNAL_VALUE | FLOAT64 | シグナル値 z_hat_JP |
| SIDE | STRING | 'LONG' / 'SHORT' / 'NONE' |
| RANK | INT64 | シグナル絶対値の順位（1=最強） |
| POSITION_JPY | FLOAT64 | 想定ポジション金額（円） |
| KILL_SWITCH | BOOL | Kill Switch 発動中なら True |
| ROLLING_IC | FLOAT64 | 26週 Rolling IC 値 |

- **収集スクリプト**: `scripts/signal_011_4_daily.py`
- **更新方式**: DELETE + WRITE_APPEND（リラン安全。当日分を削除してから挿入）
- **更新タイミング**: 毎営業日 06:30 JST（Cloud Scheduler `signal-011-4-daily`）
- **データソース**: yfinance（米国ETF終値）+ BQ STOCK_PRICE_JQUANTS（日本ETF）+ GCS pickle（C0/V0）
- **GCS 依存**: `gs://stock_data_1930932/signal_011_4/c0_v0.pkl`（C0/V0 事前計算結果）

---

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.PAPER_TRADE_011_4` | 011-4 戦略 ペーパートレード P&L | 日次 | Cloud Run Job `paper-trade-011-4-pnl`（月〜金 19:00 JST） |

**`STOCK.PAPER_TRADE_011_4` スキーマ:**

| カラム名 | 型 | 説明 |
|---------|-----|------|
| DATE | DATE | 取引日 |
| TICKER | STRING | ETFティッカー |
| SIDE | STRING | 'LONG' / 'SHORT' |
| POSITION_JPY | FLOAT64 | ポジション金額（円） |
| OPEN_PRICE | FLOAT64 | 寄付値 |
| CLOSE_PRICE | FLOAT64 | 引け値 |
| RETURN_PCT | FLOAT64 | リターン（%） |
| PNL_JPY | FLOAT64 | 損益（円） |
| CUMULATIVE_PNL | FLOAT64 | 累積損益（円、初日からの積算） |
| KILL_SWITCH | BOOL | Kill Switch 状態 |

- **収集スクリプト**: `scripts/paper_trade_011_4_pnl.py`
- **更新方式**: DELETE + WRITE_APPEND（リラン安全）
- **更新タイミング**: 毎営業日 19:00 JST（Cloud Scheduler `paper-trade-011-4-pnl-daily`）
- **データソース**: BQ SIGNAL_011_4（シグナル）+ BQ STOCK_PRICE_JQUANTS（株価）
- **メール通知**: 毎日 P&L 結果を送信（Kill Switch ON 時もノートレード通知を送信）

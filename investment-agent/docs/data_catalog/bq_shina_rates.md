# STOCK.SHINA_RATES
> 親: [`data_catalog.md`](../../data_catalog.md)

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


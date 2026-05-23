# STOCK.STOCK_CODE_LIST
> 親: [`data_catalog.md`](../../data_catalog.md)

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.STOCK_CODE_LIST` | 上場銘柄マスタ（銘柄コード・銘柄名・業種・市場区分） | 随時 | TSE: JPX公開データ。FSE/SSE/NSE: `scripts/update_regional_codes.py` |

**`STOCK.STOCK_CODE_LIST` スキーマ:**

全カラムが STRING 型。

| カラム名 | 型 | 説明 |
|---------|-----|------|
| TICKER | STRING (REQUIRED) | 銘柄コード（4桁数字 `"1301"` またはアルファナメリック `"130A"` ） |
| EXCHANGE | STRING (REQUIRED) | 証券取引所（`TSE`, `FSE`, `SSE`, `NSE`） |
| STOCK_NAME | STRING | 銘柄名（企業名・ファンド名・ETF名など） |
| MARKET_CATEGORY | STRING | 市場・商品区分（TSEは `プライム（内国株式）`, `スタンダード（内国株式）`, `グロース（内国株式）`, ETF・ETN, REIT等。FSE/SSE/NSEは本則市場, アンビシャス, プレミア, メイン, ネクスト等） |
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
- TSE内国株式3市場を抽出する場合は `MARKET_CATEGORY IN ('プライム（内国株式）','スタンダード（内国株式）','グロース（内国株式）')` を使う。`プライム` などサフィックスなしの値では一致しない
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


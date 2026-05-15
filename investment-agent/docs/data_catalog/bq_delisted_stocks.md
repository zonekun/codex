# STOCK.DELISTED_STOCKS
> 親: [`data_catalog.md`](../../data_catalog.md)

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.DELISTED_STOCKS` | 上場廃止銘柄マスタ（廃止理由・TOB/MBO判定・買付価格等） | 随時 | `scripts/scrape_matsui_delisted.py`（TOB廃止予定の先行取得）+ `scripts/scrape_jpx_delisted.py`（廃止確定後の本登録）+ `scripts/fetch_tob_announcements.py`（TOB詳細をEDINETから抽出）|

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
- 更新方法: 3段階
  1. `scripts/scrape_matsui_delisted.py` で松井証券 公開買付ページから「上場廃止予定」銘柄を先行 INSERT（DELISTING_DATE=NULL, IS_TOB_MBO=NULL）
  2. `scripts/scrape_jpx_delisted.py` で JPX 上場廃止一覧をスクレイピング → 新規 INSERT + 松井先行分 UPDATE（DELISTING_DATE等を補完）
  3. `scripts/fetch_tob_announcements.py --missing-only` で未抽出の TOB_* を EDINET からバックフィル → UPDATE
- **バックフィル実績 (2026-04-20)**: 577件の IS_TOB_MBO=TRUE レコードに対し **299件 (51.8%) で TOB_DOC_ID 取得成功**、うち **288件が IS_PAPER_TOB_LABEL=TRUE**。失敗278件の内訳は分類のみで「TOB無関係」断定は強制廃止8件のみ。詳細は `docs/knowledges/analysis/007_tob_ml_prediction.md`


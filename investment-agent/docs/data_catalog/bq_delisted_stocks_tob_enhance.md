# STOCK.DELISTED_STOCKS_TOB_ENHANCE
> 親: [`data_catalog.md`](../../data_catalog.md)

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.DELISTED_STOCKS_TOB_ENHANCE` | TOB公表の公式IR初出日・根拠情報（irbank/TDnet由来）。DELISTED_STOCKSのEDINET公告日（TOB_ANNOUNCEMENT_DATE）を補完する別定義の日付テーブル | 随時 | Codexが irbank.net / BQ TDNET_DOCUMENTS_ENHANCED から収集。BQ投入スクリプト: `scripts/load_tob_ir_release_dates.py`（予定） |

**`STOCK.DELISTED_STOCKS_TOB_ENHANCE` スキーマ:**

| カラム名 | 型 | モード | 説明 |
|---------|-----|--------|------|
| `TICKER` | STRING | REQUIRED | 銘柄コード ★PK / FK → DELISTED_STOCKS.TICKER |
| `IR_FIRST_RELEASE_DATE` | DATE | NULLABLE | 公式IR初出日（irbank/TDnet）。DELISTED_STOCKSの`TOB_ANNOUNCEMENT_DATE`（EDINET公告日）とは別定義 |
| `IR_RELEASE_KIND` | STRING | NULLABLE | 採用した公式IRの種類。値: `予告` / `正式` / `不明` |
| `SOURCE` | STRING | NULLABLE | 採用候補を取得した情報源。値: `WEB_IRBANK_TDNET` / `BQ_TDNET` / `UNRESOLVED` |
| `DOC_ID_OR_URL` | STRING | NULLABLE | TDnet文書ID（取得不能時のみURLフォールバック。今回成果物ではURLなし） |
| `DOC_TITLE` | STRING | NULLABLE | IRリリースタイトル |
| `EVIDENCE_TEXT` | STRING | NULLABLE | 採用根拠にした公式IRタイトル。本文抜粋ではなく原則`DOC_TITLE`と同一 |
| `CONFIDENCE` | STRING | NULLABLE | tob_announcement_dateが公式IR初出日らしいかのルールベース信頼度。値: `high` / `medium`（unresolvedはこのテーブルから除外） |
| `NOTES` | STRING | NULLABLE | 判定メモ（例: `formal_title_match`） |

**主キー:** `TICKER` — NOT ENFORCED

**値の定義:**

`IR_RELEASE_KIND`:
- `予告`: 正式TOB開始前の「公開買付けの開始予定」「実施予定」などの予告IR。正式開始IRより優先採用
- `正式`: 「公開買付けの開始」「MBOの実施」「賛同の意見表明・応募推奨」など正式公表IR
- `不明`: 公式TDnet系候補が取れなかった行（主にunresolved）

`SOURCE`:
- `WEB_IRBANK_TDNET`: irbank.net の銘柄別TDnet一覧から取得（主ソース）
- `BQ_TDNET`: BQ `TDNET_DOCUMENTS_ENHANCED` から取得（irbank側で候補なし時の補助ソース）
- `UNRESOLVED`: 公式TDnet系候補が取れなかった行

`CONFIDENCE`:
- `high`: 公式TDnet/irbankタイトルがTOB開始・開始予定・MBO実施・賛同/応募推奨等の強いパターンに一致
- `medium`: TOB関連語はあるが強いパターンより弱い汎用一致（今回8件）

**注意事項:**
- データ範囲: Codex初回収集（2026-05-19）。対象は `DELISTED_STOCKS.IS_TOB_MBO = TRUE` の616件
- 収録件数: 475行（unresolved 141件は除外）
- 件数内訳: `IR_RELEASE_KIND` 正式426件・予告49件 / `SOURCE` WEB_IRBANK_TDNET 471件・BQ_TDNET 4件
- `CONFIDENCE = medium` は8件のみ
- `DOC_TITLE` と `EVIDENCE_TEXT` は原則同一。将来的に本文抜粋に更新する場合は`EVIDENCE_TEXT`のみ変更
- 投入用CSV: `data/csv/tob_ir_release_dates/tob_announcement_dates_for_claude_20260519_200803.csv`
- 詳細CSV（根拠確認用）: `data/csv/tob_ir_release_dates/tob_ir_release_dates_20260519_200803.csv`
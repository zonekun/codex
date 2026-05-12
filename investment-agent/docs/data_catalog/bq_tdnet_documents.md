# STOCK.TDNET_DOCUMENTS_ENHANCED
> 親: [`data_catalog.md`](../../data_catalog.md)

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


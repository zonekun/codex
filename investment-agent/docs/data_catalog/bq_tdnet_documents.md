# STOCK.TDNET_DOCUMENTS_ENHANCED
> 親: [`data_catalog.md`](../../data_catalog.md)

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED` | TDnet適時開示書類のテキスト・チャンク・埋め込みベクトル | 日次 | `tdnet-load-daily`（火〜土 02:00 JST）で BQ投入、`ai_processing_flow` Workflows で AI判定（Gemma 2-pass） |

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
| CHUNK_TEXT | STRING | チャンクテキスト（分割済みテキスト）。**詳細は §チャンク化仕様 参照** |
| CHUNK_INDEX | INT64 | 同一 DOC_ID 内のチャンク順序（0 始まり）。NULL の意味は 2 通り: (a) CHUNK_TEXT NULL のメタデータのみ行、(b) **2026-05-18 以前にロードされた旧データ**（遡及採番なし）。本文順復元は `ORDER BY CHUNK_INDEX`、NULL を除外したい場合は `WHERE CHUNK_INDEX IS NOT NULL` |
| EMBEDDING | ARRAY\<FLOAT64\> | テキスト埋め込みベクトル（text-embedding-004, 768次元、3カテゴリ限定）。**詳細は §Vector Index 仕様 参照** |
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
    → Gemma Pass 2（決算短信 + 決算説明資料、受注高/受注残高マージ）
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

## 使い分けガイド: CHUNK_INDEX の3ケース

> ⚠️ **禁則**: `CHUNK_INDEX IS NOT NULL` を検索条件に加えない。2026-05-18 以前のロード分は全件 NULL のため、その条件で絞ると旧データが全件消える。

2026-05-18 以降の新規ロード分は `CHUNK_INDEX` が採番されるが、**利用側に縛り（必ず IS NOT NULL）はかけない**。順序が要らないユースケースでは過去データも有効に使える。ケース別パターン:

```sql
-- ケースA: 順序復元が必要（LLM 全文読み・全文連結など）
-- 新規ロード分のみ対象。旧データは順序不能のため除外
SELECT CHUNK_TEXT
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE DOC_ID = 'X'
  AND CHUNK_INDEX IS NOT NULL
ORDER BY CHUNK_INDEX;

-- ケースB: 順序不要（Vector Search・集計・存在確認）
-- CHUNK_INDEX を見る必要なし。従来クエリのまま動く
SELECT DOC_ID, CHUNK_TEXT
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE TICKER = '7011'
  AND EMBEDDING IS NOT NULL;  -- Vector Search 用

-- ケースC: 順序が望ましいが旧データも含めたい
-- NULL を末尾に回して連結（旧データはバラバラだが含まれる）
SELECT STRING_AGG(CHUNK_TEXT, '\n' ORDER BY CHUNK_INDEX NULLS LAST)
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE DOC_ID = 'X';
```

### 重複行への注意（INSERT→DELETE 非原子の既知症状）

`phase5_bq_insert_finalize` は INSERT→DELETE 順序入替済み（004 B-4 / `013_tdnet_load.md §T-1`）だが、INSERT 成功後・DELETE 失敗の中断 → 再実行で **同 DOC_ID の completed 行が重複追加** されるケースが既知。CHUNK_INDEX 列追加後はこの重複行が **同じ `(DOC_ID, CHUNK_INDEX)` で 2 セット返る**。順序復元クエリでは以下のいずれかで重複除去:

```sql
-- 案1: DISTINCT で重複行除去（軽量・推奨）
-- EXTRACTED_AT が NULL の旧データでも動作する
SELECT DISTINCT CHUNK_INDEX, CHUNK_TEXT
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE DOC_ID = 'X' AND CHUNK_INDEX IS NOT NULL
ORDER BY CHUNK_INDEX;

-- 案2: 最新 EXTRACTED_AT のセットだけ採用（厳密）
-- ⚠️ EXTRACTED_AT は 2026-05-18 以前ロード分が NULL のため、過去データを含む汎用クエリでは案1を使うこと
-- 新規ロード分（2026-05-18 以降）のみ対象にする場合に限り案2を使用可
SELECT CHUNK_INDEX, CHUNK_TEXT
FROM (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY DOC_ID, CHUNK_INDEX
    ORDER BY EXTRACTED_AT DESC
  ) AS rn
  FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
  WHERE DOC_ID = 'X' AND CHUNK_INDEX IS NOT NULL
)
WHERE rn = 1
ORDER BY CHUNK_INDEX;
```

### 健全性監視クエリ

deploy 初日・1週間後・1ヶ月後に実行を推奨（`<cutoff>` は `2026-05-18` 以降の本番適用日を指定）:

```sql
-- 監視1: ai-finalize 後の CHUNK_INDEX NULL 漏れ検知
SELECT COUNT(*) AS leaked_rows
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE SUBMISSION_DATE >= '<cutoff>'
  AND AI_STATUS = 'completed' AND CHUNK_TEXT IS NOT NULL
  AND CHUNK_INDEX IS NULL;
-- 期待: 0。1 以上は書込み修正の欠落

-- 監視2: CHUNK_INDEX 連番健全性（doc あたり MAX(CHUNK_INDEX)+1 = チャンク行数）
SELECT DOC_ID, COUNT(*) AS rows, MAX(CHUNK_INDEX) AS max_ci,
       COUNT(*) - 1 = MAX(CHUNK_INDEX) AS is_healthy
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE SUBMISSION_DATE >= '<cutoff>' AND CHUNK_TEXT IS NOT NULL
GROUP BY DOC_ID
HAVING NOT is_healthy
LIMIT 20;

-- 監視3: 同一 (DOC_ID, CHUNK_INDEX) 重複検知
SELECT DOC_ID, CHUNK_INDEX, COUNT(*) AS dup_cnt
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE SUBMISSION_DATE >= '<cutoff>' AND CHUNK_TEXT IS NOT NULL
GROUP BY DOC_ID, CHUNK_INDEX
HAVING dup_cnt > 1
LIMIT 20;
```

**注意事項:**
- **EXTRACTED_AT は 2026-05-18 以前ロード分が NULL**（案1: NULL 許容で据え置き。固定値 UPDATE はコスト/虚偽情報の観点から非実施）。重複除去クエリは案1（DISTINCT）を標準とし、案2（ORDER BY EXTRACTED_AT DESC）は新規ロード分のみに使用する（`tools-013_bq_past_data_recovery_20260518_232030.md` 参照）
- **FILER_NAME は 2026-05-18 以前の旧ロード分も遡及修正済み**（2026-05-20 UPDATE実施）。アルファベット ticker は FILE_NAME から REGEXP 抽出、数字 ticker は STOCK_CODE_LIST JOIN（上場廃止銘柄は FILE_NAME REGEXP fallback）。修正後の全件残存=0
- FILER_ID は TDnet に EDINET コードが存在しないため NULL
- EMBEDDING は Google text-embedding-004 モデルによる 768次元ベクトル
- **EMBEDDING / CHUNK_TEXT は NULL になりうる**: 決算短信・決算説明資料・月次開示のみ Embedding 対象。それ以外のカテゴリはメタデータのみ（CHUNK_TEXT=NULL, EMBEDDING=NULL の1行）
- **CHUNK_INDEX は 2026-05-18 以降の新規ロード分のみ採番**（過去データは NULL のまま据え置き、遡及採番なし）。本文順復元が必要なら `ORDER BY CHUNK_INDEX`、旧データを除外したい場合は `WHERE CHUNK_INDEX IS NOT NULL` を併用。**縛りはかけない方針** — Vector Search・単一チャンク・順序不要集計など旧データでも有効に使える場面が多い。詳細は §使い分けガイド 参照
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
    CHUNK_INDEX INT64,
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

## チャンク化仕様（CHUNK_TEXT カラム生成ロジック）

実装: `create_chunks_for_tdnet()` (`scripts/tdnet_load_parallel.py:451`)

| 項目 | 値 |
|------|-----|
| ライブラリ | langchain_text_splitters.RecursiveCharacterTextSplitter |
| chunk_size | 400 文字 |
| chunk_overlap | 50 文字（実質前進 350 文字/chunk） |
| セパレータ優先順 | `["\n\n[PAGE", "\n\n", "\n", "。", "、", " "]` |
| プレフィックス | 各 CHUNK_TEXT 冒頭に固定で `文書タイトル: {doc_title}\n` |
| 対象カテゴリ | `_EMBED_CATEGORIES = {"決算短信", "決算説明資料", "月次開示"}` のみ |
| 対象外カテゴリ | チャンク化されず、メタデータ1行のみ BQ 格納（CHUNK_TEXT NULL）|
| チャンク数の目安 | 5万文字（典型決算短信）で約 130〜160 チャンク |

## Vector Index 仕様（EMBEDDING カラムベクトル検索）

実装: ETL 完了後（`processed > 0` の場合のみ）に自動作成。詳細は `docs/knowledges/tools/013_tdnet_load.md §BQ Vector Index 設定`

| 項目 | 値 |
|------|-----|
| インデックス名 | `tdnet_doc_vector_index` |
| 対象列 | `EMBEDDING ARRAY<FLOAT64>` (768次元) |
| モデル | text-embedding-004（Vertex AI Batch Embedding API、リージョン us-central1） |
| コスト | 文字課金 $0.025/1M chars |
| index_type | IVF |
| distance_type | COSINE |
| ivf_options | `{"num_lists": 1000}` |

### 検索クエリ例

```sql
-- 類似チャンク検索（クエリベクトル指定）
SELECT base.DOC_ID, base.CHUNK_TEXT, distance
FROM VECTOR_SEARCH(
  TABLE `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`,
  'EMBEDDING',
  (SELECT @query_embedding AS embedding),
  top_k => 20,
  distance_type => 'COSINE'
);
```

---


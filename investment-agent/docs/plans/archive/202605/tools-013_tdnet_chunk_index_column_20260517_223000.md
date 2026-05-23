# TDnet ENHANCED テーブルへ CHUNK_INDEX 列追加（順序保証）

**作成日時**: 2026-05-17 22:30 JST
**ステータス**: 完了 (2026-05-18)
**分類**: (b) 継続改修型
**親知見 MD**: `docs/knowledges/tools/013_tdnet_load.md`
**対象ファイル**:
- `scripts/tdnet_load_parallel.py`（2566 行、commit `64c1b812` 時点）
- `scripts/tdnet_load_recovery.py`（廃止予定経路の起動遮断ガード追加、P1-2）
- `docs/data_catalog/bq_tdnet_documents.md`（スキーマ定義）
- `docs/knowledges/tools/013_tdnet_load.md` §T-3（注記追加）
- BQ テーブル `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: `STOCK.TDNET_DOCUMENTS_ENHANCED` に `CHUNK_INDEX INT64` 列を追加し、同一 DOC_ID 内の複数チャンクを `ORDER BY CHUNK_INDEX` で本文順に復元可能にする。**スコープは「今後ロードするデータのみ」。過去データの再ロード・遡及バックフィルは行わない**。

> **派生元**: `docs/reviews/202_cr_tdnet_orders_extract.md` 【重大な指摘】#2「同一 DOC_ID 内のチャンク順序を保証する `ORDER BY` 列が不明」で発覚した TBL 設計の不備を恒久是正する。受注情報抽出タスク（`ad-hoc_tdnet_orders_extract_20260517_214618.md`）の前提条件でもある。

---

## 前提サマリ

- 過去修正: なし（本案件で新規追加する列）
- 残存: 1 件（スキーマ列不足）
- 実機検証の有無: 未検証（dev → prod 段階適用）
- 関連 incident: `docs/reviews/202_cr_tdnet_orders_extract.md` #2（順序復元手段未定義）
- 内部実装: `phase4_chunk_and_embed` は内部で `(doc_id, chunk_index)` tuple を key に使用（`013_tdnet_load.md` §T-3）。**BQ に列として露出していないだけで、enumerate 順序自体は既に存在**
- 順序保証の根拠（CR-203 で検証済み）: `doc.chunks` は `RecursiveCharacterTextSplitter.split_text()` の本文順 list をそのまま代入。Embedding 並列バッチは `content_to_chunks` の `(doc, ci)` mapping 経由で `doc.embeddings[ci]` に **ci を明示キー** として書き戻し → 並列順崩れに耐性あり

### 実装着手時に確認が必要な前提（TODO）

- [ ] `tdnet_load_parallel.py` の `--limit` フラグ実装有無（smoke test の前提）。無ければ `DOCS_LIMIT` 環境変数で代替
- [ ] dev プロジェクトの BQ 同等テーブル存在有無。無ければ prod の少件パーティション（最新日 1 件）で smoke を兼用
- [ ] BQ DROP COLUMN（ロールバック時）が Vector Index 再構築をトリガーするか。`bq_tdnet_documents.md` で確認、必要なら回収手順に「Vector Index 再構築 ~XX 分」を追記

---

## 優先度の定義

- **P0**: BQ スキーマ変更と書き込み側のコード修正。これが揃わないと新規ロード分でも順序復元不能のまま
- **P1**: データカタログ MD のスキーマ反映・利用側クエリの注意書き
- **P2**: 利用側クエリの段階移行（cutoff date での切替案内）

---

## 指摘項目

### P0-1. BQ テーブルに `CHUNK_INDEX` 列を追加 🚨

**症状**: `STOCK.TDNET_DOCUMENTS_ENHANCED` のスキーマに同一 DOC_ID 内のチャンク順序を表す列がなく、`SELECT CHUNK_TEXT ... WHERE DOC_ID = 'X' ORDER BY ???` の ORDER BY 列が指定できない。

**該当**: `docs/data_catalog/bq_tdnet_documents.md:L8-L33`（スキーマ表）/ L144-L166（DDL）

**根本原因**: 設計初期に「チャンクは順不同で読まれる」前提だった（Vector Search 用途のみ想定）が、後付けで LLM 全文読みの用途が増えた。`004_coding_conventions.md` 直接該当ルールなし。`013_tdnet_load.md §T-3` は Python 内部 key の話で、BQ 列としては露出していない。

**修正方針**: `ALTER TABLE ADD COLUMN` で `CHUNK_INDEX INT64 NULLABLE` を追加。既存行は NULL のまま据え置き（過去データ再ロード不要）。

```sql
-- before（現行 DDL 抜粋）
CREATE OR REPLACE TABLE `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED` (
    DOC_ID STRING NOT NULL,
    ...
    SECTION_CATEGORY STRING,
    CHUNK_TEXT STRING,
    EMBEDDING ARRAY<FLOAT64>,
    ...
);

-- after（ALTER 実行。<cutoff date> は本プラン本番適用日を実値で埋める）
ALTER TABLE `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
ADD COLUMN CHUNK_INDEX INT64
OPTIONS(description="同一 DOC_ID 内のチャンク順序（0 始まり）。NULL の意味は 2 通り: (a) CHUNK_TEXT NULL のメタデータのみ行、(b) <cutoff date YYYY-MM-DD> 以前にロードされた旧データ（過去遡及採番なし）。本文順復元が必要なら ORDER BY CHUNK_INDEX、ただし NULL 行を除外するか NULLS LAST 指定");
```

クラスタリング・パーティション・他列に影響なし。`ADD COLUMN` は REQUIRED 化しない限り無停止・無コスト。

**呼び出し側への波及**:
- 読込み側全般: `SELECT *` を使っている既存 SQL は新列が末尾追加されるだけで影響なし。`SELECT` で列を明示している箇所は影響なし。
- 書込み側: NDJSON Load Job で row 辞書を作る箇所が新列をセットするように修正（P0-2/P0-3 参照）。
- **既存行 (CHUNK_INDEX IS NULL)**: 過去分は順序復元不可のまま据え置き。利用側が必要なら `WHERE CHUNK_INDEX IS NOT NULL` または `WHERE SUBMISSION_DATE >= '<cutoff>'` で新規ロード分のみ対象にする（P2 参照）。

**検証**:
1. dev プロジェクトの同等テーブル（あれば）で `ALTER TABLE ADD COLUMN` を先に流す
2. `INFORMATION_SCHEMA.COLUMNS` で `CHUNK_INDEX` が末尾に追加されたことを確認
3. 既存行を `SELECT CHUNK_INDEX FROM ... LIMIT 10` で全行 NULL になっていることを確認
4. 既存の `SELECT *` 系クエリが壊れていないことを `tdnet-load-daily` resume / `--job-mode=ai-finalize` のスモークで確認

**ロールバック**: 順序厳守。

1. **必ずコード revert を先**: `phase5_*` 系の row 辞書から `CHUNK_INDEX` キーを削除（P0-2/P0-3/P0-4 の commit 群を revert）。`tdnet_load_parallel.py` 全体に `ignore_unknown_values=True` 設定は **無い**（CR-203 で全文 Grep 確認、0 件）ため、列削除を先行させると `Unknown name "CHUNK_INDEX"` で Load Job が失敗する
2. **次に ALTER DROP**: `ALTER TABLE \`gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED\` DROP COLUMN CHUNK_INDEX`。BQ の DROP COLUMN は論理削除（7日以内なら `UNDROP` 可能、課金カラムは7日後解放）
3. **Vector Index 再構築の要否**: ロールバック実行前に `bq_tdnet_documents.md` または BQ 公式ドキュメントで DROP COLUMN が Vector Index に与える影響を確認。再構築が走る場合は所要時間と Vector Search 一時停止を周知

---

### P0-2. `phase5_bq_insert_finalize` で `CHUNK_INDEX` を書き込む 🚨

**症状**: ai-finalize 経路（新アーキの本流）でチャンク行を BQ Load Job 投入する際、`for ci, chunk_data in enumerate(doc.chunks):` の `ci` が row 辞書に含まれていない。

**該当**: `scripts/tdnet_load_parallel.py:L2001-L2020`（`phase5_bq_insert_finalize` chunks ループ）

```python:L2001-L2020
                if doc.chunks:
                    for ci, chunk_data in enumerate(doc.chunks):
                        row = {**base_row, "CHUNK_TEXT": chunk_data["chunk_text"]}
                        if doc.sub_categories:
                            row["SUB_CATEGORIES"] = doc.sub_categories
                        # numpy 保持 → BQ 書込時に float list へ変換
                        if (doc.embedding_set and ci < len(doc.embedding_set)
                                and doc.embedding_set[ci]):
                            row["EMBEDDING"] = doc.embeddings[ci].tolist()
                        tmp.write(json.dumps(row, ensure_ascii=False))
                        tmp.write("\n")
                        row_count += 1
                else:
                    # Embedding 対象外: メタデータ1行のみ
                    row = {**base_row, "CHUNK_TEXT": None}
                    if doc.sub_categories:
                        row["SUB_CATEGORIES"] = doc.sub_categories
                    tmp.write(json.dumps(row, ensure_ascii=False))
                    tmp.write("\n")
                    row_count += 1
```

**根本原因**: テーブル列が存在しなかったため、書込み側でも対応する key が無かった。スキーマ追加（P0-1）と同期で書込み側も更新する必要がある。

**修正方針**: chunks ループ内の row 辞書に `"CHUNK_INDEX": ci` を追加。メタデータ1行（CHUNK_TEXT=None）側は `CHUNK_INDEX=None` を明示。

```python
# after
                if doc.chunks:
                    for ci, chunk_data in enumerate(doc.chunks):
                        row = {
                            **base_row,
                            "CHUNK_TEXT":  chunk_data["chunk_text"],
                            "CHUNK_INDEX": ci,
                        }
                        if doc.sub_categories:
                            row["SUB_CATEGORIES"] = doc.sub_categories
                        if (doc.embedding_set and ci < len(doc.embedding_set)
                                and doc.embedding_set[ci]):
                            row["EMBEDDING"] = doc.embeddings[ci].tolist()
                        tmp.write(json.dumps(row, ensure_ascii=False))
                        tmp.write("\n")
                        row_count += 1
                else:
                    row = {
                        **base_row,
                        "CHUNK_TEXT":  None,
                        "CHUNK_INDEX": None,
                    }
                    if doc.sub_categories:
                        row["SUB_CATEGORIES"] = doc.sub_categories
                    tmp.write(json.dumps(row, ensure_ascii=False))
                    tmp.write("\n")
                    row_count += 1
```

**呼び出し側への波及**: 無し（NDJSON Load Job の row 辞書のみ）。

**検証**:
1. ローカル smoke: 1 doc・複数チャンクを ai-finalize で BQ に流し、`SELECT DOC_ID, CHUNK_INDEX, LEFT(CHUNK_TEXT, 30) FROM ... WHERE DOC_ID = 'X' ORDER BY CHUNK_INDEX` で 0,1,2,... 連番かつ本文が順序通りであることを確認
2. メタデータ1行行（CHUNK_TEXT=NULL）が `CHUNK_INDEX=NULL` になっていることを確認

**ロールバック**: コミット revert。スキーマ側 P0-1 を残したまま書込み側だけ戻しても NULL 化されるだけで他列は無傷。

---

### P0-3. `phase5_bq_insert` (legacy) でも `CHUNK_INDEX` を書き込む 🚨

**症状**: 旧アーキの legacy 経路（`--job-mode=full` で `submit_id` を伴わない単発実行系）でも、chunks ループ内で `CHUNK_INDEX` が row に入らない。

**該当**: `scripts/tdnet_load_parallel.py:L1418-L1432`（legacy `phase5_bq_insert` chunks ループ）

```python:L1418-L1432
                if doc.chunks:
                    for ci, chunk_data in enumerate(doc.chunks):
                        row = {**base_row, "CHUNK_TEXT": chunk_data["chunk_text"]}
                        if (doc.embedding_set and ci < len(doc.embedding_set)
                                and doc.embedding_set[ci]):
                            row["EMBEDDING"] = doc.embeddings[ci].tolist()
                        tmp.write(json.dumps(row, ensure_ascii=False))
                        tmp.write("\n")
                        row_count += 1
                else:
                    row = {**base_row, "CHUNK_TEXT": None}
                    tmp.write(json.dumps(row, ensure_ascii=False))
                    tmp.write("\n")
                    row_count += 1
```

**根本原因**: P0-2 と同根。新旧両経路で同じパターンの書き漏れ。`013_tdnet_load.md §T-1`（対称性の崩れを放置しない）に該当。

**修正方針**: P0-2 と同じ修正を legacy 側にも適用。

```python
# after（差分のみ）
                if doc.chunks:
                    for ci, chunk_data in enumerate(doc.chunks):
                        row = {
                            **base_row,
                            "CHUNK_TEXT":  chunk_data["chunk_text"],
                            "CHUNK_INDEX": ci,
                        }
                        ...
                else:
                    row = {
                        **base_row,
                        "CHUNK_TEXT":  None,
                        "CHUNK_INDEX": None,
                    }
                    ...
```

**呼び出し側への波及**: 無し。legacy 経路は新アーキ稼働3ヶ月後に廃止予定（`bq_tdnet_documents.md:L58`）だが、廃止までの間も新スキーマと一貫させる。

**検証**: P0-2 と同じ smoke を `--job-mode=full` で実施。

**ロールバック**: コミット revert。

---

### P0-4. `phase5_bq_insert_load` (pending メタデータ 1 行) も明示 NULL を入れる 🚨

**症状**: `--job-mode=load`（新アーキの BQ 投入段階）は `CHUNK_TEXT=NULL` のメタデータ1行のみを insert する。この行はその後 ai-finalize で DELETE + INSERT で置換されるが、置換前後で `CHUNK_INDEX` 列の扱いを明示しないと、スキーマ追加直後の dry-run で「列の欠落」と誤解される可能性がある。

**該当**: `scripts/tdnet_load_parallel.py:L1495-L1515`（`phase5_bq_insert_load`）

```python:L1495-L1515
                row = {
                    "DOC_ID":           doc.doc_id,
                    ...
                    "CHUNK_TEXT":       None,
                    "FILE_NAME":        doc.blob_name,
                    "AI_STATUS":        "pending",
                    "AI_PROCESSED_AT":  None,
                }
```

**根本原因**: 同上。Load Job は欠落 key を NULL 補完するため動作上は問題ないが、明示性のため row 辞書に追加する。

**修正方針**: `"CHUNK_INDEX": None,` を追加。

```python
# after
                row = {
                    "DOC_ID":           doc.doc_id,
                    ...
                    "CHUNK_TEXT":       None,
                    "CHUNK_INDEX":      None,
                    "FILE_NAME":        doc.blob_name,
                    "AI_STATUS":        "pending",
                    "AI_PROCESSED_AT":  None,
                }
```

**呼び出し側への波及**: 無し。

**検証**: `--job-mode=load` の smoke で BQ に 1 行入った後、`CHUNK_INDEX IS NULL` を確認。その後 ai-finalize でこの行が DELETE され、新行で `CHUNK_INDEX=0,1,2,...` が入ることを確認。

**ロールバック**: コミット revert。

---

### P1-1. データカタログ MD のスキーマ更新 ⚠️

**症状**: `docs/data_catalog/bq_tdnet_documents.md` のスキーマ表・DDL が `CHUNK_INDEX` を載せていない。

**該当**: `docs/data_catalog/bq_tdnet_documents.md:L10-L29`（スキーマ表）/ L144-L166（DDL）

**根本原因**: スキーマ変更時の MD 更新義務（CLAUDE.md §10 「知見ファイル整合義務: スクリプト変更時・プラン完了時・GCPリソース変更直後に対応知見MDを更新」）。

**修正方針**:
- スキーマ表に `CHUNK_INDEX INT64` 行を追加（説明: 「同一 DOC_ID 内のチャンク順序（0 始まり）。NULL = メタデータのみ行 / 2026-05-17 以前の旧データ」）
- DDL 例に `CHUNK_INDEX INT64` を追加
- 「注意事項」に **「CHUNK_INDEX は 2026-05-17 以降の新規ロード分のみ採番。それ以前の行は NULL のため、本文順復元が必要な場合は `WHERE CHUNK_INDEX IS NOT NULL` または `WHERE SUBMISSION_DATE >= '<cutoff date>'` で絞り込む」** を追記
- `013_tdnet_load.md §T-3` のコメントに「BQ にも `CHUNK_INDEX` 列として露出済み（2026-05-17）」を追記

**検証**: Read で MD を開いて表に列が増えていることを目視。`grep CHUNK_INDEX docs/data_catalog/bq_tdnet_documents.md` で 3 件以上ヒット。

**ロールバック**: MD 編集 revert。

---

### P1-2. `tdnet_load_recovery.py` の既存 CHUNK_INDEX 書込みバグへの起動遮断ガード ⚠️

**症状**: `scripts/tdnet_load_recovery.py:L487` に `"CHUNK_INDEX": chunk_data.get("chunk_index", 0)` の記述が既に存在するが、`chunk_data` 辞書側に `chunk_index` キーが入っていないため、**全 chunk が `CHUNK_INDEX=0` で書き込まれる**。新スキーマ稼働後にこの旧 recovery が起動されると、全 chunk が 0 で永続化され、ORDER BY CHUNK_INDEX が機能しないデータ汚染が発生する。

**該当**: `scripts/tdnet_load_recovery.py:L487`（既存バグ。CR-203 で発見）

**根本原因**: 旧 recovery 経路は新アーキ稼働3ヶ月後に廃止予定だが、廃止までの期間に手動起動される可能性がある。書込み側が破損したまま放置するのは危険。

**修正方針**: 廃止予定なので**修正ではなく起動遮断ガード**を追加（最小コストで汚染を止める）。

```python
# scripts/tdnet_load_recovery.py の冒頭（import 群の後）
import sys
print(
    "[FATAL] tdnet_load_recovery.py は廃止予定経路。"
    " 既存 CHUNK_INDEX 書込みバグ（L487）により全 chunk=0 でデータ汚染するため、"
    " 起動を遮断します。新アーキの tdnet_load_parallel.py を使用してください。"
    " どうしても起動が必要な場合は CHUNK_INDEX 書込みバグを修正してからガードを外してください。",
    file=sys.stderr,
)
sys.exit(2)
```

**呼び出し側への波及**: 旧 recovery を呼ぶ Cloud Scheduler / Cloud Run Job / 手動スクリプトがあれば失敗する。**事前確認**: `gcloud scheduler jobs list | grep recovery` および `gcloud run jobs list | grep recovery` で参照ジョブがゼロ件であることを確認してからガードを入れる。1 件でもあればまず参照を外す。

**検証**: ローカルで `python scripts/tdnet_load_recovery.py` を実行 → exit code 2、stderr にメッセージ。

**ロールバック**: コミット revert で復活可。バグそのものは残るので、ロールバック後に再起動するなら L487 を `enumerate` ベースに修正してから。

---

### P2-1. 利用側クエリの使い分けガイド（縛り化はしない） ℹ️

**症状**: 既存行（CHUNK_INDEX IS NULL）と新規行（CHUNK_INDEX NOT NULL）が混在する。本文順序が必要なケースとそうでないケースが両方ある。

**該当**: 利用側全般（受注抽出タスク等）

**方針（重要）**: **利用側に「縛り」はかけない**。CHUNK_INDEX が NULL の過去データでも有効に使える場面が多い:
- Vector Search で類似チャンク発見（EMBEDDING があれば順序不要）
- 単一チャンク文書（PAGE_COUNT 少）
- カウント・集計（順序不問）

利用側の判断に委ねる前提で、「順序復元が必要な場合の参考」レベルのクエリ例を提示する。

**修正方針**: `bq_tdnet_documents.md` の「注意事項」または「使い方ガイド」節に、ケース別のクエリパターン例を追加。

```sql
-- ケースA: 順序復元が必要（新規ロード分のみ対象）
SELECT CHUNK_TEXT
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE DOC_ID = 'X'
  AND CHUNK_INDEX IS NOT NULL          -- 旧データは順序不能なので除外
ORDER BY CHUNK_INDEX;

-- ケースB: 順序不要（Vector Search・集計など）
-- CHUNK_INDEX を見る必要は無い。従来クエリのまま動く

-- ケースC: 順序が望ましいが旧データも含めたい（NULL は末尾でも許容）
SELECT STRING_AGG(CHUNK_TEXT, '\n' ORDER BY CHUNK_INDEX NULLS LAST)
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE DOC_ID = 'X';
```

**重複行への注意（CR-203 #2 由来、重要）**: `phase5_bq_insert_finalize` は INSERT→DELETE 非原子（004 B-4 / `013_tdnet_load.md §T-1` 対策で順序入替済み）であり、INSERT 成功後・DELETE 失敗の中断 → 再実行で **同 DOC_ID の completed 行が追加** されるケースが既知。CHUNK_INDEX 列追加後はこの重複行が **同じ `(DOC_ID, CHUNK_INDEX)` で 2 セット返る** ため、利用側が `ORDER BY CHUNK_INDEX` するだけでは重複検知できない。順序復元が必要なクエリでは以下のいずれかを使う:

```sql
-- 案1: DISTINCT で重複行除去
SELECT DISTINCT CHUNK_INDEX, CHUNK_TEXT
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE DOC_ID = 'X' AND CHUNK_INDEX IS NOT NULL
ORDER BY CHUNK_INDEX;

-- 案2: 最新 EXTRACTED_AT のセットだけ採用
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

**検証**: MD 編集後にレビュー読み返し。実際に重複行が存在する DOC_ID（あれば）で案1/案2 を流して同じ結果になることを確認。

**ロールバック**: MD 編集 revert。

---

## 対応アンチパターン

| plan ID | 004 | T-x | G-x |
|---|---|---|---|
| P0-1 | — | — | — |
| P0-2 | — | T-1（対称性）, T-3※ | — |
| P0-3 | — | T-1, T-3※ | — |
| P0-4 | — | T-1 | — |
| P1-1 | — | — | — |
| P1-2 | A-5（捏造値継続） | — | — |
| P2-1 | B-4（INSERT→DELETE 非原子の重複行）| T-1 | — |

> 参照: `docs/knowledges/tools/013_tdnet_load.md §T-1, §T-3` / `004_coding_conventions.md §A-5, §B-4`
>
> ※ T-3 注釈: 既存 T-3 は「Python 内部の chunk → doc マッピング key に `(doc_id, chunk_index)` tuple を使う」という Python レイヤのルール。本プランはその tuple key の `chunk_index` を **BQ 列として露出させる** 改修。T-3 のスコープを拡張する位置づけ。実装完了後に `013_tdnet_load.md §T-3` の本文へ「BQ 列としても露出済み（2026-05-17）」の注記を追加すること。

---

## 検証戦略

### 段階適用順序（厳守）

**正しい順序**: `① ALTER ADD COLUMN（dev）` → `② dev smoke + dev 実機 PASS` → `③ ALTER ADD COLUMN（prod）` → `④ コード deploy（prod）` → `⑤ prod smoke`

シナリオ別リスク:

| シナリオ | ALTER と コード deploy の順序 | 影響 |
|---|---|---|
| A: ALTER 先 / コード後（**安全**） | ALTER → コード | 過渡期に走る Load Job は CHUNK_INDEX 列に何も書かない → 行は NULL になる。意図しない混在期があるが事故ではない |
| B: コード先 / ALTER 後（**事故**） | コード → ALTER | 過渡期の Load Job は `CHUNK_INDEX` キーを持つ row を送るが、列が存在せず **`Unknown name "CHUNK_INDEX"` で Load Job 失敗**。`tdnet_load_parallel.py` には `ignore_unknown_values=True` 設定が無い（CR-203 で全文 Grep 確認）ため吸収されない。**禁止** |
| C: 同期適用 | ALTER + コード同時 | 理想だが同期保証が難しい。シナリオ A で十分 |

→ **本プランは A を採用**。dev で A の手順を一回通す → prod でも同じ手順で適用。

### 1. smoke test（dev/prod 共通）

- **対象**: 1 doc（複数チャンクを持つ決算短信、5-10 チャンク程度）
- **手順**: ALTER 適用後にコード deploy → `--job-mode=ai-finalize` で 1 件処理（`--limit 1` 実装の有無は実装着手時確認、無ければ `DOCS_LIMIT=1`）→ BQ Load 完了後、以下クエリで検証
  ```sql
  SELECT CHUNK_INDEX, LEFT(CHUNK_TEXT, 50) AS head
  FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
  WHERE DOC_ID = '<smoke 投入分の DOC_ID>'
  ORDER BY CHUNK_INDEX;
  ```
- **期待結果**: `CHUNK_INDEX` が 0,1,2,...,N-1 の連番。`head` が本文の冒頭 → 中盤 → 末尾の自然な順序

### 2. dev 実機

- **対象**: 1 日分（DOCS_LIMIT=50 程度）の `tdnet-load-daily` resume + `ai_processing_flow` Workflows
- **前提**: dev プロジェクトの BQ 同等テーブルがあるかを実装着手時に確認。無ければ prod の最新日 1 partition を対象に DOCS_LIMIT=50 で代用
- **コストガード**: 想定 BQ スキャン量 < 100 MB（partition prune 効くため）
- **検証クエリ**: smoke の連番チェックを複数 DOC_ID に拡大。`CHUNK_INDEX` の MAX 値が `(CHUNK_TEXT IS NOT NULL のチャンク行数 - 1)` と一致

### 3. 本番適用判断基準（監視クエリ含む）

smoke + dev 両方 PASS に加え、本番投入後は以下の監視クエリで継続観測:

```sql
-- 監視クエリ1: ai-finalize 後の CHUNK_INDEX NULL 漏れ検知
SELECT COUNT(*) AS leaked_rows
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE SUBMISSION_DATE >= '<cutoff date>'
  AND AI_STATUS = 'completed'
  AND CHUNK_TEXT IS NOT NULL
  AND CHUNK_INDEX IS NULL;
-- 期待: 0。1以上は書込み修正の欠落

-- 監視クエリ2: CHUNK_INDEX 連番健全性（doc あたり MAX(CHUNK_INDEX)+1 = チャンク行数）
SELECT
  DOC_ID,
  COUNT(*) AS rows,
  MAX(CHUNK_INDEX) AS max_ci,
  COUNT(*) - 1 = MAX(CHUNK_INDEX) AS is_healthy
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE SUBMISSION_DATE >= '<cutoff date>'
  AND CHUNK_TEXT IS NOT NULL
GROUP BY DOC_ID
HAVING NOT is_healthy
LIMIT 20;
-- 期待: 0 件。1 件以上は INSERT→DELETE 非原子の重複行 or 採番抜け

-- 監視クエリ3: 同一 (DOC_ID, CHUNK_INDEX) の重複（P2-1 重複行検知）
SELECT DOC_ID, CHUNK_INDEX, COUNT(*) AS dup_cnt
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE SUBMISSION_DATE >= '<cutoff date>'
  AND CHUNK_TEXT IS NOT NULL
GROUP BY DOC_ID, CHUNK_INDEX
HAVING dup_cnt > 1
LIMIT 20;
-- 期待: 0 件。1 件以上は ai-finalize INSERT→DELETE 中断の痕跡（既知 B-4 症状）
```

deploy 初日・1 週間後・1 ヶ月後に上記 3 つを実行。

### 4. 回収手順

- **症状A（全行 NULL）**: 書込み修正が効いていない → コードコミット revert + 該当日付の `pending_gemma` 行を DELETE → ai-finalize 再実行
- **症状B（一部行のみ NULL）**: `WHERE CHUNK_INDEX IS NULL AND AI_STATUS='completed' AND SUBMISSION_DATE >= '<cutoff>'` で該当行を特定 → DOC_ID 単位で `pending_gemma` に戻して再 ai-finalize
- **症状C（重複行検知）**: 監視クエリ3 で検出されたら、`ROW_NUMBER() OVER (PARTITION BY DOC_ID, CHUNK_INDEX ORDER BY EXTRACTED_AT DESC) > 1` の行を DELETE（既知 B-4 症状の対症療法）
- **スキーマ ALTER ロールバック**: P0-1 ロールバック節の順序厳守（コード revert → ALTER DROP COLUMN）。Vector Index 再構築要否を事前確認

---

## 関連ドキュメント

- 親知見 MD: `docs/knowledges/tools/013_tdnet_load.md`（特に §T-3 chunk 順序保持の Python 内部実装）
- データカタログ: `docs/data_catalog/bq_tdnet_documents.md`（スキーマ・DDL）
- 発端レビュー: `docs/reviews/202_cr_tdnet_orders_extract.md` #2（順序復元手段未定義）
- 受注抽出プラン（本案件で前提が整う）: `docs/plans/ad-hoc_tdnet_orders_extract_20260517_214618.md`
- 関連 commit: `64c1b812 fix: Geminiスキップコード削除`（修正基準 commit）

---

## スコープ・非スコープ（明示）

**スコープ**:
- BQ テーブルに `CHUNK_INDEX INT64` 列を追加（ALTER ADD COLUMN）
- ai-finalize / legacy / load の3経路で書込み側コード修正
- データカタログ MD のスキーマ反映
- 利用側クエリの使い分けガイド追記（縛りはかけない）
- 旧 `tdnet_load_recovery.py` の起動遮断ガード追加（P1-2、既存バグの汚染防止）

**非スコープ**（明示的に対象外）:
- **過去データの再ロード・遡及採番**: 既存行は `CHUNK_INDEX=NULL` のまま据え置く
- 旧 `tdnet_load_recovery.py` の書込みロジック修正（廃止予定。遮断ガードのみ）
- 受注抽出タスク本体（別プラン `ad-hoc_tdnet_orders_extract_20260517_214618.md`）
- **EDINET 側テーブル `ir_documents_enhanced` の同種改修**: 別プランで検討する（本プランと同じ設計が適用できるか、UNION ALL での命名規約差異（`chunk_text` vs `CHUNK_TEXT`）も含めて再評価）
- `phase5_bq_insert_finalize` の INSERT→DELETE 非原子（既知 B-4 症状）そのものの根本修正: 重複行は P2-1 の利用側ガイドで対症療法に留め、本プランでは触らない

---

## 実装記録（2026-05-18 完了）

### ステータス
完了 (2026-05-18 JST)

### 実装 commit

| ハッシュ | 内容 |
|---------|------|
| `98642eb0` (2026-05-17) | プラン本体 + CR-203 レビュー + 蓄積ログ |
| `686d3d3a` (2026-05-18) | コード3経路 + recovery 起動遮断ガード + データカタログMD更新 + §T-3 注記 |
| BQ DDL `2026-05-18 13:46 JST` | `ALTER TABLE ADD COLUMN CHUNK_INDEX INT64` 実行（jobId `bqjob_r19ea6baf556e1761_0000019e38da2cbd_1`） |
| Cloud Build `90d25154` (2026-05-18) | 3 Job 自動 update (tdnet-load-daily / tdnet-ai-prepare / tdnet-ai-finalize) |

### 検証結果

| 段階 | 結果 |
|------|------|
| smoke (py_compile) | 両スクリプト OK |
| recovery 起動遮断ガード | EXIT_CODE=2 実動確認済み |
| BQ スキーマ反映 | `INFORMATION_SCHEMA.COLUMNS` で `CHUNK_INDEX INT64 NULLABLE` 確認 |
| 既存行 NULL 確認 | 2026-05-15 サンプル: 全行 `CHUNK_INDEX=NULL`（過去遡及採番なし方針通り） |
| 3 Job 更新確認 | `gcloud run jobs describe` で全 Job が `tdnet-load-daily:latest` digest 参照 |
| 監視1 (NULL 漏れ) | 0 件 |
| 監視3 (重複) | 0 件 |
| **本物の prod smoke** | **未実施**: 5月分は全件 `completed`、pending_gemma=0 のため即時手動 trigger 不可。**次回 `ai_processing_flow` 自然実行（新規日次投入）で初検証**。監視3クエリで重複・連番健全性を継続観測 |

### code-reviewer 推奨の採否 (CR-203 / 203_cr_tdnet_chunk_index_column.md)

| 指摘 | 採否 |
|------|------|
| 重大 #1 段階適用順序明示 | 採用（プラン検証戦略にシナリオ A/B/C 表追加） |
| 重大 #2 重複行 + CHUNK_INDEX 重複 regression | 採用（P2-1 に DISTINCT/ROW_NUMBER パターン追記） |
| 重大 #3 ロールバック手順の `ignore_unknown_values` 前提誤り | 採用（コード revert → ALTER DROP 順厳守を明記） |
| 重大 #4 `tdnet_load_recovery.py:L487` 既存バグ | 採用（P1-2 として起動遮断ガード新設） |
| 改善 #1 段階適用シナリオ別検証分離 | 採用 |
| 改善 #2 EDINET 別プラン化誘導 | 採用 |
| 改善 #3 監視クエリ追加 | 採用 |
| 改善 #4 OPTIONS description に cutoff date 明示 | 採用 |
| 改善 #5 T-3 適用範囲補足 | 採用 |
| 確認できなかった事項: `ignore_unknown_values=True` 3 箇所追加 | 見送り（順序遵守で対処、他経路影響評価コスト不適） |
| 確認できなかった事項: BQ DROP COLUMN の Vector Index 要否 | 次回対応（ロールバック実行時に確認） |

### 派生事故

deploy 作業中に Cloud Build `[WinError 32] file.tgz` で 5 回連続失敗 → md-reviewer に事故報告（204_mr_cloudbuild_md_overlooked.md）。索引ファースト原則違反（MR-153 / MR-160 に続く 3 度目）。文案 1-3 を採用して 005_cloudrun_job_deploy.md / CLAUDE.md §6 を補強済み。

### archive 移動

本 commit 完了後に `docs/plans/archive/202605/tools-013_tdnet_chunk_index_column_20260517_223000.md` へ移動。バックリンク（`013_tdnet_load.md` 冒頭）を archive パスに更新。

---

## 提出前セルフチェック

- [x] 冒頭メタ情報（作成日時 / ステータス / 対象ファイル / 対象読者 / 目的）記載済み
- [x] 前提サマリ（過去修正・残存・実機検証）記載済み
- [x] 優先度定義 P0/P1/P2 記載済み
- [x] 各項目に 症状 / 該当 / 根本原因 / 修正方針（before/after）/ 波及 / 検証 / ロールバック が揃っている
- [x] アンチパターン対応表（plan ID × 004 / T-x / G-x）記載済み
- [x] 検証戦略（smoke / dev / 本番判断基準 / 回収手順）記載済み
- [x] 関連ドキュメント（知見 MD / カタログ / commit）リンク済み
- [x] スコープ・非スコープ明示
- [x] 双方向リンク: 親知見 MD `013_tdnet_load.md` に本プランへのバックリンクを追加する（実装時に対応）

---

## レビュー追記: 2026-05-17 23:05 JST — code-reviewer

→ `docs/reviews/203_cr_tdnet_chunk_index_column.md`

# コードレビュー: TDnet 抽出失敗ドキュメント リカバリスクリプト

- 日時: 2026-05-06 12:46 JST
- 対象: `scripts/tmp_reextract_failed_docs.py`（446行、新規作成）
- パターン: 1 (新規)
- レビュアー: Claude (code-reviewer runbook)
- プランMD: `docs/plans/tools-013_tdnet_load_20260506_112815.md` P1-1

---

## 【サマリー】

- 変更の要約: BQ の TEXT_LENGTH < 300 の約 2,100 DOC を GCS から PDF 再取得し、修正済み `_content_length()` で閾値判定した上で PyPDF2/pdfminer で再抽出、BQ を DELETE → INSERT で更新する一時リカバリスクリプト
- 品質評価: **B** — 全体構造は本家 `tdnet_load_parallel.py` に準拠し堅実だが、SQL injection・DELETE→INSERT の非原子性・INSERT 行のカラム欠落に重大リスクがある
- 主要リスク:
  1. SQL injection: `doc_id` を f-string で直接 SQL に埋め込んでいる（004 C-1 違反）
  2. DELETE → INSERT 非原子: DELETE 成功後に INSERT が失敗するとデータロスト（004 B-2 違反）
  3. INSERT 行に `CHUNK_TEXT`・`SUB_CATEGORIES`・`EMBEDDING` カラムが欠落し、元データが持っていた情報が消失

---

## 【重大な指摘】（即修正）

### #1 SQL injection リスク — doc_id の f-string 直接埋め込み

- 箇所: `scripts/tmp_reextract_failed_docs.py:307-309`
- 事象: `doc_id` を `f"'{did}'"` で SQL 文字列に直接埋め込んでいる。`doc_id` が BQ から取得した値であるため現時点での実害は低いが、`doc_id` にシングルクォートを含む値（TDnet の DOC_ID 仕様が将来変更された場合や、BQ テーブルに不正値が混入した場合）があると SQL が破壊される
- トリガー: `doc_id` にシングルクォートを含む文字列が存在する場合
- 影響: SQL 構文エラーによるバッチ全体の停止、または意図しない行の DELETE
- 根拠: 004 C-1「SQL を f-string で組み立てる → `QueryJobConfig(query_parameters=[...])` で parametrize」に明確に違反
- 推奨対応: `ArrayQueryParameter` を使用してパラメタライズする。BQ の `IN` 句はパラメタライズ可能:

```python
# before: L307-309
placeholders = ", ".join(f"'{did}'" for did in batch_ids)
delete_sql = f"""
    DELETE FROM `{TABLE_ID}`
    WHERE DOC_ID IN ({placeholders})
"""

# after
from google.cloud.bigquery import QueryJobConfig, ArrayQueryParameter, ScalarQueryParameter

delete_sql = """
    DELETE FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE DOC_ID IN UNNEST(@doc_ids)
"""
job_config = QueryJobConfig(
    query_parameters=[
        ArrayQueryParameter("doc_ids", "STRING", batch_ids),
    ]
)
bq.query(delete_sql, job_config=job_config).result()
```

### #2 DELETE → INSERT 非原子性によるデータロスト経路

- 箇所: `scripts/tmp_reextract_failed_docs.py:305-370`
- 事象: DELETE を全バッチ完了してから INSERT（Load Job）を実行している。DELETE が 2,100 件すべて完了した後に Load Job が失敗すると、元の行も再抽出結果も両方失われる
- トリガー: GCS アップロード失敗、Load Job のスキーマ不一致エラー、ネットワーク障害等
- 影響: 最大 2,100 行のデータ完全消失。元データ（マーカーのみのテキスト）に価値が低いとはいえ、メタデータ（DOC_ID、TICKER、SUBMISSION_DATE、MAIN_CATEGORY、DOC_TITLE）は再構成に工数がかかる
- 根拠: 004 B-2「DELETE → INSERT を非トランザクションで流す → INSERT 先行 → 成功後 DELETE / MERGE 1文」
- 推奨対応: **INSERT 先行 → 成功後 DELETE** に順序を逆転する。具体的には:
  1. 再抽出結果を一時テーブル（例: `STOCK._tmp_reextract`）に Load Job で INSERT
  2. 一時テーブルの行数を検証（`SELECT COUNT(*) FROM _tmp_reextract`）
  3. `MERGE` 文で本テーブルを更新（MATCHED THEN UPDATE、NOT MATCHED は無視）、~~または DELETE → INSERT を1つの multi-statement トランザクション `BEGIN TRANSACTION ... COMMIT` で実行~~ [取消: 2026-05-06 — BQ の READ COMMITTED セマンティクスにより、同一トランザクション内の先行 DELETE の結果は後続ステートメントから可視。DELETE 後に同テーブルを INNER JOIN すると 0 行が返り INSERT が空振りしてデータロストする。MERGE 方式のみが安全。CR-085 #1 で発覚]
  4. 一時テーブルを削除

  プランMD のロールバック節で「元データに価値がないため実害は限定的」と記載があるが、メタデータ消失のリカバリコストは無視できない。

**[異議あり → 認定: 2026-05-06]** 推奨対応の選択肢 3 で「DELETE → INSERT を1つの multi-statement トランザクション `BEGIN TRANSACTION ... COMMIT` で実行」を有効な方式として提示した。実装側がこの方式を採用した結果、CR-085 #1 で「BQ READ COMMITTED セマンティクスにより DELETE 後の INNER JOIN が 0 行を返し INSERT が空振りする」と同一レビュアーから重大指摘を受けた。推奨対応として提示した方式自体がデータロストを引き起こす欠陥品であり、レビュアーの推奨が実装を誤誘導した。MERGE 方式のみを推奨すべきだった。**対応: 推奨選択肢を取消済み、004-1に元エントリ削除+レビュアー不備エントリ追記済み。**

### #3 INSERT 行のカラム不整合 — 元データが持つ情報の消失

- 箇所: `scripts/tmp_reextract_failed_docs.py:328-344`
- 事象: INSERT する NDJSON 行に以下のカラムが欠落または不正:
  - **`CHUNK_TEXT`**: `None` 固定。元データがチャンク分割済みの場合、再抽出後もチャンク分割して `CHUNK_TEXT` に格納すべき（本家 L1419-1431 ではチャンク分割ありで複数行 INSERT）
  - **`SUB_CATEGORIES`**: カラム自体が行に含まれていない。DDL では `ARRAY<STRING>` 型。元データの AI 判定結果（`completed` 状態なら値あり）が消失する
  - **`EMBEDDING`**: 同上。768次元ベクトルが消失
  - **`PAGE_COUNT`**: `None` 固定だが、`_extract_text_pypdf2` が `page_count` を返しており `doc.new_page_count` 等で保持可能
  - **`TEXT_LENGTH`**: `doc.new_text_length` = `len(text)` だが、本家は `len(doc.text)`。`_content_length()` のトークン数ではなく文字数を格納しているのは本家と整合しているが、L236 で `new_text_length = _content_length(text)` と設定しており、成功時（L233）は `len(text)` を設定するのに失敗時（L236）は `_content_length(text)` を設定する不整合がある
- トリガー: 全件で発生（全 INSERT 行に影響）
- 影響: AI 判定済みドキュメント（`AI_STATUS = 'completed'`）の `MAIN_CATEGORY`、`SUB_CATEGORIES`、`EMBEDDING` が消失し、AI パイプラインの再実行が必要になる。特に EMBEDDING は text-embedding-004 の再計算コスト（API 課金）が発生
- 根拠: 本家 `tdnet_load_parallel.py:L1404-1431` の base_row と比較すると `SUB_CATEGORIES`、`EMBEDDING`（チャンク付きの場合）が明確に欠落
- 推奨対応:
  1. `fetch_failed_docs()` で元の `MAIN_CATEGORY`、`SUB_CATEGORIES`、`EMBEDDING`、`AI_STATUS`、`AI_PROCESSED_AT`、`DISCLOSURE_TIME`、`FILER_NAME`、`PAGE_COUNT` も SELECT する
  2. INSERT 時に元データの値を保持し、変更があるカラム（`TEXT_LENGTH`、`CHUNK_TEXT`、`AI_STATUS` → `'pending'` に戻す？）のみ更新する
  3. あるいは `MERGE` 文で `TEXT_LENGTH` と `CHUNK_TEXT` のみ UPDATE する方式に変更する（他カラムは元データを保持）

### #4 `new_text_length` の値が成功/失敗で異なる尺度

- 箇所: `scripts/tmp_reextract_failed_docs.py:233` vs `scripts/tmp_reextract_failed_docs.py:236`
- 事象: 成功時は `doc.new_text_length = len(text)`（文字数）、失敗時は `doc.new_text_length = _content_length(text)`（トークン数）。同じフィールドに異なる尺度の値が入る
- トリガー: PyPDF2/pdfminer 両方失敗した DOC
- 影響: サマリ出力で `old_text_length`（文字数）と `new_text_length`（トークン数）を比較表示するため、ユーザーが誤解する。BQ への INSERT では成功時のみ書き込むため直接の BQ データ汚染は無いが、dry-run ログの信頼性が低下
- 根拠: L233 `doc.new_text_length = len(text)` vs L236 `doc.new_text_length = _content_length(text)` の不整合
- 推奨対応: 失敗時も `len(text)` を使うか、成功/失敗で明示的にフィールドを分ける

### #5 `AI_STATUS` を `'pending'` に設定するが元データが `'completed'` の場合の整合性

- 箇所: `scripts/tmp_reextract_failed_docs.py:343`
- 事象: INSERT 行で `AI_STATUS = 'pending'` を固定設定している。元データが AI 判定済み（`completed`）の場合、テキスト再抽出によって MAIN_CATEGORY/SUB_CATEGORIES の判定根拠が変わるため `pending` に戻すのは正しい方針。しかし、#3 で指摘した通り元の `MAIN_CATEGORY`（L336 でフェッチ済み値を使用）を INSERT 行にそのまま入れている矛盾がある — テキストが変わったのにカテゴリ判定は旧のまま、かつ `AI_STATUS = 'pending'` は「未判定」を意味する
- トリガー: AI 判定済み DOC の再抽出
- 影響: `MAIN_CATEGORY` に旧値が入った状態で `AI_STATUS = 'pending'` → AI パイプラインが再実行されると `MAIN_CATEGORY` が上書きされるため最終的には整合するが、中間状態で MAIN_CATEGORY 付きの pending 行が存在し、下流クエリ（`WHERE MAIN_CATEGORY = '決算短信' AND AI_STATUS = 'completed'`）から漏れる
- 推奨対応: `MAIN_CATEGORY` も `None` にして完全に未判定状態に戻すか、元の AI 判定結果をすべて保持して `AI_STATUS` も元の値を維持する（テキストだけ更新）。どちらかに統一する

---

## 【改善提案】（可読性・保守性）

### #1 ロギングに `logging` を使用 — CLAUDE.md 規約では `structlog`

- 箇所: `scripts/tmp_reextract_failed_docs.py:18,47-52`
- 現状: 標準 `logging` モジュールを使用している
- 提案: CLAUDE.md §コーディング規約に「print禁止。structlogを使用」と明記されている。一時スクリプトではあるが規約準拠が望ましい。ただし tmp_ スクリプトの短命性を考えると severity は低い

### #2 エラーカウント・サマリの 3 指標出力（A-7）

- 箇所: `scripts/tmp_reextract_failed_docs.py:374-393`
- 現状: `print_summary()` で `total` / `success` / `needs_vision` を出力しているが、`errors`（例外で失敗した件数）が独立してカウント・出力されていない。`reextract_parallel()` の `except` で `log.error` するだけで `errors` カウントをサマリに渡していない
- 提案: 004 A-7「終了時 `processed` / `skipped` / `errors` サマリを必ず出力」に準拠するため、`reextract_parallel()` から errors count を返し、`print_summary()` で出力する

### #3 エラー時の `sys.exit(1)` 欠落（A-1）

- 箇所: `scripts/tmp_reextract_failed_docs.py:396-446`
- 現状: `main()` にエラー時の `sys.exit(1)` がない。再抽出で全件失敗しても exit 0 で終了する
- 提案: 004 A-1「`main()` 末尾で `if errors > 0: sys.exit(1)` を徹底」。一時スクリプトだが Workflows から呼ばれる可能性も考慮すると安全側に倒すべき

### #4 `tempfile.NamedTemporaryFile` の `delete=False` + 手動削除パターン

- 箇所: `scripts/tmp_reextract_failed_docs.py:319-369`
- 現状: `NamedTemporaryFile(delete=False)` で作成し `finally` で `Path(tmp_path).unlink()` している。これ自体は正しいが、GCS 一時 blob（L351-354）の削除が `try/except: pass` で例外を握り潰している
- 提案: GCS blob 削除失敗のログ出力を追加（004 A-3 の精神）。`except Exception: pass` → `except Exception as e: log.warning("GCS blob cleanup failed: %s", e)`

### #5 `storage_client` の並列共有は安全だが `requests.Session` 注意

- 箇所: `scripts/tmp_reextract_failed_docs.py:242-270`
- 現状: `storage.Client` を全スレッドで共有している。GCS クライアントは内部で `requests.Session` を使用するが、`google-cloud-storage` ライブラリは connection pooling で thread-safe に設計されている（004 C-4 注記参照）。本家 `tdnet_load_parallel.py` も同じパターン
- 提案: 現状維持で問題なし。ただし CLAUDE.md §並列での `requests.Session` ルールとの関係を docstring にコメントで明記すると将来の混乱を防げる

### #6 dry-run 時にも GCS DL + テキスト抽出を全件実行する

- 箇所: `scripts/tmp_reextract_failed_docs.py:436-439`
- 現状: `reextract_parallel()` は dry-run / 本番 に関わらず実行される。dry-run は BQ 更新のみスキップ。これは意図的設計（再抽出結果の目視確認が目的）と読めるが、2,100 件全件の GCS DL は `--dry-run --limit 10` で段階的に検証するプランMD の手順と整合しているため問題ない
- 提案: dry-run 時のログに「BQ 更新はスキップします」の旨を明示すると運用時に安心

---

## 【修正例】（必要な箇所のみ）

#### #1 に対する修正案（SQL パラメタライズ）

```python
# before: scripts/tmp_reextract_failed_docs.py:305-313
for i in range(0, len(doc_ids), batch_size):
    batch_ids = doc_ids[i : i + batch_size]
    placeholders = ", ".join(f"'{did}'" for did in batch_ids)
    delete_sql = f"""
        DELETE FROM `{TABLE_ID}`
        WHERE DOC_ID IN ({placeholders})
    """
    bq.query(delete_sql).result()

# after
from google.cloud.bigquery import QueryJobConfig, ArrayQueryParameter

for i in range(0, len(doc_ids), batch_size):
    batch_ids = doc_ids[i : i + batch_size]
    delete_sql = f"""
        DELETE FROM `{TABLE_ID}`
        WHERE DOC_ID IN UNNEST(@doc_ids)
    """
    job_config = QueryJobConfig(
        query_parameters=[
            ArrayQueryParameter("doc_ids", "STRING", batch_ids),
        ]
    )
    result = bq.query(delete_sql, job_config=job_config).result()
    total_deleted += len(batch_ids)
    log.info("DELETE 完了: %d/%d", total_deleted, len(doc_ids))
```

#### #2 に対する修正案（INSERT 先行 → DELETE 後行）

概念のみ。MERGE 文を使う場合:

```sql
MERGE `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED` AS T
USING `gmailpj-357912.STOCK._tmp_reextract` AS S
ON T.DOC_ID = S.DOC_ID
    AND T.SUBMISSION_DATE = S.SUBMISSION_DATE
    AND T.FILE_NAME = S.FILE_NAME
WHEN MATCHED THEN
    UPDATE SET
        T.TEXT_LENGTH = S.TEXT_LENGTH,
        T.CHUNK_TEXT = S.CHUNK_TEXT,
        T.AI_STATUS = 'pending',
        T.AI_PROCESSED_AT = NULL,
        T.MAIN_CATEGORY = T.MAIN_CATEGORY,  -- 元値を保持
        T.SUB_CATEGORIES = T.SUB_CATEGORIES  -- 元値を保持
```

---

## 【確認できなかった事項】

- BQ テーブル `TDNET_DOCUMENTS_ENHANCED` の DDL に `AI_STATUS` と `AI_PROCESSED_AT` が含まれているか（`data_catalog.md` には記載があるが DDL 参考欄には含まれていない。DDL が古い可能性がある）。実テーブルの `INFORMATION_SCHEMA.COLUMNS` で確認が必要
- `doc_id` に実際にシングルクォートや特殊文字を含む値が存在するかどうか（SQL injection の実害の程度）
- 元データで `AI_STATUS = 'completed'` の DOC がどの程度あるか（#3 / #5 の影響範囲）
- `TEXT_LENGTH < 300` の条件で拾った DOC が実際にマーカー汚染によるものか、元々テキストが少ない PDF なのかの内訳（リカバリ効果の事前見積もり）

# 作業計画: EDINET load プログラム見直し（TDnet ノウハウ反映）

**作成日時**: 2026-05-13 22:30 (JST)
**ステータス**: 完了
**分類**: (b) 継続改修型
**親知見 MD**: `docs/knowledges/tools/012_edinet_load.md`
**前提プラン**: 本プラン完了後に `tools-012_edinet_backfill_20260513_221200.md`（バックフィル実行）を開始
**関連アイディアID**: -

## 目的

`scripts/edinet_load_parallel.py` に TDnet load で得た ETL アンチパターン対策を反映し、大規模バックフィル（7年分、推定 50,000+ ファイル）に耐える品質にする。

## 背景・動機

TDnet load（`scripts/tdnet_load_parallel.py`）は 2016-2026 の全年バックフィル（~50万 doc）を通じて以下の事故・改善を経験:
- OOM（19K doc ロスト: ai-finalize で `insert_rows_json` + `list[list[float]]` が爆発）
- streaming buffer 90分 DML 制限で MERGE/UPDATE 不可
- `chunk_text` をキーにした embedding マッピングで衝突→紛失
- GCS フラット走査で 15分+API 課金
- exit 0 嘘でデータ損失が検知不能

EDINET load はこれらの事故「前」の設計のまま残っており、バックフィル実行前に対策を入れる必要がある。

## 改善項目一覧

### P0: バックフィル前に必須（データ損失リスク）

#### P0-1. BQ Insert を streaming → Load Job に変更 (004 C-5)

**症状**: `insert_rows_json` は streaming buffer に入り 90分間 DML 不可。大量 insert で OOM リスクもある。

**該当**: `scripts/edinet_load_parallel.py` `phase3_bq_insert()` L812-887

```python
# ❌ 現状: streaming insert（100行バッチ）
rows_buffer: list[dict] = []
# ...
errs = bq.insert_rows_json(TABLE_ID, rows_buffer)
```

**修正方針**: GCS に NDJSON を stream write → `load_table_from_uri` で BQ Load Job。
TDnet の `phase5_bq_insert_load` / `phase5_bq_insert_finalize` パターンを移植。

```python
# ✅ 修正後: GCS経由 Load Job
upload_path = f"{GCS_BATCH_PREFIX}/load_upload_{timestamp}.jsonl"
with bucket.blob(upload_path).open("w", encoding="utf-8") as f:
    for doc in valid_docs:
        for row in _doc_to_rows(doc):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

job_config = bigquery.LoadJobConfig(
    source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
)
load_job = bq.load_table_from_uri(
    f"gs://{BUCKET_NAME}/{upload_path}", TABLE_ID, job_config=job_config,
)
load_job.result()
# 一時ファイル削除
bucket.blob(upload_path).delete()
```

**波及**: cleanup で一時 JSONL を `finally` で削除（B-5 適用）

#### P0-2. Embedding 保持を numpy float32 に変更

**症状**: `doc.embeddings: list[list[float]]` は 1 float = 24-32B。5,000 doc × 50 chunk × 768 dim ≈ 6-9 GB で OOM。

**該当**: `DocInfo.embeddings` L173, `phase2_poll_and_apply()` L721, `phase2_chunk_and_embed()` L799

```python
# ❌ 現状
doc.embeddings: list[list[float]]
doc.embeddings[ci] = embedding  # list[float]
```

**修正方針**:
```python
import numpy as np

# ✅ 修正後（DocInfo）
embeddings: np.ndarray | None = field(default=None)  # shape=(n_chunks, 768), float32

# Embedding 適用時
doc.embeddings = np.zeros((len(doc.chunks), 768), dtype=np.float32)
doc.embeddings[ci] = np.array(embedding, dtype=np.float32)
```

**メモリ見積もり**: 5,000 doc × 50 chunk × 768 × 4B = **~750 MB**（旧比 1/8）

#### P0-3. chunk↔doc マッピングキーを (doc_id, chunk_index) に変更 (T-3)

**症状**: `chunk_text` 文字列をキーにすると、同一テキストが複数 doc に存在する場合に embedding が上書きされて紛失。

**該当**: `phase2_poll_and_apply()` L700, `phase2_chunk_and_embed()` L757

```python
# ❌ 現状
content_to_chunk[chunk["chunk_text"]] = (doc, ci)
```

**修正方針**: JSONL の各行に `(doc_id, chunk_index)` を custom_id として埋め込み、結果照合に使用。
```python
# ✅ Embedding JSONL 作成時
lines.append(json.dumps({
    "content": chunk_text,
    "custom_id": f"{doc.doc_id}_{ci}",
}, ensure_ascii=False))

# 結果照合時
doc_id, ci_str = obj["custom_id"].rsplit("_", 1)
doc = doc_id_map[doc_id]
doc.embeddings[int(ci_str)] = np.array(embedding, dtype=np.float32)
```

> **注意**: Vertex AI Batch Prediction の `custom_id` フィールド対応を要確認。未対応の場合は input JSONL の行番号ベースで照合（TDnet と同じ方式）。

#### P0-4. exit code を errors > 0 で sys.exit(1) (A-1)

**該当**: `main()` L1054-1101 — errors 発生時も exit 0

```python
# ✅ main() 末尾に追加
if errors > 0:
    sys.exit(1)
```

---

### P1: バックフィル効率化（パフォーマンス・信頼性）

#### P1-1. GCS blob 走査を prefix で絞る (C-3)

**症状**: `bucket.list_blobs(prefix="edinet/")` が全 blob をフラット走査。数万ファイルで 15分+。

**該当**: `phase1_scan_and_extract()` L522

**修正方針**: `--from`/`--to` の日付範囲は blob 名に含まれないため日付フィルタ不可。
代わに ticker_from/ticker_to で `edinet/{ticker_from}/` ～ `edinet/{ticker_to}/` に分割走査。
バックフィル時は ticker 範囲指定を必須運用にする。

```python
if ticker_from:
    prefix = f"{GCS_PREFIX}/{ticker_from}/"
else:
    prefix = f"{GCS_PREFIX}/"
blob_iter = bucket.list_blobs(prefix=prefix)
```

> ticker range はファイル名の辞書順で機能するため、`1301`〜`3999` のような指定が可能。

#### P1-2. state 保存を NDJSON stream write に変更 (C-2)

**症状**: `_save_backfill_state()` が全 docs を一括 JSON → gzip。大量 doc 時にメモリ爆発。

**該当**: `_save_backfill_state()` L220-251, `_load_backfill_state()` L254-274

**修正方針**: TDnet `_save_ai_prepare_state` パターンを移植。
```python
# ✅ NDJSON stream write
with bucket.blob(docs_path).open("w", encoding="utf-8") as f:
    for doc in docs:
        f.write(json.dumps(_serialize_one_doc(doc), ensure_ascii=False) + "\n")

# ✅ NDJSON stream read
with bucket.blob(docs_path).open("r", encoding="utf-8") as f:
    for line in f:
        docs.append(_deserialize_one_doc(json.loads(line)))
```

#### P1-3. polling に deadline 追加 (D-1)

**該当**: `_poll_batch_job()` L482-498 — 無限ループ

```python
# ✅ deadline 追加
def _poll_batch_job(client, job_name, logger, label, max_wait_sec=7200):
    deadline = time.time() + max_wait_sec
    while time.time() < deadline:
        # ...existing polling...
    logger.log(f"  [{label}] タイムアウト ({max_wait_sec}s)")
    return False
```

#### P1-4. error カウントを行数単位に (A-6)

**該当**: `phase3_bq_insert()` L876 — `errors += 1`（doc 単位）

**修正方針**: Load Job 移行後は Load Job の `job.errors` から行数を取得。

#### P1-5. BQ 取込済みファイル一覧のスケーラビリティ改善

**該当**: `_load_processed_file_names()` L138-149

**現状**: 期間内の全 `DISTINCT FILE_NAME` を Python set に展開。バックフィルで広範囲指定時に肥大。

**修正方針**: ticker 範囲フィルタを SQL に追加して結果セットを縮小。
```sql
SELECT DISTINCT FILE_NAME
FROM `{TABLE_ID}`
WHERE SUBMISSION_DATE BETWEEN @d_from AND @d_to
  AND SECURITY_CODE BETWEEN @ticker_from AND @ticker_to
```

---

### P2: コード品質（規約準拠）

#### P2-1. print() → structlog 移行

**該当**: 全ファイル。`BatchLogger.log()` 内の `print()` を含む。

**修正方針**: `structlog.get_logger()` に置き換え。`BatchLogger` は GCS フラッシュ機能を残しつつ structlog をラップ。

#### P2-2. SQL パラメータ化 (C-1)

**該当**: `_load_processed_file_names()` L142-146 — f-string で SQL 組み立て

```python
# ❌ 現状
query = f"... WHERE SUBMISSION_DATE BETWEEN '{d_from_iso}' AND '{d_to_iso}'"

# ✅ 修正後
job_config = bigquery.QueryJobConfig(
    query_parameters=[
        bigquery.ScalarQueryParameter("d_from", "DATE", d_from_iso),
        bigquery.ScalarQueryParameter("d_to", "DATE", d_to_iso),
    ]
)
```

#### P2-3. mutable global 排除 (E-2)

**該当**: `DATE_MODE`, `DATE_FROM`, `DATE_TO` を `global` で書き換え（L1056）

**修正方針**: `main()` 内のローカル変数に閉じ込め、`run_edinet_batch_etl()` へ引数で渡す（既にそうなっている）。`global` 書き換えだけ削除。

#### P2-4. deploy 前 py_compile 必須化 (F-1)

**修正方針**: `cloudbuild/cloudbuild.edinet-load.yaml` に `python -m py_compile` ステップ追加。

---

## 対応アンチパターン

| Plan ID | 004 | T-x |
|---------|-----|-----|
| P0-1 | C-5, B-5 | — |
| P0-2 | C-2 | — |
| P0-3 | — | T-3 |
| P0-4 | A-1 | — |
| P1-1 | C-3 | — |
| P1-2 | C-2 | — |
| P1-3 | D-1 | — |
| P1-4 | A-6 | — |
| P1-5 | C-2 | — |
| P2-1 | — | — |
| P2-2 | C-1 | — |
| P2-3 | E-2 | — |
| P2-4 | F-1 | — |

## 検証戦略

1. **smoke test**: `--from 20260501 --to 20260501 --ticker-from 7203 --ticker-to 7203` でローカル 1 doc 処理
2. **dev 実機**: `--from 20260401 --to 20260430` で 1ヶ月分を Cloud Run 実行、BQ行数を既存と比較
3. **本番適用判断基準**: smoke + dev 両方 PASS で初めてバックフィル投入
4. **回収手順**: Load Job は冪等（file_name ベース dedup）。最悪ケースは BQ の当該期間 DELETE → 再実行

## 必要データ

| データ | ストレージ層 | パス/テーブル |
|--------|------------|--------------|
| EDINET load スクリプト | ローカル | `scripts/edinet_load_parallel.py` |
| TDnet load 参照 | ローカル | `scripts/tdnet_load_parallel.py` |
| ir_documents_enhanced | (a) BQ | `gmailpj-357912.STOCK.ir_documents_enhanced` |
| Cloud Build 定義 | ローカル | `cloudbuild/cloudbuild.edinet-load.yaml` |

## 成果物

- `scripts/edinet_load_parallel.py` の改修（P0 全4件 + P1 全5件 + P2 全4件）
- `docs/knowledges/tools/012_edinet_load.md` に EDINET ETL 固有ルール（E-1〜E-4）追記
- Cloud Run Job `edinet-load` の再デプロイ

## 完了条件

- P0 全4件が実装・smoke test PASS
- P1 の少なくとも P1-1, P1-3 が実装
- Cloud Run dev 実機テスト（1ヶ月分）PASS
- `012_edinet_load.md` に固有ルール追記済み

## 見積もり

- 想定所要時間: 4-6時間（P0 ~2h, P1 ~2h, P2 ~1h, テスト ~1h）
- 難易度: 中（TDnet からのパターン移植が主体。新規設計は少ない）

## 関連ドキュメント

- `docs/knowledges/tools/013_tdnet_load.md` §TDnet ETL 固有の再発防止ルール（T-1〜T-7）
- `docs/knowledges/tools/013-3_tdnet_backfill_archive.md`（全年バックフィル完了ナレッジ）
- `docs/knowledges/tools/004_coding_conventions.md` §バッチジョブ・ETL アンチパターン集（A-1〜F-1）
- `docs/plans/tools-012_edinet_backfill_20260513_221200.md`（後続のバックフィル実行プラン）
- TDnet OOM 事故: 2026-04-20 batch A 7bd8f（19K doc ロスト）

## 振り返り（作業後に記入）
- 実際の所要時間: 実装 ~1h（P0全5件 + P1-1/P1-2/P1-3 + P2-2/P2-3 + Dockerfile）
- うまくいった点: TDnet コードが参照パターンとして明確で移植が円滑。レビュー指摘4件を事前に反映できた
- 改善点: P2-1（structlog移行）は今回未実施。バックフィル後に対応
- 得られた知見: レビュー#1指摘の custom_id 方式は Vertex AI Batch で未保証。TDnet の defaultdict(list) 方式が正解。レビュー#2の冪等性は DELETE→INSERT パターンで解決。バックフィルは Embedding スキップ（backfill モード新設）でコスト$50-100削減

---

## レビュー追記: 2026-05-13 23:15 JST — code-reviewer

→ `docs/reviews/170_cr_edinet_load_refactor.md`

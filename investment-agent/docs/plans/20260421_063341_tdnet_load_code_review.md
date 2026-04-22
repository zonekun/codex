# tdnet_load_parallel.py 総点検・修正指示書

**作成日時**: 2026-04-21 06:33 JST
**改訂**: 2026-04-21 08:45 JST（code-reviewer 指摘反映 v2）
**対象ファイル**: `scripts/tdnet_load_parallel.py`（2,492 行、commit 2f41744 時点）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: 2026-04-20 の 13 件修正（commit 2f41744）で残った regression / 未実装 + 46 件総点検で未修正の課題を、優先度付きで実装する

## 改訂履歴 (v2)

code-reviewer 指摘（末尾「レビュー追記: 2026-04-21 08:30 JST」）を反映:
- **P0-3 修正方針訂正**（M-1）: 「既存 except 強化」→「新規 try/except 追加」（呼び出し側に try/except は無かった）
- **P0-7 新設**（M-2）: ai-finalize で text 欠損 doc が pending_* 行と共に消滅する経路を修正
- **P0-5 + P1 D-4 統合**（M-4）: `_update_ai_status` の parametrize と `num_dml_affected_rows` 移行を同時対応
- **P0-1 + P2-5 統合**（M-5, #5）: Phase 5 interface を `(processed, skipped)` に変更、失敗は raise。errors は ai-finalize 側で `phase5_errors + gemma_missing_total + text_missing_count` で合算
- **P0-6 → P2 降格**（改善提案 #2）: デッドコードで RESUME ブロッカーにしない
- **P1-3 拡張**（M-3）: `phase_gemini_tanshin_batch` 経由の orphan cancel も `_phase3_submit` 修正で救済される旨明記
- **P1 新規 M-6**: `_download_batch_results` ストリーム化（004 C-2 違反）
- **P1-4 拡張 M-7**: Vision OCR 個別 parse 失敗件数カウント
- **NR-2**: P0-4 fail-fast に tenacity retry + 最終 raise の 2 段構えを採用
- **NR-3**: P0-5 SQL parametrize 時に `UNNEST(@doc_ids)` の BQ パラメータサイズ上限確認

---

## 前提サマリ

- 2026-04-20 に batch A 7bd8f ai-finalize NameError 事故の全行点検で **46 件の設計課題**を抽出
- 同日 commit 2f41744 で **13 件を一括修正**（T-1 / B-1〜B-4 / T-3 / #6 / T-7 / T-8 / T-10 / T-9-partial / G-1 / G-2 / G-3）
- 2026-04-20〜04-21 batch B resume（ticker 6367-9997、26,940 doc）で実機検証 → 26,941 doc completed で BQ 移行成功
- **ただし review で 33 件残存 + 修正の副作用で 2 件 regression 発覚**

本書は残存項目を **P0 / P1 / P2** に分けて提示する。P0 は scheduler RESUME 前必須、P1 は次の backfill/日次安定化に向けて、P2 は余力で。

---

## P0（scheduler RESUME 前に修正必須）

### P0-1. G-2 + B-3 variable overwrite regression 🔴

**症状**: Gemma missing 件数を errors に加算したが、直後の Phase 5 戻り値で errors が 0 に上書きされ、B-3 cleanup gate が無効化される。

**該当**: `run_tdnet_batch_etl` ai-finalize ブロック

```
L2213-2215:  if gemma_missing > 0:
                 errors += gemma_missing      # ここで errors = 14
                 logger.log(...)
...
L2246:       processed, skipped, errors = phase5_bq_insert_finalize(docs, bucket, logger)
             #                           ^^^^^^ ここで errors=0 に上書き
...
L2259:       if processed > 0 and errors == 0:   # 常に真になる
                 _cleanup_ai_state(bucket, run_id, logger)
```

**根本原因**: `errors` を「累積」と「Phase 5 local」の二用途で使い回し → tuple unpack で上書き。004 A-3「副作用関数は失敗時 raise、正常系返り値に errors を混ぜない」に違反した旧設計を残したまま G-2 を被せた。

**修正方針**: `gemma_missing_total` を独立変数に保持、cleanup gate で合算判定。

```python
# L2211 から修正
gemma_missing = _apply_gemma_results(docs, gemma_results, logger)
gemma_missing_total = gemma_missing   # 新規: 独立追跡
if gemma_missing > 0:
    logger.log(f"[G-2] Gemma missing={gemma_missing} 件、cleanup gate に反映")

# L2246（変更なし、Phase 5 自身の errors のみ受け取る）
processed, skipped, phase5_errors = phase5_bq_insert_finalize(docs, bucket, logger)

# L2259 差し替え
total_errors = phase5_errors + gemma_missing_total
if processed > 0 and total_errors == 0:
    _cleanup_ai_state(bucket, run_id, logger)
else:
    logger.log(
        f"GCS state cleanup をスキップ（processed={processed}, "
        f"phase5_errors={phase5_errors}, gemma_missing={gemma_missing_total}）"
    )

# finally サマリ (L2272) も total_errors を出力
logger.log(f"成功/スキップ/エラー(Phase5): {processed} / {skipped} / {phase5_errors}")
logger.log(f"Gemma missing: {gemma_missing_total}")
```

**検証**: `_apply_gemma_results` が missing>0 を返すよう gemma_CURRENT.jsonl を 1 行欠けた状態で構築し、cleanup skip ログが出ること / state が GCS に残ることを確認。

---

### P0-2. T-6 chunk_text key collision（commit で未実装）🔴

**症状**: `content_to_chunk[chunk_text]` key が文字列で重複 chunk を上書き。同一テキスト chunk が別 doc に紐付き、embedding 適用時にどちらか一方に集約 → 他方が `embedding_set=False` のまま BQ 書込で embedding 欠落。

**該当**: `phase4_chunk_and_embed`

```
L1271:  content_to_chunk: dict[str, tuple[DocInfo, int]] = {}
L1274:  for doc in embed_docs:
L1275:      for ci, chunk in enumerate(doc.chunks):
L1276:          chunk_text = chunk["chunk_text"]
L1277:          content_to_chunk[chunk_text] = (doc, ci)   # ← 重複で上書き
L1278:          lines.append(json.dumps({"content": chunk_text}, ensure_ascii=False))
...
L1314:          chunk_text = obj["instance"]["content"]
L1315:          embedding = obj["predictions"][0]["embeddings"]["values"]
L1316:          mapping = content_to_chunk.get(chunk_text)   # ← 元 key が衝突していれば誤紐付け
```

**根本原因**: Embedding API は `content` を key として結果を返すため、同一 content → 同一 result が 1 件しか返らない。送信時点で重複 content をまとめて送れば受信側の map も 1 つで OK。ただし複数 doc の chunk が同一テキストの場合、**全 doc の embedding_set に True を立てる**処理が必要。

**修正方針 A（推奨）**: `content_to_chunks: dict[str, list[tuple[DocInfo, int]]]` に変更し、重複時も全て登録。適用時も全 entry を回す。

```python
# L1271 差し替え
from collections import defaultdict
content_to_chunks: dict[str, list[tuple[DocInfo, int]]] = defaultdict(list)
seen_content: set[str] = set()
lines: list[str] = []
for doc in embed_docs:
    for ci, chunk in enumerate(doc.chunks):
        chunk_text = chunk["chunk_text"]
        content_to_chunks[chunk_text].append((doc, ci))
        # JSONL には一意な content のみ送信（Embedding API のコスト削減）
        if chunk_text not in seen_content:
            seen_content.add(chunk_text)
            lines.append(json.dumps({"content": chunk_text}, ensure_ascii=False))

# L1316 差し替え
mappings = content_to_chunks.get(chunk_text, [])
for doc, ci in mappings:
    doc.embeddings[ci, :] = np.asarray(embedding, dtype=np.float32)
    doc.embedding_set[ci] = True
    embed_ok += 1
```

**検証**: 人工的に 2 doc に同じ chunk_text を含ませてテスト、両方の embedding_set が True になること。

---

### P0-3. filename fallback で今日付け + uuid4 捏造（B-5、原 46 件中）🔴

**症状**: 不正 TDnet ファイル名で `datetime.now().date()` と `str(uuid.uuid4())` を返す。日付フィルタが OUT のはずの不正データを IN と判定し BQ 挿入、かつ retry 毎に doc_id が変わり重複挿入。

**該当**: `parse_tdnet_filename`

```
L321:   if len(parts) >= 6:
            ...
            return sub_date, sec_code, parts[2], main_category, doc_title, parts[5]
L328:   sec_code = "UNKNOWN"
L329:   for part in blob_name.split("/"):
L330:       if part.isdigit() and len(part) == 4:
L331:           sec_code = part
L332:           break
L333:   return (
L334:       datetime.now(JST).date().isoformat(),   # ← 今日付け捏造
L335:       sec_code, "UNKNOWN", "その他（未分類）", "タイトル不明", str(uuid.uuid4()),  # ← doc_id 毎回変わる
L336:   )
```

**修正方針**: fallback で `ValueError` を raise。呼び出し側（`phase1_scan_and_extract` L552-557）の `except ValueError` で skipped++ してログ出力。

```python
# L333 差し替え
raise ValueError(f"parse_tdnet_filename 失敗: {blob_name}（parts={len(parts)}）")

# 呼び出し側 L549-557 修正（既に except ValueError: はあるがメッセージ強化）
try:
    sub_date, sec_code, filer_name, main_category, doc_title, doc_id = \
        parse_tdnet_filename(blob.name)
except ValueError as e:
    logger.log(f"  ファイル名パース失敗 → スキップ: {blob.name} ({e})")
    skipped += 1
    continue
```

**検証**: 不正な blob name（例: 3 parts しか無い）を含むテストディレクトリで phase1 実行、skipped カウントに入ること、BQ に不正日付行が無いこと。

---

### P0-4. `_load_processed_file_names` BQ 失敗 empty set 返却（B-6、原 46 件中）🔴

**症状**: BQ クエリ失敗で空 set 返却 → 重複チェック無しで処理続行 → 同一 blob を再度 INSERT → BQ 重複行大量生成。

**該当**: `_load_processed_file_names`

```
L189:   try:
L190:       rows = _get_bq_client().query(query).result()
L191:       return {row.FILE_NAME for row in rows}
L192:   except Exception as e:
L193:       print(f"警告: BQ 取込済みファイル一覧の取得に失敗: {e} → 重複チェックなしで続行")
L194:       return set()
```

**修正方針**: 失敗は raise で jobs を中断。冪等性は別ラインで担保（例: BQ 側に UNIQUE key や MERGE）するまでは fail-fast。

```python
# L189-194 差し替え
try:
    rows = _get_bq_client().query(query).result()
    return {row.FILE_NAME for row in rows}
except Exception as e:
    # 004 B-3: 失敗時 raise、呼び出し側で中断させる
    raise RuntimeError(
        f"BQ 取込済みファイル一覧取得に失敗、重複 INSERT 防止のため中断: {e}"
    ) from e
```

**検証**: BQ を一時的に権限はく奪でエラーにして、load モードが exit 1 で停止することを確認。

---

### P0-5. SQL injection via f-string（B-7/B-8、原 46 件中）🔴

**症状**: `doc_ids` / `ticker_from` / `ticker_to` を f-string で SQL に直埋め。通常は 4 桁数字 / 英数字 doc_id だが、将来的な異常データ混入でクエリ破壊 or injection 可能。

**該当 1**: `_load_pending_docs_from_bq`

```
L1504-1507:  where = [
                 f"SUBMISSION_DATE BETWEEN '{d_from_iso}' AND '{d_to_iso}'",
                 "AI_STATUS IN ('pending', 'pending_gemma')",
             ]
             if ticker_from:
                 where.append(f"TICKER >= '{ticker_from}'")
             if ticker_to:
                 where.append(f"TICKER <= '{ticker_to}'")
```

**該当 2**: `_update_ai_status`

```
L1651:  doc_ids_str = ",".join(f"'{d}'" for d in doc_ids)
L1653:  sql = f"""
L1654:  UPDATE `{TABLE_ID}`
L1655:  SET AI_STATUS = @new_status
L1656:  WHERE DOC_ID IN ({doc_ids_str})   # ← parametrize されていない
```

**該当 3**: `_delete_pending_gemma_rows`

```
L1842:  ids_str = ",".join(f"'{d}'" for d in doc_ids)
L1846:  sql = f"""
L1847:  DELETE FROM `{TABLE_ID}`
L1848:  WHERE DOC_ID IN ({ids_str})   # 同様
```

**修正方針**: `QueryJobConfig(query_parameters=[...])` + `ArrayQueryParameter` で parametrize。

```python
from google.cloud.bigquery import ArrayQueryParameter, ScalarQueryParameter, QueryJobConfig

# _load_pending_docs_from_bq 修正例
params = [
    ScalarQueryParameter("d_from", "DATE", d_from_iso),
    ScalarQueryParameter("d_to", "DATE", d_to_iso),
]
where = [
    "SUBMISSION_DATE BETWEEN @d_from AND @d_to",
    "AI_STATUS IN UNNEST(@statuses)",
]
params.append(ArrayQueryParameter("statuses", "STRING", ["pending", "pending_gemma"]))
if ticker_from:
    where.append("TICKER >= @ticker_from")
    params.append(ScalarQueryParameter("ticker_from", "STRING", ticker_from))
if ticker_to:
    where.append("TICKER <= @ticker_to")
    params.append(ScalarQueryParameter("ticker_to", "STRING", ticker_to))
sql = f"SELECT ... FROM `{TABLE_ID}` WHERE {' AND '.join(where)} ..."
rows = list(_get_bq_client().query(sql, job_config=QueryJobConfig(query_parameters=params)).result())

# _update_ai_status 修正例
sql = f"""
UPDATE `{TABLE_ID}`
SET AI_STATUS = @new_status
WHERE DOC_ID IN UNNEST(@doc_ids)
  AND AI_STATUS IN ('pending', 'pending_gemma')
"""
params = [
    ScalarQueryParameter("new_status", "STRING", new_status),
    ArrayQueryParameter("doc_ids", "STRING", doc_ids),
]
job = bq.query(sql, job_config=QueryJobConfig(query_parameters=params))
job.result()
affected = job.num_dml_affected_rows or 0
```

**検証**: 単体テストで `doc_ids=["x'; DROP TABLE y; --"]` のような値を渡しても SQL が壊れないこと、`num_dml_affected_rows=0` で正常終了。

---

### P0-6. B-1 exit 1 化の `errors = 1` デッドコード

**症状**: 各 except clause で `errors = 1` 代入 + 直後 `raise`。finally のサマリログでは使われるが、raise 後 finally を通る時 errors の値は用途があるので実害なし。ただし読みづらく、未来の保守で「なぜ代入？」と疑われる。

**該当**: L2133, L2179, L2266, L2374

```
L2130-2134:  except Exception as e:
                 logger.log(f"致命的なエラーで処理が中断: {e}")
                 logger.log(traceback.format_exc())
                 errors = 1                 # finally の summary 用
                 raise  # main() で exit 1 させるため再送出（B-1）
```

**修正方針**: コメントを補足して意図を明記するか、`errors = max(errors, 1)` に変更して既存の errors 値を潰さないようにする。

```python
except Exception as e:
    logger.log(f"致命的なエラーで処理が中断: {e}")
    logger.log(traceback.format_exc())
    errors = max(errors, 1)  # summary ログに例外発生を反映（既存 errors 値は保持）
    raise  # main() で exit 1 させるため再送出（B-1）
```

**検証**: Phase 3 直後に例外を人工注入、summary ログの errors が適切に報告されること。

---

## P1（次の backfill / 日次安定化に向けて）

### P1-1. `_phase3_poll_and_apply` ThreadPool 例外伝播（A-5、原 46 件中）🟨

**症状**: `fut.result()` 内で例外が発生すると `concurrent.futures.as_completed` ループから抜ける前に他 future 分の回収が止まる。残り future は ThreadPoolExecutor context 終了時に cancel されるが、既に実行中のものは続行、結果未回収。

**該当**: `_phase3_poll_and_apply`

```
L949-952:  with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(infos))) as pool:
               futures = [pool.submit(_poll_one, info) for info in infos]
               for fut in concurrent.futures.as_completed(futures):
                   info, results = fut.result()   # ← ここで例外なら loop 中断
```

**修正方針**: 各 `fut.result()` を try/except で囲み、個別エラーとしてログ+計上、全 future を回収。

```python
for fut in concurrent.futures.as_completed(futures):
    try:
        info, results = fut.result()
    except Exception as e:
        logger.log(f"  [phase3_poll] 1 batch でエラー（続行）: {e}")
        logger.log(traceback.format_exc())
        continue  # 他 chunk の結果は採用
    # ... 既存処理 ...
```

**検証**: 1 batch 分の polling を強制失敗させ、他 4 chunks の結果は正しく反映されることを確認。

---

### P1-2. `_poll_batch_job` 無限ループ・timeout なし（D-1、原 46 件中）🟨

**症状**: Gemini/Embedding batch が未知 state で膠着すると永久ポーリング。CLAUDE.md の stall 検知義務にも抵触。

**該当**: `_poll_batch_job`

```
L640-652:  def _poll_batch_job(client, job_name, logger, label) -> bool:
               while True:
                   job = client.batches.get(name=job_name)
                   state = job.state.name if hasattr(job.state, "name") else str(job.state)
                   logger.log(f"  [{label}] ジョブ状態: {state}")
                   if state in ("JOB_STATE_SUCCEEDED", ...): return True
                   if state in ("JOB_STATE_FAILED", ...): return False
                   time.sleep(BATCH_POLL_INTERVAL)
```

**修正方針**: `max_wait_sec` 引数 + deadline で break。未知 state の場合は未知 state カウンターでリトライ制限。

```python
def _poll_batch_job(
    client, job_name, logger, label, max_wait_sec: int = 21600  # 6h 上限
) -> bool:
    deadline = time.time() + max_wait_sec
    unknown_state_count = 0
    while True:
        if time.time() >= deadline:
            logger.log(f"  [{label}] timeout {max_wait_sec}s → 失敗扱い")
            return False
        job = client.batches.get(name=job_name)
        state = job.state.name if hasattr(job.state, "name") else str(job.state)
        logger.log(f"  [{label}] ジョブ状態: {state}")
        if state in ("JOB_STATE_SUCCEEDED", "SUCCEEDED", "completed"):
            return True
        if state in ("JOB_STATE_FAILED", "FAILED", "JOB_STATE_CANCELLED", "CANCELLED",
                     "failed", "cancelled"):
            return False
        # 未知 state は 10 回連続で発生したら failed 扱い
        if state not in ("JOB_STATE_RUNNING", "JOB_STATE_PENDING", "JOB_STATE_QUEUED",
                         "RUNNING", "PENDING", "QUEUED"):
            unknown_state_count += 1
            if unknown_state_count >= 10:
                logger.log(f"  [{label}] 未知 state {state} 連続 → 失敗扱い")
                return False
        else:
            unknown_state_count = 0
        time.sleep(BATCH_POLL_INTERVAL)
```

**検証**: 意図的に存在しない job_name を渡すと 60s × 10回 で timeout エラーが出ること。

---

### P1-3. `_phase3_submit` 途中失敗 orphan batch（D-4、原 46 件中）🟨

**症状**: N chunk 投入中、k 個目の `batches.create` が失敗すると、既投入 1〜k-1 個は放置（課金続く、結果 main に回収されず）。

**該当**: `_phase3_submit`

```
L883-900:  for idx, (start, end) in enumerate(chunks):
               ...
               input_uri = _upload_jsonl_to_gcs(...)
               job = client.batches.create(...)   # ← 途中失敗すると ...
               infos.append({...})
           return infos
```

**修正方針**: try/except で個別失敗を捕捉、既投入分を cancel、or 全 submit 後に `as_completed` で一括回収。簡易版は即 raise + 投入済 batch を cancel。

```python
for idx, (start, end) in enumerate(chunks):
    part = f"p{idx + 1}of{n_chunks}"
    ...
    try:
        job = client.batches.create(
            model=GEMINI_MODEL,
            src=input_uri,
            config=genai.types.CreateBatchJobConfig(dest=f"gs://{BUCKET_NAME}/{output_path}"),
        )
    except Exception as e:
        logger.log(f"  [{part}] batch job 投入失敗: {e}")
        # 既投入分を cancel
        for prev in infos:
            try:
                client.batches.cancel(name=prev["job"])
                logger.log(f"  cancelled orphan {prev['job']}")
            except Exception as ce:
                logger.log(f"  cancel 失敗: {ce}")
        raise
    infos.append({"job": job.name, ...})
```

**検証**: 3 chunk 投入のうち 2 個目で人工失敗を注入、1 個目が cancel されること。

---

### P1-4. Vision OCR 失敗時の needs_vision 残存 silent（B-9、原 46 件中）🟨

**症状**: `phase2_vision_batch` が失敗すると `needs_vision=True` の doc は text 空のまま。下流 filter `valid_docs = [d for d in docs if d.text]` で silent に消える。何件 Vision で失敗したか集計不明。

**該当**: `phase2_vision_batch`

```
L740-742:  success = _poll_batch_job(client, job.name, logger, "Vision")
           if not success:
               logger.log("  Vision バッチジョブ失敗 → Vision 対象は全件テキスト無しで続行")
               return
```

**修正方針**: 失敗時は `vision_fail_count` を return、caller で errors に加算 or log にサマリ行を明示。

```python
def phase2_vision_batch(
    docs: list[DocInfo], bucket, client, logger: BatchLogger,
) -> int:
    """Returns: Vision 失敗 doc 数"""
    vision_docs = [d for d in docs if d.needs_vision]
    if not vision_docs:
        return 0
    ...
    success = _poll_batch_job(...)
    if not success:
        logger.log(f"[WARN] Vision バッチ失敗 → {len(vision_docs)} doc が text 無し")
        return len(vision_docs)
    ...
    # 個別 doc の結果パース失敗もカウント
    vision_fail = sum(1 for d in vision_docs if len(d.text) < _MIN_TEXT_LEN)
    return vision_fail
```

**検証**: Vision Batch を人工失敗で返し、summary に `vision_fail=N` が出ること。

---

### P1-5. GCP client singleton 化（D-3、原 46 件中）🟨

**症状**: `_get_bq_client` / `_get_storage_client` / `_get_genai_client` / `_get_genai_client_embedding` が呼び出し毎に新規 Client を作成。1 実行で 100+ 回呼ばれると connection pool を使い捨て、認証遅延累積。

**該当**: L138-177

**修正方針**: `@functools.lru_cache(maxsize=None)` で singleton 化。

```python
import functools

@functools.lru_cache(maxsize=None)
def _get_bq_client() -> bigquery.Client:
    creds = _get_credentials()
    if creds:
        return bigquery.Client(project=PROJECT_ID, credentials=creds)
    return bigquery.Client(project=PROJECT_ID)

@functools.lru_cache(maxsize=None)
def _get_storage_client() -> storage.Client: ...

@functools.lru_cache(maxsize=None)
def _get_genai_client() -> genai.Client: ...

@functools.lru_cache(maxsize=None)
def _get_genai_client_embedding() -> genai.Client: ...
```

**注意**: テスト時に `lru_cache.cache_clear()` をどこかで呼べるように、test fixture から `_get_bq_client.cache_clear()` できる状態を維持。

**検証**: 同一実行内で 2 回 `_get_bq_client()` 呼び、`id()` が一致すること。

---

### P1-6. `DocInfo.embeddings` 型 union（F-1、原 46 件中）🟨

**症状**: `embeddings: "np.ndarray | list" = field(default_factory=list)`。下流で `if doc.embeddings:` 等 truthiness チェックをすると numpy array の ambiguous truth value エラー。実際に L2197 で `any(any(d.embedding_set) for d in docs if d.embedding_set)` と回避策を入れている。

**該当**: L263

**修正方針**: 常に numpy array で保持。Phase 4 以前は `np.zeros((0, 768), dtype=np.float32)` で初期化、Phase 4 でサイズ展開。

```python
@dataclass
class DocInfo:
    ...
    # 常に 2D numpy array (n_chunks, 768)、空時は (0, 768)
    embeddings: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 768), dtype=np.float32)
    )
    embedding_set: list[bool] = field(default_factory=list)
```

**修正影響**: 既存の `doc.embeddings = []` 等の代入も `np.zeros((0, 768))` に統一。truthiness チェック箇所を `doc.embeddings.size > 0` 等に修正。

**検証**: Phase 4 を Embedding chunk なしで通過しても `doc.embeddings` が `(0, 768)` の array であること、下流で例外が出ないこと。

---

### P1-7. Sub-category filter 条件（F-2、原 46 件中）🟨

**症状**: `_phase3_poll_and_apply` で Gemini sub_categories を採用するのは `_NEEDS_SUB_CATEGORIES`（決算短信・決算説明資料）のみ。`_AMBIGUOUS_SUBCATEGORY`（業績予想・大型受注・契約・業績の重要な先行指標）は Gemini 判定結果を捨てる。仕様なのか bug なのか不明。

**該当**: L967

```
L961-969:  try:
               text_resp = resp_obj["response"]["candidates"][0]["content"]["parts"][0]["text"]
               parsed = json.loads(text_resp)
               if isinstance(parsed, list):
                   parsed = parsed[0] if parsed else {}
               doc.is_monthly = parsed.get("is_monthly", False)
               if doc.main_category in _NEEDS_SUB_CATEGORIES:    # ← ここで業績予想等を除外
                   raw_subs = parsed.get("sub_categories", [])
                   doc.sub_categories = [c for c in raw_subs if c in VALID_CATEGORIES]
```

**修正方針**: まず意図確認（旧アーキ踏襲か、保守途中で欠落したか）。ドキュメント `013_tdnet_load.md` / commit 履歴 / プロンプトの出力仕様を照合。仕様なら**コメントで明記**、bug なら `_AMBIGUOUS_SUBCATEGORY` も条件に含める。

```python
# 仕様確認後（bug の場合）
eligible_sub = _NEEDS_SUB_CATEGORIES | _AMBIGUOUS_SUBCATEGORY
if doc.main_category in eligible_sub:
    raw_subs = parsed.get("sub_categories", [])
    doc.sub_categories = [c for c in raw_subs if c in VALID_CATEGORIES]
```

**検証**: 業績予想 doc に Gemini が `受注高/受注残高` と判定した場合、BQ に SUB_CATEGORIES が入ること確認。

---

### P1-8. `processed_files` daily 時のフラット全走査警告強化（C-7 残、T-8 未カバー分）🟨

**症状**: T-8 で ticker_from/ticker_to 両方指定時に per-ticker prefix 化したが、daily 実行（ticker 無し、date 1日のみ）は依然フラット全走査（`prefix=tdnet/`）で全 blob metadata を取得。ticker ~4000 × 平均 50 blob = 200K blob の list API 発行。

**該当**: `phase1_scan_and_extract` L543-557

```
L553-560:  else:
               if not (ticker_from or ticker_to):
                   logger.log(f"  [T-8][WARN] ticker 範囲無しでフラットバケット全走査中")
               blob_iter = bucket.list_blobs(prefix=f"{GCS_PREFIX}/")
```

**修正方針**: daily では date_from == date_to のことが多い。その場合は ticker 側を iterate + 各 ticker の prefix で list。ただし **ticker 一覧が必要**。JPX マスタ or 既存 BQ の DISTINCT TICKER を元に動的取得するのが妥当だが、複雑化する。

**暫定**: daily で date が 1 日なら、JPX マスタ CSV（data/master/ 等）から ticker 一覧を事前ロード、各 ticker を prefix list する。ticker 一覧取得失敗時は従来通りフラット走査 + WARN。

**優先度**: ロード時間が 10 分で収まる間は P1 据え置き、15 分超で P0 昇格。

**検証**: daily ジョブ実行で GCS list API 回数が 1 → ~4000 に分散、合計時間が短縮 or 同等であること。

---

### P1-9. silent except logger 化（E-1〜E-6、原 46 件中）🟨

**症状**: 多数の `except ...: continue` が件数カウントも log もなし。品質監視不可。

**該当**:
- `_extract_text_pypdf2` L382-384, `_extract_text_pdfminer` L413-415: PDF 抽出失敗
- `_load_disclosure_time_map` L514-515: index CSV 読込失敗（既に log あり、件数カウント無し）
- `_download_batch_results` L670-671: JSON decode 失敗
- `_phase3_poll_and_apply` L970-971: 分析結果 parse 失敗（log あり、件数集計なし）
- `phase4_chunk_and_embed` L1322-1323: Embedding 結果 parse 失敗（silent continue）
- `BatchLogger.flush_to_gcs` L460-461: GCS アップロード失敗（print のみ）
- `_apply_gemma_results` L1809: 既に log + return しているので OK
- `phase_gemini_tanshin_batch` L1833-1835: Gemini Batch 全体失敗（log あり、error count 無し）
- `_create_vector_index` L2066-2067: BQ インデックス作成失敗（log あり）
- `_cleanup_ai_state` L2004-2005: blob 削除失敗（log あり）

**修正方針**: 各関数に失敗カウンター追加、return で件数を caller に渡す。主要関数のみ修正（L1322-1323 Embedding parse 失敗はサイレント続行が多いため要対応）。

```python
# phase4_chunk_and_embed L1322 変更
embed_parse_errors = 0
for blob in output_blobs:
    ...
    with blob.open("r", encoding="utf-8") as f:
        for line in f:
            ...
            try:
                obj = json.loads(line)
                ...
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                embed_parse_errors += 1
                if embed_parse_errors <= 5:
                    logger.log(f"  [WARN] Embedding parse 失敗: {e}")
                continue
if embed_parse_errors:
    logger.log(f"  [WARN] Embedding parse 総失敗数: {embed_parse_errors}")
```

**検証**: 意図的に壊れた jsonl を含む output_prefix を渡し、logger に件数サマリが出ること。

---

## P2（余力で・次サイクル以降）

### P2-1. `_save_backfill_state` 旧 state schema（F-4、原 46 件中）🟦

**症状**: submit/resume モードの legacy state format（gzip 全 docs）が新 ai-prepare state と乱立。保守の分岐増加。

**修正方針**: resume モードを完全に ai-prepare + gemma-runner 経由に寄せて legacy を削除。ただし backfill 運用を止める必要あり、慎重に。

**暫定対応**: legacy コードパスはそのまま、新規開発では絶対使わない旨のコメント追加。

---

### P2-2. `_load_disclosure_time_map` 線形走査（C-6、原 46 件中）🟦

**症状**: 全 index CSV を bucket.list_blobs で取得し、1 ファイルずつ download。backfill 年跨ぎで数百ファイル発行。

**修正方針**: index CSV の prefix に年を含める運用（`index_2023*.csv` 等）+ 期間との集合交差で事前絞り込み。**影響範囲大、運用変更必要**。

---

### P2-3. module-level mutable global（F-7、原 46 件中）🟦

**症状**: `DATE_MODE` / `DATE_FROM` / `DATE_TO` が module global + `main()` で `global` 書き換え。テスト困難、並列実行不可。

**該当**: L68-71, L2388-2390

**修正方針**: `@dataclass` で config 化、`main()` が引数として下流に渡す。

---

### P2-4. config hardcode（G-1〜G-5、原 46 件中）🟦

**症状**: `GEMINI_MODEL` / `TABLE_ID` / `PROJECT_ID` / `BUCKET_NAME` / `GEMINI_LOCATION` が module 定数。dev/prod 切替不可、再ビルド必要。

**該当**: L74-75, L99-104

**修正方針**: `src.core.config.Settings` 経由で環境変数から注入。

---

### P2-5. Phase 5 成功時ログの誤解表示（C/D 臭い）🟦

**症状**: `"Phase 5 完了: 成功 {processed}, エラー {errors}"` ログ。B-2 で内部例外撤去後は errors=0 しかあり得ない（失敗は raise）→ 「エラー」項が常に 0 表示で partial failure を示唆しない。

**該当**: L1411 / L1495 / L1992

**修正方針**: ログ文言を「Phase 5 成功」のみに統一、エラー情報は例外経由で扱う。

```python
# L1992 差し替え
logger.log(f"Phase 5 (ai-finalize) 成功: 成功 {processed} doc / {row_count} 行, スキップ {skipped}")
```

---

### P2-6. `_log_rss` silent except（E-2）🟦

**症状**: `/proc/self/status` 読み失敗を無視。Windows では期待動作（存在しない）だが、Linux で読めない場合も silent。

**該当**: L52-60

**修正方針**: Windows (RUNTIME='local') 時は除外、Linux で読めなければ一度だけログ出力。

```python
_rss_warned = False
def _log_rss(logger, tag: str) -> None:
    global _rss_warned
    if RUNTIME == "local":
        return  # Windows 等
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    logger.log(f"[MEM] {tag}: RSS={kb / 1024:.0f} MB")
                    return
    except Exception as e:
        if not _rss_warned:
            logger.log(f"[WARN] _log_rss 失敗（以降抑制）: {e}")
            _rss_warned = True
```

---

## 検証戦略（全体）

1. **ローカル smoke test セット作成**: 5-10 doc のテスト fixtures を用意（正常・不正・missing Gemma 混在）。`--job-mode=load/ai-prepare/ai-finalize` を順に実行し全 phase 通過。
2. **Cloud Build → dev image → dev Cloud Run Job → 100 doc ai-finalize** で P0 修正の実機確認。
3. **batch B recovery と同規模** (25K doc) の再試行で P1 全体検証。
4. **scheduler RESUME** は P0 全消化 + smoke test 成功 + dev 実機 OK 後に判断。

---

## 関連ドキュメント

- `docs/knowledges/tools/013_tdnet_load.md` 末尾 T-1〜T-10 リスト + RESUME チェックリスト
- `docs/knowledges/tools/004_coding_conventions.md` §バッチジョブ・ETL アンチパターン集 A-1〜E-2
- `docs/knowledges/tools/078_gemma4_operation.md` §7 Gemma ↔ Gemini 結合部アンチパターン G-1〜G-3
- commit 2f41744 `fix: tdnet_load_parallel 13件の設計欠陥一括修正`
- 2026-04-20〜04-21 batch B recovery 実績（ticker 6367-9997、26,941 doc completed）

---

## 担当メモ（code-reviewer 向け）

- P0-1〜P0-6 は**batch B recovery 終了後の実機調査で確定した必須**。これを直すまで scheduler RESUME しない
- P0-2（T-6）は 2026-04-20 時点で私が P0 宣言しながら commit に含めなかった。謝罪込みで最優先
- P1 は次の backfill phase（2022/2021/2020）を始める前に消化すべき
- P2 は余力で、scheduler RESUME には不要
- 修正時は **004 A-1〜E-2 + T-1〜T-10 + G-1〜G-3 チェックリスト**を必ず通過させる
- スモークテスト未実施の fix は merge しない（commit 2f41744 の教訓）

---

## レビュー追記: 2026-04-21 08:30 JST — code-reviewer

- 対象コード: `scripts/tdnet_load_parallel.py` (2,492 行、commit 2f41744 時点)
- パターン: 2 (改修プランレビュー)
- レビュアー: Claude (code-reviewer runbook / 推論特化)

### 【サマリー】

- 変更の要約: 2026-04-20 commit 2f41744 で 13 件修正済みの後、残 33 件 + 新規 regression 2 件を P0/P1/P2 に分けて実装する。P0 6 件は scheduler RESUME 前の必須修正
- 品質評価: **A** — プランは実コードを正確に診断しており、004 アンチパターン集との突き合わせも妥当。ただし P0-3 の修正方針に**致命的な認識誤り 1 件**、および**プラン未指摘の重要な抜け漏れ 2 件**（ai-finalize の data loss 経路 / `phase_gemini_tanshin_batch` の orphan batch）あり
- 主要リスク:
  - **R1**: P0-3 を記述通りに実装すると phase1_scan_and_extract がクラッシュする（plan は既存 except があると誤認）
  - **R2**: ai-finalize で「text 無し doc」が `_delete_pending_gemma_rows` で pending_* 行を失い、completed 行も挿入されず**恒久的に行方不明**になる経路が残存（プラン未指摘）
  - **R3**: `phase_gemini_tanshin_batch` は P1-3 (orphan cancel) と同じ問題を抱えるが、上位 try/except で例外握り潰しもあるため二重に検知不能（プラン未指摘）

### 【改修プラン評価】

#### 妥当性

- **診断精度**: 高い。プラン記載の**行番号と症状は概ね実コードと一致**（ただし行番号に最大 +27 行のドリフトあり。例: P1-1 は plan L949-952 だが実コードでは L974-977）。症状→根本原因→修正方針の因果が正しく、対症療法ではなくアンチパターンと結び付けた構造的修正になっている
- **優先度付け**: 妥当。P0 6 件は「データ破壊・セキュリティ・gate 無効化」で scheduler RESUME ブロッカーとして正しく隔離。P1 は次 backfill 前、P2 は余力、という階層も合理的
- **依拠ルールの明示**: 004 A-1〜E-2、T-1〜T-10、G-1〜G-3 との対応関係を逐一明示。Reviewer が独自判断せずルールベースで追試できる設計になっている
- **一点だけ注意**: P0-6 は「読みづらさ」だけで P0 優先度は過剰。P2 降格が妥当（影響: 無、修正: 文言化のみ）

#### 副作用・デグレードチェック

- **DC-1** (P0-3): `parse_tdnet_filename` を raise 化すると、**既に動いている Phase 1 の正常経路がクラッシュ**する。プラン L151-159 の「既に except ValueError: はある」は誤認（実際は `date.fromisoformat` にしか except が無い）。呼び出し側の新規 try/except 追加が必須
- **DC-2** (P0-4): BQ 取込済みファイル一覧の fail-fast 化で、**日次ジョブが BQ 権限一時切れ・quota・ネットワーク断で頻繁停止**するリスク。commit 2f41744 以前は silent 続行で事故を起こしたが、逆振りで新しい運用事故を招かないよう tenacity + 最終 fail-fast の 2 段構えが安全
- **DC-3** (P0-5): `_delete_pending_gemma_rows` の SQL parametrize 化で、**partition prune が効かなくなる**可能性。`ScalarQueryParameter("d_from", "DATE", ...)` を string ではなく DATE 型で渡す必要あり。確認を検証項目に追加
- **DC-4** (P1-5 lru_cache): `_get_credentials()` の `global _creds` と lru_cache の組合せで**テスト時に両方リセット必須**。test fixture から `_get_bq_client.cache_clear()` + `_creds=None` の両方を叩かないと singleton が古いまま持ち越し
- **DC-5** (P1-6 embeddings 型統一): `np.zeros((0, 768))` 統一で、**ダウンストリームの truthiness チェックと代入が 4 箇所全て影響**（L1299, L1379, L1906, L2255）。プラン「修正影響」で概念は触れているが具体行の enumeration が未完
- **DC-6** (P2-5 Phase 5 errors 削除): 返り値シグネチャを `(processed, skipped, errors)` → `(processed, skipped)` に変更すると、**呼び出し側 3 箇所すべて tuple unpack 修正**（L1411, L1495, L2246）。旧・新モード混在中に片方を忘れると silent tuple unpack エラー
- **DC-7** (全体): commit 2f41744 で「静めるために入れた」緩和策を剥がしていないか注意。特に P0-1 で `if gemma_missing > 0: errors += gemma_missing` は 2f41744 で入れたばかり。これを別変数名に分離するのは問題ないが、**元の意図（gate への反映）を壊さない**こと

#### 抜け漏れ（類似観点での横展開含む）

- [ ] **M-1**: P0-3 の呼び出し側修正は**新規 try/except 追加**が必須（plan 誤認）
  - プラン L151-159: 「既に `except ValueError:` はあるがメッセージ強化」— 誤り
  - 実コード L567-575: `parse_tdnet_filename(blob.name)` の呼び出しは try/except で囲まれていない。try/except は直後の `date.fromisoformat(sub_date)` に対してのみ設けられている
  - `parse_tdnet_filename` を raise 化すると phase1 の for-loop 先頭で例外が出て loop 脱出、**Phase 1 全件ロスト**で job 中断
  - 修正時は新規 try/except を追加、かつ `phase1_extract_for_docs` L1577 の広義 `except Exception` が raise 化後の ValueError を握り潰して `main_category="その他（未分類）"` にフォールバックする副作用も検討要（ai-prepare では doc_id は BQ 由来なので許容可、ただしログ付与必須）

- [ ] **M-2**: ai-finalize での **text 無し doc のデータロスト経路**が P0 未対応
  - L2234 `doc_ids = [d.doc_id for d in docs]` は text の有無に関わらず全 doc を含む
  - L2246 `phase5_bq_insert_finalize` は L1909 `valid_docs = [d for d in docs if d.text]` で text 有りのみ insert（text 無しは completed 行を作らない）
  - L2251 `_delete_pending_gemma_rows(doc_ids, ...)` は全 doc_id の pending_* 行を DELETE
  - 結果: **text 取得失敗 doc は pending_* 行が消えた上に completed 行もない → BQ から消滅、再実行しても `AI_STATUS IN ('pending', 'pending_gemma')` で拾えない**
  - 004 B-4「多段 write は自己修復できる順序に」違反、silent data loss。scheduler RESUME 前に扱うべき P0
  - 修正方針: `_delete_pending_gemma_rows` に渡す doc_ids は `[d.doc_id for d in docs if d.text]` に限定、または insert 成功 doc のみ対象にする。さらに text 無し doc は errors に計上 + サマリに `text_missing=N` として表示

- [ ] **M-3**: `phase_gemini_tanshin_batch` も P1-3 orphan cancel の対象（**類似観点の横展開**）
  - L1828-1835: 内部で `phase3_analysis_batch`（= `_phase3_submit` + poll）を呼び、失敗時は `try/except Exception` で握り潰して続行
  - submit 中に k 件目で失敗した場合、既投入 k-1 件の Gemini batch は cancel されず課金続行、かつ ai-finalize 本体は成功扱いで continue → 失敗が log にしか現れない
  - P1-3 の orphan cancel ロジックを `_phase3_submit` に入れれば自動的にこちらも救済されるので、**修正ポイントは `_phase3_submit` 1 箇所で十分**。ただし plan に「tanshin_batch 経由も同時に救済される」旨の明記が欲しい

- [ ] **M-4**: `_update_ai_status` の `total_rows` 誤報告（P0-5 と P1 D-4 の統合要）
  - L1673 `affected = result.total_rows if hasattr(result, "total_rows") else 0`
  - BQ の DML に対する `RowIterator.total_rows` は実行影響行数ではない（0 か None）。L1675-1680 の `_job_ref._job_id → num_dml_affected_rows` で上書きを試みるが、private API 依存（P1 D-4 の指摘）+ 失敗時 silent except → ログで「0 行 UPDATE」と誤表示
  - P0-5 で parametrize 修正する際に**同時に `num_dml_affected_rows` ルート 1 本化**すべき。P0-5 と P1 D-4 が同じ関数に触るので、修正担当者がどちらで触るか明示する必要あり

- [ ] **M-5**: P2-5（Phase 5 errors=0 固定の誤解ログ）は **P0 昇格**が妥当
  - L1987 `errors = 0` ハードコード → L1992 ログは常に「エラー 0」
  - 呼び出し側 L2246 `processed, skipped, errors = phase5_bq_insert_finalize(...)` でこの errors=0 が B-3 cleanup gate に伝播（P0-1 の根本原因の 1 つ）
  - P0-1 を `phase5_errors + gemma_missing_total` で合算する修正で救えるが、**Phase 5 自身が errors を返さないインターフェースにすべき**（成功時は単に return、失敗時は raise — 004 A-2「副作用関数は失敗時 raise、正常系返り値に errors を混ぜない」）
  - 併せて log 文言も「成功 N / スキップ M」のみに

- [ ] **M-6**: `_download_batch_results` が 004 C-2 違反（**類似観点の横展開**）
  - L680 `content = blob.download_as_string().decode("utf-8")` で Embedding 出力 JSONL を 1 blob 全量メモリロード。Embedding 並列 5 チャンク × 数万 row で数百 MB / blob。004 C-2 の 100MB 目安超過
  - `blob.open("r", encoding="utf-8")` でストリーム読みに変更すべき（P1 に新項目として追加）

- [ ] **M-7**: P1-9 silent except リストで **L779-780 Vision OCR 個別 doc パース失敗**が抜けている
  - `phase2_vision_batch` L779-780 は `except (KeyError, IndexError)` で log のみ、件数カウントなし
  - P1-4 の vision_fail_count と統合すべき

- [ ] **M-8**: ロールバック手順・検証パイプライン不明
  - プラン末尾「検証戦略」は smoke test・dev 実機・batch B 規模再試行を列挙するが、**各 P0 修正の単体 rollback 手順**が無い
  - P0-4 (BQ failure fail-fast) は過度に fail-fast 化して日次ジョブが不安定化する可能性。flag で旧動作に戻せるスイッチがあると安全
  - M-2 のような silent data loss 修正は過去データへの遡及影響チェック（既に消えた doc の recovery）も併せて計画すべき

#### 新規リスク

- **NR-1** (P0-3 起因): 上記 M-1 を見落とすと phase1 全件ロスト。実装レビューで必ず try/except 追加を確認
- **NR-2** (P0-4 起因): BQ 取込済みファイル一覧取得が失敗すると job exit 1。BQ 権限一時切れ・quota・ネットワーク断で日次ジョブが頻繁に失敗するリスク。retry (tenacity) + 最終的な fail-fast、という 2 段構えが安全
- **NR-3** (P0-5 起因): `_update_ai_status` / `_delete_pending_gemma_rows` の SQL を parametrize 化する際、`doc_ids` リストが長大（数万件）になると `UNNEST(@doc_ids)` でも BQ 側パラメータサイズ上限に触れる可能性。`BQ_BATCH_SIZE=100` で分割送信しているか、P0-5 修正時に確認必要
- **NR-4** (P1-5 lru_cache singleton 起因): `_get_credentials()` が `global _creds` を使う module-level mutable global（P2-3）と lru_cache の組合せは、テスト時に cache_clear + `_creds=None` の両方リセットが要る。プランに明示を
- **NR-5** (P1-6 embeddings 型統一起因): L1906 `doc.embeddings = []` を `np.zeros((0, 768))` に統一すると、ダウンストリームの L1379 `doc.embeddings[ci].tolist()` や L1951-1953 の `doc.embedding_set[ci]` チェックとの整合が必要。プラン「修正影響」欄で触れているが、具体行リストは未提示

### 【重大な指摘】（即修正、優先順）

#### #1 P0-3 修正方針の致命的誤り — **プラン最優先で訂正**
- 箇所: プラン L151-159 / 実コード `scripts/tdnet_load_parallel.py:567-575`
- 事象: 「呼び出し側 L549-557 は既に `except ValueError` がある」は誤認。実際は `parse_tdnet_filename` 呼び出しは裸で、try/except は `date.fromisoformat` の方にしかない
- トリガー: plan 通りに `raise ValueError` を入れ、呼び出し側を修正せず merge
- 影響: phase1 の for-loop で最初の不正 filename blob に到達した瞬間 job クラッシュ、Phase 1 全件ロスト
- 根拠: 実コード該当箇所（L567-568 に try:/except: 無し、L570-575 の try は `date.fromisoformat` 用）
- 推奨対応: プランの呼び出し側修正例を「既存 except 強化」ではなく「新規 try/except を parse_tdnet_filename 呼び出しに追加」に書き換える。加えて `phase1_extract_for_docs` L1577 の広義 except の ValueError 握り潰しも明記

#### #2 ai-finalize で text 無し doc が恒久的に消滅（プラン未指摘）
- 箇所: `scripts/tdnet_load_parallel.py:2234, 2246, 2251`
- 事象: text 取得失敗 doc は completed 行を得ず、かつ pending_* 行も DELETE される → BQ から消滅
- トリガー: ai-finalize で state.json に含まれる doc のうち phase1_extract_for_docs で text 取得に失敗したもの（例: GCS download 失敗、PDF 破損）が 1 件でもある場合、毎回発生
- 影響: 将来 ai-finalize を再実行しても pending 状態の行が無いので再処理不可。AI 分類・Embedding 欠落データが BQ から silent に消える。004 B-4 違反
- 根拠:
  - L2234: `doc_ids = [d.doc_id for d in docs]` — text の有無で絞っていない
  - L1909: `valid_docs = [d for d in docs if d.text]` — text 無しは insert されない
  - L2251: `_delete_pending_gemma_rows(doc_ids, ...)` — L1882 の SQL `WHERE DOC_ID IN ({ids_str})` で text 無し doc の pending_* 行まで削除
- 推奨対応: `doc_ids` を insert 対象のみに絞る（`inserted_ids = [d.doc_id for d in docs if d.text]`）。併せて text 無し件数を errors にカウント + finally サマリに `text_missing={N}` 明示。scheduler RESUME 前の P0 として追加

#### #3 `phase_gemini_tanshin_batch` で orphan Gemini batch が無視される（プラン未指摘）
- 箇所: `scripts/tdnet_load_parallel.py:1828-1835`
- 事象: `phase3_analysis_batch` が内部で `_phase3_submit` を呼ぶが、submit 中の部分失敗時に投入済 batch を cancel せず、かつ外側 L1833 `except Exception` で例外握り潰し
- トリガー: 決算短信の件数が多い日、Gemini batches.create が chunk 2/5 で失敗
- 影響: 既投入の chunk 1 は走り続けて課金、結果は main 側で回収されない。ai-finalize は成功扱いで continue、Gemma 結果のみで BQ 投入される（sub_categories の受注判定が欠落）
- 根拠: L1828 `try:` → L1833 `except: logger.log(...warning...)` で失敗は WARNING ログ 1 行のみ。L1829 `phase3_analysis_batch` が P1-3 の修正対象 `_phase3_submit` を内包
- 推奨対応: P1-3 の orphan cancel を `_phase3_submit` 内部に置けば自動的に救済される。プラン P1-3 に「`phase_gemini_tanshin_batch` 経由の submit も同じ修正で救済される」旨を明記。加えて `phase_gemini_tanshin_batch` の外側 except を握り潰しから `errors` カウント + log に変更

#### #4 `_update_ai_status` の `total_rows` → 誤報告（プランで P0-5 + P1 D-4 がバラバラに触れる）
- 箇所: `scripts/tdnet_load_parallel.py:1673-1680`
- 事象: BQ DML に対して `RowIterator.total_rows` を読んで「UPDATE N 行」とログ。これは通常 0 か None を返すため誤情報
- トリガー: 常時
- 影響: ログ信頼性低下。監視で「AI_STATUS UPDATE 0 行」→ 実際は成功、を誤検知
- 根拠: BQ `query().result()` の返却型は UPDATE/DELETE の場合 `_EmptyRowIterator`。`total_rows` は SELECT 用メタデータ
- 推奨対応: `job = bq.query(sql, job_config=...); job.result(); affected = job.num_dml_affected_rows or 0`。P0-5 parametrize 修正時に同時対応。plan の P0-5 修正例と P1 D-4 が同じ関数を触るので担当と順序を明記

#### #5 Phase 5 の `errors=0` 固定と B-3 gate 汚染（プラン P0-1 + P2-5 分離を統合）
- 箇所: `scripts/tdnet_load_parallel.py:1405-1406, 1489-1490, 1986-1987, 2246, 2259`
- 事象: Phase 5 は内部例外を raise するだけなので errors は常に 0。呼び出し側の tuple unpack で累積 errors (gemma_missing 含む) が上書きされ、B-3 cleanup gate `if errors == 0` が常時真化
- トリガー: Gemma missing が存在する全ての ai-finalize 実行
- 影響: Gemma 部分失敗時に state を cleanup してしまい再実行不能（プラン P0-1 が既に認識済み）。加えて文言として「エラー 0」がサマリに常時出るため監視の異常検知穴に（004 A-7）
- 根拠: L1987 `errors = 0`、L1411 / L1495 / L1992 の log 文言
- 推奨対応:
  - Phase 5 インターフェースを `(processed, skipped)` に変更（失敗は raise）、返り値から errors を除去（004 A-2）
  - ai-finalize 側は `phase5_errors = 0` で明示、`total_errors = gemma_missing_total + phase5_errors + text_missing_count` を cleanup gate と summary に使う
  - plan P0-1 と P2-5 を統合し、P2-5 を P0 に昇格

#### #6 P0-5 SQL injection — `_delete_pending_gemma_rows` が partition prune と parametrize の両立要
- 箇所: `scripts/tdnet_load_parallel.py:1872-1887`
- 事象: f-string 埋込 (`ids_str`, `d_from_iso`, `d_to_iso`) の parametrize 修正で partition prune が効かなくなる可能性
- トリガー: P0-5 実装時、`BETWEEN @d_from AND @d_to` を `ScalarQueryParameter("d_from", "DATE", d_from_iso)` に置換
- 影響: BQ optimizer が partition prune を効かせられず、全パーティションスキャンで BQ 料金急増
- 根拠: BQ で partition prune を効かせるには literal date か明確な型指定が必要。`ScalarQueryParameter("d_from", "DATE", "2026-04-01")` は効く（string ではなく DATE 型で渡す）ことを確認済だが、plan の修正例 L243 が `DATE` 型指定のみで partition prune 確認を明示していない
- 推奨対応: parametrize 修正後に dry-run で `job.total_bytes_processed` を比較、partition prune 有効を確認。plan の検証項目に追加

#### #7 P1-2 `_poll_batch_job` で失敗時のエラー詳細が欠落
- 箇所: `scripts/tdnet_load_parallel.py:665-668`
- 事象: 失敗 state 検出時 `logger.log(f"[{label}] ジョブ失敗: {state}")` のみ、`job.error`（Vertex AI Batch の失敗詳細）を読まない
- トリガー: バッチジョブ失敗時、常時
- 影響: デバッグ情報不足。監視側が「何が失敗したか」判別不能
- 根拠: `genai.Client.batches.get()` の返り値には `error` 属性（GoogleRpcStatus）があるが、コードは `state.name` しか読まない
- 推奨対応: P1-2 timeout 追加と同時に、失敗時 `logger.log(f"{label} error_detail: {getattr(job, 'error', None)}")` を追加

### 【改善提案】（可読性・保守性）

#### #1 プランの行番号を最新コードに合わせる
- 箇所: プラン全体
- 現状: plan L949-952 は実コード L974-977、plan L883-900 は実コード L880-923 など、±20 行のドリフトがある
- 提案: plan 実装前に `git show 2f41744 -- scripts/tdnet_load_parallel.py` で行番号を確定、plan 側を更新

#### #2 P0-6 は P2 降格が妥当
- 箇所: プラン P0-6
- 現状: `errors = 1` 代入のデッドコード読みづらさ。修正で挙動は変わらない
- 提案: P0 から P2 降格。scheduler RESUME のブロッカーにしない

#### #3 T-6 chunk_text 衝突修正で「seen_content 集合」の意義を明記
- 箇所: プラン P0-2 修正例 L107-110
- 現状: `seen_content` で JSONL 重複送信を防ぐが、意図が「API コスト削減 + 受信 map 単純化」のみで、**受信側 parse の O(1) lookup を効かせる主目的** が伝わりにくい
- 提案: コメントで「Embedding API は同一 content を de-dup しないため、送信時点で uniq 化しないと課金が増える」旨を明記

#### #4 P1-6 embeddings 型統一でダウンストリームの影響範囲を enumerate
- 箇所: プラン P1-6
- 現状: 「修正影響」に「`doc.embeddings = []` 等の代入も `np.zeros((0, 768))` に統一」とあるが具体箇所は未列挙
- 提案: L1906 `doc.embeddings = []`、L1299 `doc.embeddings = np.zeros(...)` 、L2255 `any(any(d.embedding_set)...)`、L1379 `doc.embeddings[ci].tolist()` の一覧を plan に追記

#### #5 004 アンチパターン集と plan の対応表を plan 末尾に整理
- 箇所: プランの「関連ドキュメント」
- 現状: 「004 A-1〜E-2」と言及されるが、**P0-1〜P2-6 → 004 のどのコード**という対応マップが無い
- 提案: 次の表を追加
  | plan ID | 004 アンチパターン | T-x | G-x |
  |---|---|---|---|
  | P0-1 | B-1, B-3, A-3 | T-7 (cleanup gate) | G-2 |
  | P0-2 | (B-8 新規) | T-6 | — |
  | P0-3 | A-5 | — | — |
  | P0-4 | B-3 | — | — |
  | P0-5 | C-1 | — | — |
  | P0-6 | A-1 | — | — |
  | P1-1 | A-4 | — | — |
  | P1-2 | D-1 | — | — |
  | P1-3 | D-3 | — | — |
  | P1-4 | A-3 | — | — |
  | P1-5 | D-2 | — | — |
  | P1-6 | F-1 | — | — |
  | P1-7 | F-2 | — | — |
  | P1-8 | C-3 | T-8 | — |
  | P1-9 | A-3 | — | — |

#### #6 検証戦略に dev 環境の cost ガードを追加
- 箇所: プラン「検証戦略」
- 現状: 「dev Cloud Run Job → 100 doc ai-finalize」で実機確認とあるが、Gemini batch cost の上限設定や意図せぬ全件実行の防止策がない
- 提案: dev 専用の `BQ_BATCH_SIZE=10` / `DOCS_LIMIT=100` / `EMBED_SKIP=1` のようなスイッチを script に追加（または plan で言及）、修正確認用の最小コスト run を再現可能にする

### 【修正例】（必要な箇所のみ）

##### #1 (P0-3 呼び出し側): `phase1_scan_and_extract` で parse_tdnet_filename を try/except で囲む

```python
# scripts/tdnet_load_parallel.py:567 付近 修正前
for blob in blob_iter:
    if not blob.name.lower().endswith(".pdf"):
        continue

    sub_date, sec_code, filer_name, main_category, doc_title, doc_id = \
        parse_tdnet_filename(blob.name)

    try:
        d_blob = date.fromisoformat(sub_date)
    except ValueError:
        logger.log(f"  日付パース失敗 → スキップ: {blob.name}")
        skipped += 1
        continue

# 修正後
for blob in blob_iter:
    if not blob.name.lower().endswith(".pdf"):
        continue

    try:
        sub_date, sec_code, filer_name, main_category, doc_title, doc_id = \
            parse_tdnet_filename(blob.name)
    except ValueError as e:
        logger.log(f"  ファイル名パース失敗 → スキップ: {blob.name} ({e})")
        skipped += 1
        continue

    try:
        d_blob = date.fromisoformat(sub_date)
    except ValueError:
        # parse_tdnet_filename が成功した後の二重安全網
        logger.log(f"  日付パース失敗 → スキップ: {blob.name}")
        skipped += 1
        continue
```

##### #2 (ai-finalize data loss 修正): `doc_ids` を insert 対象のみに絞る

```python
# scripts/tdnet_load_parallel.py:2234 付近 修正前
doc_ids = [d.doc_id for d in docs]
...
processed, skipped, errors = phase5_bq_insert_finalize(docs, bucket, logger)
...
_delete_pending_gemma_rows(doc_ids, min_d, max_d, logger)

# 修正後
processed, skipped, phase5_errors = phase5_bq_insert_finalize(docs, bucket, logger)

# insert 済 doc_id のみ DELETE 対象（text 無し doc の pending_* 行は保持して再実行可能にする）
inserted_doc_ids = [d.doc_id for d in docs if d.text]
text_missing = len(docs) - len(inserted_doc_ids)
if text_missing > 0:
    logger.log(f"[DATA] text 取得失敗 doc: {text_missing} 件 → pending_* 行を保持（再実行で拾える）")

_delete_pending_gemma_rows(inserted_doc_ids, min_d, max_d, logger)

total_errors = phase5_errors + gemma_missing_total + text_missing
if processed > 0 and total_errors == 0:
    _cleanup_ai_state(bucket, run_id, logger)
else:
    logger.log(
        f"GCS state cleanup をスキップ "
        f"(processed={processed}, phase5_errors={phase5_errors}, "
        f"gemma_missing={gemma_missing_total}, text_missing={text_missing})"
    )
```

##### #4 (_update_ai_status): `num_dml_affected_rows` 1 本化 + parametrize

```python
# scripts/tdnet_load_parallel.py:1655 修正後
def _update_ai_status(
    doc_ids: list[str], new_status: str, logger: BatchLogger,
) -> int:
    if not doc_ids:
        return 0
    bq = _get_bq_client()
    sql = f"""
    UPDATE `{TABLE_ID}`
    SET AI_STATUS = @new_status
    WHERE DOC_ID IN UNNEST(@doc_ids)
      AND AI_STATUS IN ('pending', 'pending_gemma')
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("new_status", "STRING", new_status),
        bigquery.ArrayQueryParameter("doc_ids", "STRING", doc_ids),
    ])
    job = bq.query(sql, job_config=job_config)
    job.result()
    affected = job.num_dml_affected_rows or 0
    logger.log(f"BQ AI_STATUS UPDATE 完了: {new_status} に {affected} 行（対象 {len(doc_ids)} doc）")
    return affected
```

### 【確認できなかった事項】

- `phase3_analysis_batch` 本体のコード（L1829 で呼ばれているが定義箇所を今回読んでいない）— 内部で `_phase3_submit` + `_phase3_poll_and_apply` をどう組み合わせるか、orphan cancel が適用されるかは現コードで未確認。plan 実装時に併せて確認必要
- `_save_backfill_state` / `_load_backfill_state`（resume モード用、legacy）の実装 — P2-1 の legacy 削除判断に影響。今回未読
- `truncate_for_model` (src.llm.truncation) の挙動 — 決算短信カテゴリで truncate がどう効くか、Vertex AI Batch の 1 row サイズ上限 (概ね 1MB) との関係
- `VALID_CATEGORIES` / `_NEEDS_SUB_CATEGORIES` / `_AMBIGUOUS_OVERWRITE` / `_MONTHLY_SUB_CATEGORIES` / `_NEEDS_GEMINI_ANALYSIS` / `_EMBED_CATEGORIES` の定義内容 — P1-7 の仕様確認で参照必須だが今回未読。`_NEEDS_SUB_CATEGORIES` と `_AMBIGUOUS_SUBCATEGORY` の意図的な分離か bug かは定義を読まないと断定不可
- BQ テーブル `TDNET_DOCUMENTS_ENHANCED` のスキーマ（AI_STATUS カラムの NULLABLE / REQUIRED / enum 値、SUBMISSION_DATE が partition key か、DOC_ID + CHUNK_TEXT の PK 相当制約）— `data_catalog.md` で再確認すべき
- 2026-04-20 batch B recovery の実測ログ — gemma_missing が発生した前例があるか、P0-1 regression の「顕在化条件」確認で有用
- Cloud Workflows 側の YAML（ai-prepare 成功時の gate 条件、`_SUCCESS` マーカー検証が入っているか）— plan A-8 の実装が上流で整っているかに影響
- Vertex AI Batch のレート制限・quota 運用状況 — P1-3 の orphan cancel が課金規模でどれだけ意味があるかの判断材料

### レビュー完了後の推奨アクション

1. **プラン修正**: M-1 (P0-3 try/except 追加), M-2 (P0-7 新規: text 欠損 data loss), M-3 (P1-3 に tanshin_batch 明記), M-4 (P0-5 と P1 D-4 の統合), M-5 (P2-5 を P0 昇格) を反映
2. **行番号再採番**: 現コード基準で plan 全項目の L番号を更新
3. **対応マップ追加**: plan ↔ 004 アンチパターンの突合表（改善提案 #5）
4. **検証戦略強化**: dev 環境 cost ガード・rollback 手順・partition prune 確認を追記
5. **修正優先順の再確認**: P0-6 → P2 降格、P2-5 → P0 昇格

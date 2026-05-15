# CR-172: edinet_load_parallel.py バックフィルモード コードレビュー

- 日時: 2026-05-14 JST
- 対象: `scripts/edinet_load_parallel.py`
- パターン: 1 (新規レビュー)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: EDINET ETL スクリプトに backfill モード（Embedding スキップ）、Load Job 化、numpy float32 化、NDJSON stream write、冪等性 DELETE を追加。512Mi/1CPU Cloud Run Job での実行を想定。
- 品質評価: **C** — ticker 範囲スキャンが完全に壊れている致命的バグ、Phase 1 で日付抽出不能 blob が日付フィルタをすり抜ける論理欠陥、メモリ解放漏れによる OOM リスクが複合的に存在する
- 主要リスク:
  1. `ticker_from` 指定時の GCS prefix 絞り込みが `ticker_from` 1銘柄のみスキャンし、`ticker_from+1` 以降が全て欠落する
  2. blob 名に日付が含まれないファイルが日付フィルタをバイパスし、全期間の blob が処理対象に混入する
  3. Phase 1 完了後に `doc.text`（HTML 全文）がメモリに残り続け、大量ファイル処理時に 512Mi を超過する

---

## 【重大な指摘】（即修正）

### #1 ticker 範囲スキャンが最初の 1 銘柄しかスキャンしない

- 箇所: `scripts/edinet_load_parallel.py:556-561`
- 事象: `ticker_from` が指定されると `scan_prefix = f"{GCS_PREFIX}/{ticker_from}/"` で GCS `list_blobs` を呼ぶ。GCS の `list_blobs(prefix=...)` は完全前方一致のため、`prefix="edinet/1301/"` は `edinet/1301/` 配下の blob **のみ** を返す。`edinet/1302/`, `edinet/1303/`, ... の blob は一切列挙されない。
- トリガー: `--ticker-from 1301 --ticker-to 3727` のように範囲指定した場合。提出 MD のテストロットでは ticker 範囲未使用のため発覚しないが、本番バックフィルで使った瞬間にデータ欠落が起きる。
- 影響: **データ欠損**。ticker 範囲バックフィルで `ticker_from` の 1 銘柄のみ処理され、残りの全銘柄が無言でスキップされる。ログにもエラーは出ない。
- 根拠: GCS `list_blobs(prefix=X)` は prefix X で始まる blob を全て返すが、X を含まない blob は返さない。`edinet/1301/` は `edinet/1302/` を含まない。後段の ticker フィルタ（行 576-579）は list_blobs の結果に対して適用されるため、そもそも列挙されない blob はフィルタ対象にすらならない。
- 推奨対応: **[検証済み]** `ticker_from` がある場合も `scan_prefix = f"{GCS_PREFIX}/"` で全件スキャンし、行 576-579 の ticker フィルタのみで範囲制御する。スキャン時間が増えるが、正確性が優先。あるいは GCS の `list_blobs` に `start_offset` / `end_offset` パラメータ（lexicographic range）を使って `start_offset=f"{GCS_PREFIX}/{ticker_from}/"`, `end_offset=f"{GCS_PREFIX}/{ticker_to}0/"` とすれば効率化できる（GCS Python client v2.0+ で利用可能）。

**[採用]** start_offset/end_offset 方式で修正済み。

### #2 blob 名に日付がない場合、日付フィルタがバイパスされる

- 箇所: `scripts/edinet_load_parallel.py:582-595`
- 事象: `_extract_date_from_blob_name` が `None` を返した場合（ファイル名に `_YYYYMMDD_` パターンがない）、`if blob_date_str:` が False となり日付フィルタブロック全体がスキップされる。結果、日付に無関係な blob が全て処理対象に含まれる。
- トリガー: `MERGED_REPORT.html` のようにファイル名にアンダースコア区切りの 8 桁日付を含まないファイル。提出 MD にある通り GCS パスは `edinet/{ticker}/MERGED_REPORT.html` 形式であり、日付が含まれていないケースが想定される。
- 影響: `--from 20260101 --to 20260131` で 2026年1月のみ処理したいのに、全期間の blob が混入する。backfill モードでは意図しない大量データの処理が走り、OOM の直接原因になり得る。
- 根拠: `_extract_date_from_blob_name`（行 333-339）はファイル名の最終パスコンポーネントを `_` 分割し、8 桁数字パーツを探す。`MERGED_REPORT.html` にはマッチしない。
- 推奨対応: **[方向性]** `blob_date_str` が None の場合の挙動を決定する必要がある。選択肢: (a) HTML 内の提出日（`_extract_metadata_and_text` の `submission_date`）で日付フィルタする（ただし HTML ダウンロード後なので性能低下）、(b) 日付不明の blob はスキップする、(c) blob の GCS metadata (creation time) で判定する。backfill の正確性を優先するなら (a) が最善。

**[採用]** (b) スキップ方式で修正済み。全期間混入防止を優先。

### #3 Phase 1 後に doc.text がメモリに残り続ける（OOM の主因）

- 箇所: `scripts/edinet_load_parallel.py:627-637` (DocInfo 生成), `647-664` (_phase2_chunk)
- 事象: Phase 1 で各 DocInfo の `text` フィールドに HTML 全文テキストが格納される。`_phase2_chunk` でチャンク化した後も `doc.text` は解放されない。Phase 3 の `valid_docs` フィルタ（行 878）も `d.text` が truthy かどうかで判定しているため、`doc.text = ""` にすると Phase 3 で `valid_docs` から外れてしまう構造上の問題がある。
- トリガー: 数百件以上の HTML を処理するバックフィル。有価証券報告書 1 件あたり数十～数百 KB のテキスト。500 件 * 200KB = 100MB がテキストだけで消費。チャンク（重複あり）を加えると 150MB 超。GCS blob 列挙、BQ クライアント、ロガー等のオーバーヘッドを含めると 512Mi に到達し得る。
- 影響: Cloud Run Job (512Mi) で OOM kill。前回の失敗の直接原因がコード未デプロイだったとしても、大量ファイル時に再発する構造上の問題。
- 根拠: `DocInfo.text` は dataclass フィールドであり、明示的に解放しない限りオブジェクトが生存する間メモリを占有する。`_phase2_chunk` 後にはテキストは不要（チャンクが生成済み）。
- 推奨対応: **[検証済み]** `_phase2_chunk` 完了後に `doc.text = ""` として解放する。Phase 3 の `valid_docs` フィルタを `d.text or d.chunks` に変更するか、別のフラグ（例: `doc.processed = True`）で判定する。

**[採用]** doc.text="" + Phase3フィルタを `d.text or d.chunks` に変更済み。

### #4 backfill モードで genai_client を不要に初期化する

- 箇所: `scripts/edinet_load_parallel.py:1030`
- 事象: `genai_client = _get_genai_client()` が `run_mode` に関係なく無条件に呼ばれる。backfill モードでは Embedding を使わないため genai client は不要。Vertex AI SDK の初期化はメモリを消費し（数十 MB）、また Vertex AI の API エンドポイント接続を試みるため、権限不足時にエラーになるリスクがある。
- トリガー: `RUN_MODE=backfill` で実行した場合。
- 影響: 512Mi 環境で数十 MB のメモリ浪費。Vertex AI 権限がない Cloud Run Job では初期化時にエラーになる可能性。
- 根拠: `_get_genai_client`（行 129-139）は `genai.Client(vertexai=True)` を構築する。backfill モードのコードパス（行 1085-1091）は genai_client を一切使用しない。
- 推奨対応: **[検証済み]** `genai_client` の初期化を遅延化し、`run_mode != "backfill"` の場合のみ初期化する。もしくは backfill ブランチの前に移動。

**[採用]** 各モード分岐の直前で遅延初期化する方式で修正済み。

### #5 DELETE → LOAD の非原子性によるデータ喪失リスク

- 箇所: `scripts/edinet_load_parallel.py:931-948`
- 事象: `_delete_existing_rows`（行 933）で対象 FILE_NAME の既存行を DELETE した後、GCS upload + Load Job（行 939-948）を実行する。Load Job が失敗した場合（GCS upload タイムアウト、BQ quota 超過、一時ファイル破損等）、DELETE は完了済みで Load は未完了となり、データが消失する。
- トリガー: Load Job 実行中のネットワーク障害、BQ の一時的なエラー、GCS upload 失敗。
- 影響: 既存データの喪失。冪等性 DELETE が裏目に出て、既存行が消えたまま新行が挿入されない。再実行すれば Phase 1 から再処理されるが、GCS 上の元 HTML が削除されていた場合はリカバリ不能。
- 根拠: DELETE (DML) と Load Job は別トランザクション。BQ にはクロスステートメントのトランザクションはない（スクリプト内を除く）。
- 推奨対応: **[方向性]** (a) DELETE を Load Job 成功後に実行する（ただし一時的に重複行が存在する）、(b) Load Job の `write_disposition` を `WRITE_TRUNCATE` にしてパーティション単位で上書きする（ただし他の日付のデータに影響）、(c) 現状維持で「再実行すればリカバリ可能」とドキュメント化する。バックフィルユースケースでは (a) が安全。

**[採用]** 一時テーブルにLoad → 成功確認 → 既存行DELETE → 本テーブルへINSERT方式で修正済み。

### #6 `print()` がロギングに使用されている（structlog 未使用）

- 箇所: `scripts/edinet_load_parallel.py:172`, `1198-1205`
- 事象: CLAUDE.md §7 は `print禁止。structlogを使用` を規約としているが、本スクリプトでは `print()` が直接使用されている。`BatchLogger.log` 内（行 482）でも `print(line)` を使用。
- トリガー: 常時。
- 影響: 規約違反。Cloud Run のログ集約で構造化ログが効かず、ログ検索・フィルタリングが困難になる。
- 根拠: CLAUDE.md §7 「ロギング: print禁止。structlogを使用」
- 推奨対応: **[方向性]** structlog に移行する。ただし BatchLogger との併存設計が必要なため、改修スコープが広がる。backfill 修正の優先度が高いため、この指摘はバックフィルデプロイ後に対応でもよい。

**[見送り: バックフィルデプロイ後に対応]**

---

## 【改善提案】（可読性・保守性）

### #1 BatchLogger の _lines がメモリに蓄積し続ける

- 箇所: `scripts/edinet_load_parallel.py:471-493`
- 現状: `self._lines` にログ行が追加され続け、`flush_to_gcs` 後もクリアされない。長時間実行のバックフィルではログ行が数千行になり、毎回の `flush_to_gcs` で全行を再アップロードする。
- 提案: `flush_to_gcs` 後に `self._lines = []` でクリアするか、GCS への追記書き込みに変更する。ただし GCS blob は追記をサポートしないため、flush 回数を制限するか、ローカルファイルに書いてから最後にアップロードする方式を検討。

### #2 _load_processed_file_names の SUBMISSION_DATE フィルタと Phase 1 の blob 名日付フィルタの不一致

- 箇所: `scripts/edinet_load_parallel.py:150` vs `582-595`
- 現状: BQ の重複チェックは `SUBMISSION_DATE`（HTML 内の提出日）でフィルタするが、Phase 1 の日付フィルタは blob ファイル名から抽出した日付を使用。提出日と blob 名日付が異なる場合、BQ に既に存在するファイルが `processed_files` に含まれず、不必要に再処理される（冪等性 DELETE → 再 INSERT で実害はないが、無駄な処理が発生する）。
- 提案: どちらか一方のフィルタリング基準に統一する。

### #3 `_extract_date_from_blob_name` のパース戦略が限定的

- 箇所: `scripts/edinet_load_parallel.py:333-339`
- 現状: ファイル名を `_` で split して 8 桁数字を探す。`MERGED_REPORT.html` や `20240101.html`（アンダースコアなし）のような命名パターンではマッチしない。
- 提案: `re.search(r'\d{8}', fname)` でファイル名全体から 8 桁数字を探す方式に変更。ただし誤検出リスクがあるため、年月日の妥当性チェック（年: 2000-2030、月: 01-12、日: 01-31）を加える。

### #4 型ヒント `bucket` パラメータに型注釈がない

- 箇所: 複数関数（`phase1_scan_and_extract`, `phase2_chunk_and_submit`, `phase3_bq_insert` 等）
- 現状: `bucket` パラメータの型が annotation なし。CLAUDE.md §7 は「型ヒント必須」。
- 提案: `storage.Bucket` を型注釈に追加。

---

## 【修正例】（必要な箇所のみ）

#### #1 に対する修正案（ticker 範囲スキャン）

```python
# before: scripts/edinet_load_parallel.py:556-561
    if ticker_from:
        scan_prefix = f"{GCS_PREFIX}/{ticker_from}/"
        logger.log(f"  prefix絞り込み: {scan_prefix} (ticker_from={ticker_from})")
    else:
        scan_prefix = f"{GCS_PREFIX}/"
    blob_iter = bucket.list_blobs(prefix=scan_prefix)

# after: start_offset/end_offset で lexicographic range を使う
    scan_prefix = f"{GCS_PREFIX}/"
    list_kwargs: dict = {"prefix": scan_prefix}
    if ticker_from:
        list_kwargs["start_offset"] = f"{GCS_PREFIX}/{ticker_from}/"
        logger.log(f"  ticker範囲: {ticker_from} ～ {ticker_to or '末尾'}")
    if ticker_to:
        # ticker_to の次の辞書順（末尾に '0' を付けて ticker_to/ 以下を含める）
        list_kwargs["end_offset"] = f"{GCS_PREFIX}/{ticker_to}0/"
    blob_iter = bucket.list_blobs(**list_kwargs)
```

#### #2 に対する修正案（日付なし blob のフィルタ）

```python
# before: scripts/edinet_load_parallel.py:582-595
        blob_date_str = _extract_date_from_blob_name(blob.name)
        if blob_date_str:
            try:
                ...
                if not (d_from <= d_blob <= d_to):
                    continue
            except ValueError:
                ...
                continue

# after: 日付抽出不能な blob はスキップ（バックフィルでは日付指定が必須前提）
        blob_date_str = _extract_date_from_blob_name(blob.name)
        if not blob_date_str:
            skipped += 1
            continue
        try:
            d_blob = date(
                int(blob_date_str[:4]),
                int(blob_date_str[4:6]),
                int(blob_date_str[6:8]),
            )
            if not (d_from <= d_blob <= d_to):
                continue
        except ValueError:
            logger.log(f"  日付パース失敗 → スキップ: {blob.name}")
            skipped += 1
            continue
```

#### #3 に対する修正案（text メモリ解放）

```python
# before: scripts/edinet_load_parallel.py:1085-1091
            if run_mode == "backfill":
                logger.log("backfill モード: Embedding スキップ")
                _phase2_chunk(docs, logger)
                logger.flush_to_gcs()

                processed, skipped, errors = phase3_bq_insert(docs, bucket, logger)

# after: チャンク化後にテキスト解放
            if run_mode == "backfill":
                logger.log("backfill モード: Embedding スキップ")
                _phase2_chunk(docs, logger)
                # チャンク化完了後、元テキストを解放してメモリ節約
                for doc in docs:
                    doc.text = ""
                logger.flush_to_gcs()

                processed, skipped, errors = phase3_bq_insert(docs, bucket, logger)
```

（Phase 3 の `valid_docs` フィルタも合わせて変更が必要）

```python
# before: scripts/edinet_load_parallel.py:878
    valid_docs = [d for d in docs if d.text]

# after: text または chunks があれば有効
    valid_docs = [d for d in docs if d.text or d.chunks]
```

---

## 【確認できなかった事項】

- GCS の blob 命名パターンの実態（`MERGED_REPORT.html` にアンダースコア区切り日付が含まれるか否か）。#2 の影響度はこの確認結果に依存する。
- `text-embedding-004` の Batch Prediction API レスポンスフォーマット（`predictions[0]["embeddings"]["values"]` のパス）が現行バージョンで正しいか。API 仕様変更があれば Phase 2 が silent fail する。
- `_delete_existing_rows` に渡す `file_names` の最大サイズ上限。BQ のパラメータ化クエリで巨大配列を渡した場合の挙動（数万件の FILE_NAME 配列がクエリパラメータサイズ上限に収まるか）。
- Cloud Run Job (512Mi) での実際の RSS メモリ使用量。#3 の修正で十分か、Phase 1 の blob 逐次処理（バッチ分割）が必要かは実測が必要。

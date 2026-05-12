# コードレビュー: extract_monthly_data.py Gemini Batch Prediction 化改修

- 日時: 2026-05-05 11:45 JST
- 対象: `scripts/extract_monthly_data.py` 未コミット diff
- パターン: 1 (新規レビュー)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: Gemini 同期呼び出しを Batch Prediction API に変換し、phase_extract() を2パス構造（Pass1: データ収集 + 非Gemini即時処理、Pass2: バッチ投入 → ポーリング → 結果適用 → 保存）に分割。`--batch / --no-batch` CLI フラグを追加
- 品質評価: **B** — TDnet load の実績あるバッチパターンを忠実に踏襲しており基本構造は堅実だが、ポーリング無限ループ（D-1違反）と overwrite_past_months 共存時のデータ不整合リスクが重大
- 主要リスク:
  1. `_poll_monthly_batch` に deadline がなく、ジョブが stuck すると永久ハング（D-1 違��）
  2. TDnet source + `overwrite_past_months` + batch_mode で、regex overwrite が Gemini バッチ結果と重複蓄積する設計不整合
  3. バッチジョブ全件失敗時に全 deferred_companies が skip 扱いになるが、errors カウントに含まれず exit 0 で終了（A-1/A-7 違反）

## 【重大な指摘】（即修正）

### #1 ポ���リング無限ループに deadline がない（D-1 違反）

- 箇所: `scripts/extract_monthly_data.py:571-584`（`_poll_monthly_batch` 関��）
- 事象: `while True` ループが終了状態に到達しない場合（API がハング、未知の state 値を返す等）、プロセスが永久に停止する
- トリガー: Batch Prediction API が `RUNNING` / `PENDING` / 未知 state を返し続ける場合。Cloud Run Job のタイムアウト（デフォルト10分）では不十分な場合もある
- 影響: Cloud Run Job の実行時間超過課金、ジョブが成功扱いにならず後続 Workflow が止まる
- 根拠: `tdnet_load_parallel.py:680-696` の `_poll_batch_job` も同じ問題を抱えているが、月次抽出は250社×複数リクエストでバッチが大きく、完了に時間がかかるため影響がより顕著
- 推奨対応: 関数引数に `max_wait_sec: int = 3600` を追加し、`deadline = time.time() + max_wait_sec` で break + False 返却

### #2 overwrite_past_months が batch_mode で不整合を起こす

- 箇所: `scripts/extract_monthly_data.py:3318-3348`（overwrite_past_months セクション）と `scripts/extract_monthly_data.py:3776-3783`（deferred 格納）
- 事��: TDnet source + `extraction_method="gemini"` + `overwrite_past_months=True` の企業で、以下の実行順序になる:
  1. per-document ループ: batch_mode なので Gemini リクエストは収集のみ（`rec` は None のまま）→ records に追加されない
  2. overwrite_past_months: `_extract_pdf_all_months`（regex）が実行され、records に追加される
  3. deferred_companies に `records` が格納される（overwrite 結果のみ含む）
  4. バッチ結果が返ってきて `comp_data["records"].append(rec)` で追加��れる
  5. 最終保存時に `drop_duplicates(subset=["year_month"], keep="first")` で重複除去

  問題: 手順5の重複除去は `submission_date` 降順ソート後に `keep="first"` なので最新提出日が優先される。しかし overwrite の source は `"pdf_column_overwrite"` で regex ベース、バッチ結果は `"tdnet"` で Gemini ベース。**同一 year_month に対してどちらが残るかは submission_date の値次第**であり、意図した「overwrite が上書きする」セマンティクスが崩れる
- トリガー: `overwrite_past_months: true` かつ `extraction_method: gemini` のアダプター（Gemini で個別月を抽出 + regex で全月上書き の両方を行う設計）
- 影響: overwrite が意図する全月一括上書きがバッチ結果で上書き返される可能性。データの正確性が submission_date の偶然に依存
- 根拠: 同期モード（既存パス）では overwrite 前に per-document ループの Gemini 結果が records に入り、overwrite が `rec_map` で上書きするため整合する。batch_mode では per-document Gemini 結果がバッチ完了後に追加されるため、overwrite の後に Gemini 結果が来る
- 推奨対応: バッチ結果適用のループ内で、`overwrite_past_months` アダプターの場合は overwrite 結果を最優先するロジックを追加（例: overwrite source のレコードを後から再マージ）。または、`overwrite_past_months` アダプターではバッチモードでも同期モードにフォールバックする

### #3 バッチ失敗時に errors カウントが加算されず exit 0 で終了（A-1/A-7 違反）

- 箇所: `scripts/extract_monthly_data.py:3832-3834` と `scripts/extract_monthly_data.py:3865-3872`
- 事象: `_submit_and_poll_extract_batch` が��ッチ失敗時に空 dict を返す。結果として全 deferred_companies が `records=[]` → `results["skip"].append(ticker)` で skip 扱い。しかし `phase_extract` の戻り値 `results` の `"fail"` リストは空のまま
- トリガー: バッチジョブが FAILED/CANCELLED で返ってきた場合
- 影響: 上流（`main()` 関数）が成功扱いで exit 0 する。Cloud Run Job が SUCCESS 報告し、Workflow が次段を発火する可能性
- 根��: `main()` 末尾に `sys.exit(1 if errors else 0)` のような gate がない（既存の同期パスも同様の問題を抱えている可能性があるが、バッチ失敗は全社分が一括で失われるため影響が大きい）
- 推奨対応: バッチジョブ失敗時は `results["fail"]` に全 deferred 企業を追加し、`_error_entries` にも `error_type="batch_job_failed"` で記録。`main()` に exit code gate を追加

### #4 JSONL request obj の `generation_config` キー名が Batch API 仕様と不整合の可能性

- 箇所: `scripts/extract_monthly_data.py:540-550`（`_build_batch_request_obj` 関数）
- 事象: JSONL 内で `"generation_config"` キーを使用しているが、TDnet load (`tdnet_load_parallel.py:935`) も同じキー名 `"generation_config"` を��っている。Batch Prediction API のドキュメント上は `generationConfig`（camelCase）が正式な場合がある。`google-genai` ライブラリの Batch API がどちらを受け付けるかはバージョン依存
- ���リガー: `google-genai` ライブラリの更新でキー名の解釈が変わった場合
- 影響: バッチジョブが全リクエストを処理不能と判定し、結果0件になる
- 根拠: `tdnet_load_parallel.py` で既に本番稼働実績があるため、現時点では問題ない可能性が高い。ただし TDnet load のバッチモデルは `gemini-3-flash-preview` で、monthly extract も同モデルなので整合する
- 推奨対応: TDnet load と同じキー名を使っており既に動作実績があるため、現時点では対応不要。ライブラリ更新時に注意

### #5 `_deferred_companies` の `adapter` が dict 参照共有で、ループ中の上書きリスク

- 箇所: `scripts/extract_monthly_data.py:3778` と `scripts/extract_monthly_data.py:3235`
- ��象: `_deferred_companies[ticker] = {..., "adapter": adapter, ...}` で adapter dict を参照として保持する。もし後続の企業ループで同一 adapter オブジェクトが再利用・変更される場合、deferred 側の参照も変わる
- トリガー: `companies` リストの複数企業が同一 adapter オブジェクトを共有している場合（通常は JSON から個別読み込みなので起きにくいが、キャッシュ等で共有される��合）
- 影響: バッチ結果パース時に誤った adapter で解析され、フィールド抽出が不正になる
- 根拠: `_batch_meta[_key]` にも `"adapter": adapter` が格納されており、こちらは per-request 単位。deferred_companies は per-ticker 単位。最後の adapter が勝つ構造になっているが、1企業1アダプターが前提なので通常は��題ない
- 推奨対応: リスク低。現状の設計（1��業1アダプター）が維持される限り��題ない。念のため `copy.deepcopy(adapter)` を使う場合はメモリコスト増に注意

### #6 `non-tdnet(pdf)` source で batch_mode 時に PDF をダウンロードしてから GCS URI を使う無駄

- 箇所: `scripts/extract_monthly_data.py:3635-3638` と `scripts/extract_monthly_data.py:3681-3694`
- 事象: `non-tdnet(pdf)` ソースでは `pdf_bytes = blob.download_as_bytes()` で PDF をローカルにダウンロードした後、batch_mode では `_pdf_gcs_uri = f"gs://{GCS_BUCKET}/{blob.name}"` として GCS URI をバッチリクエストに使う。ダウンロードした `pdf_bytes` は使われない
- トリガ���: `non-tdnet(pdf)` + batch_mode + extraction_method="gemini" の全企業
- 影響: 不要な GCS ダウンロード I/O が発生（数十〜数百 PDF × 数MB）。処理時間とネットワーク帯域の無駄
- 根拠: 同期モード���else ブランチ）では `pdf_bytes` を `_extract_pdf_gemini_personal` に渡すので必要。batch_mode では GCS URI 経由で Gemini API に直接渡すので不要
- 推奨対応: batch_mode 時は `pdf_bytes` のダウンロードをスキップ。ただし年月推定 (`_parse_year_month` 含む PDF テキスト読み取り) に `pdf_bytes` が必要な箇所（`pdfplumber.open(io.BytesIO(pdf_bytes))` at line 3647）があるため、その分岐だけ残す設計にする。あるいは、年月推定は先に行い batch_mode 判定は後にする

## 【改善提案】（可読性・保守性）

### #1 バッチヘルパー関数を別モジュールに分離する

- 箇所: `scripts/extract_monthly_data.py:399-729`（約330行のバッチヘルパー群）
- 現状: 既に 3955 行のスクリプトに 330 行が追加され、4000行超の巨大ファイルになっている
- 提案: `scripts/lib_batch_monthly.py` としてバッチヘルパー群（`_build_extract_prompt`, `_build_extract_response_schema`, `_build_batch_request_obj`, `_upload_monthly_batch_jsonl`, `_poll_monthly_batch`, `_download_monthly_batch_results`, `_parse_batch_result_single`, `_parse_batch_result_multi_month`, `_submit_and_poll_extract_batch`）を分離。既存の `scripts/lib_*.py` 命名規約に準拠

### #2 `_batch_meta` の list comprehension でティッカー単位のリクエスト数を数える箇所が O(n)

- 箇所: `scripts/extract_monthly_data.py:3782`
- 現状: `len([k for k in _batch_meta if _batch_meta[k]['ticker'] == ticker])` が企業ごとに全 _batch_meta を走査
- 提案: ログ出力のみの用途なので致命的ではないが、企業数 × リクエスト数が大きい場合に O(n^2)。`_batch_count_by_ticker = defaultdict(int)` で収集時にインクリメントする方が効率的

### #3 `_build_extract_prompt` が同期パス (`extract_from_text_gemini`) のプロンプトと重複

- 箇所: `scripts/extract_monthly_data.py:404-496`（`_build_extract_prompt`）と `scripts/extract_monthly_data.py:216-395`（`extract_from_text_gemini` 内のプロンプト構築）
- 現状: 同期パスの��ロンプトは `extract_from_text_gemini` 関数内に直接構築されている。バッチ用に `_build_extract_prompt` を新設したが、同期パスは未統一
- 提案: 将来的に同期パスも `_build_extract_prompt` を使うように統一すると、プロンプト改善が1箇所で済む。現時点では同期パスのプロンプトとバッチパスのプロンプトが微妙に異なる可能性があり、結果の一貫性が保証されない

### #4 `_parse_batch_result_single` 内の年度フィルタが magic number

- 箇所: `scripts/extract_monthly_data.py:548-549`
- 現状: `if 2015 <= num <= 2035: continue` で年度っぽい数値をフィルタしているが、このロジックは同期パス（`extract_from_text_gemini`）にも存在する。2035年以降に問題になる
- 提案: 定数化（`YEAR_NOISE_RANGE = (2015, 2035)`）して1箇所で管理。同期パスと共通化

## 【修正例】（必要な箇所のみ）

#### #1 に対する修正案
```python
# before: scripts/extract_monthly_data.py:568-584
def _poll_monthly_batch(
    client, job_name: str, logger: logging.Logger, label: str = "extract",
) -> bool:
    """バッチジョブをポーリングする. 成功なら True."""
    while True:
        job = client.batches.get(name=job_name)
        ...
        time.sleep(BATCH_POLL_INTERVAL_MONTHLY)

# after
def _poll_monthly_batch(
    client, job_name: str, logger: logging.Logger, label: str = "extract",
    max_wait_sec: int = 3600,
) -> bool:
    """バッチジョブをポ��リングする. 成功なら True."""
    deadline = time.time() + max_wait_sec
    while time.time() < deadline:
        job = client.batches.get(name=job_name)
        state = job.state.name if hasattr(job.state, "name") else str(job.state)
        logger.info(f"  [batch:{label}] ジョブ状態: {state}")

        if state in ("JOB_STATE_SUCCEEDED", "SUCCEEDED", "completed"):
            return True
        if state in ("JOB_STATE_FAILED", "FAILED", "JOB_STATE_CANCELLED", "CANCELLED",
                     "failed", "cancelled"):
            logger.warning(f"  [batch:{label}] ジョブ失敗: {state}")
            return False

        time.sleep(BATCH_POLL_INTERVAL_MONTHLY)

    logger.warning(f"  [batch:{label}] タイムアウト ({max_wait_sec}s)")
    return False
```

#### #3 ��対する修正案
```python
# before: scripts/extract_monthly_data.py:3832-3834
if _batch_lines:
    logger.info(f"--- Gemini Batch 投入: ... ---")
    batch_results = _submit_and_poll_extract_batch(_batch_lines, gcs, logger)

# after
if _batch_lines:
    logger.info(f"--- Gemini Batch 投入: {len(_batch_lines)} リクエスト / {len(_deferred_companies)} 社 ---")
    batch_results = _submit_and_poll_extract_batch(_batch_lines, gcs, logger)

    if not batch_results:
        # バッチジョブ失敗: 全 deferred 企業を fail 扱い
        for ticker in _deferred_companies:
            results["fail"].append(ticker)
            _error_entries.append({
                "ticker": ticker, "error_type": "batch_job_failed",
                "error_detail": "Gemini Batch Prediction ジョブ失敗",
            })
        logger.warning(f"  Gemini Batch 全件失敗: {len(_deferred_companies)} 社を fail 記録")
    else:
        # 結果を各企業の records に振り分け ...
```

## 【確認できなかった事項】

- `google-genai` ライブラリの Batch Prediction API が `response_schema` を JSONL リクエスト内に埋め込む形式で正しく動作するか（TDnet load では `response_schema` 未使用のため実績がない。TDnet load は `response_mime_type: "application/json"` のみ）
- Batch Prediction API の入力 JSONL サイズ上限。250社 × 複数文書で数万行になる可能性があるが、API 上限（リクエスト数/JSONL ファイルサイズ）が不明
- `VERTEXAI_REGION = "global"` が Batch Prediction で正しいか。TDnet load は `us-central1` を使っている。`google-genai` Client が `location="global"` で batch API を呼べるかは実行して確認する必要がある
- `multi_month` パース（`_parse_batch_result_multi_month`）で `re.search(r"\[.*\]", text_resp, re.DOTALL)` を使うが、JSON 内に `]` が含まれる場合に最初の `]` で切れないか（`re.DOTALL` + greedy `.*` なので最後の `]` まで取る設計だが、Gemini がコードブロック等で `]` を出す可能性）

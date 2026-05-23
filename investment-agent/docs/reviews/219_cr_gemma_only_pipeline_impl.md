# 219 — Gemma専用パイプライン化 実装レビュー（Phase A〜E）

## レビュー対象ファイル

**コード（Phase A〜D）:**
- `scripts/gemma_tpu_worker.py` — Pass 2 処理追加（`build_prompt_pass2`, 2-pass main, 独立 resume, `_PASS2_CATEGORIES`）
- `scripts/tdnet_load_parallel.py` — PyMuPDF換装 + Gemini完全剥離 + `_merge_gemma_pass2()`/`_load_gemma_pass2_results()` 追加
- `workflows/ai_processing_flow.yaml` — ヘッダーコメント更新（Gemini→Gemma 2-pass）
- `pyproject.toml` — `pymupdf>=1.27.2` 追加

**ドキュメント（Phase E-6〜E-8）:**
- `docs/knowledges/tools/013_tdnet_load.md` — 現況サマリ更新 + 設計判断履歴セクション追加
- `docs/data_catalog/bq_tdnet_documents.md` — チャンク化仕様 + Vector Index 仕様セクション追加

## レビューパターン

**code-reviewer パターン 2（実装レビュー、改修型）**

## 事象・背景

`docs/plans/tools-013_gemma_only_pipeline_20260520_220510.md` の Phase A〜E を実装完了（コミット `92d5113d`）。
本日中に E-2〜E-5（smoke test + Cloud Build デプロイ）を実行する前に、実装の不備を洗い出したい。

**プランは既に `218_cr_gemma_only_pipeline_plan.md` でレビュー済み。本レビューのスコープは実装のみ。**
プランの設計判断（Gemini 廃止の妥当性、Pass 2 採用の是非、PoC 矛盾の指摘等）は対象外。
プラン記載通りに実装されているか、副作用バグがないか、削除波及漏れがないか、を見てほしい。

## 補足情報

- 対象コミット: `92d5113d`（差分: 6 files / +916 / -543）
- `_PASS2_CATEGORIES` は両スクリプトで定義し、片方修正時の同期義務をコメント明記（プラン A-1 改善#1 対応）
- 既存 `_NEEDS_SUB_CATEGORIES` / `_NEEDS_GEMINI_ANALYSIS` 定数は撤去済み（プラン C-3, C-4）
- `DocInfo.needs_analysis` / `analysis_model` フィールドは波及削除済み
- `phase2_vision_batch()` 関数本体は削除済み（プラン C-4 レビュー指摘 #7 対応）
- 旧 `full/submit/resume` モードの Phase 3 経路はエラー終了化（プラン C-4）
- 画像PDF滞留対策: `AI_STATUS='skipped_image_pdf'` 新ステータス（プラン B-2b 案 (b)）

## 重点確認ポイント

1. **`_PASS2_CATEGORIES` 同期義務コメント**: 両ファイルに「片方修正時は両方同期せよ」が明示されているか
2. **Pass 1/Pass 2 独立 resume**: Pass 1 完了済み + Pass 2 途中で preempt → 再起動で Pass 1 スキップ・Pass 2 のみ resume が成立するか
3. **`_SUCCESS` 作成タイミング**: Pass 2 完了後のみ（Pass 1 完了時点では作成しない）
4. **`build_prompt_pass2` の truncate**: `text[:TEXT_LIMIT]` を使用（プラン: `truncate_for_model` 不在のため代用）
5. **`needs_analysis` / `analysis_model` の残存参照**: grep 0 件を確認したか
6. **`_merge_gemma_pass2` diff カウンタ**: added_cnt / removed_cnt がログ出力されるか（プラン E-4 観測ポイント）
7. **画像PDF B-2b**: `skipped_image_pdf` への遷移が正しく行われるか（`text=""` かつ `extract_method=="none"` の条件）
8. **`GcsPusher.push_fn` パラメータ**: Pass 1 と Pass 2 で異なる GCS パスに書き込まれるか
9. **`call_one(prompt_fn=...)` パラメータ**: Pass 2 で `build_prompt_pass2` が正しく渡されるか
10. **Gemini クライアント残存**: `genai.Client` は Embedding Batch 用に維持（完全削除でないか確認）

---

# レビュー結果

- 日時: 2026-05-21 06:30 JST
- 対象: コミット `92d5113d`（6 files / +916 / -543）
- パターン: 2（既存パイプラインの改修レビュー）
- レビュアー: Claude (code-reviewer runbook, メインエージェント直接実行 — Agent ツール不可環境のため §起動方式の制約により 004-1 への追記はスキップ)

## 【サマリー】

- 変更の要約: Gemini Flash Batch を廃止し Gemma 2-pass に置換、PDF抽出を PyMuPDF + pdfminer fallback に換装、Vision OCR を廃止して `skipped_image_pdf` 新ステータスを導入。
- 品質評価: **C** — 本筋（Pass 2 / 受注マージ / state.json / resume）はおおむね正しい。一方で「**E-2〜E-5 を回す前に必ず潰すべき deploy ブロッカー**」が複数件存在（pymupdf が Dockerfile に欠落、削除された `GEMINI_LOCATION` への参照残存、プラン C-4「旧モードのエラー終了化」未実装）。これらは smoke test 段階で発覚するが、コミット前に E-1（grep 0件確認）を行えば気付けたはずの取りこぼし。
- 主要リスク:
  1. **pymupdf が `docker/Dockerfile.tdnet-load-daily` に欠落** — コンテナで `import fitz` が ImportError、`_extract_text_pymupdf` の `except Exception` がそれを飲み込み、全件 pdfminer フォールバックに silent 劣化する（プラン B-2a の効能が完全に消える）。
  2. **`GEMINI_LOCATION` 削除漏れ** — `_get_genai_client()` (157 行) がまだ参照しており、`JOB_MODE=full` で実行されると `NameError`。プラン C-4「旧経路をエラー終了化」が実装されておらず、旧 `run_mode in (full, submit, resume)` で `phase2_vision_batch` / `phase3_*` への呼び出しが残存して NameError 連発する。
  3. **Pass 2 API 失敗時に Pass 1 の「受注高/受注残高」を消す** — `_merge_gemma_pass2` が Pass 2 結果の `error` フィールドを判定せず、`sub_categories=[]` を「該当なし」と解釈して discard する。HTTP timeout 1 件で受注ラベルが落ちる。

---

## 【パターン2: 改修プラン評価】

### 妥当性

- プランの方針（Gemini 廃止 + Gemma Pass 2 + PyMuPDF 換装 + Vision OCR 廃止）は実装上**おおむね追従**している。Pass 1/2 独立 resume、`_PASS2_CATEGORIES` 両ファイル同期コメント、`_SUCCESS` を Pass 2 完了後に作成、`call_one(prompt_fn=...)` / `GcsPusher(push_fn=...)` パラメータ化は正しく実装されている。
- ただし**プラン C-4「旧 `full/submit/resume` モードの Phase 3 経路はエラー終了させる」が実装されていない**。`run_tdnet_batch_etl` の 2018, 2021, 2072, 2077, 2088, 2091 行に削除済み関数（`phase2_vision_batch` / `phase3_analysis_batch` / `_phase3_submit` / `_phase3_poll_and_apply`）への呼び出しが**そのまま残存**。さらに 2005 行で `_get_genai_client()` を呼ぶが、`_get_genai_client` 内部の `GEMINI_LOCATION` 定数も削除済み。これは「プラン宣言と実装の乖離」であり、改修プラン評価としては**未完了**。

### 副作用・デグレードチェック

- [x] **`phase4_chunk_and_embed` の第3引数置換**: `tdnet_load_parallel.py:1939` が `phase4_chunk_and_embed(docs, bucket, embed_client, logger, embed_client)` に変わった。元は `client=genai_client, embed_client=embed_client` の2 client 体制。`phase4_chunk_and_embed` 内部 (880-890行) は `_embed_client = embed_client or client` で `embed_client` が渡れば `client` を無視する設計なので**動作は同じ**。ただし「`client` 引数に `embed_client` を渡す」のは意味的に奇妙で、後続の改修者が混乱する。リファクタが半端で、`client` 引数自体を削除する方が clean。
- [x] **`_apply_gemma_results` の `sub_categories_gemma` バックアップ**: Pass 1 結果を `doc.sub_categories_gemma` に複製する仕組みは維持されており、`_merge_gemma_pass2` の前提（`set(doc.sub_categories_gemma)`）と整合。問題なし。
- [x] **`AI_STATUS='pending_gemma'` UPDATE 順序**: `phase_ai_prepare` 内で先に `_update_ai_status(doc_ids_with_text, 'pending_gemma')` → 続けて `_update_ai_status(doc_ids_image_pdf, 'skipped_image_pdf')` の順序。`_update_ai_status` の SQL は `AI_STATUS IN ('pending', 'pending_gemma')` を WHERE に持つので、`pending_gemma` 化済みの doc を再度 `skipped_image_pdf` に更新する可能性はあるが、`doc_ids_image_pdf` は `not d.text` で抽出するため `doc_ids_with_text` と重複しない。問題なし。
- [x] **`needs_vision` フィールド残存**: `DocInfo.needs_vision` フィールド (265行) と `_serialize_docs` / `_deserialize_docs` の `needs_vision` キー (766/790行) は残置。誰も読まない dead field だが、過去 state.json との互換性のためなら許容範囲。明示すべき。
- [x] **画像PDF doc も state.json に投入される**: `phase_ai_prepare` は全 doc（画像PDFの `text=""` 含む）を state.json に書き出す。Gemma TPU worker は `pending_docs` を `text` の有無でフィルタしないため、`text=""` の doc を Pass 1 prompt に投入 → 無意味な推論 → TPU リソース消費。BQ INSERT は `if d.text` でガードされるので最終データは汚れないが、**TPU コストの無駄使い**になる。プラン B-2b の趣旨は「pending 滞留防止」だけで Gemma 投入抑制はスコープ外と解釈できるが、設計上は明示すべき。

### 抜け漏れ（類似観点での横展開含む）

- [x] **Dockerfile 横展開漏れ**: `pyproject.toml` への pymupdf 追加（プラン B-1）が必要だが、**実際にはコミット 92d5113d の差分には pyproject.toml が含まれていない**（pre-existing で `pymupdf>=1.27.2.2` が既存）。コミットメッセージは「pyproject.toml: pymupdf>=1.27.2 追加」を主張しているが偽。さらに重要なのは、`docker/Dockerfile.tdnet-load-daily` の `pip install` 行に pymupdf が無いこと。**3ジョブ（tdnet-load-daily / tdnet-ai-prepare / tdnet-ai-finalize）の共通イメージにて fitz が import 失敗**する。
- [x] **`Dockerfile.tdnet-load-parallel` も同様に pymupdf 欠落**。こちらは旧 `tdnet-load-parallel` ジョブ用なので段階廃止対象だが、もし残っているなら同じく更新が必要。
- [x] **`GEMINI_LOCATION` 削除漏れ**: 定数定義は削除済み (旧 75行) だが、`_get_genai_client()` の 157行が参照を保持。`JOB_MODE=full` で `NameError`。
- [x] **削除済み関数の呼び出し残存**: `run_tdnet_batch_etl` 内の旧 full/submit/resume 経路で `phase2_vision_batch` / `phase3_analysis_batch` / `_phase3_submit` / `_phase3_poll_and_apply` を呼ぶ箇所が 5 箇所残存。プラン C-4「エラー終了化」が未実装。
- [x] **dead code `_extract_text_pypdf2` / `PyPDF2` import**: `_extract_text_pypdf2` (392行) は新コードからは呼ばれない。`PyPDF2` のトップレベル import (32行) も同様。プラン B-1「PyPDF2 完全置換」の趣旨では削除すべき。
- [x] **`Dockerfile.tdnet-load-daily` 内の `PyPDF2>=3.0` 行**: 上記と連動。新コードでは不要。
- [x] **`workflows/ai_processing_flow.yaml` の `find_recent_pending` クエリ**: `WHERE AI_STATUS = 'pending'` のみ。`skipped_image_pdf` 導入後も既存挙動と整合（pending 以外は対象外）。問題なし。

### 新規リスク

- **silent 劣化リスク（最大）**: Dockerfile に pymupdf が無いと、`_extract_text_pymupdf` の `except Exception: return "", 0` が **ImportError を含む全例外を黙って吸収**する。本番 deploy 後にログから「pymupdf 全件失敗→pdfminer fallback」が読み取れず、抽出精度向上の効能が0になる。E-2 smoke test の検証項目に「`extract_method='pymupdf'` が一定割合存在する」「`import fitz` 単体テストが OK」を含めるべき。
- **API 不安定で受注ラベルが消える**: Pass 2 は決算短信 + 決算説明資料のみ対象だが、TPU プリエンプション後再起動の境界で Pass 2 1件だけが HTTP error になると Pass 1 で検出した「受注高/受注残高」が消える。Pass 1 では検出されてかつ Pass 2 で API error の場合は Pass 1 の値を保持すべき（後述 #2 推奨）。
- **`JOB_MODE` 未設定運用時のクラッシュ**: `main()` の `job_mode = args.job_mode or os.environ.get("JOB_MODE") or "full"` で default が `"full"`。Cloud Run Job のジョブ定義側で必ず `JOB_MODE` を渡している前提だが、デバッグ目的でローカル実行（引数なし）すると即 NameError。default を `"load"` か `"unknown"` に変えて明示 raise する方が安全。

---

## 【重大な指摘】（即修正）

### #1 pymupdf が Dockerfile に欠落 — fitz import が silent fallback でデグレ

- 箇所: `docker/Dockerfile.tdnet-load-daily`（および `docker/Dockerfile.tdnet-load-parallel`）。コード側は `scripts/tdnet_load_parallel.py:422` の `import fitz`。
- 事象: コンテナでの `import fitz` が `ModuleNotFoundError` を投げる。`_extract_text_pymupdf` (412-433) は `try/except Exception: return "", 0` で全例外を吸収するため、呼び出し側 (647行 / 1261行付近) は「fitz が無い」事実を**ログにも残さず**全件 pdfminer フォールバックに流れる。
- トリガー: `cloudbuild.tdnet-load-daily.yaml` で build → 3 ジョブ（`tdnet-load-daily` / `tdnet-ai-prepare` / `tdnet-ai-finalize`）に同じ image をデプロイ。E-2 で `tdnet-ai-prepare` を回すと発火。
- 影響: プラン B-2a「PyMuPDF 換装による抽出精度向上」の効能が**完全消失**。`extract_method='pymupdf'` の doc は 0 件、全件 `pdfminer` または `none`。コスト/性能シミュレーションも崩れる。
- 根拠: `docker/Dockerfile.tdnet-load-daily` の `pip install` 行に `PyPDF2>=3.0 / pdfminer.six` はあるが `pymupdf` (`PyMuPDF`) は無い。`pyproject.toml` には `pymupdf>=1.27.2.2` (69行) があるが、これは `uv` ローカル用で Dockerfile からは無関係。
- 推奨対応 **[検証済み]**:
  1. `docker/Dockerfile.tdnet-load-daily` の `pip install` 行に `PyMuPDF>=1.27.2` を追加（PyPI 名は `PyMuPDF`、import 名は `fitz`）。
  2. `_extract_text_pymupdf` の `except Exception` を細分化し、`ModuleNotFoundError` / `ImportError` だけは別ログを出して再 raise するか、少なくとも `logger.log` で記録する。silent fallback を防ぐ。
  3. `Dockerfile.tdnet-load-parallel` も同様に更新（廃止予定でも E-2/E-3 期間中は本番で動く可能性あり）。
  4. プラン B-1「pyproject.toml: pymupdf>=1.27.2 追加」はコミットメッセージとは裏腹に既存（コミット 92d5113d の差分には含まれていない）。プランの記述を訂正するか、コミットメッセージを訂正する。

### #2 Pass 2 API 失敗時に Pass 1 検出済みの「受注高/受注残高」を誤って削除

- 箇所: `scripts/tdnet_load_parallel.py:1530-1556`（`_merge_gemma_pass2`）。
- 事象: Pass 2 で vLLM が HTTP non-200 / no-choices / Exception を返した doc は `gemma_pass2_CURRENT.jsonl` に `{"doc_id": ..., "sub_categories": [], "is_monthly": null, "error": "..."}` の形で記録される（`gemma_tpu_worker.py:382-410`）。`_merge_gemma_pass2` は `p2 = pass2_results.get(doc.doc_id, {})` で truthy 判定後、`if "受注高/受注残高" in pass2_sub: ... else: gemma_sub.discard("受注高/受注残高")` を実行。**error レコードでも `sub_categories=[]` を「該当なし」と解釈** → Pass 1 で検出した「受注高/受注残高」を消す。
- トリガー: TPU プリエンプト直前の inflight Pass 2 リクエスト、vLLM 一時 500、HTTP timeout などで Pass 2 1件が error 終了。
- 影響: 決算短信/決算説明資料に書かれた受注情報の検出ラベルが欠落 → 後段の受注検索・分析が漏れる。Pass 2 だけが失敗するケースは稀だが、TPU プリエンプト多発時は確率的に起こる。
- 根拠: `gemma_tpu_worker.py:382, 387, 409` が `sub_categories: []` を error 経路で出力。`tdnet_load_parallel.py:1543-1555` で error フィールドを未参照。
- 推奨対応 **[検証済み]**:
  - `_merge_gemma_pass2` で `p2.get("error")` が truthy なら、その doc については Pass 1 の値を保持する（Pass 2 結果無し相当 = `no_pass2_cnt`）。
  - 加えて `merged_cnt` カウンタを「正常 Pass 2 反映」「Pass 2 error 数」「Pass 2 結果なし数」の3区分にログ分離すると E-4 観測ポイントが明確になる。

### #3 削除済み関数への呼び出しが残存 — `JOB_MODE=full` で NameError

- 箇所: `scripts/tdnet_load_parallel.py` の以下:
  - 2018行: `_phase3_poll_and_apply(docs, bucket, genai_client, logger, phase3_info)`
  - 2072行: `phase2_vision_batch(docs, bucket, genai_client, logger)`
  - 2077行: `phase3_info = _phase3_submit(docs, bucket, genai_client, logger)`
  - 2088行: `phase3_analysis_batch(docs, bucket, genai_client, logger)`
  - 加えて 2005行 `_get_genai_client()` が **削除済みの `GEMINI_LOCATION`** (削除位置: 旧 75行) を 157行で参照。
- 事象: `JOB_MODE=full`（または `JOB_MODE` 未設定で default `full`）で `run_tdnet_batch_etl` を実行すると、最初に 2005行で `NameError: name 'GEMINI_LOCATION' is not defined` が発生。仮にそれを直してもさらに `phase2_vision_batch` / `_phase3_*` で `NameError` 連発（プラン C-4 で削除済みのため）。
- トリガー: `JOB_MODE` 未設定でローカル実行、または運用者がうっかり `JOB_MODE=full` で Cloud Run Job を実行。
- 影響: 即クラッシュ。本番3ジョブは `JOB_MODE=load/ai-prepare/ai-finalize` を渡している前提なので即時影響は限定的だが、デバッグ/手動運用での不可解クラッシュの原因になる。プラン C-4 の宣言「旧モードはエラー終了させる」が**実装欠落**。
- 根拠: 上記 grep 結果。
- 推奨対応 **[検証済み]**:
  - `run_tdnet_batch_etl` の冒頭で `if job_mode == "full": raise SystemExit("job_mode='full' は廃止済み。load/ai-prepare/ai-finalize を指定してください。")` を追加し、2004行以降のレガシーコード（`# ── 従来互換（job_mode='full'）` セクション全部）を削除する。
  - 同時に `_get_genai_client()` 関数自体を削除（呼び出し元が無くなるため）。`GEMINI_LOCATION` 参照も消える。
  - `main()` の `job_mode = args.job_mode or os.environ.get("JOB_MODE") or "full"` の default を `"load"` に変更するか、未指定で raise する。

### #4 Pass 2 で `_load_gemma_pass2_results` の JSON parse 失敗が silent

- 箇所: `scripts/tdnet_load_parallel.py:1500-1517`（`_load_gemma_pass2_results`）。
- 事象: Pass 1 の `_load_gemma_results` (1397-1414) は `parse_errors` をカウントして stderr に WARN ログを出す（004 A-3「silent continue 禁則」遵守）。一方 Pass 2 版は `except json.JSONDecodeError: continue` のみで silent。
- 影響: gemma_pass2_CURRENT.jsonl が部分破損していても気付かず、Pass 2 結果が欠落したまま finalize が走る。
- 根拠: 1510行 `except json.JSONDecodeError: continue`。
- 推奨対応 **[方向性]**: Pass 1 と同じ形で parse_errors カウンタを追加し、`> 0` なら logger ないし stderr で警告を出す。

---

## 【改善提案】（可読性・保守性）

### #1 `phase4_chunk_and_embed` の第3引数の意味が曖昧

- 箇所: `scripts/tdnet_load_parallel.py:1939`
- 現状: `phase4_chunk_and_embed(docs, bucket, embed_client, logger, embed_client)` — 第3引数の意味的役割は「分析用 client」だが、Gemini 廃止で実質不使用。`embed_client` を 2 回渡しているのは「関数シグネチャを変えたくない」苦肉策。
- 提案: `phase4_chunk_and_embed` のシグネチャを `(docs, bucket, embed_client, logger)` に変更し、不要な `client` 引数を削除。呼び出し側を `phase4_chunk_and_embed(docs, bucket, embed_client, logger)` に。

### #2 `_extract_text_pymupdf` の fitz.Document を close していない

- 箇所: `scripts/tdnet_load_parallel.py:412-433`
- 現状: `doc = fitz.open(stream=pdf_bytes, filetype="pdf")` の `doc.close()` 呼び出しが無い。`fitz.Document` は C 拡張側でファイルハンドル/メモリを保持。例外時もリーク。
- 提案: `try/finally: doc.close()` を入れる（あるいは Python 3.4+ ContextManager サポートの `with fitz.open(...) as doc:` を使う、これが PyMuPDF でも有効）。数千件処理で fd 枯渇/メモリリークのリスク削減。

### #3 dead code: `_extract_text_pypdf2` / `PyPDF2` トップレベル import / `Dockerfile` 内 PyPDF2

- 箇所: `scripts/tdnet_load_parallel.py:32, 392-410` + `docker/Dockerfile.tdnet-load-daily` の `pip install` 行
- 現状: 新コードからは呼ばれない（grep で確認済み）。
- 提案: 関数定義・import 行・Dockerfile の PyPDF2 行を削除。プラン B-1「PyPDF2 完全置換」の趣旨に沿わせる。

### #4 `DocInfo.needs_vision` フィールド残存

- 箇所: `scripts/tdnet_load_parallel.py:265, 766, 790`
- 現状: Vision OCR 廃止で誰も使わない dead field。`_serialize_docs` / `_deserialize_docs` も読み書きしている。
- 提案: 旧 state.json との後方互換性が不要なら削除。必要なら明示コメントで「backward compat only, 2026-05-21 以降 unused」と書く。

### #5 画像PDF doc も Gemma Pass 1 投入されている

- 箇所: `scripts/tdnet_load_parallel.py:_save_ai_prepare_state` (1290 前後) + `scripts/gemma_tpu_worker.py:574-588`
- 現状: `phase_ai_prepare` は `text=""` の doc も含めて state.json に書き、worker は `text` 有無でフィルタしない。`text=""` の doc を Pass 1 prompt に投入。
- 提案: ① `_save_ai_prepare_state` で `if d.text` の doc のみ書き出す、または ② worker の `pending_docs` フィルタに `d.get("text")` 条件を追加。TPU リソース節約。
- 注意: BQ INSERT 側は `if d.text` ガード済みでデータは汚れない。「効率改善」のレベル。

### #6 `_PASS2_CATEGORIES` 同期義務の機械的検証手段が無い

- 箇所: `scripts/tdnet_load_parallel.py:241-243` と `scripts/gemma_tpu_worker.py:74-76`
- 現状: 「★ 同期義務」コメントで人間注意喚起しているが、CI で食い違いを検出する仕組みがない。
- 提案: 後段で smoke test スクリプト or `cloudbuild.tdnet-load-daily.yaml` の事前 step で `python -c "import sys; ...; assert set_a == set_b"` 風の guard を入れる。今回は範囲外でも、知見MDの「同期義務」を運用ルールに昇格させる。

### #7 コミットメッセージと差分の乖離

- 箇所: コミット 92d5113d のメッセージ「Phase B: ... pyproject.toml: pymupdf>=1.27.2 追加」
- 現状: 実際の差分には `pyproject.toml` が含まれていない（pre-existing で既に `pymupdf>=1.27.2.2`）。
- 提案: 次のコミットメッセージで訂正、または amend は禁止規約のためフォローアップコミットで明示。

---

## 【修正例】（必要な箇所のみ）

#### #1 に対する修正案

```dockerfile
# before: docker/Dockerfile.tdnet-load-daily の pip install 行
RUN pip install --no-cache-dir \
    PyPDF2>=3.0 \
    google-cloud-storage>=2.16 \
    ...
    pdfminer.six>=20231228 \
    ...

# after
RUN pip install --no-cache-dir \
    PyMuPDF>=1.27.2 \
    google-cloud-storage>=2.16 \
    ...
    pdfminer.six>=20231228 \
    ...
# PyPDF2 行は削除（dead code 連動）
```

```python
# before: scripts/tdnet_load_parallel.py:412-433
def _extract_text_pymupdf(pdf_bytes: bytes) -> tuple[str, int]:
    import fitz
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        ...
    except Exception:
        return "", 0

# after（リソースリーク + silent fallback 両対応）
def _extract_text_pymupdf(pdf_bytes: bytes) -> tuple[str, int]:
    try:
        import fitz  # noqa: PLC0415
    except ImportError as e:
        # silent fallback だと B-2a の効能が消えるので明示エラー
        raise RuntimeError(f"PyMuPDF (fitz) not installed: {e}") from e
    doc = None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_count = len(doc)
        buf: list[str] = []
        for i, page in enumerate(doc, 1):
            text = page.get_text("text")
            text = _normalize_page_text(text)
            buf.append(f"[PAGE {i}]\n{text}\n")
        return "\n".join(buf), page_count
    except Exception:
        return "", 0
    finally:
        if doc is not None:
            doc.close()
```

#### #2 に対する修正案

```python
# before: tdnet_load_parallel.py:1530-1556
for doc in docs:
    gemma_sub = set(doc.sub_categories_gemma or [])
    if doc.main_category in _PASS2_CATEGORIES:
        p2 = pass2_results.get(doc.doc_id, {})
        if p2:
            pass2_sub = set(p2.get("sub_categories", []))
            if "受注高/受注残高" in pass2_sub:
                ...
            else:
                ...
            merged_cnt += 1
        else:
            no_pass2_cnt += 1
    doc.sub_categories = sorted(gemma_sub)

# after
error_pass2_cnt = 0
for doc in docs:
    gemma_sub = set(doc.sub_categories_gemma or [])
    if doc.main_category in _PASS2_CATEGORIES:
        p2 = pass2_results.get(doc.doc_id, {})
        if p2 and not p2.get("error"):
            # Pass 2 が正常終了した場合のみ反映
            pass2_sub = set(p2.get("sub_categories", []))
            if "受注高/受注残高" in pass2_sub:
                if "受注高/受注残高" not in gemma_sub:
                    added_cnt += 1
                gemma_sub.add("受注高/受注残高")
            else:
                if "受注高/受注残高" in gemma_sub:
                    removed_cnt += 1
                gemma_sub.discard("受注高/受注残高")
            merged_cnt += 1
        elif p2 and p2.get("error"):
            # Pass 2 API error → Pass 1 の値を保持（safe fallback）
            error_pass2_cnt += 1
        else:
            no_pass2_cnt += 1
    doc.sub_categories = sorted(gemma_sub)
logger.log(
    f"Pass2マージ完了: {_PASS2_CATEGORIES} 対象 {merged_cnt}件マージ "
    f"(追加 {added_cnt}件/削除 {removed_cnt}件) / "
    f"Pass2 error fallback {error_pass2_cnt}件 / "
    f"Pass2結果なし fallback {no_pass2_cnt}件"
)
```

#### #3 に対する修正案

```python
# before: run_tdnet_batch_etl 末尾の旧 full/submit/resume 経路（2004-2110行）
# ── 従来互換（job_mode='full'）: 以下は既存ロジック ──
genai_client = _get_genai_client()
embed_client = _get_genai_client_embedding()

try:
    if run_mode == "resume":
        ...
        _phase3_poll_and_apply(docs, bucket, genai_client, logger, phase3_info)
        ...
    else:
        ...
        phase2_vision_batch(docs, bucket, genai_client, logger)
        ...

# after（プラン C-4 の宣言を実装）
raise SystemExit(
    f"job_mode='{job_mode}'（full/submit/resume）は 2026-05-21 廃止。"
    " load/ai-prepare/ai-finalize を指定してください。"
    " 詳細: docs/plans/tools-013_gemma_only_pipeline_20260520_220510.md"
)
```

ついでに `_get_genai_client()` 関数本体（152-162）も削除。`GEMINI_LOCATION` 参照が消える。

---

## 【重点確認ポイントへの回答】

1. **`_PASS2_CATEGORIES` 同期義務コメント**: ✅ 両ファイル (tdnet_load_parallel.py:241-243, gemma_tpu_worker.py:74-76) に明示。
2. **Pass 1/Pass 2 独立 resume**: ✅ `gemma_tpu_worker.py:507-588` で Pass 1 / Pass 2 を独立に resume。preempt 後再起動で Pass 1 を skip、Pass 2 のみ再開する設計が成立。ただし Pass 2 の overlap_ratio 整合性チェック (Pass 1 の 2c に相当するチェック) は無し。許容範囲。
3. **`_SUCCESS` 作成タイミング**: ✅ Pass 2 完了後のみ作成（657行）。Pass 1 完了時点では作成しない。
4. **`build_prompt_pass2` の truncate**: ✅ `text[:TEXT_LIMIT]` で代用（188行）。Pass 1 の `_prompt_tail` も `text[:TEXT_LIMIT]` (168行) なので等価。
5. **`needs_analysis` / `analysis_model` 残存参照**: ✅ grep 0件確認済み。完全削除。
6. **`_merge_gemma_pass2` diff カウンタ**: ✅ added_cnt / removed_cnt をログ出力（1553-1556）。
7. **画像PDF B-2b**: ✅ 条件 `not d.text and d.extract_method == "none"` で正しく抽出（1761行）、`AI_STATUS='skipped_image_pdf'` に更新。
8. **`GcsPusher.push_fn`**: ✅ Pass 1 = `gcs_push_current`（デフォルト）、Pass 2 = `gcs_push_pass2`（明示）。GCS パスは `gemma_CURRENT.jsonl` / `gemma_pass2_CURRENT.jsonl` で分離。
9. **`call_one(prompt_fn=...)`**: ✅ Pass 2 で `build_prompt_pass2` を渡している（gemma_tpu_worker.py:634）。
10. **Gemini クライアント残存**: ⚠️ `_get_genai_client_embedding()` (Embedding 用、us-central1) は維持。一方 `_get_genai_client()` (旧 Gemini 分析用、global) は呼び出し元が旧 full モードのみで実質 dead だが**関数本体は残存**しており、その内部で削除済み `GEMINI_LOCATION` を参照 → 旧モード実行で NameError。

---

## 【確認できなかった事項】

- **E-2 smoke test 実行**: 本レビューは静的解析のみで、実際の Cloud Run Job 実行はしていない。fitz import 失敗の挙動、Pass 2 error の混入頻度は smoke test で実測すべき。
- **`_extract_text_pymupdf` の抽出品質**: PyMuPDF と pdfminer の抽出品質差は実 PDF で比較していない。プラン B-2a の効能評価は smoke test 後に E-4 で確認。
- **`gemma_pass2_CURRENT.jsonl` のサイズ規模**: 決算短信+決算説明資料は週次で数百件規模と想定するが、実測値は不明。OOM 予備軍にはならない想定だが要確認。
- **Workflow Step 3 callback 受信時の Pass 2 完了保証**: worker.py が Pass 2 完了後に `notify_callback("succeeded", ...)` を送るので問題ないと推論できるが、preempt 後再起動シナリオで callback が 2 重送信されないかは未検証（Workflows callback は冪等のはず、要確認）。

---

## 【メインエージェント直接実行による制約】

skills/code-reviewer.md §起動方式によれば、本スキルは Agent ツールでのサブエージェント実行を必須とし、メインエージェントによるインライン実行時は `docs/knowledges/tools/004-1_code_review_findings_log.md` への追記禁止。本環境では Agent ツールが利用可能 deferred tool 一覧に存在しないため、メインエージェントとして直接実行している。よって 004-1 への追記は本レビューでは行わない。次回 Agent ツール利用可能環境で本レビューの主要指摘を 004-1 に追記することを推奨する（タグ候補: `gemini-removal`, `dockerfile-sync-miss`, `dead-symbol-reference`, `error-swallowed-by-bare-except`, `plan-declaration-vs-impl-gap`）。

---

## 返却 2026-05-21

- #1 (Dockerfile pymupdf 欠落): [採用] — Dockerfile に PyMuPDF 追加 + ImportError silent 化解消
- #2 (Pass 2 error で受注ラベル消失): [採用] — `_merge_gemma_pass2` で `p2.get("error")` 判定追加、error 時は Pass 1 維持
- #3 (削除済み関数 / GEMINI_LOCATION 参照残存): [採用] — 旧 full/submit/resume モードを早期エラー終了化、`_get_genai_client` 削除
- #4 (Pass 2 JSON parse silent): [採用] — `_load_gemma_pass2_results` で parse_errors を WARN ログ化
- 改善 `phase4_chunk_and_embed` 引数半端: [見送り: 動作同等・後続改修者の判断に委ねる]
- 改善 `fitz.Document.close()` 漏れ: [採用] — try/finally で close 追加
- 改善 dead code (`_extract_text_pypdf2` / `PyPDF2`): [採用] — 関数本体 + import + Dockerfile 行削除
- 改善 `DocInfo.needs_vision` 残存: [見送り: 過去 state.json との互換性のため温存]
- 改善 画像PDF doc が TPU Pass 1 に投入: [採用] — state.json 投入時に `text=""` doc を除外
- 改善 `_PASS2_CATEGORIES` 同期機械検証: [見送り: コメント明記で十分]
- 改善 コミットメッセージ pyproject.toml 主張矛盾: [見送り: 実害なし]

# 作業計画: Gemini廃止 → Gemma専用パイプライン化（PDF抽出換装 + 2-pass処理）

**作成日時**: 2026-05-20 22:05 (JST)
**ステータス**: 完了（2026-05-21 smoke test + Cloud Build デプロイ済み）
**分類**: (b) 継続改修型
**親知見 MD**: `docs/knowledges/tools/013_tdnet_load.md`
**関連アイディアID**: -

---

## 目的

1. Gemini Flash Batch（Phase 3分析）を廃止し、**Gemma 2-pass処理**（Pass 2 = Geminiプロンプト移植）に置き換えてコスト削減する。
2. PDFテキスト抽出を **PyPDF2/pdfminer → PyMuPDF（fitz）** に換装し、抽出精度・速度を向上する。

---

## 背景・動機

### 現行アーキ（変更前）

```
ai-prepare:
  Phase 1: PyPDF2 → pdfminer フォールバック でテキスト抽出
  Phase 2: Gemini Vision Batch OCR（画像PDF分のみ）
  → state.json 保存

ai-finalize:
  Gemma Pass 1結果適用（全件）
  Gemini Flash Batch（決算短信のみ受注判定）
  マージ: 決算短信の「受注高/受注残高」のみGeminiで上書き
  Embedding + BQ insert
```

### 改修後アーキ（変更後）

```
ai-prepare:
  Phase 1: PyMuPDF（fitz）でテキスト抽出（total_chars < 300 → pdfminer フォールバック）
  Phase 2: Vision OCR 廃止（画像PDF は text="" で AI処理スキップ）
  → state.json 保存

ai-finalize:
  Gemma Pass 1結果適用（全件）
  Gemma Pass 2結果読み込み（決算短信 + 決算説明資料）
  マージ: 「受注高/受注残高」を Pass 2 Gemma で上書き（対象拡大: 決算短信 → +決算説明資料）
  Embedding + BQ insert
```

### 変更の動機

- Gemini Flash Batch は月次 ~$0.5/週のコスト要因（`013-1` 参照）
- PyMuPDF は本プロジェクトのベストソリューション（`skills/orders_soldier.md` Step 4-B）として実証済み

---

## 設計判断・トレードオフ（PoC履歴との関係）

### 判断 1: Gemma 過剰検知問題を承知のうえで Gemini 廃止に進む

**前提となる過去PoC知見** (`013-1_ai_cost_and_gemma_poc.md`):
- 「Gemma は受注高/受注残高を過剰検知する → ハイブリッド（決算短信受注のみ Gemini）」
- 「v2（+ 受注ルール）はサンプル正解率 40.0% で逆効果 → 不採用」

**本プランの判断**:
- 上記 PoC 結論を**承知の上**で Gemini 完全廃止に進む（採用ROI が見合わないため）
- Gemma 過剰検知の補正は**本プラン外の別タスクで他手段でカバー**する（事後フィルタ・キーワードルール後段適用・受注専用軽量モデル等を別途検討）
- Pass 2 Gemma vs Gemini の精度比較 spike は**実施しない**（時間コスト > 期待便益と判断）

**承認**: ユーザー意思決定（2026-05-20）。トレードオフを明示的に受容。

**継続レビュー対策**: 本判断は code-reviewer / md-reviewer が将来も繰り返し指摘する可能性が高い。再指摘抑止のため、本セクションと同等の内容を `013_tdnet_load.md` の Phase I サマリ近辺に**判断履歴として恒久記録**する（PHASE E-7 で実施）。

---

## 作業ステップ

### PHASE A: gemma_tpu_worker.py — Pass 2 プロンプト追加・2-pass処理実装

1. - [x] **A-1**: `build_prompt_pass2(doc_title: str, text: str) -> str` 追加
   - 内容: `tdnet_load_parallel.py` の `_build_merged_prompt()` を**一言一句そのまま**移植
   - 出力形式: `{"is_monthly": true, "sub_categories": ["カテゴリ1", ...]}`
   - `TEXT_LIMIT` は Pass 1 と同じ定数を使用
   - `_PASS2_CATEGORIES: set[str] = {"決算短信", "決算説明資料"}` を定数として追加
   - **改善#1 対応**: `tdnet_load_parallel.py` 側にも同名定数 `_PASS2_CATEGORIES` を定義する（C-3 参照）。両ファイル独立スクリプトのため import 経路は作らない代わりに、**`gemma_tpu_worker.py` 冒頭コメントで「`tdnet_load_parallel.py` の `_PASS2_CATEGORIES` と一致させること（片方修正時は両方同期義務）」を明記**して同期忘れを防ぐ

2. - [x] **A-2**: `main()` に Pass 2 処理を追加
   - Pass 1（全件 `build_prompt()`）完了後に実行
   - 対象: `state.json` の各 doc の `pre_main_category`（state に保存済み）が `_PASS2_CATEGORIES` に属するもの
   - 結果を `gs://{BUCKET}/ai_job/{RUN_ID}/gemma_pass2_CURRENT.jsonl` に書き出し（Pass 1の `gemma_CURRENT.jsonl` と独立ファイル）
   - `GcsPusher` 相当のpreemption耐性（定期GCS push + resume）を Pass 2にも実装
   - **`_SUCCESS` ファイルの作成・Callback送信はPass 2完了後**（Pass 1完了時点では行わない）

3. - [x] **A-3**: Pass 2 resume 処理追加
   - `RESUME_RUN_ID` がある場合、`gemma_pass2_CURRENT.jsonl` もresume対象（Pass 1と対称に）
   - `gemma_pass2_CURRENT.jsonl` の既存行数を読んでスキップ
   - **改善#2 対応**: Pass 1 resume と Pass 2 resume は**それぞれ独立して動作可能**であること（典型ケース: Pass 1 完了済み全件 + Pass 2 途中で preempt → 再起動で Pass 1 スキップ・Pass 2 のみ resume）
   - PHASE E に「Pass 2 途中での preempt + 再起動シナリオの smoke test」を任意追加（時間あれば実施）

---

### PHASE B: tdnet_load_parallel.py — テキスト抽出換装

4. - [x] **B-1**: `_extract_text_pymupdf(pdf_bytes: bytes) -> tuple[str, int]` 追加

   ```python
   def _extract_text_pymupdf(pdf_bytes: bytes) -> tuple[str, int]:
       """PyMuPDF でテキスト抽出。戻り値: (text, page_count)。
       
       [PAGE N] マーカーを維持（BQ CHUNK_TEXT との互換性保持）。
       fitz.open 失敗時は ("", 0) を返す（呼び出し側でフォールバック）。
       T-6対応: 各ページテキストに `_normalize_page_text()` を必ず通す
         （サロゲートペア除去 + 連続空白圧縮 + 空行圧縮）。
         過去事故 2026-04-25 (`tdnet-ai-prepare-28cd5`) の再発防止。
       T-7対応: content_length は呼出し側で `_content_length()` 判定（マーカー除去後）。
       """
       import fitz  # noqa: PLC0415
       try:
           doc = fitz.open(stream=pdf_bytes, filetype="pdf")
           page_count = len(doc)
           buf: list[str] = []
           for i, page in enumerate(doc, 1):
               text = page.get_text("text")
               text = _normalize_page_text(text)  # ★ T-6: サロゲート除去含む正規化を必ず経由
               buf.append(f"[PAGE {i}]\n{text}\n")
           return "\n".join(buf), page_count
       except Exception:
           return "", 0
   ```

   - `import fitz` は関数内ローカルインポート（既存 `from pdfminer...` と同様のパターン）
   - **重要 (T-6)**: `_normalize_page_text()` 経由は必須。既存 `_extract_text_pypdf2` / `_extract_text_pdfminer` と同じ正規化経路に揃え、`_save_ai_prepare_state` の UnicodeEncodeError 再発を防ぐ

5. - [x] **B-2**: テキスト抽出フロー更新（`phase1_scan_and_extract()` / `phase1_extract_for_docs()` の呼び出し箇所）

   現行フロー:
   ```
   PyPDF2 → content_length < 300 → pdfminer → content_length < 300 → needs_vision=True
   ```
   改修後フロー:
   ```
   PyMuPDF → content_length < 300 → pdfminer（フォールバック） → content_length < 300 → text=""（Vision送信しない）
   ```
   - `needs_vision = True` への設定コードを `needs_vision = False`（固定）に変更
   - `phase2_vision_batch()` 呼び出し箇所を削除（**関数本体も削除**、B-4 参照）

5-bis. - [x] **B-2b**: 画像PDF（text=""）の `AI_STATUS` 滞留対策（レビュー指摘 #5）

   **問題**: Vision OCR 廃止後、画像PDF doc は `text=""` のまま AI処理スキップになる。現行 `_update_ai_status(doc_ids_with_text, "pending_gemma")` は **text あり doc のみ pending_gemma 化**するため、画像PDF doc は `pending` のまま永続残置 → 月次回収パイプラインに滞留が蓄積する。

   **対応**:
   - **(a) load モード時**: 画像PDF判定が確定したdocは BQ INSERT 時に `AI_STATUS='skipped_image_pdf'`（新ステータス）でロード。後続 ai-prepare で pick up しない
   - **(b) ai-prepare モード時**: PyMuPDF/pdfminer 両者で text="" になった doc は `_update_ai_status([...], "skipped_image_pdf")` で明示遷移し、`pending` 残置を防止
   - **代替案（許容）**: 上記が実装負担大なら、最低限 PHASE E に **AI_STATUS=pending 滞留検知 SQL を運用ガード**として残す
     ```sql
     SELECT COUNT(*) FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
     WHERE AI_STATUS='pending' AND SUBMISSION_DATE >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
     ```
   - **実装判断**: 本プラン実装時に (a)+(b) と「滞留検知SQLのみ」を比較し、トレードオフを記録して選択する

6. - [x] **B-3**: `pyproject.toml` に `pymupdf>=1.27.2` 追加

   ```toml
   [tool.uv.sources]
   # pymupdf は fitz として import する
   pymupdf = { version = ">=1.27.2" }
   ```

   > **注意**: pdfplumber は今回追加しない。Phase 1 の目的は全文テキスト取得であり、orders-soldier のようなキーワードヒットページ限定の表抽出は不要（Gemma が全文を解析するため）。将来の抽出精度改善タスクで追加を検討する。

---

### PHASE C: tdnet_load_parallel.py — Gemini Phase 3廃止・ai-finalize更新

7. - [x] **C-1**: `_load_gemma_pass2_results(bucket, run_id: str) -> dict[str, dict]` 追加

   ```python
   def _load_gemma_pass2_results(bucket, run_id: str) -> dict[str, dict]:
       """Gemma Pass 2 結果（gemma_pass2_CURRENT.jsonl）を読み込む。
       
       Pass 2 対象外 doc（決算短信・決算説明資料以外）は空 dict を返す。
       G-1対応: blob.open("r") で行単位ストリーム読み。
       """
       path = f"ai_job/{run_id}/gemma_pass2_CURRENT.jsonl"
       blob = bucket.blob(path)
       if not blob.exists():
           return {}
       # _load_gemma_results() と同じ実装パターン
       ...
   ```

8. - [x] **C-2**: `phase_gemini_tanshin_batch()` を削除し、ai-finalize フローを更新

   変更前（ai-finalize フロー）:
   ```python
   _apply_gemma_results(docs, gemma_results, logger)   # Pass 1
   phase_gemini_tanshin_batch(docs, bucket, genai_client, logger)  # Gemini
   _merge_gemini_juchu(docs, logger)
   ```

   変更後:
   ```python
   _apply_gemma_results(docs, gemma_results, logger)    # Pass 1（変更なし）
   pass2_results = _load_gemma_pass2_results(bucket, run_id)  # Pass 2読み込み
   _merge_gemma_pass2(docs, pass2_results, logger)      # Pass 2マージ
   ```

9. - [x] **C-3**: `_merge_gemini_juchu()` を `_merge_gemma_pass2()` に改修

   **`_NEEDS_SUB_CATEGORIES` 定数との関係（改善#3 対応）**:
   - 既存 `_NEEDS_SUB_CATEGORIES = {"決算短信", "決算説明資料"}` (L244) は Phase 3 旧経路でのみ使用されている → **C-4 の Gemini 剥離と同時に削除**
   - 新規 `_PASS2_CATEGORIES = {"決算短信", "決算説明資料"}` がその役割を引き継ぐ（実体は同じだが意味論的に Pass 2 対象を明示）

   ```python
   _PASS2_CATEGORIES: set[str] = {"決算短信", "決算説明資料"}
   
   def _merge_gemma_pass2(
       docs: list[DocInfo],
       pass2_results: dict[str, dict],
       logger: BatchLogger,
   ) -> None:
       """Gemma Pass 2 の受注判定を Pass 1 の SUB に差分適用する。
       
       Pass 2 対象: 決算短信 + 決算説明資料（従来は決算短信のみ）
       マージ内容: 「受注高/受注残高」のみ（他のSUBはPass 1 Gemmaを維持）
       Pass 2 結果なし: Pass 1結果をそのまま使用（safe fallback）
       """
       merged_cnt = 0
       no_pass2_cnt = 0
       for doc in docs:
           gemma_sub = set(doc.sub_categories_gemma or [])
           if doc.main_category in _PASS2_CATEGORIES:
               p2 = pass2_results.get(doc.doc_id, {})
               if p2:
                   pass2_sub = set(p2.get("sub_categories", []))
                   if "受注高/受注残高" in pass2_sub:
                       gemma_sub.add("受注高/受注残高")
                   else:
                       gemma_sub.discard("受注高/受注残高")
                   merged_cnt += 1
               else:
                   no_pass2_cnt += 1  # fallback: Pass 1のまま
           doc.sub_categories = sorted(gemma_sub)
       logger.log(
           f"Pass2マージ完了: {_PASS2_CATEGORIES} 対象 {merged_cnt}件マージ / "
           f"Pass2結果なし(fallback) {no_pass2_cnt}件"
       )
   ```

10. - [x] **C-4**: Geminiクライアント関連コードを削除・整理

    **削除対象（コード本体）**:
    - `GEMINI_MODEL` 定数（L75）、`LOCATION_GEMINI` 定数（L101、Embedding 用と分離されている場合のみ）
    - `_phase3_submit()`, `_phase3_poll_and_apply()`, `_phase3_poll_and_apply_legacy()`, `_compute_phase3_chunks()`, `PHASE3_PARALLEL`, `PHASE3_MIN_PER_CHUNK`, `_build_merged_prompt()`
    - `_NEEDS_GEMINI_ANALYSIS` 定数（L245-249）

    **削除波及（レビュー指摘 #3 対応、必須）**:
    - `DocInfo` データクラスから `needs_analysis: bool` / `analysis_model: str` フィールド削除（L272-273）
    - `phase1_scan_and_extract` L649-650 の `needs_analysis = main_category in _NEEDS_GEMINI_ANALYSIS` / `analysis_model = GEMINI_MODEL` 2 行削除
    - `phase1_scan_and_extract` L667-668 の DocInfo 構築引数から `needs_analysis` / `analysis_model` を削除
    - `phase1_scan_and_extract` L674 の `logger.log(f"Gemini 分析必要: ... 件")` 削除
    - **作業前漏れチェック**: `grep -n "needs_analysis\|analysis_model\|_NEEDS_GEMINI_ANALYSIS\|GEMINI_MODEL" scripts/tdnet_load_parallel.py` で残存参照0を確認
    - `full / submit / resume` モード（`tdnet_load_parallel.py:2382-2470`）の Phase 3 経路: 旧 `tdnet-load-parallel` / `tdnet-load-recovery` は段階的廃止方針（013 知見MD既載）に基づき**今回プランで一気に剥がす**。該当モードはエラー終了させる

    **維持**:
    - `genai.Client` は Embedding Batch で引き続き使用するため `google-genai` 依存自体は維持
    - Embedding 用の Vertex AI クライアントは `LOCATION_GEMINI` (us-central1) が必要なら維持

    **`phase2_vision_batch()` の扱い（レビュー指摘 #7 対応）**:
    - **関数本体ごと削除**（旧プランの「DEPRECATEDマーク3ヶ月保持」案は破棄）
    - ロールバックは `git revert` で前コミット復元すれば関数復活する。物理的保持の機械的価値なし
    - 呼出箇所（L2116 ai-prepare / L2446 full モード）も同時削除

---

### PHASE D: ai_processing_flow.yaml — Gemini Batch ステップ削除

11. - [x] **D-1**: `ai_processing_flow.yaml` から Phase 3 Gemini関連ステップを削除

    削除対象:
    - Gemini Batch submit ステップ（`phase3_submit_call` 相当）
    - Gemini Batch poll ステップ
    - ai-finalize への `gemini_job_id` パラメータ渡し

    ai-finalize への引数変更: `run_id` のみで Pass 2結果（GCS上の `gemma_pass2_CURRENT.jsonl`）を参照するよう変更。

    **Pass 2追加の timeout 影響評価（レビュー指摘 #4 対応）**:
    - 現行 `tdnet-gemma-runner` connector_params.timeout = 14400s (4h)、max_retries = 7
    - Pass 2 対象は決算短信＋決算説明資料 ≈ 全 doc の 20-30%（ai-prepare 後の doc 数ベース）
    - Pass 2 追加 TPU 稼働時間増分: **+20-30% 程度**と試算（Pass 1 と並列ではなく直列）
    - **判断**: 14400s は変更不要。PHASE E-5 dev 実機テストで実測時間を計測し、**+50% 超なら timeout を 21600s (6h) に拡大**
    - バックフィル時（決算短信比率高、決算繁忙期）はさらに +10〜20% 上振れ余地あり → 014（バックフィル監視）側で実測値モニタする

---

### PHASE E: ビルド・デプロイ・検証

12. - [x] **E-1**: `python -m py_compile` (T-1 必須)

    ```bash
    PYTHONUTF8=1 python -m py_compile scripts/tdnet_load_parallel.py scripts/gemma_tpu_worker.py
    ```

13. - [x] **E-2**: smoke test — load モード（2026-05-20: `DATE_FROM=DATE_TO=20260520`）

    実施結果（2026-05-21）:
    - job exit(0) ✓
    - 247件全て既存BQレコードのため正常スキップ（デデュプ動作確認）
    - PyMuPDF抽出は E-3 の ai-prepare で `extract_method: "pymupdf"` 確認済み

14. - [x] **E-3**: smoke test — ai-prepare（2026-02-13: 12件）

    実施結果（2026-05-21）:
    - state.json に 2件のテキスト格納、`extract_method: "pymupdf"` ✓
    - 10件の画像PDF → `skipped_image_pdf` に AI_STATUS 遷移 ✓
    - Phase 2 (Vision OCR) 廃止済み → スキップ ✓
    - state.json から画像PDF 10件除外（2docs のみ保存）✓
    - run_id: `93844c32-9106-498c-97fb-eb24547a3353`

15. - [x] **E-4**: smoke test — ai-finalize（mock Gemma results で実施）

    実施結果（2026-05-21）:
    - Gemma Pass 1 適用: 2/2件 ✓
    - **Pass 2 マージ: 1件マージ、追加1/削除0/error0/fallback0** ✓
    - SUB_CATEGORIES に `受注高/受注残高` 追加（決算説明資料 5301東海カーボン）✓
    - Embedding Batch: JOB_STATE_SUCCEEDED (1チャンク) ✓
    - BQ Insert: 成功2/スキップ0/エラー0 ✓
    - pending_* DELETE: 2行削除 ✓
    - GCS cleanup: 4 blob 削除 ✓
    - BQ確認: AI_STATUS='completed', MAIN/SUB 正確に設定済み ✓

16. - [x] **E-5**: Cloud Build + 3 Job デプロイ（2026-05-21）

    実施結果:
    - Build ID: `c3152a7b-9dfc-44a4-be79-49b15ab73a40` (1分31秒)
    - PyMuPDF>=1.27.2 インストール確認 ✓
    - 3 Job 更新: `tdnet-load-daily`, `tdnet-ai-prepare`, `tdnet-ai-finalize` ✓
    - Image: `us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-load-daily:latest`
      (digest: sha256:37659ac9fee4f0e0cb800c28d7ad7da19fec5663d887233956f7808b465b7ca3)

17. - [x] **E-6**: `013_tdnet_load.md` の現況サマリ・関連リソース表を更新（PyMuPDF / Pass 2 / Gemini廃止の反映）

18. - [x] **E-7**: `013_tdnet_load.md` に **判断履歴セクション**を恒久追記（レビュー継続抑止）

    **追記目的**: 「Gemma 過剰検知問題を承知のうえ Gemini 廃止」という設計判断を知見MDに残し、将来の code-reviewer / md-reviewer が同じ指摘を繰り返さないようにする（毎回 PoC 矛盾の指摘を受けないため）。

    **追記内容**:
    - セクション名: `## 設計判断履歴: Gemini Flash 廃止 (2026-05-20)` （013_tdnet_load.md の Phase I サマリ近辺に追加）
    - 本文（要点）:
      - 当時の判断: コスト ROI（Gemini 月 $0.5/週相当） vs Gemma 過剰検知補正コストの比較で、Gemini 廃止 + 別タスクでの補正カバーを選択
      - PoC（`013-1`）との関係: Gemma 過剰検知の事実は認識した上での意思決定。事後で精度問題が顕在化したら別タスクで補正
      - 参照: 本プラン `tools-013_gemma_only_pipeline_20260520_220510.md` §設計判断・トレードオフ

19. - [x] **E-8**: `docs/data_catalog/bq_tdnet_documents.md` に **チャンク化仕様 + Vector Index 仕様** を新規セクションとして追記

    **追記目的**: チャンク仕様（chunk_size=400 等）と Vector Index 定義は現状コード docstring と本プラン内にしか存在せず、データカタログを参照する多数のツール・スクリプト（受注抽出・supply_chain 等）から発見不能。データカタログに集約してリンクハブ化する。

    **追記内容 1: チャンク化仕様セクション**
    ```markdown
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
    ```

    **追記内容 2: Vector Index セクション**
    ```markdown
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
    ```

    **既存 CHUNK_TEXT / EMBEDDING カラム説明欄の修正**:
    - CHUNK_TEXT 行: `| CHUNK_TEXT | STRING | チャンクテキスト（分割済みテキスト）。**詳細は §チャンク化仕様 参照**。|`
    - EMBEDDING 行: `| EMBEDDING | ARRAY<FLOAT64> | テキスト埋め込みベクトル（text-embedding-004, 768次元、3カテゴリ限定）。**詳細は §Vector Index 仕様 参照**。|`

---

## 必要データ

| データ | ストレージ層 | パス/テーブル |
|--------|------------|--------------|
| 開示 PDF | (a) GCS | `gs://stock_data_1930932/tdnet/` |
| AI 中間ファイル（Pass 2追加） | (a) GCS | `ai_job/{run_id}/gemma_pass2_CURRENT.jsonl` |
| 書き込み先 | (a) BQ | `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED` |

---

## 成果物

| ファイル | 変更内容 |
|---------|---------|
| `scripts/gemma_tpu_worker.py` | `build_prompt_pass2()` 追加・2-pass処理・Pass 2 GCS出力・`_PASS2_CATEGORIES` 同期コメント |
| `scripts/tdnet_load_parallel.py` | PyMuPDF換装（`_normalize_page_text` 経由）/ Gemini完全剥離（`DocInfo.needs_analysis` 等の波及削除含む）/ `_merge_gemma_pass2()` 追加 / 画像PDF滞留対策 |
| `workflows/ai_processing_flow.yaml` | Gemini Batch ステップ削除 + timeout 実測後拡大判定 |
| `pyproject.toml` | `pymupdf>=1.27.2` 追加 |
| `docs/knowledges/tools/013_tdnet_load.md` | 現況サマリ更新 + **「設計判断履歴: Gemini Flash 廃止」セクション恒久追記**（レビュー継続抑止） |
| `docs/data_catalog/bq_tdnet_documents.md` | **チャンク化仕様** + **Vector Index 仕様** セクション新規追加（多ツール参照のためデータカタログを集約ハブ化） |

---

## 完了条件

1. `tdnet-load-daily`（load / ai-prepare / ai-finalize）が Gemini 不使用で全ステップ完了
2. 1日分 smoke test で AI_STATUS='completed' 件数が旧アーキと同等
3. 決算短信・決算説明資料の `受注高/受注残高` マージが既知受注企業1社で確認できること
4. **Pass 2 効果可視化** (E-4 観測ポイント): `gemma_pass2_CURRENT.jsonl` 行数 ≥ 対象 doc 数 90%、`merged_cnt > 0`、`no_pass2_cnt < merged_cnt × 10%`
5. **画像PDF滞留なし** (B-2b): 14日以内の `AI_STATUS='pending'` 残存件数が旧アーキと同等以下
6. **判断履歴の知見MD反映** (E-7): `013_tdnet_load.md` に「設計判断履歴: Gemini Flash 廃止 (2026-05-20)」セクションが追加されていること
7. **データカタログ集約ハブ化** (E-8): `bq_tdnet_documents.md` に「チャンク化仕様」「Vector Index 仕様」セクションが追加されていること

---

## 見積もり

- 想定所要時間: 4〜6時間（コーディング 2h + smoke test 2h + 本番デプロイ確認 2h）
- 難易度: 中（既存構造的改変・2-pass処理新設・Gemini完全剥離）

---

## 対応アンチパターン

| plan ID | 004 | T-x | G-x |
|---|---|---|---|
| B-1 (PyMuPDF 抽出) | - | T-7 (`_content_length` 判定) | - |
| B-2 (Vision 廃止 + 滞留対策) | A-3 (silent skip 禁止) | T-6 (`_normalize_page_text` 経由) | - |
| A-2 (Pass 2 main 処理) | B-4 (多段 write 順序), A-3 | - | G-1 (CURRENT.jsonl ストリーム), G-2 (部分失敗の明示) |
| C-3 (`_merge_gemma_pass2`) | - | - | - |
| C-4 (Gemini 剥離) | F-1 (deploy 前スモーク) | T-5 (旧 Phase 経路非使用) | - |
| E-1〜E-5 | F-1 | T-1 (Phase 5 触ったらスモーク) | - |

---

## 検証戦略

1. **smoke test（E-2〜E-4）**: 1日分（〜100 doc）で全ステップ通過確認
2. **dev 実機（E-5後）**: 日次パイプライン手動実行1回、AI_STATUS='completed' 件数確認
3. **本番適用判断基準**: smoke + dev 両方 PASS で初めて prod
4. **回収手順**: `git revert` で `gemma_tpu_worker.py` / `tdnet_load_parallel.py` を前コミットに戻し、Workflows 再デプロイ（Pass 2 JSONL は GCS Lifecycle 14日で自動削除）

---

## 関連ドキュメント

- `docs/knowledges/tools/013_tdnet_load.md` — 親知見MD（本プランのバックリンク先）
- `docs/knowledges/tools/078_gemma4_operation.md` — Gemma TPU / vLLM 操作手順
- `docs/knowledges/tools/080_workflows_runbook.md` — Cloud Workflows
- `skills/orders_soldier.md` §Step 4-B — PyMuPDF + pdfplumber ハイブリッドの先行実装

---

## 振り返り（作業後に記入）

- 実際の所要時間:
- うまくいった点:
- 改善点:
- 得られた知見:

---

## レビュー追記: 2026-05-20 23:10 JST — code-reviewer

→ `docs/reviews/218_cr_gemma_only_pipeline_plan.md`

---

## レビュー追記: 2026-05-21 06:30 JST — code-reviewer（実装レビュー、コミット 92d5113d）

→ `docs/reviews/219_cr_gemma_only_pipeline_impl.md`

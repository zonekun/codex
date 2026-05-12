# コードレビュー: TDnet 抽出失敗ドキュメント リカバリスクリプト（Vision Batch 追加後・全体）

- 日時: 2026-05-06 15:30 JST
- 対象: `scripts/tmp_reextract_failed_docs.py`（707行、CR-084 指摘 #1-#5 修正後 + Step 3 Vision Batch OCR 追加）
- パターン: 1 (新規)
- レビュアー: Claude (code-reviewer runbook)
- 前回レビュー: `docs/reviews/084_cr_reextract_failed_docs.md`

---

## 【サマリー】

- 変更の要約: CR-084 の 5 件の重大指摘（SQL injection、DELETE→INSERT 非原子性、カラム保持、尺度統一、AI_STATUS 整合性）を修正し、MERGE 方式からトランザクション DELETE→INSERT 方式に変更。加えて Step 3 として Gemini Vision Batch OCR を追加し、PyPDF2/pdfminer 両方失敗した DOC を OCR で回収するパイプラインを完成
- 品質評価: **A** — CR-084 の重大指摘 5 件はすべて適切に修正されている。Vision Batch 処理は本家 `tdnet_load_parallel.py` の `phase2_vision_batch()` と高い忠実度で実装。残存する指摘は重大度が低い
- 主要リスク:
  1. トランザクション内 INNER JOIN のデータ整合性リスク（DELETE 後に同テーブルから JOIN）
  2. ポーリングに deadline がない（D-1 違反）
  3. INSERT カラムの不足（FILER_NAME、DISCLOSURE_TIME、PAGE_COUNT、EXTRACTED_AT 等）

---

## 【前回レビュー（CR-084）指摘 Regression チェック】

### CR-084 #1: SQL injection → MERGE 方式 → **修正済み**

CR-084 では `doc_id` を f-string で直接 SQL に埋め込んでいた。現在は一時テーブル + `DELETE ... WHERE DOC_ID IN (SELECT DOC_ID FROM _tmp_table)` に変更されており、ユーザー入力が SQL に直接混入する経路は消滅。**解消確認済み**。

### CR-084 #2: DELETE→INSERT 非原子性 → トランザクション化 → **修正済み**

L566-593 で `BEGIN TRANSACTION; DELETE ...; INSERT ...; COMMIT TRANSACTION;` が単一の multi-statement クエリとして発行されている。DELETE と INSERT が同一トランザクション内で実行されるため、INSERT 失敗時は DELETE もロールバックされる。**解消確認済み**。

### CR-084 #3: INSERT 行のカラム不整合 → INNER JOIN で元カラム保持 → **部分修正**

L585-590 で INNER JOIN を使い元テーブル `T_orig` から `TICKER`, `SUBMISSION_DATE`, `FILE_NAME`, `DOC_TITLE` を引き継いでいる。ただし SELECT するカラムが限定的で、元テーブルの全カラム（後述 #2 で詳細）が保持されていない。**方向性は正しいが不完全**。

### CR-084 #4: `new_text_length` の尺度不整合 → **修正済み**

L279 `doc.new_text_length = len(text)` (成功時)、L283 `doc.new_text_length = len(text)` (失敗時) — 両方とも `len(text)` で統一されている。**解消確認済み**。

### CR-084 #5: `AI_STATUS` と `MAIN_CATEGORY` の整合性 → **修正済み**

L583 で `AI_STATUS = 'pending'` を設定し、L582 で `SECTION_CATEGORY` に `T_orig.DOC_TITLE` を設定している。元の `MAIN_CATEGORY` は INSERT カラムに含まれず NULL になるため、AI パイプラインが `pending` から再判定する設計。テキスト変更後の中間状態で旧カテゴリが残る問題は解消。**解消確認済み**。

---

## 【重大な指摘】（即修正）

### #1 トランザクション内 INNER JOIN が DELETE 後のテーブルを参照する — 結果 0 行の可能性

- 箇所: `scripts/tmp_reextract_failed_docs.py:566-593`
- 事象: トランザクション内で以下の順序で実行される:
  1. `DELETE FROM TABLE WHERE DOC_ID IN (SELECT DOC_ID FROM _tmp_table)` — 該当行を削除
  2. `INSERT INTO TABLE SELECT ... FROM _tmp_table INNER JOIN (SELECT ... FROM TABLE QUALIFY ...) AS T_orig` — **削除済みの TABLE** から元データを JOIN で取得しようとする

  BQ のトランザクションは READ COMMITTED 分離レベルであり、同一トランザクション内の先行 DML の結果は後続ステートメントから見える。つまり DELETE 後の TABLE は対象 DOC_ID の行が存在しないため、INNER JOIN の結果は 0 行になる。**INSERT が何も挿入しない**。
- トリガー: 全件で発生（トランザクション内の DELETE→INSERT パターン自体の構造的問題）
- 影響: 2,100 件の DOC が DELETE されるだけで INSERT されず、全件データロスト。トランザクション自体は成功するため（0 行 INSERT はエラーにならない）、ログ上は「トランザクション DELETE → INSERT 完了」と報告される
- 根拠: BQ multi-statement トランザクションの READ COMMITTED セマンティクス（https://cloud.google.com/bigquery/docs/multi-statement-queries#transactions）。DELETE の効果は同一トランザクション内の後続ステートメントから可視
- 推奨対応: 以下のいずれか:
  - **方式 A: MERGE 文に変更**（最もシンプル）: DELETE を省き、`MERGE INTO TABLE USING _tmp_table WHEN MATCHED THEN UPDATE SET TEXT_LENGTH = S.TEXT_LENGTH, AI_STATUS = 'pending', MAIN_CATEGORY = NULL, ...`
  - **方式 B: INSERT 先行 → DELETE 後行**: INSERT を先に実行し、成功後に DELETE で古い行を消す。ただし同一 DOC_ID が 2 行存在する中間状態が生まれるため PK 重複が問題ならば方式 A が安全
  - **方式 C: DELETE 前に元データを _tmp_orig に退避**: 追加の一時テーブルに元データを保存してから DELETE → INSERT で _tmp_orig から JOIN

### #2 INSERT カラムの不足 — 元データの多数カラムが消失

- 箇所: `scripts/tmp_reextract_failed_docs.py:572-583`
- 事象: INSERT 対象カラムが `DOC_ID, TICKER, SUBMISSION_DATE, FILE_NAME, DOC_TITLE, TEXT_LENGTH, SECTION_CATEGORY, AI_STATUS` の 8 列のみ。`data_catalog.md` の DDL によるとテーブルには以下の追加カラムがあり、すべて NULL/空になる:
  - `FILER_NAME` (STRING) — 提出者名。NULL で消失
  - `FILER_ID` (STRING) — TDnet では元々 NULL なので問題なし
  - `DISCLOSURE_TIME` (STRING) — 開示時刻。NULL で消失
  - `MAIN_CATEGORY` (STRING) — CR-084 #5 修正で意図的に NULL（pending に戻すため）。これは設計意図通り
  - `SUB_CATEGORIES` (ARRAY<STRING>) — AI判定サブカテゴリ。NULL で消失（pending なので正しい）
  - `PAGE_COUNT` (INT64) — ページ数。NULL で消失。`_extract_text_pypdf2` が page_count を返しているが未使用
  - `CHUNK_TEXT` (STRING) — チャンクテキスト。NULL で消失。本家では DOC 全文を CHUNK_TEXT に格納する行も INSERT する
  - `EMBEDDING` (ARRAY<FLOAT64>) — 768次元ベクトル。NULL で消失（pending なので正しい）
  - `EXTRACTED_AT` (TIMESTAMP, DEFAULT CURRENT_TIMESTAMP()) — DDL の DEFAULT が効くので問題なし
- トリガー: 全件の INSERT で発生
- 影響: `FILER_NAME` と `DISCLOSURE_TIME` は AI パイプライン非依存のメタデータであり、pending 再判定でも復元されない。特に `DISCLOSURE_TIME` は下流の `EARNINGS_DISCLOSURE_CALENDAR` ETL で使用される。`PAGE_COUNT` も消失する。一時スクリプトかつ対象が TEXT_LENGTH < 300 の DOC（おそらく AI 判定前が多い）ため実害は限定的だが、修正コストは低い
- 推奨対応: INNER JOIN のサブクエリ（L586-589）の SELECT に `FILER_NAME`, `DISCLOSURE_TIME`, `PAGE_COUNT` を追加し、INSERT カラムリストにも追加する。あるいは #1 の MERGE 方式なら UPDATE SET で変更カラムのみ指定すればこの問題は自動解消

### #3 ポーリングに deadline がない — D-1 違反

- 箇所: `scripts/tmp_reextract_failed_docs.py:338-357`
- 事象: `_poll_batch_job()` が `while True` で無限ループし、Gemini Batch API がハング状態（終局状態に遷移しない）の場合、スクリプトが永久に停止する
- トリガー: Gemini Batch API の内部障害、ネットワーク断、未知の中間状態（`RUNNING` 等が `state.name` に該当しない文字列で返る場合）
- 影響: スクリプトが無限ハング。ローカル実行なら Ctrl+C で止められるが、Cloud Run Job で実行した場合はタイムアウトまで課金が続く
- 根拠: 004 D-1「polling 無限ループに timeout がない → deadline = time.time() + max_wait_sec で break」
- 推奨対応: `_poll_batch_job()` に `max_wait_sec` パラメータを追加し、`deadline = time.time() + max_wait_sec` でループを打ち切る。本家 `tdnet_load_parallel.py:687-703` も同じ問題を持っているが（本家のリスクは許容されている状態）、新規コードでは対策すべき。推奨値: 7200 秒（2時間）

---

## 【改善提案】（可読性・保守性）

### #1 `SECTION_CATEGORY` に `DOC_TITLE` を設定している意図が不明

- 箇所: `scripts/tmp_reextract_failed_docs.py:574,582`
- 現状: INSERT カラムの `SECTION_CATEGORY` に `T_orig.DOC_TITLE` を設定している。`SECTION_CATEGORY` はチャンク単位の分類（本家では `"full_text"` や `"summary"` 等の値が入る）であり、`DOC_TITLE` の値を入れるのは意味的に不整合
- 提案: `SECTION_CATEGORY` を `NULL` にするか、`'full_text'` のようなリテラル値を設定する。あるいは #1 の MERGE 方式にして変更不要カラムは触らない

### #2 `new_text_length` の設定が失敗時に `len(text)` — Vision 前のテキスト長

- 箇所: `scripts/tmp_reextract_failed_docs.py:283`
- 現状: PyPDF2/pdfminer 両方失敗時に `doc.new_text_length = len(text)` が設定される。この値は Vision OCR 成功後に L466-469 で上書きされるため最終的な BQ 書き込みには影響しない。しかし Vision 前のサマリ出力時にはこの中間値が表示される可能性がある
- 提案: 現状で実害はないが、可読性のために失敗時は `doc.new_text_length = 0` に設定するとより明確

### #3 ロギングに `logging` を使用 — CLAUDE.md 規約では `structlog`

- 箇所: `scripts/tmp_reextract_failed_docs.py:18,64-69`
- 現状: 標準 `logging` モジュールを使用。CLAUDE.md §コーディング規約に「print禁止。structlogを使用」と明記されている
- 提案: 一時スクリプト（tmp_）の短命性を考慮すると severity は低い。ただし本家 `tdnet_load_parallel.py` も `BatchLogger` で structlog ではなく独自ロガーを使用しているため、プロジェクト全体で統一できていない現状。修正は任意

### #4 Vision Batch で 2,100 件を 1 バッチで投入 — 本家は並列分割

- 箇所: `scripts/tmp_reextract_failed_docs.py:382-483`
- 現状: Vision OCR 対象を 1 つのバッチジョブで全件投入している。本家 `tdnet_load_parallel.py` では Phase 3（Analysis Batch）で `PHASE3_PARALLEL = 5` の並列分割を行っているが、Phase 2（Vision Batch）は 1 バッチ。再抽出スクリプトの Vision 対象は PyPDF2/pdfminer 両方失敗分のみなので件数は限定的（おそらく数十〜数百件）であり、1 バッチで問題ない
- 提案: 現状維持で問題なし。件数が 1,000 件を超える場合は並列分割を検討

### #5 `--skip-vision` と `--dry-run` の組み合わせ動作

- 箇所: `scripts/tmp_reextract_failed_docs.py:690-697`
- 現状: `--skip-vision --dry-run` を指定すると、PyPDF2/pdfminer 再抽出のみ実行し、Vision はスキップ、BQ 更新もスキップ。動作として自然で問題ない。`--skip-vision` のみ（dry-run なし）だと PyPDF2/pdfminer 成功分のみ BQ 更新。これも意図通り
- 提案: 現状の設計で問題なし

### #6 `errors` カウントが Vision 失敗を反映しない

- 箇所: `scripts/tmp_reextract_failed_docs.py:694-695`
- 現状: `vision_batch_ocr()` は成功件数（`vision_ok`）を返すが、Vision 失敗件数（`vision_parse_fail` + `vision_no_result`）はログ出力のみ。`main()` の `errors` カウントに反映されない。L702 で `errors > 0` なら `sys.exit(1)` だが、Vision 失敗は含まれない
- 提案: Vision のパース失敗・結果欠落は「抽出不可」であって「処理エラー」ではないため、`sys.exit(1)` の対象にしないのは妥当な設計判断。ただしサマリには反映済み（`print_summary()` で `still_failed` として計上）

---

## 【Vision Batch 処理の本家準拠度チェック】

本家 `tdnet_load_parallel.py` の `phase2_vision_batch()` (L730-831) と比較:

| 観点 | 本家 | 再抽出スクリプト | 差異 |
|------|------|-----------------|------|
| JSONL 構築 | `key: vision_{i}`, `fileData` + `text` prompt | 同一構造 | 差異なし |
| プロンプト文言 | `f"以下のPDF文書（タイトル: {doc_title_hint}）..."` | 同一 | 差異なし |
| `genai.types.CreateBatchJobConfig` | `dest` のみ指定 | 同一 | 差異なし |
| ポーリング | `_poll_batch_job()` — 同一ロジック | 同一 | 差異なし（両方 deadline なし） |
| 結果パース | `resp["response"]["candidates"][0]["content"]["parts"][0]["text"]` | 同一 | 差異なし |
| 閾値判定 | `len(text) >= _MIN_TEXT_LEN`（Vision はマーカー非含有） | 同一 | 差異なし |
| 失敗カウント | `vision_ok / vision_parse_fail / vision_no_result / vision_too_short` | 同一 4 指標 | 差異なし |
| WARNING ログ | `★WARNING★ Gemini Vision フォールバック使用` | なし | **差異あり**（画像 PDF 疑いの WARNING を出していない。一時スクリプトでは不要と判断） |
| `doc.extract_method` 設定 | `doc.extract_method = "gemini_vision"` | `doc.new_extract_method = "gemini_vision"` | フィールド名の差異のみ（FailedDoc dataclass の設計差） |

**結論**: 本家との差異は WARNING ログの省略のみ。JSONL 構築・ポーリング・結果パース・閾値判定はすべて本家準拠。Vision はマーカーを含まないため `len(text)` での判定は T-7 ルール準拠。

---

## 【確認できなかった事項】

- BQ の multi-statement トランザクションで DELETE 後に同テーブルを SELECT した場合の正確な可視性（#1）。READ COMMITTED のドキュメント上は「先行 DML の結果は後続ステートメントから見える」だが、実際の挙動は BQ の実装バージョンに依存する可能性がある。確実を期すなら MERGE 方式への変更を推奨
- `TEXT_LENGTH < 300` の DOC のうち `AI_STATUS = 'completed'` がどの程度あるか（カラム消失の実害範囲）
- Vision Batch API が 1 バッチで受け入れ可能な最大リクエスト数（ドキュメント上は 30,000 件まで）
- `_tmp_reextract` 一時テーブルが既に存在した場合の挙動（L549 で `DROP TABLE IF EXISTS` しているため問題ないはず）

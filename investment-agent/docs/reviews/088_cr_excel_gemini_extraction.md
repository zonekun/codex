# コードレビュー: extract_monthly_data.py excel_gemini 新規実装

- 日時: 2026-05-06 21:30 JST
- 対象: `scripts/extract_monthly_data.py` (差分: `extraction_method="excel_gemini"` 関連)
- パターン: 1 (新規)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: XLSX ファイルを pandas で CSV テキスト化し、Gemini API で月次メトリクスを抽出する `excel_gemini` extraction method を追加。同期モード（個人 API キー）とバッチモード（Vertex AI Batch）の両方に対応し、ローカルファイル / GCS blob の両パスをカバー。
- 品質評価: **B** — 既存パターン（`html_gemini`, `gemini`）を踏襲した堅実な実装。ただし一時ファイルリーク経路、CSV非対応、プロンプトのガードレール不足に改善の余地あり。
- 主要リスク:
  1. GCS パスで `blob.download_to_filename()` が例外を投げた場合、`tmp_path` が未定義のまま `finally` ブロックで `os.unlink(tmp_path)` → `NameError` でリークが隠蔽される
  2. `_extract_xlsx_gemini_personal()` は `pd.read_excel()` 固定のため、`format: csv` のアダプターが非バッチモードで来ると空リスト返却（サイレント失敗）
  3. `_build_excel_gemini_prompt()` に集計列・四半期列の除外指示がなく、既存 PDF プロンプトの禁止事項ガードレールが欠落

---

## 【重大な指摘】（即修正）

### #1 GCS パスで tmp_path 未定義時の NameError → 一時ファイルリーク

- 箇所: `scripts/extract_monthly_data.py:3631-3661`
- 事象: `with tempfile.NamedTemporaryFile(...)` ブロック（3632行）で `tmp_path = tmp.name` が設定されるが、もし `blob.download_to_filename(tmp_path)` (3634行) より前に `with` ブロック自体が正常完了しているため `tmp_path` は定義済み。しかし、`with` ブロック内で例外が発生した場合（ディスクフル等）、`tmp_path` が部分的に設定されたまま `finally` の `os.unlink(tmp_path)` (3659行) が実行される。さらに深刻なのは、**`blob.download_to_filename(tmp_path)` は `with` ブロックの外**（3634行）にあるため、ダウンロード失敗時は空の一時ファイルがディスクに残る（`except` で catch されるが `finally` の `os.unlink` は実行される）。この構造自体は動作するが、以下のエッジケースがある: `tmp` の `with` ブロック終了後かつ `blob.download_to_filename` 前に例外が発生するケースは考えにくいが、`pd.read_excel` / `_extract_xlsx_gemini_personal` 内で例外が発生し `except` で catch → `finally` で unlink の流れは正常に動く。
- **実際の問題点の修正**: `with` ブロックの構造を見直すと、`tmp_path` は `with` 内で必ず設定されるため NameError は発生しない。ただし、**`batch_mode=False` かつ `_gemini_xlsx_client=None`（API キー未設定）の場合**、`try` ブロック内でどの分岐にも入らず、一時ファイルが作成・ダウンロードされるだけで何も処理されずに `finally` で削除される。この場合は無駄な GCS ダウンロードとディスク I/O が発生する。
- トリガー: `excel_gemini` method + GCS パス + `GEMINI_API_KEY` 未設定 + 非バッチモード
- 影響: 無駄なネットワーク・ディスク I/O（データ欠損はなし）
- 根拠: 3522-3535行で `_gemini_xlsx_client` は `GEMINI_API_KEY` 未設定時に `None` のまま。3630-3661行で GCS blob をダウンロードして `tmp_path` に保存するが、`batch_mode=False` かつ `_gemini_xlsx_client=None` では 3641行と3654行の両方の条件に入らず、ファイルが使われないまま削除される。
- 推奨対応: GCS ループの入口で `if not batch_mode and not _gemini_xlsx_client: continue` のガードを追加し、無駄なダウンロードをスキップする。ローカルパス側（3578-3606行）は同等のガードが既にある（`elif _gemini_xlsx_client:` で分岐、3601行）が、GCS 側にはこのガードがない。

### #2 _extract_xlsx_gemini_personal が CSV ファイルを処理できない

- 箇所: `scripts/extract_monthly_data.py:1372`
- 事象: `_extract_xlsx_gemini_personal()` は `pd.read_excel(xlsx_path, ...)` を固定で呼ぶ。アダプターの `format` が `csv` の場合でも `pd.read_excel()` が呼ばれ、`ValueError` / `InvalidFileFormatError` が発生して空リスト返却。
- トリガー: `extraction_method="excel_gemini"` + `format="csv"` のアダプター + 非バッチモード
- 影響: サイレントに空リスト返却。ログには `[excel_gemini] ... 読み込み失敗` と出るが、ユーザーにはレコード0件としか見えない。
- 根拠: バッチモード側（3592-3595行）では `if fpath.suffix.lower() == ".csv"` で分岐して `pd.read_csv()` を使っている。非バッチの `_extract_xlsx_gemini_personal()` にはこの分岐がない。
- 推奨対応: `_extract_xlsx_gemini_personal()` の先頭で `xlsx_path.suffix.lower()` を見て `.csv` なら `pd.read_csv(..., header=None, encoding=adapter.get("encoding", "utf-8-sig"))` に分岐する。関数名は `xlsx` を冠しているが、実態は「ファイル→CSV テキスト→Gemini」なので CSV 入力も自然。

### #3 _build_excel_gemini_prompt に集計列・四半期除外のガードレール欠如

- 箇所: `scripts/extract_monthly_data.py:1346-1354`
- 事象: 既存の `_build_extract_prompt()` (442-471行) には「集計列を絶対に取らない」「対象月厳守」「当年/前年の取り違え禁止」「全店/既存店の取り違え禁止」「サブカテゴリ/業態の取り違え禁止」「パーセント値の形式」「年度数字を値として返さない」という7項目の禁止事項が詳細に記述されている。`_build_excel_gemini_prompt()` にはこれらが一切ない。
- トリガー: Excel が四半期累計列・前年比較列・サブカテゴリ行を含む場合（月次開示 Excel ではよくある構造）
- 影響: Gemini が四半期累計値を月次値として返す、前年の値を当年として返す等の誤抽出。本番データが汚染される。
- 根拠: `_build_excel_gemini_prompt` は multi-month 抽出（全月一括）を前提としているため、`_build_extract_prompt` の multi-month 版（425-433行）と比較すべき。multi-month 版にも禁止事項は記述されていないが、PDF の場合は `response_schema` やデータ構造が異なるため影響度が異なる。Excel→CSV テキストでは表構造が失われるため、Gemini が列の意味を誤解するリスクがPDFより高い。
- 推奨対応: `_build_excel_gemini_prompt()` のプロンプトに最低限以下を追加:
  - 「1Q/2Q/3Q/4Q/累計/通期/年度合計/YTD 等の集計行はスキップし、単月の値のみ抽出」
  - 「前年/前期の行は取らない。当年/最新期の値のみ」
  - field の `description` がある場合は「description で指定された行/カテゴリのみ選ぶ」

### #4 batch_meta に `since` キーが欠落（excel_gemini パス）

- 箇所: `scripts/extract_monthly_data.py:3594-3605` (ローカル) / `scripts/extract_monthly_data.py:3644-3648` (GCS)
- 事象: `_batch_meta[_key]` に `"since"` キーが含まれていない。バッチ結果適用時（4112行）の `since=meta.get("since", since)` でフォールバックの外側スコープの `since` 変数が使われるため、現状のコードでは**たまたま正しく動く**。しかし、TDnet パス（3964行）では `"since": since` を明示的に設定している。
- トリガー: 将来のリファクタリングでバッチ結果適用ロジックが別関数に切り出された場合、外側スコープの `since` にアクセスできなくなり `KeyError` または意図しないデフォルト値が使われる。
- 影響: 現時点では正常動作するが、保守性リスク。
- 根拠: TDnet バッチメタ（3964行）では `"since": since` を明示的に設定しているため、設計意図としては `_batch_meta` に `since` を含めるべき。
- 推奨対応: `_batch_meta[_key]` に `"since": since` を追加（ローカル・GCS 両パス）。

---

## 【改善提案】（可読性・保守性）

### #1 custom_prompt キー名の不統一

- 箇所: `scripts/extract_monthly_data.py:1342` vs `scripts/extract_monthly_data.py:423`
- 現状: `_build_excel_gemini_prompt()` は `adapter.get("custom_prompt", "")` を参照し、`_build_extract_prompt()` の multi-month 版は `adapter.get("gemini_custom_prompt", "")` を参照。`_extract_html_gemini_personal()` は `adapter.get("custom_prompt", "")` を参照。アダプターの JSON に `custom_prompt` と `gemini_custom_prompt` の両方が存在する可能性があり、どちらが使われるかが呼び出し元のメソッドに依存する。
- 提案: アダプター JSON のキー名を統一する（`custom_prompt` に一本化するか、`gemini_custom_prompt` に一本化）。暫定対策として `_build_excel_gemini_prompt()` 内で両キーをフォールバックで参照する: `adapter.get("gemini_custom_prompt", "") or adapter.get("custom_prompt", "")`

### #2 _extract_xlsx_gemini_personal のパラメータ名 `xlsx_path` が実態と乖離

- 箇所: `scripts/extract_monthly_data.py:1358`
- 現状: パラメータ名 `xlsx_path` だが、バッチモード側では CSV ファイルも入力される（#2 の指摘と関連）。
- 提案: `file_path` にリネームし、docstring でサポートする形式（`.xlsx`, `.xls`, `.csv`）を明記。

### #3 GCS excel_gemini パスと既存 xlsx パスのコード重複

- 箇所: `scripts/extract_monthly_data.py:3625-3661` (excel_gemini) vs `scripts/extract_monthly_data.py:3662-3671` (既存 xlsx)
- 現状: 両パスとも `tempfile.NamedTemporaryFile` → `blob.download_to_filename` → 処理 → `os.unlink` の同一パターンを個別に実装。一時ファイル管理のロジックが3箇所（PDF, excel_gemini, 既存 xlsx）に散在。
- 提案: 一時ファイルの生成・ダウンロード・削除を `contextmanager` でラップした共通ヘルパを検討。ただし差分が小さいので優先度は低い。

### #4 CSV 切り詰め上限 20000 文字のハードコード

- 箇所: `scripts/extract_monthly_data.py:1353`
- 現状: `csv_text[:20000]` で固定切り詰め。Gemini のコンテキストウィンドウに対する安全マージンとしては妥当だが、巨大な XLSX（数千行）では重要なデータ（最新月のデータが末尾にある場合）が切り捨てられる可能性がある。
- 提案: 定数化して `MAX_CSV_TEXT_LEN = 20000` のように定義。また、巨大ファイルの場合はログに切り詰めた旨を出力すると、デバッグ時に助かる。

### #5 extraction_method のハードコード文字列

- 箇所: `scripts/extract_monthly_data.py:1418-1419`, `1440-1441`, `3518`, `3522`, `4031` 他多数
- 現状: `"excel_gemini"` がリテラル文字列として約15箇所に散在。
- 提案: 現行の `"gemini"` / `"regex"` も同様にリテラルなので本差分固有の問題ではないが、method 種別が3つに増えたタイミングで `Enum` or 定数への集約を検討する余地がある。

---

## 【確認できなかった事項】

- `_call_with_timeout()` 内のスレッドリーク: Gemini API が hung した場合のスレッド leak は既存コード（50-72行）のコメントで「プロセス終了で消える」と記述されている。`excel_gemini` で大量ファイル処理時にスレッドが累積する可能性があるが、`max_workers=4` 制限があるため実害は限定的と推測。ただし実行時の挙動は確認できていない。
- アダプター JSON に `sheet_name` が文字列で指定されたケース（例: `"Sheet2"` ではなく存在しないシート名）でのエラーメッセージの適切さ。`pd.read_excel` は `ValueError` を投げるはずだが、ログの `[excel_gemini] ... 読み込み失敗` メッセージで原因特定が十分かは実行してみないと不明。
- `response.text` が空文字列の場合の挙動（1398行）。`strip()` 後に空文字列 → `re.search(r"\[.*\]", "", re.DOTALL)` → `None` → `re.search(r"\{.*\}", "", re.DOTALL)` → `None` → 空リスト返却。正常に空リストが返るはず。
- `_build_batch_request_obj` に `pdf_gcs_uri` も `full_text` も渡していない（3600行）ため、`parts` は `[{"text": prompt}]` のみ。`_build_excel_gemini_prompt` のプロンプト末尾に `csv_text[:20000]` が含まれているので CSV データは prompt 内に含まれており問題ないが、PDF URI 付きのバッチリクエストとは構造が異なる点は認識しておくべき。
- 既存の `extract_from_xlsx()` との戻り値スキーマの違い。`extract_from_xlsx()` は `year_month` を `"unknown"` で返す場合がある（2668行の TODO コメント）のに対し、`_extract_xlsx_gemini_personal()` は `year_month` が正規表現マッチしなければスキップする。この差異が downstream の重複除去（4050-4054行）に影響しないかは実データで検証が必要。

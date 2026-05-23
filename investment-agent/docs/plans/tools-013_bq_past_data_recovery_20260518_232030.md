# TDNET_DOCUMENTS_ENHANCED: 過去データリカバリ（EXTRACTED_AT / FILER_NAME）

**作成日時**: 2026-05-18 23:20 JST
**ステータス**: 完了（2026-05-20）
**分類**: (b) 継続改修型
**親知見 MD**: `docs/knowledges/tools/013_tdnet_load.md`
**対象テーブル**: `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`

---

## 発端

`scripts/tdnet_load_parallel.py` に `EXTRACTED_AT` の記述が一切なかったこと、および `tdnet-ai-prepare` の `state.json` に `filer_name` が保存されていなかったことにより、過去データに以下2件の不具合が残存している。いずれも 2026-05-18 の修正で新規ロード分は解消済み。本プランは既存データの遡及修正を扱う。

---

## 修正項目

### A-1. EXTRACTED_AT 遡及修正

**現状**: テーブル全件（全パーティション）の `EXTRACTED_AT` が NULL。

**副作用**: `bq_tdnet_documents.md §重複行への注意` の案2（`ORDER BY EXTRACTED_AT DESC` による最新行選択）が機能しない。重複行が発生した際に「どちらの行が新しいか」を判定できない。

**3案の比較:**

| 案 | 内容 | コスト | 推奨度 |
|----|------|--------|--------|
| 案1: NULL 許容 | EXTRACTED_AT = NULL のまま放置。重複行除去は案1（DISTINCT）のみ使う | 0円 | **推奨** |
| 案2: 一括 UPDATE で固定値埋め | `UPDATE ... SET EXTRACTED_AT = '2026-01-01T00:00:00+09:00' WHERE EXTRACTED_AT IS NULL` | 高（全件スキャン + DML） | 非推奨 |
| 案3: INSERT OVERWRITE | BQ パーティションテーブル仕様上、全件再挿入は非現実的 | 極高 | 対象外 |

**推奨案: 案1（NULL 許容）**

理由:
- 過去分のロード日時は実際には不明であり、固定値（`2026-01-01T00:00:00`）は虚偽の情報になる
- BQ DML UPDATE は全件スキャンを要し、大量行への実行はコスト・時間ともに不利
- 重複行除去は案1（DISTINCT）で十分機能する

**重複行除去クエリ（案1 DISTINCT を標準として採用）:**

```sql
-- 案1: DISTINCT で重複行除去（軽量・推奨）
-- EXTRACTED_AT が NULL の旧データでも動作する
SELECT DISTINCT CHUNK_INDEX, CHUNK_TEXT
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE DOC_ID = 'X' AND CHUNK_INDEX IS NOT NULL
ORDER BY CHUNK_INDEX;
```

案2（`ORDER BY EXTRACTED_AT DESC`）は `EXTRACTED_AT` が NULL の行では動作しないため、新規ロード分のみを対象とする場合に限り使用可。過去データを含む汎用クエリでは案1を使うこと。

---

### B-1. FILER_NAME 遡及修正（アルファベット ticker）

**現状**: アルファベットを含む ticker（例: 146A, 186A, 206A, 215A, 262A, 267A, 319A, 347A, 353A, 398A, 399A, 409A, 490A, 560A 等）の `FILER_NAME` が空文字列（`''`）。

**原因**: `tdnet-ai-prepare` が `state.json` に `filer_name` を保存しておらず、`tdnet-ai-finalize` が空文字で INSERT していた。

**影響範囲**: `2026-05-18` 当日分は BQ クエリで確認済み。過去分（アルファベット ticker が存在する全パーティション）も同様の経路を通った銘柄は空文字の可能性がある。

**修正方針**: `FILE_NAME`（blob_name）から会社名を `REGEXP_EXTRACT` で抽出して UPDATE する。

ファイル名フォーマット:
```
tdnet/TICKER/YYYYMMDD_TICKER_会社名_カテゴリ_タイトル_DOCID.pdf
```

**影響範囲確認クエリ（STEP 1 で実行）:**

```sql
-- アルファベット ticker かつ FILER_NAME が空文字の行数・銘柄数を確認
SELECT
  COUNT(*) AS total_rows,
  COUNT(DISTINCT TICKER) AS ticker_count,
  MIN(SUBMISSION_DATE) AS earliest_date,
  MAX(SUBMISSION_DATE) AS latest_date
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE REGEXP_CONTAINS(TICKER, r'[A-Za-z]')
  AND FILER_NAME = '';

-- 銘柄別の行数確認
SELECT
  TICKER,
  COUNT(*) AS row_count,
  MIN(SUBMISSION_DATE) AS earliest_date,
  MAX(SUBMISSION_DATE) AS latest_date,
  ARRAY_AGG(DISTINCT FILE_NAME LIMIT 3) AS sample_files
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE REGEXP_CONTAINS(TICKER, r'[A-Za-z]')
  AND FILER_NAME = ''
GROUP BY TICKER
ORDER BY TICKER;
```

**FILER_NAME 抽出クエリ（動作確認用・STEP 2 で実行）:**

```sql
-- FILE_NAME からの会社名抽出パターン確認（dry-run: 先頭50件）
SELECT
  TICKER,
  FILE_NAME,
  FILER_NAME AS current_filer_name,
  REGEXP_EXTRACT(FILE_NAME, r'/[0-9]+[A-Za-z]+/[0-9]{8}_[0-9A-Za-z]+_([^_]+)_') AS extracted_name
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE REGEXP_CONTAINS(TICKER, r'[A-Za-z]')
  AND FILER_NAME = ''
  AND FILE_NAME IS NOT NULL
LIMIT 50;
```

**UPDATE クエリ（STEP 3 で実行）:**

```sql
-- FILER_NAME を FILE_NAME から抽出して更新
UPDATE `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
SET FILER_NAME = REGEXP_EXTRACT(FILE_NAME, r'/[0-9]+[A-Za-z]+/[0-9]{8}_[0-9A-Za-z]+_([^_]+)_')
WHERE REGEXP_CONTAINS(TICKER, r'[A-Za-z]')
  AND FILER_NAME = ''
  AND FILE_NAME IS NOT NULL
  AND REGEXP_EXTRACT(FILE_NAME, r'/[0-9]+[A-Za-z]+/[0-9]{8}_[0-9A-Za-z]+_([^_]+)_') IS NOT NULL;
```

**コスト概算（UPDATE の BQ スキャン量）:**

BQ の DML UPDATE はパーティションプルーニングが効く場合と効かない場合がある。本クエリは `WHERE SUBMISSION_DATE` によるパーティション絞り込みを行っていないため、**テーブル全件スキャン**が発生する可能性がある。

- テーブルの推定サイズ: CHUNK_TEXT + EMBEDDING 含む大型テーブル。数十 GB〜数百 GB 規模
- BQ DML は無料枠なし（オンデマンド: $5/TB）
- **コスト削減策**: SUBMISSION_DATE でパーティション指定して分割実行する

```sql
-- パーティション分割実行例（2026-05-18 分のみ）
UPDATE `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
SET FILER_NAME = REGEXP_EXTRACT(FILE_NAME, r'/[0-9]+[A-Za-z]+/[0-9]{8}_[0-9A-Za-z]+_([^_]+)_')
WHERE SUBMISSION_DATE = '2026-05-18'
  AND REGEXP_CONTAINS(TICKER, r'[A-Za-z]')
  AND FILER_NAME = ''
  AND FILE_NAME IS NOT NULL
  AND REGEXP_EXTRACT(FILE_NAME, r'/[0-9]+[A-Za-z]+/[0-9]{8}_[0-9A-Za-z]+_([^_]+)_') IS NOT NULL;
```

STEP 1 の影響範囲確認で日付範囲が判明してから、パーティション単位で分割実行すること。

---

## 実施手順（STEP順）

### STEP 1: 影響範囲の定量確認

**目的**: B-1（FILER_NAME）の修正対象行数・日付範囲を確認する

1. BQ コンソール または `mcp__gcp__bq_query` で §B-1「影響範囲確認クエリ」を実行
2. 結果から以下を記録する:
   - 対象行数（total_rows）
   - 対象銘柄数（ticker_count）
   - 最古の SUBMISSION_DATE（earliest_date）→ パーティション分割実行の範囲特定に使う
3. A-1（EXTRACTED_AT）は案1（NULL 許容）採用につき、本 STEP での追加作業なし

**完了条件**: 影響行数・日付範囲が判明し、STEP 3 のコスト概算が確定できること

**実行結果（2026-05-20 08:30 JST）:**

| 対象 | total_rows | ticker_count | earliest_date | latest_date | FILE_NAME NULL |
|------|-----------|-------------|-------------|-----------|--------------|
| アルファベット ticker | 40,073 | 338 | 2024-09-24 | 2026-05-18 | 0行 |
| 数字のみ ticker（参考確認）| 9,315,255 | 4,999 | 2017-01-04 | 2026-05-18 | 未確認 |

- アルファベット ticker: パーティション範囲 2024-09-24〜2026-05-18（約20ヶ月）→ 月単位分割で実行（30超のため）
- FILE_NAME NULL = 0 → 40,073行が全件 UPDATE 対象
- **数字 ticker**: 9,315,255行 / 4,999銘柄と大規模。スコープ追加するか**ユーザー判断待ち**

---

### STEP 2: REGEXP 抽出パターンの動作確認

**目的**: `REGEXP_EXTRACT` が正しく会社名を取り出せるか確認する

1. §B-1「FILER_NAME 抽出クエリ（dry-run）」を実行（LIMIT 50）
2. `extracted_name` 列の値が正しい会社名の**完全形**になっているか目視確認
3. 抽出できていない行（NULL）があれば `FILE_NAME` のサンプルを確認し、正規表現パターンを修正
4. 確認観点:
   - アルファベット ticker（例: `146A`）のパスでパターンが正しく動作するか
   - **会社名に `_` が含まれる銘柄を意図的にサンプルに含めて確認する**。パターン `[^_]+` は `_` で打ち切られるため、部分文字列（例: 「A」）が書き込まれる誤データになるリスクがある。該当する FILE_NAME が存在した場合は正規表現パターンの修正が必要
   - `FILE_NAME` が NULL の行が存在するか（UPDATE から自動除外されるが件数を把握しておく）

**完了条件**: 抽出パターンが主要銘柄で正しく動作することを確認

---

### STEP 3: FILER_NAME の UPDATE 実行（パーティション分割）

**目的**: 影響範囲の全行に対して `FILER_NAME` を更新する

1. STEP 1 で判明した `earliest_date` から `2026-05-18` までの日付範囲を確認
2. パーティション分割粒度の判断: STEP 1 で判明した総パーティション数（日数）が **30 以下なら日単位、30 超なら月単位**でまとめて実行する
3. パーティション単位で §B-1「UPDATE クエリ（パーティション分割実行例）」を実行
   - まず `SUBMISSION_DATE = '2026-05-18'` 単日で試行し、結果を確認
   - 問題なければ過去分も判断粒度で順次実行
   - **中断した場合の再実行**: 同じ WHERE 条件（`FILER_NAME = ''`）で再実行すれば冪等。既更新行（`FILER_NAME != ''`）は UPDATE 対象外のため二重書きしない
4. 各実行後に更新行数（affected rows）を記録
5. UPDATE 後に以下2クエリで残存状況を確認:

```sql
-- クエリ1: FILE_NAME がある行の残存（期待: 0）
SELECT COUNT(*) AS remaining_with_filename
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE REGEXP_CONTAINS(TICKER, r'[A-Za-z]')
  AND FILER_NAME = ''
  AND FILE_NAME IS NOT NULL;

-- クエリ2: FILE_NAME が NULL の残存（件数を記録。対応不要 or 別途検討）
SELECT COUNT(*) AS remaining_null_filename
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE REGEXP_CONTAINS(TICKER, r'[A-Za-z]')
  AND FILER_NAME = ''
  AND FILE_NAME IS NULL;
```

**完了条件**: クエリ1が `0` かつクエリ2の件数を記録した

---

### STEP 4: 知見 MD・data_catalog MD の更新

**目的**: 本修正内容と今後のクエリ指針を知見ファイルに反映する

1. `docs/data_catalog/bq_tdnet_documents.md` の「重複行への注意」セクションに以下を追記:
   - `EXTRACTED_AT` は旧データ（2026-05-18 以前）は NULL であり案2の重複除去は使えない
   - 汎用クエリでは案1（DISTINCT）を標準とすること
2. `docs/knowledges/tools/013_tdnet_load.md` §現況サマリ 下部に以下を追記:
   - B-1 修正経緯: `_docs_from_ai_state` が ticker によらず `filer_name=""` をハードコード返却していた
   - 対応: `state.json` への `filer_name` 保存追加 + `_docs_from_ai_state` の `d.get("filer_name", "")` 修正（commit 8459c94a）
   - 過去データ: 本プラン（REGEXP_EXTRACT UPDATE）で遡及修正

**完了条件**: 両ファイルへの追記が完了し `git commit` 済み

---

### STEP 5: 完了確認・プランクローズ

1. STEP 3 の残存確認クエリ1が `0`、クエリ2の件数を記録したことを確認
2. 本プランMD のステータスを `完了` に更新
3. `git commit docs:` で本プランMDを含めてコミット

---

### B-2. FILER_NAME 遡及修正（数字のみ ticker） ← スコープ追加（2026-05-20）

**発端**: STEP 1 で数字 ticker も 9,315,255行 / 4,999銘柄 / 2017-01-04〜2026-05-18 が FILER_NAME = '' と判明。

**修正方針**: 2段階
1. **STOCK_CODE_LIST JOIN**（優先）: 82.5%（7,681,649行 / 4,144銘柄）をカバー
2. **REGEXP fallback**（残余 17.5%、主に上場廃止銘柄）: FILE_NAME から会社名抽出

**UPDATE クエリ（STEP 3b で実行、年単位分割）:**

```sql
-- B-2 STOCK_CODE_LIST JOIN方式（数字 ticker 対象）
UPDATE `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED` e
SET e.FILER_NAME = s.STOCK_NAME
FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST` s
WHERE e.TICKER = s.TICKER
  AND NOT REGEXP_CONTAINS(e.TICKER, r'[A-Za-z]')
  AND e.FILER_NAME = ''
  AND e.SUBMISSION_DATE BETWEEN 'YYYY-01-01' AND 'YYYY-12-31'
```

**STEP 3b: 数字 ticker 実行ログ**

| chunk | 期間 | 状態 |
|-------|------|------|
| 1 | 2017-01-01〜2018-12-31 | ✅ 完了 |
| 2 | 2019-01-01〜2020-12-31 | ✅ 完了 |
| 3 | 2021-01-01〜2022-12-31 | ✅ 完了 |
| 4 | 2023-01-01〜2024-12-31 | ✅ 完了 |
| 5 | 2025-01-01〜2026-05-18 | ✅ 完了 |

**STEP 3c: REGEXP fallback（unjoinable 1.6M行）** ✅ 完了
- 対象: STOCK_CODE_LIST に存在しない上場廃止銘柄（855銘柄、1,633,606行）
- パターン: `r'tdnet/[0-9]+/[0-9]{8}_[0-9]+_([^_]+)_'`
- 2017-2019 / 2020-2022 / 2023-2026 の3チャンク実行。残存=0

---

## 注意事項・非スコープ

- **A-1（EXTRACTED_AT）は UPDATE しない**: 過去データの「いつロードしたか」は復元不可能であり、固定値の埋め込みは虚偽情報になる。NULL のまま放置し、クエリ側で案1（DISTINCT）を使う設計を標準とする
- **FILER_NAME が NULL（`''` でなく真の NULL）の行は本 UPDATE 対象外**: 現状の修正対象は空文字列（`''`）のみ。NULL の行が存在する場合は別途調査が必要
- **FILE_NAME が NULL の行は自動的に UPDATE 対象外**: WHERE 句で `FILE_NAME IS NOT NULL` を指定しているため、これらの行は `FILER_NAME = ''` のまま残る。残存数が多い場合は別途対応を検討
- **アルファベット ticker 以外の銘柄（4桁数字のみ）は暫定非スコープ**: 修正前の `_docs_from_ai_state` は ticker によらず `filer_name=""` を返していたため、数字 ticker でも旧 ai-finalize 経路を通った行が `FILER_NAME = ''` の可能性がある。STEP 1 実施時に数字 ticker にも同様クエリを走らせて実態確認を推奨。影響が確認された場合はスコープに追加する
- **EMBEDDING・MAIN_CATEGORY 等の他カラムは非スコープ**: 本プランは `EXTRACTED_AT` と `FILER_NAME` の2項目のみを対象とする
- **新規ロード分（2026-05-18 以降）は修正済み**: `tdnet_load_parallel.py` 修正・`state.json` への `filer_name` 保存修正により、以降のロードは自動的に正しい値が入る

---

## レビュー追記: 2026-05-18 23:45 JST — code-reviewer

→ `docs/reviews/211_cr_bq_past_data_recovery.md`

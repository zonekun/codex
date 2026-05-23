# TDNET_DOCUMENTS_ENHANCED: EXTRACTED_AT NULL 修正 & FILER_NAME 空文字調査

**作成日時**: 2026-05-18 23:01 JST
**ステータス**: 完了（2026-05-18）
**分類**: (b) 継続改修型
**親知見 MD**: `docs/knowledges/tools/013_tdnet_load.md`
**対象ファイル**:
- `scripts/tdnet_load_parallel.py`（EXTRACTED_AT 修正対象、3経路）
**対象読者**: 次セッション担当
**目的**: 2026-05-18 の BQ データ状態調査で判明した 2 件の不具合を記録し、修正・調査を引き継ぐ。CHUNK_INDEX NULL は設計仕様（記録のみ、修正不要）。

---

## 発端

リョーサン菱洋（167A、2026-05-14）の文書 `140120260501516302.pdf` がBQ CHUNK_TEXTに存在するかを確認する中で、以下の 3 点を発見した。

---

## 調査済み事項（やったこと）

### A. CHUNK_INDEX NULL（**設計仕様 — 修正不要**）

- 全チャンクで `CHUNK_INDEX IS NULL`
- **原因**: 2026-05-18 の `ALTER TABLE ADD COLUMN CHUNK_INDEX INT64` 改造（プラン `tools-013_tdnet_chunk_index_column_20260517_223000.md` 完了済み）のスコープが「新規ロード分のみ」。過去データの遡及採番は明示非スコープ。
- `013_tdnet_load.md §T-3` / `bq_tdnet_documents.md §注意事項` に記載済み。TODO なし。

### B. FILER_NAME — NULL ではなく**空文字列 `""`**（167A 固有）

- BQ の CSV 出力でカンマ連続が NULL に見えたが、実際は `FILER_NAME = ''`（空文字列）。
- SUBMISSION_DATE=2026-05-14 の**全銘柄集計では `null_filer = 0`**（他の銘柄は値あり）。167A のみ全チャンクが空文字列。
- コード上の経路: `phase5_bq_insert_load` / `phase5_bq_insert_finalize` で `"FILER_NAME": doc.filer_name` をセット。ai-prepare が BQ から再読み込みする際 `filer_name = r.FILER_NAME or ""` でフォールバック（L1610）。
- **根本原因**: load 時点で `doc.filer_name` が空だったと推定。index CSV 側の欠損か、`parse_tdnet_filename` のパース失敗か、未確認。

### C. EXTRACTED_AT — **全銘柄・全日付で NULL**（設計欠落）

- 2026-05-18 新規データを含む全パーティションで `EXTRACTED_AT IS NULL`。
- **原因**: `scripts/tdnet_load_parallel.py` に `EXTRACTED_AT` の記述が**一切ない**（Grep 0件）。
- BQ Load Job（NDJSON）はスキーマの `DEFAULT CURRENT_TIMESTAMP()` を**適用しない仕様**。行辞書にキーが含まれない列は NULL になる。
- DDL に `DEFAULT CURRENT_TIMESTAMP()` が書かれているが、Load Job 経由では機能していない状態。
- `bq_tdnet_documents.md §重複行への注意` の案2（`ORDER BY EXTRACTED_AT DESC`）が機能しない問題もある。

---

## 優先度の定義

- **P0**: なし（データ破壊・SLO 違反なし）
- **P1**: EXTRACTED_AT 修正（重複行除去クエリ案2が機能しない副作用あり）
- **P2**: FILER_NAME 空文字の根本原因調査（167A 固有なら影響軽微）

---

## 指摘項目

### P1-1. `EXTRACTED_AT` をコードの row 辞書に追加 ⚠️

**症状**: `EXTRACTED_AT` が全件 NULL。`bq_tdnet_documents.md` の重複行除去クエリ案2（`ORDER BY EXTRACTED_AT DESC`）が機能しない。

**修正対象**: `scripts/tdnet_load_parallel.py` の 3 経路すべて

| 経路 | 関数 | 行番号（参考） |
|------|------|-------------|
| 新アーキ（ai-finalize） | `phase5_bq_insert_finalize` | L1994〜 の `base_row` |
| 新アーキ（load） | `phase5_bq_insert_load` | L1504〜 の `row` |
| legacy | `phase5_bq_insert` | L1404〜 の `base_row` |

**修正方針**: 各 row / base_row 辞書に以下を追加。

```python
from datetime import datetime, timezone, timedelta
JST = timezone(timedelta(hours=9))

# row 辞書に追加
"EXTRACTED_AT": datetime.now(JST).isoformat(),
```

> **注意**: `datetime.now()` は禁止（CLAUDE.md §7）。JST 明示必須。

**検証**:
1. `py_compile scripts/tdnet_load_parallel.py`
2. ai-finalize smoke: BQ に流した後 `SELECT EXTRACTED_AT FROM ... WHERE DOC_ID = 'X'` で非 NULL を確認
3. 重複行除去クエリ案2（`ORDER BY EXTRACTED_AT DESC`）が期待通り動作することを確認

**ロールバック**: コミット revert。

---

### P2-1. 167A FILER_NAME 空文字の根本原因調査 ℹ️

**症状**: 167A（リョーサン菱洋）の全チャンクで `FILER_NAME = ''`。他銘柄は値あり。

**調査手順**:
1. `parse_tdnet_filename` 関数を確認。ファイル名 `20260514_167A_リョーサン菱洋_...` からFILER_NAMEを取得する処理を特定。
2. 167A のアルファベット含み ticker（`167A`）でパース正規表現が失敗していないか確認。
3. index CSV（`gs://stock_data_1930932/tdnet/index_*.csv`）で 167A の `filer_name` 列を直接確認。
4. 同じくアルファベット含み ticker（`146A`、`1397` 等）で FILER_NAME が入っているか BQ で確認。

**確認クエリ**:
```sql
-- アルファベット含み ticker の FILER_NAME 状況を確認
SELECT TICKER, FILER_NAME, COUNT(*) AS cnt
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE SUBMISSION_DATE = '2026-05-18'
  AND REGEXP_CONTAINS(TICKER, r'[A-Za-z]')
GROUP BY TICKER, FILER_NAME
ORDER BY TICKER
LIMIT 20;
```

**対処方針（原因確認後）**:
- index CSV 側の欠損 → index CSV の取得・保存ロジックを確認
- `parse_tdnet_filename` のパース失敗 → 正規表現を修正し再デプロイ
- 167A 固有の一時的な問題（index CSV 更新遅延等）なら、現在のデータでは解消済みの可能性あり

---

## 関連ドキュメント

- `docs/knowledges/tools/013_tdnet_load.md` §T-3（CHUNK_INDEX の仕様記録）
- `docs/data_catalog/bq_tdnet_documents.md`（EXTRACTED_AT DEFAULT 記載、重複行クエリ案2）
- `docs/plans/archive/202605/tools-013_tdnet_chunk_index_column_20260517_223000.md`（CHUNK_INDEX 改造プラン）

---

## スコープ・非スコープ

**スコープ**:
- EXTRACTED_AT の row 辞書追記（3経路）
- FILER_NAME 空文字の根本原因調査

**非スコープ**:
- CHUNK_INDEX NULL の遡及採番（設計非スコープ）
- EXTRACTED_AT が NULL の過去データの遡及埋め戻し
- `bq_tdnet_documents.md` の DEFAULT 記述修正（DDL 参考用途なので据え置き）

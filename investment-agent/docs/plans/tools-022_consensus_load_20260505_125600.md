# STOCK.CONSENSUS テーブル再構成（QUICK 5項目対応）

**作成日時**: 2026-05-05 12:56 JST
**ステータス**: 全ステップ完了（2026-05-05）
**分類**: (b) 継続改修型
**親知見 MD**: `docs/knowledges/tools/022_consensus_load.md`
**関連知見 MD**: `docs/knowledges/tools/095_consensus_quick.md`
**基準 commit**: `cd9fb99`

## 目的

`STOCK.CONSENSUS` テーブルを QUICK 5項目（売上高・営業利益・経常利益・純利益・EPS）対応のスキーマに再構成する。TARGET列を廃止し、FY列で年度を識別する方式に変更する。

## スコープ

- **本プランの範囲**: BQ スキーマ変更（テーブル / VIEW / TVF）+ 書き込み側スクリプト改修 + 影響ファイルの漏れなき特定
- **本プランの範囲外**: 下流読み取りスクリプトの改修方針（TARGET廃止後の当期/来期判定ロジック、SOURCE変更対応等）は各プログラムの個別改修タスクで設計・実装する

## 背景・動機

- QUICK コンセンサス取得ルート追加により、経常利益のみだった CONSENSUS テーブルを5項目対応に拡張する必要がある
- RAKU は廃止予定（QUICK 安定稼働後）
- TARGET 列（CURRENT/NEXT）は廃止し、FY 列の値で年度を単純保存する方式に変更

## 設計

### 新スキーマ

| カラム名 | 型 | NULLABLE | 説明 |
|---------|-----|----------|------|
| DATAAT | DATE | NO | 取得日 |
| TICKER | STRING | NO | 銘柄コード（4桁） |
| FY | STRING | NO | 決算期（YYYYMM）。当期/来期の区別は読み取り側で判定 |
| QUARTER | STRING | NO | `1Q` / `2Q` / `3Q` / `FY` |
| REVENUE | INTEGER | YES | 売上高（百万円）。IFIS=NULL |
| OP_PROFIT | INTEGER | YES | 営業利益（百万円）。IFIS=NULL |
| ORD_PROFIT | INTEGER | YES | 経常利益（百万円）。旧 PROFIT 列 |
| NET_PROFIT | INTEGER | YES | 純利益（百万円）。IFIS=NULL |
| EPS | FLOAT64 | YES | EPS（円）。IFIS=NULL |
| SOURCE | STRING | NO | `IFIS` / `QUICK` |

### 廃止カラム

| カラム | 理由 |
|--------|------|
| PROFIT | ORD_PROFIT にリネーム |
| TARGET | FY 列で年度識別に一本化。読み取り側で当期/来期を判定 |

### ソース別データ特性

| SOURCE | QUARTER | 数値項目 | 年度 |
|--------|---------|---------|------|
| IFIS | 1Q / 2Q / 3Q / FY | ORD_PROFIT のみ（他4列 NULL） | 当期（CURRENT相当）のみ |
| QUICK | FY のみ | 5項目すべて | 当期・来期・再来期 |

> **備考**: RAKU廃止により1Q-3QのNEXTコンセンサスは取得不能になるが、そもそも四半期別でNEXTを因子に入れていること自体が不適切。下流改修時に是正すること。

### VIEW マージルール

`V_CONSENSUS_MERGED`: **QUICK 優先**、IFIS 補完。TARGET 列なし。

### テーブル移行方式

DROP → CREATE（既存データ破棄、移行不要）

---

## 作業ステップ

### P0: BQ スキーマ（テーブル再構成の前提）

1. [x] `STOCK.CONSENSUS` テーブル DROP → 新スキーマで CREATE
2. [x] `STOCK.V_CONSENSUS_MERGED` VIEW 再作成（QUICK優先・IFIS補完・TARGET廃止・新列追加）
3. [x] `STOCK.fn_consensus_merged_asof` TVF 再作成（同上）
4. [x] SQL ファイルを `scripts/sql/` に保存

### P0: 書き込み側スクリプト改修

5. [x] `scripts/update_conse_quick.py` — 新規作成完了（BQ INSERT + resume.json、CSV廃止でIFIS/RAKU同等構造）
6. [x] `scripts/update_conse_ifis.py` — 新スキーマ対応完了（PROFIT→ORD_PROFIT、TARGET廃止、新列NULL）。Codex実装→Claude Code取り込み（2026-05-05）
7. [x] `scripts/update_conse_rakuten.py` — RAKU 廃止完了（git rm削除、メニュー削除、data_catalog更新）

### P1: 読み取り側スクリプト改修（影響特定済み、実装は個別タスク）

8. [x] `scripts/zaraba_earnings.py` — TARGET廃止→FY判定ベース5項目対応完了（v3スキーマ）。レビュー078: B評価、critical#1修正済み、#2 deferred
9. [x] `scripts/earnings_model/batch_rerun_predict.py` — 便宜的完了（決算反応予測モデル再開発で統合予定）
10. [x] `scripts/export_consensus_csv.py` — TARGET廃止+PROFIT→ORD_PROFIT+C案FY判定対応完了（2026-05-05）
11. [x] `scripts/lib_conse_csv_from_view.py` — C案ロジック（PERIOD_REL判定）実装完了。`update_conse_quick.py` に `export_consensus_csv()` 呼び出し追加

### P1: ドキュメント更新

13. [x] `data_catalog.md` — スキーマ定義全面書き���え完了（QUICK/IFIS 2ソース、5項目、TARGET廃止）
14. [x] `docs/knowledges/tools/022_consensus_load.md` — QUICK/IFIS 2ソース体制に全面書き換え完了
15. [x] `docs/knowledges/tools/095_consensus_quick.md` — Phase 2完了+RAKU廃止実施済み反映

---

## 影響ファイル一覧

### BQ オブジェクト（3件）

| オブジェクト | 種別 | 変更内容 |
|------------|------|---------|
| `STOCK.CONSENSUS` | テーブル | DROP → 新スキーマ CREATE |
| `STOCK.V_CONSENSUS_MERGED` | VIEW | QUICK優先マージ、TARGET廃止、5項目追加 |
| `STOCK.fn_consensus_merged_asof` | TVF | 同上 |

### 書き込み側スクリプト（2件）

| ファイル | 影響箇所 | 変更概要 |
|---------|---------|---------|
| `scripts/update_conse_ifis.py` | L43: `BQ_TABLE`, L241-264: INSERT行構築 | PROFIT→ORD_PROFIT、TARGET列削除、新列(REVENUE等)をNULLで追加 |
| `scripts/update_conse_rakuten.py` | L54: `BQ_TABLE`, L723-770: レコード構築 | RAKU廃止。削除 or アーカイブ |

### 読み取り側スクリプト（4件）

| ファイル | 影響箇所 | 変更概要 |
|---------|---------|---------|
| `scripts/zaraba_earnings.py` | L482: SELECT列, L519: TARGET列チェック, L866: `TARGET=="CURRENT"`, L878: `TARGET=="NEXT"` | PROFIT→ORD_PROFIT。TARGET廃止→FY列から当期/来期を判定するロジックに変更。`_consensus_to_prior_fields()` の CURRENT/NEXT 分岐を FY 判定に書き換え |
| `scripts/earnings_model/batch_rerun_predict.py` | L181-188: SQL(`TARGET IN ('CURRENT','NEXT')`, `PROFIT AS CONSENSUS_PROFIT`, `SOURCE='RAKU'`), L324: `drop_duplicates(["TICKER","QUARTER","TARGET"])`, L327: cons_map キー `(TICKER, QUARTER, TARGET)` | TARGET列廃止→FY列で年度判定。PROFIT→ORD_PROFIT。SOURCE='RAKU'→新ソース体系。cons_mapキーからTARGET除去 |
| `scripts/export_consensus_csv.py` | L44: SELECT列(`TARGET`), L52: `TARGET=="NEXT"` 判定 | TARGET廃止→FY列で当期/来期を判定してピボット列を構築 |
| `scripts/lib_conse_csv_from_view.py` | L33: SELECT列(`TARGET`), L44: `row["TARGET"]`, L50-55: TARGET で CURRENT/NEXT 分岐 | TARGET廃止→FY列で当期/来期を判定 |

### ドキュメント（3件）

| ファイル | 変更概要 |
|---------|---------|
| `data_catalog.md` | L1212-1258: CONSENSUS スキーマ定義・VIEW 仕様・TVF 仕様を全面書き換え |
| `docs/knowledges/tools/022_consensus_load.md` | ソース構成(IFIS+QUICK)、TARGET廃止、VIEW/TVF仕様更新 |
| `docs/knowledges/tools/095_consensus_quick.md` | Phase 2 BQ対応完了を反映 |

---

## 検証戦略

1. **SQL 単体**: CREATE 後に `SELECT COUNT(*), COUNT(DISTINCT TICKER)` で空テーブル確認。VIEW/TVF は IFIS データ INSERT 後に動作確認
2. **書き込み smoke**: `update_conse_ifis.py --ticker 7203 --dry-run` → 新スキーマの行が正しく構築されることを確認
3. **読み取り smoke**: 各下流スクリプトで小範囲実行し、ORD_PROFIT 取得・FY 判定が正常動作することを確認
4. **回収手順**: CREATE TABLE / VIEW / TVF はすべて `CREATE OR REPLACE` で再作成可能。スクリプトは git revert

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/tools/022_consensus_load.md`（親知見）
- 知見 MD: `docs/knowledges/tools/095_consensus_quick.md`（QUICK仕様）
- TVF プラン: `docs/plans/20260428_230000_consensus_asof_tvf_plan.md`（TVF作成済み、再構成で再作成必要）
- data_catalog: `data_catalog.md` §STOCK.CONSENSUS

---

## 設計決定事項（2026-05-05 追記）

### PERIOD_REL（当期/来期/再来期）判定方式: C案（Python側ランタイム判定）

- **BQテーブルに PERIOD_REL カラムは追加しない**
- 下流スクリプト（merged CSV出力・ザラバ・決算モデル等）が実行時に以下を行う:
  1. FIN_STATEMENTS から `SELECT TICKER, MAX(CurrentFiscalYearEndDate) WHERE TypeOfCurrentPeriod='FY' GROUP BY TICKER` を1回取得
  2. CONSENSUS の FY 列と比較し、CURRENT/NEXT/NEXT2 を Python 内で判定
- 実装箇所: `scripts/lib_conse_csv_from_view.py`（実装完了 2026-05-05）
- 根拠: BQ読み取りコスト最小化。VIEW内JOINやルックアップテーブルより安価

### update_conse_quick.py アーキテクチャ

- 逐次append CSV廃止。BQ + resume.json のみ（IFIS/RAKU同等構造）
- merged CSV出力: `run()` 末尾で `export_consensus_csv(bq_client, CSV_PATH)` を呼び出し（実装完了 2026-05-05）

---

## レビュー追記: 2026-05-05 13:04 JST — code-reviewer

→ `docs/reviews/074_cr_consensus_restructure.md`

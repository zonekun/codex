# CONSENSUS SOURCE カラム追加・IFIS取り込み 上流プラン

**作成日時**: 2026-04-28 05:00 JST  
**対象ファイル**:
- BigQuery `gmailpj-357912.STOCK.CONSENSUS`（DDL変更）
- `scripts/update_conse_rakuten.py`（839行、commit 45b80f5 時点）
- `scripts/update_conse_ifis.py`（新規、Codex `codex/integration` ブランチから取り込み）
- `data_catalog.md`（スキーマ定義更新）
- `docs/knowledges/tools/022_conse_rakuten.md`（知見MD更新）

**対象読者**: code-reviewer サブエージェント / 次セッション担当  
**目的**: `STOCK.CONSENSUS` に `SOURCE` カラムを追加し、既存RAKU/新規IFISデータを分離保存する。下流 VIEW `V_CONSENSUS_MERGED` の前提を整える上流作業。非スコープ: 下流VIEW定義・下流スクリプト参照先変更（別プラン `20260427_230000_consensus_view_downstream_merge.md` で実施）。

---

## 前提サマリ

- 過去修正: commit 45b80f5 で `extract_consensus()` の FY 抽出バグ修正済み（`000000` → HTML正規抽出）
- Codex引継ぎ: `update_conse_ifis.py` が Codex `codex/integration` ブランチで実装済み（py_compile + dry-run 検証済み）
- 先行レビュー: `20260427_214427_ifis_direct_consensus_plan.md` が Request changes 判定。本プランはその指摘を反映した改訂版
- 後続プラン: `20260427_230000_consensus_view_downstream_merge.md`（下流VIEW + スクリプト参照先変更）
- 実機検証の有無: Codex 側で `--ticker 6723 --dry-run` 済み。BQ insert smoke は未実施

---

## BQ テーブル注記（DATAAT データ特性）

> **DATAAT の運用特性**: 各 SOURCE（RAKU / IFIS）のスクリプトは1回の実行で全銘柄の全QUARTER を同一 DATAAT でinsertする。したがって **同一SOURCE内の全行は同一DATAATを持つ**。例: RAKU 最新約4,000行が DATAAT=2026-04-26、IFIS 最新約4,000行が DATAAT=2026-04-24。RAKU と IFIS の最新 DATAAT は同一とは限らず、異なる可能性が高い。

---

## 優先度の定義

- **P0**: BQ DDL変更 + 既存行埋め戻し（後続の全作業の前提）
- **P1**: `update_conse_rakuten.py` SOURCE付与 + `update_conse_ifis.py` 取り込み
- **P2**: ドキュメント更新（data_catalog.md、知見MD）

---

## 指摘項目

### P0-1. BQ `STOCK.CONSENSUS` に `SOURCE` カラム追加 🚨

**症状**: SOURCE カラムが存在しないため、RAKU/IFIS データを分離できない。

**該当**: BigQuery DDL

**修正方針**:

```sql
ALTER TABLE `gmailpj-357912.STOCK.CONSENSUS`
ADD COLUMN SOURCE STRING;
```

**検証**:
```sql
SELECT column_name, data_type
FROM `gmailpj-357912.STOCK.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'CONSENSUS' AND column_name = 'SOURCE';
```

**ロールバック**: `ALTER TABLE ... DROP COLUMN SOURCE`（ただし埋め戻し後は DROP すると情報消失）

---

### P0-2. 既存行 `SOURCE='RAKU'` 埋め戻し 🚨

**症状**: 既存行の SOURCE が NULL のため、下流クエリで `WHERE SOURCE = 'RAKU'` が既存データを除外してしまう。

**該当**: BigQuery DML

**根本原因**: SOURCE カラムが新規追加のため、既存行はすべて NULL。

**修正方針**:

```sql
UPDATE `gmailpj-357912.STOCK.CONSENSUS`
SET SOURCE = 'RAKU'
WHERE SOURCE IS NULL;
```

**streaming buffer 制約への対処**:
- `update_conse_rakuten.py` は `insert_rows_json`（streaming insert）を使用
- streaming buffer に残っている行は UPDATE 対象外となる場合がある
- **実行条件**: `update_conse_rakuten.py` が実行中でないこと。直近の楽天コンセンサス取得から**最低30分**経過していること
- UPDATE 後に NULL 残存を確認:
  ```sql
  SELECT COUNT(*) FROM `gmailpj-357912.STOCK.CONSENSUS` WHERE SOURCE IS NULL;
  ```
- 0件でなければ待機して再実行。解決しない場合は CTAS/swap で対処

**検証**:
```sql
SELECT SOURCE, COUNT(*) AS cnt
FROM `gmailpj-357912.STOCK.CONSENSUS`
GROUP BY SOURCE;
-- 期待: SOURCE='RAKU' のみ、NULL=0
```

**ロールバック**: `UPDATE ... SET SOURCE = NULL WHERE SOURCE = 'RAKU'`（実害なし、元に戻るだけ）

---

### P1-1. `update_conse_rakuten.py` に `SOURCE='RAKU'` 付与 ⚠️

**症状**: 現在の insert 行に SOURCE がないため、今後の新規レコードも SOURCE=NULL になる。

**該当**: `scripts/update_conse_rakuten.py:L828`

```python:L828
bq_rows = [{"DATAAT": dataat, "TICKER": code, "FY": fy_period, **rec} for rec in records]
```

**修正方針**:

```python
# before
bq_rows = [{"DATAAT": dataat, "TICKER": code, "FY": fy_period, **rec} for rec in records]

# after
bq_rows = [{"DATAAT": dataat, "TICKER": code, "FY": fy_period, "SOURCE": "RAKU", **rec} for rec in records]
```

**呼び出し側への波及**: なし（BQ テーブルに新カラムが追加済みであれば、既存カラム + SOURCE で insert される）

**検証**: `--ticker 7203` で1銘柄テスト → BQ で `WHERE TICKER = '7203' AND SOURCE = 'RAKU' ORDER BY DATAAT DESC LIMIT 5` 確認

**ロールバック**: git revert。SOURCE='RAKU' 付きで入った行は UPDATE で NULL に戻せるが、実害ないのでそのまま。

---

### P1-2. `update_conse_ifis.py` Codex 実装の取り込み ⚠️

**症状**: IFIS直取得スクリプトが Codex リポジトリにのみ存在し、本番リポジトリに未配置。

**該当**: `C:\Users\zonekun\Documents\codex\investment-agent\scripts\update_conse_ifis.py` → `C:\gdrive\claude\investment-agent\scripts\update_conse_ifis.py`

**Codex 実装の確認結果**:

| 確認項目 | 結果 |
|---------|------|
| FY抽出 | `.prog_quarter` → `th` → NFKC正規化 → `今期\s*(\d{6})` → 月`01`〜`12`検証。FY取得不能時はスキップ（`000000` 不使用）。**OK** |
| コンセンサス行特定 | `tbody tr` → `th` テキストに `コンセンサス予想` を含む行を検索。`rows[2]` 固定ではない。**OK**（先行レビュー指摘反映済み） |
| QUARTER | `1Q/2Q/3Q/FY` の4列、`TARGET='CURRENT'` のみ。**NEXT は生成しない**。**OK**（IFIS制約通り） |
| SOURCE | `SOURCE='IFIS'` 固定。**OK** |
| BQ insert | `insert_rows_json`（streaming insert）。先行レビューでは Load Job + MERGE 推奨だったが、RAKU版と同じ方式。**要判断** |
| 再開機能 | `data/logs/conse_ifis_resume.json`。RAKU版と衝突しない。**OK** |
| CSV出力 | `C:\Users\zonekun\Dropbox\stock\py\conse_ifis.csv`。RAKU版と別ファイル。**OK** |
| print vs structlog | `print()` 使用。コーディング規約（structlog必須）違反。**要修正** |
| encoding | `open()` で `encoding` 指定あり（csv: cp932、state: utf-8）。**OK** |

**取り込み時の修正事項**:

1. **print → structlog**: 全 `print()` を `structlog.get_logger()` に置換
2. **冪等性判断**: 初回は RAKU 版と同じ `insert_rows_json` で開始（全件実行は低頻度のため重複リスクは低い）。将来的に Load Job + MERGE に移行する余地を残す
3. **`requests.Session` のスレッドセーフ**: 現行はシングルスレッドのため問題なし。将来並列化する場合は `threading.local()` 分離が必要（CLAUDE.md 規約）

**検証**:
```bash
# 1. py_compile
PYTHONUTF8=1 C:\venvs\investment-agent\Scripts\python.exe -m py_compile scripts/update_conse_ifis.py

# 2. dry-run（BQ未接続でも動く）
PYTHONUTF8=1 C:\venvs\investment-agent\Scripts\python.exe scripts/update_conse_ifis.py --ticker 6723 --dry-run
# 期待: FY=202612, 1Q=90400, 2Q=170900, 3Q=267600, FY=372075

# 3. BQ insert smoke（SOURCE カラム追加・埋め戻し完了後）
PYTHONUTF8=1 C:\venvs\investment-agent\Scripts\python.exe scripts/update_conse_ifis.py --ticker 6723
# BQ確認:
# SELECT * FROM STOCK.CONSENSUS WHERE TICKER = '6723' AND SOURCE = 'IFIS' ORDER BY DATAAT DESC;
```

**ロールバック**: ファイル削除 + `DELETE FROM STOCK.CONSENSUS WHERE SOURCE = 'IFIS'`

---

### P2-1. `data_catalog.md` 更新 📝

**該当**: `data_catalog.md:L1201-L1219`

**修正内容**:
- スキーマテーブルに `SOURCE STRING` 行を追加
- FY の説明を更新（`000000` 固定 → RAKU は `YYYYMM`（2026-04-27以降）/ IFIS は `YYYYMM`）
- 更新方法に IFIS 版を追記
- DATAAT データ特性の注記を追加
- 行数を更新

```markdown
| カラム名 | 型 | 説明 |
|---------|-----|------|
| DATAAT | DATE | 取得日（スクレイピング実行日。中断再開時も初回起動日で固定） |
| TICKER | STRING | 銘柄コード（4桁） |
| FY | STRING | 決算期（YYYYMM）。RAKU: 2026-04-27以降は正規抽出、それ以前は `000000`。IFIS: 常に正規値 |
| QUARTER | STRING | 四半期区分（`1Q` / `2Q` / `3Q` / `FY`） |
| PROFIT | INTEGER | 経常利益コンセンサス（百万円） |
| TARGET | STRING | `CURRENT`（当期予想）/ `NEXT`（来期予想）。IFIS は CURRENT のみ |
| SOURCE | STRING | データソース。`RAKU`（楽天証券経由IFIS）/ `IFIS`（IFIS株予報直取得） |
```

> **DATAAT データ特性**: 各 SOURCE のスクリプトは1回の実行で全銘柄の全QUARTER を同一 DATAAT で insert する。同一 SOURCE 内の全行は同一 DATAAT を持つ。RAKU と IFIS の最新 DATAAT は異なる可能性が高い。

**ロールバック**: git revert

---

### P2-2. 知見MD `022_conse_rakuten.md` 更新 📝

**修正内容**:
- IFIS直取得版（`update_conse_ifis.py`）の存在と使い分けを記載
- SOURCE カラムの説明を追加
- 下流 VIEW `V_CONSENSUS_MERGED` への参照を追加

**ロールバック**: git revert

---

## 実行順序（依存関係）

```
P0-1. SOURCE カラム追加 (ALTER TABLE)
    │
    ▼
P0-2. 既存行 SOURCE='RAKU' 埋め戻し (UPDATE)
    │    ※ streaming buffer 制約: RAKU スクリプト非実行中 + 30分以上経過
    │
    ├─→ P1-1. update_conse_rakuten.py に SOURCE='RAKU' 追加
    └─→ P1-2. update_conse_ifis.py 取り込み + structlog修正
    │
    ▼
[検証: P1-2 の dry-run + BQ insert smoke]
    │
    ▼
P2-1. data_catalog.md 更新
P2-2. 022_conse_rakuten.md 更新
    │
    ▼
[後続: 下流プラン実行（VIEW作成 + スクリプト参照先変更）]
```

**IFIS 全件実行のタイミング**: P0 + P1 + 下流プラン（VIEW作成 + 下流スクリプト修正）が**すべて完了してから**実施する。下流が VIEW 参照に切り替わる前に IFIS データを投入すると、生テーブル直接参照の下流で重複混入する。

---

## 検証戦略

1. **smoke test**: P0 完了後に `SELECT SOURCE, COUNT(*) FROM CONSENSUS GROUP BY 1` で全行 RAKU 確認。P1-2 の `--ticker 6723` で IFIS 1件 insert → BQ 確認
2. **dev 実機**: VIEW 定義不要（本プランは上流のみ）。IFIS dry-run で 10 銘柄テスト（`--ticker 6723,7203,8306,9984,4502,6758,6861,8035,6723,3382`）
3. **本番適用判断基準**: P0 の NULL 残存=0、P1-1 の新規 insert に SOURCE='RAKU' が入ること、P1-2 の dry-run 全成功
4. **回収手順**: SOURCE カラムは `ALTER TABLE ... DROP COLUMN`。IFIS データは `DELETE FROM CONSENSUS WHERE SOURCE = 'IFIS'`。RAKU スクリプトは git revert。いずれもデータ非破壊

---

## 対応アンチパターン

該当なし（本プランはカラム追加・埋め戻し・新規スクリプト取り込みのみ。既存データの破壊的変更を伴わない）。

---

## 関連ドキュメント

- 先行プラン（レビュー済み）: `docs/plans/20260427_214427_ifis_direct_consensus_plan.md`
- 後続プラン（下流VIEW）: `docs/plans/20260427_230000_consensus_view_downstream_merge.md`
- Codex 引継ぎ: `C:\Users\zonekun\Documents\codex\investment-agent\docs\codex-to-claude-handoff.md`（2026-04-27 IFIS関連2エントリ）
- 知見 MD: `docs/knowledges/tools/022_conse_rakuten.md`
- data_catalog: `data_catalog.md` L1201-L1219
- commit 45b80f5: conse_rakuten FY期抽出バグ修正

---

## レビュー追記: 2026-04-27 23:30 JST -- code-reviewer

# コードレビュー: CONSENSUS SOURCE カラム追加・IFIS取り込み 上流プラン

- 日時: 2026-04-27 23:30 JST
- 対象: `docs/plans/20260428_050000_consensus_source_column_upstream.md`
- パターン: 2 (改修)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: `STOCK.CONSENSUS` に `SOURCE STRING` カラムを追加し、既存行を `RAKU` で埋め戻し、楽天スクリプトに `SOURCE='RAKU'` 付与、Codex実装の IFIS 直取得スクリプトを取り込む上流プラン。
- 品質評価: **A** -- 先行レビューの Request changes 指摘を的確に反映し、上流/下流の分離も適切。Codex実装の品質も高い。ただし Codex コードに複数の規約違反・堅牢性欠落がある。
- 主要リスク:
  1. `update_conse_ifis.py` に `sys.exit(1)` が無く、BQ エラー多発時も exit 0 で終了する（004/A-1 違反）
  2. `update_conse_ifis.py` が全面 `print()` で structlog 未使用（CLAUDE.md コーディング規約違反）
  3. resume ロジックで `resume_from` のティッカー自体を処理せずスキップするバグ（L310-313）

---

## 【パターン2: 改修プラン評価】

### フォーマット適合性チェック

- [x] 冒頭に対象ファイルの基準 commit hash（`45b80f5`）が書かれている
- [x] 前提サマリで過去修正と残件数が明示されている
- [x] 優先度の定義（P0/P1/P2）が冒頭にある
- [ ] **部分不適合**: 各項目は「症状 / 該当 / 修正方針 / 検証 / ロールバック」を備えるが、P1-2 以外に「根本原因」フィールドが省略されている。本プランの性質上（新規追加であり既存バグ修正ではない）許容範囲ではあるが、テンプレートとの厳密な対応では欠落。
- [x] 修正方針に before/after の両方が書かれている（P1-1）
- [x] 呼び出し側への波及が明示されている（P1-1: 「なし」、P1-2: なし）
- [ ] **対応アンチパターン表が「該当なし」**: 後述するが `update_conse_ifis.py` に A-1/A-7 違反がある。「該当なし」は不正確。
- [x] 検証戦略が smoke / dev / 本番適用判断基準 / 回収手順の 4 段を網羅
- [x] ロールバック手順が各項目に書かれている
- [ ] **フォーマット正本へのリンクが欠落**: テンプレートでは末尾に `skills/planning.md` へのリンクが求められるが本プランには無い

### 妥当性

本プランは SOURCE カラム追加 + 埋め戻し + スクリプト修正という素直な DDL 拡張であり、既存データの構造を壊さない。ALTER TABLE ADD COLUMN は NULL で追加されるため既存行に実害なし。方針は真因（RAKU/IFIS 分離不能）に正しく対処している。

先行レビュー（Request changes）の 5 指摘に対する反映状況:
1. **IFIS抽出が `rows[2]` 固定でなくヘッダ検索** -- 反映済み。`find_consensus_row()` でヘッダ文言 `コンセンサス予想` を検索（`update_conse_ifis.py:L216-222`）。
2. **FY抽出に NFKC 正規化 + 月検証** -- 反映済み。`extract_current_fy_from_ifis()` で NFKC 正規化 + `01<=fy[4:6]<=12` 検証（`update_conse_ifis.py:L200-213`）。
3. **streaming buffer 制約考慮** -- 反映済み。P0-2 で 30 分待機 + NULL 残存確認 + CTAS/swap フォールバックを記載。
4. **既存本番系クエリの SOURCE 固定修正** -- 下流プランに委譲。妥当な判断（上流/下流の分離は設計的に正しい）。ただし下流プラン未完了の間に IFIS 全件投入しないルールが明記されている（プラン末尾「IFIS全件実行のタイミング」）点が重要で、正しくガードされている。
5. **BQ書き込みの冪等性** -- 先行レビューでは Load Job + MERGE 推奨だったが、本プランでは RAKU 版と同じ `insert_rows_json` を採用。プラン内で「初回は低頻度のため重複リスクは低い。将来的に Load Job + MERGE に移行する余地を残す」と明記。**これは現実的な判断であり許容する。ただし全件実行が繰り返される運用になった場合は C-5 アンチパターンに該当する点を認識すべき。**

### 副作用・デグレードチェック

- [x] ALTER TABLE ADD COLUMN は既存クエリに影響しない（既存クエリは SOURCE を参照していないため、NULL が追加されても挙動不変）
- [x] UPDATE SET SOURCE='RAKU' WHERE SOURCE IS NULL は既存データの他カラムを変更しない
- [x] `update_conse_rakuten.py` の修正は `"SOURCE": "RAKU"` を dict に追加するだけ。`**rec` より前に置かれているため `rec` 内に SOURCE キーが存在しても上書きされるが、現行 `extract_consensus()` は SOURCE を返さないので問題なし
- [x] 下流クエリは本プランでは変更しない。IFIS 投入前に下流プランで対処するフローが正しく定義されている

### 抜け漏れ（類似観点での横展開含む）

- [x] `update_conse_rakuten.py` 内の `insert_to_bq()` 関数自体は修正不要（呼び出し元の dict 構築で SOURCE を追加するだけ）
- [ ] **CSV 出力に SOURCE 列が含まれない**: `update_conse_ifis.py:L83-86` の `save_to_csv()` は `["TICKER", "1Q_CURRENT", ...]` 固定で SOURCE を含まない。楽天版も同じ。CSV 利用者が RAKU/IFIS を区別できないが、Dropbox CSV は簡易参照用なので実害は低い。ドキュメントに注記があると良い。
- [ ] **`update_conse_ifis.py` に `FY_NEXT` CSV列があるが IFIS は CURRENT のみ**: `build_csv_row()` (L283-290) で `FY_NEXT` 列を定義しているが IFIS は常に `TARGET='CURRENT'` なので常に空。動作上の問題はないが、楽天版との CSV フォーマット統一のための設計意図が明示されていると良い。

### 新規リスク

- **IFIS 側のレートリミット**: `REQUEST_INTERVAL_SEC = 1.0` で全銘柄（約 4,000 銘柄）を順次取得すると最低 66 分かかる。IFIS 側が IP ベースでレートリミットを敷いている場合、途中でブロックされる可能性がある。Codex 引継ぎメモにも「不発銘柄やIFIS側制限が出る場合はSelenium fallbackを検討」とある。初回全件実行時に HTTP 429 / 403 が出た場合のリトライ・バックオフ戦略がコードに無い。
- **BQ 課金**: `get_japanese_stock_tickers()` が BQ クエリ + JPX Excel フォールバックの 2 段構成だが、IFIS 版が楽天版と独立して毎回 BQ クエリを発行する。頻度が低い（明示的指示時のみ）なので実害は軽微。

---

## 【重大な指摘】（即修正）

### #1 `update_conse_ifis.py`: `sys.exit` が無く BQ エラー多発でも exit 0（004/A-1 違反）

- 箇所: `update_conse_ifis.py:L363-L406`（`run()` 関数全体と `__main__` ブロック）
- 事象: `process_codes()` が `Counter` を返し、`run()` が集計を `print` するだけで `sys.exit()` しない。`bq_error` や `http_error` が大量発生しても常に exit 0。
- トリガー: BQ 書き込みエラーが発生した場合。または IFIS 側が全面 403 を返した場合。
- 影響: 将来的にスケジューラ連携やチェーン実行で「成功扱い」になり、後続処理が誤発火する。現時点ではローカル手動実行のみなので即座の実害は低いが、004/A-1 に明確に違反。
- 根拠: コード全体に `import sys` も `sys.exit` も存在しない。
- 推奨対応: `run()` 末尾で `stats["bq_error"] + stats["http_error"] > 0` なら `sys.exit(1)`。`import sys` を追加。

### #2 `update_conse_ifis.py`: 全面 `print()` 使用（CLAUDE.md ロギング規約違反）

- 箇所: `update_conse_ifis.py` 全体（L73, L94, L129, L144, L147, L165, L315, L319, L328, L336, L345, L383, L392-394 等、20箇所以上）
- 事象: CLAUDE.md のコーディング規約は「print禁止。structlogを使用」と明記。本スクリプトは `structlog` を一切 import せず、全ログ出力が `print()`。
- トリガー: 常時。
- 影響: 構造化ログが取れず、将来の Cloud Run 移行時にログ検索・フィルタが不能。プロジェクト規約との一貫性が崩れる。
- 根拠: CLAUDE.md「ロギング: print禁止。structlogを使用」。ただし既存 `update_conse_rakuten.py` も `print()` を使用しており（structlog 未導入）、Codex がそれに合わせた可能性がある。
- 推奨対応: プラン記載通り `structlog.get_logger()` に置換。楽天版も同様だが、本プランのスコープは IFIS 版のみで十分。

### #3 `update_conse_ifis.py`: resume ロジックで `resume_from` 自体を処理せずスキップ

- 箇所: `update_conse_ifis.py:L306-L313`
- 事象: `resume_from` に一致したティッカーを見つけた時点で `skip_until_resume = False` にするが、その直後に `continue` するため、**`resume_from` のティッカー自体は処理されない**。
- トリガー: 中断再開時。例: `resume_from = "7203"` の場合、7203 はスキップされ 7203 の次のティッカーから処理再開。
- 影響: 中断時に `save_state(dataat, code)` が呼ばれるタイミングによっては、最後に処理完了したティッカーではなく「処理中に中断したティッカー」が記録される可能性がある。BQ insert 成功前に `save_state` が呼ばれるパス（HTTP エラー時: L322、skip 時: L331）では、そのティッカーは未処理のまま resume 対象外になる。
- 根拠: L310-313: `if code == resume_from: skip_until_resume = False` → `continue`（同一ループ反復）。楽天版（`update_conse_rakuten.py:L812-818`）も同じロジックだが、楽天版は `save_state` を処理完了後（L839）に呼ぶため、`resume_from` は「最後に完了したティッカー」を指す設計。IFIS 版も同様の設計意図と読めるが、HTTP エラー時（L322）にも `save_state` が呼ばれるため、「完了していないが記録されたティッカー」が生じうる。
- 推奨対応: これは楽天版と同じ設計パターンなので、意図的な「最後に完了したティッカーの次から再開」であると解釈できる。ただしその場合、HTTP エラー時に `save_state` を呼ぶのは設計矛盾（未完了なのに resume ポイントを進めている）。HTTP エラー時の `save_state` を `save_state` 前に「BQ insert 成功した最終ティッカー」を別途追跡するか、HTTP エラー時は `save_state` を呼ばないようにすることを検討。

### #4 `update_conse_ifis.py`: 終了サマリに `processed` / `skipped` / `errors` の 3 指標が不足（004/A-7 違反）

- 箇所: `update_conse_ifis.py:L391-394`
- 事象: `Counter` の `most_common()` で出力されるが、`processed` の総数が明示されない。`Counter` のキーは `dry_run_ok`, `saved`, `bq_error`, `http_error`, `fy_not_found` 等だが、「何銘柄中何銘柄処理したか」が不明。
- トリガー: 常時。
- 影響: 監視側が「処理件数 0 の成功」を検知できない。
- 推奨対応: ループ内で `stats["processed"] += 1` を追加し、サマリに `processed / total` を表示。

---

## 【改善提案】（可読性・保守性）

### #1 P0-2 の streaming buffer 待機時間を具体化

- 箇所: プラン P0-2
- 現状: 「最低30分経過していること」とあるが、BigQuery の streaming buffer は最大 90 分残存する可能性がある（BQ 公式ドキュメント）。
- 提案: 「最低90分」に変更するか、「30分後に UPDATE 実行し、NULL 残存があれば追加で 60 分待機」と 2 段階にする。現行の記述でも NULL 確認 + 再実行のフローがあるので実運用上は問題ないが、待機時間の根拠を明示すると良い。

### #2 `update_conse_ifis.py` のレートリミット対策

- 箇所: `update_conse_ifis.py:L316-L324`
- 現状: HTTP エラーは `except Exception` で catch して `continue` するのみ。429/403 が連続した場合もバックオフせず 1 秒間隔で次のティッカーに進む。
- 提案: 429 レスポンスを検出したら `time.sleep(60)` 等のバックオフを入れる。または連続 N 回 HTTP エラーでループ停止する safety valve を追加。

### #3 `data_catalog.md` 更新案の行数が古い

- 箇所: プラン P2-1
- 現状: 「行数: 14,046」は 2026-03-24 確認時の値。SOURCE 追加後に行数表記を更新する記載がプランにあるが、更新後の値は RAKU 最新実行時の行数に依存するため、実行時に確認が必要。プランに「実行後に `SELECT COUNT(*) FROM CONSENSUS` で取得した値に更新」と手順を明記すると漏れにくい。

### #4 `update_conse_ifis.py` CSV 出力先が Dropbox 直下

- 箇所: `update_conse_ifis.py:L43`
- 現状: `CSV_PATH = r"C:\Users\zonekun\Dropbox\stock\py\conse_ifis.csv"`
- 提案: CLAUDE.md に「Dropbox はクラウドラン本番運用のスクリプトがローカル実行時のテスト出力先として使うのも禁止」とあるが、本スクリプトはローカル専用・明示的実行のみなので厳密には禁止対象外。ただし楽天版が同じ Dropbox パスに CSV を出しているため、楽天版との一貫性で許容。注意だけ記録する。

---

## 【確認できなかった事項】

- **IFIS ページの HTML 構造変更頻度**: `prog_quarter` クラスや `コンセンサス予想` ヘッダ文言が IFIS 側のリニューアルで変わる頻度が不明。実行時に大量の `consensus_row_not_found` スキップが発生する場合は HTML 構造の変更を疑う必要がある。
- **IFIS 側のアクセス制限ポリシー**: robots.txt / 利用規約での自動取得の可否は確認していない。dry-run が 1 銘柄で成功しているが、全件実行時の挙動は不明。
- **BQ streaming buffer の正確な残存時間**: 公式ドキュメントでは「up to 90 minutes」だが、実測値はケースバイケース。30 分で足りるかは実行して NULL 確認で判断するしかない。
- **`update_conse_rakuten.py` の既存 `print()` 規約違反**: 楽天版も全面 `print()` だが、本プランのスコープ外のため指摘対象外とした。将来的には楽天版も structlog 化が望ましい。

---

## 【判定】

**Approve（条件付き）**

重大指摘 #1（sys.exit 欠落）と #2（print→structlog）は取り込み時に修正すればブロッカーではない。プランの設計方針・上流/下流分離・先行レビュー指摘の反映は適切。#3（resume ロジック）は楽天版と同一パターンであり既知の設計トレードオフとして許容するが、HTTP エラー時の `save_state` は要検討。

取り込み時の最低限の修正事項:
1. `import sys` + `run()` 末尾で error 時 `sys.exit(1)` 追加
2. `print()` を `structlog.get_logger()` に置換
3. `process_codes()` に `processed` カウンタ追加、終了サマリに総数表示
4. 対応アンチパターン表を「該当なし」から A-1/A-7 に更新

# CONSENSUS下流マージロジック: BQ VIEW統合

**作成日時**: 2026-04-27 23:00 JST  
**対象ファイル**:
- BigQuery `gmailpj-357912.STOCK.V_CONSENSUS_MERGED`（新規VIEW）
- `scripts/zaraba_earnings.py`（1,300行超、commit 45b80f5 時点）
- `scripts/earnings_model/batch_rerun_predict.py`（600行超、commit 45b80f5 時点）
- `scripts/export_consensus_csv.py`（91行、commit 45b80f5 時点）

**対象読者**: code-reviewer サブエージェント / 次セッション担当  
**目的**: RAKU/IFIS 2ソースの CONSENSUS データを BQ VIEW で統合し、下流プログラムは VIEW を参照するだけで「IFIS優先・片方補完・SOURCE別最新DATAAT」のマージロジックを得られるようにする。スコープ: VIEW定義 + 下流3スクリプトの参照先変更。非スコープ: SOURCE カラム追加・埋め戻し・IFIS取り込みスクリプト本体（別プランで実施済み/実施予定）。

---

## 前提サマリ

- 前提: `STOCK.CONSENSUS` に `SOURCE STRING` カラムが追加済み、既存行が `SOURCE='RAKU'` で埋め戻し済みであること
- 前提: `update_conse_rakuten.py` が `SOURCE='RAKU'` 付きでinsertしていること
- 前提: `update_conse_ifis.py` が `SOURCE='IFIS'` 付きでinsertしていること（未実行でもVIEWは定義可能）
- 関連プラン: `docs/plans/20260427_214427_ifis_direct_consensus_plan.md`（実装順序 Step 1〜4 完了後に本プランを実行）
- 実機検証の有無: 未検証

---

## マージルール

| 条件 | 採用SOURCE |
|------|-----------|
| 同一 (TICKER, FY, QUARTER, TARGET) に IFIS・RAKU 両方あり | **IFIS** |
| IFIS のみ | IFIS |
| RAKU のみ | RAKU |

**DATAAT の扱い**: 各 SOURCE 内で **TICKER 単位の最新 DATAAT** のレコードを使う。RAKU と IFIS の DATAAT が異なっても問題ない。

**データ特性**: 各SOURCE内の全行は同一DATAATを持つ（1回のスクリプト実行で全銘柄を同一日付でinsert）。例: RAKU 最新約4,000行が DATAAT=4/26、IFIS 最新約4,000行が DATAAT=4/24。このため VIEW の `PARTITION BY TICKER, FY, QUARTER, TARGET` と `PARTITION BY TICKER` は実質同一結果となる。

**VIEW が返す DATAAT**: 個別行の実DATAAT ではなく、VIEW全行で `MAX(全SOURCE の最新DATAAT)` を代表日付として返す。キャッシュ判定に使う用途。例: RAKU=4/26, IFIS=4/24 → 全行に `4/26` を返す。

**IFIS の制約**: `TARGET='NEXT'` のレコードは存在しない（CURRENT FY のみ）。したがって NEXT は常に RAKU からのフォールバック。

---

## 優先度の定義

- **P0**: VIEW 定義（下流がVIEW参照に切り替わるための土台）
- **P1**: 下流3スクリプトの参照先変更
- **P2**: export_consensus_csv の SOURCE_USED 表示対応（Nice-to-have）

---

## 指摘項目

### P0-1. BQ VIEW `STOCK.V_CONSENSUS_MERGED` 定義 🚨

**症状**: 現在 VIEW が存在しないため、下流は生テーブルを直接参照しており SOURCE 混在に対応できない。

**修正方針**: CTE で各 SOURCE の最新レコードを抽出し、FULL OUTER JOIN + COALESCE でマージする VIEW を作成。

```sql
CREATE OR REPLACE VIEW `gmailpj-357912.STOCK.V_CONSENSUS_MERGED` AS
WITH raku_latest AS (
  SELECT TICKER, FY, QUARTER, PROFIT, TARGET, DATAAT
  FROM `gmailpj-357912.STOCK.CONSENSUS`
  WHERE SOURCE = 'RAKU'
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY TICKER, FY, QUARTER, TARGET
    ORDER BY DATAAT DESC
  ) = 1
),
ifis_latest AS (
  SELECT TICKER, FY, QUARTER, PROFIT, TARGET, DATAAT
  FROM `gmailpj-357912.STOCK.CONSENSUS`
  WHERE SOURCE = 'IFIS'
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY TICKER, FY, QUARTER, TARGET
    ORDER BY DATAAT DESC
  ) = 1
),
max_dataat AS (
  -- 全SOURCE横断の最新日付（代表DATAAT）
  SELECT GREATEST(
    (SELECT MAX(DATAAT) FROM raku_latest),
    (SELECT COALESCE(MAX(DATAAT), DATE '1900-01-01') FROM ifis_latest)
  ) AS representative_dataat
)
SELECT
  COALESCE(i.TICKER, r.TICKER) AS TICKER,
  COALESCE(i.FY, r.FY) AS FY,
  COALESCE(i.QUARTER, r.QUARTER) AS QUARTER,
  COALESCE(i.PROFIT, r.PROFIT) AS PROFIT,
  COALESCE(i.TARGET, r.TARGET) AS TARGET,
  CASE WHEN i.PROFIT IS NOT NULL THEN 'IFIS' ELSE 'RAKU' END AS SOURCE_USED,
  (SELECT representative_dataat FROM max_dataat) AS DATAAT
FROM ifis_latest i
FULL OUTER JOIN raku_latest r
  ON i.TICKER = r.TICKER
  AND i.FY = r.FY
  AND i.QUARTER = r.QUARTER
  AND i.TARGET = r.TARGET
```

**検証**:
```sql
-- VIEW作成後の基本確認
SELECT SOURCE_USED, COUNT(*) FROM `STOCK.V_CONSENSUS_MERGED` GROUP BY 1;

-- 特定銘柄でIFIS優先が効いているか
SELECT * FROM `STOCK.V_CONSENSUS_MERGED` WHERE TICKER = '6723' ORDER BY QUARTER;

-- RAKU/IFISの値が異なるケースの確認
SELECT v.TICKER, v.QUARTER, v.PROFIT AS merged, v.SOURCE_USED,
       r.PROFIT AS raku_profit, i.PROFIT AS ifis_profit
FROM `STOCK.V_CONSENSUS_MERGED` v
LEFT JOIN raku_latest r USING (TICKER, FY, QUARTER, TARGET)
LEFT JOIN ifis_latest i USING (TICKER, FY, QUARTER, TARGET)
WHERE r.PROFIT IS NOT NULL AND i.PROFIT IS NOT NULL AND r.PROFIT != i.PROFIT
LIMIT 20;
```

**ロールバック**: `DROP VIEW STOCK.V_CONSENSUS_MERGED` で即座に消去可能。下流を戻す場合は git revert。

---

### P1-1. `zaraba_earnings.py` VIEW参照化 ⚠️

**症状**: 現在 `STOCK.CONSENSUS` を直接 `MAX(DATAAT)` で参照。SOURCE混在時に重複取得のリスク。

**該当**: `scripts/zaraba_earnings.py:L469-L483` / `_load_or_fetch_consensus()`

```python:L469-L483
max_sql = f"SELECT FORMAT_DATE('%Y%m%d', MAX(DATAAT)) AS max_dataat FROM `{ds}.CONSENSUS`"
# ...
conse_sql = f"""
SELECT DATAAT, TICKER, FY, QUARTER, PROFIT, TARGET
FROM `{ds}.CONSENSUS`
WHERE DATAAT = (SELECT MAX(DATAAT) FROM `{ds}.CONSENSUS`)
"""
```

**修正方針**: VIEW を参照するように変更。VIEW は各SOURCE内の最新を既にマージ済み＋代表DATAAT（全SOURCE中の最新日付）を全行に付与するため、日付フィルタ不要で全件取得できる。

```python
# before
max_sql = f"SELECT FORMAT_DATE('%Y%m%d', MAX(DATAAT)) AS max_dataat FROM `{ds}.CONSENSUS`"
conse_sql = f"""
SELECT DATAAT, TICKER, FY, QUARTER, PROFIT, TARGET
FROM `{ds}.CONSENSUS`
WHERE DATAAT = (SELECT MAX(DATAAT) FROM `{ds}.CONSENSUS`)
"""

# after
max_sql = f"SELECT FORMAT_DATE('%Y%m%d', MAX(DATAAT)) AS max_dataat FROM `{ds}.V_CONSENSUS_MERGED`"
conse_sql = f"""
SELECT DATAAT, TICKER, FY, QUARTER, PROFIT, TARGET, SOURCE_USED
FROM `{ds}.V_CONSENSUS_MERGED`
"""
```

**キャッシュ管理への影響**: VIEW の DATAAT は全行一律で「全SOURCE中の最新日付」（例: RAKU=4/26, IFIS=4/24 → 全行 4/26）。`MAX(DATAAT)` によるキャッシュ鮮度判定は現行と同じロジックで機能する。いずれかの SOURCE が更新されれば代表DATAAT が変わり、キャッシュ miss → 再取得が発火する。

**VIEW全件取得の件数根拠**: VIEW は各 (TICKER, FY, QUARTER, TARGET) につき最新1行のみ返す（dedup済み）。件数は「銘柄数 × QUARTER種類 × TARGET種類」程度（現行の MAX(DATAAT) フィルタ時と同等）。

**��び出し側への波及**:
- `_refresh_prior_consensus()` (L496-551): `df_conse` の列名は変わらないため影響なし
- `_guidance_vs_consensus()` (L1290): `consensus_profit` / `consensus_profit_next` フィールド参照のみ、影響なし

**検証**: `--data consensus` モードでキャッシュ更新 → CSV内容確認。6723 ルネサスで IFIS 値が入っているか。

**ロールバック**: git revert でコード戻し + 古いキャッシュCSV削除。データ破壊なし。

---

### P1-2. `batch_rerun_predict.py` VIEW参照化 ⚠️

**症状**: `DATAAT <= DATE_MAX_PREDICT` で as-of 取得している。SOURCE混在時に同一キーの重複が発生する。

**該当**: `scripts/earnings_model/batch_rerun_predict.py:L162-L168`

```python:L162-L168
q_cons = f"""SELECT TICKER, QUARTER, TARGET, DATAAT, CONSENSUS_PROFIT FROM (
  SELECT TICKER, QUARTER, TARGET, DATAAT, PROFIT AS CONSENSUS_PROFIT,
    ROW_NUMBER() OVER (PARTITION BY TICKER, QUARTER, TARGET ORDER BY DATAAT DESC) AS rn
  FROM `gmailpj-357912.STOCK.CONSENSUS`
  WHERE DATAAT <= '{hy(DATE_MAX_PREDICT)}'
    AND TARGET IN ('CURRENT', 'NEXT')
) WHERE rn <= 20"""
```

**修正方針**: このクエリは **as-of（特定日時点）** の最新値を求める用途。VIEW は「全時点の最新」なのでそのまま置き換えられない。2つの選択肢:

**案A（推奨）: VIEW不使用、SOURCE='RAKU'固定のまま段階移行**
- 決算反応モデルは精度に直結するため、IFIS統合は慎重に検証してから行う
- 当面は `AND SOURCE = 'RAKU'` を追加するのみ
- IFIS統合は別途ICバックテストで有効性を確認してから

**案B: as-of対応VIEW（TVF）を別途作成**
- `STOCK.fn_consensus_merged_asof(date)` のようなTVF
- 複雑度が上がるため、現時点では不要

```python
# before (現行)
q_cons = f"""SELECT TICKER, QUARTER, TARGET, DATAAT, CONSENSUS_PROFIT FROM (
  SELECT TICKER, QUARTER, TARGET, DATAAT, PROFIT AS CONSENSUS_PROFIT,
    ROW_NUMBER() OVER (PARTITION BY TICKER, QUARTER, TARGET ORDER BY DATAAT DESC) AS rn
  FROM `gmailpj-357912.STOCK.CONSENSUS`
  WHERE DATAAT <= '{hy(DATE_MAX_PREDICT)}'
    AND TARGET IN ('CURRENT', 'NEXT')
) WHERE rn <= 20"""

# after (案A: SOURCE固定)
q_cons = f"""SELECT TICKER, QUARTER, TARGET, DATAAT, CONSENSUS_PROFIT FROM (
  SELECT TICKER, QUARTER, TARGET, DATAAT, PROFIT AS CONSENSUS_PROFIT,
    ROW_NUMBER() OVER (PARTITION BY TICKER, QUARTER, TARGET ORDER BY DATAAT DESC) AS rn
  FROM `gmailpj-357912.STOCK.CONSENSUS`
  WHERE DATAAT <= '{hy(DATE_MAX_PREDICT)}'
    AND TARGET IN ('CURRENT', 'NEXT')
    AND SOURCE = 'RAKU'
) WHERE rn <= 20"""
```

**呼び出し側への波及**: なし（出力カラムは同一）

**検証**: 既存の batch_rerun 結果と diff が出ないことを確認（SOURCE='RAKU' 追加は既存RAKU行のみ残すため結果不変）

**将来方針**: IFIS統合の有効性がICバックテストで確認でき次第、BQ Table Function (TVF) `STOCK.fn_consensus_merged_asof(date)` を作成し、as-of マージを BQ 側で完結させる。TVF化すればこのクエリは `SELECT * FROM STOCK.fn_consensus_merged_asof('{hy(DATE_MAX_PREDICT)}')` に置換できる。

---

### P1-3. `export_consensus_csv.py` VIEW参照化 ⚠️

**症状**: 最新DATAAT全件をピボットしてCSV出力。SOURCE混在時に同一TICKERの重複行が発生する。

**該当**: `scripts/export_consensus_csv.py:L43-L47`

```python:L43-L47
sql = f"""
    SELECT *
    FROM `{BQ_TABLE}`
    WHERE DATAAT = (SELECT MAX(DATAAT) FROM `{BQ_TABLE}`)
"""
```

**修正方針**: VIEW参照に切り替え。VIEW は既にマージ済みなので日付フィルタ不要。

```python
# before
BQ_TABLE = "gmailpj-357912.STOCK.CONSENSUS"
sql = f"""
    SELECT *
    FROM `{BQ_TABLE}`
    WHERE DATAAT = (SELECT MAX(DATAAT) FROM `{BQ_TABLE}`)
"""

# after
BQ_VIEW = "gmailpj-357912.STOCK.V_CONSENSUS_MERGED"
sql = f"""
    SELECT TICKER, FY, QUARTER, PROFIT, TARGET, DATAAT, SOURCE_USED
    FROM `{BQ_VIEW}`
"""
```

**呼び出し側への波及**: なし（ピボットロジックは TICKER/QUARTER/TARGET/PROFIT を使うため変更不要）

**検証**: 出力CSV の行数・値を VIEW 切替前後で比較。IFIS 導入前は完全一致のはず。

**ロールバック**: git revert。Dropbox上のCSVは次回正常実行で上書きされる。

---

### P2-1. export_consensus_csv に SOURCE_USED 列追加 📝

**症状**: CSV利用者がどのソースの値か判別できない。

**修正方針**: ピボット後に SOURCE_USED 列を残す（optional）。Excel上で IFIS/RAKU の出所が分かる。

**検証**: CSVを目視確認。

---

## 実行順序（依存関係）

```
[前提: 別プラン Step 1-4 完了]
    │
    ▼
P0-1. VIEW作成 (BQ DDL)
    │
    ├─→ P1-1. zaraba_earnings.py 修正
    ├─→ P1-3. export_consensus_csv.py 修正
    └─→ P1-2. batch_rerun_predict.py 修正 (SOURCE='RAKU'固定のみ)
    │
    ▼
[検証: 全スクリプトの既存動作が変わらないことを確認]
    │
    ▼
P2-1. export CSV に SOURCE_USED 追加 (optional)
```

---

## 検証戦略

1. **smoke test**: VIEW 作成後に `SELECT * FROM V_CONSENSUS_MERGED WHERE TICKER = '6723' LIMIT 10` で IFIS 優先が効いているか確認
2. **下流 diff**: 各スクリプトの修正前後で出力が同一であることを確認（IFIS 投入前なので全行 SOURCE_USED='RAKU' のはず）
3. **IFIS投入後検証**: `update_conse_ifis.py --ticker 6723` 実行後に VIEW で IFIS 値が優先されること、zaraba_earnings のキャッシュで IFIS 値が取得されることを確認
4. **回収手順**: VIEW は `DROP VIEW` で即消去。スクリプトは git revert。データ破壊なし

---

## FY不一致の考慮

RAKU は `FY='000000'` だった歴史がある（2026-04-27 修正済み）。IFIS は `FY=YYYYMM`。

**JOIN キーに FY を含む**ため、同一銘柄でも FY が異なると別レコード扱いになる。

**動作**:
- RAKU の FY='000000' 行と IFIS の FY='202612' 行は JOIN でマッチしない → 両方独立して VIEW に出力される
- ただし QUALIFY により各 (TICKER, FY, QUARTER, TARGET) の最新DATAAT 1行のみ残る
- 2026-04-27 以降の RAKU 行は正しい FY が入る → IFIS と正しく JOIN される
- 過去の FY='000000' 行は RAKU 最新行（正しいFY）より古い DATAAT を持つため、QUALIFY で淘汰される

**結論**: FY='000000' の歴史的データは QUALIFY の DATAAT DESC で自然に排除される。下流への影響なし。

---

## 対応アンチパターン

該当なし（本プランは新規VIEW定義 + 参照先変更のみ。既存データの書き換え・削除を伴わない）。

---

## 関連ドキュメント

- 親プラン: `docs/plans/20260427_214427_ifis_direct_consensus_plan.md`
- 知見 MD: `docs/knowledges/tools/022_conse_rakuten.md`
- data_catalog: `data_catalog.md` の STOCK.CONSENSUS セクション
- フォーマット正本: `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット

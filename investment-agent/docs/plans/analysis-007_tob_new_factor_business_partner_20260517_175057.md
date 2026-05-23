# TOB MLモデル C: 新因子 has_business_partner_investor 追加

**作成日時**: 2026-05-17 17:50 JST（詳細化: 2026-05-17）
**ステータス**: 中止（2026-05-18: has_business_partner_investor は train_rf.py から削除済み、コミット 4e811148）
**依存**: Plan B (`analysis-007_tob_classify_listed_corp_20260517_175057.md`) 完了後に Step C 以降を着手
  （EXTEND テーブルに `LISTED_CORP` TYPE が存在しないと因子が全件 FALSE になる）
**対象ファイル**:
- `scripts/tob_prediction/compute_owner_features.py`（264行）
- `scripts/tob_prediction/train_rf.py`（579行）
- `docs/data_catalog/bq_shareholder_composition.md`
- `docs/knowledges/analysis/007_tob_ml_prediction.md`
**対象読者**: 実装担当者

---

## 前提サマリ

- 現在の Walk-Forward ROC-AUC: **0.755**（26変数、2022-2025年平均）
- `TOP_SHAREHOLDER_IS_PUBLIC`: 筆頭株主1名のみが対象。Gemini判定で 7,153件 (19%) が TRUE
- `SHAREHOLDER_COMPOSITION_EXTEND` の TYPE: INDIVIDUAL / INSTITUTION / PRIVATE_CORP / FOREIGN_CUSTODIAN / TRUST_BANK / ASSET_MGMT のみ（`LISTED_CORP` は Plan B 後に追加）
- 問題③の観察例: キーエンス→ジャストシステム (4686)、桧家HD→日本アクア (1429) は PRIVATE_CORP 誤分類
- 自己株式は有価証券報告書 XBRL の大株主上位10名テーブルに原則出現しないが、Step B のゲート確認で実態を検証する

---

## Step A: EDA（Plan B 完了前でも実施可能）

### A-1. proxy EDA クエリ（BQ コンソールで直接実行）

Plan B 完了前は `EXTEND` テーブルに `LISTED_CORP` が存在しないため、
`STOCK_CODE_LIST.STOCK_NAME` との直接名前照合でプロキシとする。

```sql
-- proxy EDA: TOP10 に上場企業が含まれるか (名前完全一致) × TOB/非TOB分布
-- 実行場所: BQ コンソール / 課金: ~300MB 程度
WITH expanded AS (
  SELECT
    SC.TICKER,
    SC.FISCAL_YEAR_END,
    JSON_VALUE(entry, '$.name')                               AS entry_name,
    SAFE_CAST(JSON_VALUE(entry, '$.ratio') AS FLOAT64)        AS entry_ratio
  FROM `gmailpj-357912.STOCK.SHAREHOLDER_COMPOSITION` SC,
  UNNEST(JSON_QUERY_ARRAY(SC.TOP10_NAMES_JSON)) AS entry
  WHERE SC.TOP10_NAMES_JSON IS NOT NULL
),
listed_match AS (
  SELECT
    E.TICKER,
    E.FISCAL_YEAR_END,
    LOGICAL_OR(SCL.TICKER IS NOT NULL)                        AS has_listed_corp_proxy,
    IFNULL(SUM(IF(SCL.TICKER IS NOT NULL, E.entry_ratio, 0)), 0) AS listed_corp_ratio
  FROM expanded E
  LEFT JOIN `gmailpj-357912.STOCK.STOCK_CODE_LIST` SCL
    ON SCL.STOCK_NAME = E.entry_name
    AND E.TICKER != SCL.TICKER   -- 自己株除外（名前が自社と一致するケース）
  GROUP BY E.TICKER, E.FISCAL_YEAR_END
),
labels AS (
  SELECT TICKER, EXTRACT(YEAR FROM TOB_ANNOUNCEMENT_DATE) AS tob_year
  FROM `gmailpj-357912.STOCK.DELISTED_STOCKS`
  WHERE IS_PAPER_TOB_LABEL = TRUE
),
base AS (
  SELECT
    LM.TICKER,
    EXTRACT(YEAR FROM LM.FISCAL_YEAR_END) AS fiscal_year,
    LM.has_listed_corp_proxy,
    LM.listed_corp_ratio,
    (L.TICKER IS NOT NULL) AS is_tob
  FROM listed_match LM
  LEFT JOIN labels L
    ON L.TICKER = LM.TICKER
    AND L.tob_year = EXTRACT(YEAR FROM LM.FISCAL_YEAR_END) + 1
  WHERE EXTRACT(YEAR FROM LM.FISCAL_YEAR_END) BETWEEN 2016 AND 2024
)
SELECT
  has_listed_corp_proxy,
  COUNT(*)                                       AS n,
  SUM(CAST(is_tob AS INT64))                     AS n_tob,
  ROUND(AVG(CAST(is_tob AS FLOAT64)) * 100, 2)  AS tob_rate_pct
FROM base
GROUP BY has_listed_corp_proxy
ORDER BY has_listed_corp_proxy
```

**判定基準**:
- `has_listed_corp_proxy=TRUE` の `tob_rate_pct` が FALSE より低い → 安定株主＝買収防衛の負相関。因子の方向は「防衛要因」
- 高い → 戦略的再編の前兆として正相関。どちらでも RF は学習可能
- 差が 0.5pt 未満 → 因子の予測力が小さい可能性。SHAP で Walk-Forward 後に最終判断

### A-2. 既存 `top_shareholder_is_public` との相関確認クエリ

```sql
SELECT
  SC.TOP_SHAREHOLDER_IS_PUBLIC,
  LM.has_listed_corp_proxy,
  COUNT(*) AS n,
  ROUND(AVG(CAST(is_tob AS FLOAT64)) * 100, 2) AS tob_rate_pct
FROM (
  -- 上の listed_match CTE と同一ロジック。一体化して実行すること
  SELECT E.TICKER, E.FISCAL_YEAR_END,
    LOGICAL_OR(SCL.TICKER IS NOT NULL) AS has_listed_corp_proxy,
    (L.TICKER IS NOT NULL) AS is_tob
  FROM (
    SELECT SC2.TICKER, SC2.FISCAL_YEAR_END,
      JSON_VALUE(entry, '$.name') AS entry_name
    FROM `gmailpj-357912.STOCK.SHAREHOLDER_COMPOSITION` SC2,
    UNNEST(JSON_QUERY_ARRAY(SC2.TOP10_NAMES_JSON)) AS entry
    WHERE SC2.TOP10_NAMES_JSON IS NOT NULL
  ) E
  LEFT JOIN `gmailpj-357912.STOCK.STOCK_CODE_LIST` SCL
    ON SCL.STOCK_NAME = E.entry_name AND E.TICKER != SCL.TICKER
  LEFT JOIN (
    SELECT TICKER, EXTRACT(YEAR FROM TOB_ANNOUNCEMENT_DATE) AS tob_year
    FROM `gmailpj-357912.STOCK.DELISTED_STOCKS`
    WHERE IS_PAPER_TOB_LABEL = TRUE
  ) L ON L.TICKER = E.TICKER
    AND L.tob_year = EXTRACT(YEAR FROM E.FISCAL_YEAR_END) + 1
  WHERE EXTRACT(YEAR FROM E.FISCAL_YEAR_END) BETWEEN 2016 AND 2024
  GROUP BY E.TICKER, E.FISCAL_YEAR_END, L.TICKER
) LM
JOIN `gmailpj-357912.STOCK.SHAREHOLDER_COMPOSITION` SC
  ON SC.TICKER = LM.TICKER AND SC.FISCAL_YEAR_END = LM.FISCAL_YEAR_END
GROUP BY SC.TOP_SHAREHOLDER_IS_PUBLIC, LM.has_listed_corp_proxy
ORDER BY 1, 2
```

**判定**: `TOP_SHAREHOLDER_IS_PUBLIC=TRUE` と `has_listed_corp_proxy=TRUE` の重複率が 60% 超 → 因子が高度に相関。Walk-Forward で AUC 改善しなければ追加しない。

### A-3. 自己株式の実態確認クエリ

```sql
-- 自社名が TOP10_NAMES_JSON に出現するケースを確認
SELECT SC.TICKER, SCL.STOCK_NAME, JSON_VALUE(entry, '$.name') AS entry_name,
  SAFE_CAST(JSON_VALUE(entry, '$.ratio') AS FLOAT64) AS ratio
FROM `gmailpj-357912.STOCK.SHAREHOLDER_COMPOSITION` SC,
UNNEST(JSON_QUERY_ARRAY(SC.TOP10_NAMES_JSON)) AS entry
JOIN `gmailpj-357912.STOCK.STOCK_CODE_LIST` SCL
  ON SCL.TICKER = SC.TICKER AND SCL.STOCK_NAME = JSON_VALUE(entry, '$.name')
LIMIT 20
```

**判定**: 0件 → 自己株除外フィルタ `E.TICKER != SCL.TICKER` は念の為のガードで実害なし。
1件以上 → 自己株がTOP10に混入している。フィルタが機能しているか確認。

---

## Step B: Plan B 完了ゲート確認

Plan B 完了後、以下のクエリで `LISTED_CORP` の件数を確認してから Step C に進む。

```sql
-- Plan B 完了確認
SELECT TYPE, COUNT(*) AS n
FROM `gmailpj-357912.STOCK.SHAREHOLDER_COMPOSITION_EXTEND`
GROUP BY TYPE ORDER BY n DESC
```

**合格条件**: `LISTED_CORP` が 50件以上存在すること（Plan B が推定する上場事業法人数）。
`株式会社キーエンス` と `株式会社桧家ホールディングス` が LISTED_CORP であること:

```sql
SELECT NAME, TYPE FROM `gmailpj-357912.STOCK.SHAREHOLDER_COMPOSITION_EXTEND`
WHERE NAME IN ('株式会社キーエンス', '株式会社桧家ホールディングス')
```

---

## Step C: `compute_owner_features.py` 変更

### C-1. `NEW_COLUMNS` にカラム追加

**該当**: `compute_owner_features.py:L32-L39`

```python
# before
NEW_COLUMNS = [
    bigquery.SchemaField("REAL_TOP_NAME", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("REAL_TOP_RATIO", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("REAL_TOP_TYPE", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("OWNER_COUNT_IN_TOP10", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("OWNER_RATIO_IN_TOP10", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("HAS_FAMOUS_INVESTOR", "BOOL", mode="NULLABLE"),
]

# after
NEW_COLUMNS = [
    bigquery.SchemaField("REAL_TOP_NAME", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("REAL_TOP_RATIO", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("REAL_TOP_TYPE", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("OWNER_COUNT_IN_TOP10", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("OWNER_RATIO_IN_TOP10", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("HAS_FAMOUS_INVESTOR", "BOOL", mode="NULLABLE"),
    bigquery.SchemaField("HAS_BUSINESS_PARTNER_INVESTOR", "BOOL", mode="NULLABLE"),
    bigquery.SchemaField("BUSINESS_PARTNER_RATIO_IN_TOP10", "FLOAT64", mode="NULLABLE"),
]
```

### C-2. `owner_agg` CTE に LISTED_CORP 集計を追加

**該当**: `compute_owner_features.py:L179-L185`（`update_sql` 文字列内の `owner_agg` CTE）

```sql
-- before
owner_agg AS (
    SELECT
      TICKER,
      FISCAL_YEAR_END,
      COUNTIF(entry_type IN ('INDIVIDUAL', 'ASSET_MGMT'))       AS OWNER_COUNT_IN_TOP10,
      IFNULL(SUM(IF(entry_type IN ('INDIVIDUAL', 'ASSET_MGMT'), entry_ratio, 0)), 0) AS OWNER_RATIO_IN_TOP10,
      LOGICAL_OR(entry_name IN (SELECT name FROM famous_investors)) AS HAS_FAMOUS_INVESTOR
    FROM entries_typed
    GROUP BY TICKER, FISCAL_YEAR_END
)

-- after
owner_agg AS (
    SELECT
      TICKER,
      FISCAL_YEAR_END,
      COUNTIF(entry_type IN ('INDIVIDUAL', 'ASSET_MGMT'))       AS OWNER_COUNT_IN_TOP10,
      IFNULL(SUM(IF(entry_type IN ('INDIVIDUAL', 'ASSET_MGMT'), entry_ratio, 0)), 0) AS OWNER_RATIO_IN_TOP10,
      LOGICAL_OR(entry_name IN (SELECT name FROM famous_investors)) AS HAS_FAMOUS_INVESTOR,
      LOGICAL_OR(entry_type = 'LISTED_CORP')                        AS HAS_BUSINESS_PARTNER_INVESTOR,
      IFNULL(SUM(IF(entry_type = 'LISTED_CORP', entry_ratio, 0)), 0.0) AS BUSINESS_PARTNER_RATIO_IN_TOP10
    FROM entries_typed
    GROUP BY TICKER, FISCAL_YEAR_END
)
```

### C-3. UPDATE SET 句に新カラムを追加

**該当**: `compute_owner_features.py:L134-L140`（`SET` 句）

```sql
-- before
SET
  SC.REAL_TOP_NAME        = derived.REAL_TOP_NAME,
  SC.REAL_TOP_RATIO       = derived.REAL_TOP_RATIO,
  SC.REAL_TOP_TYPE        = derived.REAL_TOP_TYPE,
  SC.OWNER_COUNT_IN_TOP10 = derived.OWNER_COUNT_IN_TOP10,
  SC.OWNER_RATIO_IN_TOP10 = derived.OWNER_RATIO_IN_TOP10,
  SC.HAS_FAMOUS_INVESTOR  = derived.HAS_FAMOUS_INVESTOR

-- after
SET
  SC.REAL_TOP_NAME                   = derived.REAL_TOP_NAME,
  SC.REAL_TOP_RATIO                  = derived.REAL_TOP_RATIO,
  SC.REAL_TOP_TYPE                   = derived.REAL_TOP_TYPE,
  SC.OWNER_COUNT_IN_TOP10            = derived.OWNER_COUNT_IN_TOP10,
  SC.OWNER_RATIO_IN_TOP10            = derived.OWNER_RATIO_IN_TOP10,
  SC.HAS_FAMOUS_INVESTOR             = derived.HAS_FAMOUS_INVESTOR,
  SC.HAS_BUSINESS_PARTNER_INVESTOR   = derived.HAS_BUSINESS_PARTNER_INVESTOR,
  SC.BUSINESS_PARTNER_RATIO_IN_TOP10 = derived.BUSINESS_PARTNER_RATIO_IN_TOP10
```

### C-4. derived サブクエリの SELECT に追加

**該当**: `compute_owner_features.py:L189-L197`（`SELECT ... FROM owner_agg OA LEFT JOIN real_top RT ...`）

```sql
-- before（末尾）
      OA.HAS_FAMOUS_INVESTOR
    FROM owner_agg OA
    LEFT JOIN real_top RT ...

-- after
      OA.HAS_FAMOUS_INVESTOR,
      OA.HAS_BUSINESS_PARTNER_INVESTOR,
      OA.BUSINESS_PARTNER_RATIO_IN_TOP10
    FROM owner_agg OA
    LEFT JOIN real_top RT ...
```

### C-5. dry-run モードに LISTED_CORP EDA を追加

**該当**: `compute_owner_features.py:L234-L239`（`dry-run` ブランチ）

```python
# before
if args.mode == "dry-run":
    print("=== dry-run: 有名投資家EDAのみ実行 (BQ変更なし) ===")
    build_famous_investors(client)
    print("\nALTER TABLE と UPDATE は --mode sample/full で実行されます。")
    return

# after
if args.mode == "dry-run":
    print("=== dry-run: 有名投資家EDA + LISTED_CORP件数確認 (BQ変更なし) ===")
    build_famous_investors(client)
    _eda_listed_corp(client)
    print("\nALTER TABLE と UPDATE は --mode sample/full で実行されます。")
    return
```

以下の関数を `build_famous_investors` の直後（L99の後）に追加する:

```python
def _eda_listed_corp(client: bigquery.Client) -> None:
    """LISTED_CORP TYPE の件数とTOP10出現分布を表示（変更なし）."""
    sql = f"""
    WITH expanded AS (
      SELECT SC.TICKER, SC.FISCAL_YEAR_END,
        JSON_VALUE(entry, '$.name') AS entry_name
      FROM `{SC_TABLE}` SC,
      UNNEST(JSON_QUERY_ARRAY(SC.TOP10_NAMES_JSON)) AS entry
      WHERE SC.TOP10_NAMES_JSON IS NOT NULL
    )
    SELECT
      COALESCE(SCE.TYPE, 'UNKNOWN') AS entry_type,
      COUNT(DISTINCT CONCAT(E.TICKER, CAST(E.FISCAL_YEAR_END AS STRING))) AS n_records,
      COUNT(DISTINCT E.TICKER) AS n_tickers
    FROM expanded E
    LEFT JOIN `{EXTEND_TABLE}` SCE ON SCE.NAME = E.entry_name
    WHERE COALESCE(SCE.TYPE, 'UNKNOWN') = 'LISTED_CORP'
    GROUP BY entry_type
    """
    df = client.query(sql).to_dataframe()
    print("\n=== LISTED_CORP 出現件数 (Plan B 完了確認) ===")
    print(df.to_string(index=False) if not df.empty else "LISTED_CORP=0件 → Plan B 未完了")
```

### C-5.5. Plan B ガード関数を追加（レビュー#1対応）

**該当**: `compute_owner_features.py` — `build_famous_investors` の直後に新関数追加、`compute_and_update()` 冒頭（ALTER TABLE後・UPDATE前）で呼び出す

```python
def _check_plan_b_complete(client: bigquery.Client) -> None:
    """Plan B 完了確認: LISTED_CORP が 50 件以上存在することを検証."""
    sql = f"""
    SELECT COUNT(*) AS n
    FROM `{EXTEND_TABLE}`
    WHERE TYPE = 'LISTED_CORP'
    """
    df = client.query(sql).to_dataframe()
    n = int(df["n"].iloc[0])
    if n < 50:
        raise RuntimeError(
            f"Plan B 未完了: LISTED_CORP={n}件 (基準: 50件以上)。"
            "Plan B を完了してから再実行してください。"
        )
    logger.info("plan_b_gate_passed", listed_corp_count=n)
```

`compute_and_update()` の冒頭に追加:
```python
# before（L101）
def compute_and_update(client: bigquery.Client, sample_tickers: list[str] | None = None) -> None:

# after: 関数先頭（ticker_filter 構築前）に呼び出しを追加
    _check_plan_b_complete(client)
```

> **目的**: Plan B 未完了状態での誤実行を防ぐ。`sample/full` 双方で実行前に LISTED_CORP 件数を確認し、50件未満なら `RuntimeError` で即停止。

### C-6. `verify_results` に新カラムを追加

**該当**: `compute_owner_features.py:L216-L224`

```python
# before
    sql = f"""
    SELECT TICKER, FISCAL_YEAR_END, REAL_TOP_NAME, REAL_TOP_TYPE,
           OWNER_COUNT_IN_TOP10, OWNER_RATIO_IN_TOP10, HAS_FAMOUS_INVESTOR
    FROM `{SC_TABLE}` ...

# after
    sql = f"""
    SELECT TICKER, FISCAL_YEAR_END, REAL_TOP_NAME, REAL_TOP_TYPE,
           OWNER_COUNT_IN_TOP10, OWNER_RATIO_IN_TOP10, HAS_FAMOUS_INVESTOR,
           HAS_BUSINESS_PARTNER_INVESTOR, BUSINESS_PARTNER_RATIO_IN_TOP10
    FROM `{SC_TABLE}` ...
```

### C-7. `main()` の sample_tickers を変更（レビュー#2対応）

**該当**: `compute_owner_features.py:L248-L249`（`sample` ブランチ）

```python
# before
        sample_tickers = ["9983", "4530", "4917", "7203"]
        print(f"\n--- sample: {sample_tickers} の4銘柄で確認 ---")

# after
        sample_tickers = ["4686", "1429", "9983", "4530"]  # ジャストシステム・日本アクア・ユニクロ・久光
        print(f"\n--- sample: {sample_tickers} の4銘柄で確認（4686・1429がTRUEなら Plan B/C 正常） ---")
```

> **理由**: smoke test で `HAS_BUSINESS_PARTNER_INVESTOR=TRUE` を確認できる銘柄（LISTED_CORP が実際に TOP10 に存在する 4686/1429）を含める必要がある。旧リストのマンダム(4917)・トヨタ(7203)は LISTED_CORP 判定確認に適しない。

---

## Step D: `train_rf.py` 変更

### D-1. `BINARY_FEATURES` に追加（26→27変数）

**該当**: `train_rf.py:L69-L74`

```python
# before
BINARY_FEATURES = [
    "has_activist",
    "top_shareholder_is_public",
    "real_top_is_individual",
    "has_famous_investor",
]

# after
BINARY_FEATURES = [
    "has_activist",
    "top_shareholder_is_public",
    "real_top_is_individual",
    "has_famous_investor",
    "has_business_partner_investor",
]
```

> **変数数判断**: 27変数 << 学習サンプル数（~3,850行/年 × 5年≈19,250行、正例~750件）。追加可。

> **`business_partner_ratio_in_top10` の CONTINUOUS 追加は Step F の Walk-Forward AUC を見て判断。
> 初回は BINARY のみで実装し、改善不十分な場合に追加を検討する。**

### D-2. `_load_shareholders` クエリに新カラムを追加

**該当**: `train_rf.py:L133-L143`

```python
# before
def _load_shareholders(client: bigquery.Client) -> pd.DataFrame:
    sql = """
    SELECT
      TICKER, FISCAL_YEAR_END,
      TOP_SHAREHOLDER_RATIO, TOP_SHAREHOLDER_IS_PUBLIC,
      FOREIGN_RATIO, INDIVIDUAL_RATIO, FINANCIAL_INST_RATIO,
      OTHER_CORP_RATIO, TOP10_CONCENTRATION, HAS_ACTIVIST,
      REAL_TOP_TYPE, OWNER_COUNT_IN_TOP10, OWNER_RATIO_IN_TOP10, HAS_FAMOUS_INVESTOR
    FROM `gmailpj-357912.STOCK.SHAREHOLDER_COMPOSITION`
    """

# after
def _load_shareholders(client: bigquery.Client) -> pd.DataFrame:
    sql = """
    SELECT
      TICKER, FISCAL_YEAR_END,
      TOP_SHAREHOLDER_RATIO, TOP_SHAREHOLDER_IS_PUBLIC,
      FOREIGN_RATIO, INDIVIDUAL_RATIO, FINANCIAL_INST_RATIO,
      OTHER_CORP_RATIO, TOP10_CONCENTRATION, HAS_ACTIVIST,
      REAL_TOP_TYPE, OWNER_COUNT_IN_TOP10, OWNER_RATIO_IN_TOP10, HAS_FAMOUS_INVESTOR,
      HAS_BUSINESS_PARTNER_INVESTOR, BUSINESS_PARTNER_RATIO_IN_TOP10
    FROM `gmailpj-357912.STOCK.SHAREHOLDER_COMPOSITION`
    """
```

### D-3. `build_feature_matrix` に特徴量マッピングを追加

**該当**: `train_rf.py:L323-L328`（shareholder features ブロック末尾）

```python
# before（最終行）
        df["has_famous_investor"] = df["HAS_FAMOUS_INVESTOR"].astype("boolean").fillna(False).astype(int)

# after（追加）
        df["has_famous_investor"] = df["HAS_FAMOUS_INVESTOR"].astype("boolean").fillna(False).astype(int)
        df["has_business_partner_investor"] = (
            df["HAS_BUSINESS_PARTNER_INVESTOR"].astype("boolean").fillna(False).astype(int)
        )
        df["business_partner_ratio_in_top10"] = df["BUSINESS_PARTNER_RATIO_IN_TOP10"].fillna(0.0)
```

> `business_partner_ratio_in_top10` はモデルに使わなくても計算しておく（Step F で判断）。
> CONTINUOUS_FEATURES に追加する場合: 以下の **2箇所を同時に変更**すること（片方漏れ注意）:
> 1. `train_rf.py:L66` の `CONTINUOUS_FEATURES` リストに `"business_partner_ratio_in_top10"` を追記
> 2. `build_feature_matrix` 内の `keep_cols`（L337 相当: `["TICKER", "year", "label"] + CONTINUOUS_FEATURES + BINARY_FEATURES`）— `CONTINUOUS_FEATURES` 参照で自動更新されるため、リストを直接変更していなければ修正不要。ただし `keep_cols` をハードコードに変えた場合は手動追加が必要。

---

## Step E: 実行シーケンス

```powershell
# E-1. Plan B 完了ゲート確認（BQ コンソールで Step B クエリを実行）

# E-2. EDA 確認（変更なし）
PYTHONUTF8=1 uv run python scripts/tob_prediction/compute_owner_features.py --mode dry-run

# 期待出力:
# === LISTED_CORP 出現件数 (Plan B 完了確認) ===
# entry_type   n_records  n_tickers
# LISTED_CORP       XXXX       YYYY   ← 50件以上あること

# E-3. ALTER TABLE + sample 実行（4銘柄確認）
PYTHONUTF8=1 uv run python scripts/tob_prediction/compute_owner_features.py --mode sample

# 確認項目:
# - ジャストシステム(4686): HAS_BUSINESS_PARTNER_INVESTOR=TRUE（キーエンスが LISTED_CORP）
# - 日本アクア(1429): HAS_BUSINESS_PARTNER_INVESTOR=TRUE（桧家HDが LISTED_CORP）
# - ユニクロ(9983): 一般的な信託口が中心 → FALSEか確認
# - 久光(4530): 創業家（有限会社）が筆頭 → FALSE

# E-4. 全件更新
PYTHONUTF8=1 uv run python scripts/tob_prediction/compute_owner_features.py --mode full

# E-5. BQ キャッシュ削除（必須） + Walk-Forward 再実行
# ⚠️ shareholders.csv を削除してから --refresh を付けること。
# 削除せず --refresh なしで実行すると旧スキーマ CSV を読み込み KeyError でクラッシュする。
Remove-Item C:\tmp\tob_prediction\shareholders.csv
PYTHONUTF8=1 uv run python scripts/tob_prediction/train_rf.py --refresh
```

---

## Step F: 評価・検証

### F-1. Walk-Forward 合格基準

| 指標 | 基準 | 現行 |
|-----|------|------|
| ROC-AUC 平均 | >= 0.755（維持または向上） | 0.755 |
| PR-AUC 平均 | >= 0.079（維持または向上） | 0.079 |

### F-2. SHAP 確認（`train_rf.py` の feature importance ログで確認）

```python
# train_rf.py:L525 の importance ログを確認
# 目標: has_business_partner_investor が top20 に入ること（正負どちらでも可）
```

### F-3. 個別銘柄スコア変化確認

```powershell
# 2025年の予測確率を表示（久光・マンダム・ジャストシステムの順位変化）
PYTHONUTF8=1 uv run python scripts/tob_prediction/screen_tob.py --year 2025 --top-n 200
```

確認観点:
- ジャストシステム(4686): スコアに変化があるか（`has_business_partner_investor=TRUE` の影響）
- 久光(4530)・マンダム(4917): 依然 Top15% 未達であることを確認（本因子ではカバーしない）

### F-4. ROC-AUC が低下した場合の対処

- SHAP で `has_business_partner_investor` の寄与が極めて小さい（<0.001）→ 因子を除去して 26変数に戻す
- 低下幅 > 0.005 かつ SHAP 寄与小 → 除去確定
- 低下幅 <= 0.005 → 誤差範囲として許容

### F-5. `business_partner_ratio_in_top10` の追加判断

F-1 で ROC-AUC が横ばい（±0.003）の場合:
- CONTINUOUS_FEATURES に `business_partner_ratio_in_top10` を追加（28変数）して再評価
- AUC が 0.002 以上向上 → 採用。向上しない → 除外（BQ カラムは残す）

---

## Step G: ドキュメント更新

### G-1. `bq_shareholder_composition.md` にカラム追加

`docs/data_catalog/bq_shareholder_composition.md` のスキーマ表末尾（`HAS_FAMOUS_INVESTOR` の次行）に以下を追加:

```markdown
| HAS_BUSINESS_PARTNER_INVESTOR | BOOL | NULLABLE | TOP10 に LISTED_CORP TYPE の株主が1件以上含まれるか（Plan B/C 追加） |
| BUSINESS_PARTNER_RATIO_IN_TOP10 | FLOAT64 | NULLABLE | TOP10 中の LISTED_CORP TYPE 株主の合計保有比率 |
```

### G-2. `007_tob_ml_prediction.md` の Walk-Forward 結果表を更新

`007_tob_ml_prediction.md` §Random Forest 実装結果 の:
1. 特徴量数の記述: `26変数` → `27変数（またはXX変数）`
2. Walk-Forward 評価結果表を最新値で上書き
3. SHAP 重要度 top10 リストを更新（`has_business_partner_investor` のランクを追記）
4. 「既知の限界・宿題」セクション: Plan C 完了を追記

---

## 検証戦略

1. **smoke test**: `--mode sample`（4銘柄）で ジャストシステム(4686) が `HAS_BUSINESS_PARTNER_INVESTOR=TRUE` であること
2. **dev 実機**: `--mode full` → BQ 37,657行更新。`--refresh` で Walk-Forward 全年評価
3. **本番適用判断基準**: ROC-AUC >= 0.755 かつ SHAP で因子の寄与が確認できること
4. **回収手順**: AUC 低下時は `BINARY_FEATURES` から `has_business_partner_investor` を削除 → `--refresh` 再実行（BQ カラムは残す）

---

## 対応アンチパターン

| step | 004 |
|------|-----|
| C-2 (owner_agg) | §逐次永続化（SQL変更はatomic） |
| E-5 (--refresh 忘れ) | §破壊的操作（キャッシュ古いまま評価は禁止） |

> 参照: `docs/knowledges/tools/004_coding_conventions.md`

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/analysis/007_tob_ml_prediction.md`
- 依存プラン B: `docs/plans/analysis-007_tob_classify_listed_corp_20260517_175057.md`
- 問題整理: `docs/plans/analysis-007_tob_owner_filter_problems_20260517_175057.md`
- データカタログ: `docs/data_catalog/bq_shareholder_composition.md`

---

## 提出前セルフチェック

- [ ] 冒頭に基準 commit hash があるか（概要プランのため省略、実装時に記入）
- [ ] Step B ゲート確認を実施してから Step C に進むか
- [ ] `owner_agg` CTE の前後が揃っているか（derived SELECT にも追記したか）
- [ ] `_load_shareholders` クエリと `build_feature_matrix` の両方に追記したか
- [ ] `--refresh` フラグ付きで `train_rf.py` を実行しているか
- [ ] ジャストシステム(4686) の smoke test を実施したか

---

## 実装記録（実装後に記入）

**実装 commit**: — YYYY-MM-DD HH:MM JST
**検証結果**:
- smoke test: 未実施
- dev 実機: 未実施（Walk-Forward ROC-AUC: —）
- 本番適用: 未適用

**Walk-Forward 評価結果（27変数）**:

| 評価年 | ROC-AUC | PR-AUC | has_business_partner_investor SHAP rank |
|--------|---------|--------|-----------------------------------------|
| 2022 | — | — | — |
| 2023 | — | — | — |
| 2024 | — | — | — |
| 2025 | — | — | — |
| **平均** | **—** | **—** | |

---

## 実装後チェック

- [ ] ステータスを「完了」に更新
- [ ] 実装 commit hash を記録
- [ ] Walk-Forward 結果表を記入
- [ ] `007_tob_ml_prediction.md` の特徴量数・Walk-Forward 表・SHAP リストを更新
- [ ] `bq_shareholder_composition.md` に新カラムを追記
- [ ] 完了プランを `docs/plans/archive/202605/` に移動

---

## レビュー追記: 2026-05-17 18:30 JST — code-reviewer

→ `docs/reviews/200_cr_tob_new_factor_business_partner.md`

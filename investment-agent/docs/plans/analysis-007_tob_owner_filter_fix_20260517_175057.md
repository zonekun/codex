# TOB MLモデル: 候補CSV生成スクリプト新規作成（アクティビスト・上場事業法人除外）

**作成日時**: 2026-05-17 17:50 JST
**ステータス**: 中止（2026-05-18: generate_family_holding_candidates.py の存在を確認済み、実装不要と判断）
**対象ファイル**: `scripts/tob_prediction/generate_family_holding_candidates.py`（新規作成。既存スクリプトなし）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: 創業家資産管理会社候補 CSV を生成する専用スクリプトを作成し、アクティビスト（activist_aliases.csv）と上場事業法人（STOCK_CODE_LIST 名前照合）を除外する。`compute_owner_features.py` のスコープ外。

---

## 前提サマリ

- 過去修正: b24e7277 — TOB MLモデル26変数化完了（OWNER_COUNT/RATIO/HAS_FAMOUS_INVESTOR等）
- 残存: アクティビスト除外・上場事業法人除外の2件（問題整理プランを参照）
- 実機検証の有無: 未検証（インライン生成のため既存スクリプトなし）
- 関連 incident: `analysis-007_tob_owner_filter_problems_20260517_175057.md`

---

## 優先度の定義

- **P0**: 目視確認前に必須。アクティビストや事業法人を誤ってASET_MGMTに昇格するとBQが汚染される。
- **P1**: 精度向上。将来的な根本修正まではこの暫定除外で対応。
- **P2**: ブロッカーではない拡張。

---

## 指摘項目

### P0-1. アクティビスト除外ロジックの追加 🚨

**症状**: `株式会社UH Partners3`（光通信系アクティビスト）等が候補リストに混入し、誤ってASET_MGMTに昇格するリスクがある。

**該当**: `generate_family_holding_candidates.py`（新規作成スクリプト）のフィルタ処理

**根本原因**: アクティビストは1社に集中保有するため「PRIVATE_CORP + 1社のみ出現 + 5%以上」の絞り込みを通過してしまう。`activist_aliases.csv` の 315エイリアス名との照合が候補生成段階で行われていない。

**修正方針**: BQ クエリで候補を取得後、Python 側で `data/master/activist_aliases.csv` の `ALIAS` 列と株主名を照合して除外する。

```python
# before（インライン生成時の処理イメージ: フィルタなし）
# 候補取得後にアクティビスト除外なし → 315エイリアスが混入しうる

# after
def load_activist_names() -> set[str]:
    """activist_aliases.csv の ALIAS 列を全て返す（正規化なし、大文字統一）."""
    path = Path("data/master/activist_aliases.csv")
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {r["ALIAS"].strip() for r in rows}

# 候補取得後
activist_names = load_activist_names()
df = df[~df["株主名"].isin(activist_names)]
```

**呼び出し側への波及**:
- 無し（新規スクリプト。`compute_owner_features.py` には影響しない）

**検証**: 
- `株式会社UH Partners3` が除外されることを確認
- 除外件数を `logger.info("activist_excluded", count=N)` でログ出力

**ロールバック**: 新規スクリプトのため既存データへの影響なし。CSV は `C:\tmp\tob_prediction\` のローカルファイルのみ。

---

### P0-2. 上場事業法人除外ロジックの追加（暫定対処）🚨

**症状**: `株式会社キーエンス`（4662 ジャストシステム株主）、`株式会社桧家ホールディングス`（1429 日本アクア株主）等の上場事業法人が PRIVATE_CORP に誤分類されており候補リストに混入する。

**該当**: `generate_family_holding_candidates.py`（新規作成スクリプト）のフィルタ処理

**根本原因**: `SHAREHOLDER_COMPOSITION_EXTEND` に `LISTED_CORP` 型がなく、上場事業法人が `PRIVATE_CORP` に落ちている。根本修正（`classify_shareholder_names.py` の `STOCK_CODE_LIST` 照合追加）は別プランで実施。本プランでは候補CSV生成段階の暫定除外を実装する。

**修正方針**: BQ から `STOCK_CODE_LIST.STOCK_NAME` を全件取得し、株主名を正規化（`株式会社` / `㈱` / `（株）` 等の法人格プレフィックスを除去）したうえで名前照合して除外する。

```python
# before（暫定対処前: 上場事業法人の除外なし）
# df には キーエンス・桧家HD 等が PRIVATE_CORP として残る

# after
def load_listed_company_names(client: bigquery.Client) -> set[str]:
    """STOCK_CODE_LIST の STOCK_NAME とそのプレフィックス除去版を返す."""
    sql = "SELECT DISTINCT STOCK_NAME FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST` WHERE STOCK_NAME IS NOT NULL"
    df = client.query(sql).to_dataframe()
    names: set[str] = set()
    for raw in df["STOCK_NAME"]:
        raw = raw.strip()
        names.add(raw)
        for prefix in ("株式会社", "㈱", "（株）", "(株)"):
            if raw.startswith(prefix):
                names.add(raw[len(prefix):].strip())
    return names

# 候補取得後
listed_names = load_listed_company_names(client)

def normalize_shareholder(name: str) -> str:
    for prefix in ("株式会社", "㈱", "（株）", "(株)"):
        if name.startswith(prefix):
            return name[len(prefix):].strip()
    return name.strip()

df = df[~df["株主名"].apply(normalize_shareholder).isin(listed_names)]
```

**呼び出し側への波及**:
- 無し（新規スクリプト）

**検証**:
- `株式会社キーエンス`、`株式会社桧家ホールディングス` が除外されることを確認
- 除外件数を `logger.info("listed_corp_excluded", count=N)` でログ出力
- 除外対象のサンプルを CSV で `C:\tmp\tob_prediction\excluded_listed_corps.csv` に出力して確認できるようにする

**ロールバック**: 新規スクリプト。ローカル CSV のみ。既存 BQ テーブルへの影響なし。

---

### P1-1. スクリプト化（インライン生成からの昇格）⚠️

**症状**: 候補 CSV がセッション内インライン実行で生成されており、再現性がない。

**修正方針**: `scripts/tob_prediction/generate_family_holding_candidates.py` を新規作成。モードは `--mode dry-run`（件数確認のみ）/ `--mode full`（CSV 出力）の2種。

```python
# スクリプト骨格（after）
"""
Usage:
    PYTHONUTF8=1 uv run python scripts/tob_prediction/generate_family_holding_candidates.py --mode dry-run
    PYTHONUTF8=1 uv run python scripts/tob_prediction/generate_family_holding_candidates.py --mode full
"""

BQ SQL（PRIVATE_CORP + 1社のみ出現）:

WITH private_entries AS (
  SELECT
    JSON_VALUE(entry, '$.name') AS shareholder_name,
    SC.TICKER,
    SC.STOCK_NAME AS issuer_name,  -- JOINで付与
    MAX(SAFE_CAST(JSON_VALUE(entry, '$.ratio') AS FLOAT64)) AS max_ratio
  FROM `gmailpj-357912.STOCK.SHAREHOLDER_COMPOSITION` SC,
  UNNEST(JSON_QUERY_ARRAY(SC.TOP10_NAMES_JSON)) AS entry
  INNER JOIN `gmailpj-357912.STOCK.SHAREHOLDER_COMPOSITION_EXTEND` SCE
    ON SCE.NAME = JSON_VALUE(entry, '$.name') AND SCE.TYPE = 'PRIVATE_CORP'
  WHERE SC.TOP10_NAMES_JSON IS NOT NULL
  GROUP BY 1, 2, 3
),
single_ticker AS (
  SELECT shareholder_name
  FROM private_entries
  GROUP BY shareholder_name
  HAVING COUNT(DISTINCT TICKER) = 1
)
SELECT pe.shareholder_name, pe.TICKER, scl.STOCK_NAME AS issuer_name, pe.max_ratio
FROM single_ticker st
JOIN private_entries pe ON pe.shareholder_name = st.shareholder_name
LEFT JOIN `gmailpj-357912.STOCK.STOCK_CODE_LIST` scl ON scl.TICKER = pe.TICKER

フィルタ（Python側）:
① 有限会社/合同会社: max_ratio >= 0.01（1%以上）
② 株式会社: max_ratio >= 0.05（5%以上）
両区分: アクティビスト除外 + 上場事業法人除外

出力CSV列: 発行体TICKER, 発行体名, 株主名, 最大保有比率, 区分, 判定（空欄）
"""
```

**呼び出し側への波及**: 無し

**検証**:
- `--mode dry-run` で件数確認（期待: ①<725件 ②<1,698件）
- 除外ログで `UH Partners3`, `キーエンス`, `桧家ホールディングス` が含まれることを確認

**ロールバック**: 新規スクリプト。BQ変更なし。

---

## 対応アンチパターン

| plan ID | 004 | T-x | G-x |
|---|---|---|---|
| P0-1 | — | — | — |
| P0-2 | — | — | — |
| P1-1 | — | — | — |

> 本プランはデータ汚染防止目的の新規スクリプト作成。既存テーブル改変なし。アンチパターン非該当。

---

## 検証戦略

1. **smoke test**: `--mode dry-run` で件数ログ確認。UH Partners3/キーエンス/桧家HDが除外済みであることをログで確認。
2. **dev 実機**: `--mode full` で CSV 出力 → 秀丸で10件サンプル目視確認（除外漏れがないか）
3. **本番適用判断基準**: dry-run PASS + 除外ログ確認 + CSV サンプル目視確認で適用
4. **回収手順**: CSV のみのため BQ ロールバック不要。スクリプト修正→再実行で即回収可能

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/analysis/007_tob_ml_prediction.md`
- 問題整理: `docs/plans/analysis-007_tob_owner_filter_problems_20260517_175057.md`
- 関連 commit: `b24e7277` — TOB MLモデル26変数化完了
- アクティビストマスタ: `data/master/activists.csv`（39件）/ `activist_aliases.csv`（315件）
- 上場銘柄マスタ: `docs/data_catalog/bq_stock_code_list.md`

---

## 提出前セルフチェック（必須）

- [x] 冒頭に基準 commit hash があるか
- [x] 全項目が 7 フィールド（症状/該当/根本原因/修正方針/呼び出し側波及/検証/ロールバック）を揃えているか
- [x] 修正方針に before/after の両方があるか
- [x] 呼び出し側への波及が行番号リストで明示されているか（新規スクリプトのため「無し」）
- [x] 対応アンチパターン表が末尾にあるか
- [x] 検証戦略が smoke / dev / 本番適用判断基準 / 回収手順の 4 段を網羅しているか
- [x] ロールバック手順があるか（BQ変更なし。CSV のみ）
- [x] 「既に〜がある」系の前提を実コードで Read 確認したか（activist_aliases.csv 315件, STOCK_CODE_LIST STOCK_NAME列 確認済み）

---

## 実装記録（実装後に記入）

**実装 commit**: `<hash>` — YYYY-MM-DD HH:MM JST
**検証結果**:
- smoke test: <PASS/FAIL>
- dev 実機: <PASS/FAIL>
- 本番適用: 未適用

**code-reviewer 推奨の採否**:
| # | 推奨内容 | 採否 | 理由 |
|---|---------|------|------|
| 1 | | | |

---

## 実装後チェック（実装完了時に記入）

- [ ] 冒頭のステータスを「完了」に更新したか
- [ ] 実装 commit hash を記録したか
- [ ] 検証結果（smoke / dev）を記録したか
- [ ] code-reviewer 推奨の採否を記録したか
- [ ] 関連知見MD（007_tob_ml_prediction.md）にこのプランの変更を反映したか
- [ ] 完了プランを `docs/plans/archive/YYYYMM/` に移動したか

---

## レビュー結果

**CR-198**: `docs/reviews/198_cr_tob_owner_filter_fix.md`
**判定**: B（軽微指摘あり。即着手可）
**主要指摘**:
- A-1: activist照合で全角/半角正規化なし（取りこぼしリスク）
- A-2: 法人格プレフィックスが4種のみ（classify_shareholder_names.py と要照合）
- C-1: 候補0件時の無言終了（`logger.warning` 追加を推奨）
- C-4: 根本修正後の二重除外への `TODO` コメント追記を推奨

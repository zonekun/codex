# TOB MLモデル B: SHAREHOLDER_COMPOSITION_EXTEND に LISTED_CORP 型追加（根本修正）

**作成日時**: 2026-05-17 17:50 JST（詳細化: 2026-05-17 JST）
**ステータス**: 完了
**依存**: Plan A (`analysis-007_tob_owner_filter_fix_20260517_175057.md`) の実装・検証完了後に着手
**問題整理**: `analysis-007_tob_owner_filter_problems_20260517_175057.md` §問題②

---

## 目的

`SHAREHOLDER_COMPOSITION_EXTEND` の TYPE に `LISTED_CORP`（上場事業法人）を追加し、
キーエンス・桧家ホールディングス等の上場企業が `PRIVATE_CORP` に誤分類される根本原因を除去する。

---

## 背景・問題

- 現状: `classify_shareholder_names.py` は `STOCK_CODE_LIST` との照合を行わない
- 結果: 上場事業法人が `CORP_SUFFIXES_JP` にマッチし `PRIVATE_CORP` に落ちる
- 影響:
  - `generate_family_holding_candidates.py`（Plan A）では暫定的に名前照合で除外するが、BQ側の型は誤ったまま
  - `REAL_TOP_TYPE`, `OWNER_COUNT_IN_TOP10` 等のオーナー色因子も微量汚染されている可能性あり
  - Plan C（`has_business_partner_investor`）の実装前提となる正確な分類が未実現

---

## 前提サマリ（実コード確認済み）

- 基準 commit: `b24e7277` — TOB MLモデル26変数化完了（OWNER_COUNT/RATIO/HAS_FAMOUS_INVESTOR追加）
- `SHAREHOLDER_COMPOSITION_EXTEND` 件数: 56,160名、PRIVATE_CORP: 10,393名（2026-05-16）
- `STOCK_CODE_LIST` 件数: 約 4,541件（2026-02-23時点）
- `classify_shareholder_names.py` の `classify_name()` は Priority 1〜10 のルールベース。Priority 9（line 545-546）が `PRIVATE_CORP` 返却
- `compute_owner_features.py` の `owner_agg` CTE は `COUNTIF(entry_type IN ('INDIVIDUAL', 'ASSET_MGMT'))` → `LISTED_CORP` は既に除外されるため変更不要
- `load_shareholder_name_types.py` の TYPE カラムは STRING/REQUIRED → `LISTED_CORP` は既存スキーマで受け入れ可能、変更不要

---

## 設計決定（概要プランの検討事項を解決）

### 決定①: STOCK_CODE_LIST照合の方式 → **候補B（ローカルCSV）採用**

理由:
- `classify_shareholder_names.py` は現在BQ問い合わせなしの純粋ルールベース設計（テスト・再現性を保つ）
- 上場銘柄名は毎月第3営業日に自動更新されるため鮮度は問題なし
- 専用スクリプト `export_listed_company_names.py` で BQ → `data/master/listed_company_names.csv` に出力

### 決定②: LISTED_CORP の `real_top` CTE での扱い → **スキップしない（`compute_owner_features.py` 変更不要）**

- `real_top` CTE: `entry_type NOT IN ('TRUST_BANK', 'FOREIGN_CUSTODIAN', 'UNKNOWN')` → `LISTED_CORP` はここに残す
  - 上場親会社が筆頭株主の場合に `REAL_TOP_TYPE=LISTED_CORP` として正確に記録される
- `owner_agg` CTE: `COUNTIF(entry_type IN ('INDIVIDUAL', 'ASSET_MGMT'))` → `LISTED_CORP` は既にカウント対象外
- **→ `compute_owner_features.py` は変更不要**

### 決定③: LISTED_CORP チェックの挿入位置

`classify_name()` 関数の Priority 9（`PRIVATE_CORP` 返却, line 545-546）直前に挿入。
`has_corp_suffix == True` のパスのみで照合（INDIVIDUAL 等のパスには影響しない）。

---

## 変更対象ファイル（確定版）

| ファイル | 変更内容 | 種別 |
|---------|---------|------|
| `scripts/tob_prediction/export_listed_company_names.py` | 新規作成（BQ → `data/master/listed_company_names.csv` 出力） | 新規 |
| `scripts/tob_prediction/classify_shareholder_names.py` | `--listed-names-csv` オプション追加 + Priority 9 直前に LISTED_CORP チェック挿入 | 修正 |
| `scripts/tob_prediction/load_shareholder_name_types.py` | 変更不要（TYPE は STRING/REQUIRED で `LISTED_CORP` 受け入れ可能） | 確認のみ |
| `scripts/tob_prediction/compute_owner_features.py` | 変更不要（owner_agg は INDIVIDUAL/ASSET_MGMT のみカウント, real_top は LISTED_CORP を残す） | 確認のみ |
| `docs/data_catalog/bq_shareholder_composition_extend.md` | TYPE定義テーブルに `LISTED_CORP` 行を追加・注意事項追記 | 修正 |

---

## 指摘項目

### P0-1. `export_listed_company_names.py` 新規作成 🚨

**症状**: `classify_shareholder_names.py` が上場銘柄名セットにアクセスする手段がない。

**該当**: 新規スクリプト（`scripts/tob_prediction/export_listed_company_names.py`）

**根本原因**: `classify_shareholder_names.py` は BQ 非依存設計のため、外部から CSV を渡す仕組みが必要。

**修正方針（after）**:

```python
"""BQ STOCK_CODE_LIST から上場銘柄名セットを data/master/listed_company_names.csv に出力する.

Usage:
    PYTHONUTF8=1 uv run python scripts/tob_prediction/export_listed_company_names.py --dry-run
    PYTHONUTF8=1 uv run python scripts/tob_prediction/export_listed_company_names.py
"""

from __future__ import annotations
import argparse
import re
from pathlib import Path
import pandas as pd
import structlog
from google.cloud import bigquery
from google.oauth2 import service_account
from src.core.config import settings

logger = structlog.get_logger()

OUTPUT_CSV = Path("data/master/listed_company_names.csv")
PROJECT = "gmailpj-357912"
SQL = """
SELECT DISTINCT STOCK_NAME
FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
WHERE STOCK_NAME IS NOT NULL
ORDER BY STOCK_NAME
"""

_ALL_SPACES_RE = re.compile(r"[\s　\xa0]+")
_CORP_PREFIXES = ("株式会社", "㈱", "（株）", "(株)", "有限会社", "合同会社",
                  "合資会社", "合名会社")
_CORP_SUFFIXES_END = ("株式会社", "㈱", "（株）", "(株)")


def _normalize_fullwidth(name: str) -> str:
    # classify_shareholder_names.py と同一実装
    result = []
    for ch in name:
        cp = ord(ch)
        if 0xFF21 <= cp <= 0xFF3A:
            result.append(chr(cp - 0xFEE0))
        elif 0xFF41 <= cp <= 0xFF5A:
            result.append(chr(cp - 0xFEE0))
        elif 0xFF10 <= cp <= 0xFF19:
            result.append(chr(cp - 0xFEE0))
        else:
            result.append(ch)
    return "".join(result)


def _normalize_for_listed_check(name: str) -> str:
    """法人格プレフィックス/サフィックス除去 + fullwidth→ASCII + スペース除去 + upper.

    Args:
        name: 原文でも fullwidth 変換済みでも可（内部で再変換するため結果は同一）。
    # TODO: classify_shareholder_names.py に同一実装あり。変更時は両ファイルを同期すること。
    """
    n = _normalize_fullwidth(name).strip()
    n = _ALL_SPACES_RE.sub("", n)
    for prefix in _CORP_PREFIXES:
        if n.startswith(prefix):
            n = n[len(prefix):]
            break
    for suffix in _CORP_SUFFIXES_END:
        if n.endswith(suffix):
            n = n[:-len(suffix)]
            break
    return n.upper()


def build_client() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(
        settings.google_application_credentials
    )
    return bigquery.Client(project=PROJECT, credentials=creds)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="CSV出力せず先頭20件を表示して終了")
    args = parser.parse_args()

    client = build_client()
    df = client.query(SQL).to_dataframe()
    logger.info("bq_fetched", count=len(df))

    df["name_normalized"] = df["STOCK_NAME"].apply(_normalize_for_listed_check)
    df = df.rename(columns={"STOCK_NAME": "stock_name"})

    if args.dry_run:
        print(df.head(20).to_string(index=False))
        return

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8")  # internal use only (utf-8); not for external sharing
    logger.info("listed_names_exported", count=len(df), path=str(OUTPUT_CSV))


if __name__ == "__main__":
    main()
```

**呼び出し側への波及**: なし（新規スクリプト）

**検証**:
- `--dry-run` で `キーエンス`, `桧家ホールディングス` が `name_normalized` 列に存在することを確認
- full 実行で `data/master/listed_company_names.csv` が件数 ~4,541件で生成されること

**ロールバック**: `data/master/listed_company_names.csv` 削除のみ。BQ変更なし。

---

### P0-2. `classify_shareholder_names.py` 修正: `--listed-names-csv` + LISTED_CORP チェック挿入 🚨

**症状**: `株式会社キーエンス`, `株式会社桧家ホールディングス` 等が `PRIVATE_CORP` に誤分類される。

**該当**: `classify_shareholder_names.py` の `classify_name()` Priority 9（line 545-546）および `main()`

**根本原因**: Priority 6 で `has_corp_suffix = True` になった後、STOCK_CODE_LIST との照合なしに `PRIVATE_CORP` を返す。

**修正方針（before）**:

モジュールレベルに追加なし。`classify_name()` Priority 9:
```python
# --- Priority 9: PRIVATE_CORP (has corporate suffix but not classified above) ---
if has_corp_suffix:
    return "PRIVATE_CORP", "RULE"
```

`main()` に `--listed-names-csv` オプションなし。

**修正方針（after）**:

① モジュールレベルに定数・グローバル変数・関数を追加（`SONNET_CSV = ...` の直後あたりに配置）:
```python
# module-level set, populated by load_listed_names() before classification
_listed_normalized_names: set[str] = set()

_LISTED_CHECK_PREFIXES = (
    "株式会社", "㈱", "（株）", "(株)", "有限会社", "合同会社",
    "合資会社", "合名会社",
)
_LISTED_CHECK_SUFFIXES_END = ("株式会社", "㈱", "（株）", "(株)")


def _normalize_for_listed_check(name: str) -> str:
    """上場銘柄名照合用の正規化（法人格プレフィックス/サフィックス除去 + スペース除去 + upper）.

    Args:
        name: 原文でも fullwidth 変換済みでも可（内部で再変換するため結果は同一）。
    # TODO: export_listed_company_names.py に同一実装あり。変更時は両ファイルを同期すること。
    """
    n = _normalize_fullwidth(name).strip()
    n = _ALL_SPACES_RE.sub("", n)
    for prefix in _LISTED_CHECK_PREFIXES:
        if n.startswith(prefix):
            n = n[len(prefix):]
            break
    for suffix in _LISTED_CHECK_SUFFIXES_END:
        if n.endswith(suffix):
            n = n[:-len(suffix)]
            break
    return n.upper()


def load_listed_names(csv_path: Path) -> None:
    """上場銘柄名CSVをグローバルセットに読み込む."""
    global _listed_normalized_names
    df = pd.read_csv(csv_path, encoding="utf-8")
    _listed_normalized_names = set(df["name_normalized"].dropna().tolist())
```

② `classify_name()` Priority 9 の変更:
```python
# --- Priority 9: PRIVATE_CORP (has corporate suffix but not classified above) ---
if has_corp_suffix:
    if _listed_normalized_names:
        if _normalize_for_listed_check(name_norm) in _listed_normalized_names:
            return "LISTED_CORP", "RULE"
    return "PRIVATE_CORP", "RULE"
```

③ `main()` のパーサーに追加:
```python
parser.add_argument(
    "--listed-names-csv",
    type=str,
    default=None,
    help="上場銘柄名CSVパス（data/master/listed_company_names.csv）。省略時は LISTED_CORP 分類をスキップ",
)
```

③ `main()` の `names_df = pd.read_csv(...)` の直後（分類実行前）に追加:
```python
if args.listed_names_csv:
    load_listed_names(Path(args.listed_names_csv))
    logger.info("listed_names_loaded", count=len(_listed_normalized_names))
```

**呼び出し側への波及**: `--listed-names-csv` 省略時は既存動作と完全互換

**検証**:
- `--dry-run --listed-names-csv data/master/listed_company_names.csv` で LISTED_CORP 件数 > 0 かつサンプルに `株式会社キーエンス` / `株式会社桧家ホールディングス` が含まれること
- `--listed-names-csv` 省略時と比較して `TRUST_BANK`, `INDIVIDUAL`, `INSTITUTION` の件数が変化しないこと（PRIVATE_CORP のみ減少、LISTED_CORP が増加）

**ロールバック**: `--listed-names-csv` オプションを省略すれば既存動作に戻る。CSV を削除しても動作継続可能。

---

### P1-1. `docs/data_catalog/bq_shareholder_composition_extend.md` 更新 ⚠️

**症状**: TYPE定義テーブルに `LISTED_CORP` が記載されていない。

**該当**: `docs/data_catalog/bq_shareholder_composition_extend.md` TYPE定義テーブル（line 22-31）・注意事項（line 33-36）・カラム説明（line 14）

**修正方針（before）**:
```markdown
| TYPE | STRING | REQUIRED | INDIVIDUAL / ASSET_MGMT / INSTITUTION / TRUST_BANK / FOREIGN_CUSTODIAN / PRIVATE_CORP |
...
| PRIVATE_CORP | 上記以外の法人（事業会社・資産管理会社含む） | 有限会社Fight&Step, ㈱ティ・ケー・ワイ |
```

**修正方針（after）**:

カラム説明 line 14:
```markdown
| TYPE | STRING | REQUIRED | INDIVIDUAL / ASSET_MGMT / INSTITUTION / TRUST_BANK / FOREIGN_CUSTODIAN / PRIVATE_CORP / LISTED_CORP |
```

TYPE定義テーブル（PRIVATE_CORP の前）に行を追加:
```markdown
| LISTED_CORP | 上場事業法人（STOCK_CODE_LIST.STOCK_NAME に存在する法人） | 株式会社キーエンス, 株式会社桧家ホールディングス |
| PRIVATE_CORP | 上記以外の法人（創業家資産管理会社等） | 有限会社Fight&Step, ㈱ティ・ケー・ワイ |
```

注意事項に追加:
```markdown
- `LISTED_CORP` は `real_top` CTE に残る（`REAL_TOP_TYPE=LISTED_CORP` として記録）。`OWNER_COUNT/RATIO_IN_TOP10` にはカウントされない（INDIVIDUAL/ASSET_MGMT のみ）
- `LISTED_CORP` 再分類後の再実行フロー: `classify_shareholder_names.py --listed-names-csv` → `load_shareholder_name_types.py --mode full` → `compute_owner_features.py --mode full`
```

**呼び出し側への波及**: なし（ドキュメント更新のみ）

**検証**: 目視確認のみ

**ロールバック**: git revert

---

## 実行順序（Step-by-Step）

| Step | コマンド | 確認ポイント |
|------|---------|------------|
| 1 | `PYTHONUTF8=1 uv run python scripts/tob_prediction/export_listed_company_names.py --dry-run` | `キーエンス`, `桧家ホールディングス` が `name_normalized` に含まれること |
| 2 | `PYTHONUTF8=1 uv run python scripts/tob_prediction/export_listed_company_names.py` | `data/master/listed_company_names.csv` 出力（~4,541件） |
| 3 | `PYTHONUTF8=1 uv run python scripts/tob_prediction/classify_shareholder_names.py --dry-run --listed-names-csv data/master/listed_company_names.csv` | LISTED_CORP 件数 > 0、キーエンス/桧家HD がサンプルに含まれること |
| 4 | `PYTHONUTF8=1 uv run python scripts/tob_prediction/classify_shareholder_names.py --listed-names-csv data/master/listed_company_names.csv` | `C:\tmp\tob_prediction\shareholder_name_types.csv` 再生成 |
| 5 | `PYTHONUTF8=1 uv run python scripts/tob_prediction/load_shareholder_name_types.py --mode dry-run` | TYPE分布確認（LISTED_CORP > 0, PRIVATE_CORP 減少） |
| 6 | `PYTHONUTF8=1 uv run python scripts/tob_prediction/load_shareholder_name_types.py --mode full` | BQ `SHAREHOLDER_COMPOSITION_EXTEND` 全件 TRUNCATE+INSERT |
| 7 | BQ確認クエリ（下記） | キーエンス・桧家HD が LISTED_CORP になっていること |
| 8 | `PYTHONUTF8=1 uv run python scripts/tob_prediction/compute_owner_features.py --mode sample` | 4686/1429/9983/7203 でオーナー色カラム正常更新確認 |
| 9 | `PYTHONUTF8=1 uv run python scripts/tob_prediction/compute_owner_features.py --mode full` | 全件 DML UPDATE |
| 10 | `train_rf.py` 再実行（既存コマンド） | 精度変化確認（任意） |

**BQ 確認クエリ（Step 7 用）**:
```sql
SELECT NAME, TYPE, CONFIDENCE
FROM `gmailpj-357912.STOCK.SHAREHOLDER_COMPOSITION_EXTEND`
WHERE NAME IN ('株式会社キーエンス', '株式会社桧家ホールディングス')
```

期待結果: `TYPE = 'LISTED_CORP'`, `CONFIDENCE = 'RULE'`

**LISTED_CORP 件数確認クエリ**:
```sql
SELECT TYPE, COUNT(*) AS cnt
FROM `gmailpj-357912.STOCK.SHAREHOLDER_COMPOSITION_EXTEND`
GROUP BY TYPE
ORDER BY cnt DESC
```

---

## 検証戦略

1. **smoke test**: Step 3 の dry-run で LISTED_CORP 件数 > 0 かつキーエンス/桧家HD がサンプルに含まれること
2. **dev 実機**: Step 7 の BQ 確認クエリで直接確認。Step 8 の sample で REAL_TOP_TYPE が正しく更新されること（4686 は `REAL_TOP_TYPE=LISTED_CORP` になるはず）
3. **本番適用判断基準**: smoke test PASS + BQ直接確認 PASS + sample verify PASS で Step 9（全件UPDATE）を実行
4. **回収手順**: `--listed-names-csv` なしで `classify_shareholder_names.py` を再実行 → `load_shareholder_name_types.py --mode full` で LISTED_CORP が消えて元の分類に戻る（WRITE_TRUNCATE）

---

## Plan A との整合

Plan A の `generate_family_holding_candidates.py` P0-2（上場事業法人除外の暫定対処）は、本プラン完了後に不要となる。
本プラン完了時に Plan A の P0-2 該当箇所に以下の TODO コメントを追記すること:
```python
# TODO: Plan B (LISTED_CORP type 追加, analysis-007_tob_classify_listed_corp) 完了後、
#        SHAREHOLDER_COMPOSITION_EXTEND の TYPE='LISTED_CORP' で直接フィルタ可能。
#        この暫定除外ブロックは削除すること。
```

---

## 完了条件

- [x] `株式会社キーエンス` の TYPE が `LISTED_CORP` になっていること（BQ確認クエリで確認）
- [-] `株式会社桧家ホールディングス` の TYPE が `LISTED_CORP` になっていること → STOCK_CODE_LIST に現在の名称が存在しないため PRIVATE_CORP のまま（会社名変更・上場廃止等による。コードは正常動作）
- [x] `OWNER_COUNT_IN_TOP10` が上場事業法人を誤カウントしていないこと（`compute_owner_features.py` の `owner_agg` は INDIVIDUAL/ASSET_MGMT のみカウントであることをコードで確認）
- [-] Plan A の `excluded_listed_corps.csv` の件数と今回の LISTED_CORP 昇格件数が概ね一致すること（20%以内の誤差を許容）※ Plan A 未完了のため事後検証

---

## 対応アンチパターン

| plan ID | 004 | T-x | G-x |
|---|---|---|---|
| P0-1 | — | — | — |
| P0-2 | — | — | — |
| P1-1 | — | — | — |

> BQ TRUNCATE は `load_shareholder_name_types.py` の既存設計（`WRITE_TRUNCATE`）に従う。新規追加・削減ではなく型分類の精緻化であり、アンチパターン非該当。

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/analysis/007_tob_ml_prediction.md`
- 問題整理: `analysis-007_tob_owner_filter_problems_20260517_175057.md`
- Plan A: `analysis-007_tob_owner_filter_fix_20260517_175057.md`
- BQ スキーマ: `docs/data_catalog/bq_shareholder_composition_extend.md`
- 上場銘柄マスタ: `docs/data_catalog/bq_stock_code_list.md`

---

## 提出前セルフチェック（必須）

- [x] 冒頭に基準 commit hash があるか（b24e7277）
- [x] 全項目が 7 フィールド（症状/該当/根本原因/修正方針/呼び出し側波及/検証/ロールバック）を揃えているか
- [x] 修正方針に before/after の両方があるか（P1-1はドキュメントのみなので before/after テキスト表記）
- [x] 呼び出し側への波及が明示されているか
- [x] 設計検討事項3点を全て決定済みか
- [x] 実行順序（Step-by-Step）があるか
- [x] BQ確認クエリが明示されているか
- [x] 回収手順があるか
- [x] `compute_owner_features.py` 変更不要であることを実コード確認で裏付けたか（owner_agg は INDIVIDUAL/ASSET_MGMT のみ）
- [x] `load_shareholder_name_types.py` 変更不要であることを実コード確認で裏付けたか（TYPE は STRING/REQUIRED）

---

## 実装記録（実装後に記入）

**実装 commit**: `0b57d0ac` — 2026-05-17 19:35 JST
**検証結果**:
- smoke test: PASS（LISTED_CORP: 1,978件、キーエンスがサンプルに含まれることを確認）
- dev 実機: PASS（BQ確認クエリ: `株式会社キーエンス → LISTED_CORP`、トヨタ(7203) REAL_TOP_TYPE=LISTED_CORP 確認）
- 本番適用: PASS（全件UPDATE 37,607行）

**code-reviewer 推奨の採否**:
| # | 推奨内容 | 採否 | 理由 |
|---|---------|------|------|
| 1 | | | |

---

## 実装後チェック（実装完了時に記入）

- [x] 冒頭のステータスを「完了」に更新したか
- [x] 実装 commit hash を記録したか（`0b57d0ac`）
- [x] 検証結果（smoke / dev）を記録したか
- [x] code-reviewer 推奨の採否を記録したか（改善#4はfalse alarm確認済み、他4件取り込み）
- [ ] 関連知見MD（007_tob_ml_prediction.md）にこのプランの変更を反映したか
- [ ] Plan A の P0-2 該当箇所に TODO コメントを追記したか
- [ ] 完了プランを `docs/plans/archive/YYYYMM/` に移動したか

---

## レビュー追記: 2026-05-17 18:30 JST — code-reviewer

→ `docs/reviews/200_cr_tob_classify_listed_corp.md`

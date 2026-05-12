---
name: 決算反応モデル学習データ除外管理
description: 特殊イベント銘柄（子会社統合・M&A・上場廃止手続中・データ欠損等）を線形モデル学習時に除外するレコード管理機構（GCS + CLI）
type: tools
作成日: 2026-04-15
ステータス: 有効
関連ファイル:
  - scripts/earnings_model/exclusion_manager.py
  - scripts/earnings_model/review_report.py
  - scripts/earnings_model/download_review_data.py
---

## 概要

決算反応モデル（線形モデル化検討中）の学習データから、特殊イベント銘柄（子会社統合・M&A・上場廃止手続中・データ欠損等）を除外するためのレコード管理機構。

**ポイント:**

- レポート表示上は全件維持（当たり/ハズレ判定もそのまま）。
- 学習時のフィルタ情報として「除外」「除外理由」列を併記する。
- 管理単位: `{ticker, predict_date}` ペア。
- 論理削除のみ（`removed_at` を埋める）。物理削除はしない。

## 蓄積先

- **GCS パス**: `gs://stock_data_1930932/earnings_model/earnings_reaction_exclusions/exclusions.json`
- **フォーマット**: JSON array

```json
[
  {
    "ticker": "3387",
    "predict_date": "20260414",
    "reason": "子会社統合（特殊イベント）",
    "added_at": "2026-04-15T22:29:54+09:00",
    "removed_at": null
  }
]
```

- `added_at` / `removed_at` は JST の ISO8601 タイムスタンプ（秒精度）。
- `removed_at=null` のレコードが「アクティブな除外」として扱われる。

## CLI 使い方

スクリプト: `scripts/earnings_model/exclusion_manager.py`

### 追加

```bash
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
    scripts/earnings_model/exclusion_manager.py add 3387 20260414 "子会社統合（特殊イベント）"
```

- 同じ `{ticker, predict_date}` でアクティブなレコードが既にある場合は WARNING を出して何もしない。
- `added_at` は実行時刻（JST）で自動付与。

### 一覧

```bash
# 全件
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
    scripts/earnings_model/exclusion_manager.py list

# 特定 predict_date でフィルタ
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
    scripts/earnings_model/exclusion_manager.py list --predict-date 20260414
```

- structlog で active / removed を混在表示。

### 削除（論理削除）

```bash
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
    scripts/earnings_model/exclusion_manager.py remove 3387 20260414
```

- アクティブなレコード（`removed_at=null`）の `removed_at` を実行時刻（JST）で埋める。
- アクティブなレコードが無い場合は WARNING を出して何もしない。

## review_report.py 側の挙動

`review_report.py` 起動時に `fetch_exclusions(predict_date)` が GCS から exclusions.json をDLし、対象 `predict_date` かつ `removed_at=null` のレコードのみ採用する（ローカルキャッシュなし）。

各レポート行の **末尾2列** に以下を追加:

| 列名 | 内容 |
|------|------|
| `除外` | `Y`（該当 `ticker × predict_date` が除外登録あり）または 空 |
| `除外理由` | 除外登録があれば `reason`、無ければ 空 |

- 既存の4分類（当たり/中立/ハズレ/要確認）やソート順（結果→|score|降順）は変更しない。
- 除外レコード自体はレポートから削除せず、列でマーキングするのみ。

## 学習時の利用方針

- 線形モデル学習時: `除外==Y` の行は学習対象から除外する。
- 再利用ヘルパー: `from scripts.earnings_model.exclusion_manager import get_active_exclusion_set` で `set[(ticker, predict_date)]` を取得できる。学習コードはこれで前処理時にフィルタする。
- NaN レコード（`actual_return` が NaN）の扱い: **学習時も除外する**（方向判定不能なので教師信号にならない）。補完はしない。

```python
# 学習時の利用例
from scripts.earnings_model.exclusion_manager import get_active_exclusion_set

excl = get_active_exclusion_set()  # 全 predict_date のアクティブ除外
df_train = df_all[
    ~df_all.apply(lambda r: (r['ticker'], r['predict_date']) in excl, axis=1)
    & df_all['actual_return'].notna()
]
```

## 認証

- GCS アクセスは既存スクリプトと同じ `service_account.Credentials.from_service_account_file("C:/gdrive/claude/investment-agent/keys/gcp-service-account.json")` + `project="and-and-and"` を使用。

# 外部API + キャッシュ
> 親: [`data_catalog.md`](../../data_catalog.md)

### (d) 外部API + キャッシュ

#### API系

| API | エンドポイント例 | キャッシュTTL | 備考 |
|-----|----------------|-------------|------|
| J-Quants | 株価日次、財務データ | 24時間 | JQUANTS_API_KEY 必要 |
| yfinance | 株価データ | 24時間 | API Key不要。BQ補完用 |

#### ファイルダウンロード系（`data/cache/` に保管）

| ファイル | 説明 | 更新頻度 | 備考 |
|---------|------|---------|------|
| `data/cache/yasai_price_maff.xlsx` | 農林水産省 野菜小売価格調査（現在） | 週次（手動） | 2021/4〜現在 |
| `data/cache/yasai_price_maff_past.xlsx` | 農林水産省 野菜小売価格調査（過去） | 固定（更新不要） | 2017/11〜2021/3 |

**`yasai_price_maff.xlsx` / `yasai_price_maff_past.xlsx` 詳細:**

| 項目 | 内容 |
|------|------|
| ソース | 農林水産省 食品価格動向調査（野菜） |
| 現在ファイルURL | `https://www.maff.go.jp/j/zyukyu/anpo/kouri/k_yasai/` からリンクされるExcel |
| 過去ファイルURL | `https://www.maff.go.jp/j/zyukyu/anpo/kouri/k_yasai/attach/xls/y_past-1.xlsx` |
| シート構成 | `価格`（円/kg）、`前週比`（比率）、`平年比`（過去5ヶ年平均比） |
| **採用シート** | **`平年比`**（季節性補正済み。1.0=平年並み、1.15=15%高騰） |
| 非採用シート | `前週比`（季節性未補正のため不適切） |
| 対象野菜品目 | キャベツ・ねぎ・はくさい・だいこん・ほうれんそう・レタス・きゅうり・トマト等（複数列） |
| 分析での使用方法 | 全品目の平年比を行平均 → **1階差分（Δ平年比）**で定常化して使用 |
| 更新方法 | 手動ダウンロード・上書き保存（自動取得スクリプトなし） |
| 関連分析 | `docs/knowledges/analysis/002_yasai_price_earnings_prediction.md` |

**Excel読み込み実装（元号日付のパース）:**

MAFFのExcelは日付列が元号形式（例: `令和３年１月`）のため、そのままpandasで読むと文字列になる。
以下のパターンで読み込み・変換する。

```python
import pandas as pd
import re

ERA_START = {
    "令和": pd.Timestamp("2019-05-01"),
    "平成": pd.Timestamp("1989-01-08"),
    "昭和": pd.Timestamp("1926-12-25"),
}

def parse_wareki(s: str) -> pd.Timestamp | None:
    """元号+年+月の文字列 → Timestamp。例: '令和３年１月' → 2021-01-01"""
    s = s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))  # 全角数字→半角
    m = re.match(r"(令和|平成|昭和)(\d+)年(\d+)月", s)
    if not m:
        return None
    era, year, month = m.group(1), int(m.group(2)), int(m.group(3))
    base = ERA_START[era]
    western_year = base.year + year - 1
    return pd.Timestamp(f"{western_year}-{month:02d}-01")

# Excelの読み込み（平年比シート）
# ヘッダー行が複数あるため header=None で読み込み、手動でスキップ
df_raw = pd.read_excel("data/cache/yasai_price_maff.xlsx",
                        sheet_name="平年比", header=None)
# 先頭2行: タイトル行（不要）。3行目以降がデータ
df = df_raw.iloc[2:].reset_index(drop=True)
df.columns = df_raw.iloc[1].values          # 2行目を列名に
df["date"] = df.iloc[:, 0].apply(lambda x: parse_wareki(str(x)))
df = df.dropna(subset=["date"])
```

**2ファイルの結合（過去 + 現在）:**

```python
past = pd.read_excel("data/cache/yasai_price_maff_past.xlsx", sheet_name="平年比", header=None)
curr = pd.read_excel("data/cache/yasai_price_maff.xlsx",      sheet_name="平年比", header=None)

# それぞれ同様にパースして pd.concat で縦結合
# 重複日付（2021/4前後のオーバーラップ）は drop_duplicates で除去
df_all = pd.concat([df_past, df_curr]).drop_duplicates(subset=["date"]).sort_values("date")
```

**探索したが不採用のデータソース（再探索不要）:**

| ソース | 不採用理由 |
|--------|-----------|
| 東京都中央卸売市場 日報CSV | 月次集計のみ利用可・データ期間短（2025/2〜） |
| WAGRI API | 日次・2019〜で優秀だが申請制（未取得。必要なら申請を検討） |
| cultivationdata.net API | MAFFと同一データ（重複） |
| 東京青果物情報センター | 有料会員制 |

---


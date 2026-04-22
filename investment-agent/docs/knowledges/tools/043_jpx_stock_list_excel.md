# JPX 上場銘柄一覧 Excel（data_j.xls）

**カテゴリ**: tools
**作成日**: 2026-03-14
**ステータス**: 有効
**関連ファイル**: `scripts/update_conse_rakuten.py`

## 概要

JPX（日本取引所グループ）が公開している上場銘柄一覧 Excel を直接ダウンロードして
プライム・スタンダード・グロース（内国株式）の銘柄コード一覧を取得する方法。
BQ `STOCK_CODE_LIST` の代替として使用可能（BQ接続問題の回避策にもなる）。

## ダウンロード URL

```
https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls
```

参照ページ: https://www.jpx.co.jp/markets/statistics-equities/misc/01.html

毎営業日更新される。

## 読み込みコード

```python
import io
import requests
import pandas as pd

url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
resp = requests.get(url, timeout=30)
resp.raise_for_status()
df = pd.read_excel(io.BytesIO(resp.content), dtype=str)

# TSEプライム・スタンダード・グロース（内国株式）に絞り込み
target_categories = {
    "プライム（内国株式）",
    "スタンダード（内国株式）",
    "グロース（内国株式）",
}
df = df[df["市場・商品区分"].isin(target_categories)]
tickers = sorted(df["コード"].str.zfill(4).tolist())
# → 約 3768 銘柄（2026-03-14時点）
```

## 主要カラム

| カラム名 | 内容 |
|---------|------|
| `コード` | 銘柄コード（数値または文字列。`str.zfill(4)` で4桁正規化） |
| `銘柄名` | 会社名 |
| `市場・商品区分` | プライム（内国株式）/ スタンダード（内国株式）/ グロース（内国株式）等 |
| `17業種区分` | 業種分類 |
| `33業種区分` | より詳細な業種分類 |
| `規模区分` | 大型・中型・小型 |

## BQ STOCK_CODE_LIST との対応

| JPX カラム | BQ カラム | 備考 |
|-----------|----------|------|
| `コード` | `TICKER` | zfill(4) で一致 |
| `市場・商品区分` | `MARKET_CATEGORY` | 値が完全一致 |
| `銘柄名` | `STOCK_NAME` | — |

## 使いどころ

- BQ 接続ができない環境（`sys.path.insert` によるSSL問題等）での代替
- Cloud Run 外のローカル実行で BQ クライアントを避けたい場合
- 常に最新の上場銘柄一覧が必要な場合（BQ は定期ロードに依存）

## 注意事項

- アルファベット付きコード（`130A`, `135A` 等）は文字列として読み込まれる。`dtype=str` 必須
- 外国株・ETF・REIT等も含まれるため `市場・商品区分` でフィルタすること
- JPX のURL構造が変わった場合はダウンロードURLを更新する

## 根拠・出典

- 2026-03-14 セッションで BQ hang 問題の回避策として採用
- `update_conse_rakuten.py` の銘柄リスト取得をBQからこの方式に変更済み

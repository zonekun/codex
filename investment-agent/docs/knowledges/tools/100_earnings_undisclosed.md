# 決算未発表会社一覧（menu_earnings_undisclosed.py）

**カテゴリ**: tools
**作成日**: 2026-05-12
**ステータス**: 有効
**関連ファイル**: `scripts/menu_earnings_undisclosed.py`
**PSメニュー**: [`023_powershell_menu.md`](023_powershell_menu.md)「決算未発表会社一覧」

---

## 概要

当日の決算発表予定（BQ `EARNINGS_DISCLOSURE_CALENDAR`）と TDNet 適時開示一覧を突合し、実行時刻までに決算短信が未開示の銘柄を一覧表示する。

## データソース

| ソース | 用途 | 取得方法 |
|--------|------|---------|
| `STOCK.EARNINGS_DISCLOSURE_CALENDAR` (RECORD_TYPE='S', CATEGORY='R') | 当日の決算発表予定 | BQ パラメータクエリ |
| `STOCK.STOCK_CODE_LIST` | 銘柄名補完 | BQ JOIN |
| TDNet 適時開示一覧 `I_list_NNN_YYYYMMDD.html` | 開示済み決算短信 | httpx + BeautifulSoup スクレイピング |

## ロジック

1. BQ から当日の決算発表予定を取得（DISCLOSURE_TIME <= 実行時刻）
2. TDNet 開示一覧を全ページ取得し、「決算短信」を含むタイトルの銘柄コードを抽出
3. 予定にあるが TDNet に未掲載の銘柄を表示

## 実行コマンド

```bash
PYTHONUTF8=1 <python> scripts/menu_earnings_undisclosed.py
```

PSメニューから選択して実行可能。番号は [`023_powershell_menu.md`](023_powershell_menu.md) を参照。

## 出力例

```
=== 決算未発表会社一覧（2026-05-12 15:23時点） ===

BQ: 決算発表予定を取得中...
  予定: 121件（15:23まで）
TDNet: 開示済み決算短信を取得中...
  開示済み: 133件

TICKER     予定  四半期     会社名
------------------------------------------------------------
  7183  11:00  本決算     あんしん保証
  2502  11:30  1Q      アサヒグループホールディングス
  8086  14:00  本決算     ニプロ

未発表: 3社 / 予定: 121社
```

## 注意事項

- **対象は当日のみ**。日付指定の引数はない
- カットオフ時刻は実行時刻（JST）を自動取得。15:00以降に実行すれば15:00予定分も含まれる
- ghostrader.net の予定時刻は概算のため、数十分のずれは正常
- TDNet スクレイピングは 0.5秒/ページ間隔。全ページ取得に数秒〜十数秒かかる
- TDNet の HTML 構造（CSSクラス `kjTime`, `kjCode`, `kjTitle`）に依存 → 構造変更時は `003_tdnet_official_scraping.md` と合わせて修正

## 依存

- `httpx`, `beautifulsoup4`, `lxml`, `google-cloud-bigquery`, `google-auth`（全て既存依存）

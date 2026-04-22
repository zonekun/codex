# 楽天証券 IFIS コンセンサス取得スクリプト

**カテゴリ**: tools
**作成日**: 2026-03-04
**ステータス**: 有効
**関連ファイル**: `scripts/update_conse_rakuten.py`

## 概要

楽天証券のIFIS業績ページ（iframe: stockFrame）から経常利益コンセンサスをSeleniumで取得し、BigQuery `STOCK.CONSENSUS` テーブルにロードする。全上場銘柄（TSE プライム・スタンダード・グロース）を対象とし、中断時に続きから再開できる。

## 実行方法

```bash
# 通常実行（中断時は自動再開）
PYTHONUTF8=1 python scripts/update_conse_rakuten.py

# 強制新規実行（再開状態を破棄）
PYTHONUTF8=1 python scripts/update_conse_rakuten.py --fresh
```

PowerShell メニュー（`claude-investment-agent.bat`）の項目3・4からも起動可能。

## 出力先

**BigQuery**: `gmailpj-357912.STOCK.CONSENSUS`

| カラム | 型 | 説明 |
|-------|----|------|
| DATAAT | DATE | 実行日（初回起動時の日付で固定） |
| TICKER | STRING | 銘柄コード（4桁） |
| FY | STRING | 決算期（YYYYMM） |
| QUARTER | STRING | 1Q / 2Q / 3Q / FY |
| PROFIT | INT64 | 経常利益コンセンサス（百万円） |
| TARGET | STRING | CURRENT（当期）/ NEXT（次期） |

## 再開機能

**状態ファイル**: `data/logs/conse_resume.json`

```json
{"dataat": "2026-03-04", "last_ticker": "7203"}
```

- 起動時に状態ファイルがあれば自動的に `last_ticker` の次から再開
- `dataat` は最初の実行日で固定（日をまたいでも変わらない）
- 完了時に状態ファイルを自動削除
- `--fresh` 指定時は状態ファイルを無視・削除して新規実行

## 銘柄リスト取得

**JPX 上場銘柄一覧 Excel** から直接取得（BQ不要・`043_jpx_stock_list_excel.md` 参照）:

```python
import io, requests, pandas as pd
url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
resp = requests.get(url, timeout=30)
df = pd.read_excel(io.BytesIO(resp.content), dtype=str)
target = {"プライム（内国株式）", "スタンダード（内国株式）", "グロース（内国株式）"}
df = df[df["市場・商品区分"].isin(target)]
tickers = sorted(df["コード"].str.zfill(4).tolist())  # 約3768銘柄
```

> **変更履歴**: 2026-03-14 に BQ 方式から JPX Excel 方式に変更 → 2026-03-15 に BQ 方式に戻す（JPX Excel はフォールバックとして残存）。

## トラブルシューティング

### venv を使うと BQ / pandas がハングする（根本原因：日本語パス）

**症状**: BQ クエリ / pandas import で無限にハングする（タイムアウトなし）

**根本原因**: venv が `G:\マイドライブ\...`（日本語パス）に置かれている

- Windows は日本語パスにある `.pyd`（コンパイル済み Python 拡張）を正常にロードできない
- `pandas`, `pyarrow` 等のコンパイル済み拡張が日本語パスからの DLL ロードでハング
- `google-cloud-bigquery` の import は `_pandas_helpers` → `pandas` と連鎖するため、BQ import 自体もハングする
- `certifi` の `cacert.pem` も日本語パスにあるため、venv の `requests` が SSL 証明書を読めない（`SSLEOFError`）

**調査で確認したこと（2026-03-14）**:
| 実行方法 | pandas import | BQ import | requests SSL |
|---------|--------------|-----------|-------------|
| システムPython（venv なし） | ✅ 正常 | ✅ 正常 | ✅ 正常 |
| システムPython + `sys.path.insert(0, venv)` | ❌ ハング | ❌ ハング | ❌ SSLEOFError |
| `.venv\Scripts\python.exe` 直接実行 | ❌ ハング | ❌ ハング | ❌ SSLEOFError |
| `uv run python` | ❌ ハング | ❌ ハング | `REQUESTS_CA_BUNDLE` で修正可 |
| `REQUESTS_CA_BUNDLE=C:/certifi/cacert.pem` uv run | ❌ ハング | ❌ ハング | ✅ 正常（でも BQ import が先にハング） |

**重要**: TLS 接続自体（`ssl + socket` で直接）は全パターンで TLSv1.3 で正常。urllib3 のバージョンも同じ（2.6.3）。SSL 問題の本質は certifi の **日本語パス** にある。

**なぜシステムPython だけ動くのか**:
- システムPython のパッケージは `C:\Users\zonekun\AppData\Local\Programs\Python\Python312\Lib\site-packages\` （ASCII パス）にある
- selenium / PIL / google.generativeai / bs4 / google-cloud-bigquery がすべてシステムPython にインストール済みのため、venv は不要

**修正方針**:
- `sys.path.insert` 行を削除（済）
- BQ 銘柄取得を JPX Excel 方式に変更（済）
- PowerShell メニューからはシステムPython で実行する（venv Python は使わない）
- 抜本的解決: venv を ASCII パスに移設（未実施）

## 画像認証への対応

楽天証券ログイン時に画像認証が表示された場合:

1. `C:\Users\zonekun\Dropbox\アプリ\kabucom\raku.txt` を作成
2. クリックすべきキーワードを1行ずつ記入して保存
3. スクリプトが自動検出してGemini API（gemini-2.5-flash）で画像を判定しクリック
4. ファイルは読み込み後に自動削除される

## 前提条件

- Chrome + ChromeDriver インストール済み
- `SeleniumProfile3` プロファイルで楽天証券にログイン済み（セッション維持）
- GCP サービスアカウントキー: `keys/gcp-service-account.json`

## 根拠・出典

- 2026-03-04 セッションで実装
- 元スクリプト: `C:\Users\zonekun\Dropbox\stock\py\zz_conse_raku.py` を取り込み・改修

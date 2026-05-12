# コンセンサス取得スクリプト（QUICK / IFIS 2ソース）

**カテゴリ**: tools
**作成日**: 2026-03-04
**更新日**: 2026-05-05
**ステータス**: 有効（RAKU廃止→QUICK/IFIS 2ソース体制に移行完了）
**関連ファイル**: `scripts/update_conse_quick.py`（QUICK）、`scripts/update_conse_ifis.py`（IFIS）、`scripts/lib_conse_csv_from_view.py`（CSV出力共通関数）
**計画**: `docs/plans/tools-022_consensus_load_20260505_125600.md`（QUICK 5項目対応テーブル再構成）

## 概要

コンセンサス予想を2ソースから取得し、BigQuery `STOCK.CONSENSUS` テーブルにロードする。全上場銘柄（TSE プライム・スタンダード・グロース）を対象とし、中断時に続きから再開できる。

| ソース | スクリプト | 取得方法 | 取得項目 | SOURCE列 |
|--------|-----------|----------|----------|----------|
| QUICK | `update_conse_quick.py` | Selenium（松井証券リサーチネット） | 売上高, 営業利益, 経常利益, 純利益, EPS | `QUICK` |
| IFIS | `update_conse_ifis.py` | requests（IFIS株予報直URL） | 経常利益のみ | `IFIS` |

下流クエリは `STOCK.V_CONSENSUS_MERGED` VIEW を参照すること（QUICK優先・IFIS ORD_PROFIT 補完のマージ済み）。

> **as-of参照**: `batch_rerun_predict.py` 等で過去時点コンセンサスが必要な場合は TVF `fn_consensus_merged_asof(target_date)` を使用。

## BQスキーマ

`data_catalog.md`「STOCK.CONSENSUS」セクションに詳細定義あり。要点:

| カラム | 型 | 説明 |
|--------|-----|------|
| DATAAT | DATE | 取得日 |
| TICKER | STRING | 銘柄コード（4桁） |
| FY | STRING | 決算期（YYYYMM） |
| QUARTER | STRING | `1Q` / `2Q` / `3Q` / `FY` |
| REVENUE | INTEGER | 売上高（百万円）。IFIS=NULL |
| OP_PROFIT | INTEGER | 営業利益（百万円）。IFIS=NULL |
| ORD_PROFIT | INTEGER | 経常利益（百万円） |
| NET_PROFIT | INTEGER | 純利益（百万円）。IFIS=NULL |
| EPS | FLOAT64 | EPS（円）。IFIS=NULL |
| SOURCE | STRING | `IFIS` / `QUICK` |

> **廃止カラム（2026-05-05）**: `PROFIT`（→ORD_PROFITにリネーム）、`TARGET`（→FY列で年度識別に一本化。当期/来期判定は読み取り側Python）

## ソース別データ特性

| SOURCE | QUARTER | 数値項目 | 年度 |
|--------|---------|---------|------|
| QUICK | FY のみ | 5項目すべて | 当期・来期・再来期 |
| IFIS | 1Q / 2Q / 3Q / FY | ORD_PROFIT のみ（他4列 NULL） | 当期のみ |

## 実行方法

### QUICK（松井証券リサーチネット）

```bash
# 通常実行（中断時は自動再開）
PYTHONUTF8=1 python scripts/update_conse_quick.py

# 強制新規実行（再開状態を破棄）
PYTHONUTF8=1 python scripts/update_conse_quick.py --fresh

# 特定銘柄のみ
PYTHONUTF8=1 python scripts/update_conse_quick.py --ticker 7203,9984

# dry-run
PYTHONUTF8=1 python scripts/update_conse_quick.py --dry-run
```

PowerShell メニュー（`claude-investment-agent.bat`）の項目5・6からも起動可能。
詳細: `docs/knowledges/tools/095_consensus_quick.md`

### IFIS（IFIS株予報）

```bash
# 通常実行（中断時は自動再開）
PYTHONUTF8=1 python scripts/update_conse_ifis.py

# 強制新規実行
PYTHONUTF8=1 python scripts/update_conse_ifis.py --fresh

# 特定銘柄のみ（カンマ区切り）
PYTHONUTF8=1 python scripts/update_conse_ifis.py --ticker 7203,9984

# dry-run（BQ/CSV保存せず抽出結果だけ表示）
PYTHONUTF8=1 python scripts/update_conse_ifis.py --dry-run
```

- Selenium不要（requests直接取得）
- IFIS株予報URL: `https://kabuyoho.ifis.co.jp/index.php?action=tp1&sa=report&bcode={code}`

PowerShell メニューの項目3・4からも起動可能。

## 出力先

**BigQuery**: `gmailpj-357912.STOCK.CONSENSUS` → 下流 VIEW `STOCK.V_CONSENSUS_MERGED`（QUICK優先・IFIS補完）

> **スキーマ・VIEW仕様・データ特性**: `data_catalog.md`「STOCK.CONSENSUS」セクションを必ず参照。

## CSV出力

両スクリプトとも、全銘柄のBQ INSERT完了後に `V_CONSENSUS_MERGED` VIEWからクエリし、C案 PERIOD_REL 判定を経てCSVを一括出力する（`lib_conse_csv_from_view.py` の `export_consensus_csv()` を使用）。

| ソース | CSV出力先 | フォーマット |
|--------|----------|-------------|
| QUICK | `C:\Users\zonekun\Dropbox\stock\py\conse_quick.csv` | ヘッダなし・cp932・6列 |
| IFIS | `C:\Users\zonekun\Dropbox\stock\py\conse_ifis.csv` | ヘッダなし・cp932・6列 |

6列: `TICKER, 1Q_CURRENT, 2Q_CURRENT, 3Q_CURRENT, FY_CURRENT, FY_NEXT`

> VIEWはQUICK優先・IFIS補完のマージ済みデータのため、どちらのスクリプトから実行しても同じVIEW結果が出力される。PERIOD_REL判定は `V_LATEST_DISCLOSURE` VIEW（`_derive_current_fy` パターン）から当期FYを導出し、`fy >= current_fy` でフィルタ。詳細は `docs/data_catalog/bq_fin_summary.md` §V_LATEST_DISCLOSURE。

## 再開機能

| ソース | 状態ファイル |
|--------|-------------|
| QUICK | `data/logs/conse_quick_resume.json` |
| IFIS | `data/logs/conse_ifis_resume.json` |

```json
{"dataat": "2026-05-05", "last_ticker": "7203"}
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

## 前提条件

### QUICK
- Edge + EdgeDriver インストール済み
- 既存Edgeプロファイル（松井証券認証済み）
- 松井証券口座（SMS OTP認証）
- 詳細: `docs/knowledges/tools/095_consensus_quick.md`

### IFIS
- requests のみ（Selenium不要）
- GCP サービスアカウントキー: `keys/gcp-service-account.json`

## トラブルシューティング

### venv を使うと BQ / pandas がハングする（根本原因：日本語パス）

**症状**: BQ クエリ / pandas import で無限にハングする（タイムアウトなし）

**根本原因**: venv が `G:\マイドライブ\...`（日本語パス）に置かれている場合に発生。`C:\venvs\investment-agent` に配置することで解決済み。

**対策**: `C:\venvs\investment-agent\Scripts\python.exe` を使用する（CLAUDE.md §実行環境参照）。

## 根拠・出典

- 2026-03-04: RAKU/IFIS 2ソース体制で初期実装
- 2026-05-05: QUICK追加・RAKU廃止・テーブル再構成（5項目対応、TARGET列廃止）
- 2026-05-12: C案FY判定を `V_LATEST_DISCLOSURE` VIEW に移行（`_derive_current_fy` パターン統一、FY開示済み銘柄の当期コンセンサス消失バグ修正）

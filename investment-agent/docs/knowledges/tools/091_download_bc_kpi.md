# download_bc_kpi.py 仕様

**カテゴリ**: tools
**作成日**: 2026-05-03
**ステータス**: 有効
**関連ファイル**: `scripts/download_bc_kpi.py`, `scripts/download_bc_kpi_v2.py`
**親MD**: [`042_monthly_disclosure_master.md`](042_monthly_disclosure_master.md)

---

## 概要

バフェットコード `/company/{code}/kpi` ページから月次 KPI 時系列データをスクレイプし `data/csv/bc_monthly_kpi.csv` に保存する。

## 出力

- **CSV**: `data/csv/bc_monthly_kpi.csv`（カラム: `ticker, year_month, field, value`、encoding: utf-8-sig）
- **GCS**: `gs://stock_data_1930932/csv/bc_monthly_kpi.csv`（中間保存 + 最終保存）

---

## 対象銘柄の決定（優先順）

1. `--tickers <T...>` — 指定銘柄のみ
2. `--targets-csv <path>` — CSV の先頭列を ticker として読み込み。成功した銘柄は CSV から行削除（消込）
3. 省略時 — GCS `monthly/meta/*/structure.json` が存在する全銘柄（`meta/monthly/{ticker}_extract_adapter.json` に `inactive_reason` がある銘柄は除外）

---

## CSV書き込みモード

| フラグ | モード | 挙動 |
|--------|--------|------|
| `--resume` あり | 追記（`"a"`） | 既存 CSV に ticker が**1行でもあれば**その ticker 全体をスキップ |
| `--resume` なし | 上書き（`"w"`） | 既存 CSV を全消去して新規作成 |

**注意**: `--resume` のスキップ判定は **ticker 単位**（year_month 単位ではない）。特定月の補完（例: 26・3だけ欠損）には使えない。`--resume` なしで実行 → バックアップとマージが必要。

---

## レートリミット

| タイミング | 待機時間 |
|-----------|---------|
| 1銘柄ごと | 10〜18秒ランダム（`WAIT_MIN` / `WAIT_MAX`） |
| 10社ごと（`BATCH_SIZE=10`） | 30〜60秒ランダム + GCS 中間保存 |
| 終了時 | GCS 最終保存（`finally` ブロック） |

---

## WAF対策

- Selenium + ローカル Chrome（`C:\Program Files\Google\Chrome\Application\chrome.exe`）
- `webdriver` プロパティ隠蔽（CDP `Page.addScriptToEvaluateOnNewDocument`）
- automation フラグ除去（`excludeSwitches: ["enable-automation"]`）
- WAF 画面検知: タイトルが `Human Verification` / `Verification Required` なら最大20秒待機
- タイムアウト → `[]` を返しデータなし扱い
- 例外で WAF キーワード検知 → 60秒待機、3回で中断（`--resume` で再開可能）

**TODO: WAFリトライ改善（未実装）**:
- `wait_waf` タイムアウトを WAF カウンタに加算する（現状は「データなし」素通り）
- WAF 検知後に同じ ticker を長めバックオフ（120-180秒）後にリトライ（最大2回）
- 3回即中断ではなく、バックオフ込みで連続N回失敗時に中断

---

## スクレイプ仕様

- ページ内最初の `<table>` のみ解析
- ヘッダー行（th のみ）: `YYYY年` + `N月` × 12 を認識
- データ行（th + td）: th[0] = メトリクス名、td = 各月の値
- 値パース: カンマ除去、`％` 除去、float 変換
- スキップ値: `-` / `－` / `—` / `N/A` / `na`
- スペーサー行（`kpi__table-spacer` クラス）は skip

---

## 実行コマンド例

```bash
# 全銘柄取得（GCS structure.json 全件）
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/download_bc_kpi.py

# 特定銘柄のみ
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/download_bc_kpi.py --tickers 3097 2294

# 中断後の再開
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/download_bc_kpi.py --resume

# CSV指定 + 消込
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/download_bc_kpi.py --targets-csv data/csv/bc_kpi_missing_2603.csv
```

---

## v2（Linux VM版）

`download_bc_kpi_v2.py` — Google Chrome + Xvfb + nodriver。1GB RAM 向け、`DISPLAY=:99` 必須。`claude-high-vm` 等のリモート実行用。

---

## BC KPI 2026-03 未取得銘柄（2026-05-03 取得試行）

`data/csv/bc_kpi_no_2603.csv` — BC側に2026-03データが存在しない104社のリスト（ticker / latest_ym / fields_at_latest）。

| latest_ym | 社数 | 見込み |
|-----------|------|--------|
| 2026-02 | 24 | BC更新遅延の可能性大。本運用 cron で再取得すれば拾える見込み |
| 2026-01 | 5 | 同上（1-2ヶ月遅延） |
| 2025-03〜2025-12 | 23 | 月次開示停止 or BC収集停止の可能性。個別確認要 |
| 〜2024-12 | 52 | 月次開示停止濃厚。`_excluded` 候補 |

**本運用時の扱い**: 定期実行で latest_ym=2026-02 の24社を優先再取得。2025年以前で止まっている銘柄は `_excluded` 検討対象。

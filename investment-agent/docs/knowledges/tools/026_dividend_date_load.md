# dividend_date_load.py - 配当データ取得・BQ格納

**カテゴリ**: tools
**作成日**: 2026-03-07
**ステータス**: 有効
**関連ファイル**: `scripts/dividend_date_load.py`

## 概要

yfinance から全銘柄の権利落ち日・権利付き最終日・配当額を取得して BigQuery の `STOCK.DIVIDEND_DATE` テーブルに MERGE (upsert) でロードするスクリプト。

## 使い方

```bash
# 全銘柄（BQ STOCK_CODE_LIST から取得）
PYTHONUTF8=1 python scripts/dividend_date_load.py

# 指定銘柄のみ
PYTHONUTF8=1 python scripts/dividend_date_load.py --ticker 7203 9984

# BQ 書き込みなしで動作確認
PYTHONUTF8=1 python scripts/dividend_date_load.py --dry-run
```

## スリープ設定と実行時間

### 適正値（4541銘柄・タイムアウト7200s 以内に収まる設定）

```python
SLEEP_MIN      = 0.1    # 銘柄間スリープ最小（秒）
SLEEP_MAX      = 0.3    # 銘柄間スリープ最大（秒）
BULK_INTERVAL  = 200    # この件数ごとに長めのスリープを挟む
BULK_SLEEP_MIN = 2.0    # バルクスリープ最小（秒）
BULK_SLEEP_MAX = 5.0    # バルクスリープ最大（秒）
REQUEST_TIMEOUT = 15    # yfinance リクエストタイムアウト（秒）
```

### 推定実行時間の計算方法

```
スリープ合計 = 銘柄数 × 平均スリープ + (銘柄数 / BULK_INTERVAL) × 平均バルクスリープ
            = 4541 × 0.2 + (4541 / 200) × 3.5
            ≈ 908s + 79s = 987s（約16分）

取得時間（無配スキップ含む）= 推定2〜3秒/銘柄（タイムアウトがない場合）
                           ≈ 全体で30〜40分以内
```

### タイムアウトを引き起こす設定（禁止）

```python
# ❌ 4541銘柄でタイムアウト7200s を超える
SLEEP_MIN      = 0.5    # スリープ合計だけで ~5900s になる
SLEEP_MAX      = 1.5
BULK_INTERVAL  = 50
BULK_SLEEP_MIN = 10.0
BULK_SLEEP_MAX = 20.0
```

計算：`4541 × 1.0 + (4541/50) × 15 = 4541 + 1362 = 5903s` のスリープだけで、実際の処理時間を加えると 7200s を超える。

## curl_cffi セッションの設定

yfinance のリクエストに Chrome の TLS フィンガープリントを偽装する。`REQUEST_TIMEOUT` を設定しないとリクエストがハングして全体がタイムアウトする原因になる。

```python
session = curl_requests.Session(impersonate="chrome124")
session.headers.update({"User-Agent": _UA})
session.timeout = REQUEST_TIMEOUT  # 必須
```

## BQ ロード方式

キー `(TICKER, EX_DATE)` で MERGE upsert。削除は行わない。

1. 一時テーブル（`_tmp_dividend_date_<timestamp>`）に WRITE_TRUNCATE でロード
2. MERGE SQL で本テーブルに upsert
3. 一時テーブルを削除

## Cloud Run Job 設定

| 項目 | 値 |
|------|----|
| タイムアウト | 7200s |
| メモリ | 512Mi |
| スケジュール | 月初 20:00 JST（`dividend-date-load-monthly`） |

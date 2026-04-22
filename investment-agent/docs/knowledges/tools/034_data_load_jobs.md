# データロード Cloud Run ジョブ群

**カテゴリ**: tools
**作成日**: 2026-03-08
**ステータス**: 有効
**関連ファイル**:
- `scripts/shina_margin_balance_load.py` + `docker/Dockerfile.shina-margin-balance-load` + `cloudbuild/cloudbuild.shina-margin-balance-load.yaml`
- `scripts/stock_code_list_load.py` + `docker/Dockerfile.stock-code-list-load` + `cloudbuild/cloudbuild.stock-code-list-load.yaml`
- `scripts/stock_price_load.py` + `docker/Dockerfile.stock-price-load` + `cloudbuild/cloudbuild.stock-price-load.yaml`
- `functions/dividend_date_scheduler/` — Cloud Functions（第3営業日チェック → dividend-date-load 起動）
- `functions/stock_code_list_scheduler/` — Cloud Functions（第3営業日チェック → stock-code-list-load 起動）

---

## Cloud Scheduler 全スケジューラー一覧（実態）

> 確認日: 2026-03-31。`gcloud scheduler jobs list --location=us-west1` の結果を反映。

| スケジューラー名 | Cloud Run Job | cron（JST） | 実行時刻 JST | 備考 |
|----------------|--------------|------------|------------|------|
| `tdnet-download-daily` | `tdnet-download` | `50 23 * * 1-5` | 平日 23:50 | GCS保存のみ |
| `tdnet-load-parallel-daily` | `tdnet-load-parallel` | `0 2 * * 2-6` | 火〜土 02:00 | BQ `TDNET_DOCUMENTS_ENHANCED`。DATE_MODE="y"（昨日分） |
| `edinet-download-daily` | `edinet-download` | `0 23 * * 1-5` | 平日 23:00 | GCS保存のみ |
| `edinet-delay-daily` | `edinet-delay` | `30 18 * * 1-5` | 平日 18:30 | 遅延開示の追加取得 |
| `edinet-load-parallel-daily` | `edinet-load-parallel` | `0 1 * * 2-6` | 火〜土 01:00 | BQ `IR_DOCUMENTS_ENHANCED` |
| `jquants-fin-summary-daily` | `jquants-fin-summary` | `0 21 * * *` | 毎日 21:00 | BQ `FIN_SUMMARY` |
| `is-holiday-daily` | `is-holiday` | `0 8 * * *` | 毎日 08:00 | 休日マスタ更新 |
| `shina-margin-balance-load-17` | `shina-margin-balance-load` | `0 17 * * 1-5` | 平日 17:00 | BQ `SHINA_RATES`, `MARGIN_BALANCE` |
| `shina-margin-balance-load-20` | `shina-margin-balance-load` | `0 20 * * 1-5` | 平日 20:00 | 同上（1日2回） |
| `stock-price-load-daily` | `stock-price-load` | `0 16 * * 1-5` | 平日 16:00 | BQ `STOCK_PRICE` + GCS + Dropbox |
| `index-price-load-scheduler` | `index-price-load` | `30 8 * * 1-5` (UTC) | 平日 17:30 | BQ `INDEX_PRICE` |
| `stock-price-jquants-load-scheduler` | `stock-price-jquants-load` | `0 8 * * 1-5` (UTC) | 平日 17:00 | BQ `STOCK_PRICE_JQUANTS` |
| `stock-code-list-load-monthly` | `stock-code-list-load` | `0 20 1-8 * *` | 毎月1〜8日 20:00 | Cloud Functions 経由（第3営業日チェック） |
| `dividend-date-load-monthly` | `dividend-date-load` | `0 20 1-8 * *` | 毎月1〜8日 20:00 | Cloud Functions 経由（第3営業日チェック） |
| `earnings-schedule-load-early` | `earnings-schedule-load` | `0 5 1-13 * 0-5` | 1-13日 05:00（土曜除く） | ghostrader.net → BQ + Dropbox |
| `earnings-schedule-load-late` | `earnings-schedule-load` | `0 5 18-31 * 0-5` | 18-31日 05:00（土曜除く） | 同上 |
| `earnings-actual-load-weekly` | `earnings-actual-load` | `0 5 * * 0` | 毎週日曜 05:00 | TDNET実績→BQ（--week: 過去7日分） |

**日次フロー**: `tdnet-download-daily`（23:50）→ GCS保存 → `tdnet-load-parallel-daily`（翌02:00）→ BQ格納

---

## ジョブ詳細一覧

| ジョブ名 | スクリプト | データソース | BQ テーブル | スケジュール |
|---------|-----------|------------|------------|------------|
| `shina-margin-balance-load` | `shina_margin_balance_load.py` | taisyaku.jp | `SHINA_RATES`, `MARGIN_BALANCE` | 平日 17:00 & 20:00 JST |
| `stock-code-list-load` | `stock_code_list_load.py` | JPX 東証銘柄リスト | `STOCK_CODE_LIST` | 毎月第3営業日 20:00 JST |
| `stock-price-load` | `stock_price_load.py` | yfinance | `STOCK_PRICE` + GCS + Dropbox | 平日 16:00 JST |
| `index-price-load` | `index_price_load.py` | J-Quants V2 + yfinance | `INDEX_PRICE` | 平日 17:30 JST |
| `stock-price-jquants-load` | `stock_price_jquants_load.py` | J-Quants V2 `/equities/bars/daily` | `STOCK_PRICE_JQUANTS` | 平日 17:00 JST |

---

## 各ジョブ詳細

### 1. shina-margin-balance-load（品貸料・貸借残高）

**処理内容**: taisyaku.jp から `shina.csv`（品貸料）と `zandaka.csv`（貸借残高）をダウンロードし、クレンジング後に BQ へ WRITE_APPEND。

**Image**: `us-west1-docker.pkg.dev/gmailpj-357912/shina/shina-margin-balance-load:latest`

**対応環境**: ローカル / Colab個人 / Colab Enterprise / Cloud Run Job（4環境）

```bash
# ビルド
gcloud builds submit --config cloudbuild/cloudbuild.shina-margin-balance-load.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source

# Job 作成（初回）
gcloud run jobs create shina-margin-balance-load \
  --image us-west1-docker.pkg.dev/gmailpj-357912/shina/shina-margin-balance-load:latest \
  --region us-west1 \
  --service-account bq-loader@gmailpj-357912.iam.gserviceaccount.com \
  --memory 512Mi --task-timeout 1800

# 手動実行
gcloud run jobs execute shina-margin-balance-load --region us-west1
```

---

### 2. stock-code-list-load（JPX 銘柄リスト）

**処理内容**: JPX が公開する東証銘柄リスト（CSV/Excel）を BeautifulSoup でスクレイピングしてダウンロードし、BQ `STOCK.STOCK_CODE_LIST` にロード（WRITE_TRUNCATE）。

**依存**: `notify.py`（メール通知・ログキャプチャ）

**Image**: `us-west1-docker.pkg.dev/gmailpj-357912/stock/stock-code-list-load:latest`

**対応環境**: Colab個人 / Colab Enterprise / Cloud Run Job（ローカルは cloudrun 扱い）

```bash
# ビルド
gcloud builds submit --config cloudbuild/cloudbuild.stock-code-list-load.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source

# Job 作成（初回）
gcloud run jobs create stock-code-list-load \
  --image us-west1-docker.pkg.dev/gmailpj-357912/stock/stock-code-list-load:latest \
  --region us-west1 \
  --service-account bq-loader@gmailpj-357912.iam.gserviceaccount.com \
  --memory 512Mi --task-timeout 1800

# 手動実行
gcloud run jobs execute stock-code-list-load --region us-west1
```

---

### 3. stock-price-load（yfinance 株価）

**処理内容**: yfinance で全銘柄の株価日次データを取得し、以下3箇所に保存：
1. GCS `gs://stock_data_1930932/stock_price/new/` （CSV）
2. BQ `STOCK.STOCK_PRICE`（WRITE_APPEND）
3. Dropbox（バックアップ）

**依存**: `notify.py`、`yfinance`、`dropbox`、`jpholiday`

**Image**: `us-west1-docker.pkg.dev/gmailpj-357912/stock/stock-price-load:latest`

**対応環境**: Colab個人 / Colab Enterprise / Cloud Run Job

```bash
# ビルド
gcloud builds submit --config cloudbuild/cloudbuild.stock-price-load.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source

# Job 作成（初回）
gcloud run jobs create stock-price-load \
  --image us-west1-docker.pkg.dev/gmailpj-357912/stock/stock-price-load:latest \
  --region us-west1 \
  --service-account bq-loader@gmailpj-357912.iam.gserviceaccount.com \
  --memory 1Gi --task-timeout 3600

# 手動実行
gcloud run jobs execute stock-price-load --region us-west1
```

---

---

### 4. index-price-load（株価指数日次データ）

**処理内容**: J-Quants V2 API で全74指数（TOPIX等）、yfinance で日経225 OHLC を取得し BQ `STOCK.INDEX_PRICE` に WRITE_APPEND。BQ の既存日をチェックして重複回避。

**認証**: J-Quants V2 API → `JQUANTS_API_KEY` 環境変数（`x-api-key` ヘッダー）。V1 の MAILADDRESS/PASSWORD 方式は廃止。

**V2 API 仕様変更点**:
- エンドポイント: `/v1/indices` → `/v2/indices/bars/daily`、`/v1/indices/topix` → `/v2/indices/bars/daily/topix`
- レスポンスキー: `"indices"` / `"topix"` → `"data"`
- カラム名: `Open/High/Low/Close` → `O/H/L/C`（短縮形）
- プラン制限: 指数四本値は **Standard 以上** が必要（Free は取得不可）

**Image**: `us-west1-docker.pkg.dev/gmailpj-357912/stock/index-price-load:latest`

**スケジューラ**: `index-price-load-scheduler`（us-west1、`30 8 * * 1-5` UTC = 17:30 JST）

```bash
# ビルド
gcloud builds submit --config cloudbuild/cloudbuild.index-price-load.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source

# Job 更新（初回作成済み）
gcloud run jobs update index-price-load \
  --image us-west1-docker.pkg.dev/gmailpj-357912/stock/index-price-load:latest \
  --region us-west1

# 手動実行
gcloud run jobs execute index-price-load --region us-west1

# バックフィル（ローカル）
PYTHONUTF8=1 python scripts/index_price_load.py --backfill
PYTHONUTF8=1 python scripts/index_price_load.py --backfill --from 20260323 --to 20260327
```

---

---

### 5. stock-price-jquants-load（J-Quants 株価四本値）

**処理内容**: J-Quants `/v2/equities/bars/daily` から全銘柄の株価四本値（調整前・調整済み）を取得し、BQ `STOCK.STOCK_PRICE_JQUANTS` に DATE 単位で DELETE → WRITE_APPEND。

**認証**: `JQUANTS_API_KEY` 環境変数（`x-api-key` ヘッダー）

**Image**: `us-west1-docker.pkg.dev/gmailpj-357912/stock/stock-price-jquants-load:latest`

**スケジューラ**: `stock-price-jquants-load-scheduler`（us-west1、`0 9 * * 1-5` UTC = 18:00 JST）

```bash
# ビルド
gcloud builds submit --config cloudbuild/cloudbuild.stock-price-jquants-load.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source

# Job 作成（初回）
gcloud run jobs create stock-price-jquants-load \
  --image us-west1-docker.pkg.dev/gmailpj-357912/stock/stock-price-jquants-load:latest \
  --region us-west1 \
  --service-account bq-loader@gmailpj-357912.iam.gserviceaccount.com \
  --set-env-vars JQUANTS_API_KEY=<key> \
  --memory 512Mi --task-timeout 1800

# 手動実行
gcloud run jobs execute stock-price-jquants-load --region us-west1

# バックフィル（ローカル）
PYTHONUTF8=1 python scripts/stock_price_jquants_load.py --backfill
PYTHONUTF8=1 python scripts/stock_price_jquants_load.py --backfill --from 20260101 --to 20260327
```

---

## Cloud Functions スケジューラ（第3営業日トリガー）

### なぜ Cloud Functions を挟むか

Cloud Scheduler から Cloud Run Job を直接 HTTP トリガーで起動することも可能だが、
「**毎月第N営業日のみ実行**」というロジックは cron 式で表現できない。
そのため:

```
Cloud Scheduler（毎日 cron）
  → Cloud Functions（第3営業日かチェック）
      → 第3営業日のみ: Cloud Run Job API を呼び出して Job 起動
      → それ以外: 何もせず 200 を返す
```

この構成により、**cron では表現できない営業日ロジックを Python で実装**できる。

### 対象ジョブ

| Functions 名 | ファイル | 起動する Job | タイミング |
|-------------|---------|------------|----------|
| `dividend-date-scheduler` | `functions/dividend_date_scheduler/main.py` | `dividend-date-load` | 毎月第3営業日 |
| `stock-code-list-scheduler` | `functions/stock_code_list_scheduler/main.py` | `stock-code-list-load` | 毎月第3営業日 |

### 実装パターン（両 Functions 共通）

```python
def is_nth_business_day(n: int) -> bool:
    """今日が当月の第 n 営業日かどうかを返す."""
    today = datetime.datetime.now(JST).date()
    count = 0
    for day_num in range(1, today.day + 1):
        d = today.replace(day=day_num)
        if d.weekday() < 5 and not jpholiday.is_holiday(d) and d not in SPECIAL_DATES:
            count += 1
    return count == n
```

`SPECIAL_DATES` に年末年始などの特別休日を手動追加する（`jpholiday` が未対応のもの）。

### Cloud Functions デプロイ

```bash
# dividend_date_scheduler
gcloud functions deploy dividend-date-scheduler \
  --gen2 \
  --runtime python311 \
  --region us-west1 \
  --source functions/dividend_date_scheduler \
  --entry-point check_and_trigger \
  --trigger-http \
  --no-allow-unauthenticated \
  --service-account bq-loader@gmailpj-357912.iam.gserviceaccount.com

# stock_code_list_scheduler（同様）
gcloud functions deploy stock-code-list-scheduler \
  --gen2 \
  --runtime python311 \
  --region us-west1 \
  --source functions/stock_code_list_scheduler \
  --entry-point check_and_trigger \
  --trigger-http \
  --no-allow-unauthenticated \
  --service-account bq-loader@gmailpj-357912.iam.gserviceaccount.com
```

### SPECIAL_DATES の更新

年をまたぐ前に `SPECIAL_DATES` に新年の特別休日を追記する。

```python
SPECIAL_DATES = {
    datetime.date(2025, 12, 31),
    datetime.date(2026,  1,  2),
    datetime.date(2026,  1,  3),
    # 来年分を追加
    datetime.date(2026, 12, 31),
    datetime.date(2027,  1,  2),
    datetime.date(2027,  1,  3),
}
```

---

## 関連知見

- `docs/knowledges/tools/019_holiday_handling.md` — 休日判定の詳細
- `docs/knowledges/tools/020_nth_business_day_scheduler.md` — 第N営業日スケジューラ設計
- `docs/knowledges/tools/026_dividend_date_load.md` — dividend-date-load の詳細
- `docs/knowledges/tools/033_check_jobs.md` — ジョブ実行状況確認 CLI

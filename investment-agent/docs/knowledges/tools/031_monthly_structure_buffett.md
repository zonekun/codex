# 月次パイプライン Step1：構造収集 & buffett-code スクレイピング

**カテゴリ**: tools
**作成日**: 2026-03-08
**ステータス**: 有効
**関連ファイル**:
- `scripts/buffett_monthly_scrape.py` — TDnet BQ 月次構造収集（Cloud Run Job）
- `scripts/buffett_monthly_local.py` — buffett-code.com Selenium スクレイピング（ローカルのみ）
- `docker/Dockerfile.buffett-monthly` — buffett_monthly_scrape.py 用 Docker イメージ
- `cloudbuild/cloudbuild.buffett-monthly.yaml` — Cloud Build 設定

---

## 月次パイプライン全体像

```
Step 1a: buffett_monthly_scrape.py
         TDnet BQ → 月次開示企業一覧 + 項目構造
         → GCS monthlydata/{ticker}/structure.json
            GCS monthlydata/company_list.json

Step 1b: buffett_monthly_local.py（補完・参照用）
         buffett-code.com → KPI データ（Selenium ローカル実行）
         → GCS monthlydata/{ticker}/buffett_kpi.json

Step 2: monthly_data_load.py
        TDnet BQ チャンクテキスト → GCS monthlydata/{ticker}/{yyyy-mm}.json
        （structure.json の monthly_items を参照）
```

---

## buffett_monthly_scrape.py（Step 1a）

### 概要

`TDNET_DOCUMENTS_ENHANCED` の `MAIN_CATEGORY='月次開示'` から：
1. 月次開示企業の一覧（ticker・社名・最新提出日）を取得
2. 各企業の最新チャンクテキストを解析し「月次項目構造」を抽出
3. GCS に structure.json と company_list.json として保存

### GCS 出力

| パス | 内容 |
|------|------|
| `gs://stock_data_1930932/monthlydata/company_list.json` | 月次開示企業一覧（全社） |
| `gs://stock_data_1930932/monthlydata/{ticker}/structure.json` | 企業別 月次項目構造 |

**structure.json スキーマ:**
```json
{
  "ticker": "7049",
  "name": "識学",
  "scraped_at": "2026-03-08T09:00:00+09:00",
  "source": "tdnet_bq",
  "doc_count": 42,
  "latest_date": "2026-02-05",
  "latest_title": "2026年1月度 月次情報",
  "monthly_items": [
    {"name": "全店 売上", "unit": "百万円"},
    {"name": "既存店 売上", "unit": "百万円"}
  ]
}
```

### 月次項目抽出ロジック

チャンクテキストから `parse_monthly_items()` で最大30項目を抽出：
- パターン1: `項目名（単位）` 形式（円・百万・件・人・台・% など単位キーワード含む）
- パターン2: 日本語2〜25文字の行の直後に3桁以上の数値がある行をヘッダと判定

### 実行方法

```bash
# ローカル実行
PYTHONUTF8=1 python scripts/buffett_monthly_scrape.py

# Cloud Run Job デプロイ
gcloud builds submit --config cloudbuild/cloudbuild.buffett-monthly.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source

gcloud run jobs create buffett-monthly \
  --image us-west1-docker.pkg.dev/gmailpj-357912/tools/buffett-monthly:latest \
  --region us-west1 \
  --service-account bq-loader@gmailpj-357912.iam.gserviceaccount.com \
  --memory 512Mi \
  --task-timeout 1800

gcloud run jobs execute buffett-monthly --region us-west1
```

### 実行環境

| 環境 | 認証 |
|------|------|
| Cloud Run Job（`CLOUD_RUN_JOB` 環境変数あり） | ADC |
| ローカル | `keys/gcp-service-account.json` |

---

## buffett_monthly_local.py（Step 1b）

### 概要

buffett-code.com から Selenium で月次 KPI データを取得し GCS に保存する。
**ローカル Chrome 専用**（Cloud Run 非対応）。

### URL パターン

| 用途 | URL |
|------|-----|
| 月次開示企業一覧 | `https://www.buffett-code.com/monthly_reports` |
| KPI詳細 | `https://www.buffett-code.com/company/{code}/kpi` |

### 前提条件

- Windows ローカルに Chrome インストール済み（`C:\Program Files\Google\Chrome\Application\chrome.exe`）
- `selenium`, `webdriver-manager` インストール済み
- `keys/gcp-service-account.json` でGCS認証

### 実行方法

```bash
PYTHONUTF8=1 python scripts/buffett_monthly_local.py
```

### GCS 出力

```
gs://stock_data_1930932/monthlydata/{ticker}/buffett_kpi.json
```

### 注意事項

- buffett-code.com はログイン不要だがレートリミットに注意
- 533社対応（前回セッションで完走確認済み 2026-03頃）
- ローカル実行のみ。Cloud Run 化する場合は Headless Chrome + Selenium Grid が必要

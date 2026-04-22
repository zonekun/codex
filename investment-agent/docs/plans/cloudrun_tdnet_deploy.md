# Cloud Run Job デプロイ手順 - TDnet ダウンロード

**作成日**: 2026-02-25
**ステータス**: 未着手

## 構成概要

```
Cloud Scheduler（毎営業日 19:10 JST）
    ↓ トリガー
Cloud Run Job（tdnet-download）
    ↓ PDF取得 → GCS保存
gs://stock_data_1930932/tdnet/{code4}/{filename}.pdf
```

| 項目 | 値 |
|------|----|
| プロジェクト | `gmailpj-357912` |
| リージョン | `us-west1` |
| サービスアカウント | `bq-loader@gmailpj-357912.iam.gserviceaccount.com` |
| コンテナイメージ | `us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-download:latest` |

---

## 事前準備

```bash
# gcloud の認証・プロジェクト設定
gcloud auth login
gcloud config set project gmailpj-357912
```

---

## STEP 1: Artifact Registry リポジトリ作成

```bash
gcloud artifacts repositories create tdnet \
  --repository-format=docker \
  --location=us-west1 \
  --description="TDnet download job image"
```

---

## STEP 2: イメージのビルド & プッシュ（Cloud Build）

```bash
# investment-agent/ ディレクトリで実行
cd C:\Users\zonekun\Dropbox\claude\investment-agent

gcloud builds submit \
  --tag us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-download:latest \
  --dockerfile Dockerfile.tdnet \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source \
  .
```

> 再ビルド時も同じコマンドで上書きされる（`:latest` タグを使用）。

---

## STEP 3: IAM 設定（GCS 書き込み権限）

```bash
# bq-loader に GCS バケットへの objectAdmin 権限を付与
gcloud storage buckets add-iam-policy-binding gs://stock_data_1930932 \
  --member="serviceAccount:bq-loader@gmailpj-357912.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

# Cloud Run Job 実行権限（bq-loader が Cloud Run Jobs を呼べるよう）
gcloud projects add-iam-policy-binding gmailpj-357912 \
  --member="serviceAccount:bq-loader@gmailpj-357912.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

---

## STEP 4: Cloud Run Job 作成

```bash
gcloud run jobs create tdnet-download \
  --image us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-download:latest \
  --region us-west1 \
  --service-account bq-loader@gmailpj-357912.iam.gserviceaccount.com \
  --memory 512Mi \
  --cpu 1 \
  --task-timeout 3600 \
  --max-retries 1
```

---

## STEP 5: 手動実行テスト

```bash
# 今日分を実行（DATE_MODE="t" がデフォルト）
gcloud run jobs execute tdnet-download --region us-west1

# 特定日を指定して実行（--args で引数を上書き）
gcloud run jobs execute tdnet-download \
  --region us-west1 \
  --args="--from,20260224"

# 期間指定
gcloud run jobs execute tdnet-download \
  --region us-west1 \
  --args="--from,20260217,--to,20260224"
```

---

## STEP 6: ログ確認

```bash
# 最新の実行ログ
gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="tdnet-download"' \
  --limit 100 \
  --format "value(textPayload)" \
  --project gmailpj-357912

# GCS の保存確認
gcloud storage ls gs://stock_data_1930932/tdnet/ --recursive | head -20
```

---

## STEP 7: Cloud Scheduler 設定（定刻自動実行）

```bash
# Cloud Scheduler API を有効化（未有効の場合）
gcloud services enable cloudscheduler.googleapis.com

# スケジューラーのサービスアカウントに Cloud Run Job 実行権限
# （STEP 3 で run.invoker を付与済みなら不要）

# スケジュールジョブ作成
gcloud scheduler jobs create http tdnet-download-daily \
  --schedule "10 19 * * 1-5" \
  --time-zone "Asia/Tokyo" \
  --uri "https://us-west1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/gmailpj-357912/jobs/tdnet-download:run" \
  --http-method POST \
  --oauth-service-account-email bq-loader@gmailpj-357912.iam.gserviceaccount.com \
  --location us-central1
```

> **注**: Cloud Scheduler のロケーションは `us-central1` 固定（グローバルサービス）。
> Cloud Run Job 自体は `us-west1` で動く。

---

## イメージ更新手順（スクリプト変更時）

```bash
# 1. イメージを再ビルド & プッシュ
gcloud builds submit \
  --tag us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-download:latest \
  --dockerfile Dockerfile.tdnet \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source \
  .

# 2. Cloud Run Job に新しいイメージを反映
gcloud run jobs update tdnet-download \
  --image us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-download:latest \
  --region us-west1
```

---

## 関連ファイル

- `scripts/tdnet_download.py` — 実行スクリプト本体
- `docker/Dockerfile.tdnet` — コンテナビルド定義
- `docs/knowledges/tools/003_tdnet_download.md` — スクリプト使用方法
- `docs/knowledges/api/003_tdnet_official_scraping.md` — TDnet スクレイピング仕様

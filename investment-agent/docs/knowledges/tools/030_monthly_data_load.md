# 月次データロード（monthly-data-load）

**カテゴリ**: tools
**作成日**: 2026-03-08
**ステータス**: 有効
**関連ファイル**:
- `scripts/monthly_data_load.py` — メインスクリプト
- `docker/Dockerfile.monthly-data-load` — Docker イメージ定義
- `cloudbuild/cloudbuild.monthly-data-load.yaml` — Cloud Build 設定

---

## 概要

TDnet BQ テーブル（`TDNET_DOCUMENTS_ENHANCED`）から月次開示カテゴリの文書を取得し、
企業×月ごとの JSON ファイルとして GCS に保存する。月次パイプラインの Step 3。

---

## GCS 保存先

| パス | 内容 |
|------|------|
| `gs://stock_data_1930932/monthlydata/_progress.json` | 進捗管理ファイル |
| `gs://stock_data_1930932/monthlydata/{ticker}/{yyyy-mm}.json` | 月次データ本体 |

---

## 進捗管理（_progress.json）

`loaded_keys` リストで「どの ticker / 月まで取り込み済みか」を管理する。

```json
{
  "schema_version": 1,
  "note": "loaded_keys から行を削除するとその月がリランされます。形式: ticker/yyyy-mm",
  "loaded_keys": [
    "7049/2026-01",
    "7049/2025-12"
  ],
  "metadata": {
    "7049/2026-01": {
      "name": "識学",
      "submission_date": "2026-02-05",
      "doc_title": "2026年1月度 月次情報",
      "loaded_at": "2026-03-08T09:00:00+09:00"
    }
  }
}
```

**リランしたい月は `loaded_keys` から該当行を削除して再実行する。**

---

## 月次データ本体（{yyyy-mm}.json）

```json
{
  "ticker":          "7049",
  "name":            "識学",
  "yyyymm":          "2026-01",
  "submission_date": "2026-02-05",
  "doc_title":       "2026年1月度 月次情報",
  "file_name":       "tdnet_abc123.pdf",
  "doc_count":       1,
  "chunk_texts":     ["チャンク1テキスト", "チャンク2テキスト", ...],
  "monthly_items":   [],
  "loaded_at":       "2026-03-08T09:00:00+09:00",
  "source":          "tdnet_bq"
}
```

- `chunk_texts`: BQ の CHUNK_TEXT 列（複数チャンクを結合）
- `monthly_items`: 同ティッカーの `structure.json` から読み込む（存在しなければ空リスト）
- 同月に複数文書がある場合は最新 `submission_date` を primary とし、チャンクは全結合

---

## 実行方法

### ローカル実行

```bash
# 全期間（未ロード分のみ）
PYTHONUTF8=1 python scripts/monthly_data_load.py

# 期間指定
PYTHONUTF8=1 python scripts/monthly_data_load.py --from 2025-01 --to 2026-03

# ドライラン（書き込みなし、対象一覧のみ表示）
PYTHONUTF8=1 python scripts/monthly_data_load.py --dry-run
```

### Cloud Run Job デプロイ

```bash
# イメージビルド
gcloud builds submit --config cloudbuild/cloudbuild.monthly-data-load.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source

# Job 作成（初回）
gcloud run jobs create monthly-data-load \
  --image us-west1-docker.pkg.dev/gmailpj-357912/tools/monthly-data-load:latest \
  --region us-west1 \
  --service-account bq-loader@gmailpj-357912.iam.gserviceaccount.com \
  --memory 512Mi \
  --task-timeout 3600

# Job 実行（手動）
gcloud run jobs execute monthly-data-load --region us-west1

# 引数付き実行（--from/--to/--dry-run）
gcloud run jobs execute monthly-data-load --region us-west1 \
  --args="--from,2025-01,--to,2026-03"

# スケジュール設定（毎日 JST 7:00 = UTC 22:00 前日）
gcloud scheduler jobs create http monthly-data-load-daily \
  --location us-west1 \
  --schedule "0 22 * * *" \
  --uri "https://us-west1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/gmailpj-357912/jobs/monthly-data-load:run" \
  --http-method POST \
  --oauth-service-account-email bq-loader@gmailpj-357912.iam.gserviceaccount.com
```

---

## 実行環境対応

| 環境 | 判別条件 | 認証 |
|------|---------|------|
| Colab Personal | `google.colab` import 可 & `GOOGLE_CLOUD_PROJECT` 未設定 | `GCP_SA_KEY` Colab Secret |
| Colab Enterprise | `google.colab` import 可 & `GOOGLE_CLOUD_PROJECT` 設定済み | ADC |
| Cloud Run Job | `CLOUD_RUN_JOB` 環境変数あり | ADC |
| ローカル | その他 | `keys/gcp-service-account.json` |

---

## YYYYMM 抽出ロジック

文書タイトルから対象月を抽出する。抽出できない場合は提出日の前月をフォールバックとして使用。

| パターン | 例 |
|---------|-----|
| `(\d{4})年\s*(\d{1,2})\s*月` | 2026年1月度 月次情報 |
| `(\d{4})[/\-](\d{1,2})` | 2026/01, 2026-01 |
| `(\d{2})年\s*(\d{1,2})\s*月` | 26年1月（2000年代として処理） |

---

## BQ クエリ対象

```sql
SELECT TICKER, FILER_NAME, SUBMISSION_DATE, DOC_TITLE, FILE_NAME,
       ARRAY_AGG(CHUNK_TEXT ORDER BY CHUNK_INDEX) AS chunk_texts
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE MAIN_CATEGORY = '月次開示'
  AND TICKER IS NOT NULL
  AND FILE_NAME IS NOT NULL
GROUP BY TICKER, FILER_NAME, SUBMISSION_DATE, DOC_TITLE, FILE_NAME
ORDER BY SUBMISSION_DATE DESC
```

---

## 注意事項

- 進捗は 50 件処理ごとに中間保存される（Cloud Run タイムアウト対策）
- 同月に複数文書がある場合（修正版など）はすべてのチャンクを結合して保存
- `structure.json` は別途作成が必要（なければ `monthly_items: []` で保存される）
- `_progress.json` を手動編集してリランする場合は JSON 形式が崩れないよう注意

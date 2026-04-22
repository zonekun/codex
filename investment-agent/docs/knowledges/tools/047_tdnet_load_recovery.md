# TDnet ロード リカバリジョブ（tdnet-load-recovery）

**カテゴリ**: tools
**作成日**: 2026-03-15
**ステータス**: 有効
**関連ファイル**: `scripts/tdnet_load_recovery.py`, `docker/Dockerfile.tdnet-load-recovery`, `cloudbuild/cloudbuild.tdnet-load-recovery.yaml`

## 概要

`tdnet-load-parallel` の Quota 超過エラー（429）で BQ 未取込になったファイルを
再処理するリカバリ専用 Cloud Run Job。
`tdnet-load-parallel` は変更せず、リカバリ用に独立したイメージ・スクリプトとして管理する。

## tdnet-load-parallel との差異

| 設定 | tdnet-load-parallel | tdnet-load-recovery |
|------|--------------------|--------------------|
| MAX_WORKERS | 5 | **1**（シングルスレッド） |
| embedding batch_size | 5 | **20**（API コール数 1/4） |
| embedding sleep | 0.1s | **0.5s** |
| API 消費量 | 最大 1500 req/min（Quota 上限） | **約 120 req/min**（上限の 8%） |
| Docker イメージ | `tdnet/tdnet-load-parallel:latest` | `tdnet/tdnet-load-recovery:latest` |
| ログプレフィックス | `tdnet_load_to_bq_*_parallel_log.txt` | `tdnet_recovery_*_log.txt` |
| スキップログ | BQ 取込済みは `スキップ (BQ 取込済)` と出力 | 取込済みはサイレントスキップ（ログ省略） |

## Quota 問題の背景

- textembedding-gecko（us-west1）のデフォルト Quota: **1500 req/min**
- tdnet-load-parallel は MAX_WORKERS=5, batch_size=5, sleep=0.1s → 最大 1500 req/min
- 複数ジョブを同時起動すると即座に超過 → 429 Quota Exceeded エラー
- 2026-03-14 の 48 ジョブ同時起動で 2025 年全月にわたりエラー発生

## いつ使うか

- `tdnet-load-parallel` で 429 エラーが大量発生した後のリカバリ
- エラーファイルは BQ 未取込のため、同じ日付範囲で再実行すれば自動でスキップ＋リトライ
- 複数ジョブを同時起動しても 120 req/min × N ジョブ → N≦12 なら Quota 内

## デプロイコマンド

```bash
# イメージビルド・プッシュ
cd /c/gdrive/claude/investment-agent
gcloud builds submit --config cloudbuild/cloudbuild.tdnet-load-recovery.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source .

# Cloud Run Job 初回作成（作成済みの場合は不要）
gcloud run jobs create tdnet-load-recovery \
  --image us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-load-recovery:latest \
  --region us-west1 \
  --service-account bq-loader@gmailpj-357912.iam.gserviceaccount.com \
  --memory 2Gi --cpu 1 --task-timeout 604800 --max-retries 0
```

## 実行コマンド

```bash
# 特定月のリカバリ（1ジョブずつ順次実行推奨）
gcloud run jobs execute tdnet-load-recovery \
  --region us-west1 \
  --args="--from=20251101,--to=20251130"

# 複数月を同時実行する場合（2〜3ジョブまで）
gcloud run jobs execute tdnet-load-recovery --region us-west1 --args="--from=20251201,--to=20251231"
gcloud run jobs execute tdnet-load-recovery --region us-west1 --args="--from=20251101,--to=20251130"
# ↑ 2ジョブ同時でも 240 req/min → Quota 1500 の 16%、安全
```

## ログ確認

```bash
gcloud logging read \
  "resource.type=cloud_run_job AND resource.labels.job_name=tdnet-load-recovery" \
  --limit=50 --freshness=1d \
  --format="table(timestamp,textPayload)"
```

## 根拠・出典

- 2026-03-15 セッションで実装
- textembedding-gecko Quota: `docs/knowledges/tools/034_data_load_jobs.md` 参照
- 関連知見: `docs/knowledges/tools/027_jquants_mcp_server.md`（Cloud Run Job デプロイパターン）

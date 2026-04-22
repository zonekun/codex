# Cloud Run Job 2重トリガー検出（check-duplicate-triggers）

**カテゴリ**: tools
**作成日**: 2026-04-05
**ステータス**: 運用停止（2026-04-17）
**関連ファイル**:
- `scripts/check_duplicate_triggers.py`
- `docker/Dockerfile.check-duplicate-triggers`
- `cloudbuild/cloudbuild.check-duplicate-triggers.yaml`

## 運用停止の経緯（2026-04-17）

本ツールは「Cloud Scheduler の at-least-once delivery による2重発火」を監視する想定だったが、継続的に2重トリガーが観測されたのは `earnings-schedule-load` のみで、根本原因は GCP の仕様ではなく **unix-cron の設定ミス**だった（day-of-month と day-of-week の両指定が OR 結合される仕様を AND と誤解していた。詳細は `065_earnings_schedule_load.md` の「設計意図」）。

設定を修正して2重トリガーが解消したため、本監視ジョブは運用停止する。Scheduler `check-duplicate-triggers-daily` は削除。Cloud Run Job 本体・イメージは残置（再利用する可能性があれば再有効化）。

## 概要

全 Cloud Run Job の直近24時間の実行履歴を Cloud Run Admin API で取得し、同一ジョブが5分以内に2回以上起動された（2重トリガー）ケースを検出してメール通知する。

## 背景

Cloud Scheduler は **at-least-once delivery** のため、ネットワーク再送や GCP 内部リトライで同じスケジュールが2回発火することがある。DELETE → INSERT（バッチロード）のように非アトミックな冪等処理では、並行実行で重複データが生まれる。

ただし運用実績としては at-least-once による重複発火は観測されず、2重トリガーの全ケースは cron 設定ミス（DoM/DoW の OR 結合誤解）に由来していた。

## 監視対象

**全 Cloud Run Job を自動取得**（`run_v2.JobsClient.list_jobs()`）。ジョブの追加・削除時に本スクリプトの修正は不要。

## 判定ロジック

- 同一ジョブで `DUP_THRESHOLD_MIN`（5分）以内に2件以上の実行 → 2重トリガーと判定
- 検出時: メール通知（`send_mail`）。リカバリは手動で別途対応

## Cloud Run Job

- **ジョブ名**: `check-duplicate-triggers`
- **イメージ**: `us-west1-docker.pkg.dev/gmailpj-357912/tools/check-duplicate-triggers:latest`
- **リージョン**: us-west1
- **メモリ**: 512Mi / CPU: 1 / タイムアウト: 120s

## スケジュール

| スケジューラー | cron (JST) | 備考 |
|:--|:--|:--|
| `check-duplicate-triggers-daily` | `30 5 * * *` | 毎日 05:30。多くのジョブが 05:00 に走るため30分後に検査 |

## ビルド・デプ���イ

```bash
# ビルド
gcloud builds submit \
  --config cloudbuild/cloudbuild.check-duplicate-triggers.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source .

# ジョブ更新
gcloud run jobs update check-duplicate-triggers \
  --image us-west1-docker.pkg.dev/gmailpj-357912/tools/check-duplicate-triggers:latest \
  --region us-west1

# 手動実行
gcloud run jobs execute check-duplicate-triggers --region us-west1
```

## 2重トリガーを受けた場合のリカバリ

メールで通知されたジョブごとに個別対応が必要。対応例:

| ジョブ | リカバリ方法 |
|:--|:--|
| `earnings-schedule-load` | `dedup_scheduled()` で自動除去済み。追加対応不要 |
| その他 DELETE→INSERT 系 | BQ で重複確認 → 手動 dedup DELETE |
| 冪等なジョブ（is-holiday 等） | 対応不要（2回走っても結果は同じ） |

## TODO: Cloud Tasks による2重実行防止（様子見）

2重トリガーの検知頻度が多すぎる場合、Cloud Tasks の**重複排除（Deduplication）**機能で根本防止を検討する。

**方式**: Scheduler → Cloud Tasks API → Cloud Run Job に変更。タスク名に日時を含む一意識別子（例: `job-name-20260405-0500`）を付与し、同名タスクの再投入を 409 Conflict で拒否させる。

**判断基準**: 週に複数回の2重トリガーが継続的に発生する場合に導入を検討。現状は監視 + dedup DML で対応中。

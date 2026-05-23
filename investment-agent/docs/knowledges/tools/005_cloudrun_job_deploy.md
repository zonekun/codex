# Cloud Run Job デプロイ手順（汎用）

**カテゴリ**: tools
**作成日**: 2026-02-25
**ステータス**: 有効
**適用範囲**: プロジェクト内の Python バッチスクリプト全般

## 前提・共通設定

| 項目 | 値 |
|------|----|
| プロジェクト | `gmailpj-357912` |
| サービスアカウント | `bq-loader@gmailpj-357912.iam.gserviceaccount.com` |
| リージョン | `us-west1` |
| ビルド方法 | `gcloud builds submit`（ローカルに Docker 不要） |
| ソースステージング | `gs://gmailpj-357912-cloudbuild/source` （us-west1 単一リージョン。**必ず指定**） |

> **`--gcs-source-staging-dir` 省略禁止**: デフォルトだと `gmailpj-357912_cloudbuild`（US マルチリージョン）にソースが保存されコストがかかる。`gcloud builds submit` には常に `--gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source` を付与すること。

| イメージ置き場 | `us-west1-docker.pkg.dev/gmailpj-357912/<repo>/<job>:latest` |

スケジュール時刻・タイムアウトはプログラムごとに異なる（後述）。

---

## ① 環境変数によるランタイム判別

```python
def detect_runtime() -> str:
    import os
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"  # CLOUD_RUN_JOB=Jobs, K_SERVICE=Services。K_JOBは存在しない
    try:
        import google.colab  # noqa: F401
        return "colab_enterprise" if os.environ.get("GOOGLE_CLOUD_PROJECT") else "colab_personal"
    except ImportError:
        return "local"   # ← 必ず "local"。"cloudrun" は絶対に書かない

def get_bq_client() -> bigquery.Client:
    if RUNTIME == "local":
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(KEY_FILE)
        return bigquery.Client(project=PROJECT, credentials=creds)
    else:
        return bigquery.Client(project=PROJECT)  # Cloud Run / Colab: ADC 自動適用
```

> ⚠️ **`except ImportError: return "cloudrun"` は繰り返し発生する重大バグ**。ローカルも Cloud Run 扱いになり認証が逆転してクラッシュする。新規スクリプト作成時に必ず確認すること。

---

## ② Dockerfile テンプレート

スクリプトごとに `docker/Dockerfile.<jobname>` として作成する。

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# 必要な依存のみインストール（スクリプトに応じて変更）
RUN pip install --no-cache-dir \
    httpx>=0.27 \
    beautifulsoup4>=4.12 \
    lxml>=4.9 \
    google-cloud-storage>=2.16

COPY scripts/<script>.py scripts/<script>.py

ENV PYTHONUTF8=1

ENTRYPOINT ["python", "scripts/<script>.py"]
```

---

## ③ cloudbuild.yaml テンプレート

`cloudbuild/cloudbuild.<jobname>.yaml` として作成する（`--dockerfile` フラグは古いgcloudでは非対応）。

```yaml
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args:
      - build
      - -f
      - docker/Dockerfile.<jobname>
      - -t
      - us-west1-docker.pkg.dev/gmailpj-357912/<repo>/<jobname>:latest
      - .
  - name: 'gcr.io/cloud-builders/docker'
    args:
      - push
      - us-west1-docker.pkg.dev/gmailpj-357912/<repo>/<jobname>:latest
  - name: 'gcr.io/google.com/cloudsdktool/cloud-sdk:slim'
    entrypoint: 'bash'
    args:
      - '-c'
      - |
        set -e
        echo "Updating job: <jobname>"
        gcloud run jobs update <jobname> --region=us-west1 \
          --image=us-west1-docker.pkg.dev/gmailpj-357912/<repo>/<jobname>:latest

images:
  - us-west1-docker.pkg.dev/gmailpj-357912/<repo>/<jobname>:latest
```

> **テンプレートに `docker push` + `jobs update` ステップが含まれている理由**: Cloud Build の `images:` ディレクティブは全 steps 完了後に push するため、steps 内で `jobs update` すると旧 digest を解決してしまう。明示的に `docker push` してから `jobs update` する必要がある。`images:` は冪等な再 push として残す（ビルドサマリに表示される）。

---

## ④ デプロイ手順（初回）

```bash
# 0. 作業ディレクトリ
cd G:\マイドライブ\claude\investment-agent

# 1. Artifact Registry リポジトリ作成（リポジトリが未作成の場合のみ）
gcloud artifacts repositories create <repo> \
  --repository-format=docker \
  --location=us-west1

# 2. ビルド & プッシュ
gcloud builds submit \
  --config cloudbuild/cloudbuild.<jobname>.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source \
  .

# 3. IAM（GCS書き込みが必要な場合。初回のみ）
gcloud storage buckets add-iam-policy-binding gs://stock_data_1930932 \
  --member="serviceAccount:bq-loader@gmailpj-357912.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

# 4. Cloud Run Job 作成
gcloud run jobs create <jobname> \
  --image us-west1-docker.pkg.dev/gmailpj-357912/<repo>/<jobname>:latest \
  --region us-west1 \
  --service-account bq-loader@gmailpj-357912.iam.gserviceaccount.com \
  --memory 512Mi \
  --cpu 1 \
  --task-timeout <秒> \
  --max-retries 1
```

### タイムアウトの目安

| 処理内容 | 推奨タイムアウト |
|---------|---------------|
| 軽量API取得 | 600s |
| 1日分スクレイピング + GCS保存（数百件） | 3600s |
| 大量データ処理 | 7200s |
| EDINET 1ヶ月 | 7200s（約90〜100秒/日） |
| EDINET 3ヶ月 | 14400s |
| EDINET 1年 | 36000s |

> タイムアウト変更: `gcloud run jobs update <jobname> --task-timeout <秒> --region us-west1`
> 再実行時はGCS事前チェックで既取得済みをスキップするため大幅短縮される。

---

## ⑤ テスト実行

```bash
# 今日分を実行（デフォルト）
gcloud run jobs execute <jobname> --region us-west1

# 引数を渡す場合
# ✅ 正しい: 引数名と値は = で結ぶ。複数引数はカンマで連結
gcloud run jobs execute <jobname> \
  --region us-west1 \
  --args="--from=20260224,--to=20260224"

# ⚠️ --args が "expected one argument" エラーになる場合の workaround
# update で args を設定してから execute（引数なし）で実行する:
gcloud run jobs update <jobname> --region us-west1 --args="--from=20160101,--to=20160331"
gcloud run jobs execute <jobname> --region us-west1 --async
# ※ update は Job のデフォルト引数を書き換えるため、次回以降の実行にも引き継がれる点に注意

# ログ確認
gcloud logging read \
  "resource.type=cloud_run_job AND resource.labels.job_name=<jobname>" \
  --limit=50 \
  --format="value(textPayload)" \
  --project=gmailpj-357912
```

---

## ⑥ スクリプト変更時の更新

> **⚠️ Windows + Google Drive 必須ルール**: プロジェクトルート（`G:\マイドライブ\...` / `C:\gdrive\...`）から `gcloud builds submit .` を直接実行禁止。**`[WinError 32] file.tgz`（PermissionError / プロセスはファイルにアクセスできません）でリトライ無限ループに陥る**。下記「一時ビルドディレクトリ方式」（`/c/tmp/cloudbuild-*`）で必ず実行すること。理由: Google Drive 同期プロセスが staging tar 作成中の `file.tgz` をロックする + MR-171（Drive 全体 3216ファイル/20.5MiB がアップロードされ遅延）+ MR-204（2026-05-18 / 5回連続失敗事故）。
>
> **ルール**: スクリプト修正 → 再ビルド → `jobs update --image` の3ステップが1セット。`jobs update` を忘れると古いイメージが定時実行され続ける（`:latest` タグはdigestで固定されるため自動更新されない）。
>
> **テンプレート更新（2026-04-26〜）**: §③ テンプレートに `docker push` + `jobs update` ステップを追加。新規作成時は必ず §③ 形式を使用すること。旧形式のファイルは手動 `jobs update` が必要（次回修正時に §③ へ移行推奨）。**見分け方**: cloudbuild.yaml 内に `gcloud run jobs update` があれば新形式、なければ旧形式。
>
> **過去事故（2026-04-25）**: サロゲート修正を Cloud Build したが `jobs update` を忘れ、3 Job が旧イメージで再失敗。

```bash
# 再ビルド & プッシュ & Job 自動更新（一時ビルドディレクトリ方式）
# Google Drive上で gcloud builds submit するとプロジェクト全体がアップロードされ遅い（MR-171: 3216ファイル/20.5MiB）
# → ローカルSSD上に一時ディレクトリを作り、必要ファイルだけコピーしてビルドする

# 1. 一時ビルドディレクトリ作成 & 必要ファイルのみコピー
BUILD_DIR=$(mktemp -d /c/tmp/cloudbuild-XXXXXX)
mkdir -p "$BUILD_DIR/docker" "$BUILD_DIR/scripts"
cp docker/Dockerfile.<jobname> "$BUILD_DIR/docker/"
cp scripts/<script>.py "$BUILD_DIR/scripts/"
# ※ Dockerfile の COPY で参照する全ファイルをコピーすること

# 2. 一時ディレクトリからビルド送信
gcloud builds submit "$BUILD_DIR" \
  --config cloudbuild/cloudbuild.<jobname>.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source

# 3. 後片付け
rm -rf "$BUILD_DIR"

# フォールバック: cloudbuild.yaml に自動 update ステップが無い場合のみ手動実行
gcloud run jobs update <jobname> \
  --image us-west1-docker.pkg.dev/gmailpj-357912/<repo>/<jobname>:latest \
  --region us-west1
```

### 共有イメージパターン（1 image → N jobs）

1 つのイメージを複数の Job が共有する場合（例: `tdnet-load-daily` イメージを `tdnet-load-daily` / `tdnet-ai-prepare` / `tdnet-ai-finalize` の 3 Job が使用）、cloudbuild.yaml の `jobs update` ステップで全 Job を更新する:

```yaml
  - name: 'gcr.io/google.com/cloudsdktool/cloud-sdk:slim'
    entrypoint: 'bash'
    args:
      - '-c'
      - |
        set -e
        for job in <job1> <job2> <job3>; do
          echo "Updating job: $$job"
          gcloud run jobs update "$$job" --region=us-west1 \
            --image=us-west1-docker.pkg.dev/gmailpj-357912/<repo>/<image>:latest
        done
```

> **`set -e` 必須**: for ループ内の 1 Job 失敗が無視されて部分更新になることを防ぐ。

---

## ⑦ Cloud Scheduler（定刻実行）

```bash
gcloud services enable cloudscheduler.googleapis.com

gcloud scheduler jobs create http <jobname>-daily \
  --schedule "<cron>" \
  --time-zone "Asia/Tokyo" \
  --uri "https://us-west1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/gmailpj-357912/jobs/<jobname>:run" \
  --http-method POST \
  --oauth-service-account-email bq-loader@gmailpj-357912.iam.gserviceaccount.com \
  --location us-west1
```

> `--location` は **`us-west1`** に統一。**`roles/run.invoker` が必須**（未設定だと `status.code: 7` でサイレント失敗）。

```bash
# 初回のみ: invoker 権限付与
gcloud projects add-iam-policy-binding gmailpj-357912 \
  --member="serviceAccount:bq-loader@gmailpj-357912.iam.gserviceaccount.com" \
  --role="roles/run.invoker"

# 手動トリガー & ステータス確認
gcloud scheduler jobs run <jobname>-daily --location us-west1
gcloud scheduler jobs describe <jobname>-daily --location us-west1 --format="yaml(status,lastAttemptTime)"
# status: {} = 成功、status.code: 7 = 権限不足
```

---

## ⑧ 環境変数オーバーライド（実行時パラメータ）

スクリプト冒頭でデフォルト値を定義し、`os.environ.get("ENV_VAR")` で上書きするパターン。`execute` に `--set-env-vars` は使えないので、先に `update` してから `execute` する。

```bash
# Job の環境変数を更新してから実行
gcloud run jobs update <jobname> \
  --set-env-vars EDINET_PRODUCTION=true,EDINET_DATE_MODE=2,EDINET_DATE=20260224 \
  --region us-west1
gcloud run jobs execute <jobname> --region us-west1

# 環境変数を削除する（元の設定値に戻す）
# ✅ 正しい: --remove-env-vars（カンマ区切り）
gcloud run jobs update <jobname> \
  --remove-env-vars EDINET_DATE_MODE,EDINET_DATE,EDINET_PRODUCTION \
  --region us-west1
# ❌ 誤り: --clear-env-vars はリスト形式を受け付けずエラーになる

# 即時実行なら --args が便利（update 不要）
gcloud run jobs execute <jobname> --region us-west1 --args="--from=20260224"
```

---

## ⑨ OOM（メモリ不足）の診断と対処

### OOM の検出

`gcloud run jobs executions describe <execution>` の `status.conditions[].message` に `exit code 137` が出たら OOM。ログでも `Killed` で確認できる。

### Cloud Run コンテナのローカルFSへのファイル蓄積によるOOM

**Cloud Run のコンテナ書き込み層はメモリバック（overlayfs）**のため、ローカルFSにファイルを書き続けるとメモリを消費し続けてOOMになる。メモリ設定を増やしても同じ位置でOOMが再発するのが特徴。

**根本対策**: Cloud Run 実行時はローカルFSに書かず、ダウンロード後即座に GCS にアップロードする。

```python
IS_CLOUD_RUN = os.environ.get("CLOUD_RUN_JOB") is not None

# ダウンロード後の保存処理
if IS_CLOUD_RUN and bucket:
    blob = bucket.blob(gcs_path)
    blob.upload_from_string(data, content_type=content_type)
    # ローカルには書かない → メモリを消費しない
else:
    out_path.write_bytes(resp.content)
```

> 一時的なメモリ増加: `gcloud run jobs update <jobname> --memory 4Gi --region=us-west1`。完了後は戻す。

---

## ⑩ よくある罠・注意事項

| 罠 | 詳細 | 正しい対処 |
|----|------|-----------|
| `--clear-env-vars` に変数名を渡すとエラー | `--clear-env-vars` はフラグのみで引数を取らない（全環境変数をクリア） | 特定変数の削除は `--remove-env-vars VAR1,VAR2` を使う |
| `K_JOB` は存在しない | Cloud Run Jobs の環境変数は `CLOUD_RUN_JOB` | `detect_runtime()` では `CLOUD_RUN_JOB` を参照 |
| **`detect_runtime()` の末尾を `"cloudrun"` にする** | ローカルも Cloud Run 扱いになり認証が逆転してクラッシュ。**繰り返し発生している重大バグ** | 必ず `return "local"` にする（詳細は ① を参照） |
| **`GOOGLE_APPLICATION_CREDENTIALS` が Job の環境変数に残留** | Cloud Run コンテナ内にキーファイルが存在せず ADC 代わりにキーファイル読み込みを試みてクラッシュ | `gcloud run jobs update <jobname> --remove-env-vars GOOGLE_APPLICATION_CREDENTIALS --region us-west1` |
| Vertex AI 使用ジョブは `roles/aiplatform.user` が必要 | text-embedding / Gemini を呼ぶスクリプトは 403 PermissionDenied。付与直後は IAM 反映に数十秒かかる | `gcloud projects add-iam-policy-binding ... --role="roles/aiplatform.user"` 後、数分待ってから実行 |
| `--args` のスペース区切り | `--args="--from 20260224"` は動作しない | `--args="--from=20260224"`（`=` で結ぶ） |
| **`execute` に `--set-env-vars` は使えない** | `unrecognized arguments` エラー | 先に `jobs update --update-env-vars KEY=VAL` で Job を更新してから `execute` する |
| argparse と Colab の衝突 | Colab の Jupyter カーネル引数が `sys.argv` に混入し argparse がエラー | `RUNTIME` 判定でコラボ時は `parse_args()` をスキップする |
| `status.conditions[0].type` は常に `"Completed"` | 実行直後でもこの値になるため完了判定に使えない | `status.succeededCount` / `status.failedCount` を使う |
| `executions list` の `Completed/Unknown` = 実行中 | STATUS が `Completed` で CONDITION が `Unknown` の場合、実際にはタスクが実行中 | `gcloud run jobs executions describe <name> --region us-west1` |
| `Completed/True` でも 0件取得の「偽成功」がある | try/except でループ内の例外を握りつぶしたまま exit(0) で終了すると成功判定になる | ログの `合計: N 件` を必ず確認。N=0 なら全日付エラーの可能性大 |
| CPU に無効な値を指定するとエラー | `--cpu 1.2` などの中間値は無効 | 有効値: `.08`〜`1`（小数）、`1`、`2`、`4`、`6`、`8` |
| `gsutil rsync -r ...` on Windows | `Permission denied: .../gsutil/VERSION` | **`gcloud storage rsync -r` を使う** |
| **Playwright ベースイメージのバージョン固定** | `mcr.microsoft.com/playwright/python:v1.50.0-jammy` に `pip install playwright>=1.50` を入れると最新版がインストールされバイナリ不一致でクラッシュ | `pip install "playwright==1.50.0"` とベースイメージと**完全一致**でピンする |
| **スクリプトリネーム時の Dockerfile パス更新漏れ** | `scripts/foo_v2.py` → `scripts/foo.py` リネーム後に Dockerfile の `COPY`/`ENTRYPOINT` が古いパスのままでビルド失敗 | リネーム時は ① Dockerfile の COPY/ENTRYPOINT、② Dockerfile 自体のリネーム、③ cloudbuild.yaml の参照、④ スケジューラ/Artifact Registry を一括更新。ビルドが通ることを確認してから完了とする |
| **プロジェクトルートから `gcloud builds submit` するとビルドコンテキスト肥大化 + Windows ファイルロック (`WinError 32` / `file.tgz`)** | (1) Dockerfile は1ファイルしか COPY しないのに Google Drive 上のプロジェクト全体 3216ファイル/20.5MiB がアップロードされ遅延（MR-171）。(2) Google Drive 同期プロセスが staging tar（`file.tgz`）をロックし `PermissionError` でリトライ無限ループ（MR-204: 2026-05-18 / 5回連続失敗事故） | §⑥ の一時ビルドディレクトリ方式（`/c/tmp/cloudbuild-*`）を使う。ローカルSSD上に必要ファイルだけコピーしてビルド。Google Drive の遅延もロックも回避できる |

---

## 既存 Job 一覧

| Job名 | スクリプト | Artifact Registry | タイムアウト | スケジュール |
|-------|-----------|-----------------|------------|------------|
| `tdnet-download` | `scripts/tdnet_download.py` | `tdnet/tdnet-download` | 3600s | 毎営業日 19:10 JST（未設定） |
| `jquants-fin-summary` | `scripts/jquants_get_fin_summary.py` | `jquants/jquants-fin-summary` | 600s | 火〜土 02:00 / 月〜金 18:30 JST |
| `edinet-download` | `scripts/edinet_download.py` | `edinet/edinet-download` | **日数に応じて変更**（1年分=36000s） | 未設定 |
| `edinet-load` | `scripts/edinet_load.py` | `edinet/edinet-load` | **86400s（24時間）** | 未設定 |
| `tdnet-load` | `scripts/tdnet_load.py` | `tdnet/tdnet-load` | 3600s | 未設定 |
| `edinet-delay` | `scripts/edinet_delay.py` | `edinet/edinet-delay` | 600s | 毎営業日 18:30 JST（`edinet-delay-daily`） |
| `shina-margin-balance-load` | `scripts/shina_margin_balance_load.py` | `shina/shina-margin-balance-load` | 600s | 毎営業日 17:00・20:00 JST（`shina-margin-balance-load-17/20`） |
| `is-holiday` | `scripts/is_holiday.py` | `tools/is-holiday` | 60s | 毎日 08:00 JST（`is-holiday-daily`） ※土日含む |
| `stock-price-load` | `scripts/stock_price_load.py` | `stock/stock-price-load` | 1200s | 毎営業日 17:00 JST（`stock-price-load-daily`） |
| `stock-code-list-load` | `scripts/stock_code_list_load.py` | `stock/stock-code-list-load` | 600s | 毎月3日 20:00 JST（`stock-code-list-load-monthly`） |
| `irbank-tdnet-download` | `scripts/irbank_tdnet_download.py` | `tdnet/irbank-tdnet-download` | **43200s（12h）** | 手動実行のみ（過去データ取得用） |
| `edinet-xbrl-extractor` | `scripts/edinet_xbrl_extractor.py` | `edinet/edinet-xbrl-extractor` | 3600s | 手動実行のみ（清原スクリーニング用） |
| `check-duplicate-triggers` | `scripts/check_duplicate_triggers.py` | `tools/check-duplicate-triggers` | 120s | 毎日 05:30 JST（`check-duplicate-triggers-daily`） |
| `stock-price-yf-am-load` | `scripts/stock_price_yf_am_load.py` | `stock/stock-price-yf-am-load` | 2400s | 毎営業日 11:45 JST（`stock-price-yf-am-load-daily`）。4並列タスク |
| `signal-011-4-daily` | `scripts/signal_011_4_daily.py` | `stock/signal-011-4` | 600s | 毎日 06:30 JST（Scheduler: `signal-011-4-daily-scheduler`）⛔停止中 |
| `paper-trade-011-4-pnl` | `scripts/paper_trade_011_4_pnl.py` | `stock/paper-trade-011-4` | 600s | 毎日 19:00 JST（Scheduler: `paper-trade-011-4-pnl-scheduler`）⛔停止中 |
| `tdnet-shift-md-updater` | `scripts/tdnet_shift_md_updater.py` | `tools/tdnet-shift-md-updater` | 120s | Workflow `tdnet-schedule-revert` から呼び出し（年4回） |
| `vcp-insider-daily` | `scripts/tob_prediction/vcp_daily_cloud.py` | `tob/vcp-insider-daily` | 3600s | 毎営業日 19:00 JST（`vcp-insider-daily-scheduler`）。Dropbox `/stock/AI分析優待/インサイダーVCP.xlsx` に追記 |

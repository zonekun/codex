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
    # Cloud Run Jobs:     CLOUD_RUN_JOB  ※ K_JOB は誤り・存在しない
    # Cloud Run Services: K_SERVICE
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    try:
        import google.colab  # noqa: F401
        if os.environ.get("GOOGLE_CLOUD_PROJECT"):
            return "colab_enterprise"
        return "colab_personal"
    except ImportError:
        return "local"   # ← 必ず "local"。"cloudrun" は絶対に書かない
```

> **重要**: Cloud Run Jobs の環境変数は `CLOUD_RUN_JOB`。`K_JOB` は存在しない。
> `K_SERVICE` は Cloud Run Services（HTTP常時起動型）にのみセットされる。

### ⚠️ 繰り返し発生するバグ：`return "cloudrun"` の誤記

**過去に複数回同じミスが発生している。新規スクリプトを書くたびに必ず確認すること。**

#### 誤ったパターン（絶対に書いてはいけない）

```python
    except ImportError:
        return "cloudrun"   # ❌ ローカルも Cloud Run と同じ扱いになる
```

この書き方をすると `RUNTIME == "cloudrun"` がローカルでも True になり、
`get_bq_client()` 等で Cloud Run（ADC）向けの認証コードが走って
**ローカルでは正常動作するが Cloud Run でキーファイルを探してクラッシュする**、
またはその逆（ローカルで ADC が使われ意図しない認証になる）パターンが発生する。

#### 正しい認証分岐パターン（必ずこの形にする）

```python
def get_bq_client() -> bigquery.Client:
    if RUNTIME == "local":
        # ローカルPC のみキーファイルで明示認証
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(KEY_FILE)
        return bigquery.Client(project=PROJECT, credentials=creds)
    else:
        # Cloud Run / Colab: ADC を使用（サービスアカウントが自動適用）
        return bigquery.Client(project=PROJECT)
```

| RUNTIME | 認証方式 |
|---------|---------|
| `"local"` | サービスアカウントキーファイル（`keys/gcp-service-account.json`） |
| `"cloudrun"` | ADC（Cloud Run のサービスアカウントが自動適用） |
| `"colab_personal"` | ADC（`auth.authenticate_user()` 後） |
| `"colab_enterprise"` | ADC（自動） |

#### 既知の被害事例

| 日付 | スクリプト | 症状 | 原因 |
|------|-----------|------|------|
| 2026-03-05 | `dividend_date_load.py` | Cloud Run で `keys/gcp-service-account.json` not found | `detect_runtime()` 末尾が `return "cloudrun"` で、`get_bq_client()` が `RUNTIME == "cloudrun"` のときキーファイルを読もうとした |

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

images:
  - us-west1-docker.pkg.dev/gmailpj-357912/<repo>/<jobname>:latest
```

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
| 1日分スクレイピング + GCS保存（数百件） | 3600s（60分） |
| 軽量API取得 | 600s（10分） |
| 大量データ処理 | 7200s（2時間） |

#### EDINET ダウンロードのタイムアウト（日数に応じて設定）

実績: 約 90〜100秒/日（GCS事前チェックでスキップされた日はほぼ0秒）

| 対象日数 | 推奨タイムアウト | コマンド例 |
|---------|---------------|-----------|
| 1日 | 600s（10分） | `--task-timeout 600` |
| 1週間（7日） | 1800s（30分） | `--task-timeout 1800` |
| 1ヶ月（31日） | 7200s（2時間） | `--task-timeout 7200` |
| 3ヶ月（90日） | 14400s（4時間） | `--task-timeout 14400` |
| 1年（366日） | 36000s（10時間） | `--task-timeout 36000` |

> **注意**: 再実行時はGCS事前チェックで既取得済みの日をスキップするため大幅に短縮される。
> タイムアウトを変更するには `gcloud run jobs update <jobname> --task-timeout <秒> --region us-west1` を実行する。

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

# ❌ 誤り: スペース区切り相当の カンマ区切り（"--from,20260224" 形式）は動作しないケースあり
# --args="--from,20260224,--to,20260224"

# ⚠️ --args が "expected one argument" エラーになる場合の workaround
# シェルの引数解析と gcloud の引数解析が競合することがある。
# その場合は update で args を設定してから execute（引数なし）で実行する:
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

> **ルール**: Cloud Run Job で動いているスクリプト（`scripts/*.py`）を修正したら、**再ビルド＋`jobs update` まで完了させる**までが1セット。ローカルソースの修正だけで終えるとイメージに焼き込まれた旧コードが定時実行され続ける（`YF_STOCK_INFO` の完了メール事故 2026-04-11〜2026-04-17 で6日間旧挙動が継続した）。コミット前チェックリスト:
> 1. `scripts/<job>.py` を修正
> 2. `gcloud builds submit --config cloudbuild/cloudbuild.<jobname>.yaml ...` で再ビルド
> 3. `gcloud run jobs update <jobname> --image ...:latest` で digest 再解決
> 4. 可能ならテスト実行（`gcloud run jobs execute`）で挙動確認

```bash
# 再ビルド & プッシュ
gcloud builds submit --config cloudbuild/cloudbuild.<jobname>.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source .

# Job に反映
gcloud run jobs update <jobname> \
  --image us-west1-docker.pkg.dev/gmailpj-357912/<repo>/<jobname>:latest \
  --region us-west1
```

> ⚠️ **`:latest` の罠**: `gcloud builds submit` でイメージを上書きしても、Cloud Run Job は**既にpullした古いdigestを保持し続ける**（タグではなく digest で固定される）。再ビルド後は必ず `gcloud run jobs update --image ...:latest` を実行して digest を再解決させること。これを忘れると古いコードのままで実行される（無音の罠）。

---

## ⑦ Cloud Scheduler（定刻実行）

スケジュール時刻はプログラムごとに異なる。

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

> Cloud Scheduler の `--location` は **`us-west1`** に統一する（Cloud Run Job のリージョンと揃える）。

### Cloud Scheduler から Cloud Run Job を呼ぶ場合の必要権限

スケジューラが Cloud Run Job を実行するには、**OAuth SA に `roles/run.invoker` が必要**。
これがないと `status.code: 7`（PERMISSION_DENIED）でサイレント失敗する。

```bash
# 初回のみ（SA ごとに一度だけ設定）
gcloud projects add-iam-policy-binding gmailpj-357912 \
  --member="serviceAccount:bq-loader@gmailpj-357912.iam.gserviceaccount.com" \
  --role="roles/run.invoker"

# 動作確認（手動トリガー）
gcloud scheduler jobs run <jobname>-daily --location us-west1

# ステータス確認（status: {} = 成功、status.code: 7 = 権限不足）
gcloud scheduler jobs describe <jobname>-daily --location us-west1 --format="yaml(status,lastAttemptTime)"
```

---

## ⑧ 環境変数オーバーライドパターン（実行時パラメータ）

`gcloud run jobs execute --args` での引数渡しに加え、**環境変数でジョブの動作パラメータを上書き**する設計パターン。
スクリプト冒頭に設定値を書きつつ、Cloud Run 実行時だけ環境変数で差し替えられる。

### 実装例（edinet_download.py より）

```python
# --- 設定ブロック（デフォルト値）---
DATE_SELECT_MODE = 1          # 1=今日 / 2=指定日 / 3=範囲
SPECIFIED_DATE   = "20260218"
RANGE_START_DATE = "20240101"
PRODUCTION_MODE  = False
API_KEY          = os.environ.get("EDINET_API_KEY", "xxxxxx")  # シークレットはこちら

# --- 環境変数オーバーライド（Cloud Run 用）---
if os.environ.get("EDINET_DATE_MODE"):
    DATE_SELECT_MODE = int(os.environ["EDINET_DATE_MODE"])
if os.environ.get("EDINET_DATE"):
    SPECIFIED_DATE = os.environ["EDINET_DATE"]
if os.environ.get("EDINET_RANGE_START"):
    RANGE_START_DATE = os.environ["EDINET_RANGE_START"]
if os.environ.get("EDINET_PRODUCTION"):
    PRODUCTION_MODE = os.environ["EDINET_PRODUCTION"].lower() == "true"
```

### 実行時に環境変数を渡す

```bash
# 本番モードで指定日を実行
gcloud run jobs update <jobname> \
  --set-env-vars EDINET_PRODUCTION=true,EDINET_DATE_MODE=2,EDINET_DATE=20260224 \
  --region us-west1

gcloud run jobs execute <jobname> --region us-west1
```

> **即時実行なら `--args` が便利**（`gcloud run jobs update` が不要）:
> ```bash
> gcloud run jobs execute <jobname> --region us-west1 --args="--from=20260224"
> ```

### 環境変数を削除する（元の設定値に戻す）

```bash
# ✅ 正しい: --remove-env-vars（カンマ区切り）
gcloud run jobs update <jobname> \
  --remove-env-vars EDINET_DATE_MODE,EDINET_DATE,EDINET_PRODUCTION \
  --region us-west1

# ❌ 誤り: --clear-env-vars はリスト形式を受け付けずエラーになる
# --clear-env-vars EDINET_DATE_MODE,EDINET_DATE,EDINET_PRODUCTION
```

---

## ⑨ OOM（メモリ不足）の診断と対処

### OOM の検出方法

`gcloud run jobs executions describe <execution>` の出力で以下を確認する:

```yaml
status:
  conditions:
  - message: 'Task failed with exit code 137.'   # 137 = OOM Kill
    type: Completed
    status: 'False'
  failedCount: 1
```

Cloud Logging でも確認できる:
```
gcloud logging read 'resource.labels.job_name="<jobname>"' --limit=20 --format="value(textPayload)"
```
`exit code 137` または `Killed` というメッセージが OOM のサイン。

### EDINET ダウンロードでの OOM 原因

**LogCapture（io.StringIO）+ 大量ダウンロード日の組み合わせ**で発生する。

- `LogCapture` は stdout を `io.StringIO` にバッファリングするため、処理量が多い日は
  バッファが数百MB〜GB 規模になりうる
- 有報年集中日（例: 2024-06-25 前後）は 1日分の提出件数が数千件に及び、
  ログ出力行数が通常の 10 倍以上になることがある
- 2Gi メモリ設定で 2024-06-25 の処理中に OOM 発生を確認

### 対処

```bash
# メモリを一時的に増やして実行
gcloud run jobs update edinet-download --memory 4Gi --region=us-west1
gcloud run jobs execute edinet-download --region=us-west1 --args="..."

# 完了後に元に戻す（コスト削減）
gcloud run jobs update edinet-download --memory 2Gi --region=us-west1
```

> 恒久的なメモリ増加はコスト増になるため、大量処理期間のみ一時的に増やして元に戻すこと。

### 【重要】Cloud Run コンテナのローカルFSへのファイル蓄積によるOOM

**Cloud Run のコンテナ書き込み層はメモリバック**（overlayfs）であるため、
ローカルFSにファイルを書き続けるとメモリを消費し続けてOOMになる。

#### 症状

スクレイピング・ダウンロードジョブで数百社・数百ファイルを処理する場合、
途中（例: 270/300社目）で突然 OOM が発生する。メモリ設定を 1Gi → 2Gi に増やしても
同じ箇所で OOM が再発するのが特徴（ファイル蓄積が原因のため、メモリ増加では根本解決にならない）。

```
Out-of-memory event detected in container
Container terminated on signal 9.
```

#### 確認方法

Cloud Logging で OOM 直前のログを見ると、大きなファイル（10MB超のPDF等）を
`out_path.write_bytes(resp.content)` でローカルに書いた直後に kill されている。

#### 根本対策：GCS 直接アップロード

Cloud Run 実行時はローカルFSに書かず、ダウンロード後即座に GCS にアップロードする。

```python
IS_CLOUD_RUN = os.environ.get("K_SERVICE") is not None or os.environ.get("CLOUD_RUN_TASK_INDEX") is not None

def _upload_to_gcs(bucket, ticker: str, filename: str, data: bytes, ext: str) -> None:
    gcs_path = f"monthlyir/{ticker}/{filename}"
    blob = bucket.blob(gcs_path)
    blob.upload_from_string(data, content_type=_ext_content_type(ext))

# ダウンロード後の保存処理
if IS_CLOUD_RUN and bucket:
    _upload_to_gcs(bucket, ticker, filename, resp.content, ext)
    # ローカルには書かない → メモリを消費しない
else:
    out_path.write_bytes(resp.content)  # ローカル実行時は従来通り
```

#### スキップ判定もGCSベースに変更

ローカルFSに書かない場合、次回実行時のスキップ判定（既存ファイルチェック）を
GCS の既存オブジェクト一覧で代替する。

```python
def _get_ticker_gcs_hashes(bucket, ticker: str) -> set[str]:
    """GCS上の既存ファイルのURLハッシュセット（8文字）を返す。"""
    prefix = f"monthlyir/{ticker}/"
    hashes = set()
    for blob in bucket.list_blobs(prefix=prefix):
        name = blob.name.rsplit("/", 1)[-1]
        base = name.rsplit(".", 1)[0]
        h = base.rsplit("_", 1)[-1]
        if len(h) == 8:
            hashes.add(h)
    return hashes

# メイン処理ループで各ティッカー処理前に呼ぶ
existing_hashes = _get_ticker_gcs_hashes(bucket, ticker) if IS_CLOUD_RUN else None

# スキップ判定
_skip = (existing_hashes is not None and h in existing_hashes) or out_path.exists()
```

#### 実績

`download-monthly` ジョブ（300社・数千ファイル）で確認:
- メモリ 1Gi: 141社目でOOM
- メモリ 2Gi: 270社目でOOM（同じファイル蓄積問題）
- GCS直接保存に変更: **300/300社完走**（メモリ設定は 2Gi のまま）

> **教訓**: Cloud Run ジョブでファイルをダウンロードして蓄積する処理は、
> 必ず GCS 直接保存パターンを採用すること。メモリ増加は対症療法にすぎない。

---

## ⑨ よくある罠・注意事項

| 罠 | 詳細 | 正しい対処 |
|----|------|-----------|
| `--clear-env-vars` に変数名を渡すとエラー | `--clear-env-vars` はフラグのみで引数を取らない（全環境変数をクリア） | 特定変数の削除は `--remove-env-vars VAR1,VAR2` を使う |
| `K_JOB` は存在しない | Cloud Run Jobs の環境変数は `CLOUD_RUN_JOB`。`K_JOB` はセットされない | `detect_runtime()` では `CLOUD_RUN_JOB` を参照 |
| **`detect_runtime()` の末尾を `"cloudrun"` にする** | `except ImportError: return "cloudrun"` にするとローカルも Cloud Run 扱いになり、認証が逆転してクラッシュする。**繰り返し発生している重大バグ** | 必ず `return "local"` にする。認証分岐は `if RUNTIME == "local":` で行う（詳細は ① を参照） |
| **`GOOGLE_APPLICATION_CREDENTIALS` が Job の環境変数に残留している** | Job 作成時に `--set-env-vars GOOGLE_APPLICATION_CREDENTIALS=keys/gcp-service-account.json` が設定されていると、Cloud Run コンテナ内にそのファイルが存在しないため ADC ではなくキーファイル読み込みを試みてクラッシュする。`google.auth.default()` が `_get_explicit_environ_credentials()` を呼び、`File keys/gcp-service-account.json was not found` エラーになる | `gcloud run jobs update <jobname> --remove-env-vars GOOGLE_APPLICATION_CREDENTIALS --region us-west1` で削除。Job にサービスアカウント（`--service-account bq-loader@...`）が設定されていれば ADC が自動的に機能する |
| Vertex AI 使用ジョブは `roles/aiplatform.user` が必要 | text-embedding / Gemini を呼ぶスクリプトは SA に `roles/aiplatform.user` を付与しないと 403 PermissionDenied になる。付与直後は IAM 反映に数十秒かかるため、付与してすぐ実行すると最初の数件がエラーになることがある | `gcloud projects add-iam-policy-binding ... --role="roles/aiplatform.user"` を実行後、数分待ってから Job を実行する |
| `--args` のスペース区切り | `--args="--from 20260224"` は動作しない | `--args="--from=20260224"`（`=` で結ぶ） |
| **`execute` に `--set-env-vars` は使えない** | `gcloud run jobs execute ... --set-env-vars KEY=VAL` は `unrecognized arguments` エラー | 先に `jobs update --update-env-vars KEY=VAL` で Job を更新してから `execute` する |
| argparse と Colab の衝突 | Colab の Jupyter カーネル引数が `sys.argv` に混入し argparse がエラー | `RUNTIME` 判定でコラボ時は `parse_args()` をスキップする |
| `status.conditions[0].type` は常に `"Completed"` | Cloud Run Job の condition の `type` フィールドは条件名（= 文字列 `"Completed"`）が入るフィールドであり、実際の完了状態ではない。実行直後でもこの値になるため完了判定に使えない | `status.succeededCount` / `status.failedCount` を使う |
| `executions list` の `Completed/Unknown` = 実行中 | `gcloud run jobs executions list` の STATUS 列が `Completed` で CONDITION が `Unknown` の場合、実際にはタスクが実行中。`describe` で `N tasks currently running` を確認すること | `gcloud run jobs executions describe <name> --region us-west1` |
| `Completed/True` でも 0件取得の「偽成功」がある | try/except でループ内の例外を握りつぶしたまま Python が exit(0) で終了すると Cloud Run は成功（True）と判定する。取得件数 0 で正常終了しているケースがある | ログの `合計: N 件` を必ず確認。N=0 なら全日付エラーの可能性大 |
| CPU に無効な値を指定するとエラー | `--cpu 1.2` などの中間値は無効。gcloud がエラーを返す | 有効値: `.08`〜`1`（小数）、`1`、`2`、`4`、`6`、`8`。中程度のスペックが必要な場合は `2` を指定する |
| `gsutil rsync -r ...` on Windows | `Permission denied: .../gsutil/VERSION` | **`gcloud storage rsync -r` を使う**。gsutil は `C:\Program Files (x86)\...` への書き込みを試みて権限エラーになる |
| **Playwright ベースイメージのバージョン固定** | `mcr.microsoft.com/playwright/python:v1.50.0-jammy` はブラウザバイナリを同梱しているが Python パッケージ `playwright` は含まれない。`pip install playwright>=1.50` を追加すると最新版（例: 1.58）がインストールされ、ベースイメージのバイナリ（1.50用）と不一致になり `Executable doesn't exist at /ms-playwright/chromium_headless_shell-XXXX/...` でクラッシュ | `pip install "playwright==1.50.0"` とベースイメージと**完全一致**でピンすること。バージョンアップ時はベースイメージと pip パッケージを同時に変更する |
| **スクリプトリネーム時の Dockerfile パス更新漏れ** | `scripts/foo_v2.py` → `scripts/foo.py` 等にリネームすると `docker/Dockerfile.<jobname>` 側の `COPY scripts/foo_v2.py ...` と `ENTRYPOINT`/`CMD` のパスが古いまま残り、ビルド時に `COPY failed: file not found in build context: stat scripts/foo_v2.py: file does not exist` で常時失敗する。さらに Dockerfile 自体のリネーム漏れも起こりやすい。実例: `download-monthly-v2`（2026-03-21 `download_monthly_v2.py` → `download_monthly.py` リネーム時の漏れで、2026-04-15 に未使用と判定し削除） | スクリプトリネーム時は必ず以下をセットで更新:<br>① `docker/Dockerfile.<jobname>` 内の `COPY` / `ENTRYPOINT` / `CMD` のパス<br>② `docker/Dockerfile.<jobname>` 自体のリネーム（もし古い命名を引きずる場合）<br>③ `cloudbuild/cloudbuild.<jobname>.yaml` 内の `--file` / tag 参照<br>④ スケジューラ / Artifact Registry / `--args` の参照<br>リネーム後は必ず一度 `gcloud builds submit` でビルドが通ることを確認する |

---

## 既存 Job 一覧

| Job名 | スクリプト | Artifact Registry | タイムアウト | スケジュール |
|-------|-----------|-----------------|------------|------------|
| `tdnet-download` | `scripts/tdnet_download.py` | `tdnet/tdnet-download` | 3600s | 毎営業日 19:10 JST（未設定） |
| `jquants-fin-summary` | `scripts/jquants_get_fin_summary.py` | `jquants/jquants-fin-summary` | 600s | 未設定 |
| `edinet-download` | `scripts/edinet_download.py` | `edinet/edinet-download` | **日数に応じて変更**（1年分=36000s） | 未設定 |
| `edinet-load` | `scripts/edinet_load.py` | `edinet/edinet-load` | **86400s（24時間）**（※1年分ETLは10時間超かかる実績あり。Cloud Runの最大値） | 未設定 |
| `tdnet-load` | `scripts/tdnet_load.py` | `tdnet/tdnet-load` | 3600s | 未設定 |
| `edinet-delay` | `scripts/edinet_delay.py` | `edinet/edinet-delay` | 600s | 毎営業日 18:30 JST（`edinet-delay-daily`） |
| `shina-margin-balance-load` | `scripts/shina_margin_balance_load.py` | `shina/shina-margin-balance-load` | 600s | 毎営業日 17:00・20:00 JST（`shina-margin-balance-load-17/20`） |
| `is-holiday` | `scripts/is_holiday.py` | `tools/is-holiday` | 60s | 毎日 08:00 JST（`is-holiday-daily`） ※土日含む |
| `stock-price-load` | `scripts/stock_price_load.py` | `stock/stock-price-load` | 1200s | 毎営業日 17:00 JST（`stock-price-load-daily`） |
| `stock-code-list-load` | `scripts/stock_code_list_load.py` | `stock/stock-code-list-load` | 600s | 毎月3日 20:00 JST（`stock-code-list-load-monthly`）※第3営業日近似 |
| `irbank-tdnet-download` | `scripts/irbank_tdnet_download.py` | `tdnet/irbank-tdnet-download` | **43200s（12h）**（2025/02の10590件で11.5時間かかる実績あり） | 手動実行のみ（過去データ取得用） |
| `edinet-xbrl-extractor` | `scripts/edinet_xbrl_extractor.py` | `edinet/edinet-xbrl-extractor` | 3600s | 手動実行のみ（清原スクリーニング用。環境変数 `XBRL_TEST_LIMIT`/`XBRL_START_DATE`/`XBRL_DAYS` で制御） |
| `check-duplicate-triggers` | `scripts/check_duplicate_triggers.py` | `tools/check-duplicate-triggers` | 120s | 毎日 05:30 JST（`check-duplicate-triggers-daily`）。全ジョブの2重トリガー監視 |
| `stock-price-yf-am-load` | `scripts/stock_price_yf_am_load.py` | `stock/stock-price-yf-am-load` | 2400s | 毎営業日 11:45 JST（`stock-price-yf-am-load-daily`）。4並列タスク。前場スナップショット |
| `signal-011-4-daily` | `scripts/signal_011_4_daily.py` | `stock/signal-011-4` | 600s | 毎日 06:30 JST（`signal-011-4-daily`）。011-4 戦略シグナル計算 |
| `paper-trade-011-4-pnl` | `scripts/paper_trade_011_4_pnl.py` | `stock/paper-trade-011-4` | 600s | 毎日 19:00 JST（`paper-trade-011-4-pnl-daily`）。011-4 ペーパートレード P&L |

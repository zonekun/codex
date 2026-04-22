# 月次 第N営業日スケジュール実行

**カテゴリ**: tools
**作成日**: 2026-03-03
**ステータス**: 有効
**関連ファイル**:
- `functions/stock_code_list_scheduler/main.py`
- `functions/stock_code_list_scheduler/requirements.txt`

## 概要

Cloud Scheduler はクロン式で「第N営業日」を直接指定できない。
Cloud Functions を中継役にして、スクリプト内で営業日判定を行う構成で解決する。

## アーキテクチャ

```
Cloud Scheduler（毎月1〜8日 20:00 JST）
    ↓ OIDC認証
Cloud Functions（第N営業日判定）
    ├─ 第N営業日でなければ → 即終了（Cloud Run起動なし）
    └─ 第N営業日なら → Cloud Run Job 起動
```

## なぜ毎月1〜8日か

第3営業日は必ず月の1〜8日の範囲内に収まる（最悪ケース: 1〜3日が全て祝日＋土日で8日が第3営業日）。
`0 20 1-8 * *` とすることで無駄な呼び出しを最小化しつつ全ケースをカバーできる。

一般化: 第N営業日は最大でも `1〜(N+5)日` の範囲内に収まる。

## Cloud Scheduler 登録コマンド

Cloud Functions URL への呼び出しは `--oidc-service-account-email` を使う。
`--oauth-service-account-email` は `.googleapis.com` 専用で、Functions URL には使えない（エラーになる）。

```bash
FUNCTION_URL="https://us-west1-<project>.cloudfunctions.net/<function-name>"

gcloud scheduler jobs create http <jobname>-monthly \
  --schedule "0 20 1-8 * *" \
  --time-zone "Asia/Tokyo" \
  --uri "$FUNCTION_URL" \
  --http-method POST \
  --oidc-service-account-email bq-loader@gmailpj-357912.iam.gserviceaccount.com \
  --oidc-token-audience "$FUNCTION_URL" \
  --location us-west1
```

## Cloud Functions 実装テンプレート

```python
"""Cloud Functions: 第N営業日チェック → Cloud Run Job 起動."""

import datetime
import functions_framework
import google.auth
import google.auth.transport.requests
import jpholiday
import requests

JST = datetime.timezone(datetime.timedelta(hours=9))

# jpholiday が対応しない特別休日（毎年更新）
SPECIAL_DATES = {
    datetime.date(2025, 12, 31),
    datetime.date(2026,  1,  2),
    datetime.date(2026,  1,  3),
}

PROJECT  = "gmailpj-357912"
LOCATION = "us-west1"
JOB_NAME = "<cloud-run-job-name>"
N        = 3  # 第N営業日


def is_nth_business_day(n: int) -> bool:
    today = datetime.datetime.now(JST).date()
    count = 0
    for day_num in range(1, today.day + 1):
        d = today.replace(day=day_num)
        if d.weekday() < 5 and not jpholiday.is_holiday(d) and d not in SPECIAL_DATES:
            count += 1
    return count == n


@functions_framework.http
def check_and_trigger(request):
    today = datetime.datetime.now(JST).date()
    if not is_nth_business_day(N):
        return f"{today} は第{N}営業日ではないためスキップ", 200

    url = (
        f"https://{LOCATION}-run.googleapis.com"
        f"/apis/run.googleapis.com/v1"
        f"/namespaces/{PROJECT}/jobs/{JOB_NAME}:run"
    )
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())

    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {credentials.token}"},
        json={},
        timeout=30,
    )
    resp.raise_for_status()
    return f"{today} は第{N}営業日 → {JOB_NAME} 起動 (status={resp.status_code})", 200
```

## requirements.txt

```
functions-framework>=3.0
jpholiday>=0.1
google-auth>=2.0
requests>=2.31
```

## デプロイコマンド

```bash
# Cloud Functions API を有効化（初回のみ）
gcloud services enable cloudfunctions.googleapis.com

# デプロイ
gcloud functions deploy <function-name> \
  --gen2 \
  --runtime python312 \
  --region us-west1 \
  --source functions/<function-dir> \
  --entry-point check_and_trigger \
  --trigger-http \
  --no-allow-unauthenticated \
  --service-account bq-loader@gmailpj-357912.iam.gserviceaccount.com \
  --memory 256Mi \
  --timeout 60s
```

## Functions 中継を使うべきかの判断基準

```
Cron式で表現できない → Functions 中継が有効
Cron式で表現できる  → スクリプト内判定で十分（現状維持）
```

| スケジュール例 | Cron表現 | 推奨 |
|-------------|---------|------|
| 毎日・毎週月〜金 | 可能 | スクリプト内で jpholiday 判定 → Functions 不要 |
| 月次・第N営業日 | 不可能 | Functions 中継 |

> 日次ジョブに Functions を挟む必要はない。スクリプト内 jpholiday 判定で休日スキップが既に実装されており、
> Functions を追加しても障害点が増えるだけでメリットがない。

## コスト

| リソース | 呼び出し回数 | 費用 |
|---------|------------|------|
| Cloud Scheduler | 月最大8回 | ほぼ無料 |
| Cloud Functions | 月最大8回 | 無料枠（月200万回）内 |
| Cloud Run Job | 月1回（第N営業日のみ） | 実処理分のみ |

## 実装済みジョブ

| ジョブ | Functions | スケジュール |
|-------|-----------|------------|
| `stock-code-list-load` | `stock-code-list-scheduler` | 毎月第3営業日 20:00 JST |
| `dividend-date-load` | `dividend-date-scheduler` | 毎月第3営業日 20:00 JST |

## gcloud.cmd の Scheduler jobs create が失敗する問題

`gcloud.cmd scheduler jobs create` を Git Bash から実行すると `'C:\Program' は...認識されていません` エラーになる（パスのスペースが原因）。

**回避策**: フルパスで gcloud バイナリを直接呼ぶ。

```bash
"/c/Program Files (x86)/Google/Cloud SDK/google-cloud-sdk/bin/gcloud" scheduler jobs create http <name> \
  --schedule "0 20 1-8 * *" \
  ...
```

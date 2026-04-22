"""Cloud Functions: 第3営業日チェック → stock-code-list-load ジョブ起動."""

import datetime
import json

import functions_framework
import google.auth
import google.auth.transport.requests
import jpholiday
import requests

JST = datetime.timezone(datetime.timedelta(hours=9))

# jpholiday が対応しない特別休日
SPECIAL_DATES = {
    datetime.date(2025, 12, 31),
    datetime.date(2026,  1,  2),
    datetime.date(2026,  1,  3),
}

PROJECT  = "gmailpj-357912"
LOCATION = "us-west1"
JOB_NAME = "stock-code-list-load"


def is_nth_business_day(n: int) -> bool:
    """今日が当月の第 n 営業日かどうかを返す."""
    today = datetime.datetime.now(JST).date()
    count = 0
    for day_num in range(1, today.day + 1):
        d = today.replace(day=day_num)
        if d.weekday() < 5 and not jpholiday.is_holiday(d) and d not in SPECIAL_DATES:
            count += 1
    return count == n


@functions_framework.http
def check_and_trigger(request):
    """第3営業日であれば Cloud Run Job を起動する."""
    today = datetime.datetime.now(JST).date()
    print(f"実行日: {today}")

    if not is_nth_business_day(3):
        msg = f"{today} は第3営業日ではないためスキップ"
        print(msg)
        return msg, 200

    # Cloud Run Jobs API を呼び出す
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

    msg = f"{today} は第3営業日 → {JOB_NAME} を起動しました (status={resp.status_code})"
    print(msg)
    return msg, 200

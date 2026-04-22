"""Cloud Functions: EDINET バックフィルの Embedding バッチジョブ完了を監視し、resume を起動する.

5分おきに Cloud Scheduler から呼ばれ、GCS 上の backfill_state ファイルを確認。
Embedding バッチジョブが SUCCEEDED なら edinet-load を resume モードで起動する。
"""

import json

import functions_framework
import google.auth
import google.auth.transport.requests
import requests
from google.cloud import storage

PROJECT  = "gmailpj-357912"
LOCATION = "us-west1"
BUCKET   = "stock_data_1930932"
STATE_PREFIX = "batch_prediction/edinet/backfill_state"
JOB_NAME = "edinet-load"


def _list_pending_states(bucket) -> list[dict]:
    """GCS から未完了の backfill_state ファイルを取得する."""
    states = []
    for blob in bucket.list_blobs(prefix=STATE_PREFIX):
        if not blob.name.endswith(".json"):
            continue
        if "_docs.json.gz" in blob.name:
            continue
        if "/done/" in blob.name:
            continue
        try:
            content = blob.download_as_text()
            state = json.loads(content)
            state["_blob_name"] = blob.name
            states.append(state)
        except Exception as e:
            print(f"state読込エラー: {blob.name} - {e}")
    return states


def _check_batch_status(job_name: str) -> str:
    """Vertex AI バッチジョブの状態を取得する（google-genai SDK）."""
    from google import genai
    client = genai.Client(vertexai=True, project=PROJECT, location="global")
    job = client.batches.get(name=job_name)
    state = job.state.name if hasattr(job.state, "name") else str(job.state)
    return state


def _trigger_resume(date_from: str, date_to: str) -> str:
    """Cloud Run Job を resume モードで起動する."""
    url = (
        f"https://{LOCATION}-run.googleapis.com"
        f"/apis/run.googleapis.com/v1"
        f"/namespaces/{PROJECT}/jobs/{JOB_NAME}:run"
    )
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())

    body = {
        "overrides": {
            "containerOverrides": [{
                "env": [
                    {"name": "RUN_MODE", "value": "resume"},
                    {"name": "DATE_FROM", "value": date_from},
                    {"name": "DATE_TO", "value": date_to},
                ],
            }],
        },
    }

    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    return f"resume 起動完了 (status={resp.status_code})"


@functions_framework.http
def poll_and_resume(request):
    """Embedding バッチジョブの完了を確認し、完了していれば resume を起動する."""
    client = storage.Client(project=PROJECT)
    bucket = client.bucket(BUCKET)

    states = _list_pending_states(bucket)
    if not states:
        msg = "待機中の backfill state なし"
        print(msg)
        return msg, 200

    results = []
    for state in states:
        date_from = state["date_from"]
        date_to   = state["date_to"]
        embedding = state.get("embedding", {})
        blob_name = state["_blob_name"]

        print(f"チェック中: {date_from}～{date_to}")

        # Embedding ジョブの状態確認（EDINET は1ジョブのみ）
        job_name = embedding.get("job")
        if not job_name:
            msg = f"⚠️ {date_from}～{date_to}: Embedding ジョブ情報なし"
            print(msg)
            results.append(msg)
            continue

        status = _check_batch_status(job_name)
        print(f"  embedding job: {status}")

        if status in ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "FAILED", "CANCELLED"):
            msg = f"❌ {date_from}～{date_to}: Embedding バッチ失敗 ({status})。手動確認が必要。"
            print(msg)
            results.append(msg)
        elif status in ("JOB_STATE_SUCCEEDED", "SUCCEEDED", "completed"):
            # ロックファイルで2重起動を防止
            lock_path = f"{blob_name}.resume_triggered"
            lock_blob = bucket.blob(lock_path)
            if lock_blob.exists():
                msg = f"⏭️ {date_from}～{date_to}: resume 起動済み（ロックあり）"
                print(msg)
                results.append(msg)
                continue
            # ロック作成 → resume 起動
            lock_blob.upload_from_string("triggered", content_type="text/plain")
            msg = _trigger_resume(date_from, date_to)
            print(f"✅ {date_from}～{date_to}: {msg}")
            results.append(f"✅ {date_from}～{date_to}: resume 起動")
        else:
            msg = f"⏳ {date_from}～{date_to}: Embedding バッチ実行中 ({status})"
            print(msg)
            results.append(msg)

    return "\n".join(results), 200

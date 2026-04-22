"""Cloud Functions: TDnet バックフィルのバッチジョブ完了を監視し、resume を起動する.

5分おきに Cloud Scheduler から呼ばれ、GCS 上の backfill_state ファイルを確認。
全バッチジョブが SUCCEEDED なら tdnet-load-parallel を resume モードで起動する。
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
STATE_PREFIX = "batch_prediction/tdnet/backfill_state"
JOB_NAME = "tdnet-load-parallel"


def _list_pending_states(bucket) -> list[dict]:
    """GCS から未完了の backfill_state ファイルを取得する."""
    states = []
    for blob in bucket.list_blobs(prefix=STATE_PREFIX):
        if not blob.name.endswith(".json"):
            continue
        if "_docs.json.gz" in blob.name:
            continue
        # 完了済み（done/ ディレクトリに移動済み）はスキップ
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


def _trigger_resume(
    date_from: str, date_to: str,
    ticker_from: str | None = None, ticker_to: str | None = None,
) -> str:
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

    env_vars = [
        {"name": "RUN_MODE", "value": "resume"},
        {"name": "DATE_FROM", "value": date_from},
        {"name": "DATE_TO", "value": date_to},
    ]
    if ticker_from:
        env_vars.append({"name": "TICKER_FROM", "value": ticker_from})
    if ticker_to:
        env_vars.append({"name": "TICKER_TO", "value": ticker_to})

    body = {
        "overrides": {
            "containerOverrides": [{
                "env": env_vars,
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
    ticker_info = f" ticker={ticker_from}~{ticker_to}" if ticker_from else ""
    return f"resume 起動完了{ticker_info} (status={resp.status_code})"


def _cleanup_state(bucket, blob_name: str, docs_path: str) -> None:
    """完了した state ファイルを削除する."""
    try:
        bucket.blob(blob_name).delete()
        bucket.blob(docs_path).delete()
        print(f"state 削除完了: {blob_name}, {docs_path}")
    except Exception as e:
        print(f"state 削除エラー: {e}")


@functions_framework.http
def poll_and_resume(request):
    """バッチジョブの完了を確認し、完了していれば resume を起動する."""
    client = storage.Client(project=PROJECT)
    bucket = client.bucket(BUCKET)

    states = _list_pending_states(bucket)
    if not states:
        msg = "待機中の backfill state なし"
        print(msg)
        return msg, 200

    results = []
    for state in states:
        date_from    = state["date_from"]
        date_to      = state["date_to"]
        ticker_from  = state.get("ticker_from")
        ticker_to    = state.get("ticker_to")
        phase3       = state["phase3"]
        blob_name    = state["_blob_name"]
        docs_path    = state.get("docs_path", "")

        ticker_info = f" ticker={ticker_from}~{ticker_to}" if ticker_from else ""
        print(f"チェック中: {date_from}～{date_to}{ticker_info}")

        # 全バッチジョブの状態確認
        # 新フォーマット: "job" キー / 旧フォーマット: "flash_job"+"pro_job" キー
        all_done = True
        any_failed = False
        job_keys = ["job"] if "job" in phase3 else ["flash_job", "pro_job"]
        for key in job_keys:
            job_name = phase3.get(key)
            if job_name is None:
                continue
            status = _check_batch_status(job_name)
            print(f"  {key}: {status}")
            if status in ("JOB_STATE_SUCCEEDED", "SUCCEEDED", "completed"):
                continue
            elif status in ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "FAILED", "CANCELLED"):
                any_failed = True
                all_done = False
            else:
                all_done = False

        label = f"{date_from}～{date_to}{ticker_info}"
        if any_failed:
            msg = f"❌ {label}: バッチ失敗。手動確認が必要。"
            print(msg)
            results.append(msg)
        elif all_done:
            # ロックファイルで2重起動を防止
            lock_path = f"{blob_name}.resume_triggered"
            lock_blob = bucket.blob(lock_path)
            if lock_blob.exists():
                msg = f"⏭️ {label}: resume 起動済み（ロックあり）"
                print(msg)
                results.append(msg)
                continue
            # ロック作成 → resume 起動
            lock_blob.upload_from_string("triggered", content_type="text/plain")
            msg = _trigger_resume(date_from, date_to, ticker_from, ticker_to)
            print(f"✅ {label}: {msg}")
            results.append(f"✅ {label}: resume 起動")
        else:
            msg = f"⏳ {label}: バッチ実行中"
            print(msg)
            results.append(msg)

    return "\n".join(results), 200

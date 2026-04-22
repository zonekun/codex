# -*- coding: utf-8 -*-
"""Cloud Run Job 2重トリガー検出.

全 Cloud Run Job の直近24時間の実行を取得し、
同一ジョブで5分以内に2回以上起動されたケースを検出してメール通知する。

実行環境: Windows ローカル / Cloud Run Job
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import structlog
from google.cloud import run_v2
from google.oauth2 import service_account

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import send_mail

# ── 定数 ────────────────────────────────────────────
JST = timezone(timedelta(hours=+9), "JST")
PROJECT_ID = "gmailpj-357912"
REGION = "us-west1"
DUP_THRESHOLD_MIN = 5  # この分数以内に2回以上 → 2重トリガーと判定
SCHEDULER_SA = "bq-loader@gmailpj-357912.iam.gserviceaccount.com"  # SA以外（手動実行）は除外

log = structlog.get_logger()


# ============================================================
# 実行環境判別・認証
# ============================================================

def detect_runtime() -> str:
    """実行環境を自動判別する."""
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    return "local"


RUNTIME: str = detect_runtime()


def _get_credentials() -> service_account.Credentials | None:
    """ローカル実行時のみ SA キーから認証情報を返す."""
    if RUNTIME == "local":
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from src.core.config import settings
        return service_account.Credentials.from_service_account_file(
            settings.google_application_credentials,
        )
    return None  # Cloud Run: ADC


# ============================================================
# Cloud Run API で実行一覧取得
# ============================================================

def get_all_jobs(creds: service_account.Credentials | None) -> list[str]:
    """Cloud Run Job 名を全件取得する."""
    client = run_v2.JobsClient(credentials=creds)
    parent = f"projects/{PROJECT_ID}/locations/{REGION}"
    jobs: list[str] = []
    for job in client.list_jobs(parent=parent):
        # job.name = "projects/.../locations/.../jobs/<jobname>"
        jobs.append(job.name.split("/")[-1])
    log.info("jobs_found", cnt=len(jobs))
    return jobs


def get_recent_executions(
    job: str,
    creds: service_account.Credentials | None,
    since: datetime,
) -> list[dict]:
    """ジョブの直近実行を取得する（since 以降のみ）."""
    client = run_v2.ExecutionsClient(credentials=creds)
    parent = f"projects/{PROJECT_ID}/locations/{REGION}/jobs/{job}"
    rows: list[dict] = []
    for exc in client.list_executions(parent=parent):
        created = exc.create_time
        if created is None:
            continue
        # timezone-aware に統一
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created < since:
            continue
        # 手動実行（SA以外）は監視対象外
        creator = exc.creator or ""
        if creator != SCHEDULER_SA:
            continue
        rows.append({
            "name": exc.name.split("/")[-1],
            "created_utc": created,
            "created_jst": created.astimezone(JST),
            "creator": creator,
        })
    return sorted(rows, key=lambda e: e["created_utc"])


# ============================================================
# 2重トリガー検出
# ============================================================

def find_duplicates(
    executions: list[dict],
) -> list[tuple[dict, dict]]:
    """DUP_THRESHOLD_MIN 以内のペアを検出する."""
    pairs: list[tuple[dict, dict]] = []
    for i in range(len(executions) - 1):
        diff = (executions[i + 1]["created_utc"] - executions[i]["created_utc"]).total_seconds()
        if diff <= DUP_THRESHOLD_MIN * 60:
            pairs.append((executions[i], executions[i + 1]))
    return pairs


# ============================================================
# main
# ============================================================

def main() -> None:
    """メイン処理."""
    log.info("=== check_duplicate_triggers START ===")
    now = datetime.now(JST)
    since = (now - timedelta(hours=24)).astimezone(timezone.utc)

    creds = _get_credentials()
    jobs = get_all_jobs(creds)

    all_dups: dict[str, list[tuple[dict, dict]]] = {}
    for job in jobs:
        execs = get_recent_executions(job, creds, since)
        pairs = find_duplicates(execs)
        if pairs:
            all_dups[job] = pairs
            log.warning("dup_found", job=job, cnt=len(pairs))

    if not all_dups:
        log.info("=== check_duplicate_triggers DONE (no duplicates) ===")
        return

    # メール本文構築
    lines: list[str] = [
        f"直近24時間に2重トリガーを検出しました（{now.strftime('%Y-%m-%d %H:%M')} JST 時点）\n",
        f"判定基準: 同一ジョブで {DUP_THRESHOLD_MIN}分以内に2回以上起動\n",
    ]
    for job, pairs in sorted(all_dups.items()):
        lines.append(f"\n■ {job}")
        for a, b in pairs:
            delta = int(
                (b["created_utc"] - a["created_utc"]).total_seconds(),
            )
            lines.append(
                f"  {a['created_jst'].strftime('%H:%M:%S')} → "
                f"{b['created_jst'].strftime('%H:%M:%S')} "
                f"(差 {delta}秒)"
            )
            lines.append(f"    実行1: {a['name']}  by {a['creator']}")
            lines.append(f"    実行2: {b['name']}  by {b['creator']}")

    body = "\n".join(lines)
    dup_jobs = ", ".join(sorted(all_dups.keys()))
    send_mail(
        subject=f"[2重トリガー検出] {dup_jobs}",
        body=body,
    )
    log.info("=== check_duplicate_triggers DONE (mail sent) ===",
             dup_jobs=list(all_dups.keys()))


if __name__ == "__main__":
    main()

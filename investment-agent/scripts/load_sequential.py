"""tdnet-load / edinet-load シーケンシャル実行スクリプト.

指定した年リストを順に実行し、両ジョブが完了してから次の年へ進む。
"""
import subprocess
import time
import sys
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
REGION = "us-west1"
GCLOUD = "gcloud.cmd"
POLL_INTERVAL = 60  # 秒


def now_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")


def log(msg: str) -> None:
    print(f"[{now_jst()}] {msg}", flush=True)


def submit_job(job: str, args: str) -> str:
    """ジョブを非同期実行してexecution名を返す."""
    import re
    result = subprocess.run(
        [GCLOUD, "run", "jobs", "execute", job,
         "--region", REGION, f"--args={args}", "--async"],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    # ANSIエスケープ除去後に "Execution [xxxxx]" を抽出
    combined = result.stdout + result.stderr
    clean = re.sub(r'\x1b\[[0-9;]*m', '', combined)
    m = re.search(r"Execution \[([^\]]+)\]", clean)
    return m.group(1) if m else ""


def get_status(execution: str) -> str:
    """実行状態を返す: 'success' | 'failed' | 'running'

    Cloud Run は成功・失敗ともに type=Completed を返す。
    実際の成否は conditions[0].status (True/False) と reason で判断する。
    """
    result = subprocess.run(
        [GCLOUD, "run", "jobs", "executions", "describe", execution,
         "--region", REGION,
         "--format=value(status.conditions[0].type,status.conditions[0].status,status.conditions[0].reason)"],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    out = result.stdout.strip()
    if not out:
        return "running"
    parts = out.split("\t")
    cond_type   = parts[0] if len(parts) > 0 else ""
    cond_status = parts[1] if len(parts) > 1 else ""
    reason      = parts[2] if len(parts) > 2 else ""

    if cond_type == "Completed":
        if cond_status == "True" and not reason:
            return "success"
        elif cond_status == "Unknown":
            return "running"  # Completed/Unknown = まだ実行中 (Waiting for execution to complete)
        else:
            return "failed"  # status=False or reason あり (タイムアウト・NonZeroExitCode)
    if cond_type == "Failed":
        return "failed"
    return "running"


def wait_all(executions: dict[str, str]) -> bool:
    """全executionが完了するまで待機。{job: execution_name}"""
    pending = dict(executions)
    failed = []

    while pending:
        still_pending = {}
        for job, ex in pending.items():
            status = get_status(ex)
            if status == "success":
                log(f"  ✅ 完了: {job} ({ex})")
            elif status == "failed":
                log(f"  ❌ 失敗: {job} ({ex})")
                failed.append(ex)
            else:
                still_pending[job] = ex
        pending = still_pending

        if pending:
            log(f"  待機中... 残り {len(pending)} 件: {list(pending.keys())}")
            time.sleep(POLL_INTERVAL)

    return len(failed) == 0


def run_year(year: int) -> bool:
    log(f"{'='*60}")
    log(f"{year}年 load 開始")

    executions = {}

    # tdnet-load: --from/--to 形式
    tdnet_args = f"--from={year}0101,--to={year}1231"
    ex = submit_job("tdnet-load", tdnet_args)
    if ex:
        log(f"  投入: tdnet-load {tdnet_args} → {ex}")
        executions["tdnet-load"] = ex
    time.sleep(1)

    # edinet-load: --from/--to 形式（--year は未対応のため変換）
    edinet_args = f"--from={year}0101,--to={year}1231"
    ex = submit_job("edinet-load", edinet_args)
    if ex:
        log(f"  投入: edinet-load {edinet_args} → {ex}")
        executions["edinet-load"] = ex

    log(f"{year}年 2ジョブ投入完了 → 完了待機中...")
    ok = wait_all(executions)
    if ok:
        log(f"✅ {year}年 load 完了")
    else:
        log(f"❌ {year}年 load 一部失敗")
    return ok


def main() -> None:
    years = [int(y) for y in sys.argv[1:]] if len(sys.argv) > 1 else [2024, 2023, 2022, 2021]
    log(f"load バックフィル開始: {years}")

    for year in years:
        ok = run_year(year)
        if not ok:
            log(f"⚠️ {year}年で失敗があったため以降の年をスキップします")
            sys.exit(1)
        time.sleep(5)

    log("🎉 全年度 load 完了")


if __name__ == "__main__":
    main()

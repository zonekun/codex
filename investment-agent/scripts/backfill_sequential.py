"""過去年度 irbank-tdnet-download / edinet-download シーケンシャル実行スクリプト.

指定した年リストを順に実行し、両ジョブが全件完了してから次の年へ進む。
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


def is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def build_splits(year: int) -> list[str]:
    """年を17分割したargs文字列リストを返す."""
    feb_end = 29 if is_leap(year) else 28
    return [
        f"--from={year}0101,--to={year}0131",
        f"--from={year}0201,--to={year}0215",
        f"--from={year}0216,--to={year}02{feb_end:02d}",
        f"--from={year}0301,--to={year}0331",
        f"--from={year}0401,--to={year}0430",
        f"--from={year}0501,--to={year}0515",
        f"--from={year}0516,--to={year}0531",
        f"--from={year}0601,--to={year}0615",
        f"--from={year}0616,--to={year}0630",
        f"--from={year}0701,--to={year}0731",
        f"--from={year}0801,--to={year}0815",
        f"--from={year}0816,--to={year}0831",
        f"--from={year}0901,--to={year}0930",
        f"--from={year}1001,--to={year}1031",
        f"--from={year}1101,--to={year}1115",
        f"--from={year}1116,--to={year}1130",
        f"--from={year}1201,--to={year}1231",
    ]


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
    """execution のステータスを返す（Completed / Failed / Running 等）."""
    result = subprocess.run(
        [GCLOUD, "run", "jobs", "executions", "describe", execution,
         "--region", REGION, "--format=value(status.conditions[0].type)"],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return result.stdout.strip()


def wait_all(executions: dict[str, list[str]]) -> bool:
    """全executionが完了するまで待機。Failedがあった場合Falseを返す."""
    pending = {job: list(exs) for job, exs in executions.items()}
    failed = []

    while any(pending.values()):
        for job, exs in list(pending.items()):
            still_pending = []
            for ex in exs:
                status = get_status(ex)
                if status == "Completed":
                    pass  # 完了
                elif status == "Failed":
                    log(f"  ❌ FAILED: {ex} ({job})")
                    failed.append(ex)
                else:
                    still_pending.append(ex)
            pending[job] = still_pending

        remaining = sum(len(v) for v in pending.values())
        if remaining > 0:
            log(f"  待機中... 残り {remaining} 件")
            time.sleep(POLL_INTERVAL)

    if failed:
        log(f"  ⚠️ 失敗したexecution: {failed}")
        return False
    return True


def run_year(year: int) -> bool:
    splits = build_splits(year)
    jobs = ["irbank-tdnet-download", "edinet-download"]

    log(f"{'='*60}")
    log(f"{year}年 投入開始（{len(splits)}分割 × 2ジョブ = {len(splits)*2}件）")

    executions: dict[str, list[str]] = {}
    for job in jobs:
        exs = []
        for args in splits:
            ex = submit_job(job, args)
            if ex:
                exs.append(ex)
                log(f"  投入: {job} {args} → {ex}")
            time.sleep(0.3)
        executions[job] = exs

    log(f"{year}年 全{len(splits)*2}ジョブ投入完了 → 完了待機中...")
    ok = wait_all(executions)
    if ok:
        log(f"✅ {year}年 完了")
    else:
        log(f"❌ {year}年 一部失敗")
    return ok


def main() -> None:
    years = [int(y) for y in sys.argv[1:]] if len(sys.argv) > 1 else [2019, 2018, 2017, 2016]
    log(f"バックフィル開始: {years}")

    for year in years:
        ok = run_year(year)
        if not ok:
            log(f"⚠️ {year}年で失敗があったため以降の年をスキップします")
            sys.exit(1)
        time.sleep(5)

    log("🎉 全年度 完了")


if __name__ == "__main__":
    main()

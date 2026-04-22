"""Cloud Run ジョブ実行状況確認ツール.

使い方:
    python scripts/check_jobs.py                    # 全ジョブ最新N件
    python scripts/check_jobs.py edinet-download    # 特定ジョブのみ
    python scripts/check_jobs.py --limit 30         # 取得件数変更（デフォルト10）
    python scripts/check_jobs.py --all              # 全ジョブ名を一覧
    python scripts/check_jobs.py --update-active    # active_jobs.md の🔄エントリを自動更新
"""
import subprocess
import sys
import argparse
import re
import platform
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Windows では gcloud は .cmd ファイル経由で呼び出す
GCLOUD = "gcloud.cmd" if platform.system() == "Windows" else "gcloud"

JST = timezone(timedelta(hours=9))

KNOWN_JOBS = [
    "dividend-date-load",
    "edinet-download",
    "edinet-load",
    "edinet-load-parallel",
    "edinet-delay",
    "irbank-tdnet-download",
    "is-holiday",
    "jquants-fin-summary",
    "shina-margin-balance-load",
    "stock-code-list-load",
    "stock-price-load",
    "tdnet-download",
    "tdnet-load",
    "tdnet-load-parallel",
    "edinet-xbrl-extractor",
]

def status_icon(cond_type: str, cond_status: str, reason: str, completed: str) -> str:
    """Cloud Run の conditions から表示アイコンを決定する.

    Cloud Run は成功・失敗ともに type=Completed を返す。
    実際の成否は conditions[0].status（True=成功, False=失敗）で判断する必要がある。
    reason が非空（NonZeroExitCode 等）の場合も失敗扱い。
    完了時刻がない場合は実行中とみなす。
    """
    # 完了時刻がない = まだ実行中
    if not completed or completed == "-":
        return "🔄"
    if cond_type == "Cancelled":
        return "⛔"
    # Completed だが status=False or reason あり → 実際は失敗
    if cond_type == "Completed" and (cond_status == "False" or reason):
        return "❌"
    if cond_type == "Completed" and cond_status == "True":
        return "✅"
    if cond_type in ("Failed", "Running"):
        return "❌" if cond_type == "Failed" else "🔄"
    return "❓"


def format_args(args_raw: str) -> str:
    """args フィールドを人間が読みやすい形式に整形する.

    - リストブラケット・クォートを除去
    - セミコロンをスペースに変換
    - 8桁日付が2つあれば YYYYMMDD-YYYYMMDD に圧縮
    - 引数なしは "(デフォルト)" を返す
    """
    # ['--from=20220101', '--to=20220131'] → --from=20220101 --to=20220131
    clean = re.sub(r"[\[\]']", "", args_raw).replace(";", " ").strip()
    if not clean:
        return "(デフォルト)"
    # 8桁日付が含まれる場合は圧縮表示
    dates = re.findall(r'\d{8}', clean)
    if dates:
        return "-".join(dates)
    # それ以外はそのまま（長すぎる場合は切り詰め）
    return clean[:35]


def utc_to_jst(utc_str: str) -> str:
    """UTC文字列をJST文字列に変換する."""
    if not utc_str:
        return "-"
    try:
        dt = datetime.strptime(utc_str[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone(JST).strftime("%m/%d %H:%M")
    except Exception:
        return utc_str[:16]


def get_executions(job: str, limit: int) -> list[dict]:
    """gcloud でジョブの実行一覧を取得する."""
    cmd = [
        GCLOUD, "run", "jobs", "executions", "list",
        f"--job={job}",
        "--region=us-west1",
        f"--limit={limit}",
        "--format=value(name,metadata.creationTimestamp,status.completionTime,"
        "status.conditions[0].type,status.conditions[0].status,status.conditions[0].reason,"
        "spec.template.spec.containers[0].args)",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return []

    rows = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name_full   = parts[0]
        created     = parts[1] if len(parts) > 1 else ""
        completed   = parts[2] if len(parts) > 2 else ""
        cond_type   = parts[3] if len(parts) > 3 else ""
        cond_status = parts[4] if len(parts) > 4 else ""
        reason      = parts[5] if len(parts) > 5 else ""
        args_raw    = parts[6] if len(parts) > 6 else ""
        args_str    = format_args(args_raw)
        name        = name_full.split("/")[-1]
        completed_jst = utc_to_jst(completed)
        rows.append({
            "name":          name,
            "created_jst":   utc_to_jst(created),
            "completed_jst": completed_jst,
            "icon":          status_icon(cond_type, cond_status, reason, completed_jst),
            "reason":        reason,
            "args":          args_str,
        })
    return rows


def get_scheduler_jobs() -> list[dict]:
    """Cloud Scheduler ジョブ一覧を取得する."""
    cmd = [
        GCLOUD, "scheduler", "jobs", "list",
        "--location=us-west1",
        "--format=value(name,schedule,timeZone,scheduleTime,state)",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return []

    rows = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name      = parts[0].split("/")[-1]
        schedule  = parts[1] if len(parts) > 1 else ""
        tz        = parts[2] if len(parts) > 2 else ""
        next_time = parts[3] if len(parts) > 3 else ""
        state     = parts[4] if len(parts) > 4 else ""
        rows.append({
            "name":      name,
            "schedule":  schedule,
            "tz":        tz,
            "next_jst":  utc_to_jst(next_time),
            "state":     state,
        })
    # 次回実行時刻順にソート
    rows.sort(key=lambda r: r["next_jst"])
    return rows


def print_scheduler_table() -> None:
    """次回スケジュール実行予定テーブルを表示する."""
    jobs = get_scheduler_jobs()
    if not jobs:
        print("  スケジュール情報を取得できませんでした")
        return

    w_name = 35
    w_cron = 22
    w_next = 14
    print(f"\n{'─'*85}")
    print(f"  次回スケジュール実行予定")
    print(f"{'─'*85}")
    print(f"  {'ジョブ名':{w_name}}  {'cron':>{w_cron}}  {'次回実行(JST)':{w_next}}  状態")
    print(f"  {'─'*w_name}  {'─'*w_cron}  {'─'*w_next}  {'─'*7}")
    for j in jobs:
        state_icon = "✅" if j["state"] == "ENABLED" else "⏸"
        tz_short = "JST" if "Tokyo" in j["tz"] else j["tz"][:6]
        cron_tz = f"{j['schedule']} ({tz_short})"
        print(f"  {j['name']:{w_name}}  {cron_tz:>{w_cron}}  {j['next_jst']:{w_next}}  {state_icon} {j['state']}")
    print()


def print_job_table(job: str, limit: int, show_args: bool = False) -> None:
    """ジョブの実行状況テーブルを表示する."""
    execs = get_executions(job, limit)
    if not execs:
        print(f"  {job}: 実行履歴なし")
        return

    print(f"\n{'─'*95}")
    print(f"  {job}  （直近{len(execs)}件）")
    print(f"{'─'*95}")
    print(f"  {'状態':4}  {'開始(JST)':12}  {'完了(JST)':12}  {'パラメータ':30}  {'失敗理由':20}  {'実行ID'}")
    print(f"  {'─'*4}  {'─'*12}  {'─'*12}  {'─'*30}  {'─'*20}  {'─'*25}")
    for e in execs:
        reason_short = e.get("reason", "")[:20]
        print(f"  {e['icon']}     {e['created_jst']:12}  {e['completed_jst']:12}  {e.get('args',''):30}  {reason_short:20}  {e['name']}")
    print()


def update_active_jobs() -> None:
    """active_jobs.md の 🔄 実行中エントリをgcloud結果で自動更新する."""
    active_jobs_path = Path(__file__).resolve().parent.parent / "data" / "logs" / "active_jobs.md"
    if not active_jobs_path.exists():
        print("active_jobs.md が見つかりません")
        return

    content = active_jobs_path.read_text(encoding="utf-8")
    lines = content.split("\n")
    updated = False

    for i, line in enumerate(lines):
        if "🔄 実行中" not in line:
            continue
        # テーブル行からジョブ名と実行IDを抽出
        cols = [c.strip() for c in line.split("|") if c.strip()]
        if len(cols) < 5:
            continue
        job_name = cols[0]
        exec_id = cols[1]

        # ジョブ名が KNOWN_JOBS に含まれない場合もgcloud照会を試みる
        # 実行IDが具体的なら直接照会、なければジョブ名で最新1件を取得
        execs = get_executions(job_name, 3)
        if not execs:
            continue

        # 実行IDで一致するものを探す
        matched = None
        for e in execs:
            if exec_id != "-" and exec_id in e["name"]:
                matched = e
                break
        # 実行IDが不明("-")の場合、開始時刻が近いものを探す
        if not matched and exec_id == "-" and len(cols) > 2:
            # 最新の実行を使う
            matched = execs[0]

        if not matched:
            continue

        icon = matched["icon"]
        if icon in ("✅", "❌", "⛔"):
            # ステータスラベルを更新
            status_label = {"✅": "✅ 完了", "❌": "❌ 失敗", "⛔": "⛔ キャンセル"}[icon]
            if matched["completed_jst"] != "-":
                status_label += f" ({matched['completed_jst']})"
            # 実行IDも更新（auto-addで"-"だった場合）
            if exec_id == "-" and matched["name"]:
                cols[1] = matched["name"]
            cols[4] = status_label
            lines[i] = "| " + " | ".join(cols) + " |"
            updated = True
            print(f"  更新: {job_name} → {status_label}")

    if updated:
        active_jobs_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nactive_jobs.md を更新しました")
    else:
        print("更新対象の 🔄 エントリはありません")


def main() -> None:
    parser = argparse.ArgumentParser(description="Cloud Run ジョブ実行状況確認")
    parser.add_argument("job", nargs="?", help="ジョブ名（省略時は全ジョブ）")
    parser.add_argument("--limit", type=int, default=10, help="取得件数（デフォルト10）")
    parser.add_argument("--all", action="store_true", help="全ジョブ名を一覧表示")
    parser.add_argument("--update-active", action="store_true",
                        help="active_jobs.md の🔄エントリを自動更新")
    args = parser.parse_args()

    if args.update_active:
        print(f"\n=== active_jobs.md 自動更新 ({datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}) ===")
        update_active_jobs()
        return

    if args.all:
        print("管理対象ジョブ一覧:")
        for j in KNOWN_JOBS:
            print(f"  - {j}")
        return

    jobs = [args.job] if args.job else KNOWN_JOBS
    print(f"\n=== Cloud Run ジョブ実行状況 ({datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}) ===")
    for job in jobs:
        print_job_table(job, args.limit)

    if not args.job:
        print_scheduler_table()


if __name__ == "__main__":
    main()

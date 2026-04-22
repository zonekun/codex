"""Cloud Run ジョブ実行ログ確認ツール（実行ID指定）.

使い方:
    python scripts/check_exec_log.py tdnet-load-zxd8j
    python scripts/check_exec_log.py tdnet-load-zxd8j --limit 50
    python scripts/check_exec_log.py tdnet-load-zxd8j --tail           # 最新N件（デフォルト）
    python scripts/check_exec_log.py tdnet-load-zxd8j --head           # 先頭N件（起動ログ確認）
    python scripts/check_exec_log.py tdnet-load-zxd8j --grep "インサート"  # キーワードフィルタ
    python scripts/check_exec_log.py tdnet-load-zxd8j --summary        # 進捗サマリ自動生成
"""
import subprocess, sys, argparse, re, platform, json
from datetime import datetime, timezone, timedelta

GCLOUD = "gcloud.cmd" if platform.system() == "Windows" else "gcloud"
JST = timezone(timedelta(hours=9))
PROJECT = "gmailpj-357912"
FRESHNESS = "7d"


def run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return r.stdout + r.stderr


def to_jst(ts: str) -> str:
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(JST)
        return dt.strftime("%m/%d %H:%M:%S")
    except Exception:
        return ts


def get_exec_info(exec_name: str) -> dict:
    """実行IDからジョブ名・期間・ステータスを取得"""
    # ジョブ名を推定（実行IDの末尾ハッシュを除いたもの）
    job_name = re.sub(r"-[a-z0-9]{5}$", "", exec_name)

    out = run([GCLOUD, "run", "jobs", "executions", "describe", exec_name,
               "--region", "us-west1", "--format", "json"])
    try:
        d = json.loads(out)
    except Exception:
        return {"job_name": job_name, "status": "不明", "error": out[:300]}

    status = d.get("status", {})
    start  = to_jst(status.get("startTime", ""))
    end    = to_jst(status.get("completionTime", ""))
    conds  = status.get("conditions", [])
    state  = "🔄 実行中" if not status.get("completionTime") else \
             ("✅ 完了" if any(c.get("type") == "Completed" and c.get("status") == "True" for c in conds) else "❌ 失敗")

    # パラメータ（args）取得
    spec = d.get("spec", {}).get("template", {}).get("spec", {})
    containers = spec.get("containers", [{}])
    args = containers[0].get("args", []) if containers else []
    params = " ".join(args) if args else "(デフォルト)"

    return {
        "job_name": job_name,
        "exec_name": exec_name,
        "state": state,
        "start": start,
        "end": end,
        "params": params,
    }


def fetch_logs(exec_name: str, job_name: str, limit: int, order: str, grep: str | None) -> list[str]:
    filter_str = (
        f'resource.type=cloud_run_job '
        f'AND resource.labels.job_name={job_name} '
        f'AND labels."run.googleapis.com/execution_name"={exec_name}'
    )
    if grep:
        filter_str += f' AND textPayload:"{grep}"'

    cmd = [
        GCLOUD, "logging", "read", filter_str,
        "--limit", str(limit),
        "--order", order,
        "--format", "value(timestamp,textPayload)",
        f"--freshness={FRESHNESS}",
        "--project", PROJECT,
    ]
    out = run(cmd)
    lines = [l for l in out.splitlines() if l.strip()]
    return lines


def make_summary(exec_name: str, job_name: str) -> None:
    """進捗サマリを自動生成"""
    print("\n📊 進捗サマリ生成中...")

    # BQ インサート合計
    lines = fetch_logs(exec_name, job_name, 2000, "desc", "BQ インサート")
    counts = [int(m.group(1)) for l in lines if (m := re.search(r"(\d+) 件", l))]
    total_insert = sum(counts)

    # 直近の TICKER
    lines2 = fetch_logs(exec_name, job_name, 200, "desc", None)
    tickers = sorted(set(
        int(m.group(1)) for l in lines2 if (m := re.search(r"TICKER: (\d+)", l))
    ))

    # 起動時ログ（取込済み件数）
    lines3 = fetch_logs(exec_name, job_name, 30, "asc", "取込済みファイル数")
    skip_match = re.search(r"取込済みファイル数: (\d+)", "\n".join(lines3))
    skip_count = int(skip_match.group(1)) if skip_match else 0

    print(f"  取込済み（スキップ）: {skip_count:,} 件")
    print(f"  今回BQインサート累計: {total_insert:,} 件以上")
    if tickers:
        print(f"  処理済みTICKER範囲: {min(tickers)} 〜 {max(tickers)} （{len(tickers)} 銘柄）")
    print()


def main():
    parser = argparse.ArgumentParser(description="Cloud Run 実行ID ログ確認")
    parser.add_argument("exec_name", help="実行ID（例: tdnet-load-zxd8j）")
    parser.add_argument("--limit",   type=int, default=30, help="取得行数（デフォルト30）")
    parser.add_argument("--head",    action="store_true",  help="先頭N件（起動ログ）")
    parser.add_argument("--grep",    type=str, default=None, help="キーワードフィルタ")
    parser.add_argument("--summary", action="store_true",  help="進捗サマリ自動生成")
    args = parser.parse_args()

    exec_name = args.exec_name
    order = "asc" if args.head else "desc"

    # 実行情報取得
    info = get_exec_info(exec_name)
    job_name = info["job_name"]

    print(f"\n{'='*70}")
    print(f"  実行ID : {exec_name}")
    print(f"  ジョブ : {job_name}")
    print(f"  状態   : {info.get('state','?')}")
    print(f"  開始   : {info.get('start','-')}")
    print(f"  完了   : {info.get('end','-')}")
    print(f"  パラメ : {info.get('params','-')}")
    print(f"{'='*70}")

    # サマリモード
    if args.summary:
        make_summary(exec_name, job_name)
        return

    # ログ取得
    label = "先頭" if args.head else "最新"
    grep_label = f' (grep: "{args.grep}")' if args.grep else ""
    print(f"\n📋 {label} {args.limit} 件{grep_label}:\n")

    lines = fetch_logs(exec_name, job_name, args.limit, order, args.grep)

    if not lines:
        print("  (ログなし / フィルタに一致なし)")
        return

    for line in lines:
        # timestamp と本文を分割して整形
        parts = line.split("\t", 1)
        if len(parts) == 2:
            ts = to_jst(parts[0])
            msg = parts[1]
            print(f"  {ts}  {msg}")
        else:
            print(f"  {line}")

    print()


if __name__ == "__main__":
    main()

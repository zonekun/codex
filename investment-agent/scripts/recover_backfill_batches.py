"""未resume分のバックフィルバッチを full モードで再実行する.

#6, #9〜#17 は state ファイル上書きバグで Gemini 結果が BQ に書き込まれていない。
これらを full モードで Cloud Run Job として再実行し、Phase 1〜5 を一気通貫で行う。

Usage:
    PYTHONUTF8=1 python scripts/recover_backfill_batches.py [--dry-run]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time

# 未resume バッチ一覧（ticker_from, ticker_to）
# #5(2915-3221), #7(3548-3891), #8(3892-4194) は resume 済みなので除外
RECOVERY_BATCHES: list[tuple[str, str]] = [
    ("3222", "3547"),   # #6
    ("4196", "4488"),   # #9
    ("4489", "4814"),   # #10
    ("4816", "5255"),   # #11
    ("5256", "5938"),   # #12
    ("5939", "6247"),   # #13
    ("6248", "6573"),   # #14
    ("6574", "6957"),   # #15
    ("6958", "7242"),   # #16
    ("7244", "7610"),   # #17
]

JOB_NAME = "tdnet-load-parallel"
REGION = "us-west1"
DATE_FROM = "20240101"
DATE_TO = "20241231"
GCLOUD = "gcloud.cmd"


def execute_batch(ticker_from: str, ticker_to: str, *, dry_run: bool = False) -> str:
    """Cloud Run Job を full モードで実行する."""
    cmd = [
        GCLOUD, "run", "jobs", "execute", JOB_NAME,
        "--region", REGION,
        "--update-env-vars",
        f"DATE_FROM={DATE_FROM},DATE_TO={DATE_TO},"
        f"TICKER_FROM={ticker_from},TICKER_TO={ticker_to},"
        f"RUN_MODE=full",
        "--async",
    ]
    print(f"  cmd: {' '.join(cmd)}")
    if dry_run:
        return "(dry-run)"
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr.strip()}", file=sys.stderr)
        return f"ERROR: {result.stderr.strip()}"
    # 実行IDを抽出
    for line in result.stderr.split("\n"):
        if "execution" in line.lower() and JOB_NAME in line:
            return line.strip()
    return result.stderr.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="未resume バックフィルバッチのリカバリ")
    parser.add_argument("--dry-run", action="store_true", help="実行せず表示のみ")
    parser.add_argument("--batch", type=int, nargs="*",
                        help="特定バッチのみ実行（0-indexed: 0=#6, 1=#9, ...）")
    parser.add_argument("--interval", type=int, default=210,
                        help="バッチ間のインターバル秒数（デフォルト: 210=3.5分）")
    args = parser.parse_args()

    batches = RECOVERY_BATCHES
    if args.batch is not None:
        batches = [RECOVERY_BATCHES[i] for i in args.batch if i < len(RECOVERY_BATCHES)]

    print(f"=== バックフィル リカバリ ===")
    print(f"対象: {len(batches)} バッチ")
    print(f"モード: {'dry-run' if args.dry_run else 'LIVE'}")
    print(f"インターバル: {args.interval}秒")
    print()

    for i, (tf, tt) in enumerate(batches):
        print(f"[{i+1}/{len(batches)}] ticker {tf}〜{tt}")
        result = execute_batch(tf, tt, dry_run=args.dry_run)
        print(f"  → {result}")

        if i < len(batches) - 1 and not args.dry_run:
            print(f"  次のバッチまで {args.interval}秒 待機...")
            time.sleep(args.interval)

    print()
    print("完了。check_jobs.py で進捗を確認してください。")


if __name__ == "__main__":
    main()

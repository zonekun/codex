#!/usr/bin/env python3
"""per-ticker 結果を JSONL に追記.

Usage:
  PYTHONUTF8=1 python scripts/agent_bc/log_progress.py \
      --session-ts 20260419_200000 \
      --ticker 8218 \
      --status fixed \
      --before-ratio 0.39 \
      --after-ratio 1.0 \
      --applied-fix "F1: tokens 全店舗 厳格化" \
      --note "一致率 6/18 → 18/18"

  # escalation 記録
  PYTHONUTF8=1 python scripts/agent_bc/log_progress.py \
      --session-ts 20260419_200000 --ticker 2685 --status escalation \
      --tried "F1,F2,H" --note "narrative PDF で Gemini でも数値不一致"
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = ROOT / "data/logs"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--session-ts", required=True)
    p.add_argument("--ticker", required=True)
    p.add_argument("--status", required=True,
                   choices=["fixed", "rollback", "escalation", "skipped", "error"])
    p.add_argument("--before-ratio", type=float, default=-1.0)
    p.add_argument("--after-ratio", type=float, default=-1.0)
    p.add_argument("--applied-fix", default="")
    p.add_argument("--tried", default="")
    p.add_argument("--note", default="")
    args = p.parse_args()

    jsonl_path = LOG_DIR / f"agent_bc_match_{args.session_ts}_progress.jsonl"
    esc_path = LOG_DIR / f"agent_bc_match_{args.session_ts}_escalation.csv"
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    record = {
        "ts": datetime.now(JST).isoformat(),
        "ticker": args.ticker,
        "status": args.status,
        "before_ratio": args.before_ratio if args.before_ratio >= 0 else None,
        "after_ratio": args.after_ratio if args.after_ratio >= 0 else None,
        "applied_fix": args.applied_fix,
        "tried": args.tried,
        "note": args.note,
    }
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"[log] {jsonl_path}: {args.ticker} {args.status}")

    if args.status == "escalation":
        write_header = not esc_path.exists()
        with esc_path.open("a", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["ticker", "tried", "note", "ts"])
            w.writerow([args.ticker, args.tried, args.note, record["ts"]])
        print(f"[esc] {esc_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

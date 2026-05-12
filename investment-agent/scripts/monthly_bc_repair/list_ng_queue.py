#!/usr/bin/env python3
"""reconcile CSV から未処理 ticker キューを生成.

apply 済 (adapter.bc_key 設定済) と bc_ignore=true と escalation 記録済を除外.

Usage:
  PYTHONUTF8=1 python scripts/monthly_bc_repair/list_ng_queue.py \
      --reconcile-csv data/logs/bc_key_reverse_mapping_20260419_170039_gemini.csv \
      --exclude-escalation data/logs/agent_bc_match_20260419_escalation.csv \
      --limit 50 \
      --output data/logs/agent_bc_queue_<ts>.csv
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


def load_applied_set() -> set[tuple[str, str]]:
    """adapter.json に bc_key 明示済 or bc_ignore=true の (ticker, key) 集合."""
    applied: set[tuple[str, str]] = set()
    adapter_dir = ROOT / "meta/monthly"
    if not adapter_dir.exists():
        return applied
    for p in adapter_dir.glob("*_extract_adapter.json"):
        try:
            adp = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        ticker = p.name.removesuffix("_extract_adapter.json")
        for fld in adp.get("fields", []):
            key = fld.get("key") or fld.get("name", "")
            if key and (fld.get("bc_key") or fld.get("bc_ignore")):
                applied.add((ticker, key))
    return applied


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--reconcile-csv", required=True)
    p.add_argument("--exclude-escalation", default="",
                   help="escalation CSV (1列目=ticker)")
    p.add_argument("--limit", type=int, default=0, help="0 = 無制限")
    p.add_argument("--output", default="")
    p.add_argument("--sort", choices=["high_conf", "low_conf", "ticker"],
                   default="high_conf",
                   help="high_conf: match_ratio 降順, low_conf: 昇順, ticker: ticker 昇順")
    args = p.parse_args()

    rc_path = Path(args.reconcile_csv)
    if not rc_path.exists():
        print(f"[error] reconcile CSV なし: {rc_path}")
        return 1

    rows: list[dict] = []
    for enc in ("cp932", "utf-8-sig"):
        try:
            with rc_path.open(encoding=enc) as f:
                rows = list(csv.DictReader(f))
            break
        except UnicodeDecodeError:
            continue

    applied = load_applied_set()

    excl_tickers: set[str] = set()
    if args.exclude_escalation and Path(args.exclude_escalation).exists():
        with Path(args.exclude_escalation).open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                t = (r.get("ticker") or "").strip()
                if t:
                    excl_tickers.add(t)

    queue: list[dict] = []
    for r in rows:
        t = (r.get("ticker") or "").strip()
        ok = (r.get("our_key") or "").strip()
        if not t or not ok:
            continue
        if (t, ok) in applied:
            continue
        if t in excl_tickers:
            continue
        queue.append(r)

    if args.sort == "high_conf":
        queue.sort(key=lambda r: -float(r.get("match_ratio") or 0))
    elif args.sort == "low_conf":
        queue.sort(key=lambda r: float(r.get("match_ratio") or 0))
    else:
        queue.sort(key=lambda r: (r["ticker"], r["our_key"]))

    if args.limit > 0:
        queue = queue[:args.limit]

    # 出力
    out_path = args.output
    if not out_path:
        ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        out_path = f"data/logs/agent_bc_queue_{ts}.csv"

    fieldnames = list(queue[0].keys()) if queue else []
    with open(out_path, "w", encoding="cp932", newline="", errors="replace") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in queue:
            w.writerow(r)

    print(f"[{datetime.now(JST):%H:%M:%S}] 元 CSV: {len(rows)} 行")
    print(f"  apply 済除外: {len(applied)} 件")
    print(f"  escalation 除外 ticker: {len(excl_tickers)}")
    print(f"  キュー: {len(queue)} 行 → {out_path}")

    # 標準出力に Top 20 (画面確認用)
    print("\n=== 先頭 10 件 ===")
    for r in queue[:10]:
        print(f"  {r.get('ticker','')} {r.get('our_key','')[:40]:40s} "
              f"suggest={str(r.get('suggested_bc_key',''))[:30]:30s} "
              f"ratio={r.get('match_ratio','')} sem={r.get('semantic','')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

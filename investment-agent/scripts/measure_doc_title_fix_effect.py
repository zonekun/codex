#!/usr/bin/env python3
"""doc_title_pattern 修復後の効果測定.

1. _doc_title_pattern_fixed_at or _doc_title_pattern_manual_fix_at が付いている
   adapter を列挙（修復対象 ticker）
2. 各 ticker を再 extract (since=2024)
3. compare --offline で再突合
4. before(Round 2 CSV) と after の OK/NG/BC_NODATA 差分を集計
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
ADAPTER_DIR = Path("data/monthly_adapters")
PREV_CSV = Path(r"C:\tmp\buffett_compare_20260418_173658.csv")
PY = r"C:/venvs/investment-agent/Scripts/python.exe"


def log(msg: str) -> None:
    ts = datetime.now(JST).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def find_fixed_tickers() -> list[str]:
    fixed = []
    for path in sorted(ADAPTER_DIR.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                a = json.load(f)
        except Exception:
            continue
        if a.get("_doc_title_pattern_fixed_at") or a.get("_doc_title_pattern_manual_fix_at"):
            fixed.append(path.stem)
    return fixed


def load_counts(path: Path, tickers: set[str]) -> tuple[int, int, int, dict]:
    ok = ng = nd = 0
    per_t: dict[str, dict[str, int]] = defaultdict(lambda: {"ok": 0, "ng": 0, "nd": 0})
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            t = r.get("ticker", "")
            if t not in tickers:
                continue
            m = r.get("match", "")
            if m == "OK":
                ok += 1
                per_t[t]["ok"] += 1
            elif m == "NG":
                ng += 1
                per_t[t]["ng"] += 1
            elif m == "BC_NODATA":
                nd += 1
                per_t[t]["nd"] += 1
    return ok, ng, nd, per_t


def main() -> None:
    log("=== 修復済 ticker 一覧 ===")
    tickers = find_fixed_tickers()
    log(f"  {len(tickers)} 銘柄")
    print(" ".join(tickers))

    # Step 1: 再 extract
    log("=== 再 extract (since=2024) ===")
    proc = subprocess.run(
        [PY, "scripts/extract_monthly_data.py", "--tickers", *tickers, "--since", "2024"],
        capture_output=True, text=True, encoding="utf-8",
    )
    # 最後の行だけログに出す
    tail = proc.stdout.strip().splitlines()[-10:]
    for line in tail:
        print(line)
    if proc.returncode != 0:
        log(f"extract failed: {proc.returncode}")
        log(proc.stderr[-500:] if proc.stderr else "")

    # Step 2: compare (--offline)
    log("=== compare --offline ===")
    proc = subprocess.run(
        [PY, "scripts/compare_monthly_buffett.py", "--offline", "--tickers", *tickers],
        capture_output=True, text=True, encoding="utf-8",
    )
    tail = proc.stdout.strip().splitlines()[-15:]
    for line in tail:
        print(line)

    # 最新 compare CSV 探索
    csv_files = sorted(Path(r"C:\tmp").glob("buffett_compare_*.csv"), key=lambda p: p.stat().st_mtime)
    latest = csv_files[-1]
    log(f"after CSV: {latest}")
    log(f"before CSV: {PREV_CSV}")

    # Step 3: before/after 集計
    log("=== before/after 効果測定 ===")
    tickers_set = set(tickers)
    b_ok, b_ng, b_nd, b_per = load_counts(PREV_CSV, tickers_set)
    a_ok, a_ng, a_nd, a_per = load_counts(latest, tickers_set)

    log(f"対象: {len(tickers_set)} 銘柄")
    log(f"  OK:       {b_ok:4d} → {a_ok:4d}  (Δ {a_ok - b_ok:+d})")
    log(f"  NG:       {b_ng:4d} → {a_ng:4d}  (Δ {a_ng - b_ng:+d})")
    log(f"  BC_NODATA:{b_nd:4d} → {a_nd:4d}  (Δ {a_nd - b_nd:+d})")

    improved = []
    degraded = []
    unchanged = []
    for t in sorted(tickers_set):
        b = b_per.get(t, {"ok": 0, "ng": 0, "nd": 0})
        a = a_per.get(t, {"ok": 0, "ng": 0, "nd": 0})
        d = a["ok"] - b["ok"]
        if d > 0:
            improved.append((t, b["ok"], a["ok"], d))
        elif d < 0:
            degraded.append((t, b["ok"], a["ok"], d))
        else:
            unchanged.append(t)
    log(f"改善 {len(improved)} 銘柄、劣化 {len(degraded)} 銘柄、横ばい {len(unchanged)} 銘柄")
    log("改善 Top 15:")
    for t, b, a, d in sorted(improved, key=lambda x: -x[3])[:15]:
        log(f"  + {t}: OK {b} → {a} ({d:+d})")
    log("劣化 Top 10:")
    for t, b, a, d in sorted(degraded, key=lambda x: x[3])[:10]:
        log(f"  - {t}: OK {b} → {a} ({d:+d})")


if __name__ == "__main__":
    main()

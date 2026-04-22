#!/usr/bin/env python3
"""few_records (extract 結果が 0-3 件の) ticker に対して doc_title_pattern を緩和して再抽出.

BQ MAIN_CATEGORY='月次開示' が既にフィルタしているので、
doc_title_pattern はアダプター側 inclusion の二次防御だが、過剰に厳しいと records 激減する.
ここではパターンを "月次|月度|売上|速報|実績|業績|概況|KPI|状況|報告" 程度に緩和.

Usage:
  PYTHONUTF8=1 python scripts/agent_bc/mass_loosen_title_pattern_bg.py \
      --tickers 189A 3370 3395 3543 3608 4015 ...
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent

LOOSE_PATTERN = "月次|月度|月間|売上|速報|実績|業績|概況|KPI|ＫＰＩ|状況|報告|推移|月末"


def parse_agent_result(out: str) -> dict | None:
    m = re.search(r"=== AGENT_RESULT_JSON ===\n(\{.*\})", out)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            return None
    return None


def run(cmd: list[str], timeout: int = 900) -> tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    r = subprocess.run(cmd, capture_output=True, text=True, check=False,
                       env=env, cwd=str(ROOT), timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", nargs="+", required=True)
    p.add_argument("--session-ts", required=True)
    args = p.parse_args()

    for i, t in enumerate(args.tickers, 1):
        print(f"\n[{i}/{len(args.tickers)}] {t}")
        adapter_path = ROOT / f"data/monthly_adapters/{t}.json"
        if not adapter_path.exists():
            print(f"  skip: adapter なし")
            continue

        # snapshot
        run([sys.executable, "scripts/agent_bc/snapshot_adapter.py",
             "--ticker", t, "--save"])

        # 元 pattern 記録
        with adapter_path.open(encoding="utf-8") as f:
            adp = json.load(f)
        orig_pattern = adp.get("doc_title_pattern", "")
        print(f"  orig_pattern: {orig_pattern[:60]!r}")

        patch = json.dumps({
            "doc_title_pattern": LOOSE_PATTERN,
            "_agent_loosened_from": orig_pattern,
        })
        rc, out = run([sys.executable, "scripts/agent_bc/apply_adapter_patch.py",
                       "--ticker", t, "--patch", patch, "--no-snapshot"])
        if rc != 0:
            print(f"  patch 失敗"); continue

        rc, out = run([sys.executable, "scripts/agent_bc/reextract_and_compare.py",
                       "--ticker", t], timeout=1200)
        res = parse_agent_result(out)
        if not res:
            print(f"  compare 解析失敗")
            run([sys.executable, "scripts/agent_bc/log_progress.py",
                 "--session-ts", args.session_ts, "--ticker", t,
                 "--status", "error", "--applied-fix", "B3:loosen doc_title_pattern",
                 "--note", "compare result parse fail"])
            continue

        after = res.get("match_ratio", 0)
        ok = res.get("ok", 0); ng = res.get("ng", 0); nodata = res.get("bc_nodata", 0)
        print(f"  → ratio={after:.1%} ok={ok} ng={ng} nodata={nodata}")

        # status 判定
        status = "fixed" if after >= 0.5 else ("escalation" if after > 0 else "escalation")
        run([sys.executable, "scripts/agent_bc/log_progress.py",
             "--session-ts", args.session_ts, "--ticker", t,
             "--status", status, "--after-ratio", str(after),
             "--applied-fix", "B3:loosen doc_title_pattern",
             "--note", f"pattern 緩和後 ratio={after:.1%} (orig={orig_pattern[:30]!r})"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

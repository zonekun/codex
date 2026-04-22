#!/usr/bin/env python3
"""複数 ticker に use_fy_history_correction=true 適用 + records 削除 + extract + compare 一括."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", nargs="+", required=True)
    args = p.parse_args()

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    results = []
    for t in args.tickers:
        print(f"\n====== {t} ======")
        # snapshot
        subprocess.run(
            [sys.executable, "scripts/agent_bc/snapshot_adapter.py",
             "--ticker", t, "--save"],
            check=False, env=env, cwd=str(ROOT))
        # patch
        r = subprocess.run(
            [sys.executable, "scripts/agent_bc/apply_adapter_patch.py",
             "--ticker", t, "--patch", '{"use_fy_history_correction": true}',
             "--no-snapshot"],
            capture_output=True, text=True, check=False, env=env, cwd=str(ROOT))
        sys.stdout.write(r.stdout)
        # reextract+compare
        r = subprocess.run(
            [sys.executable, "scripts/agent_bc/reextract_and_compare.py", "--ticker", t],
            capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
            timeout=900)
        out = r.stdout
        # AGENT_RESULT_JSON 取得
        import re
        m = re.search(r"=== AGENT_RESULT_JSON ===\n(\{.*\})", out)
        if m:
            try:
                j = json.loads(m.group(1))
                results.append(j)
                print(f"  result: ok={j['ok']} ng={j['ng']} bc_nodata={j['bc_nodata']} "
                      f"ratio={j['match_ratio']:.1%}")
            except Exception as e:
                print(f"  JSON parse err: {e}")
        else:
            print("  AGENT_RESULT_JSON 無し")

    print("\n=== sumary ===")
    for j in results:
        print(f"  {j['ticker']}: ratio={j['match_ratio']:.1%} ({j['ok']}/{j['total']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

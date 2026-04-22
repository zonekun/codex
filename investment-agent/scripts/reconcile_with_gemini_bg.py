#!/usr/bin/env python3
"""reconcile (semantic guard + Gemini matching) + 全銘柄 compare + Dropbox 同期."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")

ROOT = Path(__file__).resolve().parent.parent
DROPBOX_DIR = Path(r"C:\Users\zonekun\Dropbox\stock\temp\bc_key_reverse_mapping")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


def main() -> int:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"

    # 1) 全銘柄 compare
    logger.info("=== Phase 1: 全銘柄 compare ===")
    before_ts = datetime.now(JST).timestamp()
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=1800,
    )
    sys.stdout.write(r.stdout[-1500:])
    candidates = sorted(Path("C:/tmp").glob("buffett_compare_*.csv"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    compare_out = None
    for c in candidates:
        if c.stat().st_mtime >= before_ts:
            compare_out = c
            break
    if not compare_out:
        logger.error("compare 出力見つからず")
        return 1
    logger.info(f"  compare: {compare_out}")

    # 2) reconcile (--gemini-semantic)
    logger.info(f"\n=== Phase 2: reconcile (Gemini semantic) ===")
    reconcile_out = ROOT / f"data/logs/bc_key_reverse_mapping_{datetime.now(JST):%Y%m%d_%H%M%S}_gemini.csv"
    r = subprocess.run(
        [sys.executable, "scripts/reconcile_bc_key_from_compare.py",
         "--compare-csv", str(compare_out),
         "--output", str(reconcile_out),
         "--gemini-semantic"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=2400,
    )
    sys.stdout.write(r.stdout[-3000:])
    sys.stderr.write(r.stderr[-1500:])

    # 3) Dropbox 同期
    logger.info(f"\n=== Phase 3: Dropbox 同期 ===")
    DROPBOX_DIR.mkdir(parents=True, exist_ok=True)
    if reconcile_out.exists():
        dst = DROPBOX_DIR / reconcile_out.name
        shutil.copy2(reconcile_out, dst)
        logger.info(f"✅ Dropbox: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

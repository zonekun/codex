#!/usr/bin/env python3
"""reconcile V2: 意味カテゴリガード付き reverse mapping 再生成.

1. 全銘柄 compare を実行して最新 CSV を取得
2. reconcile_bc_key_from_compare.py (semantic guard付き改修済) を実行
3. 結果を Dropbox にコピー
"""
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

DROPBOX_DIR = Path(r"C:\Users\zonekun\Dropbox\stock\temp\bc_key_reverse_mapping")


def main() -> int:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"

    # 1) 全銘柄 compare (--output オプション無し、自動生成)
    logger.info("=== Phase 1: 全銘柄 compare 実行 ===")
    before_ts = datetime.now(JST).timestamp()
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=1800,
    )
    sys.stdout.write(r.stdout[-2000:])
    sys.stderr.write(r.stderr[-800:])

    # 最新の compare CSV を取得 (before_ts 以降の新規ファイル)
    candidates = sorted(Path("C:/tmp").glob("buffett_compare_*.csv"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    compare_out = None
    for c in candidates:
        if c.stat().st_mtime >= before_ts:
            compare_out = c
            break
    if not compare_out:
        logger.error("最新 compare CSV が見つからず、fallback")
        if candidates:
            compare_out = candidates[0]
        else:
            return 1
    logger.info(f"  compare 出力: {compare_out}")

    # 2) reconcile (semantic guard付き)
    logger.info(f"\n=== Phase 2: reconcile 実行 (input={compare_out.name}) ===")
    reconcile_out = ROOT / f"data/logs/bc_key_reverse_mapping_{datetime.now(JST):%Y%m%d_%H%M%S}.csv"
    r = subprocess.run(
        [sys.executable, "scripts/reconcile_bc_key_from_compare.py",
         "--compare-csv", str(compare_out),
         "--output", str(reconcile_out)],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=600,
    )
    sys.stdout.write(r.stdout[-2000:])
    sys.stderr.write(r.stderr[-800:])
    if r.returncode != 0:
        logger.error("reconcile 失敗")
        return 1

    # 3) Dropbox 同期
    logger.info(f"\n=== Phase 3: Dropbox 同期 ===")
    DROPBOX_DIR.mkdir(parents=True, exist_ok=True)
    dropbox_copy = DROPBOX_DIR / reconcile_out.name
    shutil.copy2(reconcile_out, dropbox_copy)
    logger.info(f"✅ Dropbox: {dropbox_copy}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

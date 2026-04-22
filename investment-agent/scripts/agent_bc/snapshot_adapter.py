#!/usr/bin/env python3
"""adapter.json の snapshot / restore.

Usage:
  # save
  PYTHONUTF8=1 python scripts/agent_bc/snapshot_adapter.py --ticker 8218 --save
  # restore latest
  PYTHONUTF8=1 python scripts/agent_bc/snapshot_adapter.py --ticker 8218 --restore
  # restore specific
  PYTHONUTF8=1 python scripts/agent_bc/snapshot_adapter.py --ticker 8218 --restore-file <path>
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent
SNAP_DIR = ROOT / "data/snapshots/adapters"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--save", action="store_true")
    g.add_argument("--restore", action="store_true", help="最新 snapshot から復元")
    g.add_argument("--restore-file", type=Path, help="指定ファイルから復元")
    g.add_argument("--list", action="store_true", help="ticker の snapshot 一覧")
    p.add_argument("--sync-gcs", action="store_true", default=True)
    args = p.parse_args()
    ticker = args.ticker
    adapter_path = ROOT / f"data/monthly_adapters/{ticker}.json"
    SNAP_DIR.mkdir(parents=True, exist_ok=True)

    if args.save:
        if not adapter_path.exists():
            print(f"[skip] adapter なし: {adapter_path}")
            return 1
        ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        dst = SNAP_DIR / f"{ticker}_{ts}.json"
        shutil.copy2(adapter_path, dst)
        print(f"[save] {dst}")
        return 0

    if args.list:
        snaps = sorted(SNAP_DIR.glob(f"{ticker}_*.json"))
        print(f"{ticker} snapshots ({len(snaps)}):")
        for s in snaps:
            print(f"  {s.name}  ({s.stat().st_size} bytes)")
        return 0

    # restore
    src = None
    if args.restore_file:
        src = args.restore_file
    else:
        snaps = sorted(SNAP_DIR.glob(f"{ticker}_*.json"))
        if not snaps:
            print(f"[error] snapshot なし: {ticker}")
            return 1
        src = snaps[-1]

    shutil.copy2(src, adapter_path)
    print(f"[restore] {src} → {adapter_path}")

    if args.sync_gcs:
        from google.cloud import storage
        client = storage.Client(project="gmailpj-357912")
        client.bucket("stock_data_1930932").blob(
            f"monthly/meta/{ticker}/extract_adapter.json"
        ).upload_from_filename(str(adapter_path))
        print("[gcs] 同期完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())

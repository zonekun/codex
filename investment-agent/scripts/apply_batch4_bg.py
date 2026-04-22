#!/usr/bin/env python3
"""batch4 apply: 2154 + 9201 mapping.
9201 はローカル adapter が無いため GCS から pull してから apply.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    from google.cloud import storage
    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")

    # 9201 adapter を GCS から pull
    local_path = ROOT / "data/monthly_adapters/9201.json"
    if not local_path.exists():
        blob = bucket.blob("monthly/meta/9201/extract_adapter.json")
        if blob.exists():
            blob.download_to_filename(str(local_path))
            print(f"[pull] 9201 adapter from GCS → {local_path}")
        else:
            print(f"[warn] 9201 adapter not on GCS either")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    r = subprocess.run(
        [sys.executable, "scripts/apply_bc_key_reverse_mapping.py",
         "--input", "data/logs/user_approved_mapping_20260419_batch4.csv",
         "--tickers", "2154", "9201",
         "--min-ratio", "0.9"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=300,
    )
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())

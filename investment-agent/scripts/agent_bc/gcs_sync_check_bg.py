#!/usr/bin/env python3
"""ローカル data/monthly_adapters/ と GCS monthly/meta/<T>/extract_adapter.json の差分確認.

未同期な ticker を列挙し、必要なら一括 upload.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent


def md5_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sync", action="store_true", help="未同期 ticker を一括 upload")
    args = p.parse_args()

    from google.cloud import storage
    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")

    adapters_dir = ROOT / "data/monthly_adapters"
    mismatches: list[tuple[str, str]] = []
    missing_gcs: list[str] = []
    only_local: list[str] = []

    for f in sorted(adapters_dir.glob("*.json")):
        ticker = f.stem
        local_bytes = f.read_bytes()
        local_md5 = md5_bytes(local_bytes)
        blob = bucket.blob(f"monthly/meta/{ticker}/extract_adapter.json")
        if not blob.exists():
            missing_gcs.append(ticker)
            continue
        gcs_bytes = blob.download_as_bytes()
        gcs_md5 = md5_bytes(gcs_bytes)
        if local_md5 != gcs_md5:
            mismatches.append((ticker, f"local={local_md5[:8]} gcs={gcs_md5[:8]}"))

    print(f"総ローカル adapter: {len(list(adapters_dir.glob('*.json')))}")
    print(f"差分あり (mismatch): {len(mismatches)}")
    print(f"GCS に存在しない: {len(missing_gcs)}")

    if mismatches:
        print("\n=== 差分 ticker (最初 20) ===")
        for t, info in mismatches[:20]:
            print(f"  {t}: {info}")
        if args.sync:
            print("\n=== 一括 upload 開始 ===")
            for t, _ in mismatches:
                f = adapters_dir / f"{t}.json"
                bucket.blob(f"monthly/meta/{t}/extract_adapter.json").upload_from_filename(str(f))
                print(f"  synced: {t}")

    if missing_gcs:
        print("\n=== GCS 欠落 ticker (最初 20) ===")
        for t in missing_gcs[:20]:
            print(f"  {t}")
        if args.sync:
            print("\n=== 一括 upload (新規) 開始 ===")
            for t in missing_gcs:
                f = adapters_dir / f"{t}.json"
                bucket.blob(f"monthly/meta/{t}/extract_adapter.json").upload_from_filename(str(f))
                print(f"  uploaded: {t}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

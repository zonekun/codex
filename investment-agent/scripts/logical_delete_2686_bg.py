#!/usr/bin/env python3
"""2686 イオン完全子会社化で追う価値なし → 論理削除."""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")

ROOT = Path(__file__).resolve().parent.parent
ADAPTER_PATH = ROOT / "data/monthly_adapters/2686.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


def main() -> int:
    from google.cloud import storage
    with ADAPTER_PATH.open(encoding="utf-8") as f:
        adp = json.load(f)
    adp["_excluded"] = True
    adp["_excluded_reason"] = "イオン完全子会社化で追う価値なし (2026-04-19)"
    adp["_excluded_at"] = datetime.now(JST).isoformat()
    with ADAPTER_PATH.open("w", encoding="utf-8") as f:
        json.dump(adp, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ adapter 論理削除: {ADAPTER_PATH}")

    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")
    bucket.blob("monthly/meta/2686/extract_adapter.json").upload_from_filename(str(ADAPTER_PATH))
    logger.info("✅ GCS sync")

    # records も削除（以降の compare 対象外化）
    rec_blob = bucket.blob("monthly/record/2686/monthly_records.json")
    if rec_blob.exists():
        rec_blob.delete()
        logger.info("✅ records 削除")

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""2735 use_fy_history_correction + 期末店舗数 regex debug."""
from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")

ROOT = Path(__file__).resolve().parent.parent
ADAPTER_PATH = ROOT / "data/monthly_adapters/2735.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


def main() -> int:
    from google.cloud import storage
    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")

    with ADAPTER_PATH.open(encoding="utf-8") as f:
        adp = json.load(f)

    adp["use_fy_history_correction"] = True
    adp["regex_redesign_at"] = datetime.now(JST).isoformat()
    adp["regex_redesign_note"] = "use_fy_history_correction 追加. fy_end_month=8 で month>8 なら year-1 補正."

    with ADAPTER_PATH.open("w", encoding="utf-8") as f:
        json.dump(adp, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ adapter 更新")

    bucket.blob("monthly/meta/2735/extract_adapter.json").upload_from_filename(str(ADAPTER_PATH))
    rec_blob = bucket.blob("monthly/record/2735/monthly_records.json")
    if rec_blob.exists():
        rec_blob.delete()

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    r = subprocess.run(
        [sys.executable, "scripts/extract_monthly_data.py", "--tickers", "2735", "--since", "2024"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT), timeout=900,
    )
    sys.stdout.write(r.stdout[-2000:])

    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        logger.info(f"\n--- records ({len(records)} 件) ---")
        for rec in sorted(records, key=lambda r: str(r.get('year_month',''))):
            logger.info(f"  {rec.get('year_month')}: {rec.get('fields', {})}")

    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", "2735"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT), timeout=300,
    )
    sys.stdout.write(r.stdout[-2000:])
    return 0


if __name__ == "__main__":
    sys.exit(main())

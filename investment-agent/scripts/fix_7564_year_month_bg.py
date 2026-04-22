#!/usr/bin/env python3
"""7564 ワークマン year_month 誤抽出修正.

問題: month_from_title_regex=`(\\d{1,2})月` が PDF タイトル「2026年3月期」の
「3月」を拾い、全124 PDF が 同じ year_month (年-03) に上書きされる.

修正: 8218 と同じく year_month_from_submission_minus_1=True を設定し、
     year_from_title_regex / month_from_title_regex を廃止.
     ファイル名先頭の YYYYMMDD (TDnet submission date) を sub_date として使う.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")

ROOT = Path(__file__).resolve().parent.parent
ADAPTER_PATH = ROOT / "data/monthly_adapters/7564.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


def main() -> int:
    from google.cloud import storage
    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")

    with ADAPTER_PATH.open(encoding="utf-8") as f:
        adp = json.load(f)

    adp.pop("year_from_title_regex", None)
    adp.pop("month_from_title_regex", None)
    adp["year_month_from_submission_minus_1"] = True
    adp["regex_redesign_at"] = datetime.now(JST).isoformat()
    adp["regex_redesign_note"] = (
        "year_from_title_regex / month_from_title_regex を廃止. "
        "ファイル名先頭 YYYYMMDD (TDnet 提出日) - 1月 を year_month として採用."
    )

    with ADAPTER_PATH.open("w", encoding="utf-8") as f:
        json.dump(adp, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ adapter 更新: {ADAPTER_PATH}")

    bucket.blob("monthly/meta/7564/extract_adapter.json").upload_from_filename(str(ADAPTER_PATH))
    logger.info("✅ GCS sync")

    rec_blob = bucket.blob("monthly/record/7564/monthly_records.json")
    if rec_blob.exists():
        rec_blob.delete()
        logger.info("✅ 既存 records 削除")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    logger.info("--- extract ---")
    r = subprocess.run(
        [sys.executable, "scripts/extract_monthly_data.py",
         "--tickers", "7564", "--since", "2024"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=1200,
    )
    sys.stdout.write(r.stdout[-3000:])
    sys.stderr.write(r.stderr[-1500:])

    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        logger.info(f"\n--- records ({len(records)} 件) ---")
        ym_list = sorted(set(r.get("year_month", "") for r in records))
        logger.info(f"  year_months: {ym_list}")
        for rec in records[-6:]:
            logger.info(f"  {rec.get('year_month')}: {rec.get('fields', {})}")

    logger.info("\n--- compare ---")
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", "7564"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=300,
    )
    sys.stdout.write(r.stdout[-3000:])
    return 0


if __name__ == "__main__":
    sys.exit(main())

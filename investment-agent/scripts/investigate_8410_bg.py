#!/usr/bin/env python3
"""8410 year_month=2026-20 異常調査.

month=20 は存在しないので parse ロジックか adapter regex が誤っている.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")

ROOT = Path(__file__).resolve().parent.parent
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


def main() -> int:
    from google.cloud import bigquery, storage
    bq = bigquery.Client(project="gmailpj-357912")
    storage_client = storage.Client(project="gmailpj-357912")
    bucket = storage_client.bucket("stock_data_1930932")
    ticker = "8410"

    adapter_path = ROOT / f"data/monthly_adapters/{ticker}.json"
    if adapter_path.exists():
        with adapter_path.open(encoding="utf-8") as f:
            adp = json.load(f)
        logger.info("--- adapter ---")
        for k, v in adp.items():
            if k == "fields":
                for ff in v:
                    logger.info(f"  {ff}")
            else:
                logger.info(f"  {k}: {str(v)[:200]}")

    # TDnet 最新タイトル
    logger.info("\n--- TDnet 最新タイトル ---")
    sql = f"""
    SELECT SUBMISSION_DATE, DOC_TITLE
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE TICKER = '{ticker}'
      AND MAIN_CATEGORY = '月次開示'
      AND SUBMISSION_DATE >= '2024-01-01'
    GROUP BY SUBMISSION_DATE, DOC_TITLE
    ORDER BY SUBMISSION_DATE DESC
    LIMIT 15
    """
    for doc in bq.query(sql).result():
        logger.info(f"  {doc.SUBMISSION_DATE}  {doc.DOC_TITLE}")

    # 最新1件 full_text
    logger.info("\n--- 最新 full_text head ---")
    sql2 = f"""
    SELECT SUBMISSION_DATE, DOC_TITLE, STRING_AGG(CHUNK_TEXT, ' ') AS full_text
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE TICKER = '{ticker}'
      AND MAIN_CATEGORY = '月次開示'
      AND SUBMISSION_DATE >= '2025-01-01'
    GROUP BY SUBMISSION_DATE, DOC_TITLE
    ORDER BY SUBMISSION_DATE DESC
    LIMIT 2
    """
    for doc in bq.query(sql2).result():
        logger.info(f"\n  {doc.SUBMISSION_DATE} {doc.DOC_TITLE}")
        logger.info(f"  head (700): {(doc.full_text or '')[:700]}")

    # records
    rec_blob = bucket.blob(f"monthly/record/{ticker}/monthly_records.json")
    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        logger.info(f"\n--- records ({len(records)} 件) ---")
        for rec in records:
            logger.info(f"  {rec}")

    # 修正適用
    logger.info("\n--- adapter 修正適用: 月次一般 doc_title 解析規則追加 ---")
    if adapter_path.exists():
        with adapter_path.open(encoding="utf-8") as f:
            adp = json.load(f)
        # year/month regex を汎用化: "YYYY年MM月" 抽出
        adp["year_from_title_regex"] = r"(\d{4})年\d{1,2}月"
        adp["month_from_title_regex"] = r"\d{4}年(\d{1,2})月"
        from datetime import datetime, timezone, timedelta
        JST = timezone(timedelta(hours=9))
        adp["regex_redesign_at"] = datetime.now(JST).isoformat()
        adp["regex_redesign_note"] = "year_month=2026-20 異常修正. year/month_from_title_regex を YYYY年MM月 限定に変更."
        with adapter_path.open("w", encoding="utf-8") as f:
            json.dump(adp, f, ensure_ascii=False, indent=2)
        logger.info("✅ adapter 更新")
        bucket.blob(f"monthly/meta/{ticker}/extract_adapter.json").upload_from_filename(str(adapter_path))

    # re-extract
    if rec_blob.exists():
        rec_blob.delete()
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    r = subprocess.run(
        [sys.executable, "scripts/extract_monthly_data.py", "--tickers", ticker, "--since", "2024"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT), timeout=900,
    )
    sys.stdout.write(r.stdout[-2000:])

    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        logger.info(f"\n--- 修正後 records ({len(records)} 件) ---")
        for rec in records:
            logger.info(f"  {rec.get('year_month')}: {rec.get('fields', {})}")

    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", ticker],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT), timeout=300,
    )
    sys.stdout.write(r.stdout[-2000:])
    return 0


if __name__ == "__main__":
    sys.exit(main())

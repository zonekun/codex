#!/usr/bin/env python3
"""汎用 ticker 詳細調査 BG.

引数: --ticker <TICKER>
adapter + compare + records + TDnet full_text + PDF table を dump.
"""
from __future__ import annotations

import argparse
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
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", required=True)
    args = p.parse_args()
    ticker = args.ticker

    from google.cloud import bigquery, storage
    bq = bigquery.Client(project="gmailpj-357912")
    storage_client = storage.Client(project="gmailpj-357912")
    bucket = storage_client.bucket("stock_data_1930932")

    logger.info(f"{'=' * 90}")
    logger.info(f"ticker: {ticker}")
    logger.info(f"{'=' * 90}")

    adapter_path = ROOT / f"data/monthly_adapters/{ticker}.json"
    if adapter_path.exists():
        with adapter_path.open(encoding="utf-8") as f:
            adp = json.load(f)
        logger.info(f"\n--- adapter ---")
        for k, v in adp.items():
            if k == "fields":
                logger.info(f"  fields: {len(v)}")
                for ff in v:
                    logger.info(f"    key={ff.get('key','')[:40]:40s} bc_key={ff.get('bc_key','')[:30]:30s} yoy={ff.get('yoy_offset')} regex={str(ff.get('row_label_regex',''))[:80]}")
            else:
                logger.info(f"  {k}: {str(v)[:150]}")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    logger.info(f"\n--- compare ---")
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", ticker],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=300,
    )
    sys.stdout.write(r.stdout[-3500:])

    rec_blob = bucket.blob(f"monthly/record/{ticker}/monthly_records.json")
    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        logger.info(f"\n--- records ({len(records)} 件) ---")
        for rec in sorted(records, key=lambda r: str(r.get('year_month', '')))[-6:]:
            logger.info(f"  {rec.get('year_month')}: {rec.get('fields', {})}")

    # TDnet 最新 full_text
    logger.info(f"\n--- TDnet 最新 full_text ---")
    sql = f"""
    SELECT SUBMISSION_DATE, DOC_TITLE, STRING_AGG(CHUNK_TEXT, ' ') AS full_text
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE TICKER = '{ticker}'
      AND MAIN_CATEGORY = '月次開示'
      AND SUBMISSION_DATE >= '2025-06-01'
    GROUP BY SUBMISSION_DATE, DOC_TITLE
    ORDER BY SUBMISSION_DATE DESC
    LIMIT 2
    """
    for doc in bq.query(sql).result():
        logger.info(f"\n  {doc.SUBMISSION_DATE} {doc.DOC_TITLE}")
        logger.info(f"  head (500): {(doc.full_text or '')[:500]}")

    # PDF テーブル dump (最新1件)
    logger.info(f"\n--- PDF テーブル dump ---")
    try:
        import pdfplumber
        blobs = sorted([b for b in bucket.list_blobs(prefix=f"tdnet/{ticker}/") if b.name.endswith(".pdf")
                        and ("月次" in b.name or "月度" in b.name or "売上" in b.name)],
                       key=lambda b: b.name, reverse=True)
        logger.info(f"  月次 PDF: {len(blobs)} 件")
        if blobs:
            blob = blobs[0]
            logger.info(f"  file: {Path(blob.name).name}")
            pdf_bytes = blob.download_as_bytes()
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for pi, page in enumerate(pdf.pages[:2]):
                    tables = page.extract_tables()
                    logger.info(f"  page {pi+1}: {len(tables)} tables")
                    for ti, tbl in enumerate(tables):
                        logger.info(f"    [Table #{ti}] rows={len(tbl)} cols={len(tbl[0]) if tbl else 0}")
                        for ri, row in enumerate(tbl[:15]):
                            cells = [str(c)[:20] if c is not None else '' for c in row]
                            logger.info(f"      R{ri}: {cells}")
    except Exception as e:
        logger.warning(f"  PDF dump 失敗: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

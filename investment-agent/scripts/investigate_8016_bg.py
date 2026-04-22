#!/usr/bin/env python3
"""8016 NG 超調査.

compare + adapter + records + BC CSV + TDnet 文書サンプルを dump し、
何が NG か、PDF 実値は何か、BC 正解値との乖離原因を特定する.
"""
from __future__ import annotations

import io
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


def main() -> int:
    from google.cloud import bigquery, storage
    bq = bigquery.Client(project="gmailpj-357912")
    storage_client = storage.Client(project="gmailpj-357912")
    bucket = storage_client.bucket("stock_data_1930932")
    ticker = "8016"

    # 1) adapter
    adapter_path = ROOT / f"data/monthly_adapters/{ticker}.json"
    if adapter_path.exists():
        logger.info(f"--- adapter ({adapter_path.name}) ---")
        with adapter_path.open(encoding="utf-8") as f:
            adp = json.load(f)
        for k, v in adp.items():
            if k == "fields":
                logger.info(f"  fields: {len(v)} 件")
                for ff in v:
                    logger.info(f"    {ff.get('key')[:30]:30s} bc_key={ff.get('bc_key','')[:30]:30s} bc_ignore={ff.get('bc_ignore')} regex={str(ff.get('row_label_regex',''))[:80]}")
            else:
                logger.info(f"  {k}: {str(v)[:120]}")

    # 2) compare 実行して NG を特定
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    logger.info("\n--- compare ---")
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", ticker],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=300,
    )
    sys.stdout.write(r.stdout[-2500:])

    # 3) records
    rec_blob = bucket.blob(f"monthly/record/{ticker}/monthly_records.json")
    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        logger.info(f"\n--- records ({len(records)} 件) ---")
        for rec in records[-6:]:
            logger.info(f"  {rec.get('year_month')}: {rec.get('fields', {})}")

    # 4) 最新 TDnet 文書の full_text と PDF 構造
    logger.info("\n--- TDnet 最新2件 ---")
    sql = f"""
    SELECT SUBMISSION_DATE, DOC_TITLE, MAIN_CATEGORY, FILE_NAME,
           STRING_AGG(CHUNK_TEXT, ' ') AS full_text
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE TICKER = '{ticker}'
      AND MAIN_CATEGORY = '月次開示'
      AND SUBMISSION_DATE >= '2025-01-01'
    GROUP BY SUBMISSION_DATE, DOC_TITLE, MAIN_CATEGORY, FILE_NAME
    ORDER BY SUBMISSION_DATE DESC
    LIMIT 2
    """
    for doc in bq.query(sql).result():
        logger.info(f"\n  SUB_DATE={doc.SUBMISSION_DATE} TITLE={doc.DOC_TITLE}")
        logger.info(f"  full_text head (600):")
        logger.info(f"    {doc.full_text[:600]}")

    # 5) GCS PDF (pdfplumber でテーブル dump)
    logger.info("\n--- GCS PDF テーブル (最新1件) ---")
    try:
        import pdfplumber
        blobs = sorted([b for b in bucket.list_blobs(prefix=f"tdnet/{ticker}/") if b.name.endswith(".pdf")],
                       key=lambda b: b.name, reverse=True)
        if blobs:
            blob = blobs[0]
            logger.info(f"  file: {Path(blob.name).name}")
            pdf_bytes = blob.download_as_bytes()
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for pi, page in enumerate(pdf.pages):
                    tables = page.extract_tables()
                    logger.info(f"  page {pi + 1}: {len(tables)} tables")
                    for ti, tbl in enumerate(tables):
                        logger.info(f"    Table #{ti} rows={len(tbl)} cols={len(tbl[0]) if tbl else 0}")
                        for ri, row in enumerate(tbl[:15]):
                            cells = [str(c)[:18] if c is not None else '' for c in row]
                            logger.info(f"      R{ri}: {cells}")
                    if pi >= 1:
                        break
    except Exception as e:
        logger.warning(f"PDF dump 失敗: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

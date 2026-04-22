#!/usr/bin/env python3
"""8198 NG 調査 (CSV だけでは判明しない).

compare + adapter + records + BC CSV + TDnet/IR PDF をフル dump.
"""
from __future__ import annotations

import io
import json
import logging
import os
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
    ticker = "8198"

    # adapter
    adapter_path = ROOT / f"data/monthly_adapters/{ticker}.json"
    if adapter_path.exists():
        logger.info(f"--- adapter ({adapter_path.name}) ---")
        with adapter_path.open(encoding="utf-8") as f:
            adp = json.load(f)
        for k, v in adp.items():
            if k == "fields":
                logger.info(f"  fields: {len(v)}")
                for ff in v:
                    logger.info(f"    key={ff.get('key','')[:35]:35s} bc_key={ff.get('bc_key','')[:30]:30s} bc_ignore={ff.get('bc_ignore')} regex={str(ff.get('row_label_regex',''))[:60]}")
            else:
                logger.info(f"  {k}: {str(v)[:120]}")

    # compare
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    logger.info("\n--- compare ---")
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", ticker],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=300,
    )
    sys.stdout.write(r.stdout[-2500:])

    # records
    rec_blob = bucket.blob(f"monthly/record/{ticker}/monthly_records.json")
    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        logger.info(f"\n--- records ({len(records)} 件, all fields) ---")
        for rec in records[-6:]:
            logger.info(f"  {rec.get('year_month')}: {rec.get('fields', {})}")

    # GCS 資料 source 確認
    source = adp.get("source", "")
    logger.info(f"\n--- adapter.source = {source} ---")

    # GCS monthly/docs 存在確認
    docs_blobs = list(bucket.list_blobs(prefix=f"monthly/docs/{ticker}/"))
    logger.info(f"monthly/docs/{ticker}/ : {len(docs_blobs)} 件")
    for b in docs_blobs[:5]:
        logger.info(f"  {Path(b.name).name}")

    tdnet_blobs = list(bucket.list_blobs(prefix=f"tdnet/{ticker}/"))
    logger.info(f"tdnet/{ticker}/ : {len(tdnet_blobs)} 件")

    # TDnet 最新1件 全文
    logger.info("\n--- TDnet 最新文書 ---")
    sql = f"""
    SELECT SUBMISSION_DATE, DOC_TITLE, MAIN_CATEGORY,
           STRING_AGG(CHUNK_TEXT, ' ') AS full_text
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE TICKER = '{ticker}'
      AND MAIN_CATEGORY = '月次開示'
      AND SUBMISSION_DATE >= '2025-01-01'
    GROUP BY SUBMISSION_DATE, DOC_TITLE, MAIN_CATEGORY
    ORDER BY SUBMISSION_DATE DESC
    LIMIT 2
    """
    for doc in bq.query(sql).result():
        logger.info(f"\n  SUB_DATE={doc.SUBMISSION_DATE} TITLE={doc.DOC_TITLE}")
        logger.info(f"  full_text head (700):")
        logger.info(f"    {doc.full_text[:700]}")

    # PDF テーブル dump (最新1件)
    logger.info("\n--- PDF テーブル dump ---")
    try:
        import pdfplumber
        for prefix in [f"monthly/docs/{ticker}/", f"tdnet/{ticker}/"]:
            blobs = sorted([b for b in bucket.list_blobs(prefix=prefix) if b.name.endswith(".pdf")],
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
                            for ri, row in enumerate(tbl[:12]):
                                cells = [str(c)[:18] if c is not None else '' for c in row]
                                logger.info(f"      R{ri}: {cells}")
                        if pi >= 0:
                            break
                break  # 先に見つかった prefix で終了
    except Exception as e:
        logger.warning(f"PDF dump 失敗: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

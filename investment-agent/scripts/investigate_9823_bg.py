#!/usr/bin/env python3
"""9823 NG 調査: 既存店 / 全店 の混同疑い.

suggested bc_key が既存店 → 全店 にマッピング提案されているが、
既存店と全店は概念が違うため正しい値かを確認.
PDF / 本文 / adapter regex を確認して実値を特定.
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
    ticker = "9823"

    adapter_path = ROOT / f"data/monthly_adapters/{ticker}.json"
    if adapter_path.exists():
        logger.info(f"--- adapter ---")
        with adapter_path.open(encoding="utf-8") as f:
            adp = json.load(f)
        for k, v in adp.items():
            if k == "fields":
                logger.info(f"  fields: {len(v)}")
                for ff in v:
                    logger.info(f"    key={ff.get('key','')[:30]:30s} bc_key={ff.get('bc_key','')[:30]:30s} regex={str(ff.get('row_label_regex',''))[:80]}")
            else:
                logger.info(f"  {k}: {str(v)[:120]}")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    logger.info("\n--- compare ---")
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", ticker],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=300,
    )
    sys.stdout.write(r.stdout[-3000:])

    rec_blob = bucket.blob(f"monthly/record/{ticker}/monthly_records.json")
    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        logger.info(f"\n--- records ({len(records)} 件) ---")
        for rec in sorted(records, key=lambda r: str(r.get('year_month',''))):
            logger.info(f"  {rec.get('year_month')}: {rec.get('fields', {})}")

    # 既存店/全店関連 full_text dump
    logger.info("\n--- TDnet 最新2件 full_text (既存店/全店 占有行 確認) ---")
    sql = f"""
    SELECT SUBMISSION_DATE, DOC_TITLE, STRING_AGG(CHUNK_TEXT, ' ') AS full_text
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE TICKER = '{ticker}'
      AND MAIN_CATEGORY = '月次開示'
      AND SUBMISSION_DATE >= '2025-12-01'
    GROUP BY SUBMISSION_DATE, DOC_TITLE
    ORDER BY SUBMISSION_DATE DESC
    LIMIT 2
    """
    for doc in bq.query(sql).result():
        logger.info(f"\n  {doc.SUBMISSION_DATE} {doc.DOC_TITLE}")
        txt = doc.full_text
        logger.info(f"  全体長: {len(txt)}")
        # 既存店 / 全店 / 客単価 を含む行抽出
        import re as _re
        lines = _re.split(r"[\n]|  {2,}", txt)
        for keyword in ["既存店", "全店", "客単価"]:
            for i, line in enumerate(lines):
                if keyword in line:
                    logger.info(f"  [{keyword}] L{i}: {line[:200]}")
                    break

    # PDF テーブル
    logger.info("\n--- PDF テーブル dump ---")
    try:
        import pdfplumber
        blobs = sorted([b for b in bucket.list_blobs(prefix=f"tdnet/{ticker}/") if b.name.endswith(".pdf")],
                       key=lambda b: b.name, reverse=True)
        if blobs:
            blob = blobs[0]
            logger.info(f"  file: {Path(blob.name).name}")
            pdf_bytes = blob.download_as_bytes()
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for pi, page in enumerate(pdf.pages[:2]):
                    tables = page.extract_tables()
                    logger.info(f"  page {pi + 1}: {len(tables)} tables")
                    for ti, tbl in enumerate(tables):
                        logger.info(f"    Table #{ti} rows={len(tbl)} cols={len(tbl[0]) if tbl else 0}")
                        for ri, row in enumerate(tbl[:15]):
                            cells = [str(c)[:18] if c is not None else '' for c in row]
                            logger.info(f"      R{ri}: {cells}")
    except Exception as e:
        logger.warning(f"PDF dump 失敗: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

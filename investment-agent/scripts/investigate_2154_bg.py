#!/usr/bin/env python3
"""2154 エンジニア稼働率/在籍数 extract 結果の誤りを調査.

user 指摘: records が 2 件のみ、2026-02 エンジニア合計在籍数=24219 が疑わしい.
PDF 実値確認 + adapter regex 精査.
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
    ticker = "2154"

    # adapter
    adapter_path = ROOT / f"data/monthly_adapters/{ticker}.json"
    if adapter_path.exists():
        with adapter_path.open(encoding="utf-8") as f:
            adp = json.load(f)
        logger.info("--- adapter ---")
        for k, v in adp.items():
            if k == "fields":
                logger.info(f"  fields: {len(v)}")
                for ff in v:
                    logger.info(f"    key={ff.get('key','')[:40]:40s} regex={str(ff.get('row_label_regex',''))[:100]}")
            else:
                logger.info(f"  {k}: {str(v)[:120]}")

    # records 詳細
    rec_blob = bucket.blob(f"monthly/record/{ticker}/monthly_records.json")
    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        logger.info(f"\n--- records ({len(records)} 件) ---")
        for rec in records:
            logger.info(f"  {rec}")

    # TDnet 最新 複数件
    logger.info("\n--- TDnet 最新 5 件 ---")
    sql = f"""
    SELECT SUBMISSION_DATE, DOC_TITLE, MAIN_CATEGORY, STRING_AGG(CHUNK_TEXT, ' ') AS full_text
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE TICKER = '{ticker}'
      AND MAIN_CATEGORY = '月次開示'
      AND SUBMISSION_DATE >= '2025-01-01'
    GROUP BY SUBMISSION_DATE, DOC_TITLE, MAIN_CATEGORY
    ORDER BY SUBMISSION_DATE DESC
    LIMIT 5
    """
    for doc in bq.query(sql).result():
        logger.info(f"\n  {doc.SUBMISSION_DATE} {doc.DOC_TITLE}")
        txt = doc.full_text or ""
        logger.info(f"  length: {len(txt)}, head (500): {txt[:500]}")
        # 稼働率 / 在籍数 を含む行
        for kw in ["稼働率", "在籍", "建設", "機電"]:
            for m in re.finditer(kw, txt):
                ctx = txt[max(0, m.start()-20):m.end()+100]
                logger.info(f"    [{kw}]: {ctx!r}")
                break  # 1件ずつ

    # PDF 最新 テーブル
    logger.info("\n--- PDF 最新 テーブル dump ---")
    try:
        import pdfplumber
        blobs = sorted([b for b in bucket.list_blobs(prefix=f"tdnet/{ticker}/") if b.name.endswith(".pdf")
                        and ("月次" in b.name or "月度" in b.name or "在籍" in b.name or "稼働" in b.name)],
                       key=lambda b: b.name, reverse=True)
        logger.info(f"  関連 PDF: {len(blobs)} 件")
        if blobs:
            blob = blobs[0]
            logger.info(f"  file: {Path(blob.name).name}")
            pdf_bytes = blob.download_as_bytes()
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for pi, page in enumerate(pdf.pages[:2]):
                    ptext = page.extract_text() or ""
                    logger.info(f"  page {pi+1} length: {len(ptext)}")
                    tables = page.extract_tables()
                    for ti, tbl in enumerate(tables):
                        logger.info(f"    [Table #{ti}] rows={len(tbl)} cols={len(tbl[0]) if tbl else 0}")
                        for ri, row in enumerate(tbl[:20]):
                            cells = [str(c)[:20] if c is not None else '' for c in row]
                            logger.info(f"      R{ri}: {cells}")
    except Exception as e:
        logger.warning(f"  PDF dump 失敗: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

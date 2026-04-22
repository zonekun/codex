#!/usr/bin/env python3
"""2735 PDF テーブル直接 dump.

BQ full_text が 1000字切り抜きしか取れないため、
GCS tdnet/2735/ の最新 PDF を pdfplumber で読み、
全テーブル / 全テキストを dump して 期末店舗数 / 全店 / 全社 の
行構造を把握する.
"""
from __future__ import annotations

import io
import logging
import os
import sys
from pathlib import Path

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")

ROOT = Path(__file__).resolve().parent.parent
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


def main() -> int:
    from google.cloud import storage
    import pdfplumber
    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")

    # 最新 3 件の 2735 月次PDF
    blobs = sorted([b for b in bucket.list_blobs(prefix="tdnet/2735/") if b.name.endswith(".pdf")
                    and ("月次" in b.name or "月度" in b.name)],
                   key=lambda b: b.name, reverse=True)
    logger.info(f"2735 月次 PDF: {len(blobs)} 件")
    for blob in blobs[:3]:
        logger.info(f"\n{'='*100}")
        logger.info(f"FILE: {Path(blob.name).name}")
        logger.info(f"{'='*100}")
        pdf_bytes = blob.download_as_bytes()
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for pi, page in enumerate(pdf.pages):
                logger.info(f"\n--- page {pi + 1} ---")
                ptext = page.extract_text() or ""
                logger.info(f"page text length: {len(ptext)}")
                # 期末店舗数 / 全店 / 全社 出現確認
                import re
                for kw in ["期末店舗数", "期末", "店舗数", "全店", "全社", "既存店"]:
                    occ = len(re.findall(kw, ptext))
                    if occ:
                        logger.info(f"  「{kw}」: {occ} 回")
                        for m in re.finditer(kw, ptext):
                            ctx = ptext[max(0, m.start()-10):m.end()+100]
                            logger.info(f"    ctx: {ctx!r}")

                tables = page.extract_tables()
                logger.info(f"  tables: {len(tables)}")
                for ti, tbl in enumerate(tables):
                    logger.info(f"  [Table #{ti}] rows={len(tbl)} cols={len(tbl[0]) if tbl else 0}")
                    for ri, row in enumerate(tbl[:20]):
                        cells = [str(c)[:20] if c is not None else '' for c in row]
                        logger.info(f"    R{ri}: {cells}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

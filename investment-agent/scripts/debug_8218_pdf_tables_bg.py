#!/usr/bin/env python3
"""8218 コメリ PDF テーブル抽出デバッグ.

_extract_pdf_by_column が内部で使う pdfplumber.page.extract_tables() の
出力を直接 dump し、PDF 実値と extract レコード値を突合する.
"""
from __future__ import annotations

import io
import logging
import os
import sys
from pathlib import Path

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def dump_tables(blob_name: str, bucket) -> None:
    import pdfplumber
    blob = bucket.blob(blob_name)
    if not blob.exists():
        logger.warning(f"  skip: {blob_name} not exists")
        return
    pdf_bytes = blob.download_as_bytes()
    logger.info("=" * 100)
    logger.info(f"FILE: {Path(blob_name).name}")
    logger.info("=" * 100)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pi, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            page_text = page.extract_text() or ""
            logger.info(f"--- page {pi + 1}: {len(tables)} tables ---")
            logger.info(f"page_text head (300): {page_text[:300]!r}")
            for ti, tbl in enumerate(tables):
                logger.info(f"\n[Table #{ti}] rows={len(tbl)} cols={len(tbl[0]) if tbl else 0}")
                for ri, row in enumerate(tbl):
                    cells = [str(c)[:20] if c is not None else "" for c in row]
                    logger.info(f"  R{ri}: {cells}")


def main() -> int:
    from google.cloud import storage
    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")

    samples = [
        "monthly/docs/8218/202505_8218_4月度の月次情報詳細はこちら[PDF_116KB]_bf70f864.pdf",
        "monthly/docs/8218/202602_8218_1月度の月次情報詳細はこちら[PDF_116KB]_bdd83aa1.pdf",
        "monthly/docs/8218/202604_8218_最新の月次情報詳細はこちら[PDF_118KB]_7773a4d1.pdf",
    ]
    for s in samples:
        dump_tables(s, bucket)
    return 0


if __name__ == "__main__":
    sys.exit(main())

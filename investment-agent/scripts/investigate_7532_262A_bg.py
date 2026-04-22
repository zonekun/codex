#!/usr/bin/env python3
"""7532 巨大小売 / 262A Zoff 両方の NG 詳細調査.

7532: 巨大小売なので records / BC / PDF 構造要確認.
262A: 「Zoff 全店 店舗数」 マッピングは正しそうだが数字ずれ.
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


def investigate_one(ticker: str, bucket, bq) -> None:
    logger.info(f"\n{'=' * 90}")
    logger.info(f"ticker {ticker}")
    logger.info(f"{'=' * 90}")

    adapter_path = ROOT / f"data/monthly_adapters/{ticker}.json"
    if adapter_path.exists():
        with adapter_path.open(encoding="utf-8") as f:
            adp = json.load(f)
        logger.info(f"--- adapter ---")
        for k, v in adp.items():
            if k == "fields":
                logger.info(f"  fields: {len(v)}")
                for ff in v:
                    logger.info(f"    key={ff.get('key','')[:35]:35s} bc_key={ff.get('bc_key','')[:30]:30s} regex={str(ff.get('row_label_regex',''))[:80]}")
            else:
                logger.info(f"  {k}: {str(v)[:120]}")
    else:
        logger.info(f"adapter.json なし")

    # compare
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    logger.info(f"\n--- compare ({ticker}) ---")
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
        for rec in sorted(records, key=lambda r: str(r.get('year_month','')))[-6:]:
            logger.info(f"  {rec.get('year_month')}: {rec.get('fields', {})}")

    # PDF dump (latest)
    logger.info(f"\n--- PDF 最新テーブル dump ({ticker}) ---")
    try:
        import pdfplumber
        blobs = sorted([b for b in bucket.list_blobs(prefix=f"tdnet/{ticker}/") if b.name.endswith(".pdf")
                        and ("月次" in b.name or "月度" in b.name or "売上" in b.name or "月間" in b.name)],
                       key=lambda b: b.name, reverse=True)
        if blobs:
            blob = blobs[0]
            logger.info(f"  file: {Path(blob.name).name}")
            pdf_bytes = blob.download_as_bytes()
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for pi, page in enumerate(pdf.pages[:2]):
                    ptext = page.extract_text() or ""
                    logger.info(f"  page {pi+1} text length: {len(ptext)}")
                    tables = page.extract_tables()
                    logger.info(f"    tables: {len(tables)}")
                    for ti, tbl in enumerate(tables):
                        logger.info(f"    [Table #{ti}] rows={len(tbl)} cols={len(tbl[0]) if tbl else 0}")
                        for ri, row in enumerate(tbl[:15]):
                            cells = [str(c)[:18] if c is not None else '' for c in row]
                            logger.info(f"      R{ri}: {cells}")
    except Exception as e:
        logger.warning(f"  PDF dump 失敗: {e}")


def main() -> int:
    from google.cloud import bigquery, storage
    bq = bigquery.Client(project="gmailpj-357912")
    storage_client = storage.Client(project="gmailpj-357912")
    bucket = storage_client.bucket("stock_data_1930932")

    for t in ["7532", "262A"]:
        investigate_one(t, bucket, bq)

    return 0


if __name__ == "__main__":
    sys.exit(main())

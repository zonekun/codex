#!/usr/bin/env python3
"""9616 NG 調査 (同じ値が2ヶ月連続する疑い).

compare + records を年月並びで dump し、連続重複パターンを可視化する.
BC 値との突き合わせで どこがズレているか特定.
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
    ticker = "9616"

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
                    logger.info(f"    key={ff.get('key','')[:35]:35s} bc_key={ff.get('bc_key','')[:30]:30s} regex={str(ff.get('row_label_regex',''))[:60]}")
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
    sys.stdout.write(r.stdout[-3000:])

    # records 全件 + 連続重複検出
    rec_blob = bucket.blob(f"monthly/record/{ticker}/monthly_records.json")
    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        logger.info(f"\n--- records ({len(records)} 件) ---")
        # 年月並びで全件表示
        sorted_records = sorted(records, key=lambda r: str(r.get("year_month", "")))
        for rec in sorted_records:
            logger.info(f"  {rec.get('year_month')}: {rec.get('fields', {})}")

        # 連続重複検出
        logger.info("\n--- 連続重複 (同値2ヶ月以上連続) ---")
        keys_all = set()
        for r in sorted_records:
            keys_all |= set(r.get("fields", {}).keys())
        for k in sorted(keys_all):
            prev_val = None
            prev_ym = None
            dup_start = None
            for rec in sorted_records:
                v = rec.get("fields", {}).get(k)
                ym = rec.get("year_month")
                if v is not None and v == prev_val:
                    if dup_start is None:
                        dup_start = prev_ym
                    logger.info(f"  ⚠️  [{k}] {dup_start} → {ym}: {v} 連続")
                else:
                    dup_start = None
                prev_val = v
                prev_ym = ym

    # TDnet 全件タイトル・日付
    logger.info("\n--- TDnet 月次文書一覧 ---")
    sql = f"""
    SELECT SUBMISSION_DATE, DOC_TITLE
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE TICKER = '{ticker}'
      AND MAIN_CATEGORY = '月次開示'
      AND SUBMISSION_DATE >= '2025-01-01'
    GROUP BY SUBMISSION_DATE, DOC_TITLE
    ORDER BY SUBMISSION_DATE DESC
    LIMIT 20
    """
    for r2 in bq.query(sql).result():
        logger.info(f"  {r2.SUBMISSION_DATE}  {r2.DOC_TITLE}")

    # 最新 2 件の full_text head
    logger.info("\n--- TDnet 最新2件 full_text ---")
    sql2 = f"""
    SELECT SUBMISSION_DATE, DOC_TITLE, STRING_AGG(CHUNK_TEXT, ' ') AS full_text
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE TICKER = '{ticker}'
      AND MAIN_CATEGORY = '月次開示'
      AND SUBMISSION_DATE >= '2025-12-01'
    GROUP BY SUBMISSION_DATE, DOC_TITLE
    ORDER BY SUBMISSION_DATE DESC
    LIMIT 2
    """
    for doc in bq.query(sql2).result():
        logger.info(f"\n  SUB_DATE={doc.SUBMISSION_DATE} TITLE={doc.DOC_TITLE}")
        logger.info(f"  full_text head (700):")
        logger.info(f"    {doc.full_text[:700]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

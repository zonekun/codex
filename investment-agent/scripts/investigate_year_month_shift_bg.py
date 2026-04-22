#!/usr/bin/env python3
"""2664/3391 year_month ずれ疑い調査.

suggested bc_key は一致（identity）だが、records の year_month と BC の year_month
のマッピングが 1 月ズレている可能性を検証する.

チェック:
  1. records の year_month 分布
  2. BC の year_month 分布
  3. 値の時系列相関 (records[ym] vs BC[ym] / records[ym] vs BC[ym-1] / records[ym] vs BC[ym+1])
  4. 最も相関が高い offset を特定
"""
from __future__ import annotations

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


def investigate(ticker: str, bucket, bc_df) -> None:
    logger.info(f"\n{'=' * 80}")
    logger.info(f"ticker {ticker}")
    logger.info(f"{'=' * 80}")

    # records
    rec_blob = bucket.blob(f"monthly/record/{ticker}/monthly_records.json")
    if not rec_blob.exists():
        logger.info("  records 無し")
        return
    data = json.loads(rec_blob.download_as_text())
    records = data.get("records") if isinstance(data, dict) else data
    logger.info(f"  records: {len(records)} 件")

    # BC rows
    bc_sub = bc_df[bc_df["ticker"] == ticker]
    logger.info(f"  BC rows: {len(bc_sub)} 件")

    # fields 一覧
    keys = set()
    for r in records:
        keys |= set(r.get("fields", {}).keys())
    logger.info(f"  records fields: {sorted(keys)}")
    bc_fields = sorted(set(bc_sub["field"].tolist())) if len(bc_sub) else []
    logger.info(f"  BC fields: {bc_fields[:10]}")

    # 1 field を選んで offset 比較
    for our_key in sorted(keys):
        # 同名 BC field を探す
        bc_match = bc_sub[bc_sub["field"] == our_key]
        if not len(bc_match):
            continue
        logger.info(f"\n  === {our_key} ===")

        # records year_month → value
        rec_map = {r["year_month"]: r["fields"].get(our_key) for r in records
                   if r["fields"].get(our_key) is not None}
        bc_map = {}
        for _, row in bc_match.iterrows():
            try:
                bc_map[row["year_month"]] = float(row["value"])
            except (ValueError, TypeError):
                continue

        # offset 0, -1, +1 で一致率
        for offset in [0, -1, +1]:
            match_count = 0
            total = 0
            for ym, our_v in rec_map.items():
                # offset 月後の BC と比較
                from datetime import datetime as _dt, timedelta
                try:
                    ym_dt = _dt.strptime(ym, "%Y-%m")
                except ValueError:
                    continue
                # offset を月単位で加算
                y, m = ym_dt.year, ym_dt.month + offset
                while m <= 0:
                    m += 12
                    y -= 1
                while m > 12:
                    m -= 12
                    y += 1
                bc_ym = f"{y:04d}-{m:02d}"
                bc_v = bc_map.get(bc_ym)
                if bc_v is None:
                    continue
                total += 1
                try:
                    if abs(float(our_v) - bc_v) <= 1.0:
                        match_count += 1
                except (ValueError, TypeError):
                    pass
            ratio = match_count / total if total else 0
            logger.info(f"    offset={offset:+d}: {match_count}/{total} = {ratio:.2%}")


def main() -> int:
    import pandas as pd
    from google.cloud import storage
    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")

    bc_csv = ROOT / "data/csv/bc_monthly_kpi.csv"
    logger.info(f"BC CSV: {bc_csv}")
    bc_df = pd.read_csv(bc_csv, encoding="utf-8-sig", dtype=str)
    logger.info(f"  rows: {len(bc_df)}, columns: {list(bc_df.columns)[:10]}")

    for t in ["2664", "3391"]:
        investigate(t, bucket, bc_df)

    return 0


if __name__ == "__main__":
    sys.exit(main())

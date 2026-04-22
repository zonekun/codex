#!/usr/bin/env python3
"""複数 ticker を一気に inspect (records/BC/compare)、structured summary 出力.

Claude の効率的解析用.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent


def inspect_one(ticker: str, bucket) -> dict:
    result = {"ticker": ticker}

    # adapter summary
    adapter_path = ROOT / f"data/monthly_adapters/{ticker}.json"
    if adapter_path.exists():
        with adapter_path.open(encoding="utf-8") as f:
            adp = json.load(f)
        result["adapter"] = {
            "source": adp.get("source"),
            "extraction_method": adp.get("extraction_method"),
            "doc_title_pattern": adp.get("doc_title_pattern"),
            "year_from_title_regex": adp.get("year_from_title_regex"),
            "month_from_title_regex": adp.get("month_from_title_regex"),
            "use_fy_history_correction": adp.get("use_fy_history_correction"),
            "year_month_from_submission_minus_1": adp.get("year_month_from_submission_minus_1"),
            "overwrite_past_months": adp.get("overwrite_past_months"),
            "fields_count": len(adp.get("fields", [])),
            "fields_keys": [f.get("key", "") for f in adp.get("fields", [])][:8],
        }
    else:
        result["adapter"] = None

    # records summary
    rec_blob = bucket.blob(f"monthly/record/{ticker}/monthly_records.json")
    if rec_blob.exists():
        import json as _j
        data = _j.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        records_sorted = sorted(records, key=lambda r: str(r.get('year_month', '')))
        result["records"] = {
            "count": len(records),
            "ym_min": records_sorted[0].get("year_month") if records else None,
            "ym_max": records_sorted[-1].get("year_month") if records else None,
            "latest_3": [{"ym": r.get("year_month"), "fields": r.get("fields", {})}
                          for r in records_sorted[-3:]],
        }
    else:
        result["records"] = None

    return result


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", nargs="+", required=True)
    p.add_argument("--output", default="")
    args = p.parse_args()

    from google.cloud import storage
    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")

    out_path = args.output or f"data/logs/agent_bc_bulk_inspect.json"
    results = []
    for i, t in enumerate(args.tickers, 1):
        print(f"[{i}/{len(args.tickers)}] {t}", flush=True)
        try:
            results.append(inspect_one(t, bucket))
        except Exception as e:
            results.append({"ticker": t, "error": str(e)})

    with Path(out_path).open("w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n出力: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""7601 / 7611 詳細調査 + 独自ストラクチャー vs BC 突合の判断材料収集."""
from __future__ import annotations

import csv as _csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
from google.cloud import storage  # noqa: E402

sys.path.insert(0, "scripts")
from extract_monthly_data import _extract_pdf_text  # type: ignore


def log(msg: str) -> None:
    print(f"[{datetime.now(JST):%H:%M:%S}] {msg}", flush=True)


def inspect(ticker: str, gcs) -> None:
    log(f"========== {ticker} ==========")
    b = gcs.bucket("stock_data_1930932")

    # reconcile 提案
    log("reconcile 提案:")
    with open("data/logs/bc_key_reverse_mapping_20260418_234825.csv", encoding="cp932") as f:
        for r in _csv.DictReader(f):
            if r["ticker"] == ticker:
                log(f"  [{r['our_key']}] status={r['status']} ratio={r['match_ratio']} current_bc={r['current_bc_field']!r} → suggest={r['suggested_bc_key']!r} adj={r['adjustment']}")
                log(f"    samples: {r['our_value_samples']} / {r['bc_value_samples']}")

    # adapter
    log("adapter:")
    try:
        a = json.loads(b.blob(f"monthly/meta/{ticker}/extract_adapter.json").download_as_text())
        log(f"  source={a.get('source')}, method={a.get('extraction_method')}, pattern={a.get('doc_title_pattern')!r}")
        for fld in a.get("fields", []):
            log(f"  field: key={fld.get('key')!r} bc_key={fld.get('bc_key')!r}")
            if fld.get("row_label_regex"):
                log(f"    regex={fld['row_label_regex'][:90]}")
            if fld.get("description"):
                log(f"    desc={fld['description'][:100]}")
    except Exception as e:
        log(f"  err: {e}")

    # records
    log("records:")
    try:
        d = json.loads(b.blob(f"monthly/record/{ticker}/monthly_records.json").download_as_text())
        for r in sorted(d.get("records", []), key=lambda x: x["year_month"])[-4:]:
            log(f"  {r['year_month']}: {r.get('fields')}  doc={r.get('doc_title','')[:50]}")
    except Exception as e:
        log(f"  records err: {e}")

    # BC fields
    log("BC fields (recent 3 samples each):")
    bc_fields: dict[str, list[tuple[str, str]]] = {}
    with open("data/csv/bc_monthly_kpi.csv", encoding="utf-8-sig") as f:
        for r in _csv.DictReader(f):
            if r.get("ticker") == ticker:
                bc_fields.setdefault(r["field"], []).append((r["year_month"], r["value"]))
    for fname, samples in sorted(bc_fields.items()):
        log(f"  - {fname}: {samples[-3:]}")

    # PDF latest
    blobs = [x for x in b.list_blobs(prefix=f"tdnet/{ticker}/") if x.name.endswith(".pdf") and ("月次" in x.name or "速報" in x.name)]
    if blobs:
        log("直近月次 PDF リスト:")
        for x in sorted(blobs, key=lambda n: n.name)[-3:]:
            log(f"  {x.name.split('/')[-1][:90]}")
        latest = sorted(blobs, key=lambda n: n.name)[-1]
        log(f"最新 PDF text:")
        text = _extract_pdf_text(latest.download_as_bytes())
        for line in text[:2500].splitlines():
            log(f"  {line}")
    log("")


def main() -> None:
    gcs = storage.Client(project="stock-data-1930932")
    for ticker in ("7601", "7611"):
        inspect(ticker, gcs)


if __name__ == "__main__":
    main()

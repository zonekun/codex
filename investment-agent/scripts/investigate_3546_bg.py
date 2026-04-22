#!/usr/bin/env python3
"""3546 既存店 客数（前年同月比）提案の妥当性を PDF 原本で検証."""
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


def main() -> None:
    gcs = storage.Client(project="stock-data-1930932")
    b = gcs.bucket("stock_data_1930932")
    ticker = "3546"

    # reconcile CSV の提案
    log(f"=== {ticker} reconcile 提案 ===")
    with open("data/logs/bc_key_reverse_mapping_20260418_234825.csv", encoding="cp932") as f:
        for r in _csv.DictReader(f):
            if r["ticker"] == ticker:
                log(f"  [{r['our_key']}] status={r['status']} ratio={r['match_ratio']}")
                log(f"    current: bc_field={r['current_bc_field']!r}  diff={r['current_diff']}")
                log(f"    suggest: bc_key={r['suggested_bc_key']!r}  adj={r['adjustment']}  yoy={r['suggested_yoy_offset']}  unit={r['suggested_unit_scale']}")
                log(f"    samples: {r['our_value_samples']} / {r['bc_value_samples']}")

    log(f"=== {ticker} adapter ===")
    a = json.loads(b.blob(f"monthly/meta/{ticker}/extract_adapter.json").download_as_text())
    log(f"  source={a.get('source')}, method={a.get('extraction_method')}")
    log(f"  doc_title_pattern={a.get('doc_title_pattern')!r}")
    for fld in a.get("fields", []):
        log(f"  field: key={fld.get('key')!r} bc_key={fld.get('bc_key')!r} yoy={fld.get('yoy_offset')} unit={fld.get('unit_scale')}")
        if fld.get("row_label_regex"):
            log(f"    regex={fld['row_label_regex'][:100]}")

    log(f"=== {ticker} records ===")
    d = json.loads(b.blob(f"monthly/record/{ticker}/monthly_records.json").download_as_text())
    for rec in sorted(d.get("records", []), key=lambda r: r["year_month"])[-5:]:
        log(f"  {rec['year_month']}: {rec.get('fields')}  doc={rec.get('doc_title','')[:60]}")

    log(f"=== {ticker} BC fields ===")
    bc_fields: dict[str, list[tuple[str, str]]] = {}
    with open("data/csv/bc_monthly_kpi.csv", encoding="utf-8-sig") as f:
        for r in _csv.DictReader(f):
            if r.get("ticker") == ticker:
                bc_fields.setdefault(r["field"], []).append((r["year_month"], r["value"]))
    for fname, samples in sorted(bc_fields.items()):
        log(f"  - {fname}: recent={samples[-4:]}")

    blobs = [x for x in b.list_blobs(prefix=f"tdnet/{ticker}/") if x.name.endswith(".pdf") and ("月次" in x.name or "速報" in x.name)]
    if not blobs:
        return
    log(f"=== {ticker} 直近 PDF ===")
    for x in sorted(blobs, key=lambda n: n.name)[-3:]:
        log(f"  {x.name.split('/')[-1][:90]}")
    latest = sorted(blobs, key=lambda n: n.name)[-1]
    log(f"=== {ticker} 最新 PDF ===")
    text = _extract_pdf_text(latest.download_as_bytes())
    for line in text[:2500].splitlines():
        log(f"    {line}")


if __name__ == "__main__":
    main()

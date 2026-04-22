#!/usr/bin/env python3
"""3174 既存店 3 field の値の正体を PDF 原本から検証するバックグラウンド調査."""
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
    ticker = "3174"

    # 1. adapter
    log(f"=== {ticker} adapter ===")
    a = json.loads(b.blob(f"monthly/meta/{ticker}/extract_adapter.json").download_as_text())
    log(f"  source={a.get('source')}, method={a.get('extraction_method')}")
    log(f"  doc_title_pattern={a.get('doc_title_pattern')!r}")
    for fld in a.get("fields", []):
        log(f"  field: key={fld.get('key')!r} bc_key={fld.get('bc_key')!r} yoy={fld.get('yoy_offset')} unit={fld.get('unit_scale')}")
        if fld.get("row_label_regex"):
            log(f"    regex={fld['row_label_regex'][:80]}")
        if fld.get("description"):
            log(f"    desc={fld['description'][:120]}")

    # 2. records 全月
    log(f"=== {ticker} records ===")
    d = json.loads(b.blob(f"monthly/record/{ticker}/monthly_records.json").download_as_text())
    log(f"  updated_at: {d.get('updated_at')}")
    for rec in sorted(d.get("records", []), key=lambda r: r["year_month"])[-6:]:
        log(f"  {rec['year_month']}: {rec.get('fields')}  doc={rec.get('doc_title','')[:60]}")

    # 3. BC 側 3174 の field 一覧
    log(f"=== {ticker} BC fields ===")
    bc_fields: dict[str, list[tuple[str, str]]] = {}
    with open("data/csv/bc_monthly_kpi.csv", encoding="utf-8-sig") as f:
        for r in _csv.DictReader(f):
            if r.get("ticker") == ticker:
                bc_fields.setdefault(r["field"], []).append((r["year_month"], r["value"]))
    for fname, samples in sorted(bc_fields.items()):
        log(f"  - {fname}: samples={samples[:3]}")

    # 4. 直近 PDF の内容
    blobs = [x for x in b.list_blobs(prefix=f"tdnet/{ticker}/") if x.name.endswith(".pdf") and ("月次" in x.name or "速報" in x.name)]
    if not blobs:
        log(f"  (no 月次 PDF)")
        return
    log(f"=== {ticker} 直近月次 PDF リスト ===")
    for x in sorted(blobs, key=lambda n: n.name)[-5:]:
        log(f"  {x.name.split('/')[-1][:90]}")
    latest = sorted(blobs, key=lambda n: n.name)[-1]
    log(f"=== {ticker} 最新 PDF 内容 ===")
    log(f"  name: {latest.name.split('/')[-1]}")
    text = _extract_pdf_text(latest.download_as_bytes())
    for line in text[:3000].splitlines():
        log(f"    {line}")


if __name__ == "__main__":
    main()

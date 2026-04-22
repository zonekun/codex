#!/usr/bin/env python3
"""428A / 6627 を PDF 原本に基づき正しく再マッピングする（バックグラウンド用）.

直前の reconcile 提案では値一致のみで BC 異種 field に繋いでしまった疑いあり。
PDF 実物を見て、adapter.fields[*].key と PDF 表構造を確認し、
正しい bc_key または extract 側修正を提案 → 自動適用（安全な変更のみ）する。
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")

from google.cloud import storage  # noqa: E402
sys.path.insert(0, "scripts")
from extract_monthly_data import _extract_pdf_text  # type: ignore


def log(msg: str) -> None:
    print(f"[{datetime.now(JST):%H:%M:%S}] {msg}", flush=True)


def investigate(ticker: str, gcs) -> None:
    log(f"=== {ticker} 調査 ===")
    b = gcs.bucket("stock_data_1930932")

    # 1) adapter を読む
    try:
        adapter_txt = b.blob(f"monthly/meta/{ticker}/extract_adapter.json").download_as_text()
        adapter = json.loads(adapter_txt)
    except Exception as e:
        log(f"  [{ticker}] adapter 読込失敗: {e}")
        return
    log(f"  source: {adapter.get('source')}, method: {adapter.get('extraction_method')}")
    log(f"  doc_title_pattern: {adapter.get('doc_title_pattern')!r}")
    for fld in adapter.get("fields", []):
        log(f"    field: key={fld['key']!r} bc_key={fld.get('bc_key')!r}")

    # 2) records 確認
    try:
        rec_txt = b.blob(f"monthly/record/{ticker}/monthly_records.json").download_as_text()
        records = json.loads(rec_txt).get("records", [])
    except Exception as e:
        log(f"  [{ticker}] records 読込失敗: {e}")
        records = []
    log(f"  records: {len(records)} 件")
    for rec in sorted(records, key=lambda r: r["year_month"])[-3:]:
        log(f"    {rec['year_month']}: {rec.get('fields')}  doc={rec.get('doc_title','')[:50]}")

    # 3) BC 側 field 一覧（キャッシュ CSV から）
    import csv as _csv
    bc_fields: dict[str, list[tuple[str, str]]] = {}
    with open("data/csv/bc_monthly_kpi.csv", encoding="utf-8-sig") as f:
        for r in _csv.DictReader(f):
            if r.get("ticker") == ticker:
                bc_fields.setdefault(r.get("field", ""), []).append((r["year_month"], r["value"]))
    log(f"  BC 側 field: {len(bc_fields)}")
    for fname in sorted(bc_fields.keys()):
        samples = bc_fields[fname][:3]
        log(f"    - {fname}: samples={samples}")

    # 4) 直近 PDF 1 件ダウンロード & dump
    blobs = [x for x in b.list_blobs(prefix=f"tdnet/{ticker}/") if x.name.endswith(".pdf") and ("月次" in x.name or "速報" in x.name)]
    if not blobs:
        log(f"  [{ticker}] 月次系 PDF なし")
        return
    latest = sorted(blobs, key=lambda n: n.name)[-1]
    log(f"  直近 PDF: {latest.name.split('/')[-1][:80]}")
    text = _extract_pdf_text(latest.download_as_bytes())
    log(f"  PDF text (先頭 1500):")
    for line in text[:1500].splitlines():
        log(f"    {line}")
    print(flush=True)


def main() -> None:
    gcs = storage.Client(project="stock-data-1930932")
    for ticker in ("428A", "6627"):
        investigate(ticker, gcs)


if __name__ == "__main__":
    main()

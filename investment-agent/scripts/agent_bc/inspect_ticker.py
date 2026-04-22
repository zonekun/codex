#!/usr/bin/env python3
"""1 ticker の事実を全部出す: adapter/records/BC/PDF/compare.

Usage:
  PYTHONUTF8=1 python scripts/agent_bc/inspect_ticker.py --ticker 8218
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", required=True)
    p.add_argument("--pdf-tables", type=int, default=2, help="PDF テーブル dump ページ数")
    args = p.parse_args()
    ticker = args.ticker

    from google.cloud import bigquery, storage
    bq = bigquery.Client(project="gmailpj-357912")
    storage_client = storage.Client(project="gmailpj-357912")
    bucket = storage_client.bucket("stock_data_1930932")

    log(f"{'=' * 90}\nTicker: {ticker}\n{'=' * 90}")

    # 1) adapter
    adapter_path = ROOT / f"data/monthly_adapters/{ticker}.json"
    log("\n--- adapter (local) ---")
    if adapter_path.exists():
        with adapter_path.open(encoding="utf-8") as f:
            adp = json.load(f)
        for k, v in adp.items():
            if k == "fields":
                log(f"  fields: {len(v)}")
                for ff in v:
                    log(f"    key={ff.get('key', '')[:40]:40s} "
                        f"bc_key={str(ff.get('bc_key', ''))[:35]:35s} "
                        f"bc_ignore={ff.get('bc_ignore')} "
                        f"yoy={ff.get('yoy_offset')} "
                        f"unit_scale={ff.get('unit_scale')} "
                        f"regex={str(ff.get('row_label_regex', ''))[:60]}")
            else:
                log(f"  {k}: {str(v)[:150]}")
    else:
        log(f"  adapter なし: {adapter_path}")

    # 2) records (GCS)
    log("\n--- records (GCS) ---")
    rec_blob = bucket.blob(f"monthly/record/{ticker}/monthly_records.json")
    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        log(f"  件数: {len(records)}")
        for rec in sorted(records, key=lambda r: str(r.get('year_month', '')))[-6:]:
            log(f"  {rec.get('year_month')}: {rec.get('fields', {})}")
    else:
        log("  records なし")

    # 3) BC CSV
    log("\n--- BC CSV 統計 ---")
    try:
        import pandas as pd
        bc_df = pd.read_csv(ROOT / "data/csv/bc_monthly_kpi.csv",
                            encoding="utf-8-sig", dtype=str)
        bc_sub = bc_df[bc_df["ticker"] == ticker]
        log(f"  行数: {len(bc_sub)}")
        if len(bc_sub):
            fields = sorted(set(bc_sub["field"]))
            log(f"  fields ({len(fields)}):")
            for f in fields:
                fs = bc_sub[bc_sub["field"] == f]
                log(f"    {f[:45]:45s}  {fs['year_month'].min()} 〜 {fs['year_month'].max()}  ({len(fs)} rows)")
    except Exception as e:
        log(f"  BC CSV エラー: {e}")

    # 4) compare 実行
    log("\n--- compare ---")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", ticker],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT), timeout=300,
    )
    sys.stdout.write(r.stdout[-2500:])

    # 5) PDF テーブル dump (最新 1 件)
    log("\n--- PDF テーブル dump (最新) ---")
    try:
        import pdfplumber
        prefixes = [f"monthly/docs/{ticker}/", f"tdnet/{ticker}/"]
        blob = None
        for pref in prefixes:
            blobs = sorted([b for b in bucket.list_blobs(prefix=pref)
                            if b.name.endswith(".pdf")],
                           key=lambda b: b.name, reverse=True)
            if blobs:
                blob = blobs[0]
                break
        if blob:
            log(f"  file: {Path(blob.name).name}")
            pdf_bytes = blob.download_as_bytes()
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for pi, page in enumerate(pdf.pages[:args.pdf_tables]):
                    tables = page.extract_tables()
                    ptext = page.extract_text() or ""
                    log(f"  page {pi+1}: text_len={len(ptext)}, tables={len(tables)}")
                    log(f"    text head (200): {ptext[:200]!r}")
                    for ti, tbl in enumerate(tables):
                        log(f"    [Table #{ti}] rows={len(tbl)} cols={len(tbl[0]) if tbl else 0}")
                        for ri, row in enumerate(tbl[:15]):
                            cells = [str(c)[:20] if c is not None else '' for c in row]
                            log(f"      R{ri}: {cells}")
        else:
            log("  PDF なし")
    except Exception as e:
        log(f"  PDF dump 失敗: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

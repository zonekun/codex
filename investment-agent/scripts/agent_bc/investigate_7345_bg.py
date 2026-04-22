#!/usr/bin/env python3
"""7345 アイ・パートナーズフィナンシャル 33.3% を 100% に持っていく調査.

現状: 3 records (2024-07, 2025-09, 2026-03) で 2 NG.
想定: TDnet 文書が「重要な経営指標の推移」タイトルのため、adapter pattern/regex が甘く一部 field が取れてない.
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent


def main() -> int:
    from google.cloud import bigquery, storage
    import pandas as pd
    bq = bigquery.Client(project="gmailpj-357912")
    bucket = storage.Client(project="gmailpj-357912").bucket("stock_data_1930932")
    ticker = "7345"

    # 1. adapter 状態
    adp_path = ROOT / f"data/monthly_adapters/{ticker}.json"
    print("=== adapter ===")
    if adp_path.exists():
        a = json.loads(adp_path.read_text(encoding="utf-8"))
        for k, v in a.items():
            if k == "fields":
                for ff in v:
                    print(f"  key={ff.get('key','')[:40]:40s} bc_key={ff.get('bc_key','')[:30]:30s} "
                          f"regex={str(ff.get('row_label_regex',''))[:80]}")
            else:
                print(f"  {k}: {str(v)[:120]}")

    # 2. records
    print("\n=== records ===")
    b = bucket.blob(f"monthly/record/{ticker}/monthly_records.json")
    if b.exists():
        d = json.loads(b.download_as_text())
        recs = d.get("records") if isinstance(d, dict) else d
        for r in sorted(recs, key=lambda r: str(r.get('year_month',''))):
            print(f"  {r.get('year_month')}: {r.get('fields', {})}")

    # 3. BC
    print("\n=== BC ===")
    bc_df = pd.read_csv(ROOT / "data/csv/bc_monthly_kpi.csv", encoding="utf-8-sig", dtype=str)
    sub = bc_df[bc_df["ticker"] == ticker]
    for f in sorted(set(sub["field"])):
        fs = sub[sub["field"] == f]
        latest = fs.sort_values("year_month", ascending=False).head(3)
        latest_str = ', '.join(f"{r['year_month']}={r['value']}" for _, r in latest.iterrows())
        print(f"  {f}: ym {fs['year_month'].min()}-{fs['year_month'].max()}, latest=[{latest_str}]")

    # 4. compare で NG 確認
    print("\n=== compare NG ===")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", ticker],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT), timeout=300,
    )
    for ln in r.stdout.splitlines():
        if "❌" in ln or "⚠️" in ln or "一致率" in ln:
            print(f"  {ln.strip()[:200]}")

    # 5. TDnet 最新 PDF の本文確認（regex 検討）
    print("\n=== TDnet 最新 3 件 full_text 先頭 ===")
    sql = f"""
    SELECT SUBMISSION_DATE, DOC_TITLE, STRING_AGG(CHUNK_TEXT, ' ') AS full_text
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE TICKER = '{ticker}'
      AND MAIN_CATEGORY = '月次開示'
    GROUP BY SUBMISSION_DATE, DOC_TITLE
    ORDER BY SUBMISSION_DATE DESC LIMIT 3
    """
    for row in bq.query(sql).result():
        print(f"\n--- {row.SUBMISSION_DATE} {row.DOC_TITLE} ---")
        txt = (row.full_text or "")[:1500]
        print(txt)

    # 6. PDF 最新 tables
    print("\n=== PDF 最新 tables ===")
    import pdfplumber
    blobs = sorted([b for b in bucket.list_blobs(prefix=f"tdnet/{ticker}/") if b.name.endswith(".pdf")],
                   key=lambda b: b.name, reverse=True)
    if blobs:
        blob = blobs[0]
        print(f"file: {Path(blob.name).name}")
        pdf_bytes = blob.download_as_bytes()
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for pi, page in enumerate(pdf.pages[:2]):
                    tables = page.extract_tables()
                    ptext = (page.extract_text() or "")[:500]
                    print(f"\npage {pi+1} text head (500): {ptext}")
                    for ti, tbl in enumerate(tables):
                        print(f"  [Table #{ti}] rows={len(tbl)} cols={len(tbl[0]) if tbl else 0}")
                        for ri, row in enumerate(tbl[:15]):
                            cells = [str(c)[:25] if c else '' for c in row]
                            print(f"    R{ri}: {cells}")
        except Exception as e:
            print(f"PDF err: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

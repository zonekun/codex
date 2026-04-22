#!/usr/bin/env python3
"""14 escalation 銘柄の深堀調査: records vs BC の値ズレ原因を特定する資料一式を dump.

出力項目 (per ticker):
  1. adapter 全内容
  2. records 最新 3 件 (全 field)
  3. BC 同 ticker の field 一覧 (field 名 / ym範囲 / 最新値)
  4. records と BC の field 名の exact / fuzzy 対応関係
  5. TDnet 最新 1 文書の full_text 先頭 1500 文字
  6. PDF 最新 1 件のテーブル全て (pdfplumber.extract_tables)
  7. records と BC で同 ym 同 field の数値比較 (diff/ratio)
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent


def inspect(ticker: str, bucket, bq, bc_df) -> dict:
    import pdfplumber
    r = {"ticker": ticker}

    # adapter
    p = ROOT / f"data/monthly_adapters/{ticker}.json"
    r["adapter"] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    # records
    rec_blob = bucket.blob(f"monthly/record/{ticker}/monthly_records.json")
    records = None
    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        r["records_count"] = len(records)
        sorted_recs = sorted(records, key=lambda x: str(x.get("year_month", "")))
        r["records_latest3"] = sorted_recs[-3:]
        r["records_ym_min"] = sorted_recs[0].get("year_month") if sorted_recs else None
        r["records_ym_max"] = sorted_recs[-1].get("year_month") if sorted_recs else None
    else:
        r["records_count"] = 0

    # BC
    bc_sub = bc_df[bc_df["ticker"] == ticker]
    bc_fields = {}
    for f in sorted(set(bc_sub["field"])):
        sub = bc_sub[bc_sub["field"] == f]
        latest_3 = sub.sort_values("year_month", ascending=False).head(3)
        bc_fields[f] = {
            "rows": len(sub),
            "ym_min": sub["year_month"].min(),
            "ym_max": sub["year_month"].max(),
            "latest": [(r_["year_month"], r_["value"]) for _, r_ in latest_3.iterrows()],
        }
    r["bc_fields"] = bc_fields

    # ym+field 一致比較
    r["value_compare"] = []
    if records:
        for rec in sorted_recs[-5:]:
            ym = rec.get("year_month", "")
            for k, v in rec.get("fields", {}).items():
                bc_hit = bc_sub[(bc_sub["field"] == k) & (bc_sub["year_month"] == ym)]
                bc_v = float(bc_hit.iloc[0]["value"]) if len(bc_hit) else None
                try:
                    our_v = float(v)
                except (ValueError, TypeError):
                    our_v = None
                if our_v is not None:
                    r["value_compare"].append({
                        "ym": ym, "field": k, "ours": our_v,
                        "bc": bc_v,
                        "diff": (bc_v - our_v) if (bc_v is not None) else None,
                    })

    # TDnet 最新 full_text
    sql = f"""
    SELECT SUBMISSION_DATE, DOC_TITLE, STRING_AGG(CHUNK_TEXT, ' ') AS full_text
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE TICKER = '{ticker}' AND MAIN_CATEGORY = '月次開示'
    GROUP BY SUBMISSION_DATE, DOC_TITLE
    ORDER BY SUBMISSION_DATE DESC LIMIT 1
    """
    try:
        for row in bq.query(sql).result():
            r["tdnet_latest"] = {
                "date": str(row.SUBMISSION_DATE),
                "title": row.DOC_TITLE,
                "text_head": (row.full_text or "")[:1500],
            }
    except Exception as e:
        r["tdnet_latest"] = {"error": str(e)}

    # PDF dump (最新 1 件)
    r["pdf_tables"] = []
    for prefix in [f"monthly/docs/{ticker}/", f"tdnet/{ticker}/"]:
        blobs = sorted([b for b in bucket.list_blobs(prefix=prefix)
                        if b.name.endswith(".pdf")],
                       key=lambda b: b.name, reverse=True)
        if blobs:
            blob = blobs[0]
            r["pdf_latest_file"] = Path(blob.name).name
            try:
                pdf_bytes = blob.download_as_bytes()
                with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                    for pi, page in enumerate(pdf.pages[:2]):
                        ptext = (page.extract_text() or "")[:800]
                        tables = page.extract_tables()
                        r["pdf_tables"].append({
                            "page": pi + 1,
                            "text_head": ptext,
                            "tables": [[[str(c)[:25] if c else "" for c in row]
                                        for row in tbl[:12]]
                                       for tbl in tables],
                        })
            except Exception as e:
                r["pdf_error"] = str(e)
            break
    return r


def main() -> int:
    import pandas as pd
    from google.cloud import bigquery, storage
    bq = bigquery.Client(project="gmailpj-357912")
    bucket = storage.Client(project="gmailpj-357912").bucket("stock_data_1930932")
    bc_df = pd.read_csv(ROOT / "data/csv/bc_monthly_kpi.csv",
                        encoding="utf-8-sig", dtype=str)

    targets = ["2685", "2778", "3199", "3543", "3608", "3612", "3690",
               "6036", "6045", "6617", "7134", "7422", "7445", "7506"]
    all_data = []
    for i, t in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] {t}", flush=True)
        try:
            all_data.append(inspect(t, bucket, bq, bc_df))
        except Exception as e:
            all_data.append({"ticker": t, "error": str(e)})
    out = ROOT / "data/logs/deep_inspect_14_20260420.json"
    out.write_text(json.dumps(all_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n出力: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

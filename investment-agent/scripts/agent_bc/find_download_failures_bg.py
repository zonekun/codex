#!/usr/bin/env python3
"""ダウンロード失敗 ticker を列挙.

対象: monthly_adapter_index.csv の skip=False + type=scrape_links/eir_api/html_table/pdf_table
判定: monthly/docs/<T>/ に PDF ファイル無し or 極端に少ない
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent


def main() -> int:
    import pandas as pd
    from google.cloud import storage
    bucket = storage.Client(project="gmailpj-357912").bucket("stock_data_1930932")

    df = pd.read_csv(ROOT / "data/monthly_adapter_index.csv",
                     encoding="utf-8-sig", dtype=str)
    target = df[
        (df["skip"].str.lower() == "false")
        & (df["type"].isin(["scrape_links", "eir_api", "html_table", "pdf_table"]))
    ]
    print(f"対象 (non-TDnet active): {len(target)}")

    zero_files: list[dict] = []
    few_files: list[dict] = []
    adapter_missing: list[dict] = []

    for _, row in target.iterrows():
        t = row["ticker"]
        # adapter 存在
        adp_path = ROOT / f"data/monthly_adapters/{t}.json"
        adp_exists = adp_path.exists()

        # GCS PDF 数
        blobs = list(bucket.list_blobs(prefix=f"monthly/docs/{t}/"))
        pdf_count = sum(1 for b in blobs if b.name.endswith(".pdf"))

        # records 確認
        rec_blob = bucket.blob(f"monthly/record/{t}/monthly_records.json")
        rec_exists = rec_blob.exists()

        entry = {
            "ticker": t,
            "company": row["company_name"],
            "type": row["type"],
            "format": row.get("format", ""),
            "url": row["monthly_page_url"],
            "pdf_count": pdf_count,
            "adapter_exists": adp_exists,
            "records_exists": rec_exists,
            "updated_at": row.get("updated_at", ""),
        }
        if pdf_count == 0:
            zero_files.append(entry)
        elif pdf_count <= 5:
            few_files.append(entry)
        if not adp_exists:
            adapter_missing.append(entry)

    print(f"\n=== ダウンロード失敗 (GCS PDF 0 件): {len(zero_files)} 銘柄 ===")
    for e in zero_files[:30]:
        print(f"  {e['ticker']:5s} ({e['type']:14s}) {e['company'][:20]:20s} adp={e['adapter_exists']} rec={e['records_exists']}")
        print(f"        url: {e['url'][:100]}")

    print(f"\n=== PDF 少数 (1-5 件): {len(few_files)} 銘柄 ===")
    for e in few_files[:15]:
        print(f"  {e['ticker']:5s} files={e['pdf_count']} ({e['type']:14s}) {e['company'][:20]}")

    print(f"\n=== adapter 未作成: {len(adapter_missing)} 銘柄 ===")
    for e in adapter_missing[:15]:
        print(f"  {e['ticker']:5s} ({e['type']:14s}) {e['company'][:25]}")

    # CSV 出力
    import csv as _csv
    out = ROOT / "data/logs/download_failures_20260420.csv"
    all_entries = zero_files + few_files
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=list(all_entries[0].keys()) if all_entries else ["ticker"])
        w.writeheader()
        w.writerows(all_entries)
    print(f"\n出力: {out} ({len(all_entries)} 行)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

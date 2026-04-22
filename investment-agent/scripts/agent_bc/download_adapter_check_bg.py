#!/usr/bin/env python3
"""download アダプタ (source=non-tdnet(pdf)/html_table) 精査:
どの ticker が monthly/docs/<T>/ にファイル無し or NG となっているか確認.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent


def main() -> int:
    from google.cloud import storage
    bucket = storage.Client(project="gmailpj-357912").bucket("stock_data_1930932")

    adapter_dir = ROOT / "data/monthly_adapters"
    non_tdnet_tickers = []
    for p in sorted(adapter_dir.glob("*.json")):
        if p.stem.endswith("_download"):
            continue
        try:
            a = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        src = a.get("source", "")
        if src in ("non-tdnet(pdf)", "non-tdnet(html_table)", "pdf", "html_table"):
            non_tdnet_tickers.append((p.stem, src))

    print(f"non-tdnet adapter 総数: {len(non_tdnet_tickers)}")

    no_files = []
    has_files = []
    for t, src in non_tdnet_tickers:
        prefix = f"monthly/docs/{t}/"
        blobs = list(bucket.list_blobs(prefix=prefix, max_results=1))
        if not blobs:
            no_files.append((t, src))
        else:
            has_files.append((t, src, len(list(bucket.list_blobs(prefix=prefix)))))

    print(f"\n=== monthly/docs/<T>/ にファイル無し: {len(no_files)} 銘柄 ===")
    for t, src in no_files[:50]:
        # records 存在確認
        rec = bucket.blob(f"monthly/record/{t}/monthly_records.json").exists()
        # GCS extract_adapter 存在
        meta = bucket.blob(f"monthly/meta/{t}/extract_adapter.json").exists()
        print(f"  {t} ({src}): records={'yes' if rec else 'no'}, meta={'yes' if meta else 'no'}")

    print(f"\n=== ファイルあり: {len(has_files)} 銘柄 (上位 10 表示) ===")
    has_files.sort(key=lambda x: -x[2])
    for t, src, n in has_files[:10]:
        print(f"  {t} ({src}): {n} files")

    # records count 分布
    print(f"\n=== ファイルあり ticker の records 件数分布 ===")
    rec_counts = []
    for t, src, n in has_files:
        rb = bucket.blob(f"monthly/record/{t}/monthly_records.json")
        if rb.exists():
            try:
                d = json.loads(rb.download_as_text())
                recs = d.get("records") if isinstance(d, dict) else d
                rec_counts.append((t, len(recs), n))
            except Exception:
                pass
    rec_counts.sort(key=lambda x: x[1])
    print(f"  records 0件: {sum(1 for _,r,_ in rec_counts if r == 0)}")
    print(f"  records 1-3件: {sum(1 for _,r,_ in rec_counts if 1 <= r <= 3)}")
    print(f"  records 4-10件: {sum(1 for _,r,_ in rec_counts if 4 <= r <= 10)}")
    print(f"  records 11+件: {sum(1 for _,r,_ in rec_counts if r >= 11)}")

    print(f"\n=== records 少ない (0-3件) ticker (上位 30) ===")
    for t, r, n in rec_counts[:30]:
        if r <= 3:
            print(f"  {t}: records={r}, files={n}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""bc_ignore した 20 銘柄の CSV + 月次開示原本 PDF を Dropbox に一括コピー.

出力先: C:/Users/zonekun/Dropbox/stock/temp/bc_ignore_20260420/
 - bc_ignore_summary.csv (ticker, reason, note, BC fields, records_count 等)
 - <TICKER>_<submission_date>_<original_filename>.pdf (全銘柄 同一フォルダ、サブフォルダ無し)
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR = Path(r"C:\Users\zonekun\Dropbox\stock\temp\bc_ignore_20260420")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

TICKERS = [
    "2685", "2778", "3169", "3199", "3608", "3612", "3690", "3931",
    "4015", "6045", "7127", "7134", "7378", "7422", "7445", "7506",
    "7643", "8233", "8244", "9005",
]


def sanitize(s: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]", "_", s)[:180]


def main() -> int:
    import pandas as pd
    from google.cloud import bigquery, storage

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"出力先: {OUT_DIR}")

    # progress jsonl から bc_ignore 情報を復元
    bc_info: dict[str, dict] = {}
    files = [
        ROOT / "data/logs/agent_bc_match_20260419_185521_progress.jsonl",
        ROOT / "data/logs/agent_bc_match_20260420_progress.jsonl",
    ]
    for p in files:
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as f:
            for ln in f:
                if not ln.strip():
                    continue
                e = json.loads(ln)
                if e.get("ticker") in TICKERS and "bc_ignore" in (e.get("applied_fix") or ""):
                    bc_info[e["ticker"]] = e

    # adapter から bc_ignore の理由等を拾う
    storage_client = storage.Client(project="gmailpj-357912")
    bucket = storage_client.bucket("stock_data_1930932")
    bq = bigquery.Client(project="gmailpj-357912")
    bc_df = pd.read_csv(ROOT / "data/csv/bc_monthly_kpi.csv",
                        encoding="utf-8-sig", dtype=str)

    rows: list[dict] = []
    downloaded: int = 0

    for t in TICKERS:
        logger.info(f"\n--- {t} ---")
        # adapter
        adapter_reason = ""
        fields_ignored: list[str] = []
        adapter_path = ROOT / f"data/monthly_adapters/{t}.json"
        if adapter_path.exists():
            a = json.loads(adapter_path.read_text(encoding="utf-8"))
            for fld in a.get("fields", []):
                if fld.get("bc_ignore"):
                    fields_ignored.append(fld.get("key", ""))
                    adapter_reason = fld.get("_bc_ignore_reason") or adapter_reason

        # records
        rec_blob = bucket.blob(f"monthly/record/{t}/monthly_records.json")
        rec_count = 0
        rec_ym_range = ""
        if rec_blob.exists():
            d = json.loads(rec_blob.download_as_text())
            recs = d.get("records") if isinstance(d, dict) else d
            rec_count = len(recs)
            if recs:
                yms = sorted(str(r.get("year_month", "")) for r in recs)
                rec_ym_range = f"{yms[0]}〜{yms[-1]}"

        # BC fields
        bc_sub = bc_df[bc_df["ticker"] == t]
        bc_field_list = sorted(set(bc_sub["field"]))
        bc_ym_max = bc_sub["year_month"].max() if len(bc_sub) else ""

        # log reason
        progress_note = bc_info.get(t, {}).get("note", "")
        progress_fix = bc_info.get(t, {}).get("applied_fix", "")

        rows.append({
            "ticker": t,
            "fields_ignored_count": len(fields_ignored),
            "fields_ignored": " | ".join(fields_ignored[:5]),
            "bc_ignore_reason": adapter_reason,
            "progress_applied_fix": progress_fix,
            "progress_note": progress_note,
            "records_count": rec_count,
            "records_ym_range": rec_ym_range,
            "bc_fields_count": len(bc_field_list),
            "bc_fields": " | ".join(bc_field_list[:5]),
            "bc_ym_max": bc_ym_max,
        })

        # 月次開示原本 PDF を Dropbox にコピー (最新 6 件)
        copied_for_this: int = 0
        for prefix in [f"monthly/docs/{t}/", f"tdnet/{t}/"]:
            blobs = sorted([b for b in bucket.list_blobs(prefix=prefix)
                            if b.name.endswith(".pdf")
                            and ("月次" in b.name or "月度" in b.name or
                                 "売上" in b.name or "速報" in b.name or
                                 "運輸" in b.name or "KPI" in b.name or
                                 "会員" in b.name or "営業" in b.name)],
                           key=lambda b: b.name, reverse=True)
            for blob in blobs[:6]:  # 最新 6 件
                fname = Path(blob.name).name
                # ticker prefix が無ければ付ける
                if not fname.startswith(t):
                    out_name = f"{t}_{fname}"
                else:
                    out_name = fname
                out_name = sanitize(out_name)
                out_path = OUT_DIR / out_name
                if out_path.exists():
                    continue
                try:
                    blob.download_to_filename(str(out_path))
                    copied_for_this += 1
                    downloaded += 1
                except Exception as e:
                    logger.warning(f"  DL 失敗 {fname[:60]}: {e}")
            if copied_for_this > 0:
                break
        logger.info(f"  [{t}] fields_ignored={len(fields_ignored)} records={rec_count} copied={copied_for_this}")

    # CSV 出力
    csv_path = OUT_DIR / "bc_ignore_summary.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    logger.info(f"\n✅ CSV: {csv_path} ({len(rows)} rows)")
    logger.info(f"✅ PDF 原本コピー: {downloaded} ファイル → {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

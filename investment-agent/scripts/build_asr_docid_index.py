"""Phase 3.0: EDINET 有価証券報告書 (docTypeCode=120) の 2013-2026 全期間 docID索引を構築。

アウトプット: data/logs/asr_docid_index.csv
  TICKER, SUBMIT_DATE, DOC_ID, SUBMIT_YEAR, EDINET_CODE, FILER_NAME, DOC_DESCRIPTION

日付キャッシュ (C:\\tmp\\edinet_cache\\dates\\) 利用。API呼び出しは初回 ~3,500日のみ。

Usage:
  PYTHONUTF8=1 python scripts/build_asr_docid_index.py
  PYTHONUTF8=1 python scripts/build_asr_docid_index.py --start 2018-01-01 --end 2026-04-20
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import urllib3

urllib3.disable_warnings()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

API_KEY = os.environ.get("EDINET_API_KEY", "0607081d467e4c7b8229ac4f41ec3408")
EDINET_BASE = "https://api.edinet-fsa.go.jp/api/v2"
DATE_CACHE_DIR = Path(r"C:\tmp\edinet_cache\dates")
OUTPUT_CSV = Path("data/logs/asr_docid_index.csv")
JST = timezone(timedelta(hours=9))


def edinet_list_by_date(d: date, use_cache: bool = True) -> list[dict]:
    if use_cache:
        DATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = DATE_CACHE_DIR / f"{d.isoformat()}.json"
        if cache_path.exists():
            try:
                return json.load(open(cache_path, encoding="utf-8"))
            except Exception:
                pass
    r = requests.get(
        f"{EDINET_BASE}/documents.json",
        params={"date": d.isoformat(), "type": 2, "Subscription-Key": API_KEY},
        timeout=30, verify=False,
    )
    if r.status_code != 200:
        return []
    results = r.json().get("results", []) or []
    if use_cache:
        try:
            slim = [x for x in results if x.get("secCode") or x.get("docTypeCode") in ("120", "240", "290")]
            json.dump(slim, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass
        return slim
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2013-01-01")
    parser.add_argument("--end", default=datetime.now(JST).date().isoformat())
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).date()
    end = datetime.fromisoformat(args.end).date()
    print(f"[info] scan {start} → {end}  ({(end - start).days + 1} days)")

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    rows = []

    d = start
    n_days = (end - start).days + 1
    i = 0
    while d <= end:
        i += 1
        docs = edinet_list_by_date(d)
        for doc in docs:
            if doc.get("docTypeCode") != "120":
                continue
            sec_code = str(doc.get("secCode") or "")
            if len(sec_code) < 4:
                continue
            ticker = sec_code[:4]
            rows.append({
                "TICKER": ticker,
                "SUBMIT_DATE": d.isoformat(),
                "DOC_ID": doc.get("docID"),
                "SUBMIT_YEAR": d.year,
                "EDINET_CODE": doc.get("edinetCode"),
                "FILER_NAME": doc.get("filerName"),
                "DOC_DESCRIPTION": doc.get("docDescription"),
            })
        if i % 100 == 0:
            print(f"  [{i}/{n_days}] {d}  rows={len(rows)}")
        time.sleep(0.05)
        d += timedelta(days=1)

    df = pd.DataFrame(rows)
    df.drop_duplicates(subset=["TICKER", "DOC_ID"], inplace=True)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8")
    print(f"\n[info] saved: {OUTPUT_CSV}  rows={len(df)}")

    # 年別サマリー
    print("\n=== 年別 docID数 ===")
    print(df.groupby("SUBMIT_YEAR").size().to_string())


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""2735 adapter 修正: 期末店舗数 regex の向き反転.

調査結果 (bw18jpcya):
  PDF テキストは以下の並び:
    "1,885 1,890 1,908 1,906 1,905 1,909 1,909\n期末店舗数\n(9) (9)..."
  → 数字が 期末店舗数 の BEFORE にある.
  現在の regex `期末店舗数\\s+(\\d[\\d,]*)` は AFTER を想定 → 0 マッチ.

修正:
  regex を `(\\d[\\d,]*)\\s+(\\d[\\d,]*)...\\s*\\n\\s*期末店舗数` にして
  数字を前に回収. match_occurrence で上/下半期区別は維持.

また売上 row_label_regex は行内 `既存店 99.3%...` で既に動いているはずだが
下半期のみ `（売上高対前年同月比） ３月...` が同一テキスト内で複数現れる.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")

ROOT = Path(__file__).resolve().parent.parent
ADAPTER_PATH = ROOT / "data/monthly_adapters/2735.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


def main() -> int:
    from google.cloud import storage

    with ADAPTER_PATH.open(encoding="utf-8") as f:
        adp = json.load(f)

    # 期末店舗数 regex を修正: 数字 BEFORE label
    # upper half: 6〜7 数字 → match_occurrence=1
    # lower half: 1〜7 数字 → match_occurrence=2
    for f in adp.get("fields", []):
        if f.get("key") == "全店 店舗数":
            f["sections"] = [
                {
                    "months": [9, 10, 11, 12, 1, 2],
                    "row_label_regex": (
                        r"(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+"
                        r"(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+"
                        r"(\d[\d,]*)\s*[\s\S]{0,5}?期末店舗数"
                    ),
                    "column_map": {"9": 1, "10": 2, "11": 3, "12": 4, "1": 5, "2": 6},
                    "group": "{col_idx}",
                    "match_occurrence": 1,
                },
                {
                    "months": [3, 4, 5, 6, 7, 8],
                    "row_label_regex": (
                        r"(\d[\d,]*)(?:\s+(\d[\d,]*))?(?:\s+(\d[\d,]*))?"
                        r"(?:\s+(\d[\d,]*))?(?:\s+(\d[\d,]*))?(?:\s+(\d[\d,]*))?"
                        r"(?:\s+(\d[\d,]*))?\s*[\s\S]{0,5}?期末店舗数"
                    ),
                    "column_map": {"3": 1, "4": 2, "5": 3, "6": 4, "7": 5, "8": 6},
                    "group": "{col_idx}",
                    "match_occurrence": 2,
                },
            ]

    adp["regex_redesign_at"] = datetime.now(JST).isoformat()
    adp["regex_redesign_note"] = (
        "期末店舗数 regex の向き反転: PDF text では数字が label BEFORE にある. "
        "[\\s\\S] マッチで DOTALL 相当、label AFTER では 0 マッチだった."
    )

    with ADAPTER_PATH.open("w", encoding="utf-8") as f:
        json.dump(adp, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ adapter 更新: {ADAPTER_PATH}")

    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")
    bucket.blob("monthly/meta/2735/extract_adapter.json").upload_from_filename(str(ADAPTER_PATH))
    logger.info("✅ GCS sync")

    rec_blob = bucket.blob("monthly/record/2735/monthly_records.json")
    if rec_blob.exists():
        rec_blob.delete()
        logger.info("✅ 既存 records 削除")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    logger.info("--- extract ---")
    r = subprocess.run(
        [sys.executable, "scripts/extract_monthly_data.py",
         "--tickers", "2735", "--since", "2024"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=900,
    )
    sys.stdout.write(r.stdout[-2500:])

    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        logger.info(f"\n--- records ({len(records)} 件) ---")
        for rec in sorted(records, key=lambda r: str(r.get('year_month',''))):
            logger.info(f"  {rec.get('year_month')}: {rec.get('fields', {})}")

    logger.info("\n--- compare ---")
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", "2735"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=300,
    )
    sys.stdout.write(r.stdout[-2500:])
    return 0


if __name__ == "__main__":
    sys.exit(main())

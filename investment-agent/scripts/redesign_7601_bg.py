#!/usr/bin/env python3
"""7601 (ポプラ) PDF regex 手動再設計 + bc_ignore=true で独自管理化."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
from google.cloud import storage  # noqa: E402


def log(msg: str) -> None:
    print(f"[{datetime.now(JST):%H:%M:%S}] {msg}", flush=True)


# PDF 構造:
#   既 存 店 ※ 2 日商（千円） 前年比 173 101.8%
#   既 存 店 ※ 2 客数（人）   前年比 309 99.2%
#   既 存 店 ※ 2 客単価（円） 前年比 560 102.6%
#   全 店 日商（千円）         前年比 138 89.0%
#   全 店 客数（人）           前年比 253 88.0%
#   全 店 客単価（円）         前年比 544 101.2%
#   全 店 売上高前年比         89.1%
ADAPTER_7601 = {
    "source": "non-tdnet(pdf)",
    "format": "pdf",
    "extraction_method": "regex",
    "doc_title_pattern": r"^(\d{6})_(\d{4})_(\d{4}年\d{1,2}月度)_月次業績のご報告_([0-9a-f]{8})$",
    "year_from_title_regex": r"(\d{4})年\d{1,2}月度",
    "month_from_title_regex": r"(\d{1,2})月度",
    "fields": [
        {
            "key": "全店 売上（前年同月比）",
            "bc_key": "全店 売上（前年同月比）",
            "bc_ignore": True,
            "_bc_ignore_reason": "BC 側は 2016 年までのデータしかなく追跡不可。独自管理（2026-04-19）。",
            "value_type": "percentage",
            "row_label_regex": r"全\s*店\s*売上高前年比\s+([\d\.]+)",
        },
        {
            "key": "全店 日商（前年同月比）",
            "bc_key": "全店 日商（前年同月比）",
            "bc_ignore": True,
            "_bc_ignore_reason": "BC 側データ古いため独自管理。",
            "value_type": "percentage",
            "row_label_regex": r"全\s*店\s*日商（千円）\s*前年比\s+[\d,]+\s+([\d\.]+)",
        },
        {
            "key": "全店 客数（前年同月比）",
            "bc_key": "全店 客数（前年同月比）",
            "bc_ignore": True,
            "_bc_ignore_reason": "BC 側データ古いため独自管理。",
            "value_type": "percentage",
            "row_label_regex": r"全\s*店\s*客数（人）\s*前年比\s+[\d,]+\s+([\d\.]+)",
        },
        {
            "key": "全店 客単価（前年同月比）",
            "bc_key": "全店 客単価（前年同月比）",
            "bc_ignore": True,
            "_bc_ignore_reason": "BC 側データ古いため独自管理。",
            "value_type": "percentage",
            "row_label_regex": r"全\s*店\s*客単価（円）\s*前年比\s+[\d,]+\s+([\d\.]+)",
        },
        {
            "key": "既存店 日商（前年同月比）",
            "bc_key": "既存店 日商（前年同月比）",
            "bc_ignore": True,
            "_bc_ignore_reason": "BC 側データ古いため独自管理。",
            "value_type": "percentage",
            "row_label_regex": r"既\s*存\s*店[^\n]{0,10}?日商（千円）\s*前年比\s+[\d,]+\s+([\d\.]+)",
        },
        {
            "key": "既存店 客数（前年同月比）",
            "bc_key": "既存店 客数（前年同月比）",
            "bc_ignore": True,
            "_bc_ignore_reason": "BC 側データ古いため独自管理。",
            "value_type": "percentage",
            "row_label_regex": r"既\s*存\s*店[^\n]{0,10}?客数（人）\s*前年比\s+[\d,]+\s+([\d\.]+)",
        },
        {
            "key": "既存店 客単価（前年同月比）",
            "bc_key": "既存店 客単価（前年同月比）",
            "bc_ignore": True,
            "_bc_ignore_reason": "BC 側データ古いため独自管理。",
            "value_type": "percentage",
            "row_label_regex": r"既\s*存\s*店[^\n]{0,10}?客単価（円）\s*前年比\s+[\d,]+\s+([\d\.]+)",
        },
    ],
    "ticker": "7601",
    "company_name": "7601",
    "manual_override": True,
    "regex_redesign_at": datetime.now(JST).isoformat(),
    "regex_redesign_note": "gemini の adapter が全 field 同じ値にフォールバック → PDF 行ごとの regex 手動再設計。BC 側 2016 年まで古いため bc_ignore=true で独自管理。",
}


def main() -> None:
    gcs = storage.Client(project="stock-data-1930932")

    # Step 1: adapter 保存 + GCS 同期
    path = Path("data/monthly_adapters/7601.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ADAPTER_7601, f, ensure_ascii=False, indent=2)
    log(f"[7601] local saved: {path}")
    gcs.bucket("stock_data_1930932").blob("monthly/meta/7601/extract_adapter.json").upload_from_filename(
        str(path), content_type="application/json",
    )
    log("[7601] GCS synced")

    # Step 2: 再 extract
    log("=== 再 extract ===")
    r = subprocess.run(
        ["C:/venvs/investment-agent/Scripts/python.exe", "scripts/extract_monthly_data.py",
         "--tickers", "7601", "--since", "2024"],
        capture_output=True, text=True, encoding="utf-8",
    )
    for line in r.stdout.strip().splitlines()[-10:]:
        log(line)

    # Step 3: records 確認
    log("=== records 最新 ===")
    d = json.loads(gcs.bucket("stock_data_1930932").blob("monthly/record/7601/monthly_records.json").download_as_text())
    for rec in sorted(d.get("records", []), key=lambda r: r["year_month"])[-4:]:
        log(f"  {rec['year_month']}: {rec.get('fields')}")

    # Step 4: compare (bc_ignore=true のはずなので突合は skip されるはず)
    log("=== compare（bc_ignore で skip されるはず） ===")
    r = subprocess.run(
        ["C:/venvs/investment-agent/Scripts/python.exe", "scripts/compare_monthly_buffett.py",
         "--offline", "--tickers", "7601"],
        capture_output=True, text=True, encoding="utf-8",
    )
    for line in r.stdout.strip().splitlines():
        if "7601" in line or "一致率" in line or "pre-check" in line or "bc_ignore" in line:
            log(line)


if __name__ == "__main__":
    main()

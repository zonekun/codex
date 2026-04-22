#!/usr/bin/env python3
"""3174 adapter を PDF regex で手動再設計 + GCS 同期 + extract + compare 検証.

- 2026-01/02/03 PDF を全 dump して構造把握
- sections + match_occurrence で adapter 作成
- 適用後 compare で BC と照合
"""
from __future__ import annotations

import csv as _csv
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


# ========================================================================
# 3174 手動 adapter (PDF は 6 列表が複数年度並ぶ構造)
# ========================================================================
ADAPTER_3174 = {
    "source": "tdnet",
    "format": "text",
    "extraction_method": "regex",
    "fiscal_year_start_month": 9,
    "doc_title_pattern": r"月次売上高前年比等.{0,8}速報.{0,20}お知らせ",
    "year_from_title_regex": r"(\d{4})年",
    "month_from_title_regex": r"(\d{1,2})月",
    "fields": [
        {
            "key": "既存店 売上（前年同月比）",
            "bc_key": "既存店 売上（前年同月比）",
            "value_type": "percentage",
            "sections": [
                {
                    "months": [9, 10, 11, 12, 1, 2],
                    "row_label_regex": r"売\s*上\s*高\s*（既存店前年比）\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?",
                    "column_map": {"9": 1, "10": 2, "11": 3, "12": 4, "1": 5, "2": 6},
                    "group": "{col_idx}",
                    "match_occurrence": 1,
                },
                {
                    "months": [3, 4, 5, 6, 7, 8],
                    "row_label_regex": r"３月\s+４月\s+５月\s+６月\s+７月\s+８月[\s\S]{0,50}?売\s*上\s*高\s*（既存店前年比）\s+([\d\.]+)\s*%?(?:\s+([\d\.]+)\s*%?)?(?:\s+([\d\.]+)\s*%?)?(?:\s+([\d\.]+)\s*%?)?(?:\s+([\d\.]+)\s*%?)?(?:\s+([\d\.]+)\s*%?)?",
                    "column_map": {"3": 1, "4": 2, "5": 3, "6": 4, "7": 5, "8": 6},
                    "group": "{col_idx}",
                    "match_occurrence": 1,
                },
            ],
        },
        {
            "key": "既存店 客単価（前年同月比）",
            "bc_key": "既存店 客単価（前年同月比）",
            "value_type": "percentage",
            "sections": [
                {
                    "months": [9, 10, 11, 12, 1, 2],
                    "row_label_regex": r"客\s*単\s*価\s*（\s*〃\s*）\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?",
                    "column_map": {"9": 1, "10": 2, "11": 3, "12": 4, "1": 5, "2": 6},
                    "group": "{col_idx}",
                    "match_occurrence": 1,
                },
                {
                    "months": [3, 4, 5, 6, 7, 8],
                    "row_label_regex": r"３月\s+４月\s+５月\s+６月\s+７月\s+８月[\s\S]{0,150}?客\s*単\s*価\s*（\s*〃\s*）\s+([\d\.]+)\s*%?(?:\s+([\d\.]+)\s*%?)?(?:\s+([\d\.]+)\s*%?)?(?:\s+([\d\.]+)\s*%?)?(?:\s+([\d\.]+)\s*%?)?(?:\s+([\d\.]+)\s*%?)?",
                    "column_map": {"3": 1, "4": 2, "5": 3, "6": 4, "7": 5, "8": 6},
                    "group": "{col_idx}",
                    "match_occurrence": 1,
                },
            ],
        },
        {
            "key": "既存店 総利益（前年同月比）",
            "bc_key": "既存店 総利益（前年同月比）",
            "value_type": "percentage",
            "sections": [
                {
                    "months": [9, 10, 11, 12, 1, 2],
                    "row_label_regex": r"売上総利益\s*（\s*〃\s*）\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?",
                    "column_map": {"9": 1, "10": 2, "11": 3, "12": 4, "1": 5, "2": 6},
                    "group": "{col_idx}",
                    "match_occurrence": 1,
                },
                {
                    "months": [3, 4, 5, 6, 7, 8],
                    "row_label_regex": r"３月\s+４月\s+５月\s+６月\s+７月\s+８月[\s\S]{0,100}?売上総利益\s*（\s*〃\s*）\s+([\d\.]+)\s*%?(?:\s+([\d\.]+)\s*%?)?(?:\s+([\d\.]+)\s*%?)?(?:\s+([\d\.]+)\s*%?)?(?:\s+([\d\.]+)\s*%?)?(?:\s+([\d\.]+)\s*%?)?",
                    "column_map": {"3": 1, "4": 2, "5": 3, "6": 4, "7": 5, "8": 6},
                    "group": "{col_idx}",
                    "match_occurrence": 1,
                },
            ],
        },
        {
            "key": "既存店 売上個数（前年同月比）",
            "bc_key": "既存店 売上個数（前年同月比）",
            "value_type": "percentage",
            "sections": [
                {
                    "months": [9, 10, 11, 12, 1, 2],
                    "row_label_regex": r"売上個数\s*（\s*〃\s*）\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?\s+([\d\.]+)\s*%?",
                    "column_map": {"9": 1, "10": 2, "11": 3, "12": 4, "1": 5, "2": 6},
                    "group": "{col_idx}",
                    "match_occurrence": 1,
                },
                {
                    "months": [3, 4, 5, 6, 7, 8],
                    "row_label_regex": r"３月\s+４月\s+５月\s+６月\s+７月\s+８月[\s\S]{0,120}?売上個数\s*（\s*〃\s*）\s+([\d\.]+)\s*%?(?:\s+([\d\.]+)\s*%?)?(?:\s+([\d\.]+)\s*%?)?(?:\s+([\d\.]+)\s*%?)?(?:\s+([\d\.]+)\s*%?)?(?:\s+([\d\.]+)\s*%?)?",
                    "column_map": {"3": 1, "4": 2, "5": 3, "6": 4, "7": 5, "8": 6},
                    "group": "{col_idx}",
                    "match_occurrence": 1,
                },
            ],
        },
    ],
    "ticker": "3174",
    "company_name": "3174",
    "manual_override": True,
    "regex_redesign_at": datetime.now(JST).isoformat(),
    "regex_redesign_note": "gemini → PDF regex へ手動再設計。fy_start=9、9-2月は upper half 第1回目マッチ、3-8月は '３月 ４月 ５月 ６月 ７月 ８月' header anchor 後方の lower half 第1回目マッチ。unit_scale=100 削除（差分形式バグ対策の残骸）。",
}


def main() -> None:
    gcs = storage.Client(project="stock-data-1930932")
    b = gcs.bucket("stock_data_1930932")

    # PDF 全 dump
    targets = [
        (2026, 1, "20260206"),
        (2026, 2, "20260306"),
        (2026, 3, "20260407"),
    ]
    for year, month, date_prefix in targets:
        log(f"=== {year}-{month:02d} PDF ({date_prefix}) ===")
        blobs = [x for x in b.list_blobs(prefix="tdnet/3174/")
                 if x.name.endswith(".pdf") and date_prefix in x.name and "月次売上高前年比等" in x.name]
        if not blobs:
            log("  (not found)")
            continue
        blob = blobs[0]
        text = _extract_pdf_text(blob.download_as_bytes())
        # 3月度 PDF は特に詳しく
        dump_lines = text.splitlines()[:60]
        for line in dump_lines:
            log(f"    {line}")
        log("")

    # adapter 適用
    log("=== adapter 適用 ===")
    path = Path("data/monthly_adapters/3174.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ADAPTER_3174, f, ensure_ascii=False, indent=2)
    log(f"  local saved: {path}")
    b.blob("monthly/meta/3174/extract_adapter.json").upload_from_filename(str(path), content_type="application/json")
    log("  GCS synced")

    # 再 extract
    log("=== 再 extract ===")
    r = subprocess.run(
        ["C:/venvs/investment-agent/Scripts/python.exe", "scripts/extract_monthly_data.py",
         "--tickers", "3174", "--since", "2024"],
        capture_output=True, text=True, encoding="utf-8",
    )
    for line in r.stdout.strip().splitlines()[-10:]:
        log(line)

    # records 確認
    log("=== records 確認 ===")
    d = json.loads(b.blob("monthly/record/3174/monthly_records.json").download_as_text())
    for rec in sorted(d.get("records", []), key=lambda r: r["year_month"])[-4:]:
        log(f"  {rec['year_month']}: {rec.get('fields')}")

    # compare
    log("=== compare ===")
    r = subprocess.run(
        ["C:/venvs/investment-agent/Scripts/python.exe", "scripts/compare_monthly_buffett.py",
         "--offline", "--tickers", "3174"],
        capture_output=True, text=True, encoding="utf-8",
    )
    for line in r.stdout.strip().splitlines():
        if "3174" in line or "2026-" in line or "一致率" in line or "不一致" in line:
            log(line)


if __name__ == "__main__":
    main()

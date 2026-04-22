#!/usr/bin/env python3
"""428A / 6627 adapter を手動 regex 再設計 + GCS 同期 + 再extract + compare 検証."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")

from google.cloud import storage  # noqa: E402


def log(msg: str) -> None:
    print(f"[{datetime.now(JST):%H:%M:%S}] {msg}", flush=True)


# ========================================================================
# 428A 手動 adapter
# ========================================================================
# PDF 列: 9月 10月 11月 12月 1月 2月 上期計 3月 (4月 5月 6月 7月 8月 通期)
# 3月度 PDF では 9 列目まで埋まる（上期計スキップで 3月=col 8）
ADAPTER_428A = {
    "source": "tdnet",
    "format": "text",
    "extraction_method": "regex",
    "fiscal_year_start_month": 9,
    "doc_title_pattern": "売上速報についてのお知らせ",
    "year_from_title_regex": r"(\d{4})年",
    "month_from_title_regex": r"(\d{1,2})月度",
    "column_map": {
        "9": 1, "10": 2, "11": 3, "12": 4, "1": 5, "2": 6,
        "3": 8, "4": 9, "5": 10, "6": 11, "7": 12, "8": 13,
    },
    "fields": [
        {
            "key": "全店 売上（前年同月比）",
            "bc_key": "全店 売上（前年同月比）",
            "value_type": "percentage",
            "row_label_regex": r"売上高((?:\s+[\d\.]+){1,14})",
            "group": "{col_idx}",
            "match_occurrence": 1,
            "description": "PDF 1 表目(全店) の '売上高' 行、対象月列。",
        },
        {
            "key": "全店 客数（前年同月比）",
            "bc_key": "全店 客数（前年同月比）",
            "value_type": "percentage",
            "row_label_regex": r"客数((?:\s+[\d\.]+){1,14})",
            "group": "{col_idx}",
            "match_occurrence": 1,
            "description": "PDF 1 表目(全店) の '客数' 行、対象月列。",
        },
        {
            "key": "全店 客単価（前年同月比）",
            "bc_key": "全店 客単価（前年同月比）",
            "value_type": "percentage",
            "row_label_regex": r"客単価((?:\s+[\d\.]+){1,14})",
            "group": "{col_idx}",
            "match_occurrence": 1,
            "description": "PDF 1 表目(全店) の '客単価' 行、対象月列。",
        },
        {
            "key": "全店 店舗数",
            "bc_key": "全店 店舗数",
            "value_type": "integer",
            "row_label_regex": r"店舗数\(店\)((?:\s+(?:\d[\d,]*|-)){1,14})",
            "group": "{col_idx}",
            "match_occurrence": 1,
            "description": "PDF 1 表目(全店) の '店舗数(店)' 行、対象月列。",
        },
        {
            "key": "既存店 売上（前年同月比）",
            "bc_key": "既存店 売上（前年同月比）",
            "value_type": "percentage",
            "row_label_regex": r"売上高((?:\s+[\d\.]+){1,14})",
            "group": "{col_idx}",
            "match_occurrence": 2,
            "description": "PDF 2 表目(既存店) の '売上高' 行、対象月列。",
        },
        {
            "key": "既存店 客数（前年同月比）",
            "bc_key": "既存店 客数（前年同月比）",
            "value_type": "percentage",
            "row_label_regex": r"客数((?:\s+[\d\.]+){1,14})",
            "group": "{col_idx}",
            "match_occurrence": 2,
            "description": "PDF 2 表目(既存店) の '客数' 行、対象月列。",
        },
        {
            "key": "既存店 客単価（前年同月比）",
            "bc_key": "既存店 客単価（前年同月比）",
            "value_type": "percentage",
            "row_label_regex": r"客単価((?:\s+[\d\.]+){1,14})",
            "group": "{col_idx}",
            "match_occurrence": 2,
            "description": "PDF 2 表目(既存店) の '客単価' 行、対象月列。",
        },
    ],
    "ticker": "428A",
    "company_name": "428A",
    "manual_override": True,
    "regex_redesign_at": datetime.now(JST).isoformat(),
    "regex_redesign_note": "gemini → PDF regex へ手動再設計。fiscal_year_start_month=9 + column_map で上期計を物理列7としてスキップ。1表目=全店 (match_occurrence=1)、2表目=既存店 (match_occurrence=2)。",
}

# ========================================================================
# 6627 手動 adapter
# ========================================================================
# PDF 構造:
#   月次 月次 売上高           4,124 4,088 4,509   <- 1-6月
#   月次 対前年同期増減率（％）  34.3  36.7  41.5
# fiscal_year_start_month=1, 列は 1月=1, 2月=2, 3月=3 ...
ADAPTER_6627 = {
    "source": "non-tdnet(pdf)",
    "format": "pdf",
    "extraction_method": "regex",
    "fiscal_year_start_month": 1,
    "doc_title_pattern": "月次連結売上高",
    "year_from_title_regex": r"(\d{4})年",
    "month_from_title_regex": r"(\d{1,2})月",
    "column_map": {
        "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6,
        "7": 7, "8": 8, "9": 9, "10": 10, "11": 11, "12": 12,
    },
    "fields": [
        {
            "key": "連結 売上（百万円）",
            "bc_key": "連結 売上（百万円）",
            "value_type": "float",
            "row_label_regex": r"月次\s+月次\s+売上高((?:\s+[\d,]+){1,12})",
            "group": "{col_idx}",
            "match_occurrence": 1,
            "description": "PDF '月次 月次 売上高' 行、対象月列（百万円）。",
        },
        {
            "key": "連結 売上（前年同月比）",
            "bc_key": "連結 売上（前年同月比）",
            "value_type": "percentage",
            "row_label_regex": r"月次\s+対前年同期増減率.{0,10}?((?:\s+[\d\.]+){1,12})",
            "group": "{col_idx}",
            "match_occurrence": 1,
            "yoy_offset": 100,
            "description": "PDF '月次 対前年同期増減率（％）' 行、対象月列（差分形式、yoy_offset=100 で BC 100+形式に変換）。",
        },
    ],
    "ticker": "6627",
    "company_name": "6627",
    "manual_override": True,
    "regex_redesign_at": datetime.now(JST).isoformat(),
    "regex_redesign_note": "gemini → PDF regex へ手動再設計。金額 '月次 月次 売上高' 行と前年比 '対前年同期増減率（％）' 行を別 field で取得。前年比は差分形式 (34.3 等) なので yoy_offset=100 で BC 比率形式 (134.3 等) に変換。",
}


def apply_adapter(ticker: str, adapter: dict, gcs) -> None:
    path = Path(f"data/monthly_adapters/{ticker}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(adapter, f, ensure_ascii=False, indent=2)
    log(f"[{ticker}] local saved: {path}")
    blob = gcs.bucket("stock_data_1930932").blob(f"monthly/meta/{ticker}/extract_adapter.json")
    blob.upload_from_filename(str(path), content_type="application/json")
    log(f"[{ticker}] GCS synced")


def run_extract_compare(tickers: list[str]) -> None:
    log("=== extract 再実行 ===")
    r = subprocess.run(
        ["C:/venvs/investment-agent/Scripts/python.exe", "scripts/extract_monthly_data.py",
         "--tickers", *tickers, "--since", "2024"],
        capture_output=True, text=True, encoding="utf-8",
    )
    for line in r.stdout.strip().splitlines()[-15:]:
        log(line)

    log("=== compare --offline ===")
    r = subprocess.run(
        ["C:/venvs/investment-agent/Scripts/python.exe", "scripts/compare_monthly_buffett.py",
         "--offline", "--tickers", *tickers],
        capture_output=True, text=True, encoding="utf-8",
    )
    for line in r.stdout.strip().splitlines():
        if any(t in line for t in tickers) and ("OK" in line or "NG" in line or "BC_NODATA" in line):
            log(line)
        elif "一致率" in line or "不一致" in line:
            log(line)


def main() -> None:
    gcs = storage.Client(project="stock-data-1930932")
    apply_adapter("428A", ADAPTER_428A, gcs)
    apply_adapter("6627", ADAPTER_6627, gcs)
    run_extract_compare(["428A", "6627"])


if __name__ == "__main__":
    main()

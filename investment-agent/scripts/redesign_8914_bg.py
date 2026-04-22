#!/usr/bin/env python3
"""8914 adapter regex 手動再設計 (BC 目標一致).

BC 側:
  ハローストレージ稼働率（前年同月比）: 値は ~73-90% (生% 値、名前は misleading)
  ハローストレージ総室数（前年同月比）: 値は ~80000-130000 (生件数)

records 現状: 稼働率 % が総室数と同値になる全 field コピー誤抽出。
"""
from __future__ import annotations

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


def inspect_pdf(b) -> None:
    log("=== 8914 直近月次 PDF 内容 ===")
    blobs = [x for x in b.list_blobs(prefix="tdnet/8914/") if x.name.endswith(".pdf")]
    monthly = [x for x in blobs if "月次" in x.name or "稼働" in x.name or "総室" in x.name]
    log(f"  月次関連: {len(monthly)}")
    for x in sorted(monthly, key=lambda n: n.name)[-3:]:
        log(f"  {x.name.split('/')[-1][:80]}")
    if not monthly:
        # 月次関連なしなら docs から探す
        monthly = [x for x in blobs if "月" in x.name]
        log(f"  「月」含む: {len(monthly)}")
    if monthly:
        latest = sorted(monthly, key=lambda n: n.name)[-1]
        log(f"=== 最新 PDF text ===")
        text = _extract_pdf_text(latest.download_as_bytes())
        for line in text[:3500].splitlines():
            log(f"  {line}")


# Adapter (regex ベース、bc_ignore=false=BC 突合対象)
ADAPTER_8914 = {
    "source": "tdnet",
    "format": "text",
    "extraction_method": "regex",
    "doc_title_pattern": r"(\d{4})年(\d{1,2})月",
    "year_from_title_regex": r"(\d{4})年",
    "month_from_title_regex": r"(\d{1,2})月",
    "fields": [
        {
            "key": "総室数",
            "bc_key": "ハローストレージ総室数（前年同月比）",
            "value_type": "integer",
            # ハローストレージ 総室数 行: "総室数" ラベル + カンマ区切り数字
            # 複数候補あるが最初の match (ハローストレージセクション内) を取る
            "row_label_regex": r"ハローストレージ[\s\S]{0,200}?総室数[\s\S]{0,30}?([\d,]+)",
            "group": 1,
            "match_occurrence": 1,
        },
        {
            "key": "稼働室数",
            "bc_key": None,
            "bc_ignore": True,
            "_bc_ignore_reason": "BC 側に該当 field なし",
            "value_type": "integer",
            "row_label_regex": r"ハローストレージ[\s\S]{0,300}?稼働室数[\s\S]{0,30}?([\d,]+)",
            "group": 1,
            "match_occurrence": 1,
        },
        {
            "key": "稼働率(%)",
            "bc_key": "ハローストレージ稼働率（前年同月比）",
            "value_type": "percentage",
            # ハローストレージ 稼働率 行: "稼働率" + X%.X
            "row_label_regex": r"ハローストレージ[\s\S]{0,400}?稼働率[^\d]{0,10}([\d\.]+)\s*%",
            "group": 1,
            "match_occurrence": 1,
        },
        {
            "key": "既存稼働率(%)",
            "bc_key": None,
            "bc_ignore": True,
            "_bc_ignore_reason": "BC 側に該当 field なし",
            "value_type": "percentage",
            "row_label_regex": r"既存\s*稼働率[^\d]{0,10}([\d\.]+)\s*%",
            "group": 1,
            "match_occurrence": 1,
        },
        {
            "key": "新規稼働率(%)",
            "bc_key": None,
            "bc_ignore": True,
            "_bc_ignore_reason": "BC 側に該当 field なし",
            "value_type": "percentage",
            "row_label_regex": r"新規\s*稼働率[^\d]{0,10}([\d\.]+)\s*%",
            "group": 1,
            "match_occurrence": 1,
        },
    ],
    "ticker": "8914",
    "company_name": "8914",
    "manual_override": True,
    "regex_redesign_at": datetime.now(JST).isoformat(),
    "regex_redesign_note": "全 field が総室数と同値コピーバグ解消。BC「ハローストレージ総室数（前年同月比）」「ハローストレージ稼働率（前年同月比）」（名称に反して生値）を目標に regex 再設計。",
}


def main() -> None:
    gcs = storage.Client(project="stock-data-1930932")
    b = gcs.bucket("stock_data_1930932")

    # Step 1: PDF 調査
    inspect_pdf(b)

    # Step 2: records 削除 (古い誤値をクリア)
    try:
        b.blob("monthly/record/8914/monthly_records.json").delete()
        log("[8914] records deleted (GCS)")
    except Exception as e:
        log(f"[8914] records delete: {e}")

    # Step 3: adapter 保存 + GCS 同期
    path = Path("data/monthly_adapters/8914.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ADAPTER_8914, f, ensure_ascii=False, indent=2)
    b.blob("monthly/meta/8914/extract_adapter.json").upload_from_filename(
        str(path), content_type="application/json",
    )
    log("[8914] adapter 保存 + GCS 同期")

    # Step 4: 再 extract
    log("=== 再 extract ===")
    r = subprocess.run(
        ["C:/venvs/investment-agent/Scripts/python.exe", "scripts/extract_monthly_data.py",
         "--tickers", "8914", "--since", "2024"],
        capture_output=True, text=True, encoding="utf-8",
    )
    for line in r.stdout.strip().splitlines()[-10:]:
        log(line)

    # Step 5: records 確認
    log("=== records 最新 ===")
    try:
        d = json.loads(b.blob("monthly/record/8914/monthly_records.json").download_as_text())
        for rec in sorted(d.get("records", []), key=lambda r: r["year_month"])[-6:]:
            log(f"  {rec['year_month']}: {rec.get('fields')}")
    except Exception as e:
        log(f"  records 読込失敗: {e}")

    # Step 6: compare
    log("=== compare ===")
    r = subprocess.run(
        ["C:/venvs/investment-agent/Scripts/python.exe", "scripts/compare_monthly_buffett.py",
         "--offline", "--tickers", "8914"],
        capture_output=True, text=True, encoding="utf-8",
    )
    for line in r.stdout.strip().splitlines():
        if "8914" in line or "一致率" in line or "pre-check" in line or "bc_ignore" in line:
            log(line)


if __name__ == "__main__":
    main()

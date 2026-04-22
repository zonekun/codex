#!/usr/bin/env python3
"""7564 (ワークマン) を非TDnet経由に切替 + download + extract 再設計.

手順:
  1. IR ページ HTML 調査（curl_cffi）
  2. monthly_adapter_index.csv に 7564 エントリ追加/更新
  3. download_monthly.py で PDF 取得
  4. extract adapter 再設計（regex + sections/column_map）
  5. extract_monthly_data.py で再抽出
  6. compare で検証
"""
from __future__ import annotations

import csv as _csv
import io
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
from google.cloud import storage  # noqa: E402


def log(msg: str) -> None:
    print(f"[{datetime.now(JST):%H:%M:%S}] {msg}", flush=True)


IR_URL = "https://www.workman.co.jp/ir情報/月次報告"


def phase1_inspect_ir_page() -> list[str]:
    """IR ページを取得して PDF リンク候補を列挙."""
    log("=== Phase 1: IR ページ調査 ===")
    pdf_links: list[str] = []
    try:
        from curl_cffi import requests
        r = requests.get(IR_URL, impersonate="chrome110", timeout=30)
        html = r.text
        log(f"  HTTP {r.status_code}, html size: {len(html)}")
        # PDF リンク抽出
        for m in re.finditer(r'href=["\']([^"\']+\.pdf)["\']', html, re.IGNORECASE):
            pdf_links.append(m.group(1))
        log(f"  PDF リンク数: {len(pdf_links)}")
        for lk in pdf_links[:10]:
            log(f"    {lk}")
        # ページ内の目立つテキスト（リンク文字列も拾う）
        sample_anchors = re.findall(r'<a[^>]*href=["\']([^"\']+\.pdf)["\'][^>]*>([^<]+)</a>', html, re.IGNORECASE)
        log(f"  anchor+label 候補 {len(sample_anchors)}:")
        for href, label in sample_anchors[:8]:
            log(f"    [{label.strip()[:40]}] → {href}")
    except Exception as e:
        log(f"  IR ページ取得失敗: {e}")
    return pdf_links


def phase2_update_adapter_index() -> None:
    log("=== Phase 2: monthly_adapter_index.csv 更新 ===")
    idx_path = Path("data/monthly_adapter_index.csv")
    with open(idx_path, encoding="utf-8-sig") as f:
        rows = list(_csv.DictReader(f))
    fieldnames = list(rows[0].keys()) if rows else []

    # 7564 エントリ探す
    target = None
    for r in rows:
        if (r.get("ticker") or "").strip() == "7564":
            target = r
            break
    if target is None:
        # 新規追加
        new_row = {k: "" for k in fieldnames}
        new_row["ticker"] = "7564"
        new_row["company_name"] = "ワークマン"
        rows.append(new_row)
        target = new_row
        log("  新規追加: 7564")

    target["skip"] = "False"
    target["category"] = "active"
    target["monthly_page_url"] = IR_URL
    target["type"] = "scrape_links"
    target["link_text_pattern"] = "月次前年比速報"
    target["link_href_pattern"] = r".*\.pdf"
    target["format"] = "pdf"
    target["adapter_note"] = "TDnet 月次開示カテゴリが『業績予想』扱いで filter を通らないため IR HP 直接スクレイプに切替（2026-04-19）"
    target["updated_at"] = datetime.now(JST).strftime("%Y-%m-%d")

    out = io.StringIO()
    w = _csv.DictWriter(out, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)
    with open(idx_path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(out.getvalue())
    log("  monthly_adapter_index.csv 更新")


def phase3_run_download() -> None:
    log("=== Phase 3: download_monthly.py で PDF 取得 ===")
    r = subprocess.run(
        ["C:/venvs/investment-agent/Scripts/python.exe", "scripts/download_monthly.py",
         "--tickers", "7564"],
        capture_output=True, text=True, encoding="utf-8",
    )
    for line in r.stdout.strip().splitlines()[-20:]:
        log(line)
    if r.returncode != 0:
        log(f"  ⚠️ returncode={r.returncode}")
        for line in r.stderr.strip().splitlines()[-10:]:
            log(f"  STDERR: {line}")


def phase4_write_extract_adapter() -> None:
    """ワークマンは 3月決算（fy_start=4）。
    PDF 構造:
      上半期: 4月 5月 6月 1Q 7月 8月 9月 2Q 上期 (9列)
      下半期: 10月 11月 12月 3Q 1月 2月 3月 4Q 下期 通期 (10列)
    """
    log("=== Phase 4: extract_adapter.json 再設計 ===")
    adapter = {
        "source": "non-tdnet(pdf)",
        "format": "pdf",
        "extraction_method": "regex",
        "fiscal_year_start_month": 4,
        "doc_title_pattern": r"月次前年比速報",
        "year_from_title_regex": r"(\d{4})年\d{1,2}月期",
        "month_from_title_regex": r"(\d{1,2})月",
        "use_fy_history_correction": True,
        "fields": [
            {
                "key": "全店 売上（前年同月比）",
                "bc_key": "全店 売上（前年同月比）",
                "bc_ignore": True,
                "_bc_ignore_reason": "BC 側データ古いため独自管理。",
                "value_type": "percentage",
                "sections": [
                    {
                        "months": [4, 5, 6, 7, 8, 9],
                        "row_label_regex": r"全\s*店\s*売上高((?:\s+[\d\.]+){1,9})",
                        "column_map": {"4": 1, "5": 2, "6": 3, "7": 5, "8": 6, "9": 7},
                        "group": "{col_idx}",
                        "match_occurrence": 1,
                    },
                    {
                        "months": [10, 11, 12, 1, 2, 3],
                        "row_label_regex": r"全\s*店\s*売上高((?:\s+[\d\.]+){1,10})",
                        "column_map": {"10": 1, "11": 2, "12": 3, "1": 5, "2": 6, "3": 7},
                        "group": "{col_idx}",
                        "match_occurrence": 2,
                    },
                ],
            },
            {
                "key": "全店 客数（前年同月比）",
                "bc_key": "全店 客数（前年同月比）",
                "bc_ignore": True,
                "value_type": "percentage",
                "sections": [
                    {
                        "months": [4, 5, 6, 7, 8, 9],
                        "row_label_regex": r"全\s*店\s*客数((?:\s+[\d\.]+){1,9})",
                        "column_map": {"4": 1, "5": 2, "6": 3, "7": 5, "8": 6, "9": 7},
                        "group": "{col_idx}",
                        "match_occurrence": 1,
                    },
                    {
                        "months": [10, 11, 12, 1, 2, 3],
                        "row_label_regex": r"全\s*店\s*客数((?:\s+[\d\.]+){1,10})",
                        "column_map": {"10": 1, "11": 2, "12": 3, "1": 5, "2": 6, "3": 7},
                        "group": "{col_idx}",
                        "match_occurrence": 2,
                    },
                ],
            },
            {
                "key": "全店 客単価（前年同月比）",
                "bc_key": "全店 客単価（前年同月比）",
                "bc_ignore": True,
                "value_type": "percentage",
                "sections": [
                    {
                        "months": [4, 5, 6, 7, 8, 9],
                        "row_label_regex": r"全\s*店\s*客単価((?:\s+[\d\.]+){1,9})",
                        "column_map": {"4": 1, "5": 2, "6": 3, "7": 5, "8": 6, "9": 7},
                        "group": "{col_idx}",
                        "match_occurrence": 1,
                    },
                    {
                        "months": [10, 11, 12, 1, 2, 3],
                        "row_label_regex": r"全\s*店\s*客単価((?:\s+[\d\.]+){1,10})",
                        "column_map": {"10": 1, "11": 2, "12": 3, "1": 5, "2": 6, "3": 7},
                        "group": "{col_idx}",
                        "match_occurrence": 2,
                    },
                ],
            },
            {
                "key": "既存店 売上（前年同月比）",
                "bc_key": "既存店 売上（前年同月比）",
                "bc_ignore": True,
                "value_type": "percentage",
                "sections": [
                    {
                        "months": [4, 5, 6, 7, 8, 9],
                        "row_label_regex": r"既\s*存\s*店\s*売上高((?:\s+[\d\.]+){1,9})",
                        "column_map": {"4": 1, "5": 2, "6": 3, "7": 5, "8": 6, "9": 7},
                        "group": "{col_idx}",
                        "match_occurrence": 1,
                    },
                    {
                        "months": [10, 11, 12, 1, 2, 3],
                        "row_label_regex": r"既\s*存\s*店\s*売上高((?:\s+[\d\.]+){1,10})",
                        "column_map": {"10": 1, "11": 2, "12": 3, "1": 5, "2": 6, "3": 7},
                        "group": "{col_idx}",
                        "match_occurrence": 2,
                    },
                ],
            },
            {
                "key": "既存店 客数（前年同月比）",
                "bc_key": "既存店 客数（前年同月比）",
                "bc_ignore": True,
                "value_type": "percentage",
                "sections": [
                    {
                        "months": [4, 5, 6, 7, 8, 9],
                        "row_label_regex": r"既\s*存\s*店\s*客数((?:\s+[\d\.]+){1,9})",
                        "column_map": {"4": 1, "5": 2, "6": 3, "7": 5, "8": 6, "9": 7},
                        "group": "{col_idx}",
                        "match_occurrence": 1,
                    },
                    {
                        "months": [10, 11, 12, 1, 2, 3],
                        "row_label_regex": r"既\s*存\s*店\s*客数((?:\s+[\d\.]+){1,10})",
                        "column_map": {"10": 1, "11": 2, "12": 3, "1": 5, "2": 6, "3": 7},
                        "group": "{col_idx}",
                        "match_occurrence": 2,
                    },
                ],
            },
            {
                "key": "既存店 客単価（前年同月比）",
                "bc_key": "既存店 客単価（前年同月比）",
                "bc_ignore": True,
                "value_type": "percentage",
                "sections": [
                    {
                        "months": [4, 5, 6, 7, 8, 9],
                        "row_label_regex": r"既\s*存\s*店\s*客単価((?:\s+[\d\.]+){1,9})",
                        "column_map": {"4": 1, "5": 2, "6": 3, "7": 5, "8": 6, "9": 7},
                        "group": "{col_idx}",
                        "match_occurrence": 1,
                    },
                    {
                        "months": [10, 11, 12, 1, 2, 3],
                        "row_label_regex": r"既\s*存\s*店\s*客単価((?:\s+[\d\.]+){1,10})",
                        "column_map": {"10": 1, "11": 2, "12": 3, "1": 5, "2": 6, "3": 7},
                        "group": "{col_idx}",
                        "match_occurrence": 2,
                    },
                ],
            },
        ],
        "ticker": "7564",
        "company_name": "7564",
        "manual_override": True,
        "regex_redesign_at": datetime.now(JST).isoformat(),
        "regex_redesign_note": (
            "TDnet 業績予想カテゴリで filter を通らないため non-tdnet(pdf) = IR HP 直接取得に切替。"
            "PDF は上半期 4-9月 (9列) + 下半期 10月-3月 (10列) の 2 表構造。sections で分離、"
            "column_map で Q 列 (4,8) をスキップして月列のみ採用。"
            "全 field bc_ignore=true で独自管理（BC 側 2016 年までデータ古いため）。"
        ),
    }

    path = Path("data/monthly_adapters/7564.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(adapter, f, ensure_ascii=False, indent=2)
    log(f"  local saved: {path}")
    gcs = storage.Client(project="stock-data-1930932")
    gcs.bucket("stock_data_1930932").blob("monthly/meta/7564/extract_adapter.json").upload_from_filename(
        str(path), content_type="application/json",
    )
    log("  GCS synced")


def phase5_run_extract() -> None:
    log("=== Phase 5: 再 extract ===")
    r = subprocess.run(
        ["C:/venvs/investment-agent/Scripts/python.exe", "scripts/extract_monthly_data.py",
         "--tickers", "7564", "--since", "2024"],
        capture_output=True, text=True, encoding="utf-8",
    )
    for line in r.stdout.strip().splitlines()[-12:]:
        log(line)


def phase6_verify() -> None:
    log("=== Phase 6: records 確認 + compare ===")
    gcs = storage.Client(project="stock-data-1930932")
    try:
        d = json.loads(gcs.bucket("stock_data_1930932").blob("monthly/record/7564/monthly_records.json").download_as_text())
        for rec in sorted(d.get("records", []), key=lambda r: r["year_month"])[-6:]:
            log(f"  {rec['year_month']}: {rec.get('fields')}")
    except Exception as e:
        log(f"  records 読込失敗: {e}")

    r = subprocess.run(
        ["C:/venvs/investment-agent/Scripts/python.exe", "scripts/compare_monthly_buffett.py",
         "--offline", "--tickers", "7564"],
        capture_output=True, text=True, encoding="utf-8",
    )
    for line in r.stdout.strip().splitlines():
        if "7564" in line or "一致率" in line or "pre-check" in line or "BC 突合対象" in line or "bc_ignore" in line:
            log(line)


def main() -> None:
    pdf_links = phase1_inspect_ir_page()
    phase2_update_adapter_index()
    phase3_run_download()
    phase4_write_extract_adapter()
    phase5_run_extract()
    phase6_verify()


if __name__ == "__main__":
    main()

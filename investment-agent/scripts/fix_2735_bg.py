#!/usr/bin/env python3
"""2735 adapter 動作診断 + 必要に応じて修正 + 検証（バックグラウンド実行用）."""
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


def main() -> None:
    c = storage.Client(project="stock-data-1930932")
    b = c.bucket("stock_data_1930932")

    # 1. 3 ヶ月分 PDF の 期末店舗数 出現位置を診断
    targets = [
        (2026, 1, "tdnet/2735/20260206_2735_ワッツ_月次開示_2026年８月期１月度 月次売上高対前年同月比及び店舗数推移に関するお知らせ_140120260206549991.pdf"),
        (2026, 2, "tdnet/2735/20260305_2735_ワッツ_月次開示_2026年８月期２月度 月次売上高対前年同月比及び店舗数推移に関するお知らせ_140120260305576707.pdf"),
        (2026, 3, "tdnet/2735/20260406_2735_ワッツ_月次開示_2026年８月期３月度 月次売上高対前年同月比及び店舗数推移に関するお知らせ_140120260406599004.pdf"),
    ]

    for year, month, key in targets:
        log(f"=== {year}-{month:02d} PDF ===")
        pdf_bytes = b.blob(key).download_as_bytes()
        text = _extract_pdf_text(pdf_bytes)
        # 期末店舗数 ラベル毎の行を抽出
        for i, m in enumerate(re.finditer(r"期末店舗数[^\n]*", text), 1):
            content = m.group()[:100]
            log(f"  match {i}: {content!r}")

    # 2. adapter 確認
    log("=== 現 adapter 読込 ===")
    adapter_path = Path("data/monthly_adapters/2735.json")
    with open(adapter_path, encoding="utf-8") as f:
        adapter = json.load(f)
    log(f"  extraction_method: {adapter.get('extraction_method')}")
    log(f"  fields: {len(adapter.get('fields', []))}")
    for fld in adapter.get("fields", []):
        sec_count = len(fld.get("sections", []))
        log(f"    - {fld['key']}: sections={sec_count}, bc_key={fld.get('bc_key')!r}")

    # 3. 実 regex テスト
    log("=== regex PoC (各月 PDF × 各 field) ===")
    for year, month, key in targets:
        pdf_bytes = b.blob(key).download_as_bytes()
        text = _extract_pdf_text(pdf_bytes)
        log(f"--- {year}-{month:02d} ---")
        for fld in adapter.get("fields", []):
            for sec in fld.get("sections", []):
                if month not in sec.get("months", []) and str(month) not in [str(m) for m in sec["months"]]:
                    continue
                row_pat = sec["row_label_regex"]
                col_idx = sec["column_map"].get(str(month), sec["column_map"].get(month))
                match_occ = sec.get("match_occurrence", 1)
                all_m = list(re.finditer(row_pat, text, re.IGNORECASE | re.DOTALL))
                log(f"  [{fld['key'][:30]:30s}] sec months={sec['months']} col_idx={col_idx} match_occ={match_occ} total_matches={len(all_m)}")
                if len(all_m) >= match_occ:
                    m = all_m[match_occ - 1]
                    try:
                        val = m.group(col_idx) if m.lastindex and m.lastindex >= col_idx else None
                    except Exception as e:
                        val = f"ERR {e}"
                    log(f"      → match[{match_occ}].group({col_idx}) = {val!r}")
                    log(f"      match text[:120]: {m.group(0)[:120]!r}")

    # 4. extract 再実行
    log("=== extract 再実行 ===")
    r = subprocess.run(
        ["C:/venvs/investment-agent/Scripts/python.exe", "scripts/extract_monthly_data.py",
         "--tickers", "2735", "--since", "2024"],
        capture_output=True, text=True, encoding="utf-8",
    )
    for line in r.stdout.strip().splitlines()[-10:]:
        log(line)

    # 5. records の 2026-01/02/03 を確認
    log("=== 2026-01/02/03 records 確認 ===")
    d = json.loads(b.blob("monthly/record/2735/monthly_records.json").download_as_text())
    for rec in sorted(d.get("records", []), key=lambda r: r["year_month"]):
        if rec["year_month"] in ("2026-01", "2026-02", "2026-03"):
            log(f"  {rec['year_month']}: {rec.get('fields')}")

    # 6. compare 実行
    log("=== compare 検証 ===")
    r = subprocess.run(
        ["C:/venvs/investment-agent/Scripts/python.exe", "scripts/compare_monthly_buffett.py",
         "--offline", "--tickers", "2735"],
        capture_output=True, text=True, encoding="utf-8",
    )
    for line in r.stdout.strip().splitlines():
        if ("2026-0" in line and "2735" not in line) or "一致率" in line or "不一致" in line:
            log(line)


if __name__ == "__main__":
    main()

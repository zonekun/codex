#!/usr/bin/env python3
"""7564 / 8914 統合 BG: download (7564のみ必要時) + extract + compare + 結果表示."""
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


def check_docs(ticker: str, gcs) -> int:
    b = gcs.bucket("stock_data_1930932")
    blobs = list(b.list_blobs(prefix=f"monthly/docs/{ticker}/"))
    return len(blobs)


def run_cmd(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return r.stdout


def print_records(ticker: str, gcs, tail: int = 6) -> None:
    b = gcs.bucket("stock_data_1930932")
    try:
        d = json.loads(b.blob(f"monthly/record/{ticker}/monthly_records.json").download_as_text())
        log(f"records 最新 {tail}件:")
        for rec in sorted(d.get("records", []), key=lambda r: r["year_month"])[-tail:]:
            log(f"  {rec['year_month']}: {rec.get('fields')}")
    except Exception as e:
        log(f"  records err: {e}")


def handle_7564(gcs) -> None:
    log("========== 7564 (ワークマン) ==========")
    n = check_docs("7564", gcs)
    log(f"  monthly/docs/7564/ ファイル数: {n}")
    if n == 0:
        log("  PDF 未取得 → download_monthly.py 実行")
        out = run_cmd(["C:/venvs/investment-agent/Scripts/python.exe",
                       "scripts/download_monthly.py", "--tickers", "7564"])
        for line in out.strip().splitlines()[-15:]:
            log(line)
        n = check_docs("7564", gcs)
        log(f"  DL 後 monthly/docs/7564/ ファイル数: {n}")

    log("--- extract 再実行 ---")
    out = run_cmd(["C:/venvs/investment-agent/Scripts/python.exe",
                   "scripts/extract_monthly_data.py", "--tickers", "7564", "--since", "2024"])
    for line in out.strip().splitlines()[-10:]:
        log(line)

    print_records("7564", gcs, 6)

    log("--- compare ---")
    out = run_cmd(["C:/venvs/investment-agent/Scripts/python.exe",
                   "scripts/compare_monthly_buffett.py", "--offline", "--tickers", "7564"])
    for line in out.strip().splitlines():
        if "7564" in line or "一致率" in line or "不一致" in line or "bc_ignore" in line:
            log(line)


def handle_8914(gcs) -> None:
    log("========== 8914 (エリアリンク) ==========")
    log("--- extract 再実行 ---")
    out = run_cmd(["C:/venvs/investment-agent/Scripts/python.exe",
                   "scripts/extract_monthly_data.py", "--tickers", "8914", "--since", "2024"])
    for line in out.strip().splitlines()[-10:]:
        log(line)

    print_records("8914", gcs, 6)

    log("--- compare ---")
    out = run_cmd(["C:/venvs/investment-agent/Scripts/python.exe",
                   "scripts/compare_monthly_buffett.py", "--offline", "--tickers", "8914"])
    for line in out.strip().splitlines():
        if "8914" in line or "一致率" in line or "不一致" in line:
            log(line)


def main() -> None:
    gcs = storage.Client(project="stock-data-1930932")
    handle_7564(gcs)
    print()
    handle_8914(gcs)


if __name__ == "__main__":
    main()

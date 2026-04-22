#!/usr/bin/env python3
"""records 削除 → extract → compare を 1 コマンドで実行.

Usage:
  PYTHONUTF8=1 python scripts/agent_bc/reextract_and_compare.py --ticker 8218
  # 出力末尾に JSON 1 行: {"ticker": "8218", "match_ratio": 0.83, "ok": 5, "ng": 1, ...}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent


def parse_compare_output(text: str) -> dict:
    """compare の末尾サマリから一致率等を抽出."""
    res = {"ok": 0, "ng": 0, "bc_nodata": 0, "gcs_missing": 0, "errors": 0,
           "total": 0, "match_ratio": 0.0}
    for line in text.splitlines():
        m = re.search(r"一致.*?:\s*(\d+)", line)
        if m and "diff" not in line.lower():
            res["ok"] = int(m.group(1))
        m = re.search(r"不一致.*?:\s*(\d+)", line)
        if m:
            res["ng"] = int(m.group(1))
        m = re.search(r"BC未取得.*?:\s*(\d+)", line)
        if m:
            res["bc_nodata"] = int(m.group(1))
        m = re.search(r"GCSデータなし.*?:\s*(\d+)", line)
        if m:
            res["gcs_missing"] = int(m.group(1))
        m = re.search(r"エラー.*?:\s*(\d+)", line)
        if m:
            res["errors"] = int(m.group(1))
        m = re.search(r"一致率.*?:\s*([\d.]+)\s*%", line)
        if m:
            res["match_ratio"] = float(m.group(1)) / 100.0
    res["total"] = res["ok"] + res["ng"] + res["bc_nodata"]
    return res


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", required=True)
    p.add_argument("--skip-extract", action="store_true",
                   help="records 削除 + extract をスキップして compare のみ")
    p.add_argument("--since", default="2024")
    p.add_argument("--no-ng-detail", action="store_true",
                   help="NG 詳細ログを末尾 JSON に含めない")
    args = p.parse_args()
    ticker = args.ticker

    from google.cloud import storage
    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"

    if not args.skip_extract:
        rec_blob = bucket.blob(f"monthly/record/{ticker}/monthly_records.json")
        if rec_blob.exists():
            rec_blob.delete()
            print(f"[del] records 削除")

        r = subprocess.run(
            [sys.executable, "scripts/extract_monthly_data.py",
             "--tickers", ticker, "--since", args.since],
            capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
            timeout=1800,
        )
        sys.stdout.write(r.stdout[-1500:])
        if r.returncode != 0:
            print(f"[error] extract 失敗")
            print(json.dumps({"ticker": ticker, "error": "extract_failed",
                              "stderr": r.stderr[-500:]}))
            return 1

    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", ticker],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT), timeout=300,
    )
    sys.stdout.write(r.stdout[-2000:])

    parsed = parse_compare_output(r.stdout)
    parsed["ticker"] = ticker

    # NG 詳細（行単位）を抽出
    if not args.no_ng_detail:
        ng_lines = [line for line in r.stdout.splitlines()
                    if "❌" in line or "⚠️" in line]
        parsed["ng_lines"] = ng_lines[:20]

    print("\n=== AGENT_RESULT_JSON ===")
    print(json.dumps(parsed, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

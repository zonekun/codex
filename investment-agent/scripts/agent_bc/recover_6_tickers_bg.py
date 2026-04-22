#!/usr/bin/env python3
"""2790/2792/8217/9603/9887/9900 の monthly/docs/ ゴミ削除 → 再 download → adapter 生成 → extract + compare.

仮説: download_monthly.py が IR ページから無関係な HTML (マイナビ・IRカレンダー等) を scrape していた.
→ 一旦 GCS クリア、再ダウンロード、月次 PDF が入ったか確認。
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
ROOT = Path(__file__).resolve().parent.parent.parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

TARGETS = ["2790", "2792", "8217", "9603", "9887", "9900"]


def step1_cleanup_gcs() -> dict:
    """monthly/docs/<T>/ のゴミファイルを全削除."""
    from google.cloud import storage
    bucket = storage.Client(project="gmailpj-357912").bucket("stock_data_1930932")
    summary = {}
    for t in TARGETS:
        blobs = list(bucket.list_blobs(prefix=f"monthly/docs/{t}/"))
        for b in blobs:
            b.delete()
        summary[t] = len(blobs)
        logger.info(f"  [{t}] 削除: {len(blobs)} files")
    return summary


def step2_redownload() -> None:
    """download_monthly.py 実行."""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    logger.info(f"--- download_monthly.py --tickers {' '.join(TARGETS)} ---")
    r = subprocess.run(
        [sys.executable, "scripts/download_monthly.py", "--tickers"] + TARGETS,
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=1800,
    )
    sys.stdout.write(r.stdout[-2500:])
    sys.stderr.write(r.stderr[-800:])


def step3_check_files() -> dict:
    """再ダウンロード後の files 数確認."""
    from google.cloud import storage
    bucket = storage.Client(project="gmailpj-357912").bucket("stock_data_1930932")
    summary = {}
    for t in TARGETS:
        blobs = list(bucket.list_blobs(prefix=f"monthly/docs/{t}/"))
        pdfs = [b for b in blobs if b.name.endswith(".pdf")]
        htmls = [b for b in blobs if b.name.endswith(".html")]
        summary[t] = {"pdfs": len(pdfs), "htmls": len(htmls), "total": len(blobs)}
        sample_pdf = Path(pdfs[0].name).name[:80] if pdfs else "(no pdf)"
        logger.info(f"  [{t}] pdfs={len(pdfs)}, htmls={len(htmls)}, sample_pdf={sample_pdf}")
    return summary


def step4_build_adapters() -> None:
    """PDF がある ticker のみ adapter 生成."""
    from google.cloud import storage
    bucket = storage.Client(project="gmailpj-357912").bucket("stock_data_1930932")
    has_pdf = []
    for t in TARGETS:
        blobs = list(bucket.list_blobs(prefix=f"monthly/docs/{t}/"))
        if any(b.name.endswith(".pdf") for b in blobs):
            has_pdf.append(t)
    if not has_pdf:
        logger.info("adapter 生成対象無し")
        return
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    logger.info(f"--- build_monthly_extractor.py --tickers {' '.join(has_pdf)} ---")
    r = subprocess.run(
        [sys.executable, "scripts/build_monthly_extractor.py", "--tickers"] + has_pdf,
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=1800,
    )
    sys.stdout.write(r.stdout[-2000:])


def step5_extract_compare() -> None:
    """各 ticker で extract + compare."""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    for t in TARGETS:
        logger.info(f"\n--- {t} extract + compare ---")
        from google.cloud import storage
        b = storage.Client(project="gmailpj-357912").bucket("stock_data_1930932").blob(
            f"monthly/record/{t}/monthly_records.json")
        if b.exists():
            b.delete()
        r = subprocess.run(
            [sys.executable, "scripts/extract_monthly_data.py",
             "--tickers", t, "--since", "2024"],
            capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
            timeout=900,
        )
        sys.stdout.write(r.stdout[-600:])
        r = subprocess.run(
            [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", t],
            capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
            timeout=300,
        )
        import re
        m = re.search(r"一致率:\s+([\d.]+)%", r.stdout)
        m_ok = re.search(r"一致 .*?:\s+(\d+)", r.stdout)
        m_ng = re.search(r"不一致.*?:\s+(\d+)", r.stdout)
        ok = int(m_ok.group(1)) if m_ok else 0
        ng = int(m_ng.group(1)) if m_ng else 0
        ratio = float(m.group(1)) / 100 if m else 0.0
        logger.info(f"  → {t}: ok={ok}, ng={ng}, ratio={ratio:.1%}")


def main() -> int:
    logger.info("=== Step 1: GCS ゴミ削除 ===")
    step1_cleanup_gcs()
    logger.info("\n=== Step 2: 再ダウンロード ===")
    step2_redownload()
    logger.info("\n=== Step 3: ファイル数確認 ===")
    summary = step3_check_files()
    logger.info("\n=== Step 4: adapter 生成 ===")
    step4_build_adapters()
    logger.info("\n=== Step 5: extract + compare ===")
    step5_extract_compare()
    return 0


if __name__ == "__main__":
    sys.exit(main())

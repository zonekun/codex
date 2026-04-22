#!/usr/bin/env python3
"""1873/3034/7345/8165 を TDnet 経由に切替.

1. monthly_adapter_index.csv で skip=True, category=tdnet_migrated に変更（non-tdnet パイプラインから除外）
2. build_monthly_extractor.py で TDnet source の adapter 生成
3. extract_monthly_data.py で再抽出
4. compare_monthly_buffett.py で検証
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

TARGETS = ["1873", "3034", "7345", "8165"]


def step1_update_index() -> None:
    import pandas as pd
    p = ROOT / "data/monthly_adapter_index.csv"
    df = pd.read_csv(p, encoding="utf-8-sig", dtype=str).fillna("")
    ts = datetime.now(JST).strftime("%Y-%m-%d")
    for t in TARGETS:
        mask = df["ticker"] == t
        if mask.any():
            df.loc[mask, "skip"] = "True"
            df.loc[mask, "category"] = "tdnet_migrated"
            df.loc[mask, "adapter_note"] = f"TDnet 経由に移行 ({ts}): monthly/docs のスクレイプ結果は TDnet 書式だったため、non-tdnet 登録を解除"
            df.loc[mask, "updated_at"] = ts
    df.to_csv(p, index=False, encoding="utf-8-sig")
    logger.info(f"✅ monthly_adapter_index.csv 更新: {TARGETS}")


def step2_build_adapter() -> None:
    """build_monthly_extractor.py で adapter 生成 (Gemini-based)."""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    logger.info(f"--- build_monthly_extractor.py --tickers {' '.join(TARGETS)} ---")
    r = subprocess.run(
        [sys.executable, "scripts/build_monthly_extractor.py", "--tickers"] + TARGETS,
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=1800,
    )
    sys.stdout.write(r.stdout[-2000:])
    sys.stderr.write(r.stderr[-500:])


def step3_extract_compare() -> None:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    for t in TARGETS:
        logger.info(f"\n--- {t} extract + compare ---")
        # records 削除
        from google.cloud import storage
        b = storage.Client(project="gmailpj-357912").bucket("stock_data_1930932").blob(
            f"monthly/record/{t}/monthly_records.json")
        if b.exists():
            b.delete()
        r = subprocess.run(
            [sys.executable, "scripts/extract_monthly_data.py",
             "--tickers", t, "--since", "2024"],
            capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
            timeout=1200,
        )
        sys.stdout.write(r.stdout[-1000:])
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
        logger.info(f"  {t}: ok={ok}, ng={ng}, ratio={ratio:.1%}")


def main() -> int:
    step1_update_index()
    step2_build_adapter()
    step3_extract_compare()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""4 tickers (2674, 2726, 2736, 9997) 追加取得。
上書きを防ぐため:
  1. 事前にバックアップ作成
  2. --resume オプションで append モード使用
  3. 実行後 CSV の他銘柄数を検証
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data/csv/bc_monthly_kpi.csv"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

TARGETS = ["2674", "2726", "2736", "9997"]


def main() -> int:
    import pandas as pd

    # 1) 事前バックアップ
    backup_path = ROOT / f"data/csv/bc_monthly_kpi.csv.bak_{datetime.now(JST):%Y%m%d_%H%M%S}"
    shutil.copy2(CSV_PATH, backup_path)
    logger.info(f"✅ バックアップ: {backup_path}")

    # 2) 取得前の状態を記録
    df_before = pd.read_csv(CSV_PATH, encoding="utf-8-sig", dtype=str)
    before_tickers = df_before["ticker"].nunique()
    before_rows = len(df_before)
    logger.info(f"取得前: {before_rows} 行, {before_tickers} 銘柄")

    for t in TARGETS:
        has = len(df_before[df_before["ticker"] == t])
        logger.info(f"  {t}: before rows={has}")

    # 3) --resume で download_bc_kpi.py 実行
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    logger.info("--- download_bc_kpi.py --tickers 2674 2726 2736 9997 --resume ---")
    r = subprocess.run(
        [sys.executable, "scripts/download_bc_kpi.py",
         "--tickers"] + TARGETS + ["--resume"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=600,
    )
    sys.stdout.write(r.stdout[-2500:])
    sys.stderr.write(r.stderr[-800:])

    # 4) 取得後の状態を検証
    df_after = pd.read_csv(CSV_PATH, encoding="utf-8-sig", dtype=str)
    after_tickers = df_after["ticker"].nunique()
    after_rows = len(df_after)
    logger.info(f"\n取得後: {after_rows} 行, {after_tickers} 銘柄")

    # 他銘柄の数が維持されているか検証 (上書き検知)
    if after_tickers < before_tickers:
        logger.error(f"❌ 銘柄数が減少! 上書きの可能性 ({before_tickers} → {after_tickers})")
        logger.error(f"バックアップから復元してください: {backup_path}")
        return 1

    for t in TARGETS:
        sub = df_after[df_after["ticker"] == t]
        if len(sub) > 0:
            logger.info(f"  ✅ {t}: {len(sub)} rows, "
                        f"ym {sub['year_month'].min()} 〜 {sub['year_month'].max()}, "
                        f"{sub['field'].nunique()} fields")
        else:
            logger.warning(f"  ⚠️  {t}: 取得 0 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())

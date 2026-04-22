#!/usr/bin/env python3
"""BC CSV 復旧: GCS の最新 (2026-04-17) 版を pull し、7532 再取得分を merge."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")

ROOT = Path(__file__).resolve().parent.parent
LOCAL_CSV = ROOT / "data/csv/bc_monthly_kpi.csv"
BACKUP_CSV = ROOT / f"data/csv/bc_monthly_kpi_damaged_{datetime.now(JST):%Y%m%d_%H%M%S}.csv"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


def main() -> int:
    import pandas as pd
    from google.cloud import storage
    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")

    # 0) 破損版をバックアップ
    if LOCAL_CSV.exists():
        LOCAL_CSV.replace(BACKUP_CSV)
        logger.info(f"✅ 破損版をバックアップ: {BACKUP_CSV}")

    # 1) GCS から 2026-04-17 版を pull
    blob = bucket.blob("csv/bc_monthly_kpi.csv")
    blob.download_to_filename(str(LOCAL_CSV))
    logger.info(f"✅ GCS から pull: {LOCAL_CSV}")

    # 2) pull 版の検証
    df = pd.read_csv(LOCAL_CSV, encoding="utf-8-sig", dtype=str)
    logger.info(f"  total rows: {len(df)}, tickers: {df['ticker'].nunique()}")

    # 3) 7532 の新データを merge (バックアップから 7532 のみ抽出)
    bak_df = pd.read_csv(BACKUP_CSV, encoding="utf-8-sig", dtype=str)
    new_7532 = bak_df[bak_df["ticker"] == "7532"]
    logger.info(f"  破損版から 7532 抽出: {len(new_7532)} 行")

    # 既存 7532 行を削除
    df_minus_7532 = df[df["ticker"] != "7532"]
    logger.info(f"  GCS 版 7532 除外後: {len(df_minus_7532)} 行")

    # 新 7532 で上書きマージ
    merged = pd.concat([df_minus_7532, new_7532], ignore_index=True)
    merged.to_csv(LOCAL_CSV, encoding="utf-8-sig", index=False)
    logger.info(f"  ✅ マージ完了: {len(merged)} 行, {merged['ticker'].nunique()} 銘柄")

    # 4) 7532 確認
    t = merged[merged["ticker"] == "7532"]
    logger.info(f"\n--- 7532 確認 ---")
    logger.info(f"  rows: {len(t)}, ym range: {t['year_month'].min()} 〜 {t['year_month'].max()}")

    # 5) GCS にも更新版を push
    blob.upload_from_filename(str(LOCAL_CSV), content_type="text/csv")
    logger.info("✅ GCS 更新版 push 完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())

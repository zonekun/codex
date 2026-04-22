#!/usr/bin/env python3
"""7564 ワークマン - 非TDnet download 完遂.

状況:
  - 7564 の月次速報は TDnet 提出されるが MAIN_CATEGORY='業績予想' に分類され
    月次フィルタを通らない (tdnet/7564/ に 20件 PDF 存在).
  - non-tdnet(pdf) source に切替済みだが ir_page_url 未設定で DL 0件.
  - Workman IR ページ調査は不要: 既にGCS tdnet/7564/ に原本がある.

対応:
  tdnet/7564/ から title に「月次前年比速報」を含む PDF を monthly/docs/7564/ に
  コピーし、non-tdnet(pdf) extract pipeline で処理させる.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")

ROOT = Path(__file__).resolve().parent.parent
ADAPTER_PATH = ROOT / "data/monthly_adapters/7564.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> int:
    from google.cloud import storage

    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")

    # tdnet/7564/ から 月次前年比速報 のみ抽出してコピー
    logger.info("--- tdnet/7564/ スキャン ---")
    src_blobs = list(bucket.list_blobs(prefix="tdnet/7564/"))
    logger.info(f"  全 {len(src_blobs)} 件")

    target_pattern = re.compile(r"月次前年比速報")
    targets = [b for b in src_blobs if target_pattern.search(b.name)]
    logger.info(f"  月次前年比速報マッチ: {len(targets)} 件")

    if not targets:
        logger.error("月次前年比速報 PDF が見つからない")
        return 1

    # monthly/docs/7564/ にコピー
    logger.info("--- monthly/docs/7564/ へコピー ---")
    copied = 0
    for src in targets:
        # tdnet/7564/20160104_7564_J-ワークマン_... → monthly/docs/7564/20160104_...
        fname = src.name.rsplit("/", 1)[-1]
        dst_name = f"monthly/docs/7564/{fname}"
        dst_blob = bucket.blob(dst_name)
        if dst_blob.exists():
            continue
        bucket.copy_blob(src, bucket, dst_name)
        copied += 1
    logger.info(f"  コピー: {copied} 件（スキップ: {len(targets) - copied}）")

    # monthly/docs/7564/ 確認
    dl_blobs = list(bucket.list_blobs(prefix="monthly/docs/7564/"))
    logger.info(f"  monthly/docs/7564/ 現在: {len(dl_blobs)} 件")
    if dl_blobs:
        logger.info(f"    最新: {dl_blobs[-1].name.rsplit('/', 1)[-1]}")

    # records 削除
    rec_blob = bucket.blob("monthly/record/7564/monthly_records.json")
    if rec_blob.exists():
        rec_blob.delete()
        logger.info("✅ 既存 records 削除")

    # adapter GCS sync（念のため最新を反映）
    if ADAPTER_PATH.exists():
        bucket.blob("monthly/meta/7564/extract_adapter.json").upload_from_filename(str(ADAPTER_PATH))
        logger.info("✅ adapter GCS sync")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    logger.info("--- extract 実行 ---")
    r = subprocess.run(
        [sys.executable, "scripts/extract_monthly_data.py",
         "--tickers", "7564", "--since", "2024"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=900,
    )
    sys.stdout.write(r.stdout[-3000:])
    sys.stderr.write(r.stderr[-1500:])

    # records 件数確認
    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        logger.info(f"records: {len(records)} 件")
        for rec in records[-6:]:
            logger.info(f"  {rec.get('year_month')}: {list(rec.get('fields', {}).keys())}")

    logger.info("--- compare 実行 ---")
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", "7564"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=300,
    )
    sys.stdout.write(r.stdout[-3000:])
    return 0


if __name__ == "__main__":
    sys.exit(main())

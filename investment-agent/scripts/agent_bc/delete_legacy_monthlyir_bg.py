#!/usr/bin/env python3
"""GCS 旧パス monthlyir/ を全削除（moved to monthly/docs/ 以降 legacy）.

前提確認済 (2026-04-20):
 - 最新更新 2026-04-05（2週間以上書き込みなし）
 - コード 4ファイル (build_monthly_extractor.py / download_monthly.py /
   extract_monthly_data.py / migrate_gcs_monthly_paths.py) は**ローカル path のみ**参照、
   GCS monthlyir/ からの read は一切なし
 - 19 ticker のみに存在するデータは再DL可能のため切り捨て（ユーザー判断）

11,129 blob を段階的に削除してログ出力。
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


def main() -> int:
    from google.cloud import storage
    bucket = storage.Client(project="gmailpj-357912").bucket("stock_data_1930932")

    # 全 blob 列挙
    logger.info("monthlyir/ 下の blob 列挙中...")
    blobs = list(bucket.list_blobs(prefix="monthlyir/"))
    logger.info(f"削除対象 blob 数: {len(blobs)}")
    if not blobs:
        logger.info("対象なし")
        return 0

    # 安全確認: 全てが prefix="monthlyir/" であることを確認
    assert all(b.name.startswith("monthlyir/") for b in blobs), "prefix mismatch detected"
    logger.info("prefix 検証 OK (全 blob が monthlyir/ 下)")

    # バッチ削除
    deleted = 0
    errors = 0
    start = time.time()
    for i, blob in enumerate(blobs, 1):
        try:
            blob.delete()
            deleted += 1
        except Exception as e:
            logger.warning(f"  削除失敗 {blob.name}: {e}")
            errors += 1
        if i % 500 == 0:
            elapsed = time.time() - start
            rate = i / elapsed if elapsed > 0 else 0
            logger.info(f"  進捗 {i}/{len(blobs)} (OK={deleted} NG={errors}) {rate:.1f}件/秒")

    elapsed = time.time() - start
    logger.info(f"\n=== 完了 ===")
    logger.info(f"  削除成功: {deleted}")
    logger.info(f"  削除失敗: {errors}")
    logger.info(f"  所要時間: {elapsed:.1f}秒")

    # 残存確認
    remaining = list(bucket.list_blobs(prefix="monthlyir/", max_results=5))
    logger.info(f"  残存 blob: {len(remaining)}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""GCS monthly/meta/{ticker}/adapter.json → url_adapter.json リネーム (#A 完全分離).

安全方針:
 - URL 型コンテンツ (ir_page_url もしくは url+type を持つ) のみ対象
 - extract 型コンテンツ (fields を持つ) が混入した adapter.json は対象外（別途対処）
 - dry-run モード搭載。本番実行前に 10 件サンプル verify 必須
 - コピー後は md5 一致を必ず検証。不一致なら abort
 - legacy とのマージは**一切しない**（adapter.json の内容そのままコピー）
 - 成功した blob のみ `adapter.json` を後続で削除

使用方法:
  dry-run: PYTHONUTF8=1 python scripts/agent_bc/rename_adapter_to_url_adapter_bg.py --dry-run
  本番:   PYTHONUTF8=1 python scripts/agent_bc/rename_adapter_to_url_adapter_bg.py
  削除まで: PYTHONUTF8=1 python scripts/agent_bc/rename_adapter_to_url_adapter_bg.py --delete-old
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


def _is_url_adapter(d: dict) -> bool:
    """URL adapter 判定: ir_page_url あり、または (url + type) あり、fields なし."""
    has_url = "ir_page_url" in d or ("url" in d and "type" in d)
    has_fields = isinstance(d.get("fields"), list)
    return has_url and not has_fields


def _md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="コピー実行せずサマリのみ")
    p.add_argument("--limit", type=int, default=0, help="対象 ticker 数制限（0=全件）")
    p.add_argument("--delete-old", action="store_true",
                   help="url_adapter.json 作成後に元 adapter.json を削除")
    args = p.parse_args()

    from google.cloud import storage
    bucket = storage.Client(project="gmailpj-357912").bucket("stock_data_1930932")

    # adapter.json を全件列挙
    logger.info("adapter.json 列挙中...")
    adp_blobs = []
    for b in bucket.list_blobs(prefix="monthly/meta/"):
        parts = b.name.split("/")
        if len(parts) >= 4 and parts[3] == "adapter.json":
            adp_blobs.append(b)
    logger.info(f"adapter.json 総数: {len(adp_blobs)}")

    # 分類: URL型 / extract型 / その他
    url_targets = []
    stats = {"total": len(adp_blobs), "url": 0, "extract": 0, "other": 0,
             "copied": 0, "verified": 0, "skipped_exists": 0, "deleted_old": 0,
             "md5_mismatch": 0, "error": 0}
    for b in adp_blobs:
        try:
            d = json.loads(b.download_as_text())
        except Exception:
            stats["other"] += 1
            continue
        if _is_url_adapter(d):
            stats["url"] += 1
            url_targets.append(b)
        elif isinstance(d.get("fields"), list):
            stats["extract"] += 1
        else:
            stats["other"] += 1

    logger.info(f"  URL型         : {stats['url']}")
    logger.info(f"  extract型     : {stats['extract']} (対象外)")
    logger.info(f"  その他        : {stats['other']} (対象外)")

    if args.limit > 0:
        url_targets = url_targets[: args.limit]
        logger.info(f"  --limit 適用後: {len(url_targets)}")

    if args.dry_run:
        logger.info("\n=== DRY-RUN: コピー実行なし ===")
        for b in url_targets[:10]:
            ticker = b.name.split("/")[2]
            dst_exists = bucket.blob(f"monthly/meta/{ticker}/url_adapter.json").exists()
            logger.info(f"  [{ticker}] adapter.json -> url_adapter.json (dst exists: {dst_exists})")
        return 0

    # 本番実行
    for i, src_blob in enumerate(url_targets, 1):
        ticker = src_blob.name.split("/")[2]
        dst_blob = bucket.blob(f"monthly/meta/{ticker}/url_adapter.json")

        try:
            # 既に url_adapter.json が存在したらスキップ（idempotent）
            if dst_blob.exists():
                stats["skipped_exists"] += 1
                # md5 一致していれば元 adapter.json 削除候補
                src_md5 = _md5_bytes(src_blob.download_as_bytes())
                dst_md5 = _md5_bytes(dst_blob.download_as_bytes())
                if src_md5 == dst_md5:
                    stats["verified"] += 1
                    if args.delete_old:
                        src_blob.delete()
                        stats["deleted_old"] += 1
                continue

            # src を読み、dst に書く
            src_data = src_blob.download_as_bytes()
            dst_blob.upload_from_string(src_data, content_type="application/json")
            stats["copied"] += 1

            # md5 verify
            dst_reloaded = bucket.blob(f"monthly/meta/{ticker}/url_adapter.json")
            dst_reloaded.reload()
            dst_data = dst_reloaded.download_as_bytes()
            if _md5_bytes(src_data) != _md5_bytes(dst_data):
                stats["md5_mismatch"] += 1
                logger.error(f"  [{ticker}] md5 不一致! コピー失敗")
                continue
            stats["verified"] += 1

            if args.delete_old:
                src_blob.delete()
                stats["deleted_old"] += 1
        except Exception as e:
            stats["error"] += 1
            logger.warning(f"  [{ticker}] 例外: {e}")

        if i % 50 == 0:
            logger.info(f"  進捗 {i}/{len(url_targets)}")

    logger.info("\n=== 完了集計 ===")
    for k, v in stats.items():
        logger.info(f"  {k:20s}: {v}")
    return 0 if stats["error"] == 0 and stats["md5_mismatch"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

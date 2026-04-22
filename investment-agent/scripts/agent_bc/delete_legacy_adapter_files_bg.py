#!/usr/bin/env python3
"""#A 物理分離 Step E: 旧 GCS adapter 関連ファイルを削除.

削除対象:
 - monthly/meta/<ticker>/adapter.json  (516 件、URL型は url_adapter.json にコピー済)
 - monthly/meta/<ticker>/ir_url.json  (324 件、legacy)
 - monthly/meta/<ticker>/download_adapter.json  (318 件、legacy)

安全確認:
 - adapter.json 削除前に: URL 型なら url_adapter.json が存在することを確認
   (存在しなければ削除しない、レポートに記録)
 - extract 型 (fields を持つ) の adapter.json は: extract_adapter.json が存在することを確認
 - どちらにも該当しない (excluded stub 等) は ambiguous カテゴリで記録、削除する

ir_url.json / download_adapter.json は legacy 削除なので無条件に削除。
"""
from __future__ import annotations

import json
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

    # 全 blob 列挙 → 3 タイプに分類
    adapter_blobs = []
    ir_url_blobs = []
    dl_adapter_blobs = []
    for b in bucket.list_blobs(prefix="monthly/meta/"):
        name = b.name.split("/")[-1]
        if name == "adapter.json":
            adapter_blobs.append(b)
        elif name == "ir_url.json":
            ir_url_blobs.append(b)
        elif name == "download_adapter.json":
            dl_adapter_blobs.append(b)

    logger.info(f"adapter.json          : {len(adapter_blobs)}")
    logger.info(f"ir_url.json (legacy)  : {len(ir_url_blobs)}")
    logger.info(f"download_adapter.json : {len(dl_adapter_blobs)}")
    logger.info(f"合計: {len(adapter_blobs) + len(ir_url_blobs) + len(dl_adapter_blobs)} blob")

    stats = {
        "adp_deleted_url_type": 0,
        "adp_deleted_ext_type": 0,
        "adp_deleted_other": 0,
        "adp_skipped_unsafe": 0,
        "ir_url_deleted": 0,
        "dl_adp_deleted": 0,
        "error": 0,
    }

    # ---- Phase 1: adapter.json (URL型 / extract型 / other に分類して削除) ----
    logger.info("\n=== Phase 1: adapter.json 削除 ===")
    for i, b in enumerate(adapter_blobs, 1):
        ticker = b.name.split("/")[2]
        try:
            d = json.loads(b.download_as_text())
        except Exception:
            d = {}
        has_url = "ir_page_url" in d or ("url" in d and "type" in d)
        has_fields = isinstance(d.get("fields"), list)

        # 安全確認: url_adapter.json または extract_adapter.json が存在するか
        url_adp_exists = bucket.blob(f"monthly/meta/{ticker}/url_adapter.json").exists()
        ext_adp_exists = bucket.blob(f"monthly/meta/{ticker}/extract_adapter.json").exists()

        try:
            if has_url and not has_fields:
                if not url_adp_exists:
                    logger.warning(f"  [{ticker}] URL型だが url_adapter.json 無し → 削除スキップ")
                    stats["adp_skipped_unsafe"] += 1
                    continue
                b.delete()
                stats["adp_deleted_url_type"] += 1
            elif has_fields and not has_url:
                if not ext_adp_exists:
                    logger.warning(f"  [{ticker}] extract型だが extract_adapter.json 無し → 削除スキップ")
                    stats["adp_skipped_unsafe"] += 1
                    continue
                b.delete()
                stats["adp_deleted_ext_type"] += 1
            else:
                # other: excluded stub 等 (already handled by Option C earlier)
                b.delete()
                stats["adp_deleted_other"] += 1
        except Exception as e:
            logger.warning(f"  [{ticker}] adapter.json 削除失敗: {e}")
            stats["error"] += 1

        if i % 100 == 0:
            logger.info(f"  進捗 {i}/{len(adapter_blobs)}")

    # ---- Phase 2: ir_url.json (legacy 無条件削除) ----
    logger.info("\n=== Phase 2: ir_url.json 削除 ===")
    for i, b in enumerate(ir_url_blobs, 1):
        try:
            b.delete()
            stats["ir_url_deleted"] += 1
        except Exception as e:
            logger.warning(f"  ir_url.json 削除失敗 {b.name}: {e}")
            stats["error"] += 1
        if i % 100 == 0:
            logger.info(f"  進捗 {i}/{len(ir_url_blobs)}")

    # ---- Phase 3: download_adapter.json (legacy 無条件削除) ----
    logger.info("\n=== Phase 3: download_adapter.json 削除 ===")
    for i, b in enumerate(dl_adapter_blobs, 1):
        try:
            b.delete()
            stats["dl_adp_deleted"] += 1
        except Exception as e:
            logger.warning(f"  download_adapter.json 削除失敗 {b.name}: {e}")
            stats["error"] += 1
        if i % 100 == 0:
            logger.info(f"  進捗 {i}/{len(dl_adapter_blobs)}")

    logger.info("\n=== 完了集計 ===")
    for k, v in stats.items():
        logger.info(f"  {k:25s}: {v}")

    # 残存確認
    remaining_adapter = sum(1 for _ in bucket.list_blobs(prefix="monthly/meta/") if _.name.endswith("/adapter.json"))
    remaining_ir = sum(1 for _ in bucket.list_blobs(prefix="monthly/meta/") if _.name.endswith("/ir_url.json"))
    remaining_dl = sum(1 for _ in bucket.list_blobs(prefix="monthly/meta/") if _.name.endswith("/download_adapter.json"))
    logger.info(f"\n残存:")
    logger.info(f"  adapter.json          : {remaining_adapter}")
    logger.info(f"  ir_url.json           : {remaining_ir}")
    logger.info(f"  download_adapter.json : {remaining_dl}")

    return 0 if stats["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""7532 adapter + structure.json 修正.

BC の「ドン・キホーテ」サブブランド行は 2022-06 で打ち切り、
2022-07 以降は「国内小売」集約行のみ. 以下を更新:
  1. extract_adapter.json: ドン・キホーテ field に bc_ignore=true
  2. structure.json: metrics から ドン・キホーテ 系を除去 (BC の現行 KPI page 反映)
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

ROOT = Path(__file__).resolve().parent.parent
ADAPTER_PATH = ROOT / "data/monthly_adapters/7532.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


def main() -> int:
    from google.cloud import storage
    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")

    # 1) extract_adapter.json (ローカル)
    with ADAPTER_PATH.open(encoding="utf-8") as f:
        adp = json.load(f)
    for fld in adp.get("fields", []):
        key = fld.get("key", "")
        if key.startswith("ドン・キホーテ"):
            fld["bc_ignore"] = True
            fld["_bc_ignore_reason"] = "BC 側サブブランド行 2022-06 打ち切り、国内小売行に統合"
    adp["regex_redesign_at"] = datetime.now(JST).isoformat()
    adp["regex_redesign_note"] = (
        "ドン・キホーテ サブブランド 5 field を bc_ignore=true. "
        "BC は 2022-07 以降 国内小売 集約行のみ維持."
    )
    with ADAPTER_PATH.open("w", encoding="utf-8") as f:
        json.dump(adp, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ extract_adapter 更新: {ADAPTER_PATH}")

    # 2) GCS 同期: extract_adapter.json
    bucket.blob("monthly/meta/7532/extract_adapter.json").upload_from_filename(str(ADAPTER_PATH))
    logger.info("✅ GCS extract_adapter 同期")

    # 3) structure.json (GCS 直更新): ドン・キホーテ metrics を削除
    struct_blob = bucket.blob("monthly/meta/7532/structure.json")
    if struct_blob.exists():
        struct = json.loads(struct_blob.download_as_text())
        orig_metrics = struct.get("metrics", [])
        new_metrics = [m for m in orig_metrics if not m.get("name", "").startswith("ドン・キホーテ")]
        struct["metrics"] = new_metrics
        struct["updated_at"] = datetime.now(JST).isoformat()
        struct["_prev_metrics_backup"] = orig_metrics
        struct["_structure_modified_reason"] = (
            "BC の「ドン・キホーテ」サブブランド行は 2022-06 で打ち切り、現行 KPI page では "
            "「国内小売」集約行のみ. structure.json を BC の現行状態に合わせて更新."
        )
        struct_blob.upload_from_string(
            json.dumps(struct, ensure_ascii=False, indent=2),
            content_type="application/json; charset=utf-8",
        )
        logger.info(f"✅ structure.json 更新: {len(orig_metrics)} → {len(new_metrics)} metrics")

    # 4) records 削除 → 再抽出
    rec_blob = bucket.blob("monthly/record/7532/monthly_records.json")
    if rec_blob.exists():
        rec_blob.delete()
        logger.info("✅ records 削除")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    logger.info("--- extract ---")
    r = subprocess.run(
        [sys.executable, "scripts/extract_monthly_data.py", "--tickers", "7532", "--since", "2024"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT), timeout=900,
    )
    sys.stdout.write(r.stdout[-2000:])

    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        logger.info(f"\n--- records ({len(records)} 件) ---")
        for rec in sorted(records, key=lambda r: str(r.get('year_month', '')))[-5:]:
            logger.info(f"  {rec.get('year_month')}: {rec.get('fields', {})}")

    logger.info("\n--- compare ---")
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", "7532"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT), timeout=300,
    )
    sys.stdout.write(r.stdout[-2000:])
    return 0


if __name__ == "__main__":
    sys.exit(main())

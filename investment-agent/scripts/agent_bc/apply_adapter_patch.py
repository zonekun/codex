#!/usr/bin/env python3
"""adapter.json に JSON patch を deep merge + GCS 同期.

Usage:
  # トップレベル flag 追加
  PYTHONUTF8=1 python scripts/agent_bc/apply_adapter_patch.py --ticker 8218 \
      --patch '{"year_month_from_submission_minus_1": true}'

  # fields[*] の bc_ignore/yoy_offset/bc_key 等を特定 key 対象に変更
  PYTHONUTF8=1 python scripts/agent_bc/apply_adapter_patch.py --ticker 7532 \
      --field-key "ドン・キホーテ 全店 売上（前年同月比）" \
      --field-patch '{"bc_ignore": true, "_bc_ignore_reason": "BC打ち切り"}'

オプション:
  --no-snapshot: snapshot 自動作成を抑止
  --no-gcs: GCS 同期スキップ
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent


def deep_merge(base: dict, patch: dict) -> dict:
    for k, v in patch.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", required=True)
    p.add_argument("--patch", default="", help="top-level JSON patch (dict)")
    p.add_argument("--field-key", default="", help="対象 field の key (完全一致)")
    p.add_argument("--field-patch", default="", help="指定 field 単位の JSON patch")
    p.add_argument("--no-snapshot", action="store_true")
    p.add_argument("--no-gcs", action="store_true")
    args = p.parse_args()
    ticker = args.ticker
    adapter_path = ROOT / f"data/monthly_adapters/{ticker}.json"

    if not adapter_path.exists():
        print(f"[error] adapter なし: {adapter_path}")
        return 1

    # snapshot
    if not args.no_snapshot:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/agent_bc/snapshot_adapter.py"),
             "--ticker", ticker, "--save"],
            check=False, cwd=str(ROOT),
        )

    with adapter_path.open(encoding="utf-8") as f:
        adp = json.load(f)

    applied = []
    if args.patch:
        top_patch = json.loads(args.patch)
        deep_merge(adp, top_patch)
        applied.append(f"top-level: {list(top_patch.keys())}")

    if args.field_key and args.field_patch:
        fp = json.loads(args.field_patch)
        matched = 0
        for fld in adp.get("fields", []):
            if fld.get("key") == args.field_key:
                deep_merge(fld, fp)
                matched += 1
        if matched == 0:
            print(f"[warn] field-key 未一致: {args.field_key}")
        else:
            applied.append(f"field '{args.field_key}': {list(fp.keys())}")

    adp["_agent_patched_at"] = datetime.now(JST).isoformat()
    if "_agent_patch_history" not in adp:
        adp["_agent_patch_history"] = []
    adp["_agent_patch_history"].append({
        "ts": datetime.now(JST).isoformat(),
        "applied": applied,
    })

    with adapter_path.open("w", encoding="utf-8") as f:
        json.dump(adp, f, ensure_ascii=False, indent=2)
    print(f"[local] {adapter_path} 更新: {applied}")

    if not args.no_gcs:
        from google.cloud import storage
        client = storage.Client(project="gmailpj-357912")
        client.bucket("stock_data_1930932").blob(
            f"monthly/meta/{ticker}/extract_adapter.json"
        ).upload_from_filename(str(adapter_path))
        print(f"[gcs] 同期完了")

    return 0


if __name__ == "__main__":
    sys.exit(main())

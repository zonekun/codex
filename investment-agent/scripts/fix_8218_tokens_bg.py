#!/usr/bin/env python3
"""8218 コメリ: key_tokens 厳格化で サブブランド行 (ＰＷ全店等) の誤拾いを解消.

debug_8218_pdf_tables_bg.py で判明した事実:
  PDF Table #0 R8 全店舗 1月=102.8 (BC 102.9 と一致)
  PDF Table #1 R8 全店舗 1月=100.8 (BC 100.8 と一致)
  PDF Table #2 R8 全店舗 1月=102.0 (BC 102.0 と一致)
  → PDF に正解値が入っている.

_extract_pdf_by_column の matching logic:
  1. row_label_regex check (R8 マッチ)
  2. token check fallback (tokens = ["全店", "売上"])
     → "全店" は "ＰＷ全店" にも部分一致するため R5 ＰＷ全店 行が先にマッチ.
  3. fields_data に一度値が入ると `if key in fields_data: continue` で skip
     → R8 の値に辿り着かない.

修正: key を "全店舗 X" / "全既存店舗 X" に変更 → tokens ["全店舗"] で厳格マッチ.
     bc_key は "全店 X" / "既存店 X" を保持 (BC 突合互換).
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
ADAPTER_PATH = ROOT / "data/monthly_adapters/8218.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def build_fields() -> list[dict]:
    # PDF 表を 3 つ (売上/客数/客単価)、各 「全店舗」「全既存店舗」行を狙う.
    # tokens = [最初の prefix, suffix] — prefix は row label 厳格一致、
    # suffix は table_above_text (売上高前年比 / 客数前年比 / 客単価前年比) にマッチ.
    fields: list[dict] = []
    for prefix_key, bc_prefix in [("全店舗", "全店"), ("全既存店舗", "既存店")]:
        for suffix, bc_suffix in [
            ("売上（前年同月比）", "売上（前年同月比）"),
            ("客数（前年同月比）", "客数（前年同月比）"),
            ("客単価（前年同月比）", "客単価（前年同月比）"),
        ]:
            fields.append({
                "key": f"{prefix_key} {suffix}",
                "bc_key": f"{bc_prefix} {bc_suffix}",
                "row_label_regex": f"{prefix_key}",
                "value_type": "percentage",
            })
    # 店舗数
    fields.append({
        "key": "全店 店舗数",
        "bc_key": "全店 店舗数",
        "row_label_regex": r"合計([\d,]+)店舗",
        "group": 1,
        "value_type": "integer",
    })
    return fields


def main() -> int:
    from google.cloud import storage

    with ADAPTER_PATH.open(encoding="utf-8") as f:
        adapter = json.load(f)

    adapter["fields"] = build_fields()
    adapter["regex_redesign_at"] = datetime.now(JST).isoformat()
    adapter["regex_redesign_note"] = (
        "tokens 厳格化: key を '全店舗 X' / '全既存店舗 X' に変更. "
        "ＰＷ全店 / ＰＷ既存店 等のサブブランド行の誤拾いを防止."
    )

    with ADAPTER_PATH.open("w", encoding="utf-8") as f:
        json.dump(adapter, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ adapter 更新: {ADAPTER_PATH}")

    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")
    bucket.blob("monthly/meta/8218/extract_adapter.json").upload_from_filename(str(ADAPTER_PATH))
    logger.info("✅ GCS sync")

    rec_blob = bucket.blob("monthly/record/8218/monthly_records.json")
    if rec_blob.exists():
        rec_blob.delete()
        logger.info("✅ 既存 records 削除")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    logger.info("--- extract 実行 ---")
    r = subprocess.run(
        [sys.executable, "scripts/extract_monthly_data.py",
         "--tickers", "8218", "--since", "2024"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=900,
    )
    sys.stdout.write(r.stdout[-3000:])
    sys.stderr.write(r.stderr[-1500:])

    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        logger.info(f"\n--- records ({len(records)} 件) ---")
        for rec in records:
            logger.info(f"  {rec.get('year_month')}: {rec.get('fields', {})}")

    logger.info("\n--- compare ---")
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", "8218"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=300,
    )
    sys.stdout.write(r.stdout[-4000:])
    return 0


if __name__ == "__main__":
    sys.exit(main())

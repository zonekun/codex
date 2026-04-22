#!/usr/bin/env python3
"""8218 コメリ adapter 再設計 (Option A: fy_month_idx プレースホルダー).

PDF 構造（Phase 1 調査済）:
  - 提出日1行目
  - 3 テーブル: 売上高前年比 / 客数前年比 / 客単価前年比
  - 各テーブル: 4月 5月 ... ３月 当期累計 (累積表示)
  - 各テーブル行: ＰＷ既存店 / ＰＲＯ既存店 / Ｈ＆Ｇ既存店 / 全既存店舗 / ＰＷ全店 / ＰＲＯ全店 / Ｈ＆Ｇ全店 / 全店舗

fiscal_year_start_month=4 (3月期決算).
fy_month_idx: 4月→1, 5月→2, ..., 3月→12.

regex設計:
  - 「全店舗」「全既存店舗」ラベル + 12 個の optional capture group
  - group = "{fy_month_idx}"
  - 3 テーブル同ラベル区別のため match_occurrence (売上=1 / 客数=2 / 客単価=3)
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

# 12 optional groups (all CJK label-prefixed, with whitespace + [\d.]+ each)
_12GROUPS = (
    r"\s+([\d.]+)"
    + r"(?:\s+([\d.]+))?" * 11
)


def build_fields() -> list[dict]:
    """3 テーブル × 2 行 (全店舗/全既存店舗) × match_occurrence で 7 field 定義."""
    # match_occurrence: 売上=1 / 客数=2 / 客単価=3
    fields = [
        {
            "key": "全店 売上（前年同月比）",
            "bc_key": "全店 売上（前年同月比）",
            "row_label_regex": "全店舗" + _12GROUPS,
            "group": "{fy_month_idx}",
            "match_occurrence": 1,
            "value_type": "percentage",
        },
        {
            "key": "全店 客数（前年同月比）",
            "bc_key": "全店 客数（前年同月比）",
            "row_label_regex": "全店舗" + _12GROUPS,
            "group": "{fy_month_idx}",
            "match_occurrence": 2,
            "value_type": "percentage",
        },
        {
            "key": "全店 客単価（前年同月比）",
            "bc_key": "全店 客単価（前年同月比）",
            "row_label_regex": "全店舗" + _12GROUPS,
            "group": "{fy_month_idx}",
            "match_occurrence": 3,
            "value_type": "percentage",
        },
        {
            "key": "既存店 売上（前年同月比）",
            "bc_key": "既存店 売上（前年同月比）",
            "row_label_regex": "全既存店舗" + _12GROUPS,
            "group": "{fy_month_idx}",
            "match_occurrence": 1,
            "value_type": "percentage",
        },
        {
            "key": "既存店 客数（前年同月比）",
            "bc_key": "既存店 客数（前年同月比）",
            "row_label_regex": "全既存店舗" + _12GROUPS,
            "group": "{fy_month_idx}",
            "match_occurrence": 2,
            "value_type": "percentage",
        },
        {
            "key": "既存店 客単価（前年同月比）",
            "bc_key": "既存店 客単価（前年同月比）",
            "row_label_regex": "全既存店舗" + _12GROUPS,
            "group": "{fy_month_idx}",
            "match_occurrence": 3,
            "value_type": "percentage",
        },
        {
            "key": "全店 店舗数",
            "bc_key": "全店 店舗数",
            "row_label_regex": r"合計([\d,]+)店舗",
            "group": 1,
            "value_type": "integer",
        },
    ]
    return fields


def main() -> int:
    from google.cloud import storage

    with ADAPTER_PATH.open(encoding="utf-8") as f:
        adapter = json.load(f)

    # 新 adapter 構築
    adapter["source"] = "non-tdnet(pdf)"
    adapter["format"] = "pdf"
    adapter["extraction_method"] = "regex"
    adapter["fiscal_year_start_month"] = 4
    adapter["year_month_from_submission_minus_1"] = True
    adapter.pop("year_from_title_regex", None)
    adapter.pop("month_from_title_regex", None)
    adapter["fields"] = build_fields()
    adapter["manual_override"] = True
    adapter["regex_redesign_at"] = datetime.now(JST).isoformat()
    adapter["regex_redesign_note"] = (
        "Option A: fy_month_idx プレースホルダーで月列を指定. "
        "fiscal_year_start_month=4, 12 optional groups, "
        "3 テーブル (売上/客数/客単価) を match_occurrence=1/2/3 で区別."
    )

    with ADAPTER_PATH.open("w", encoding="utf-8") as f:
        json.dump(adapter, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ adapter 更新: {ADAPTER_PATH}")
    logger.info(f"  sample regex: {adapter['fields'][0]['row_label_regex'][:100]}...")

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

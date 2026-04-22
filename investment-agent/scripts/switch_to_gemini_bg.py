#!/usr/bin/env python3
"""任意 ticker の adapter を Gemini 抽出に切替.

引数: --tickers <T1 T2 ...>
各 ticker の adapter に extraction_method=gemini + overwrite_past_months=true を設定し、
fields 一覧から custom_prompt を自動生成. records 削除 → 再抽出 → compare.
"""
from __future__ import annotations

import argparse
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
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


def build_prompt(adapter: dict, ticker: str) -> str:
    fields_desc = []
    for f in adapter.get("fields", []):
        key = f.get("key") or f.get("name", "")
        val_type = f.get("value_type", "float")
        unit_hint = "整数" if val_type == "integer" else "前年同月比 (例 105.2)" if val_type == "percentage" else "数値"
        notes = f.get("notes", "")
        fields_desc.append(f'  - "{key}": {unit_hint}' + (f" ({notes})" if notes else ""))

    fields_block = "\n".join(fields_desc)

    return f"""\
あなたは ticker {ticker} の月次開示 PDF から KPI を抽出するアシスタントです.

抽出対象月: {{year}}年{{month}}月度

重要ルール:
  - 当月 (指定月) の値のみ採用
  - 四半期累計・上期累計・通期・前年同期実績 等の集計値は絶対に拾わない
  - サブブランド行ではなく「全店」「既存店」「合計」等の集約行を採用
  - 前年同月比 field には 「+X.X%増」「−X.X%減」の narrative が該当する場合は
    100 を加算した値 (例: +5.2% → 105.2, -4.9% → 95.1) を返す
  - 見つからない field は null

出力形式: JSON
{{
{fields_block}
}}
"""


def process(ticker: str) -> bool:
    from google.cloud import storage
    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")

    adapter_path = ROOT / f"data/monthly_adapters/{ticker}.json"
    if not adapter_path.exists():
        logger.warning(f"[{ticker}] adapter 無し → skip")
        return False
    with adapter_path.open(encoding="utf-8") as f:
        adp = json.load(f)

    adp["extraction_method"] = "gemini"
    adp["overwrite_past_months"] = True
    adp["custom_prompt"] = build_prompt(adp, ticker)
    adp["regex_redesign_at"] = datetime.now(JST).isoformat()
    adp["regex_redesign_note"] = "Gemini 切替 (regex 限界 or 誤マッチのため)."

    with adapter_path.open("w", encoding="utf-8") as f:
        json.dump(adp, f, ensure_ascii=False, indent=2)
    logger.info(f"[{ticker}] adapter 更新")

    bucket.blob(f"monthly/meta/{ticker}/extract_adapter.json").upload_from_filename(str(adapter_path))

    rec_blob = bucket.blob(f"monthly/record/{ticker}/monthly_records.json")
    if rec_blob.exists():
        rec_blob.delete()

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    logger.info(f"[{ticker}] extract 実行...")
    r = subprocess.run(
        [sys.executable, "scripts/extract_monthly_data.py",
         "--tickers", ticker, "--since", "2024"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=1800,
    )
    sys.stdout.write(r.stdout[-1500:])
    if r.returncode != 0:
        logger.error(f"[{ticker}] extract 失敗")
        return False

    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        logger.info(f"[{ticker}] records: {len(records)} 件")
        for rec in sorted(records, key=lambda r: str(r.get('year_month', '')))[-3:]:
            logger.info(f"  {rec.get('year_month')}: {rec.get('fields', {})}")

    logger.info(f"[{ticker}] compare 実行...")
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", ticker],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=300,
    )
    sys.stdout.write(r.stdout[-1500:])
    return True


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", nargs="+", required=True)
    args = p.parse_args()
    for t in args.tickers:
        logger.info(f"\n{'=' * 80}\n{t}\n{'=' * 80}")
        try:
            process(t)
        except Exception as e:
            logger.error(f"[{t}] exception: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

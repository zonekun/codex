#!/usr/bin/env python3
"""8914 エリアリンク adapter を Gemini 抽出に切替 (regex 限界対応).

状況:
  - PDF は「過去5年間推移」「直近9ヶ月表」「最新月narrative」等 3+ テーブル構造.
  - 調査ログ (investigate_8914_2026_01_bg.py) より、BC 80.64 は
    「... 88.29 80.64 稼働室数 稼働率(%) 既存稼働率(%) 86.84 ...」の 10列目.
  - 一方 regex は別テーブル (「.. 91.10 91,859 83.39 稼働率(%) ..」) の 83.39 を拾う.
  - 列数可変・表レイアウト複雑なため regex では根本解決困難.

対応:
  - extraction_method を gemini に切替
  - custom_prompt で BC 定義に合わせた field を明示
  - overwrite_past_months=true
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
ADAPTER_PATH = ROOT / "data/monthly_adapters/8914.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

CUSTOM_PROMPT = """\
あなたはエリアリンク (8914) ハローストレージ月次実績 PDF から KPI を抽出するアシスタントです.

PDF 構造:
  - ページ冒頭: narrative (当月の総室数/稼働室数/稼働率/既存稼働率)
    → 但し narrative の値は「パートナー出店を除いた当社のみ」の値
  - 中盤: 過去5年間推移表 (2021.6, 2021.12, 2022.6 等の半期時系列)
  - 下部: 直近数ヶ月の月次表（12月 1月 2月 3月 ... 月ヘッダ + 総室数/稼働室数/稼働率/既存稼働率 の行）
  - 巻末: 新規出店詳細

BC (バフェットコード) の稼働率は「パートナー出店を含む全体稼働率」.
→ narrative の値は採用せず、月次表 (125,741 等の総室数列) の稼働率 を採用.

抽出対象 field (指定月 {year}年{month}月):
  - 総室数: integer (例 125741) — 月次表の当月総室数
  - 稼働室数: integer — 月次表の当月稼働室数
  - 稼働率(%): float (例 80.64) — 月次表の当月稼働率 (NOT 過去5年推移表の値)
  - 既存稼働率(%): float (例 86.84) — 月次表の当月既存稼働率
  - 新規稼働率(%): float (例 44.86) — 月次表 or narrative の新規稼働率

出力形式: JSON:
  {
    "総室数": <int>,
    "稼働室数": <int>,
    "稼働率(%)": <float>,
    "既存稼働率(%)": <float>,
    "新規稼働率(%)": <float>
  }
見つからない field は null.
過去5年間推移表 (半期データ 2021.6 等) の値は絶対に採用しないこと.
"""


def main() -> int:
    from google.cloud import storage

    with ADAPTER_PATH.open(encoding="utf-8") as f:
        adapter = json.load(f)

    adapter["extraction_method"] = "gemini"
    adapter["custom_prompt"] = CUSTOM_PROMPT
    adapter["overwrite_past_months"] = True
    adapter["regex_redesign_at"] = datetime.now(JST).isoformat()
    adapter["regex_redesign_note"] = (
        "regex 限界対応: 過去5年推移表 vs 月次表 の識別が困難なため Gemini 抽出に切替. "
        "custom_prompt で BC 定義 (パートナー含む全体稼働率) を明示."
    )

    # fields の regex は残しつつ extraction_method=gemini が優先
    # bc_ignore 設定は維持

    with ADAPTER_PATH.open("w", encoding="utf-8") as f:
        json.dump(adapter, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ adapter 更新: {ADAPTER_PATH}")

    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")

    bucket.blob("monthly/meta/8914/extract_adapter.json").upload_from_filename(str(ADAPTER_PATH))
    logger.info("✅ GCS sync")

    rec_blob = bucket.blob("monthly/record/8914/monthly_records.json")
    if rec_blob.exists():
        rec_blob.delete()
        logger.info("✅ 既存 records 削除")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    logger.info("--- extract 実行（Gemini）---")
    r = subprocess.run(
        [sys.executable, "scripts/extract_monthly_data.py",
         "--tickers", "8914", "--since", "2024"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=1200,
    )
    sys.stdout.write(r.stdout[-3000:])
    sys.stderr.write(r.stderr[-1500:])

    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        logger.info(f"records: {len(records)} 件")
        for rec in records[-6:]:
            logger.info(f"  {rec.get('year_month')}: {rec.get('fields', {})}")

    logger.info("--- compare 実行 ---")
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", "8914"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=300,
    )
    sys.stdout.write(r.stdout[-3000:])
    return 0


if __name__ == "__main__":
    sys.exit(main())

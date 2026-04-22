#!/usr/bin/env python3
"""6036 KeePer技研 adapter 再設計.

問題:
  - PDF は narrative 文（「前年同月比5.0％増」等）+ 大きなテーブル（YYYY/M プレフィックス行）
    の混在構造で、既存 regex は狭い文言マッチで 2026-02/03 が取れない.
  - BC 値: 全店 売上 2026-02=105.0 / 2026-03=93.9, 既存店 売上 2026-02=101.0 / 2026-03=89.6
  - 現 extract: 6.8, 3.8, 0.8, 10.4 — narrative 内の他文や table 列のゴミを拾っている

対応:
  1. extraction_method を gemini に変更（文章 + 表 混在 PDF の符号判定は LLM に任せる）
  2. custom_prompt で月次 KPI だけを抽出させる
  3. overwrite_past_months=true で最新 PDF の時系列上書きを許容
  4. 既存 records 削除 → 再抽出 → compare で検証
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
ADAPTER_PATH = ROOT / "data/monthly_adapters/6036.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

CUSTOM_PROMPT = """\
あなたは KeePer技研 (6036) の月次速報 PDF から、月次 KPI を抽出するアシスタントです。

この PDF には以下の情報が混在しています:
  - 文章式 narrative（「前年同月比5.0%増」「前年比8％減」等の表現）
  - 大きなテーブル（先頭列が YYYY/M、2列目が売上額、3列目が前年比 %）
  - 全社合計・アフターマーケット・新車マーケット等のセクション合計表

抽出ルール:
  1. 指定月 ({year}年{month}月) の KPI のみを返すこと
  2. 全店 売上（前年同月比）: LABO 店舗 全体（既存店 + 新店）の売上前年比
     - 表中 「YYYY/M 売上額 ±X.X%」の ±X.X% に 100 を加算した値 (+5.2% → 105.2)
     - narrative に「前年同月比X.X%増」があればそれを 100+X.X として採用
     - 「減」「低下」等の符号判定を行うこと
  3. 既存店 売上（前年同月比）: ≪LABO店舗：既存店≫ 表内 「YYYY/M 売上高 ±X.X%」
  4. 直営 店舗数: narrative「直営店全N店」または表内 LABO 店舗数列 の N
  5. 既存店 客単価（前年同月比）: 既存店テーブル 平均単価列 の ±X.X%

出力形式: JSON (数値のみ、単位不要)
  {
    "全店 売上（前年同月比）": <float, 例: 105.2>,
    "直営 店舗数": <int, 例: 172>,
    "既存店 売上（前年同月比）": <float>,
    "既存店 客単価（前年同月比）": <float>
  }

見つからない field は null。
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
        "narrative + 表混在 PDF の符号判定を Gemini 抽出に切替. custom_prompt で KPI と符号ルール明示."
    )

    with ADAPTER_PATH.open("w", encoding="utf-8") as f:
        json.dump(adapter, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ adapter 更新: {ADAPTER_PATH}")

    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")

    bucket.blob("monthly/meta/6036/extract_adapter.json").upload_from_filename(str(ADAPTER_PATH))
    logger.info("✅ GCS sync")

    rec_blob = bucket.blob("monthly/record/6036/monthly_records.json")
    if rec_blob.exists():
        rec_blob.delete()
        logger.info("✅ 既存 records 削除")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    logger.info("--- extract 実行（Gemini）---")
    r = subprocess.run(
        [sys.executable, "scripts/extract_monthly_data.py",
         "--tickers", "6036", "--since", "2024"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=900,
    )
    sys.stdout.write(r.stdout[-3000:])
    sys.stderr.write(r.stderr[-1500:])

    logger.info("--- compare 実行 ---")
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", "6036"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=300,
    )
    sys.stdout.write(r.stdout[-3000:])
    return 0


if __name__ == "__main__":
    sys.exit(main())

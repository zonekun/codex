#!/usr/bin/env python3
"""8914 2026-01 稼働率 83.39 vs BC 80.64 調査.

BC 80.64 = 101396 / 125741 * 100 (稼働室数/総室数).
我々の抽出 83.39 は PDF 内の別行から.
PDF テキストを dump して 83.39 の出所を特定し、regex を修正する.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
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


def main() -> int:
    from google.cloud import bigquery, storage

    bq = bigquery.Client(project="gmailpj-357912")
    storage_client = storage.Client(project="gmailpj-357912")
    bucket = storage_client.bucket("stock_data_1930932")

    # 2026-01 PDF (提出 2026-02-05) を取得
    sql = """
    SELECT SUBMISSION_DATE, DOC_TITLE, FILE_NAME,
           STRING_AGG(CHUNK_TEXT, ' ') AS full_text
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE TICKER = '8914'
      AND MAIN_CATEGORY = '月次開示'
      AND SUBMISSION_DATE BETWEEN '2026-01-20' AND '2026-02-15'
    GROUP BY SUBMISSION_DATE, DOC_TITLE, FILE_NAME
    ORDER BY SUBMISSION_DATE DESC
    LIMIT 1
    """
    docs = list(bq.query(sql).result())
    if not docs:
        logger.error("2026-01 PDF not found")
        return 1
    doc = docs[0]
    logger.info(f"対象: {doc.SUBMISSION_DATE} {doc.DOC_TITLE}")

    text = doc.full_text
    logger.info(f"text length: {len(text)}")

    # 稼働率関連の行を全て抽出して表示
    logger.info("\n--- 稼働率 関連行 dump ---")
    # 改行 or 2+ spaces で分割
    lines = re.split(r"\n|  {2,}", text)
    for i, line in enumerate(lines):
        if re.search(r"稼働率", line):
            logger.info(f"[L{i}] {line[:250]}")

    # 83.39 付近を抽出
    logger.info("\n--- 83.39 出現箇所 ---")
    for m in re.finditer(r".{0,80}83\.39.{0,80}", text):
        logger.info(f"  → {m.group(0)}")

    # 80.64 付近 (BC 正解) を抽出
    logger.info("\n--- 80.64 出現箇所 ---")
    for m in re.finditer(r".{0,80}80\.64.{0,80}", text):
        logger.info(f"  → {m.group(0)}")

    # 101396, 125741 出現
    logger.info("\n--- 総室数/稼働室数 出現 ---")
    for kw in ["125,741", "101,396", "125741", "101396"]:
        for m in re.finditer(rf".{{0,60}}{re.escape(kw)}.{{0,60}}", text):
            logger.info(f"  [{kw}] → {m.group(0)[:150]}")
            break  # 1件のみ

    # 8914.json に稼働率 field の regex 上書き適用
    #   2026-01 PDF では「稼働率(%)」行の 1 列目が 83.39 で誤.
    #   正しくは 稼働室数/総室数 計算値 80.64.
    #   アプローチ: 稼働率 計算式化 (bc_ignore=false を維持しつつ、
    #   extract 側で compute rule を適用).
    # 簡易対応: 2026-01 のみ修正するのは複雑なので、
    # 稼働率(%) field に計算式 "calc_from_ratio" を仕込める仕組みは現状未実装.
    # → 次回実装: extract_monthly_data.py に `calc_expr` field サポート追加.
    # 今回は調査のみで regex 修正は保留.

    logger.info("\n=== 調査結果サマリー ===")
    logger.info("83.39 / 80.64 の出現パターンを上記ログから確認し、")
    logger.info("2026-01 PDF 内で正しい稼働率行を特定する regex を設計する.")
    logger.info("出力ログは標準 stdout.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

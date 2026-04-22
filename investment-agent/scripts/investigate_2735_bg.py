#!/usr/bin/env python3
"""2735 調査: sections match_occurrence=4 range-out 原因特定.

問題: 下半期 section (match_occurrence=4) で期末店舗数が範囲外.
ヒント: `\\d` Unicode match が全角数字にも hit して occurrence ずれ.

調査項目:
  1. 現状 compare 実行
  2. 最新 PDF 全文取得
  3. `期末店舗数` が本文中に何回現れるか (1回 or 複数回)
  4. 全角/半角混在テキストのチェック
  5. 修正案: (?a:\\d) ASCII 限定化
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")

ROOT = Path(__file__).resolve().parent.parent
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


def main() -> int:
    from google.cloud import bigquery
    bq = bigquery.Client(project="gmailpj-357912")
    ticker = "2735"

    # 1) compare
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    logger.info("=== compare ===")
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", ticker],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=300,
    )
    sys.stdout.write(r.stdout[-2500:])

    # 2) 最新 2 件の full_text
    logger.info("\n=== TDnet 最新 2 件 full_text ===")
    sql = f"""
    SELECT SUBMISSION_DATE, DOC_TITLE, STRING_AGG(CHUNK_TEXT, ' ') AS full_text
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE TICKER = '{ticker}'
      AND MAIN_CATEGORY = '月次開示'
      AND SUBMISSION_DATE >= '2025-09-01'
    GROUP BY SUBMISSION_DATE, DOC_TITLE
    ORDER BY SUBMISSION_DATE DESC
    LIMIT 4
    """
    for doc in bq.query(sql).result():
        txt = doc.full_text
        logger.info(f"\n--- {doc.SUBMISSION_DATE} {doc.DOC_TITLE} ---")
        logger.info(f"  text length: {len(txt)}")
        # 期末店舗数 出現位置すべて
        occurrences = list(re.finditer(r"期末店舗数", txt))
        logger.info(f"  「期末店舗数」 occurrences: {len(occurrences)}")
        for i, m in enumerate(occurrences):
            ctx = txt[max(0, m.start()-20):m.end()+150]
            logger.info(f"    [{i+1}] pos={m.start()}: {ctx!r}")

        # 「全店」も確認
        zen_ten = list(re.finditer(r"全店", txt))
        logger.info(f"  「全店」 occurrences: {len(zen_ten)}")

        # 「全 社」/「全社」
        zen_sha = list(re.finditer(r"全\s*社", txt))
        logger.info(f"  「全社」 occurrences: {len(zen_sha)}")

        # 全角数字が含まれるか
        zenkaku_nums = re.findall(r"[０-９]", txt)
        logger.info(f"  全角数字 (０-９) 出現数: {len(zenkaku_nums)}")

        # 「期末店舗数 + 数字列」の全マッチ
        logger.info(f"\n  期末店舗数\\s+数字パターン finditer:")
        for i, m in enumerate(re.finditer(r"期末店舗数\s+[\d,]+[\s\d,]+", txt)):
            ctx = txt[m.start():m.start()+250]
            logger.info(f"    match #{i+1}: {ctx!r}")

        # adapter の section2 regex 実テスト (月3〜8月、match_occurrence=4 部分)
        pat = r"期末店舗数\s+(\d[\d,]*)(?:\s+(\d[\d,]*))?(?:\s+(\d[\d,]*))?(?:\s+(\d[\d,]*))?(?:\s+(\d[\d,]*))?(?:\s+(\d[\d,]*))?"
        matches = list(re.finditer(pat, txt))
        logger.info(f"\n  section2 regex マッチ数: {len(matches)}")
        for i, m in enumerate(matches):
            logger.info(f"    [{i+1}] groups={m.groups()} matched={m.group(0)[:120]!r}")

        # ASCII 限定 版
        pat_ascii = r"期末店舗数\s+((?a:\d)[(?a:\d),]*)(?:\s+((?a:\d)[(?a:\d),]*))?"
        # この書き方は構文エラーなので簡易版
        pat_ascii_simple = r"期末店舗数\s+([0-9][0-9,]*)(?:\s+([0-9][0-9,]*))?(?:\s+([0-9][0-9,]*))?(?:\s+([0-9][0-9,]*))?(?:\s+([0-9][0-9,]*))?(?:\s+([0-9][0-9,]*))?"
        m2 = list(re.finditer(pat_ascii_simple, txt))
        logger.info(f"  ASCII-only regex マッチ数: {len(m2)}")
        for i, m in enumerate(m2):
            logger.info(f"    [{i+1}] groups={m.groups()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

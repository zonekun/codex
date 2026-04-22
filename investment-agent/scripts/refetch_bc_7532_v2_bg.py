#!/usr/bin/env python3
"""7532 BC KPI 再取得 v2.

v1 は __NEXT_DATA__ が見つからず失敗. DOM から table を直接取得する.
buffett-code.com の KPI ページは `<table>` 構造で月次データを表示.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")

ROOT = Path(__file__).resolve().parent.parent
OUT_CSV = ROOT / "data/csv/bc_monthly_kpi.csv"
CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
TARGET_URL = "https://www.buffett-code.com/company/7532/kpi"
DEBUG_HTML = ROOT / "data/logs/bc_7532_debug.html"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


def create_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    opts = Options()
    opts.binary_location = CHROME_EXE
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    return driver


def normalize_ym(s: str) -> str:
    if not s:
        return ""
    s = str(s).strip()
    m = re.match(r"(\d{4})[/\-年](\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return ""


def parse_value(s: str):
    if not s:
        return None
    s = str(s).replace(",", "").replace("％", "").replace("%", "").strip()
    if not s or s in ("-", "−", "―", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def main() -> int:
    driver = create_driver()
    try:
        logger.info(f"GET {TARGET_URL}")
        driver.get(TARGET_URL)
        # 十分長く待機（動的 rendering 対応）
        time.sleep(15)

        # ページソース保存 (デバッグ)
        html = driver.page_source
        DEBUG_HTML.parent.mkdir(parents=True, exist_ok=True)
        DEBUG_HTML.write_text(html, encoding="utf-8")
        logger.info(f"HTML length: {len(html)}, saved to {DEBUG_HTML}")

        # テーブル or 構造化データを JS で取得
        tables = driver.execute_script("""
            const tables = [];
            for (const t of document.querySelectorAll('table')) {
                const rows = [];
                for (const tr of t.querySelectorAll('tr')) {
                    const cells = [];
                    for (const c of tr.querySelectorAll('th,td')) {
                        cells.push(c.innerText.trim());
                    }
                    if (cells.length) rows.push(cells);
                }
                if (rows.length) tables.push(rows);
            }
            return tables;
        """)
        logger.info(f"Tables found: {len(tables)}")

        new_rows = []
        for ti, tbl in enumerate(tables):
            if len(tbl) < 2:
                continue
            logger.info(f"\n--- Table #{ti} rows={len(tbl)} cols={len(tbl[0])} ---")
            for ri, row in enumerate(tbl[:5]):
                logger.info(f"  R{ri}: {row[:8]}")

            # ヘッダー行: year_month の列 or field 名
            header = tbl[0]
            # Pattern A: ヘッダーに year_month (2025-01 等), 1列目に field 名
            ym_cols = {}
            for ci, h in enumerate(header):
                ym = normalize_ym(h)
                if ym:
                    ym_cols[ci] = ym
            if ym_cols:
                logger.info(f"  month columns: {len(ym_cols)}")
                for row in tbl[1:]:
                    if len(row) <= 1:
                        continue
                    field = row[0].strip()
                    if not field:
                        continue
                    for ci, ym in ym_cols.items():
                        if ci < len(row):
                            v = parse_value(row[ci])
                            if v is not None:
                                new_rows.append({
                                    "ticker": "7532",
                                    "year_month": ym,
                                    "field": field,
                                    "value": str(v),
                                })
            else:
                # Pattern B: 1列目に year_month, 各列が field
                fields_cols = {ci: h.strip() for ci, h in enumerate(header)
                              if ci > 0 and h and h.strip()}
                col0_ym = any(normalize_ym(row[0]) for row in tbl[1:4] if row)
                if col0_ym and fields_cols:
                    logger.info(f"  year_month は1列目, fields {len(fields_cols)} 列")
                    for row in tbl[1:]:
                        if len(row) <= 1:
                            continue
                        ym = normalize_ym(row[0])
                        if not ym:
                            continue
                        for ci, field in fields_cols.items():
                            if ci < len(row):
                                v = parse_value(row[ci])
                                if v is not None:
                                    new_rows.append({
                                        "ticker": "7532",
                                        "year_month": ym,
                                        "field": field,
                                        "value": str(v),
                                    })

        logger.info(f"\n合計 new rows: {len(new_rows)}")

        if not new_rows:
            logger.error("0 行, スクリプト終了")
            return 1

        # 既存 CSV 更新
        existing_rows = []
        if OUT_CSV.exists():
            with OUT_CSV.open(encoding="utf-8-sig") as f:
                r = csv.DictReader(f)
                existing_rows = [row for row in r if row.get("ticker") != "7532"]
        logger.info(f"既存 CSV (7532 除く): {len(existing_rows)} 行")

        all_rows = existing_rows + new_rows
        with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["ticker","year_month","field","value"])
            w.writeheader()
            w.writerows(all_rows)
        logger.info(f"✅ CSV 更新: {OUT_CSV}")

        # 7532 sample
        logger.info("\n--- 新 7532 sample (最初 20 行) ---")
        for r in new_rows[:20]:
            logger.info(f"  {r}")
        logger.info("\n--- field 一覧 ---")
        fields_all = sorted(set(r["field"] for r in new_rows))
        for f_ in fields_all:
            logger.info(f"  {f_}")
        logger.info("\n--- year_month 範囲 ---")
        ym_all = sorted(set(r["year_month"] for r in new_rows))
        logger.info(f"  {min(ym_all)} 〜 {max(ym_all)} ({len(ym_all)} 月)")

    finally:
        try:
            driver.quit()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

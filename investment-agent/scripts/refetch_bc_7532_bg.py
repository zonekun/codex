#!/usr/bin/env python3
"""7532 の BC 月次 KPI を再取得 (Windows Chrome + Selenium).

buffett_monthly_local.py の create_driver / wait_waf を流用し、
/company/7532/kpi から __NEXT_DATA__ の全履歴を取得して
data/csv/bc_monthly_kpi.csv の 7532 行を upsert する.

WAF が発動した場合は 30 秒待機 + JS チャレンジ自動通過に期待.
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


def wait_waf(driver, max_sec: int = 60) -> bool:
    WAF_TITLES = {"Human Verification", "Verification Required", "Just a moment..."}
    for i in range(max_sec):
        title = driver.title or ""
        if title and title not in WAF_TITLES:
            logger.info(f"WAF 通過: title={title!r} ({i}s)")
            return True
        time.sleep(1)
    logger.warning(f"WAF 未通過: title={driver.title!r}")
    return False


def extract_monthly_from_next_data(driver) -> list[dict]:
    """__NEXT_DATA__ から全月次履歴を取得."""
    nd = driver.execute_script(
        "const el=document.getElementById('__NEXT_DATA__'); return el?el.textContent:null;"
    )
    if not nd:
        logger.warning("__NEXT_DATA__ 無し")
        return []
    data = json.loads(nd)
    pp = data.get("props", {}).get("pageProps", {})

    # 月次 time-series を探す: list of dicts with date/ym key + numeric fields
    for key, val in pp.items():
        if isinstance(val, list) and len(val) >= 10:
            if isinstance(val[0], dict):
                sample = val[0]
                date_keys = [k for k in sample.keys()
                             if k.lower() in ("date", "ym", "yearmonth", "period", "month")]
                if date_keys:
                    logger.info(f"  候補 key={key!r}, 長さ={len(val)}, date_field={date_keys[0]}")
                    return val

    # 見つからない場合、全 list を dump
    logger.info("time-series 候補なし. pageProps keys:")
    for k, v in pp.items():
        if isinstance(v, list) and len(v) > 0:
            logger.info(f"  {k!r}: list[{len(v)}] sample={str(v[0])[:100]}")
    return []


def normalize_ym(s: str) -> str:
    """日付文字列を YYYY-MM 形式に正規化."""
    if not s:
        return ""
    s = str(s)
    m = re.match(r"(\d{4})-(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.match(r"(\d{4})(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.match(r"(\d{4})年(\d{1,2})月", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return ""


def main() -> int:
    driver = create_driver()
    try:
        logger.info(f"GET {TARGET_URL}")
        driver.get(TARGET_URL)
        time.sleep(5)
        if not wait_waf(driver, 60):
            logger.error("WAF 通過失敗。手動介入必要。")
            time.sleep(60)  # 追加 1分 待機 (手動解決を期待)

        # __NEXT_DATA__ から抽出
        records = extract_monthly_from_next_data(driver)
        logger.info(f"取得 records: {len(records)}")

        if not records:
            logger.error("records 0件 → スクリプト終了")
            return 1

        # 全 field を抽出
        sample = records[0]
        logger.info(f"sample keys: {list(sample.keys())}")
        date_key = next((k for k in sample.keys() if k.lower() in ("date","ym","yearmonth","period","month")), None)
        if not date_key:
            logger.error("date key 不明")
            return 1

        # 既存 CSV を読み込み, 7532 行を除去して新データ追加
        existing_rows = []
        if OUT_CSV.exists():
            with OUT_CSV.open(encoding="utf-8-sig") as f:
                r = csv.DictReader(f)
                existing_rows = [row for row in r if row.get("ticker") != "7532"]
        logger.info(f"既存 CSV (7532 除く): {len(existing_rows)} 行")

        new_rows = []
        for rec in records:
            ym = normalize_ym(rec.get(date_key, ""))
            if not ym:
                continue
            for k, v in rec.items():
                if k == date_key or v is None or v == "":
                    continue
                if isinstance(v, (int, float)):
                    new_rows.append({
                        "ticker": "7532",
                        "year_month": ym,
                        "field": k,
                        "value": str(v),
                    })

        logger.info(f"新 7532 行: {len(new_rows)}")

        all_rows = existing_rows + new_rows
        OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
        with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["ticker","year_month","field","value"])
            w.writeheader()
            w.writerows(all_rows)
        logger.info(f"✅ CSV 更新: {OUT_CSV}")

        # 7532 の sample 表示
        logger.info("\n--- 新 7532 sample ---")
        for r in new_rows[:15]:
            logger.info(f"  {r}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

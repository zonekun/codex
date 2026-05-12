"""株主優待ページ取得サンプル（複数銘柄対応）

松井証券リサーチネットにログインし、指定銘柄の株主優待情報を取得する。
生データをJSONLに保存し、構造化CSVも出力する。

Usage:
    PYTHONUTF8=1 python scripts/test_yutai_nav.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import structlog
from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

log = structlog.get_logger()

STR_USER_NAME = "62406772"
STR_PASSWORD = "gundam01"
OTP_FILE_PATH = r"C:\Users\zonekun\Dropbox\アプリ\kabucom\matsui.txt"

INPUT_CSV = r"C:\Users\zonekun\Dropbox\stock\temp\yutai\yutai_april.csv"
OUTPUT_JSONL = r"C:\tmp\yutai_raw_june.jsonl"


def load_tickers() -> list[str]:
    """CSVからティッカーリストを読み込む。"""
    import csv
    tickers = []
    with open(INPUT_CSV, encoding="shift_jis") as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for row in reader:
            tickers.append(row[0])
    return tickers


def setup_driver() -> webdriver.Edge:
    options = Options()
    user_data_dir_path = "C:\\Users\\zonekun\\AppData\\Local\\Microsoft\\Edge\\User Data"
    options.add_argument(f"--user-data-dir={user_data_dir_path}")
    options.add_argument('--profile-directory=Default')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--remote-debugging-port=9782')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    return webdriver.Edge(options=options)


def login_matsui(driver: webdriver.Edge) -> None:
    driver.get('https://www.deal.matsui.co.jp/ITS/login/MemberLogin.jsp')
    wait = WebDriverWait(driver, 10)
    wait.until(EC.presence_of_element_located((By.ID, 'login-id'))).send_keys(STR_USER_NAME)
    driver.find_element(By.NAME, 'passwd').send_keys(STR_PASSWORD)
    if os.path.exists(OTP_FILE_PATH):
        os.remove(OTP_FILE_PATH)
    driver.find_element(By.CLASS_NAME, "login-form-submit").find_element(By.TAG_NAME, 'button').click()

    otp_code = None
    for i in range(120):
        if os.path.exists(OTP_FILE_PATH):
            with open(OTP_FILE_PATH, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if len(content) == 6 and content.isdigit():
                otp_code = content
                os.remove(OTP_FILE_PATH)
                break
        time.sleep(2)
    if not otp_code:
        sys.exit()

    wait.until(EC.presence_of_element_located((By.NAME, "authNo"))).send_keys(otp_code)
    wait.until(EC.element_to_be_clickable((By.XPATH, '//input[@alt="認証する"]'))).click()


def navigate_to_researchnet(driver: webdriver.Edge) -> str:
    """リサーチネットを起動しウィンドウハンドルを返す。"""
    wait = WebDriverWait(driver, 10)
    driver.switch_to.default_content()
    driver.switch_to.frame("GM")
    wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//a[contains(., '情報検索')]"))).click()

    driver.switch_to.default_content()
    driver.switch_to.frame("LM")
    wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//a[contains(., 'リサーチネット')]"))).click()

    driver.switch_to.default_content()
    driver.switch_to.frame("CT")
    wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//img[@alt='起動する']"))).click()

    time.sleep(3)
    rn_handle = driver.window_handles[-1]
    driver.switch_to.window(rn_handle)
    log.info("researchnet_opened", handle=rn_handle)
    return rn_handle


def search_ticker(driver: webdriver.Edge, wait: WebDriverWait, ticker: str) -> None:
    """リサーチネットで銘柄を検索する。"""
    driver.switch_to.default_content()
    frames = driver.find_elements(By.TAG_NAME, "frame")
    if frames:
        driver.switch_to.frame("qrn_frame_menu")

    search_box = wait.until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "#text input[type='text']")))
    search_box.clear()
    search_box.send_keys(ticker)
    driver.find_element(By.CSS_SELECTOR, "input[alt='検索']").click()
    time.sleep(2)


def extract_yutai_data(driver: webdriver.Edge, ticker: str) -> dict | None:
    """優待ページから権利確定・優待テキストを抽出する。"""
    tables = driver.find_elements(By.TAG_NAME, "table")
    for tbl in tables:
        txt = tbl.text.strip()
        if "［権利確定］" in txt and "［株主優待］" in txt:
            kenri_match = re.search(r"［権利確定］\s*(.+?)(?:\n|［)", txt)
            kenri = kenri_match.group(1).strip() if kenri_match else ""

            yutai_match = re.search(r"［株主優待］\s*([\s\S]+)", txt)
            yutai_text = yutai_match.group(1).strip() if yutai_match else ""

            return {
                "ticker": ticker,
                "kenri_kakutei": kenri,
                "yutai_text": yutai_text,
            }
    return None


def process_ticker(
    driver: webdriver.Edge,
    wait: WebDriverWait,
    ticker: str,
    rn_handle: str,
) -> dict | None:
    """1銘柄の株主優待情報を取得する。"""
    try:
        driver.switch_to.window(rn_handle)
        search_ticker(driver, wait, ticker)

        driver.switch_to.default_content()
        driver.switch_to.frame("qrn_frame_main")
        time.sleep(1)

        yutai_href = None
        links = driver.find_elements(By.TAG_NAME, "a")
        for link in links:
            href = link.get_attribute("href") or ""
            if "report_yuutai.asp" in href and f"rcode={ticker}" in href:
                yutai_href = href
                break

        if not yutai_href:
            log.info("no_yutai", ticker=ticker)
            return None

        driver.execute_script(f"window.location.href='{yutai_href}'")
        time.sleep(3)

        driver.switch_to.default_content()
        frames = driver.find_elements(By.TAG_NAME, "frame")
        if frames:
            driver.switch_to.frame("qrn_frame_main")

        return extract_yutai_data(driver, ticker)

    except Exception as e:
        log.warning("process_error", ticker=ticker, error=str(e))
        return None


def load_done_tickers() -> set[str]:
    """JSONL既存分のティッカーを返す（再開用）。"""
    done = set()
    if os.path.exists(OUTPUT_JSONL):
        with open(OUTPUT_JSONL, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                done.add(r["ticker"])
    return done


def main() -> None:
    """CSVのティッカーを走査し、株主優待情報をJSONLに逐次保存する。"""
    tickers = load_tickers()
    done = load_done_tickers()
    remaining = [t for t in tickers if t not in done]
    log.info("start", total=len(tickers), done=len(done), remaining=len(remaining))

    if not remaining:
        log.info("all_done")
        return

    driver = setup_driver()
    extracted = 0
    no_yutai = 0

    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })

        login_matsui(driver)
        time.sleep(5)
        rn_handle = navigate_to_researchnet(driver)
        wait = WebDriverWait(driver, 10)

        for i, ticker in enumerate(remaining):
            log.info("processing", ticker=ticker,
                     progress=f"{i+1}/{len(remaining)}")
            data = process_ticker(driver, wait, ticker, rn_handle)
            if data:
                with open(OUTPUT_JSONL, "a", encoding="utf-8") as f:
                    f.write(json.dumps(data, ensure_ascii=False) + "\n")
                extracted += 1
                log.info("extracted", ticker=ticker,
                         kenri=data["kenri_kakutei"],
                         text_len=len(data["yutai_text"]))
            else:
                no_yutai += 1
            time.sleep(1)

    except Exception as e:
        log.error("fatal", error=str(e))
    finally:
        driver.quit()

    log.info("summary", extracted=extracted, no_yutai=no_yutai,
             total_in_jsonl=len(done) + extracted)


if __name__ == "__main__":
    main()

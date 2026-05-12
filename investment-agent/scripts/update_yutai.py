"""株主優待スクレイパー v2（松井証券リサーチネット経由）

全上場銘柄を走査し、株主優待情報を中間JSONLに逐次保存する。
構造化加工・Excel生成は別ステップ（Claude Code直接 or Codex委任）。

【再開機能】
    中断時はJSONLに処理済み銘柄が記録される。
    次回起動時に自動的に続きから再開。

【前提】
    - Edge + EdgeDriver インストール済み
    - 既存Edgeプロファイル（C:\\Users\\zonekun\\AppData\\Local\\Microsoft\\Edge\\User Data）
    - 松井証券口座（SMS OTP認証）
    - OTPファイル自動連携: C:\\Users\\zonekun\\Dropbox\\アプリ\\kabucom\\matsui.txt

Usage:
    PYTHONUTF8=1 python scripts/update_yutai.py
    PYTHONUTF8=1 python scripts/update_yutai.py --tickers 2498 9441 2970
    PYTHONUTF8=1 python scripts/update_yutai.py --output C:\\Users\\zonekun\\Dropbox\\stock\\優待
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import structlog
from google.cloud import bigquery
from google.oauth2 import service_account
from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

JST = timezone(timedelta(hours=+9), "JST")
log = structlog.get_logger()

# ==========================================
# パス・定数設定
# ==========================================
_BASE = os.path.join(os.path.dirname(__file__), "..")
KEY_FILE = os.path.join(_BASE, "keys", "gcp-service-account.json")
DEFAULT_OUTPUT_DIR = r"C:\Users\zonekun\Dropbox\stock\優待"
JSONL_DIR = os.path.join(_BASE, "data", "logs")
REQUEST_INTERVAL_SEC = 1.0

# ==========================================
# アカウント情報（OrderForMABatch.py からコピー・変更禁止）
# ==========================================
STR_USER_NAME = "62406772"
STR_PASSWORD = "gundam01"
OTP_FILE_PATH = r"C:\Users\zonekun\Dropbox\アプリ\kabucom\matsui.txt"

# 会社名パース用
NAME_PATTERN = re.compile(r"(.+?)（(\d{3,5}[A-Z]?)）のメニュー一覧")


# ==========================================
# OrderForMABatch.py からコピー（変更なし）
# ==========================================


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


# ==========================================
# リサーチネット遷移（test_yutai_nav.py からコピー）
# ==========================================


def navigate_to_researchnet(driver: webdriver.Edge) -> str:
    """ログイン後、リサーチネットを起動しウィンドウハンドルを返す。"""
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


# ==========================================
# 銘柄リスト取得（update_conse_quick.py と同方式）
# ==========================================


def get_bq_client() -> bigquery.Client:
    """BigQuery クライアントを構築する。"""
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return bigquery.Client(project="gmailpj-357912", credentials=creds)


def get_japanese_stock_tickers(client: bigquery.Client) -> list[str]:
    """BQ STOCK_CODE_LIST からプライム・スタンダード・グロース（内国株式）を取得する。"""
    log.info("fetching_tickers", source="BQ")
    query = """
        SELECT TICKER
        FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
        WHERE EXCHANGE = 'TSE'
          AND MARKET_CATEGORY IN (
            'プライム（内国株式）',
            'スタンダード（内国株式）',
            'グロース（内国株式）'
          )
        ORDER BY TICKER
    """
    rows = list(client.query(query).result())
    tickers = [row["TICKER"] for row in rows]
    log.info("tickers_fetched", count=len(tickers))
    return tickers


# ==========================================
# データ抽出
# ==========================================


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


def extract_company_name(driver: webdriver.Edge) -> str:
    """優待ページ末尾の「会社名（TICKER）のメニュー一覧」から会社名をパースする。"""
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
        m = NAME_PATTERN.search(body_text)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return ""


def extract_yutai_data(driver: webdriver.Edge, ticker: str) -> dict | None:
    """優待ページから権利確定・優待テキスト・会社名を抽出する。"""
    tables = driver.find_elements(By.TAG_NAME, "table")
    for tbl in tables:
        txt = tbl.text.strip()
        if "［権利確定］" in txt and "［株主優待］" in txt:
            kenri_match = re.search(r"［権利確定］\s*(.+?)(?:\n|［)", txt)
            kenri = kenri_match.group(1).strip() if kenri_match else ""

            yutai_match = re.search(r"［株主優待］\s*([\s\S]+)", txt)
            yutai_text = yutai_match.group(1).strip() if yutai_match else ""

            name = extract_company_name(driver)

            return {
                "ticker": ticker,
                "name": name,
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
    """1銘柄の株主優待情報を取得する。優待なしは None を返す。"""
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
        return None

    driver.execute_script(f"window.location.href='{yutai_href}'")
    time.sleep(3)

    driver.switch_to.default_content()
    frames = driver.find_elements(By.TAG_NAME, "frame")
    if frames:
        driver.switch_to.frame("qrn_frame_main")

    return extract_yutai_data(driver, ticker)


# ==========================================
# JSONL 再開管理
# ==========================================


def get_jsonl_path() -> str:
    """中間JSONLのパスを返す。年月付き。"""
    ym = datetime.now(JST).strftime("%Y%m")
    return os.path.join(JSONL_DIR, f"yutai_raw_{ym}.jsonl")


def load_done_tickers(jsonl_path: str) -> set[str]:
    """JSONL既存分のティッカーを返す（再開用）。"""
    done: set[str] = set()
    if os.path.exists(jsonl_path):
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    done.add(r["ticker"])
    return done


def append_jsonl(jsonl_path: str, record: dict) -> None:
    """1レコードをJSONLに追記する。"""
    os.makedirs(os.path.dirname(jsonl_path), exist_ok=True)
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ==========================================
# メイン
# ==========================================


def parse_args() -> argparse.Namespace:
    """CLI引数をパースする。"""
    parser = argparse.ArgumentParser(description="株主優待スクレイパー v2")
    parser.add_argument(
        "--tickers", nargs="*", default=None,
        help="テスト用: 指定銘柄のみ実行（例: 2498 9441 2970）",
    )
    parser.add_argument(
        "--output", default=DEFAULT_OUTPUT_DIR,
        help=f"Excel出力先ディレクトリ（デフォルト: {DEFAULT_OUTPUT_DIR}）",
    )
    return parser.parse_args()


def main() -> None:
    """全銘柄を走査し、株主優待情報をJSONLに逐次保存する。"""
    args = parse_args()
    jsonl_path = get_jsonl_path()

    if args.tickers:
        codes = [t.zfill(4) for t in args.tickers]
    else:
        bq_client = get_bq_client()
        codes = get_japanese_stock_tickers(bq_client)

    if not codes:
        log.error("no_tickers")
        sys.exit(1)

    done = load_done_tickers(jsonl_path)
    remaining = [t for t in codes if t not in done]
    log.info("start", total=len(codes), done=len(done),
             remaining=len(remaining), jsonl=jsonl_path)

    if not remaining:
        log.info("all_done")
        return

    driver = setup_driver()
    extracted = 0
    no_yutai = 0
    errors = 0

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
            try:
                data = process_ticker(driver, wait, ticker, rn_handle)
                if data:
                    append_jsonl(jsonl_path, data)
                    extracted += 1
                    log.info("extracted", ticker=ticker,
                             name=data["name"],
                             kenri=data["kenri_kakutei"],
                             text_len=len(data["yutai_text"]))
                else:
                    no_yutai += 1
                    log.info("no_yutai", ticker=ticker)
            except Exception as e:
                errors += 1
                log.warning("process_error", ticker=ticker, error=str(e))

            time.sleep(REQUEST_INTERVAL_SEC)

    except Exception as e:
        log.error("fatal", error=str(e))
    finally:
        driver.quit()

    log.info("summary", extracted=extracted, no_yutai=no_yutai,
             errors=errors, total_in_jsonl=len(done) + extracted)

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()

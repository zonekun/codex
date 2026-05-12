"""QUICK コンセンサス取得（松井証券リサーチネット経由） → BQ INSERT

【再開機能】
    中断時は data/logs/conse_quick_resume.json に状態が保存される。
    次回起動時に自動的に続きから再開（DATAATは最初の実行日で固定）。
    強制新規実行するには --fresh フラグを指定する。

【前提】
    - Edge + EdgeDriver インストール済み
    - 既存Edgeプロファイル（C:\\Users\\zonekun\\AppData\\Local\\Microsoft\\Edge\\User Data）
    - 松井証券口座（SMS OTP認証）
    - OTPファイル自動連携: C:\\Users\\zonekun\\Dropbox\\アプリ\\kabucom\\matsui.txt

Usage:
    PYTHONUTF8=1 python scripts/update_conse_quick.py
    PYTHONUTF8=1 python scripts/update_conse_quick.py --fresh
    PYTHONUTF8=1 python scripts/update_conse_quick.py --ticker 7203,9984
    PYTHONUTF8=1 python scripts/update_conse_quick.py --dry-run
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import structlog
from google.cloud import bigquery
from google.oauth2 import service_account

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts.lib_conse_csv_from_view import export_consensus_csv  # noqa: E402

from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# ==========================================
# パス・定数設定
# ==========================================
_BASE = os.path.join(os.path.dirname(__file__), "..")
KEY_FILE = os.path.join(_BASE, "keys", "gcp-service-account.json")
STATE_FILE = os.path.join(_BASE, "data", "logs", "conse_quick_resume.json")
BQ_TABLE = "gmailpj-357912.STOCK.CONSENSUS"
CSV_PATH = r"C:\Users\zonekun\Dropbox\stock\py\conse_quick.csv"
SOURCE = "QUICK"
REQUEST_INTERVAL_SEC = 1.0

# ==========================================
# アカウント情報（OrderForMABatch.py からコピー）
# ==========================================
STR_USER_NAME = "62406772"
STR_PASSWORD = "gundam01"
OTP_FILE_PATH = r"C:\Users\zonekun\Dropbox\アプリ\kabucom\matsui.txt"

log = structlog.get_logger()

# ==========================================
# [CON] データ抽出用正規表現（test_conse_quick.py で動作確認済み）
# ==========================================
CON_PATTERN = re.compile(
    r'<td[^>]*>(\d{4}/\d{2})\([^)]*\)<[^>]*>[^<]*<[^>]*>\[CON\]</span></font></td>\s*'
    r'(?:<script><!--\s*)?'
    r'tableTdI\("([^"]*)"\);\s*'
    r'(?:-->.*?)?'
    r'tableTdI\("([^"]*)"\);\s*'
    r'(?:-->.*?)?'
    r'tableTdI\("([^"]*)"\);\s*'
    r'(?:-->.*?)?'
    r'tableTdI\("([^"]*)"\);\s*'
    r'(?:-->.*?)?'
    r'tableTdI\("([^"]*)"\);',
    re.DOTALL,
)



# ==========================================
# OrderForMABatch.py からコピー（変更なし）
# ==========================================


def setup_driver():
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


def login_matsui(driver):
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
    if not otp_code: sys.exit()

    wait.until(EC.presence_of_element_located((By.NAME, "authNo"))).send_keys(otp_code)
    wait.until(EC.element_to_be_clickable((By.XPATH, '//input[@alt="認証する"]'))).click()


# ==========================================
# 再開用状態管理
# ==========================================


def load_state() -> dict[str, str] | None:
    """再開用状態ファイルを読み込む。"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(dataat: str, last_ticker: str) -> None:
    """再開用状態ファイルを保存する。"""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"dataat": dataat, "last_ticker": last_ticker}, f)


def clear_state() -> None:
    """再開用状態ファイルを削除する。"""
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)


# ==========================================
# BigQuery クライアント・銘柄リスト
# ==========================================


def get_bq_client() -> bigquery.Client:
    """BigQuery クライアントを構築する。"""
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return bigquery.Client(project="gmailpj-357912", credentials=creds)


def get_japanese_stock_tickers(client: bigquery.Client) -> list[str]:
    """BQ STOCK_CODE_LIST からプライム・スタンダード・グロース（内国株式）を取得する。"""
    log.info("fetching_tickers", source="BQ")
    try:
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
        log.info("tickers_fetched", count=len(tickers), source="BQ")
        return tickers
    except Exception as e:
        log.warning("bq_ticker_fetch_failed", error=str(e))

    log.info("fetching_tickers", source="JPX_Excel")
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        df = pd.read_excel(io.BytesIO(resp.content), dtype=str)
        target_categories = {
            "プライム（内国株式）",
            "スタンダード（内国株式）",
            "グロース（内国株式）",
        }
        df = df[df["市場・商品区分"].isin(target_categories)]
        tickers = sorted(df["コード"].str.zfill(4).tolist())
        log.info("tickers_fetched", count=len(tickers), source="JPX_Excel")
        return tickers
    except Exception as e2:
        log.error("ticker_fetch_failed", error=str(e2))
        return []




# ==========================================
# BigQuery 書き込み
# ==========================================


def _to_int(val: str) -> int | None:
    """文字列を int に変換。空文字は None。"""
    if not val:
        return None
    return int(val)


def _to_float(val: str) -> float | None:
    """文字列を float に変換。空文字は None。"""
    if not val:
        return None
    return float(val)


def build_bq_rows(records: list[dict[str, str]]) -> list[dict]:
    """CSV用レコードを BQ INSERT 用に変換する。"""
    bq_rows = []
    for r in records:
        bq_rows.append({
            "DATAAT": r["DATAAT"],
            "TICKER": r["TICKER"],
            "FY": r["FY"],
            "QUARTER": "FY",
            "REVENUE": _to_int(r["REVENUE"]),
            "OP_PROFIT": _to_int(r["OP_PROFIT"]),
            "ORD_PROFIT": _to_int(r["ORD_PROFIT"]),
            "NET_PROFIT": _to_int(r["NET_PROFIT"]),
            "EPS": _to_float(r["EPS"]),
            "SOURCE": SOURCE,
        })
    return bq_rows


def insert_bq(client: bigquery.Client, records: list[dict[str, str]]) -> None:
    """BQ CONSENSUS テーブルに streaming insert する。"""
    if not records:
        return
    bq_rows = build_bq_rows(records)
    errors = client.insert_rows_json(BQ_TABLE, bq_rows)
    if errors:
        log.warning("bq_insert_errors", errors=errors[:3])


# ==========================================
# リサーチネット遷移
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
# データ抽出
# ==========================================


def clean_value(raw: str) -> str:
    """カンマ除去。'-' は空文字を返す。"""
    raw = raw.strip()
    if raw in ("-", ""):
        return ""
    return raw.replace(",", "")


def extract_con_data(html: str, ticker: str, dataat: str) -> list[dict[str, str]]:
    """印刷フォーマットHTMLから[CON]データを抽出する。"""
    records: list[dict[str, str]] = []
    for m in CON_PATTERN.finditer(html):
        revenue_raw = m.group(2)
        if "." in revenue_raw or revenue_raw == "-":
            continue
        fy = m.group(1).replace("/", "")
        records.append({
            "TICKER": ticker,
            "FY": fy,
            "REVENUE": clean_value(m.group(2)),
            "OP_PROFIT": clean_value(m.group(3)),
            "ORD_PROFIT": clean_value(m.group(4)),
            "NET_PROFIT": clean_value(m.group(5)),
            "EPS": clean_value(m.group(6)),
            "DATAAT": dataat,
        })
    return records


def search_ticker(driver: webdriver.Edge, wait: WebDriverWait, ticker: str) -> None:
    """リサーチネットで銘柄を検索する。"""
    driver.switch_to.default_content()
    # rn_home.asp（フレームなし）と frameset（検索後）の両方に対応
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


def process_ticker(
    driver: webdriver.Edge,
    wait: WebDriverWait,
    ticker: str,
    rn_handle: str,
    dataat: str,
) -> list[dict[str, str]]:
    """1銘柄のQUICKコンセンサスを取得する。"""
    handles_before = set(driver.window_handles)

    try:
        driver.switch_to.window(rn_handle)
        search_ticker(driver, wait, ticker)

        # 銘柄リンクをクリック（qrn_frame_main 内）
        driver.switch_to.default_content()
        driver.switch_to.frame("qrn_frame_main")
        try:
            link = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH,
                    f"//a[contains(@href, 'report_summary') and contains(@href, 'rcode={ticker}')]")))
        except TimeoutException:
            return []
        link.click()
        time.sleep(2)

        # 決算・財務
        driver.switch_to.default_content()
        driver.switch_to.frame("qrn_frame_main")
        wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//a[contains(., '決算・財務')]"))).click()
        time.sleep(2)

        # 印刷フォーマット表示（別ウィンドウ）
        driver.switch_to.default_content()
        driver.switch_to.frame("qrn_frame_main")
        try:
            btn = driver.find_element(By.XPATH, "//input[@value='印刷フォーマット表示']")
        except Exception:
            try:
                btn = driver.find_element(By.XPATH, "//img[@alt='印刷フォーマット表示']/parent::a")
            except Exception:
                btn = driver.find_element(By.XPATH, "//*[contains(., '印刷フォーマット表示')]")
        btn.click()
        time.sleep(2)

        # 印刷ウィンドウに切り替え
        new_windows = set(driver.window_handles) - handles_before
        if new_windows:
            driver.switch_to.window(new_windows.pop())

        # [CON] データ抽出
        html = driver.page_source
        return extract_con_data(html, ticker, dataat)

    except Exception as e:
        log.warning("process_error", ticker=ticker, error=str(e))
        return []

    finally:
        # 印刷ウィンドウを閉じてリサーチネットに戻る
        for h in set(driver.window_handles) - handles_before:
            try:
                driver.switch_to.window(h)
                driver.close()
            except Exception:
                pass
        driver.switch_to.window(rn_handle)


# ==========================================
# メイン処理ループ
# ==========================================


def process_codes(
    driver: webdriver.Edge,
    codes: list[str],
    dataat: str,
    rn_handle: str,
    resume_from: str | None,
    bq_client: bigquery.Client,
    *,
    dry_run: bool = False,
    update_state: bool = True,
) -> Counter:
    """全銘柄を処理する。"""
    skip_until_resume = resume_from is not None
    stats: Counter = Counter()
    wait = WebDriverWait(driver, 10)
    total = len(codes)

    for code in codes:
        code = code.strip()
        if not code:
            continue
        if skip_until_resume:
            if code == resume_from:
                skip_until_resume = False
            continue

        stats["processed"] += 1
        log.info("processing", ticker=code, progress=f"{stats['processed']}/{total}")

        records = process_ticker(driver, wait, code, rn_handle, dataat)

        if not records:
            log.info("skip", ticker=code, reason="no_con_data")
            stats["no_data"] += 1
        elif dry_run:
            for r in records:
                log.info("dry_run", **r)
            stats["dry_run_ok"] += 1
        else:
            insert_bq(bq_client, records)
            log.info("saved", ticker=code, records=len(records),
                     fys=[r["FY"] for r in records])
            stats["saved"] += 1

        if update_state:
            save_state(dataat, code)
        time.sleep(REQUEST_INTERVAL_SEC)

    return stats


def parse_tickers_arg(value: str | None) -> list[str] | None:
    """--ticker 引数をパースする。"""
    if not value:
        return None
    return [x.strip().zfill(4) for x in re.split(r"[,;\s]+", value) if x.strip()]


def run(*, fresh: bool = False, ticker_arg: str | None = None, dry_run: bool = False) -> None:
    """メインエントリポイント。"""
    explicit_tickers = parse_tickers_arg(ticker_arg)
    state = None if fresh or explicit_tickers else load_state()
    dataat = state["dataat"] if state else datetime.now(tz=ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")
    resume_from = state["last_ticker"] if state else None

    bq_client = get_bq_client()

    if explicit_tickers:
        codes = explicit_tickers
        update_state = False
    else:
        codes = get_japanese_stock_tickers(bq_client)
        update_state = True

    if not codes:
        log.error("no_tickers")
        sys.exit(1)

    log.info("start", dataat=dataat, source=SOURCE, dry_run=dry_run,
             total=len(codes), resume_from=resume_from)

    driver = setup_driver()
    try:
        # bot検知対策: navigator.webdriver フラグ隠蔽
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })

        login_matsui(driver)
        time.sleep(5)
        rn_handle = navigate_to_researchnet(driver)

        stats = process_codes(
            driver, codes, dataat, rn_handle, resume_from,
            bq_client,
            dry_run=dry_run, update_state=update_state,
        )

        log.info("summary", **dict(stats.most_common()))

        if update_state:
            clear_state()

        if not dry_run:
            export_consensus_csv(bq_client, CSV_PATH)

    except Exception as e:
        log.error("fatal", error=str(e))
        sys.exit(1)
    finally:
        driver.quit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="QUICK コンセンサス取得（松井証券リサーチネット経由）")
    parser.add_argument("--fresh", action="store_true",
                        help="再開状態を破棄して新規実行")
    parser.add_argument("--ticker",
                        help="カンマ区切りの銘柄コード。指定時は再開状態を使わない")
    parser.add_argument("--dry-run", action="store_true",
                        help="CSV保存せず抽出結果だけ表示")
    args = parser.parse_args()
    run(fresh=args.fresh, ticker_arg=args.ticker, dry_run=args.dry_run)

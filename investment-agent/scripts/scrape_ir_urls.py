"""type c 企業の IR ページ URL をスクレイピングするスクリプト。

処理フロー:
  1. GCS monthly_disclosure_master.csv から type c 対象企業を読み込む
  2. 各企業について:
     a. GCS monthlydata/{ticker}/ir_url.json が存在すればスキップ（再開可能）
     b. 日経会社概要ページ https://www.nikkei.com/nkd/company/gaiyo/?scode={ticker}&ba=1 をスクレイプ
     c. 会社 HP URL を抽出
     d. 失敗した場合は DuckDuckGo Web 検索でフォールバック
     e. GCS monthlydata/{ticker}/ir_url.json に保存

出力 GCS パス: monthlydata/{ticker}/ir_url.json
  {
    "ticker": "...",
    "company_name": "...",
    "company_hp_url": "...",
    "source": "nikkei" | "web_search" | "not_found",
    "scraped_at": "2026-..."
  }
"""
import csv
import io
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup
from google.cloud import storage
from google.oauth2 import service_account

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ---- 実行環境判別 ----
IS_CLOUD_RUN = bool(os.environ.get("CLOUD_RUN_JOB"))

# ---- 設定 ----
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GCS_BUCKET = "stock_data_1930932"
GCS_MASTER_PATH = "config/monthly_disclosure_master.csv"
KEY_FILE = os.path.join(BASE_DIR, "keys", "gcp-service-account.json")
JST = timezone(timedelta(hours=9))

NIKKEI_URL = "https://www.nikkei.com/nkd/company/gaiyo/?scode={scode}&ba=1"
NIKKEI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9",
}

# nikkei.com の外部リンクとして除外するドメイン
_EXCLUDE_DOMAINS = {
    "nikkei.com", "nikkei.co.jp", "nikkei-cnbc.co.jp", "nikkei.co",
    "google.co.jp", "google.com", "moneyworld.jp",
    "twitter.com", "x.com", "ft.com", "maps.google",
}
# ドメインに含まれる文字列でも除外（部分一致）
_EXCLUDE_KEYWORDS = ["nikkei", "utm_source"]

RATE_LIMIT_SEC = 1.0  # 日経へのリクエスト間隔（秒）


# ---- GCP 認証 ----
def _get_credentials():
    if IS_CLOUD_RUN:
        import google.auth
        creds, _ = google.auth.default()
        return creds
    return service_account.Credentials.from_service_account_file(KEY_FILE)


# ---- マスタ読み込み ----
def load_target_companies(bucket) -> list[dict]:
    """type c かつ処理不要でない企業一覧を返す。"""
    blob = bucket.blob(GCS_MASTER_PATH)
    text = blob.download_as_text(encoding="utf-8")
    companies = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        if row["DISCLOSURE_TYPE"].strip() != "c":
            continue
        if "処理不要" in row.get("NOTES", ""):
            continue
        companies.append({
            "ticker": row["TICKER"].strip(),
            "company_name": row["COMPANY_NAME"].strip(),
        })
    logger.info("対象企業: %d社", len(companies))
    return companies


# ---- 日経スクレイピング ----
def _is_company_url(href: str) -> bool:
    """外部リンクのうち会社 HP と判断できるものを True にする。"""
    if not href.startswith("http"):
        return False
    if any(d in href for d in _EXCLUDE_DOMAINS):
        return False
    if any(kw in href for kw in _EXCLUDE_KEYWORDS):
        return False
    return True


def scrape_nikkei(ticker: str, session: requests.Session) -> str:
    """日経会社概要ページから会社 HP URL を取得する。失敗時は空文字列。

    日経の HTML 構造:
      <tr>
        <th class="a-w20p">URL</th>
        <td class="a-w80p"><a href="http://...">http://...</a></td>
      </tr>
    """
    url = NIKKEI_URL.format(scode=ticker)
    try:
        resp = session.get(url, headers=NIKKEI_HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning("%s: 日経 HTTP %d", ticker, resp.status_code)
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        # <th> テキストが "URL" の行の隣 <td> を探す
        th = soup.find("th", string=re.compile(r"^\s*URL\s*$"))
        if th:
            td = th.find_next_sibling("td")
            if td:
                a = td.find("a", href=True)
                if a:
                    return a["href"]
    except Exception as e:
        logger.warning("%s: 日経スクレイプ失敗 %s", ticker, e)
    return ""


# ---- Web 検索フォールバック（DuckDuckGo） ----
def search_web(company_name: str, session: requests.Session) -> str:
    """DuckDuckGo HTML 検索で会社の IR ページ URL を探す。"""
    query = company_name + " IR 月次開示"
    try:
        resp = session.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={**NIKKEI_HEADERS, "Accept": "text/html"},
            timeout=15,
        )
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        # 検索結果の最初の外部リンクを返す
        for a in soup.find_all("a", class_=re.compile("result__url|result__a")):
            href = a.get("href", "")
            # DuckDuckGo のリダイレクト URL をデコード
            m = re.search(r"uddg=(https?[^&]+)", href)
            if m:
                import urllib.parse
                return urllib.parse.unquote(m.group(1))
            if href.startswith("http") and "duckduckgo.com" not in href:
                return href
    except Exception as e:
        logger.warning("%s: Web 検索失敗 %s", company_name, e)
    return ""


# ---- GCS 保存 ----
def save_ir_url(ticker: str, data: dict, bucket) -> None:
    blob = bucket.blob(f"monthlydata/{ticker}/ir_url.json")
    blob.upload_from_string(
        json.dumps(data, ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8",
    )


def already_done(ticker: str, bucket) -> bool:
    return bucket.blob(f"monthlydata/{ticker}/ir_url.json").exists()


# ---- メイン ----
def main() -> None:
    logger.info("実行環境: %s", "Cloud Run" if IS_CLOUD_RUN else "ローカル")

    creds = _get_credentials()
    gcs = storage.Client(project="gmailpj-357912", credentials=creds)
    bucket = gcs.bucket(GCS_BUCKET)

    companies = load_target_companies(bucket)

    # スキップ済みを除外
    todo = [c for c in companies if not already_done(c["ticker"], bucket)]
    logger.info("未処理: %d社（スキップ済み: %d社）", len(todo), len(companies) - len(todo))

    session = requests.Session()

    done = skipped = nikkei_ok = web_ok = not_found = 0

    for i, c in enumerate(todo):
        ticker = c["ticker"]
        name = c["company_name"]

        # 日経スクレイプ
        hp_url = scrape_nikkei(ticker, session)
        source = "nikkei" if hp_url else ""

        if not hp_url:
            # Web 検索フォールバック
            time.sleep(RATE_LIMIT_SEC)
            hp_url = search_web(name, session)
            source = "web_search" if hp_url else "not_found"

        data = {
            "ticker": ticker,
            "company_name": name,
            "company_hp_url": hp_url,
            "source": source,
            "scraped_at": datetime.now(JST).isoformat(),
        }
        save_ir_url(ticker, data, bucket)

        if source == "nikkei":
            nikkei_ok += 1
        elif source == "web_search":
            web_ok += 1
        else:
            not_found += 1
        done += 1

        if done % 20 == 0:
            logger.info(
                "進捗: %d/%d  日経OK=%d Web検索OK=%d 未取得=%d",
                done, len(todo), nikkei_ok, web_ok, not_found,
            )
        time.sleep(RATE_LIMIT_SEC)

    logger.info("=== 完了 ===")
    logger.info("  処理: %d社 / スキップ: %d社", done, skipped)
    logger.info("  日経取得: %d社 / Web検索: %d社 / 未取得: %d社",
                nikkei_ok, web_ok, not_found)


if __name__ == "__main__":
    main()

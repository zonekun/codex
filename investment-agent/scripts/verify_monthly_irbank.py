"""irbank.net の月次開示銘柄一覧を取得し、BQ TDNET_DOCUMENTS_ENHANCED と突合する.

Usage:
    PYTHONUTF8=1 uv run python scripts/verify_monthly_irbank.py
"""
import json
import os
import re
import time
import warnings

import requests
from bs4 import BeautifulSoup
from google.cloud import bigquery
from google.oauth2 import service_account

warnings.filterwarnings("ignore")  # InsecureRequestWarning を抑制

BASE_URL = "https://irbank.net"
START_URL = f"{BASE_URL}/td/%E6%9C%88%E6%AC%A1%E3%83%BB%E9%80%9F%E5%A0%B1"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
CACHE_PATH = "data/cache/irbank_monthly.json"
KEY_PATH = "keys/gcp-service-account.json"
PROJECT = "gmailpj-357912"
TARGET_YEAR_PREFIX = "2026"  # この年以降の開示のみ収集


def scrape_irbank() -> set[str]:
    """irbank.net から月次開示ティッカーを取得（URLの /NNNN/ パターンから抽出）."""
    tickers: set[str] = set()
    stop = False
    page = 0
    next_url = START_URL

    while not stop and page < 60:
        page += 1
        resp = requests.get(next_url, headers=HEADERS, verify=False, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        current_date = ""
        for row in soup.select("table tbody tr"):
            # 日付ヘッダ行 (<td class="lf" colspan="4">2026年3月11日</td>)
            date_cell = row.select_one("td.lf[colspan]")
            if date_cell:
                current_date = date_cell.get_text(strip=True)
                # TARGET_YEAR 以前の日付になったら収集停止
                if current_date and not current_date.startswith(TARGET_YEAR_PREFIX):
                    stop = True
                    break
                continue

            if stop:
                break

            # TARGET_YEAR の行からティッカーを取得（/NNNN/ 形式のリンク）
            if current_date.startswith(TARGET_YEAR_PREFIX):
                for a in row.find_all("a", href=re.compile(r"^/\d{4}/")):
                    m = re.match(r"^/(\d{4})/", a["href"])
                    if m:
                        tickers.add(m.group(1))

        print(f"  ページ{page}: 最終日付={current_date}, 累計={len(tickers)}件")

        if not stop:
            # ページネーション: ?y=タイムスタンプ 付きリンク
            more = soup.find("a", href=re.compile(r"\?y=\d+"))
            if more:
                href = more["href"]
                next_url = href if href.startswith("http") else f"{BASE_URL}{href}"
                time.sleep(1.5)
            else:
                break

    return tickers


def query_bq() -> tuple[set[str], dict[str, str]]:
    """BQ から 2026年以降の月次開示ティッカーを取得.

    MAIN_CATEGORY='月次開示' のほか、DOC_TITLE に '月次' を含む文書も対象とする
    （ETL でカテゴリが '業績予想' 等に誤分類されるケースに対応）。
    """
    creds = service_account.Credentials.from_service_account_file(KEY_PATH)
    client = bigquery.Client(project=PROJECT, credentials=creds)
    sql = f"""
    SELECT DISTINCT TICKER, ANY_VALUE(FILER_NAME) AS NAME
    FROM `{PROJECT}.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE SUBMISSION_DATE >= '{TARGET_YEAR_PREFIX}-01-01'
      AND (
        MAIN_CATEGORY = '月次開示'
        OR EXISTS(SELECT 1 FROM UNNEST(SUB_CATEGORIES) s WHERE s = '月次')
        OR REGEXP_CONTAINS(DOC_TITLE, r'月次|月度売上|売上速報|売上推移速報|月度業績|受注速報')
      )
    GROUP BY TICKER
    ORDER BY TICKER
    """
    rows = list(client.query(sql).result())
    tickers = {r.TICKER for r in rows}
    names = {r.TICKER: r.NAME for r in rows}
    return tickers, names


if __name__ == "__main__":
    print(f"=== irbank.net スクレイピング（{TARGET_YEAR_PREFIX}年以降） ===")
    irbank_tickers = scrape_irbank()
    print(f"irbank.net 月次開示銘柄数: {len(irbank_tickers)}")

    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump({"tickers": sorted(irbank_tickers), "count": len(irbank_tickers)}, f, ensure_ascii=False, indent=2)
    print(f"キャッシュ保存: {CACHE_PATH}")

    print(f"\n=== BigQuery クエリ（{TARGET_YEAR_PREFIX}年以降） ===")
    bq_tickers, bq_names = query_bq()
    print(f"BQ 月次開示銘柄数: {len(bq_tickers)}")

    only_irbank = sorted(irbank_tickers - bq_tickers)
    only_bq = sorted(bq_tickers - irbank_tickers)
    both = irbank_tickers & bq_tickers
    match_rate = len(both) / len(irbank_tickers) * 100 if irbank_tickers else 0

    print(f"\n=== 突合結果 ===")
    print(f"irbank: {len(irbank_tickers)}件 / BQ: {len(bq_tickers)}件")
    print(f"一致: {len(both)}件  一致率（irbank基準）: {match_rate:.1f}%")
    print(f"BQにない（irbank のみ）: {len(only_irbank)}件")
    print(f"irbankにない（BQ のみ）: {len(only_bq)}件")

    if only_irbank:
        print(f"\n【BQに漏れている銘柄】")
        for code in only_irbank:
            print(f"  {code}")

    if only_bq:
        print(f"\n【irbankにない BQ 独自銘柄（先頭30件）】")
        for code in only_bq[:30]:
            print(f"  {code}: {bq_names.get(code, '')}")

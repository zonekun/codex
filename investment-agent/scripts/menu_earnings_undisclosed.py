"""決算未発表会社一覧 — 当日15:00までの予定 vs TDNet実績を突合.

PSメニュー「決算未発表会社一覧」
BQ EARNINGS_DISCLOSURE_CALENDAR の予定(S)と TDNet開示一覧を突合し、
実行時刻までに開示されていない銘柄を表示する。
詳細: docs/knowledges/tools/100_earnings_undisclosed.md, docs/knowledges/tools/023_powershell_menu.md
"""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import httpx
from bs4 import BeautifulSoup
from google.cloud import bigquery
from google.oauth2 import service_account

from src.core.config import Settings

JST = timezone(timedelta(hours=+9), "JST")

TDNET_LIST_URL = "https://www.release.tdnet.info/inbs/I_list_{page:03d}_{date}.html"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _fetch_tdnet_tickers(date_str: str) -> set[str]:
    """TDNet開示一覧から当日の決算短信開示済みtickerを取得."""
    tickers: set[str] = set()
    for page in range(1, 100):
        url = TDNET_LIST_URL.format(page=page, date=date_str)
        resp = httpx.get(url, timeout=30, headers=HEADERS, follow_redirects=True)
        if resp.status_code == 404:
            break
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "lxml")

        table = None
        for t in soup.find_all("table"):
            if t.find(class_=lambda c: c and "kjTime" in c):
                table = t
                break
        if not table:
            break

        for tr in table.find_all("tr"):
            if len(tr.find_all("td")) < 4:
                continue
            title_cell = tr.find(class_=lambda c: c and "kjTitle" in c)
            code_cell = tr.find(class_=lambda c: c and "kjCode" in c)
            if not title_cell or not code_cell:
                continue
            if "決算短信" in title_cell.get_text(" ", strip=True):
                ticker = code_cell.get_text(strip=True).replace(" ", "")[:4]
                tickers.add(ticker)

        import time
        time.sleep(0.5)

    return tickers


def _fetch_scheduled(
    client: bigquery.Client, date_str: str, cutoff_time: str
) -> list[dict]:
    """BQから当日の決算発表予定を取得（時刻確定&cutoff以前のみ）."""
    sql = """
        SELECT
            s.TICKER,
            st.STOCK_NAME,
            FORMAT_TIME('%H:%M', s.DISCLOSURE_TIME) AS sched_time,
            s.QUARTER
        FROM `gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR` s
        LEFT JOIN `gmailpj-357912.STOCK.STOCK_CODE_LIST` st
            ON s.TICKER = st.TICKER
        WHERE s.RECORD_TYPE = 'S'
          AND s.CATEGORY = 'R'
          AND s.DISCLOSURE_DATE = @target_date
          AND (s.DISCLOSURE_TIME IS NULL OR s.DISCLOSURE_TIME <= @cutoff)
        ORDER BY s.DISCLOSURE_TIME, s.TICKER
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("target_date", "DATE", date_str),
            bigquery.ScalarQueryParameter("cutoff", "STRING", cutoff_time),
        ]
    )
    rows = client.query(sql, job_config=job_config).result()
    all_rows = [dict(row) for row in rows]
    return [r for r in all_rows if r["sched_time"] is not None]


def run() -> None:
    """メイン処理."""
    now = datetime.now(JST)
    date_str = now.strftime("%Y%m%d")
    date_iso = now.strftime("%Y-%m-%d")
    cutoff = now.strftime("%H:%M:%S")

    print(f"=== 決算未発表会社一覧（{date_iso} {now.strftime('%H:%M')}時点） ===")
    print()

    settings = Settings()
    creds = service_account.Credentials.from_service_account_file(
        settings.google_application_credentials
    )
    client = bigquery.Client(project=settings.gcp_project_id, credentials=creds)

    print("BQ: 決算発表予定を取得中...")
    scheduled = _fetch_scheduled(client, date_iso, cutoff)
    print(f"  予定: {len(scheduled)}件（{cutoff[:5]}まで）")

    print("TDNet: 開示済み決算短信を取得中...")
    disclosed = _fetch_tdnet_tickers(date_str)
    print(f"  開示済み: {len(disclosed)}件")
    print()

    undisclosed = [r for r in scheduled if r["TICKER"] not in disclosed]

    if not undisclosed:
        print("全社開示済みです。未発表はありません。")
        return

    print(f"{'TICKER':>6}  {'予定':>5}  {'四半期':<6}  会社名")
    print("-" * 60)
    for r in undisclosed:
        t = r["sched_time"]
        name = (r["STOCK_NAME"] or "")[:20]
        print(f"{r['TICKER']:>6}  {t:>5}  {r['QUARTER']:<6}  {name}")

    print()
    print(f"未発表: {len(undisclosed)}社 / 予定: {len(scheduled)}社")


if __name__ == "__main__":
    run()

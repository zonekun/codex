"""松井証券の公開買付ページから「上場廃止予定」銘柄を取得し、BQ DELISTED_STOCKS に UPSERT する。

JPX 上場廃止一覧（scrape_jpx_delisted.py）は廃止済み銘柄のみ掲載されるため、
TOB進行中（＝廃止予定）の銘柄は取得できない。
本スクリプトは松井証券の公開買付ページから廃止予定銘柄を先行取得し、
後日 scrape_jpx_delisted.py が同一キーで UPDATE する運用。

取得カラム:
  - TICKER, COMPANY_NAME, MARKET_SEGMENT（松井から取得）
  - TOB_PRICE（買付価格。対抗TOBがある場合は最高価格を採用）
  - IS_TOB_MBO = NULL（/classify-tob で判定）
  - DELISTING_DATE = NULL（廃止日未定）
  - DELISTING_REASON = NULL（JPX確定後に補完）

注意:
  - 対抗TOB: 同一TICKERで複数行出る場合、最高価格を採用（TOB合戦で価格上昇の可能性）
  - TOB失敗・取り下げ: 備考「上場廃止予定」が消えるだけなので本スクリプトでは無視

Usage:
    PYTHONUTF8=1 python scripts/scrape_matsui_delisted.py
    PYTHONUTF8=1 python scripts/scrape_matsui_delisted.py --dry-run
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests
import structlog
from bs4 import BeautifulSoup
from google.cloud import bigquery
from google.oauth2 import service_account

logger = structlog.get_logger()

KEY_FILE   = "keys/gcp-service-account.json"
PROJECT    = "gmailpj-357912"
TABLE_ID   = f"{PROJECT}.STOCK.DELISTED_STOCKS"
JST        = timezone(timedelta(hours=9))
MATSUI_URL = "https://ca.image.jp/matsui/?type=9"


def get_bq() -> bigquery.Client:
    """BigQuery クライアントを生成する。"""
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return bigquery.Client(project=PROJECT, credentials=creds)


def fetch_tob_page() -> str:
    """松井証券の公開買付ページ HTML を取得する。"""
    resp = requests.get(MATSUI_URL, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_tob_rows(html: str) -> list[dict]:
    """HTML をパースし、備考「上場廃止予定」の行を抽出する。"""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="commontbl")
    if not table:
        logger.warning("commontbl not found")
        return []

    # 対抗TOB（同一TICKERで複数行）がありうるため、一旦全行収集してから最高価格を採用
    raw: dict[str, dict] = {}
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue

        remarks = tds[7].get_text(strip=True)
        if "上場廃止予定" not in remarks:
            continue

        ticker_raw = tds[2].get_text(strip=True)
        ticker = _normalize_ticker(ticker_raw)
        if not ticker:
            continue

        company_name = _extract_company_name(tds[3])
        market = tds[4].get_text(strip=True)
        price = _parse_price(tds[5].get_text(strip=True))
        tob_start = tds[0].get_text(strip=True)

        rec = {
            "TICKER": ticker,
            "COMPANY_NAME": company_name or None,
            "MARKET_SEGMENT": market or None,
            "DELISTING_DATE": None,
            "DELISTING_REASON": None,
            "FISCAL_YEAR": datetime.now(JST).year,
            "IS_TOB_MBO": None,
            "TOB_PRICE": price,
            "TOB_ANNOUNCEMENT_DATE": tob_start or None,
        }

        if ticker in raw:
            existing_price = raw[ticker]["TOB_PRICE"] or 0
            new_price = price or 0
            if new_price > existing_price:
                logger.info("competing_tob_higher_price", ticker=ticker, old=existing_price, new=new_price)
                raw[ticker] = rec
        else:
            raw[ticker] = rec

    return list(raw.values())


def _normalize_ticker(s: str) -> str | None:
    """銘柄コードを正規化する。"""
    s = s.strip()
    if re.match(r"^\d{4}[A-Z]?$", s):
        return s
    try:
        return str(int(float(s))).zfill(4)
    except (ValueError, OverflowError):
        return None


def _extract_company_name(td) -> str:
    """td タグから銘柄名テキストを抽出する（リンクテキスト優先）。"""
    a = td.find("a")
    if a:
        for img in a.find_all("img"):
            img.decompose()
        return a.get_text(strip=True)
    return td.get_text(strip=True)


def _parse_price(s: str) -> float | None:
    """「5,200円」→ 5200.0 に変換する。"""
    s = s.replace(",", "").replace("，", "").replace("円", "").strip()
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def get_existing_tickers(client: bigquery.Client) -> set[str]:
    """BQ の既存 TICKER セットを返す（廃止予定は DELISTING_DATE=NULL のものも含む）。"""
    sql = f"""
        SELECT DISTINCT TICKER
        FROM `{TABLE_ID}`
        WHERE TICKER IS NOT NULL
    """
    return {r.TICKER for r in client.query(sql).result()}


def upsert_to_bq(client: bigquery.Client, rows: list[dict]) -> int:
    """新規 TICKER を INSERT する。既存 TICKER はスキップ。

    scrape_jpx_delisted.py が後日 DELISTING_DATE 等を UPDATE する想定。
    """
    existing = get_existing_tickers(client)
    new_rows = [r for r in rows if r["TICKER"] not in existing]

    if not new_rows:
        return 0

    df = pd.DataFrame(new_rows)
    if "TOB_ANNOUNCEMENT_DATE" in df.columns:
        df["TOB_ANNOUNCEMENT_DATE"] = pd.to_datetime(df["TOB_ANNOUNCEMENT_DATE"]).dt.date

    job_config = bigquery.LoadJobConfig(
        schema=[
            bigquery.SchemaField("TICKER",                "STRING",  mode="NULLABLE"),
            bigquery.SchemaField("COMPANY_NAME",          "STRING",  mode="NULLABLE"),
            bigquery.SchemaField("MARKET_SEGMENT",        "STRING",  mode="NULLABLE"),
            bigquery.SchemaField("DELISTING_DATE",        "DATE",    mode="NULLABLE"),
            bigquery.SchemaField("DELISTING_REASON",      "STRING",  mode="NULLABLE"),
            bigquery.SchemaField("FISCAL_YEAR",           "INTEGER", mode="NULLABLE"),
            bigquery.SchemaField("IS_TOB_MBO",            "BOOLEAN", mode="NULLABLE"),
            bigquery.SchemaField("TOB_PRICE",             "FLOAT64", mode="NULLABLE"),
            bigquery.SchemaField("TOB_ANNOUNCEMENT_DATE", "DATE",    mode="NULLABLE"),
        ],
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    job = client.load_table_from_dataframe(df, TABLE_ID, job_config=job_config)
    job.result()
    return len(new_rows)


def main() -> None:
    """メイン処理。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="BQ書き込みなし")
    args = parser.parse_args()

    logger.info("matsui_tob_scrape_start")

    html = fetch_tob_page()
    rows = parse_tob_rows(html)
    logger.info("parsed", total_tob=len(rows) if rows else 0)

    if not rows:
        logger.info("no_delisting_scheduled_rows")
        return

    for r in rows:
        logger.info("delisting_scheduled", ticker=r["TICKER"], company=r["COMPANY_NAME"], price=r["TOB_PRICE"])

    if args.dry_run:
        logger.info("dry_run_complete", count=len(rows))
        return

    client = get_bq()
    inserted = upsert_to_bq(client, rows)
    logger.info("bq_insert_complete", inserted=inserted, skipped=len(rows) - inserted)


if __name__ == "__main__":
    main()

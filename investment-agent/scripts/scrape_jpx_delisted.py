"""
JPX 上場廃止銘柄一覧をスクレイピングし、BQ テーブル STOCK.DELISTED_STOCKS に追加する。

- 2017年〜現在の全廃止銘柄を取得
- 既存レコード（同一 TICKER + DELISTING_DATE）はスキップ
- IS_TOB_MBO は NULL で挿入。判定は /classify-tob スキルで実施

Usage:
    PYTHONUTF8=1 python scripts/scrape_jpx_delisted.py
    PYTHONUTF8=1 python scripts/scrape_jpx_delisted.py --year 2026  # 特定年のみ
    PYTHONUTF8=1 python scripts/scrape_jpx_delisted.py --dry-run     # BQ書き込みなし

元スクリプト: C:\\Users\\zonekun\\Dropbox\\stock\\py\\scrape_jpx_delisted.py
"""
from __future__ import annotations

import argparse
import io
import re
import time
from datetime import datetime, timezone, timedelta

import pandas as pd
import urllib3
import requests as _req
from requests.adapters import HTTPAdapter as _HA
from google.cloud import bigquery
from google.oauth2 import service_account
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# ──────────────────────────────────────────────
# SSL パッチ（Windows ローカル）
# ──────────────────────────────────────────────
urllib3.disable_warnings()

class _NoVerify(_HA):
    def send(self, req, **kw):
        kw["verify"] = False
        return super().send(req, **kw)

_orig = _req.Session.__init__
def _p(self, *a, **kw):
    _orig(self, *a, **kw)
    self.mount("https://", _NoVerify())
    self.verify = False
_req.Session.__init__ = _p

# ──────────────────────────────────────────────
# 設定
# ──────────────────────────────────────────────
KEY_FILE        = "keys/gcp-service-account.json"
PROJECT         = "gmailpj-357912"
DATASET         = "STOCK"
TABLE           = "DELISTED_STOCKS"
TABLE_ID        = f"{PROJECT}.{DATASET}.{TABLE}"
CHROME_EXE      = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
JST             = timezone(timedelta(hours=9))

BASE_URL = "https://www.jpx.co.jp"
TARGET_URLS: dict[str, str] = {
    "2017": "/listing/stocks/delisted/archives-09.html",
    "2018": "/listing/stocks/delisted/archives-08.html",
    "2019": "/listing/stocks/delisted/archives-07.html",
    "2020": "/listing/stocks/delisted/archives-06.html",
    "2021": "/listing/stocks/delisted/archives-05.html",
    "2022": "/listing/stocks/delisted/archives-04.html",
    "2023": "/listing/stocks/delisted/archives-03.html",
    "2024": "/listing/stocks/delisted/archives-02.html",
    "2025": "/listing/stocks/delisted/archives-01.html",
    "2026": "/listing/stocks/delisted/index.html",
}

BQ_SCHEMA = [
    bigquery.SchemaField("TICKER",           "STRING",  mode="NULLABLE"),
    bigquery.SchemaField("COMPANY_NAME",     "STRING",  mode="NULLABLE"),
    bigquery.SchemaField("MARKET_SEGMENT",   "STRING",  mode="NULLABLE"),
    bigquery.SchemaField("DELISTING_DATE",   "DATE",    mode="NULLABLE"),
    bigquery.SchemaField("DELISTING_REASON", "STRING",  mode="NULLABLE"),
    bigquery.SchemaField("FISCAL_YEAR",      "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("IS_TOB_MBO",       "BOOLEAN", mode="NULLABLE"),
]



def log(msg: str) -> None:
    ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ──────────────────────────────────────────────
# BQ
# ──────────────────────────────────────────────

def get_bq() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return bigquery.Client(project=PROJECT, credentials=creds)


def ensure_table(client: bigquery.Client) -> None:
    """テーブルが存在しない場合は作成する。"""
    try:
        client.get_table(TABLE_ID)
        log(f"BQテーブル確認済み: {TABLE_ID}")
    except Exception:
        table = bigquery.Table(TABLE_ID, schema=BQ_SCHEMA)
        client.create_table(table)
        log(f"BQテーブル作成: {TABLE_ID}")


def get_existing_keys(client: bigquery.Client) -> set[tuple[str, str]]:
    """既存レコードの (TICKER, DELISTING_DATE文字列) セットを返す。"""
    sql = f"""
        SELECT TICKER, CAST(DELISTING_DATE AS STRING) AS DELISTING_DATE
        FROM `{TABLE_ID}`
        WHERE TICKER IS NOT NULL AND DELISTING_DATE IS NOT NULL
    """
    return {(r.TICKER, r.DELISTING_DATE) for r in client.query(sql).result()}


def get_pending_tickers(client: bigquery.Client) -> set[str]:
    """松井 TOB 先行INSERT 分（DELISTING_DATE=NULL）の TICKER セットを返す。"""
    sql = f"""
        SELECT DISTINCT TICKER
        FROM `{TABLE_ID}`
        WHERE TICKER IS NOT NULL AND DELISTING_DATE IS NULL
    """
    return {r.TICKER for r in client.query(sql).result()}


def update_pending(client: bigquery.Client, rows: list[dict]) -> int:
    """松井先行INSERT分を JPX 確定情報で UPDATE する。"""
    if not rows:
        return 0
    updated = 0
    for r in rows:
        sql = f"""
            UPDATE `{TABLE_ID}`
            SET DELISTING_DATE = @delisting_date,
                DELISTING_REASON = @delisting_reason,
                FISCAL_YEAR = @fiscal_year
            WHERE TICKER = @ticker
              AND DELISTING_DATE IS NULL
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("ticker", "STRING", r["TICKER"]),
                bigquery.ScalarQueryParameter("delisting_date", "DATE", r["DELISTING_DATE"]),
                bigquery.ScalarQueryParameter("delisting_reason", "STRING", r.get("DELISTING_REASON")),
                bigquery.ScalarQueryParameter("fiscal_year", "INT64", r.get("FISCAL_YEAR")),
            ]
        )
        client.query(sql, job_config=job_config).result()
        updated += 1
    return updated


def insert_to_bq(client: bigquery.Client, rows: list[dict]) -> int:
    """新規レコードを BQ にバッチロードする。挿入件数を返す。"""
    if not rows:
        return 0
    df = pd.DataFrame(rows)
    df["DELISTING_DATE"] = pd.to_datetime(df["DELISTING_DATE"]).dt.date
    job_config = bigquery.LoadJobConfig(
        schema=BQ_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    job = client.load_table_from_dataframe(df, TABLE_ID, job_config=job_config)
    job.result()
    return len(rows)


# ──────────────────────────────────────────────
# スクレイピング
# ──────────────────────────────────────────────

def create_driver() -> webdriver.Chrome:
    opts = Options()
    opts.binary_location = CHROME_EXE
    opts.add_argument("--window-size=1200,800")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """JPX テーブルのカラム名を正規化する。"""
    col_map: dict[str, str] = {}
    for col in df.columns:
        c = str(col).strip()
        if re.search(r"コード|code", c, re.I):
            col_map[col] = "ticker"
        elif re.search(r"銘柄名|会社名|名称", c, re.I):
            col_map[col] = "company_name"
        elif re.search(r"市場|区分|section", c, re.I):
            col_map[col] = "market"
        elif re.search(r"廃止日|delisted", c, re.I):
            col_map[col] = "delisted_date"
        elif re.search(r"理由|reason", c, re.I):
            col_map[col] = "reason"
    df = df.rename(columns=col_map)
    for need in ["ticker", "company_name", "market", "delisted_date", "reason"]:
        if need not in df.columns:
            df[need] = None
    return df


def _normalize_ticker(x) -> str | None:
    """銘柄コードを正規化する。数値(1301.0)→'1301'、アルファナメリック(174A)→そのまま。"""
    if pd.isna(x):
        return None
    s = str(x).strip()
    if re.match(r"^\d{4}[A-Z]$", s):
        return s
    try:
        return str(int(float(s))).zfill(4)
    except (ValueError, OverflowError):
        return None


def parse_delisted_date(val) -> str | None:
    """上場廃止日を YYYY-MM-DD 文字列に変換する。"""
    if pd.isna(val):
        return None
    s = str(val).strip()
    m = re.match(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    wareki = {"令和": 2018, "平成": 1988, "昭和": 1925}
    m2 = re.match(r"(令和|平成|昭和)(\d+)年(\d+)月(\d+)日", s)
    if m2:
        year = wareki.get(m2.group(1), 0) + int(m2.group(2))
        return f"{year}-{int(m2.group(3)):02d}-{int(m2.group(4)):02d}"
    return None


def parse_fiscal_year(s: str) -> int | None:
    """取得元年度文字列から西暦4桁を抽出する。例: '2026(最新)' → 2026"""
    m = re.search(r"\d{4}", str(s))
    return int(m.group()) if m else None


def scrape_year(driver: webdriver.Chrome, year: str, path: str) -> pd.DataFrame:
    url = BASE_URL + path
    log(f"  [{year}] {url}")
    driver.get(url)
    time.sleep(3)

    html = driver.page_source
    try:
        dfs = pd.read_html(io.StringIO(html))
    except Exception as e:
        log(f"    テーブル抽出失敗: {e}")
        return pd.DataFrame()

    if not dfs:
        log("    テーブルなし")
        return pd.DataFrame()

    df = dfs[0]
    df = normalize_columns(df)
    df["source_year"] = year
    df["delisted_date"] = df["delisted_date"].apply(parse_delisted_date)
    df["ticker"] = df["ticker"].apply(
        _normalize_ticker
    )
    df = df[df["ticker"].notna() & (df["ticker"] != "ticker")].reset_index(drop=True)
    log(f"    → {len(df)}件取得")
    return df


# ──────────────────────────────────────────────
# メイン
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year",    default=None,  help="特定年のみ取得 (例: 2026)")
    parser.add_argument("--dry-run", action="store_true", help="BQ書き込みなし")
    args = parser.parse_args()

    log("=== JPX 上場廃止銘柄スクレイピング ===")

    client = get_bq()

    if not args.dry_run:
        ensure_table(client)
    existing = get_existing_keys(client)
    pending = get_pending_tickers(client)
    log(f"既存レコード: {len(existing)}件 / 松井先行INSERT（DELISTING_DATE=NULL）: {len(pending)}件")
    if args.dry_run:
        log("dry-run モード: BQ書き込みなし")

    targets = {k: v for k, v in TARGET_URLS.items()
               if args.year is None or k == args.year}

    driver = create_driver()
    try:
        all_frames: list[pd.DataFrame] = []
        for year, path in targets.items():
            df = scrape_year(driver, year, path)
            if not df.empty:
                all_frames.append(df)
    finally:
        time.sleep(2)
        driver.quit()

    if not all_frames:
        log("データ取得なし")
        return

    full_df = pd.concat(all_frames, ignore_index=True)
    log(f"\n総取得: {len(full_df)}件")

    # 新規レコードのみ抽出。松井先行INSERT分はUPDATE対象に振り分け
    new_rows: list[dict] = []
    update_rows: list[dict] = []
    skip = 0
    for _, row in full_df.iterrows():
        ticker = str(row["ticker"]) if pd.notna(row["ticker"]) else ""
        date   = str(row["delisted_date"]) if pd.notna(row["delisted_date"]) else ""
        if not ticker or not date:
            skip += 1
            continue
        if (ticker, date) in existing:
            skip += 1
            continue

        company = row.get("company_name") or ""
        rec = {
            "TICKER":           ticker,
            "COMPANY_NAME":     company or None,
            "MARKET_SEGMENT":   row.get("market") or None,
            "DELISTING_DATE":   date,
            "DELISTING_REASON": row.get("reason") or None,
            "FISCAL_YEAR":      parse_fiscal_year(row.get("source_year")),
            "IS_TOB_MBO":       None,
        }

        if ticker in pending:
            update_rows.append(rec)
        else:
            new_rows.append(rec)
        existing.add((ticker, date))

    log(f"新規INSERT: {len(new_rows)}件 / 松井先行UPDATE: {len(update_rows)}件 / スキップ: {skip}件")

    if args.dry_run:
        for label, rows_list in [("INSERT予定", new_rows), ("UPDATE予定", update_rows)]:
            if rows_list:
                log(f"dry-run: {label}（先頭5件）")
                for r in rows_list[:5]:
                    log(f"  {r}")
        return

    if update_rows:
        updated = update_pending(client, update_rows)
        log(f"松井先行分UPDATE完了: {updated}件")

    if new_rows:
        inserted = insert_to_bq(client, new_rows)
        log(f"BQ挿入完了: {inserted}件 → {TABLE_ID}")

    if not new_rows and not update_rows:
        log("新規レコードなし。BQ更新不要。")

    log("完了")


if __name__ == "__main__":
    main()

"""
JPX 上場廃止銘柄一覧をスクレイピングし、BQ テーブル STOCK.DELISTED_STOCKS に追加する。

- 2017年〜現在の全廃止銘柄を取得
- 既存レコード（同一 TICKER + DELISTING_DATE）はスキップ
- IS_TOB_MBO は TDNET BQ 検索 + Gemini で自動判定
  - BQ TDNET_DOCUMENTS_ENHANCED から直近180日の開示テキストを取得
  - テキストなし → False
  - Gemini判定基準:
      True : 第三者・外部投資家によるTOB、MBO
      False: 業績不振救済買収 / テクニカル上場廃止 / 複数会社統合

Usage:
    PYTHONUTF8=1 python scripts/scrape_jpx_delisted.py
    PYTHONUTF8=1 python scripts/scrape_jpx_delisted.py --year 2026  # 特定年のみ
    PYTHONUTF8=1 python scripts/scrape_jpx_delisted.py --dry-run     # BQ書き込みなし
    PYTHONUTF8=1 python scripts/scrape_jpx_delisted.py --eval        # 既存10件で判定精度確認

元スクリプト: C:\\Users\\zonekun\\Dropbox\\stock\\py\\scrape_jpx_delisted.py
"""
from __future__ import annotations

import argparse
import io
import os
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

VERTEXAI_REGION = "us-central1"
GEMINI_MODEL    = "gemini-2.5-flash"
TDNET_TABLE     = f"{PROJECT}.{DATASET}.TDNET_DOCUMENTS_ENHANCED"

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
    try:
        return {(r.TICKER, r.DELISTING_DATE) for r in client.query(sql).result()}
    except Exception:
        return set()


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
# IS_TOB_MBO 判定（TDNET BQ + Gemini）
# ──────────────────────────────────────────────

def _tdnet_get_texts(ticker: str, delisting_date_str: str, client: bigquery.Client) -> str | None:
    """BQ TDNET_DOCUMENTS_ENHANCED から直近180日の開示テキストを結合して返す。

    Args:
        ticker: 4桁銘柄コード
        delisting_date_str: 廃止日 YYYY-MM-DD
        client: BigQuery クライアント

    Returns:
        結合テキスト（見つかれば）または None
    """
    del_dt  = datetime.strptime(delisting_date_str, "%Y-%m-%d").date()
    from_dt = del_dt - timedelta(days=180)
    sql = f"""
        SELECT DOC_TITLE, CHUNK_TEXT
        FROM `{TDNET_TABLE}`
        WHERE TICKER = '{ticker}'
          AND SUBMISSION_DATE BETWEEN '{from_dt}' AND '{del_dt}'
        ORDER BY SUBMISSION_DATE, DOC_ID
    """
    try:
        rows = list(client.query(sql).result())
    except Exception as e:
        log(f"      BQクエリエラー: {e}")
        return None
    if not rows:
        return None
    parts = [f"【{r.DOC_TITLE}】\n{r.CHUNK_TEXT}" for r in rows]
    return "\n\n".join(parts)


def _gemini_classify(text: str, company_name: str) -> bool | None:
    """TDnet テキストを Gemini に渡して IS_TOB_MBO を True / False / None で返す。"""
    try:
        from google import genai
    except ImportError:
        log("      google-genai 未インストール → None")
        return None

    try:
        creds = service_account.Credentials.from_service_account_file(KEY_FILE)
        client = genai.Client(vertexai=True, project=PROJECT, location=VERTEXAI_REGION, credentials=creds)

        prompt = f"""以下はTDnet（適時開示）に提出された書類のテキストです。
{company_name}の上場廃止が「株主にプレミアムを付けた買収」によるものかを判定してください。

【True とする条件（プレミアム付き買収）】
- 公開買付け（TOB）が実施された場合（買付者が第三者・支配株主・親会社いずれでも True）
- 経営陣による買収（MBO）が実施された場合
- TOB後の株式併合・株式売渡請求（スクイーズアウト）による完全子会社化

【False とする条件（プレミアムなし・買収以外）】
- 株式交換・株式移転によるグループ再編（プレミアムなし・TOBなし）
- 複数会社の合併による廃止
- 業績不振・財務危機企業の救済買収（実質倒産回避・TOBなし）
- 内部管理体制不備・基準不適合などテクニカルな上場廃止
- 上記いずれにも該当しない

書類テキスト:
{text[:6000]}

「True」または「False」の1単語のみで回答してください。"""

        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        answer = response.text.strip()
        if "true" in answer.lower():
            return True
        elif "false" in answer.lower():
            return False
        log(f"      Gemini 不明回答: {answer[:80]}")
        return None
    except Exception as e:
        log(f"      Geminiエラー: {e}")
        return None


def classify_is_tob_mbo(ticker: str, company_name: str, delisting_date: str,
                         client: bigquery.Client) -> bool | None:
    """TDNET BQ 検索 + Gemini で IS_TOB_MBO を判定する。

    Args:
        ticker: 4桁銘柄コード
        company_name: 会社名
        delisting_date: 廃止日 YYYY-MM-DD
        client: BigQuery クライアント

    Returns:
        True / False / None（判定不可）
    """
    log(f"    IS_TOB_MBO判定: {company_name} ({ticker})")
    text = _tdnet_get_texts(ticker, delisting_date, client)
    if text is None:
        log(f"    → TDNET文書なし → False")
        return False
    log(f"    → TDNET文書: {len(text):,}文字 → Gemini判定")
    result = _gemini_classify(text, company_name)
    log(f"    → Gemini判定: {result}")
    return result


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
        lambda x: str(int(x)).zfill(4) if pd.notna(x) and str(x).strip().isdigit() else None
    )
    df = df[df["ticker"].notna() & (df["ticker"] != "ticker")].reset_index(drop=True)
    log(f"    → {len(df)}件取得")
    return df


# ──────────────────────────────────────────────
# メイン
# ──────────────────────────────────────────────

def eval_mode(client: bigquery.Client, year: int = 2026, n: int = 10) -> None:
    """BQ から n 件取得し、TDNET+Gemini 分類を実行して結果を表示する（BQ書き込みなし）。"""
    sql = f"""
        SELECT TICKER, COMPANY_NAME, DELISTING_DATE, DELISTING_REASON, IS_TOB_MBO
        FROM `{TABLE_ID}`
        WHERE FISCAL_YEAR = {year}
        ORDER BY DELISTING_DATE
        LIMIT {n}
    """
    rows = list(client.query(sql).result())
    log(f"=== eval モード: {len(rows)}件を再判定（BQ更新なし）===\n")
    log(f"{'TICKER':<6}  {'COMPANY':<25}  {'BQ値':<6}  {'新判定':<6}  {'一致'}")
    log("-" * 70)

    for r in rows:
        ticker  = r.TICKER
        company = r.COMPANY_NAME or ""
        date    = str(r.DELISTING_DATE)
        bq_val  = r.IS_TOB_MBO  # bool or None

        new_val = classify_is_tob_mbo(ticker, company, date, client)

        match = "✓" if new_val == bq_val else "✗" if new_val is not None else "?"
        log(f"{ticker:<6}  {company[:25]:<25}  {str(bq_val):<6}  {str(new_val):<6}  {match}")

    log("\neval 完了（BQ は変更していません）")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year",    default=None,  help="特定年のみ取得 (例: 2026)")
    parser.add_argument("--dry-run", action="store_true", help="BQ書き込みなし（IS_TOB_MBO判定も省略）")
    parser.add_argument("--eval",      action="store_true", help="BQの既存10件でIS_TOB_MBO再判定（書き込みなし）")
    parser.add_argument("--eval-year", type=int, default=2026, help="evalモードで使う年度 (デフォルト: 2026)")
    args = parser.parse_args()

    log("=== JPX 上場廃止銘柄スクレイピング ===")

    client = get_bq()

    if args.eval:
        eval_mode(client, year=args.eval_year)
        return

    if not args.dry_run:
        ensure_table(client)
    existing = get_existing_keys(client)
    log(f"既存レコード: {len(existing)}件")
    if args.dry_run:
        log("dry-run モード: BQ書き込み・IS_TOB_MBO判定なし")

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

    # 新規レコードのみ抽出 → IS_TOB_MBO 判定 → BQ INSERT
    new_rows: list[dict] = []
    skip = 0
    for _, row in full_df.iterrows():
        ticker = str(row["ticker"]) if pd.notna(row["ticker"]) else ""
        date   = str(row["delisted_date"]) if pd.notna(row["delisted_date"]) else ""
        if not ticker or not date or (ticker, date) in existing:
            skip += 1
            continue

        company = row.get("company_name") or ""

        # IS_TOB_MBO: TDNET BQ + Gemini 判定（dry-run 時はスキップ）
        if not args.dry_run:
            is_tob_mbo = classify_is_tob_mbo(ticker, company, date, client)
        else:
            is_tob_mbo = None

        new_rows.append({
            "TICKER":           ticker,
            "COMPANY_NAME":     company or None,
            "MARKET_SEGMENT":   row.get("market") or None,
            "DELISTING_DATE":   date,
            "DELISTING_REASON": row.get("reason") or None,
            "FISCAL_YEAR":      parse_fiscal_year(row.get("source_year")),
            "IS_TOB_MBO":       is_tob_mbo,
        })
        existing.add((ticker, date))

    log(f"新規: {len(new_rows)}件 / スキップ（既存・無効）: {skip}件")

    if args.dry_run:
        if new_rows:
            log("dry-run: 挿入予定データ（先頭5件）")
            for r in new_rows[:5]:
                log(f"  {r}")
        return

    if new_rows:
        inserted = insert_to_bq(client, new_rows)
        log(f"BQ挿入完了: {inserted}件 → {TABLE_ID}")
    else:
        log("新規レコードなし。BQ更新不要。")

    log("完了")


if __name__ == "__main__":
    main()

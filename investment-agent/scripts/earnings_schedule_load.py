# -*- coding: utf-8 -*-
"""ghostrader.net 決算発表予定スクレイピング → BigQuery ロード

実行環境: Windows ローカル / Cloud Run Job
ロード先: BigQuery gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR
          (RECORD_TYPE='S' のみ)
方式    : 実行日以降の S レコードを DELETE → 新規 INSERT（冪等）

対象URL:
  1. https://www.ghostrader.net/                          (当日)
  2. https://www.ghostrader.net/Schedule_1_Tomorrow.htm   (翌営業日 p1)
  3. https://www.ghostrader.net/Schedule_1_Tomorrow2.htm  (翌営業日 p2)
  4. https://www.ghostrader.net/Schedule_2_After.htm      (翌々営業日以降)
"""

import argparse
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone, date

import pandas as pd
import requests
import structlog
from bs4 import BeautifulSoup
from google.cloud import bigquery
from google.oauth2 import service_account

# ── パスを通す ──────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import send_mail, LogCapture

# ── 定数 ────────────────────────────────────────────
JST = timezone(timedelta(hours=+9), "JST")

PROJECT_ID = "gmailpj-357912"
DATASET_ID = "STOCK"
TABLE_ID = "EARNINGS_DISCLOSURE_CALENDAR"
TABLE_FQN = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

TARGET_URLS = [
    "https://www.ghostrader.net/",
    "https://www.ghostrader.net/Schedule_1_Tomorrow.htm",
    "https://www.ghostrader.net/Schedule_1_Tomorrow2.htm",
    "https://www.ghostrader.net/Schedule_2_After.htm",
]

SLEEP_SEC = 1.0  # リクエスト間隔

# 四半期マッピング（ghostrader の img alt → 本テーブル QUARTER）
QUARTER_MAP: dict[str, str] = {
    "本決算": "本決算",
    "第１四半期": "1Q",
    "第２四半期": "中間決算",
    "第３四半期": "3Q",
}

log = structlog.get_logger()


# ============================================================
# 実行環境の自動判別
# ============================================================

def detect_runtime() -> str:
    """実行環境を自動判別する."""
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    return "local"


RUNTIME: str = detect_runtime()


# ============================================================
# BigQuery クライアント
# ============================================================

def get_bq_client() -> bigquery.Client:
    """実行環境に応じた BigQuery クライアントを返す."""
    if RUNTIME == "local":
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from src.core.config import settings
        creds = service_account.Credentials.from_service_account_file(
            settings.google_application_credentials,
        )
        return bigquery.Client(project=PROJECT_ID, credentials=creds)
    else:  # cloudrun: ADC
        return bigquery.Client(project=PROJECT_ID)


# ============================================================
# 1. スクレイピング
# ============================================================

def _parse_date_text(text: str) -> date | None:
    """'2026年 4月 6日(月)' → datetime.date."""
    m = re.search(r"(\d{4})年\s*(\d+)月\s*(\d+)日", text)
    if not m:
        return None
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _parse_fiscal_year_end(text: str, announcement_date: date) -> date | None:
    """'2026年02月' → datetime.date (月末日).

    決算月テキストから FISCAL_YEAR_END を導出する。
    年が含まれない場合は announcement_date から推定する。
    """
    # パターン1: "2026年02月"
    m = re.search(r"(\d{4})年\s*(\d{1,2})月", text)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
    else:
        # パターン2: "02月" のみ
        m2 = re.search(r"(\d{1,2})月", text)
        if not m2:
            return None
        month = int(m2.group(1))
        # 発表日の年をベースに推定（発表日より3ヶ月以上未来なら前年）
        year = announcement_date.year
        candidate = date(year, month, 1)
        if candidate > announcement_date + timedelta(days=120):
            year -= 1

    # 月末日を算出
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def scrape_page(url: str) -> list[dict]:
    """1ページをスクレイピングして行リストを返す."""
    log.info("scrape_start", url=url)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
        ),
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.encoding = resp.apparent_encoding
    soup = BeautifulSoup(resp.text, "html.parser")

    # 日付抽出: <font size="6"> の中の日付テキスト
    date_el = soup.select_one("p > b > font[size='6']")
    if date_el is None:
        # フォールバック: <b> 直下の日付テキストを探す
        for b_tag in soup.find_all("b"):
            if re.search(r"\d{4}年\s*\d+月\s*\d+日", b_tag.get_text()):
                date_el = b_tag
                break

    if date_el is None:
        log.warning("date_not_found", url=url)
        return []

    announcement_date = _parse_date_text(date_el.get_text())
    if announcement_date is None:
        log.warning("date_parse_failed", url=url, text=date_el.get_text())
        return []

    log.info("date_parsed", url=url, dt=str(announcement_date))

    # データテーブル: bgcolor="#FFFBF7"
    tables = soup.find_all("table", attrs={"bgcolor": "#FFFBF7"})
    if not tables:
        log.info("no_data_tables", url=url)  # 土日祝・決算なし日は正常
        return []

    rows_out: list[dict] = []
    for table in tables:
        trs = table.find_all("tr")
        for tr in trs[1:]:  # ヘッダ行スキップ
            tds = tr.find_all("td")
            if len(tds) < 7:
                continue

            time_str = tds[0].get_text(strip=True)
            if time_str == "--:--":
                continue

            code_str = tds[1].get_text(strip=True)
            # 銘柄コード: 4桁に正規化
            code_str = re.sub(r"\D", "", code_str)[:4]
            if len(code_str) != 4:
                continue

            # 企業名
            a_tag = tds[2].find("a")
            name_str = a_tag.get_text(strip=True) if a_tag else tds[2].get_text(strip=True)

            # 四半期種別: img の alt
            img_tag = tds[6].find("img")
            if img_tag is None:
                continue
            quarter_alt = img_tag.get("alt", "").strip()
            quarter = QUARTER_MAP.get(quarter_alt)
            if quarter is None:
                log.debug("unknown_quarter", alt=quarter_alt, code=code_str)
                continue

            # 決算月（決算期末の導出用）
            fy_text = ""
            if len(tds) > 7:
                fy_text = tds[7].get_text(strip=True)
            elif len(tds) > 5:
                # カラム位置がずれる場合のフォールバック
                fy_text = tds[5].get_text(strip=True)

            fiscal_year_end = _parse_fiscal_year_end(fy_text, announcement_date)
            if fiscal_year_end is None:
                log.debug("fy_parse_fail", code=code_str, fy_text=fy_text,
                          cells_cnt=len(tds),
                          cell_texts=[c.get_text(strip=True) for c in tds])

            # 時刻パース
            disclosure_time = None
            tm = re.match(r"(\d{1,2}):(\d{2})", time_str)
            if tm:
                disclosure_time = f"{int(tm.group(1)):02d}:{tm.group(2)}:00"

            rows_out.append({
                "TICKER": code_str,
                "FISCAL_YEAR_END": str(fiscal_year_end) if fiscal_year_end else None,
                "QUARTER": quarter,
                "CATEGORY": "R",
                "RECORD_TYPE": "S",
                "REVISION_SEQ": 1,
                "DISCLOSURE_DATE": str(announcement_date),
                "DISCLOSURE_TIME": disclosure_time,
                "DISCLOSURE_NUMBER": None,
                "TYPE_OF_DOCUMENT": None,
                "DOC_TITLE": None,
                "SOURCE": "ghostrader",
                "COMPANY_NAME": name_str,  # BQ非格納。ログ用
            })

    log.info("scrape_done", url=url, row_cnt=len(rows_out))
    return rows_out


def scrape_all() -> list[dict]:
    """全URLをスクレイピングして結合リストを返す."""
    all_rows: list[dict] = []
    for url in TARGET_URLS:
        try:
            rows = scrape_page(url)
            all_rows.extend(rows)
        except Exception:
            log.error("scrape_error", url=url, exc=traceback.format_exc())
        time.sleep(SLEEP_SEC)

    # 重複除去（同一銘柄×同一日付×同一四半期）
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for r in all_rows:
        key = (r["TICKER"], r["DISCLOSURE_DATE"], r["QUARTER"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    log.info("scrape_all_done", total=len(all_rows), deduped=len(deduped))
    return deduped


# ============================================================
# 2. BigQuery ロード
# ============================================================

def delete_future_scheduled(client: bigquery.Client, today: date) -> int:
    """実行日以降の S レコードを完全削除する."""
    sql = f"""
    DELETE FROM `{TABLE_FQN}`
    WHERE RECORD_TYPE = 'S'
      AND DISCLOSURE_DATE >= @today
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("today", "DATE", str(today)),
        ],
    )
    result = client.query(sql, job_config=job_config).result()
    deleted = result.num_dml_affected_rows or 0
    log.info("delete_done", deleted=deleted, since=str(today))
    return deleted


def insert_rows(client: bigquery.Client, rows: list[dict]) -> int:
    """スクレイピング結果を BQ に INSERT する."""
    if not rows:
        log.info("no_rows_to_insert")
        return 0

    now_jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    # BQ 用 DataFrame 構築（COMPANY_NAME は除外）
    records = []
    for r in rows:
        records.append({
            "TICKER": r["TICKER"],
            "FISCAL_YEAR_END": r["FISCAL_YEAR_END"],
            "QUARTER": r["QUARTER"],
            "CATEGORY": r["CATEGORY"],
            "RECORD_TYPE": r["RECORD_TYPE"],
            "REVISION_SEQ": r["REVISION_SEQ"],
            "DISCLOSURE_DATE": r["DISCLOSURE_DATE"],
            "DISCLOSURE_TIME": r["DISCLOSURE_TIME"],
            "DISCLOSURE_NUMBER": r["DISCLOSURE_NUMBER"],
            "TYPE_OF_DOCUMENT": r["TYPE_OF_DOCUMENT"],
            "DOC_TITLE": r["DOC_TITLE"],
            "SOURCE": r["SOURCE"],
            "LOADED_AT": now_jst,
        })

    df = pd.DataFrame(records)

    if df.empty:
        log.info("no_valid_rows_after_filter")
        return 0

    null_fy = df["FISCAL_YEAR_END"].isna()
    if null_fy.any():
        log.info("null_fiscal_year_end", cnt=int(null_fy.sum()))

    # JSON ロード（TIME 型を文字列のまま渡せる）
    import io
    import json as _json

    ndjson_lines = []
    for _, row in df.iterrows():
        rec = {k: (None if pd.isna(v) else v) for k, v in row.to_dict().items()}
        ndjson_lines.append(_json.dumps(rec, ensure_ascii=False))
    ndjson_bytes = ("\n".join(ndjson_lines)).encode("utf-8")

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        schema=[
            bigquery.SchemaField("TICKER", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("FISCAL_YEAR_END", "DATE"),
            bigquery.SchemaField("QUARTER", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("CATEGORY", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("RECORD_TYPE", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("REVISION_SEQ", "INTEGER", mode="REQUIRED"),
            bigquery.SchemaField("DISCLOSURE_DATE", "DATE"),
            bigquery.SchemaField("DISCLOSURE_TIME", "TIME"),
            bigquery.SchemaField("DISCLOSURE_NUMBER", "STRING"),
            bigquery.SchemaField("TYPE_OF_DOCUMENT", "STRING"),
            bigquery.SchemaField("DOC_TITLE", "STRING"),
            bigquery.SchemaField("SOURCE", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("LOADED_AT", "DATETIME", mode="REQUIRED"),
        ],
    )

    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
    job = client.load_table_from_file(
        io.BytesIO(ndjson_bytes), table_ref, job_config=job_config,
    )
    job.result()

    log.info("insert_done", row_cnt=len(df))
    return len(df)


def dedup_scheduled(client: bigquery.Client, today: date) -> int:
    """同一(TICKER, DISCLOSURE_DATE, QUARTER)の重複Sレコードを除去する.

    並行実行で DELETE→INSERT の冪等性が崩れた場合に備え、
    最新 LOADED_AT のみ残して古い重複行を削除する。
    """
    sql = f"""
    DELETE FROM `{TABLE_FQN}` t
    WHERE RECORD_TYPE = 'S'
      AND DISCLOSURE_DATE >= @today
      AND LOADED_AT < (
        SELECT MAX(s.LOADED_AT)
        FROM `{TABLE_FQN}` s
        WHERE s.RECORD_TYPE = 'S'
          AND s.TICKER = t.TICKER
          AND s.DISCLOSURE_DATE = t.DISCLOSURE_DATE
          AND s.QUARTER = t.QUARTER
      )
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("today", "DATE", str(today)),
        ],
    )
    result = client.query(sql, job_config=job_config).result()
    deleted = result.num_dml_affected_rows or 0
    if deleted > 0:
        log.warning("dedup_deleted", deleted=deleted, since=str(today))
    else:
        log.info("dedup_clean", since=str(today))
    return deleted


# ============================================================
# 3. Dropbox CSV 出力
# ============================================================

DROPBOX_LOCAL_PATH = r"C:\Users\zonekun\Dropbox\stock\bunseki\発表日次速報_Ghos.csv"

# --- Dropbox API（Cloud Run 用） ---
DBX_APP_KEY = "t8feblcw74hoeky"
DBX_APP_SECRET = "fcjgc37d034pw1n"
DBX_REFRESH_TOKEN = "XwOxZlA8jPUAAAAAAAAAAZxnT4qRFtWLcShpKy3cNjTf3euIMqEZxCNieAQiLSDw"
DBX_UPLOAD_PATH = "/stock/bunseki/発表日次速報_Ghos.csv"

_DROPBOX_ERROR: str = ""


def _build_csv_bytes(rows: list[dict]) -> bytes:
    """TAB区切り CSV のバイト列を生成する."""
    lines: list[str] = []
    for r in sorted(rows, key=lambda x: (x["DISCLOSURE_DATE"], x["DISCLOSURE_TIME"] or "")):
        dt = r["DISCLOSURE_DATE"].replace("-", "")  # YYYYMMDD
        tm = r["DISCLOSURE_TIME"] if r["DISCLOSURE_TIME"] else ""
        # TIME は "HH:MM:SS" → "HH:MM" に切り詰め
        if tm and len(tm) >= 5:
            tm = tm[:5]
        lines.append(f"{dt},{tm},{r['QUARTER']},{r['TICKER']}")
    return ("\n".join(lines) + "\n").encode("cp932")


def save_dropbox_csv(rows: list[dict]) -> None:
    """スクレイピング結果を TAB区切り CSV で保存する.

    ローカル: ファイルシステムに直接書き込み
    Cloud Run: Dropbox API 経由でアップロード
    """
    global _DROPBOX_ERROR
    csv_bytes = _build_csv_bytes(rows)

    if RUNTIME == "local":
        os.makedirs(os.path.dirname(DROPBOX_LOCAL_PATH), exist_ok=True)
        with open(DROPBOX_LOCAL_PATH, "wb") as f:
            f.write(csv_bytes)
        log.info("dropbox_csv_saved", path=DROPBOX_LOCAL_PATH, row_cnt=len(rows))
    else:
        # Cloud Run: Dropbox API
        import dropbox
        from dropbox.files import WriteMode

        try:
            dbx = dropbox.Dropbox(
                app_key=DBX_APP_KEY,
                app_secret=DBX_APP_SECRET,
                oauth2_refresh_token=DBX_REFRESH_TOKEN,
            )
            dbx.files_upload(csv_bytes, DBX_UPLOAD_PATH, mode=WriteMode("overwrite"))
            log.info("dropbox_api_upload_done", path=DBX_UPLOAD_PATH, row_cnt=len(rows))
        except Exception as e:
            if "insufficient_space" in str(e):
                _DROPBOX_ERROR = f"Dropbox 容量不足: {DBX_UPLOAD_PATH}"
                log.warning("dropbox_insufficient_space", path=DBX_UPLOAD_PATH)
            else:
                raise


# ============================================================
# main
# ============================================================

def parse_skip_days(spec: str) -> set[int]:
    """スキップ対象日（月内の日）をパースする.

    例: '14-17' → {14,15,16,17}
        '14-17,20' → {14,15,16,17,20}
        '' → set()
    """
    if not spec:
        return set()
    days: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            days.update(range(int(a), int(b) + 1))
        else:
            days.add(int(part))
    return days


def main() -> None:
    """メイン処理.

    スケジュール設計意図:
      決算発表集中期の谷間（14〜17日）は ghostrader.net のデータが
      不安定なためスキップする。かつては cron `0 5 1-13 * 0-5` /
      `0 5 18-31 * 0-5` の2本スケジューラで「1-13日 AND 平日」
      「18-31日 AND 平日」を表現しようとしていたが、unix-cron は
      day-of-month と day-of-week の両指定を OR 結合するため、
      両方が平日に毎日発火し2重トリガーになっていた。
      現行は `0 5 * * 0-5` 1本スケジューラ + --skip-days 引数で
      コード側にスキップ日を持たせる構成。
    """
    parser = argparse.ArgumentParser(
        description="ghostrader.net 決算発表予定 → BQ ロード",
    )
    parser.add_argument(
        "--skip-days",
        default="",
        help=(
            "実行をスキップする月内の日（例: '14-17'、'14-17,20'）。"
            "決算集中期の谷間などデータ不安定日を避けるため、"
            "cron で絞らずコード側で制御する。"
        ),
    )
    args = parser.parse_args()

    log.info("=== earnings_schedule_load START ===")
    today = datetime.now(JST).date()

    skip_days = parse_skip_days(args.skip_days)
    if today.day in skip_days:
        log.info("skipped_by_skip_days",
                 day=today.day, skip_days=sorted(skip_days))
        return

    log_cap = LogCapture()
    log_cap.start()
    try:
        # 1. スクレイピング
        rows = scrape_all()

        if not rows:
            log.warning("no_data_scraped")
            send_mail(
                subject="[earnings_schedule_load] データ0件",
                body="ghostrader.net からデータが取得できませんでした。",
            )
            return

        # 2. BQ: DELETE → INSERT → dedup
        client = get_bq_client()
        delete_future_scheduled(client, today)
        inserted = insert_rows(client, rows)
        dedup_scheduled(client, today)

        # 3. Dropbox CSV 保存
        save_dropbox_csv(rows)

        log.info("=== earnings_schedule_load DONE ===", inserted=inserted)

    except Exception:
        log.error("fatal", exc=traceback.format_exc())
        send_mail(
            subject="[earnings_schedule_load] ERROR",
            body=traceback.format_exc(),
        )
        raise
    finally:
        log_cap.stop()

    if _DROPBOX_ERROR:
        send_mail(
            subject="[earnings_schedule_load] 【異常終了】DROPBOX容量不足",
            body=(
                "Dropboxへのアップロードが容量不足で失敗しました。\n"
                "主処理（BQ）は正常完了しています。\n\n"
                f"スキップされたファイル: {_DROPBOX_ERROR}"
            ),
        )
        sys.exit(1)


if __name__ == "__main__":
    main()

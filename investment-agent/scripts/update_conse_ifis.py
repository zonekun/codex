"""
IFIS株予報 直取得コンセンサス（経常利益）取得 → BigQuery STOCK.CONSENSUS ロード
実行タイミング: 明示的な指示があったときのみ（ローカル専用・requests使用）

【再開機能】
    実行中断時は data/logs/conse_ifis_resume.json に状態が保存される。
    次回起動時に自動的に続きから再開（DATAATは最初の実行日で固定）。
    強制的に新規実行するには --fresh フラグを指定する。

【前提】
    - BigQuery STOCK.CONSENSUS に SOURCE STRING カラムが追加済みであること
    - 既存楽天版レコードの SOURCE='RAKU' 埋め戻しは Claude Code 側で実施すること
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import time
import unicodedata
from collections import Counter
from datetime import date
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup
from google.cloud import bigquery
from google.oauth2 import service_account

# ==========================================
# パス・定数設定
# ==========================================

_BASE = os.path.join(os.path.dirname(__file__), "..")
KEY_FILE = os.path.join(_BASE, "keys", "gcp-service-account.json")
STATE_FILE = os.path.join(_BASE, "data", "logs", "conse_ifis_resume.json")
BQ_TABLE = "gmailpj-357912.STOCK.CONSENSUS"
CSV_PATH = r"C:\Users\zonekun\Dropbox\stock\py\conse_ifis.csv"

SOURCE = "IFIS"
IFIS_URL = "https://kabuyoho.ifis.co.jp/index.php?action=tp1&sa=report&bcode={code}"
REQUEST_TIMEOUT = 30
REQUEST_INTERVAL_SEC = 1.0

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


# ==========================================
# BigQuery クライアント
# ==========================================


def get_bq_client() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return bigquery.Client(project="gmailpj-357912", credentials=creds)


def insert_to_bq(client: bigquery.Client, rows: list[dict[str, Any]]) -> bool:
    """BQ にストリーミングインサート。既存RAKUTEN版に合わせる。"""
    if not rows:
        return True
    errors = client.insert_rows_json(BQ_TABLE, rows)
    if errors:
        print(f"  ⚠️ BQ書き込みエラー: {errors}")
        return False
    return True


# ==========================================
# CSV保存
# ==========================================


def save_to_csv(row_dict: dict[str, Any]) -> None:
    """データをCSVファイルに1銘柄1行で追記保存する（項目名なし・Shift-JIS）。"""
    keys = ["TICKER", "1Q_CURRENT", "2Q_CURRENT", "3Q_CURRENT", "FY_CURRENT", "FY_NEXT"]
    row_data = [row_dict.get(k, "") for k in keys]

    try:
        os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
        with open(CSV_PATH, mode="a", newline="", encoding="cp932", errors="replace") as f:
            writer = csv.writer(f)
            writer.writerow(row_data)
    except Exception as e:
        print(f"  ⚠️ CSV保存失敗: {e}")


# ==========================================
# 再開用状態管理
# ==========================================


def load_state() -> dict[str, str] | None:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(dataat: str, last_ticker: str) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"dataat": dataat, "last_ticker": last_ticker}, f)


def clear_state() -> None:
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)


# ==========================================
# 銘柄リスト取得
# ==========================================


def get_japanese_stock_tickers(client: bigquery.Client) -> list[str]:
    """BigQuery STOCK_CODE_LIST からプライム・スタンダード・グロース（内国株式）を取得。
    BQ取得失敗時はJPX Excelにフォールバック。
    """
    print("📊 BigQuery から銘柄リストを取得中...")
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
        print(f"✅ {len(tickers)} 銘柄取得完了（BQ）")
        return tickers
    except Exception as e:
        print(f"⚠️ BQ 取得失敗: {e} → JPX Excel にフォールバック")

    print("📊 JPX 上場銘柄一覧 Excel を取得中...")
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        df = pd.read_excel(io.BytesIO(resp.content), dtype=str)
        target_categories = {
            "プライム（内国株式）",
            "スタンダード（内国株式）",
            "グロース（内国株式）",
        }
        df = df[df["市場・商品区分"].isin(target_categories)]
        tickers = sorted(df["コード"].str.zfill(4).tolist())
        print(f"✅ {len(tickers)} 銘柄取得完了（JPX Excel）")
        return tickers
    except Exception as e2:
        print(f"❌ 銘柄リスト取得失敗（Excel）: {e2}")
        return []


# ==========================================
# IFISページ取得・抽出
# ==========================================


def normalize_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def clean_number(text: str) -> str:
    cleaned = re.sub(r"[,\s\u3000\xa0]", "", unicodedata.normalize("NFKC", text).strip())
    cleaned = cleaned.replace("--", "").replace("－", "-")
    return cleaned if re.match(r"^-?\d+$", cleaned) else ""


def fetch_ifis_page(code: str, session: requests.Session | None = None) -> str:
    sess = session or requests.Session()
    url = IFIS_URL.format(code=code)
    resp = sess.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        },
    )
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def extract_current_fy_from_ifis(soup: BeautifulSoup) -> str | None:
    prog = soup.select_one(".prog_quarter")
    if not prog:
        return None

    for th in prog.select("th"):
        text = normalize_text(th.get_text(" ", strip=True))
        m = re.search(r"今期\s*(\d{6})", text)
        if not m:
            continue
        fy = m.group(1)
        if "01" <= fy[4:6] <= "12":
            return fy
    return None


def find_consensus_row(prog) -> Any | None:
    for row in prog.select("tbody tr"):
        header = row.find("th")
        header_text = normalize_text(header.get_text(" ", strip=True) if header else "")
        if "コンセンサス予想" in header_text:
            return row
    return None


def extract_ifis_consensus(html: str) -> tuple[list[dict[str, Any]], str | None, str | None]:
    """IFIS直ページHTMLからコンセンサスを抽出する。

    Returns:
        (records, fy, skip_reason)
    """
    soup = BeautifulSoup(html, "html.parser")
    prog = soup.select_one(".prog_quarter")
    if not prog:
        return [], None, "prog_quarter_not_found"

    fy = extract_current_fy_from_ifis(soup)
    if not fy:
        return [], None, "fy_not_found"

    cons_row = find_consensus_row(prog)
    if cons_row is None:
        return [], fy, "consensus_row_not_found"

    cells = cons_row.select("td")
    quarters = ["1Q", "2Q", "3Q", "FY"]
    records: list[dict[str, Any]] = []
    for quarter, cell in zip(quarters, cells[:4]):
        val = clean_number(cell.get_text())
        if not val:
            continue
        records.append({
            "FY": fy,
            "QUARTER": quarter,
            "PROFIT": int(val),
            "TARGET": "CURRENT",
        })

    if not records:
        return [], fy, "all_consensus_values_empty"
    return records, fy, None


# ==========================================
# メイン処理
# ==========================================


def build_bq_rows(code: str, dataat: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "DATAAT": dataat,
            "TICKER": code,
            "FY": rec["FY"],
            "QUARTER": rec["QUARTER"],
            "PROFIT": rec["PROFIT"],
            "TARGET": rec["TARGET"],
            "SOURCE": SOURCE,
        }
        for rec in records
    ]


def build_csv_row(code: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    row = {k: "" for k in ["TICKER", "1Q_CURRENT", "2Q_CURRENT", "3Q_CURRENT", "FY_CURRENT", "FY_NEXT"]}
    row["TICKER"] = code
    for rec in records:
        key = f"{rec['QUARTER']}_{rec['TARGET']}"
        if key in row:
            row[key] = rec["PROFIT"]
    return row


def process_codes(
    codes: list[str],
    dataat: str,
    bq_client: bigquery.Client | None,
    resume_from: str | None,
    *,
    dry_run: bool = False,
    update_state: bool = True,
) -> Counter:
    skip_until_resume = resume_from is not None
    stats: Counter = Counter()
    session = requests.Session()

    for code in codes:
        code = code.strip()
        if not code:
            continue
        if skip_until_resume:
            if code == resume_from:
                skip_until_resume = False
            continue

        print(f"🔍 処理中: {code}")
        try:
            html = fetch_ifis_page(code, session=session)
        except Exception as e:
            print(f"  ⚠️ HTTP取得失敗: {e}")
            stats["http_error"] += 1
            if update_state:
                save_state(dataat, code)
            time.sleep(REQUEST_INTERVAL_SEC)
            continue

        records, fy, skip_reason = extract_ifis_consensus(html)
        if skip_reason:
            print(f"  ⚠️ skip: {skip_reason} (FY={fy or ''})")
            stats[skip_reason] += 1
            if update_state:
                save_state(dataat, code)
            time.sleep(REQUEST_INTERVAL_SEC)
            continue

        bq_rows = build_bq_rows(code, dataat, records)
        print(f"  ✅ 抽出: FY={fy} {len(bq_rows)}件 {[(r['QUARTER'], r['PROFIT']) for r in bq_rows]}")

        if dry_run:
            stats["dry_run_ok"] += 1
        else:
            if bq_client is None:
                raise ValueError("bq_client is required unless dry_run=True")
            if insert_to_bq(bq_client, bq_rows):
                save_to_csv(build_csv_row(code, records))
                print(f"  ✅ BQ/CSV保存完了: {code}")
                stats["saved"] += 1
            else:
                stats["bq_error"] += 1

        if update_state:
            save_state(dataat, code)
        time.sleep(REQUEST_INTERVAL_SEC)

    return stats


def parse_tickers_arg(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [x.strip().zfill(4) for x in re.split(r"[,;\s]+", value) if x.strip()]


def run(*, fresh: bool = False, ticker_arg: str | None = None, dry_run: bool = False) -> None:
    explicit_tickers = parse_tickers_arg(ticker_arg)
    state = None if fresh or explicit_tickers else load_state()
    dataat = state["dataat"] if state else date.today().strftime("%Y-%m-%d")
    resume_from = state["last_ticker"] if state else None

    bq_client = None if dry_run and explicit_tickers else get_bq_client()
    if explicit_tickers:
        codes = explicit_tickers
        update_state = False
    else:
        if bq_client is None:
            bq_client = get_bq_client()
        codes = get_japanese_stock_tickers(bq_client)
        update_state = True

    if not codes:
        print("❌ 対象銘柄なし")
        return

    print(f"DATAAT={dataat} SOURCE={SOURCE} dry_run={dry_run}")
    stats = process_codes(
        codes,
        dataat,
        bq_client,
        resume_from,
        dry_run=dry_run,
        update_state=update_state,
    )
    print("\n=== 集計 ===")
    for key, value in stats.most_common():
        print(f"{key}: {value}")

    if update_state:
        clear_state()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--ticker", help="単一またはカンマ区切りの銘柄コード。指定時は再開状態を使わない。")
    parser.add_argument("--dry-run", action="store_true", help="BQ/CSVへ保存せず抽出結果だけ表示する。")
    args = parser.parse_args()
    run(fresh=args.fresh, ticker_arg=args.ticker, dry_run=args.dry_run)

"""TOB上場廃止企業の大株主をEDINET有価証券報告書から取得し、アクティビスト判定する.

戦略:
  日付ループ（2014-01-01〜2025-12-31）で EDINET API を1日1回呼び出し、
  全TOB銘柄の有価証券報告書を一括マッチ。
  各社について上場廃止1年前±180日のウィンドウ内の有報を採用。

出力: C:\\tmp\\tob_activist\\tob_shareholders.csv
"""

from __future__ import annotations

import io
import os
import re
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests
import structlog
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- BQ SSL回避（ローカル専用） ---
import urllib3
import requests as _req
from requests.adapters import HTTPAdapter as _HA

urllib3.disable_warnings()


class _NoVerify(_HA):
    def send(self, req, **kw):  # type: ignore[override]
        kw["verify"] = False
        return super().send(req, **kw)


_orig = _req.Session.__init__


def _p(self, *a, **kw):  # type: ignore[no-untyped-def]
    _orig(self, *a, **kw)
    self.mount("https://", _NoVerify())
    self.verify = False


_req.Session.__init__ = _p  # type: ignore[assignment]

from google.cloud import bigquery
from google.oauth2 import service_account

# --- アクティビスト判定ロジック ---
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flag_activists_in_list import load_activists, match_activist

log = structlog.get_logger()

# --- 設定 ---
EDINET_API_KEY = "0607081d467e4c7b8229ac4f41ec3408"
EDINET_BASE_URL = "https://disclosure.edinet-fsa.go.jp/api/v2"
CACHE_DIR = Path(r"C:\tmp\tob_activist")
OUTPUT_CSV = CACHE_DIR / "tob_shareholders.csv"
DELISTED_CACHE = CACHE_DIR / "delisted_tob.csv"
DOCID_CACHE = CACHE_DIR / "docid_map.csv"

# 上場廃止の何日前の有報を狙うか
DAYS_BEFORE_DELIST = 365
# 許容ウィンドウ（±日数）
WINDOW_DAYS = 180

# 日付スキャン範囲
SCAN_START = "2014-01-01"
SCAN_END = "2026-03-31"


def _get_bq_client() -> bigquery.Client:
    """GCP認証済みBQクライアントを返す."""
    key_path = os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS",
        r"C:\gdrive\claude\investment-agent\keys\gcp-service-account.json",
    )
    creds = service_account.Credentials.from_service_account_file(key_path)
    return bigquery.Client(credentials=creds, project=creds.project_id)


def _create_session() -> requests.Session:
    """リトライ付きHTTPセッション."""
    s = requests.Session()
    retries = Retry(
        total=5, backoff_factor=1,
        status_forcelist=[500, 502, 503, 504, 429],
        allowed_methods=["GET"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    return s


SESSION = _create_session()


def _edinet_get(
    url: str, params: dict | None = None, stream: bool = False,
) -> requests.Response | None:
    """EDINET APIリクエスト."""
    p = dict(params or {})
    p["Subscription-Key"] = EDINET_API_KEY
    try:
        r = SESSION.get(url, params=p, timeout=60, stream=stream)
        r.raise_for_status()
        return r
    except Exception as e:
        log.warning("edinet_error", url=url, error=str(e))
        return None


def _fetch_delisted_tob() -> pd.DataFrame:
    """BQからTOB上場廃止銘柄を取得."""
    if DELISTED_CACHE.exists():
        log.info("using_delisted_cache")
        return pd.read_csv(DELISTED_CACHE, parse_dates=["DELISTING_DATE"])

    client = _get_bq_client()
    query = """
        SELECT TICKER, COMPANY_NAME, DELISTING_DATE
        FROM `gmailpj-357912.STOCK.DELISTED_STOCKS`
        WHERE IS_TOB_MBO = TRUE
        ORDER BY DELISTING_DATE DESC
    """
    df = client.query(query).to_dataframe()
    df["TICKER"] = df["TICKER"].astype(str).str.zfill(4)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(DELISTED_CACHE, index=False)
    log.info("delisted_fetched", count=len(df))
    return df


# =====================================================
# Phase 1: 日付スキャンで docID を収集
# =====================================================

def phase1_collect_docids(df_tob: pd.DataFrame) -> pd.DataFrame:
    """EDINET APIを日付ループで呼び、TOB銘柄の有報docIDを収集."""
    # 各銘柄の検索ウィンドウを算出
    ticker_windows: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}
    for _, row in df_tob.iterrows():
        t = row["TICKER"]
        dl = pd.Timestamp(row["DELISTING_DATE"])
        center = dl - pd.Timedelta(days=DAYS_BEFORE_DELIST)
        ticker_windows[t] = (
            center - pd.Timedelta(days=WINDOW_DAYS),
            center + pd.Timedelta(days=WINDOW_DAYS),
        )

    # キャッシュ済みなら読む
    if DOCID_CACHE.exists():
        log.info("using_docid_cache")
        return pd.read_csv(DOCID_CACHE, dtype=str)

    # 必要な日付範囲を算出
    all_tickers = set(ticker_windows.keys())
    found: dict[str, dict] = {}  # ticker → {docID, date, filerName, ...}

    dates = pd.date_range(SCAN_START, SCAN_END, freq="D")
    log.info("phase1_start", total_days=len(dates), tickers=len(all_tickers))

    remaining = set(all_tickers)

    for i, dt in enumerate(dates):
        # この日がどの銘柄のウィンドウ内か
        dt_ts = pd.Timestamp(dt)
        candidates = {
            t for t in remaining
            if ticker_windows[t][0] <= dt_ts <= ticker_windows[t][1]
        }
        if not candidates:
            continue

        date_str = dt.strftime("%Y-%m-%d")
        resp = _edinet_get(f"{EDINET_BASE_URL}/documents.json", {"date": date_str, "type": 2})
        if resp is None:
            time.sleep(0.2)
            continue

        try:
            data = resp.json()
        except Exception:
            time.sleep(0.2)
            continue

        for doc in data.get("results", []):
            if doc.get("docTypeCode") != "120":
                continue
            title = doc.get("docDescription", "")
            if "訂正" in title:
                continue
            sec_code = str(doc.get("secCode", ""))
            if len(sec_code) < 4:
                continue
            ticker4 = sec_code[:4]
            if ticker4 in candidates and ticker4 not in found:
                found[ticker4] = {
                    "TICKER": ticker4,
                    "DOC_ID": doc["docID"],
                    "DOC_DATE": date_str,
                    "FILER_NAME": doc.get("filerName", ""),
                }
                remaining.discard(ticker4)
                log.info("docid_found", ticker=ticker4, doc_id=doc["docID"],
                         date=date_str, remaining=len(remaining))

        if (i + 1) % 100 == 0:
            log.info("phase1_progress", day=date_str, found=len(found),
                     remaining=len(remaining))

        time.sleep(0.1)

    df_docids = pd.DataFrame(list(found.values()))
    df_docids.to_csv(DOCID_CACHE, index=False)
    log.info("phase1_done", found=len(df_docids), not_found=len(remaining))

    if remaining:
        log.warning("tickers_without_report", tickers=sorted(remaining)[:20])

    return df_docids


# =====================================================
# Phase 2: XBRL ダウンロード → 大株主抽出
# =====================================================

def _extract_shareholders_from_xbrl(doc_id: str) -> list[str]:
    """XBRL ZIPから大株主テキストブロックを抽出."""
    url = f"{EDINET_BASE_URL}/documents/{doc_id}"
    resp = _edinet_get(url, {"type": 1}, stream=True)
    if resp is None:
        return []

    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
    except Exception as e:
        log.warning("zip_error", doc_id=doc_id, error=str(e))
        return []

    # PublicDoc配下の .xbrl または .htm
    xbrl_files = [n for n in zf.namelist()
                  if "PublicDoc" in n and (n.endswith(".xbrl") or n.endswith(".htm"))]

    for xf in xbrl_files:
        content = zf.read(xf).decode("utf-8", errors="replace")
        soup = BeautifulSoup(content, "html.parser")

        # 方法1: XBRLタグ名で検索
        for tag_name in ("MajorShareholdersTextBlock",):
            elems = soup.find_all(attrs={"name": re.compile(tag_name, re.IGNORECASE)})
            if not elems:
                elems = soup.find_all(re.compile(tag_name, re.IGNORECASE))
            for elem in elems:
                names = _parse_shareholder_html(str(elem))
                if names:
                    return list(dict.fromkeys(names))

        # 方法2: セクション見出しで検索
        for heading in soup.find_all(string=re.compile(r"大株主.*状況")):
            # 見出し以降のテーブルを探す
            node = heading.find_parent()
            if node:
                table = node.find_next("table")
                if table:
                    names = _parse_shareholder_table(table)
                    if names:
                        return list(dict.fromkeys(names))

    return []


def _parse_shareholder_html(html: str) -> list[str]:
    """大株主テキストブロックHTMLから株主名を抽出."""
    soup = BeautifulSoup(html, "html.parser")
    names: list[str] = []
    for table in soup.find_all("table"):
        names.extend(_parse_shareholder_table(table))
    return names


def _parse_shareholder_table(table) -> list[str]:
    """テーブル要素から株主名を抽出."""
    names: list[str] = []
    rows = table.find_all("tr")

    # ヘッダー行を探す
    header_idx = -1
    name_col = 0
    for i, row in enumerate(rows):
        cells = row.find_all(["th", "td"])
        for j, c in enumerate(cells):
            t = c.get_text(strip=True)
            if "氏名" in t or "名称" in t:
                header_idx = i
                name_col = j
                break
        if header_idx >= 0:
            break

    start = header_idx + 1 if header_idx >= 0 else 0
    for row in rows[start:]:
        cells = row.find_all(["th", "td"])
        if len(cells) <= name_col:
            continue
        name = cells[name_col].get_text(strip=True)
        if not name or len(name) < 2:
            continue
        if name.replace(",", "").replace(".", "").isdigit():
            continue
        if name in ("計", "合計", "―", "－", "-", "所有株式数", "発行済株式"):
            continue
        names.append(name)

    return names


# =====================================================
# Phase 3: アクティビスト判定
# =====================================================

def phase2_and_3(df_tob: pd.DataFrame, df_docids: pd.DataFrame) -> pd.DataFrame:
    """大株主抽出 + アクティビスト判定."""
    activists = load_activists()
    log.info("activists_loaded", count=len(activists))

    # TOB情報をマージ
    df_work = df_tob[["TICKER", "COMPANY_NAME", "DELISTING_DATE"]].merge(
        df_docids[["TICKER", "DOC_ID", "DOC_DATE"]], on="TICKER", how="left",
    )

    # 中断再開用キャッシュ
    sh_cache = CACHE_DIR / "shareholders_raw.csv"
    done_tickers: set[str] = set()
    results: list[dict] = []
    if sh_cache.exists():
        df_sh = pd.read_csv(sh_cache, dtype=str)
        done_tickers = set(df_sh["TICKER"])
        results = df_sh.to_dict("records")
        log.info("phase2_resuming", done=len(done_tickers))

    total = len(df_work)
    for idx, row in df_work.iterrows():
        ticker = row["TICKER"]
        if ticker in done_tickers:
            continue

        doc_id = row.get("DOC_ID")
        shareholders: list[str] = []
        has_activist = False
        activist_names: list[str] = []

        if pd.notna(doc_id) and doc_id:
            shareholders = _extract_shareholders_from_xbrl(doc_id)
            for sh in shareholders:
                m = match_activist(sh, activists)
                if m:
                    has_activist = True
                    activist_names.append(m["name"])
            time.sleep(0.15)

        result = {
            "TICKER": ticker,
            "COMPANY_NAME": row["COMPANY_NAME"],
            "DELISTING_DATE": str(row["DELISTING_DATE"])[:10],
            "DOC_ID": doc_id if pd.notna(doc_id) else "",
            "DOC_DATE": row.get("DOC_DATE", ""),
            "SHAREHOLDER_COUNT": str(len(shareholders)),
            "HAS_ACTIVIST": str(has_activist),
            "ACTIVIST_NAMES": "|".join(dict.fromkeys(activist_names)),
            "SHAREHOLDERS": "|".join(shareholders[:10]),
        }
        results.append(result)
        done_tickers.add(ticker)

        progress = len(done_tickers)
        if progress % 20 == 0:
            pd.DataFrame(results).to_csv(sh_cache, index=False, encoding="utf-8-sig")
            log.info("phase2_progress", done=progress, total=total)

    df_result = pd.DataFrame(results)
    df_result.to_csv(sh_cache, index=False, encoding="utf-8-sig")
    return df_result


def main() -> None:
    """メイン処理."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # 1. TOB上場廃止銘柄を取得
    df_tob = _fetch_delisted_tob()
    log.info("tob_companies", total=len(df_tob))

    # Phase 1: docID収集（日付スキャン）
    df_docids = phase1_collect_docids(df_tob)
    log.info("docids_collected", found=len(df_docids))

    # Phase 2+3: 大株主抽出 + アクティビスト判定
    df_result = phase2_and_3(df_tob, df_docids)

    # 最終出力
    df_result.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    # サマリ
    total = len(df_result)
    found = (df_result["DOC_ID"].astype(str) != "").sum()
    has_sh = (df_result["SHAREHOLDER_COUNT"].astype(int) > 0).sum()
    activist_count = (df_result["HAS_ACTIVIST"].astype(str).str.lower() == "true").sum()

    print(f"\n{'='*60}")
    print("TOB上場廃止企業 アクティビスト株主調査")
    print(f"{'='*60}")
    print(f"対象企業数:        {total}")
    print(f"有報発見:          {found} ({found/total*100:.1f}%)")
    print(f"大株主抽出成功:    {has_sh} ({has_sh/total*100:.1f}%)")
    print(f"アクティビスト保有: {activist_count} ({activist_count/total*100:.1f}%)")
    print(f"\n出力: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()

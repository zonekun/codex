"""遅延報告イベント銘柄の大株主をEDINET有価証券報告書から取得し、アクティビスト判定する.

fetch_tob_shareholders.py と同じロジックだが、対象が遅延報告の全銘柄。
TOB済み銘柄は既存キャッシュを流用し、新規分のみEDINET APIで取得。

出力: C:\\tmp\\tob_activist\\delay_event_shareholders.csv
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

# --- アクティビスト判定ロジック ---
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flag_activists_in_list import load_activists, match_activist

log = structlog.get_logger()

# --- 設定 ---
EDINET_API_KEY = "0607081d467e4c7b8229ac4f41ec3408"
EDINET_BASE_URL = "https://disclosure.edinet-fsa.go.jp/api/v2"
CACHE_DIR = Path(r"C:\tmp\tob_activist")
OUTPUT_CSV = CACHE_DIR / "delay_event_shareholders.csv"
EXCEL_PATH = Path(r"C:\Users\zonekun\Dropbox\stock\AI分析優待\Edinet遅延.xlsx")

# 遅延報告の提出日を基準に、1年前の有報を探す
# 銘柄ごとに最初の提出日を使う
DAYS_BEFORE_EVENT = 365
WINDOW_DAYS = 180

# 日付スキャン範囲（遅延報告が2002年〜だがEDINETは2014年以降が安定）
SCAN_START = "2014-01-01"
SCAN_END = "2026-03-31"


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


def _load_event_tickers() -> pd.DataFrame:
    """遅延報告Excelから銘柄×最初の提出日を取得."""
    df = pd.read_excel(EXCEL_PATH, sheet_name="MAIN", header=None)
    df.columns = ["TICKER", "COMPANY", "REPORTER", "OBLIGATION_DATE",
                   "FILING_DATE", "DELAY_DAYS_STR"]
    df["TICKER"] = df["TICKER"].astype(str).str.zfill(4)
    df["FILING_DATE"] = pd.to_datetime(df["FILING_DATE"], errors="coerce")
    df = df.dropna(subset=["FILING_DATE"])

    # 銘柄ごとに最初の提出日を取得（有報検索の基準日）
    first_filing = df.groupby("TICKER").agg(
        COMPANY=("COMPANY", "first"),
        FIRST_FILING=("FILING_DATE", "min"),
    ).reset_index()
    return first_filing


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


def phase1_collect_docids(df_tickers: pd.DataFrame) -> pd.DataFrame:
    """日付ループでdocID収集."""
    docid_cache = CACHE_DIR / "docid_map_delay.csv"

    # 既存TOBキャッシュから流用
    tob_cache = CACHE_DIR / "docid_map.csv"
    existing: dict[str, dict] = {}
    if tob_cache.exists():
        df_tob = pd.read_csv(tob_cache, dtype=str)
        for _, r in df_tob.iterrows():
            existing[r["TICKER"]] = r.to_dict()
        log.info("reusing_tob_docids", count=len(existing))

    if docid_cache.exists():
        log.info("using_docid_cache_delay")
        return pd.read_csv(docid_cache, dtype=str)

    # 各銘柄のウィンドウを算出（最初の提出日の1年前 ± 180日）
    ticker_windows: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}
    for _, row in df_tickers.iterrows():
        t = row["TICKER"]
        if t in existing:
            continue  # TOBキャッシュにある
        first = pd.Timestamp(row["FIRST_FILING"])
        center = first - pd.Timedelta(days=DAYS_BEFORE_EVENT)
        ticker_windows[t] = (
            center - pd.Timedelta(days=WINDOW_DAYS),
            center + pd.Timedelta(days=WINDOW_DAYS),
        )

    remaining = set(ticker_windows.keys())
    found: dict[str, dict] = {}

    dates = pd.date_range(SCAN_START, SCAN_END, freq="D")
    log.info("phase1_start", total_days=len(dates), new_tickers=len(remaining),
             reused=len(existing))

    for i, dt in enumerate(dates):
        dt_ts = pd.Timestamp(dt)
        candidates = {
            t for t in remaining
            if ticker_windows[t][0] <= dt_ts <= ticker_windows[t][1]
        }
        if not candidates:
            continue

        date_str = dt.strftime("%Y-%m-%d")
        resp = _edinet_get(f"{EDINET_BASE_URL}/documents.json",
                           {"date": date_str, "type": 2})
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

    # TOBキャッシュ分とマージ
    all_found = list(existing.values()) + list(found.values())
    df_docids = pd.DataFrame(all_found)
    df_docids.to_csv(docid_cache, index=False)
    log.info("phase1_done", total=len(df_docids), new=len(found),
             not_found=len(remaining))

    if remaining:
        log.warning("tickers_without_report",
                    count=len(remaining),
                    sample=sorted(remaining)[:20])

    return df_docids


def phase2_extract_and_judge(
    df_tickers: pd.DataFrame, df_docids: pd.DataFrame,
) -> pd.DataFrame:
    """大株主抽出 + アクティビスト判定."""
    activists = load_activists()
    log.info("activists_loaded", count=len(activists))

    # TOB済みの結果を流用
    tob_sh_cache = CACHE_DIR / "shareholders_raw.csv"
    tob_results: dict[str, dict] = {}
    if tob_sh_cache.exists():
        df_tob_sh = pd.read_csv(tob_sh_cache, dtype=str)
        for _, r in df_tob_sh.iterrows():
            tob_results[r["TICKER"]] = r.to_dict()
        log.info("reusing_tob_shareholders", count=len(tob_results))

    df_work = df_tickers[["TICKER", "COMPANY"]].merge(
        df_docids[["TICKER", "DOC_ID", "DOC_DATE"]], on="TICKER", how="left",
    )

    # 中断再開用キャッシュ
    sh_cache = CACHE_DIR / "shareholders_delay_raw.csv"
    done_tickers: set[str] = set()
    results: list[dict] = []
    if sh_cache.exists():
        df_sh = pd.read_csv(sh_cache, dtype=str)
        done_tickers = set(df_sh["TICKER"])
        results = df_sh.to_dict("records")
        log.info("phase2_resuming", done=len(done_tickers))

    total = len(df_work)
    for _, row in df_work.iterrows():
        ticker = row["TICKER"]
        if ticker in done_tickers:
            continue

        # TOBキャッシュにあればそのまま使う
        if ticker in tob_results:
            results.append(tob_results[ticker])
            done_tickers.add(ticker)
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
            "COMPANY_NAME": row["COMPANY"],
            "DOC_ID": doc_id if pd.notna(doc_id) else "",
            "DOC_DATE": row.get("DOC_DATE", "") if pd.notna(row.get("DOC_DATE", "")) else "",
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

    # 遅延報告イベント銘柄
    df_tickers = _load_event_tickers()
    log.info("event_tickers", total=len(df_tickers))

    # Phase 1: docID収集
    df_docids = phase1_collect_docids(df_tickers)
    log.info("docids_collected", found=len(df_docids))

    # Phase 2: 大株主抽出 + アクティビスト判定
    df_result = phase2_extract_and_judge(df_tickers, df_docids)

    # 最終出力
    df_result.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    # サマリ
    total = len(df_result)
    found = (df_result["DOC_ID"].astype(str) != "").sum()
    has_sh = (df_result["SHAREHOLDER_COUNT"].astype(int) > 0).sum()
    activist_count = (df_result["HAS_ACTIVIST"].astype(str).str.lower() == "true").sum()

    print(f"\n{'='*60}")
    print("遅延報告イベント銘柄 アクティビスト株主調査")
    print(f"{'='*60}")
    print(f"対象銘柄数:        {total}")
    print(f"有報発見:          {found} ({found/total*100:.1f}%)")
    print(f"大株主抽出成功:    {has_sh} ({has_sh/total*100:.1f}%)")
    print(f"アクティビスト保有: {activist_count} ({activist_count/total*100:.1f}%)")
    print(f"\n出力: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()

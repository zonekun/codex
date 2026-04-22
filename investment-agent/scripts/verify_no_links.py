"""no_links カテゴリの SKIP 企業を実際にアクセスして検証するスクリプト。

GCS から skip=True のアダプターを全件取得し、
ir_url.json の monthly_page_url にアクセスして
実際にダウンロードリンクが存在するかを確認する。

使い方:
    PYTHONUTF8=1 python scripts/verify_no_links.py
    PYTHONUTF8=1 python scripts/verify_no_links.py --category no_links
    PYTHONUTF8=1 python scripts/verify_no_links.py --tickers 1234 5678
    PYTHONUTF8=1 python scripts/verify_no_links.py --show-all    # skip=False も含む全件
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from google.cloud import storage
from google.oauth2 import service_account

# ==========================================
# 設定
# ==========================================
GCS_BUCKET = "stock_data_1930932"
KEY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "keys", "gcp-service-account.json")
RATE_SEC = 1.5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9",
}

DOWNLOAD_EXT = re.compile(r"\.(pdf|xlsx|xls|csv)(\?.*)?$", re.IGNORECASE)
TABLE_KEYWORDS = re.compile(r"月次|sales|月次開示|月次売上|月次販売", re.IGNORECASE)


def _apply_ssl_patch():
    import urllib3
    from requests.adapters import HTTPAdapter
    urllib3.disable_warnings()

    class _NoVerify(HTTPAdapter):
        def send(self, req, **kw):
            kw["verify"] = False
            return super().send(req, **kw)

    _orig = requests.Session.__init__

    def _patched(self, *a, **kw):
        _orig(self, *a, **kw)
        self.mount("https://", _NoVerify())
        self.verify = False

    requests.Session.__init__ = _patched


_apply_ssl_patch()


def _get_gcs_client():
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return storage.Client(project="gmailpj-357912", credentials=creds)


def _classify_note(note: str, monthly_page_url: str) -> str:
    """skip 理由を分類する。"""
    if not note:
        return "unknown"
    note_lower = note.lower()

    # ir_url が support.google.com になっている = Botブロック
    if monthly_page_url and "support.google.com" in monthly_page_url:
        return "bot_blocked"

    # monthly_page_url が PDF / Excel 直接
    if monthly_page_url and DOWNLOAD_EXT.search(monthly_page_url):
        return "direct_file"

    if "eir" in note_lower or "spa" in note_lower or "javascript" in note_lower:
        return "eir_spa"

    if "リンクなし" in note or "no link" in note_lower or "0件" in note:
        return "no_links"

    if "table" in note_lower or "テーブル" in note:
        return "table_only"

    if "not_found" in note_lower or "404" in note or "not found" in note_lower:
        return "not_found"

    return "other"


def _fetch_page(url: str, session: requests.Session) -> tuple[int, str]:
    """ページを取得して (status_code, html) を返す。"""
    try:
        resp = session.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        return resp.status_code, resp.text
    except Exception as e:
        return 0, str(e)


def _analyze_page(url: str, html: str) -> dict:
    """ページを解析してリンク情報を返す。"""
    soup = BeautifulSoup(html, "html.parser")
    all_links = soup.find_all("a", href=True)

    download_links = []
    for a in all_links:
        href = a.get("href", "")
        abs_url = urljoin(url, href)
        if DOWNLOAD_EXT.search(abs_url):
            download_links.append({
                "text": a.get_text(strip=True)[:60],
                "url": abs_url,
            })

    # テーブル内テキストに月次キーワードが含まれるか
    tables = soup.find_all("table")
    table_monthly = False
    table_text_sample = ""
    for tbl in tables:
        text = tbl.get_text(" ", strip=True)
        if TABLE_KEYWORDS.search(text):
            table_monthly = True
            table_text_sample = text[:200]
            break

    # PDF/Excel iframe や embed
    iframes = [f.get("src", "") for f in soup.find_all(["iframe", "embed"])
               if DOWNLOAD_EXT.search(f.get("src", ""))]

    return {
        "download_links_count": len(download_links),
        "download_links": download_links[:5],  # 最大5件
        "table_monthly": table_monthly,
        "table_text_sample": table_text_sample[:100] if table_text_sample else "",
        "iframe_links": iframes[:3],
    }


def main():
    parser = argparse.ArgumentParser(description="no_links SKIP 企業の検証")
    parser.add_argument("--category", choices=["no_links", "bot_blocked", "direct_file",
                                               "eir_spa", "table_only", "not_found", "other", "all"],
                        default="no_links", help="対象カテゴリ")
    parser.add_argument("--tickers", nargs="*", help="特定ティッカーのみ")
    parser.add_argument("--show-all", action="store_true", help="skip=False も含む全件表示のみ（アクセスなし）")
    parser.add_argument("--no-access", action="store_true", help="ページアクセスをスキップして一覧のみ表示")
    args = parser.parse_args()

    print("GCS アダプター読み込み中...")
    gcs = _get_gcs_client()
    bucket = gcs.bucket(GCS_BUCKET)

    blobs = list(bucket.list_blobs(prefix="monthlydata/"))
    adapter_blobs = [b for b in blobs if b.name.endswith("download_adapter.json")]
    print(f"  アダプター総数: {len(adapter_blobs)}")

    # アダプター + ir_url を読み込む
    records = []
    for blob in adapter_blobs:
        try:
            adapter = json.loads(blob.download_as_text())
        except Exception:
            continue

        ticker = adapter.get("ticker", "")

        if args.tickers and ticker not in args.tickers:
            continue

        if not args.show_all and not adapter.get("skip", False):
            continue

        # ir_url.json 読み込み
        ir_blob = bucket.blob(f"monthlydata/{ticker}/ir_url.json")
        monthly_page_url = ""
        if ir_blob.exists():
            try:
                ir_data = json.loads(ir_blob.download_as_text())
                monthly_page_url = ir_data.get("monthly_page_url", "")
            except Exception:
                pass

        note = adapter.get("note", "")
        category = _classify_note(note, monthly_page_url)

        records.append({
            "ticker": ticker,
            "company_name": adapter.get("company_name", ""),
            "skip": adapter.get("skip", False),
            "note": note,
            "monthly_page_url": monthly_page_url,
            "category": category,
        })

    print(f"  対象レコード: {len(records)} 件")

    # カテゴリ別集計
    from collections import Counter
    cat_counts = Counter(r["category"] for r in records)
    print("\n=== カテゴリ別集計 ===")
    for cat, cnt in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat:20s}: {cnt:4d} 社")

    # フィルタリング
    if args.category != "all":
        target = [r for r in records if r["category"] == args.category]
    else:
        target = records

    print(f"\n=== 対象: {args.category} ({len(target)}社) ===")

    if args.no_access or args.show_all:
        # 一覧のみ表示
        for r in sorted(target, key=lambda x: x["ticker"]):
            print(f"  {r['ticker']:6s}  {r['company_name']:30s}  {r['monthly_page_url'][:70]}")
            if r["note"]:
                print(f"         note: {r['note'][:80]}")
        return

    # ページアクセス検証
    session = requests.Session()
    results = []

    for i, r in enumerate(sorted(target, key=lambda x: x["ticker"]), 1):
        ticker = r["ticker"]
        company = r["company_name"]
        url = r["monthly_page_url"]

        if not url:
            print(f"[{i:3d}/{len(target)}] {ticker} {company:25s} → URL なし")
            results.append({**r, "status": 0, "result": "url_missing"})
            continue

        print(f"[{i:3d}/{len(target)}] {ticker} {company:25s}  {url[:60]}", end="", flush=True)

        status, html = _fetch_page(url, session)

        if status == 0:
            print(f" → 接続失敗: {html[:60]}")
            results.append({**r, "status": 0, "result": "connection_error", "error": html[:100]})
        elif status != 200:
            print(f" → HTTP {status}")
            results.append({**r, "status": status, "result": f"http_{status}"})
        else:
            analysis = _analyze_page(url, html)
            dl_count = analysis["download_links_count"]
            has_table = analysis["table_monthly"]
            has_iframe = bool(analysis["iframe_links"])

            if dl_count > 0:
                status_str = f"✓ DLリンク {dl_count}件"
                verdict = "links_found"
            elif has_table:
                status_str = "△ テーブルあり（動的コンテンツ）"
                verdict = "table_only"
            elif has_iframe:
                status_str = f"△ iframe PDF {len(analysis['iframe_links'])}件"
                verdict = "iframe"
            else:
                status_str = "× リンクなし確認"
                verdict = "confirmed_no_links"

            print(f" → {status_str}")

            if dl_count > 0:
                for lnk in analysis["download_links"][:3]:
                    print(f"       → [{lnk['text']}] {lnk['url'][:80]}")

            if has_table and analysis["table_text_sample"]:
                print(f"       テーブル: {analysis['table_text_sample'][:80]}")

            results.append({**r, "status": status, "result": verdict, **analysis})

        time.sleep(RATE_SEC)

    # サマリー
    print("\n=== 検証結果サマリー ===")
    result_counts = Counter(r.get("result", "?") for r in results)
    for res, cnt in sorted(result_counts.items(), key=lambda x: -x[1]):
        print(f"  {res:30s}: {cnt:3d} 社")

    # リンク発見された企業のリスト
    found = [r for r in results if r.get("result") == "links_found"]
    if found:
        print(f"\n=== DLリンク発見 ({len(found)}社) ===")
        print("  → アダプター修正で取得可能")
        for r in found:
            print(f"  {r['ticker']:6s}  {r['company_name']:30s}  DL:{r['download_links_count']}件  {r['monthly_page_url'][:60]}")
            for lnk in r.get("download_links", [])[:2]:
                print(f"         {lnk['url'][:80]}")

    # テーブル / iframe のみ
    table_found = [r for r in results if r.get("result") in ("table_only", "iframe")]
    if table_found:
        print(f"\n=== テーブル/iframe（動的）({len(table_found)}社) ===")
        for r in table_found:
            print(f"  {r['ticker']:6s}  {r['company_name']:30s}  {r['monthly_page_url'][:60]}")


if __name__ == "__main__":
    main()

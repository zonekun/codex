#!/usr/bin/env python3
"""
investigate_adapters.py
失敗中の 120 ticker の IR ページを Playwright で調査し、
月次PDFリンクのパターンを特定して adapter.json を修正する。

Usage:
    uv run python scripts/investigate_adapters.py [--start N] [--end N]
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Optional
import argparse

from playwright.async_api import async_playwright, BrowserContext, Page
from google.cloud import storage
from google.oauth2 import service_account

# ============================================================
# 設定
# ============================================================
KEY_FILE = str(Path(__file__).parent.parent / "keys" / "gcp-service-account.json")
BUCKET_NAME = "stock_data_1930932"
RESULTS_FILE = str(Path(__file__).parent.parent / "data" / "investigate_results.json")
ADAPTERS_FILE = str(Path(__file__).parent.parent / "data" / "failing_adapters.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/investigate_adapters.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# 月次キーワード (EIR_MONTHLY_RE と同等)
MONTHLY_RE = re.compile(
    r"月次|月度|monthly|売上速報|売上高|月別|受注速報|受注実績|販売台数|"
    r"輸送実績|旅客数|搭乗実績|稼働実績|出荷量|KPI|Net Sales|業績速報|"
    r"月次業績|前年比|速報|月次情報|月次データ",
    re.IGNORECASE,
)
PDF_RE = re.compile(r"\.(pdf|xlsx|xls|csv)$", re.IGNORECASE)
EIR_API_BASE = "https://ssl4.eir-parts.net/EIR/View.aspx?cat={cat}&code={code}&sid=2"

# ============================================================
# GCS クライアント
# ============================================================
def get_gcs():
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return storage.Client(credentials=creds, project="gmailpj-357912")


def gcs_write_json(blob_path: str, data: dict):
    client = get_gcs()
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(blob_path)
    blob.upload_from_string(
        json.dumps(data, ensure_ascii=False, indent=2),
        content_type="application/json",
    )


def gcs_read_json(blob_path: str) -> dict:
    client = get_gcs()
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(blob_path)
    return json.loads(blob.download_as_text())


# ============================================================
# リンク収集ヘルパー
# ============================================================
def score_link(text: str, href: str) -> int:
    """月次関連リンクのスコアを返す（高いほど月次らしい）"""
    score = 0
    combined = (text + " " + href).lower()
    if MONTHLY_RE.search(combined):
        score += 10
    if PDF_RE.search(href):
        score += 5
    if re.search(r"\d{4}年?\d{1,2}月", combined):
        score += 3
    if re.search(r"monthly|month", combined, re.IGNORECASE):
        score += 3
    return score


async def collect_links_requests(url: str) -> list[dict]:
    """requests + BeautifulSoup で静的リンク収集"""
    import requests
    from bs4 import BeautifulSoup
    try:
        r = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"
        })
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)[:100]
            if not href.startswith("http"):
                from urllib.parse import urljoin
                href = urljoin(url, href)
            links.append({"text": text, "href": href, "score": score_link(text, href)})
        return sorted(links, key=lambda x: -x["score"])
    except Exception as e:
        return [{"error": str(e)}]


async def collect_links_playwright(page: Page, url: str) -> list[dict]:
    """Playwright でJS込みのリンク収集"""
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)
        links = await page.evaluate("""
            () => {
                return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                    text: (a.textContent || '').trim().substring(0, 100),
                    href: a.href
                }));
            }
        """)
        for lnk in links:
            lnk["score"] = score_link(lnk.get("text",""), lnk.get("href",""))
        return sorted(links, key=lambda x: -x["score"])
    except Exception as e:
        return [{"error": str(e)}]


async def check_eir_api(ticker: str, category: str, page_num: int) -> list[dict]:
    """EIR API から文書リストを取得"""
    import requests
    if not category:
        # カテゴリ不明 → new_release と announcement を試す
        categories = ["new_release", "announcement", "ir_material_for_fiscal_ym"]
    else:
        categories = [category]

    results = []
    for cat in categories:
        try:
            # eIR の RSS/API エンドポイント
            api_url = f"https://ssl4.eir-parts.net/EIR/View.aspx?cat={cat}&code={ticker}&sid=2"
            r = requests.get(api_url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"
            })
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, "lxml")
            rows = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(strip=True)[:100]
                if "eir-parts.net" in href or "ssl4" in href:
                    rows.append({"text": text, "href": href, "score": score_link(text, href), "cat": cat})
            results.extend(rows)
        except Exception as e:
            results.append({"error": str(e), "cat": cat})

    return sorted(results, key=lambda x: -x.get("score", 0))


def infer_patterns(links: list[dict], ticker: str) -> dict:
    """
    リンクリストから link_text_pattern / link_href_pattern を推定する
    Returns: {"link_text_pattern": ..., "link_href_pattern": ..., "reason": ...}
    """
    monthly_links = [l for l in links if l.get("score", 0) >= 5 and not l.get("error")]
    if not monthly_links:
        monthly_links = [l for l in links if l.get("score", 0) >= 3 and not l.get("error")]

    if not monthly_links:
        return {"link_text_pattern": None, "link_href_pattern": None, "reason": "月次リンクなし"}

    # href のパターンを推定
    hrefs = [l["href"] for l in monthly_links[:10]]
    texts = [l["text"] for l in monthly_links[:10] if l.get("text")]

    # 共通パスを抽出
    from urllib.parse import urlparse
    paths = [urlparse(h).path for h in hrefs if h.startswith("http")]

    # テキストからパターン
    text_pattern = None
    if texts:
        # 月次キーワードが多いなら
        monthly_texts = [t for t in texts if MONTHLY_RE.search(t)]
        if monthly_texts:
            # 共通キーワードを抽出
            keywords = []
            for kw in ["月次", "月報", "月別", "売上速報", "受注", "輸送実績", "速報", "monthly"]:
                if any(kw in t for t in monthly_texts):
                    keywords.append(kw)
            if keywords:
                text_pattern = "|".join(keywords[:3])

    # hrefからパターン
    href_pattern = None
    if paths:
        # PDF/Excel
        pdf_paths = [p for p in paths if PDF_RE.search(p)]
        if pdf_paths:
            # 共通プレフィックスを見つける
            if len(pdf_paths) >= 2:
                common = os.path.commonprefix(pdf_paths)
                if len(common) > 3:
                    ext = re.search(r"\.(pdf|xlsx|xls)", pdf_paths[0], re.I)
                    ext_str = ext.group(0) if ext else "\\.(pdf|xlsx|xls)"
                    href_pattern = re.escape(common) + ".*" + re.escape(ext_str)
                else:
                    href_pattern = ".*\\.(pdf|xlsx|xls)"
            else:
                href_pattern = ".*\\.(pdf|xlsx|xls)"

    return {
        "link_text_pattern": text_pattern,
        "link_href_pattern": href_pattern,
        "top_links": monthly_links[:5],
        "reason": f"月次候補{len(monthly_links)}件から推定",
    }


# ============================================================
# メイン調査ループ
# ============================================================
async def investigate_ticker(
    ticker: str,
    adapter: dict,
    page: Page,
    results: dict,
) -> dict:
    """1 ticker を調査して結果を返す"""
    company = adapter.get("company_name", ticker)
    typ = adapter.get("type", "scrape_links")
    ir_url = adapter.get("ir_page_url", "")

    logger.info(f"[{ticker}] {company} ({typ}) url={ir_url}")

    result = {
        "ticker": ticker,
        "company": company,
        "type": typ,
        "ir_url": ir_url,
        "links_found": 0,
        "top_monthly_links": [],
        "inferred": {},
        "fix": {},
        "error": None,
    }

    try:
        if typ == "eir_api":
            cat = adapter.get("eir_category", "")
            pg = adapter.get("eir_page", "")
            links = await check_eir_api(ticker, cat, pg)
            result["links_found"] = len([l for l in links if not l.get("error")])
            result["top_monthly_links"] = [l for l in links if l.get("score", 0) >= 5][:10]
            inferred = infer_patterns(links, ticker)
            result["inferred"] = inferred

            # eir_api の修正提案
            fix = {}
            if not adapter.get("link_text_pattern") and inferred.get("link_text_pattern"):
                fix["link_text_pattern"] = inferred["link_text_pattern"]
            if not adapter.get("eir_category") and links:
                # 最初のカテゴリを使う
                cats_found = list({l.get("cat") for l in links if l.get("cat") and l.get("score", 0) >= 5})
                if cats_found:
                    fix["eir_category"] = cats_found[0]
            result["fix"] = fix

        elif typ in ("scrape_links", "html_table"):
            # まず requests で試す
            links = await collect_links_requests(ir_url)
            has_error = any(l.get("error") for l in links)

            # エラーまたはリンクが少ない場合は Playwright
            monthly_count = len([l for l in links if l.get("score", 0) >= 5])
            if has_error or monthly_count == 0:
                logger.info(f"  [{ticker}] Playwright fallback")
                links = await collect_links_playwright(page, ir_url)
                result["used_playwright"] = True

            result["links_found"] = len([l for l in links if not l.get("error")])
            result["top_monthly_links"] = [l for l in links if l.get("score", 0) >= 5][:10]
            inferred = infer_patterns(links, ticker)
            result["inferred"] = inferred

            # scrape_links の修正提案
            fix = {}
            current_ltp = adapter.get("link_text_pattern")
            current_lhp = adapter.get("link_href_pattern")

            if not current_ltp and inferred.get("link_text_pattern"):
                fix["link_text_pattern"] = inferred["link_text_pattern"]
            if not current_lhp and inferred.get("link_href_pattern"):
                fix["link_href_pattern"] = inferred["link_href_pattern"]

            # リンクが全く見つからない場合 → playwright_required
            if result["links_found"] == 0 and not result.get("used_playwright"):
                fix["playwright_required"] = True
            elif result["links_found"] == 0 and result.get("used_playwright"):
                fix["_note"] = "Playwright でもリンク0 → ページ構造変更またはログイン必要の可能性"

            result["fix"] = fix

    except Exception as e:
        result["error"] = traceback.format_exc()
        logger.error(f"  [{ticker}] ERROR: {e}")

    return result


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=9999)
    parser.add_argument("--tickers", nargs="*", help="特定tickerのみ調査")
    args = parser.parse_args()

    # adapter.json 読み込み
    with open(ADAPTERS_FILE, encoding="utf-8") as f:
        all_adapters = json.load(f)

    if args.tickers:
        target = {t: all_adapters[t] for t in args.tickers if t in all_adapters}
    else:
        target = dict(list(all_adapters.items())[args.start:args.end])

    # 既存結果ロード（再開対応）
    results = {}
    if Path(RESULTS_FILE).exists():
        with open(RESULTS_FILE, encoding="utf-8") as f:
            results = json.load(f)

    tickers_todo = [t for t in target if t not in results]
    logger.info(f"調査対象: {len(tickers_todo)} ticker（既完了: {len(results)}件）")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        for i, ticker in enumerate(tickers_todo):
            adapter = target[ticker]
            if not adapter:
                logger.warning(f"[{ticker}] adapter.json なし → スキップ")
                continue

            logger.info(f"[{i+1}/{len(tickers_todo)}] ticker={ticker}")
            result = await investigate_ticker(ticker, adapter, page, results)
            results[ticker] = result

            # 途中保存（10件ごと）
            if (i + 1) % 10 == 0:
                with open(RESULTS_FILE, "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)
                logger.info(f"  中間保存: {len(results)}件完了")

            # レートリミット
            await asyncio.sleep(1.5)

        await browser.close()

    # 最終保存
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    logger.info(f"=== 調査完了: {len(results)}件 ===")

    # サマリー出力
    fixes_needed = {t: r for t, r in results.items() if r.get("fix")}
    no_fix = {t: r for t, r in results.items() if not r.get("fix")}
    errors = {t: r for t, r in results.items() if r.get("error")}

    logger.info(f"修正案あり: {len(fixes_needed)}件")
    logger.info(f"修正案なし: {len(no_fix)}件")
    logger.info(f"エラー: {len(errors)}件")

    for ticker, r in fixes_needed.items():
        logger.info(f"  [{ticker}] {r['company']}: {r['fix']}")


if __name__ == "__main__":
    asyncio.run(main())

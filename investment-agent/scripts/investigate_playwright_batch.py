"""playwright_required 48社のローカルブラウザ調査スクリプト。

各社のIRページをPlaywrightでレンダリングし:
1. eIR (eir-parts.net) 使用有無
2. JS後のダウンロードリンク有無
3. API/JSON エンドポイントの検出
4. HTML テーブルの有無

結果を data/playwright_investigation.json に保存する。
"""
import csv
import json
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth as _Stealth

OUT_JSON = Path("data/playwright_investigation.json")
INDEX_CSV = Path("data/monthly_adapter_index.csv")

TIMEOUT = 20000  # ms
SLEEP_SEC = 1.5

EIR_PATTERN = re.compile(r"eir-parts\.net|ssl4\.eir-parts|eolparts", re.I)
EIR_CODE_RE = re.compile(r"eir-parts\.net/(?:V4Public/eir/|[^/]+/)(\d+)/", re.I)
EIR_JS_RE = re.compile(r"(https://ssl\d+\.eir-parts\.net/V4Public/eir/(\d+)/[^\"']+\.js)", re.I)

DOWNLOAD_EXT = re.compile(r"\.(pdf|xlsx|xls|csv)(\?.*)?$", re.IGNORECASE)
MONTHLY_TEXT = re.compile(
    r"月次|monthly|売上速報|月別|受注速報|受注実績|販売台数|輸送実績|旅客数|搭乗実績|稼働実績|出荷量",
    re.IGNORECASE,
)
API_JSON_RE = re.compile(r"\.(json|js)(\?.*)?$", re.I)


def load_playwright_required():
    rows = []
    with open(INDEX_CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["category"] == "playwright_required":
                rows.append(r)
    return rows


def investigate_company(page, ticker, url):
    """1社調査。結果 dict を返す。"""
    result = {
        "ticker": ticker,
        "url": url,
        "eir_detected": False,
        "eir_codes": [],
        "eir_js_urls": [],
        "download_links": [],
        "api_json_urls": [],
        "html_tables": 0,
        "iframe_count": 0,
        "error": None,
        "verdict": "unknown",
    }

    captured_urls = []
    captured_eir_js = []
    captured_json = []

    def on_request(req):
        req_url = req.url
        captured_urls.append(req_url)
        # eIR JS 検出
        m = EIR_JS_RE.search(req_url)
        if m:
            captured_eir_js.append((m.group(1), m.group(2)))
        # JSON/JS API 検出
        if API_JSON_RE.search(req_url) and "eir-parts.net" not in req_url.lower():
            if any(kw in req_url.lower() for kw in ["sale", "monthly", "ir", "report", "data"]):
                captured_json.append(req_url)

    page.on("request", on_request)

    try:
        page.goto(url, timeout=TIMEOUT, wait_until="networkidle")
    except PWTimeout:
        try:
            page.goto(url, timeout=TIMEOUT, wait_until="domcontentloaded")
            time.sleep(3)
        except Exception as e:
            result["error"] = str(e)[:100]
            return result
    except Exception as e:
        result["error"] = str(e)[:100]
        return result

    # eIR 検出
    if any(EIR_PATTERN.search(u) for u in captured_urls):
        result["eir_detected"] = True
        for url_item, code in captured_eir_js:
            if code not in result["eir_codes"]:
                result["eir_codes"].append(code)
                result["eir_js_urls"].append(url_item)

    # ページソースからも eIR コード抽出
    try:
        html = page.content()
        for m in EIR_CODE_RE.finditer(html):
            c = m.group(1)
            if c not in result["eir_codes"]:
                result["eir_codes"].append(c)
                result["eir_detected"] = True
        # eIR JS URL をHTMLから抽出
        for m in EIR_JS_RE.finditer(html):
            if m.group(2) not in result["eir_codes"]:
                result["eir_codes"].append(m.group(2))
                result["eir_js_urls"].append(m.group(1))
                result["eir_detected"] = True
    except Exception:
        pass

    # ダウンロードリンク（レンダリング後DOM）
    try:
        links = page.query_selector_all("a[href]")
        for link in links:
            href = link.get_attribute("href") or ""
            text = link.inner_text().strip()[:50]
            # 絶対URLに変換
            if href.startswith("http"):
                abs_url = href
            elif href.startswith("//"):
                abs_url = "https:" + href
            elif href.startswith("/"):
                from urllib.parse import urlparse
                base = urlparse(url)
                abs_url = f"{base.scheme}://{base.netloc}{href}"
            else:
                continue
            if DOWNLOAD_EXT.search(abs_url):
                matched_text = MONTHLY_TEXT.search(text) or MONTHLY_TEXT.search(href)
                result["download_links"].append({
                    "text": text,
                    "url": abs_url[:120],
                    "monthly_match": bool(matched_text),
                })
    except Exception as e:
        result["error"] = (result.get("error") or "") + f" links:{str(e)[:50]}"

    # HTML テーブル数
    try:
        result["html_tables"] = len(page.query_selector_all("table"))
    except Exception:
        pass

    # iframe 数
    try:
        result["iframe_count"] = len(page.query_selector_all("iframe"))
    except Exception:
        pass

    # API JSON
    result["api_json_urls"] = captured_json[:5]

    # 判定
    if result["eir_detected"] and result["eir_codes"]:
        result["verdict"] = "eir_api"
    elif result["download_links"]:
        monthly = [d for d in result["download_links"] if d["monthly_match"]]
        result["verdict"] = "scrape_links" if monthly else "scrape_links_no_monthly"
    elif result["html_tables"] > 0:
        result["verdict"] = "html_table"
    elif result["api_json_urls"]:
        result["verdict"] = "api_json"
    else:
        result["verdict"] = "no_content"

    return result


def main():
    companies = load_playwright_required()
    print(f"調査対象: {len(companies)}社")

    # 既存結果をロード（中断再開対応）
    existing = {}
    if OUT_JSON.exists():
        with open(OUT_JSON, encoding="utf-8") as f:
            for item in json.load(f):
                existing[item["ticker"]] = item
        print(f"  既存結果: {len(existing)}社")

    results = list(existing.values())
    done_tickers = set(existing.keys())

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-web-security"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="ja-JP",
        )
        page = context.new_page()
        _Stealth().apply_stealth_sync(page)

        for i, row in enumerate(companies, 1):
            ticker = row["ticker"]
            company = row["company_name"]
            url = row["monthly_page_url"]

            if ticker in done_tickers:
                print(f"[{i}/{len(companies)}] {ticker} {company} → スキップ（既存）")
                continue

            print(f"[{i}/{len(companies)}] {ticker} {company}")
            print(f"  URL: {url}")

            res = investigate_company(page, ticker, url)
            res["company_name"] = company

            verdict = res["verdict"]
            eir = f" eir_codes={res['eir_codes']}" if res["eir_codes"] else ""
            dl = f" dl={len(res['download_links'])}" if res["download_links"] else ""
            tbl = f" tables={res['html_tables']}" if res["html_tables"] else ""
            err = f" ERR={res['error'][:50]}" if res["error"] else ""
            print(f"  → {verdict}{eir}{dl}{tbl}{err}")

            results.append(res)

            # 途中保存
            OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
            with open(OUT_JSON, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

            time.sleep(SLEEP_SEC)

        context.close()
        browser.close()

    print(f"\n=== 完了 ===")
    print(f"保存: {OUT_JSON}")

    # サマリー
    from collections import Counter
    verdict_counts = Counter(r["verdict"] for r in results)
    print("\n=== 判定サマリー ===")
    for v, cnt in sorted(verdict_counts.items(), key=lambda x: -x[1]):
        print(f"  {v:30s}: {cnt}社")

    # eir_api 詳細
    eir_companies = [r for r in results if r["verdict"] == "eir_api"]
    if eir_companies:
        print(f"\n=== eIR API 対応企業 ({len(eir_companies)}社) ===")
        for r in eir_companies:
            print(f"  {r['ticker']} {r['company_name']} codes={r['eir_codes']}")


if __name__ == "__main__":
    main()

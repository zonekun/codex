"""needs_review 28社の月次開示ページ調査スクリプト。

各社のIRページをPlaywrightでレンダリングし月次ダウンロードリンクを探索。
結果を data/investigate_needs_review.json に保存し monthly_adapter_index.csv を更新する。
"""
import csv
import json
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth as _Stealth

OUT_JSON = Path("data/investigate_needs_review.json")
INDEX_CSV = Path("data/monthly_adapter_index.csv")

TIMEOUT = 25000  # ms
SLEEP_SEC = 2.0

EIR_PATTERN = re.compile(r"eir-parts\.net|ssl4\.eir-parts|eolparts", re.I)
EIR_CODE_RE = re.compile(r"eir-parts\.net/(?:V4Public/eir/|[^/]+/)(\d+)/", re.I)
EIR_JS_RE = re.compile(r"(https://ssl\d+\.eir-parts\.net/V4Public/eir/(\d+)/[^\"']+\.js)", re.I)

DOWNLOAD_EXT = re.compile(r"\.(pdf|xlsx|xls|csv)(\?.*)?$", re.IGNORECASE)
MONTHLY_TEXT = re.compile(
    r"月次|monthly|売上速報|月別|受注速報|受注実績|販売台数|輸送実績|旅客数|搭乗実績|稼働実績|出荷量|売上高速報",
    re.IGNORECASE,
)

# 各社のIR URL（GCS ir_url.json の company_hp_url ベース＋IR直リンク）
COMPANY_IR_URLS = {
    "1925": "http://www.daiwahouse.co.jp/investor/",       # 大和ハウス工業
    "2587": "http://www.suntory.co.jp/sbf/ir/",            # サントリー食品（SBF）
    "3030": "http://www.pub-hub.com/ir/",                  # ハブ（pub-hub.com）
    "3045": "http://www.kawasaki-corp.co.jp/ir/",          # カワサキ
    "3048": "http://www.biccamera.co.jp/ir/",              # ビックカメラ
    "3094": "https://www.supervalue.jp/ir/",               # スーパーバリュー
    "3169": "http://www.unico-fan.co.jp/ir/",              # UNICO（家具）
    "3196": "https://hotland.co.jp/ir/",                   # ホットランド（/policy/ → /ir/）
    "3349": "http://www.cosmospc.co.jp/ir/",               # コスモス薬品
    "3407": "https://www.asahi-kasei.com/jp/ir/",          # 旭化成（active済み）
    "3561": "http://www.chikaranomoto.com/ir/library/monthly/",  # 力の源（active済み）
    "3678": "https://mediado.jp/ir/",                      # メディアドゥ（HTTP2エラー）
    "6425": "https://www.universal-777.co.jp/ir/library/", # ユニバーサル（skip済み）
    "7201": "http://www.nissan.co.jp/JP/COMPANY/INVESTOR/", # 日産（nissan.co.jp）
    "7326": "https://www.sbiig.co.jp/ir/",                 # SBI（skip済み）
    "7453": "https://ryohin-keikaku.jp/ir/",               # 良品計画（active済み）
    "7502": "https://www.plazacreate.co.jp/ir/",           # プラザクリエイト
    "7514": "http://www.himaraya.co.jp/ir/",               # ヒマラヤ
    "7544": "http://www.three-f.co.jp/ir/",                # スリーエフ（three-f.co.jp）
    "7612": "https://www.colowide.co.jp/ir/sale/",         # コロワイド（active済み）
    "8165": "http://www.senshukai.co.jp/ir/",              # 千趣会（monthly.html → /ir/）
    "8282": "https://www.ksdenki.co.jp/kshd/default.aspx", # ケーズHD（正しいURL）
    "8706": "https://www.kyokuto-sec.co.jp/ir/",           # 極東証券
    "9020": "http://www.jreast.co.jp/investor/",           # JR東日本
    "9201": "http://www.jal.com/ja/investor/",             # JAL
    "9412": "https://www.skyperfectjsat.co.jp/ir/monthly/",# スカパー
    "9983": "http://www.fastretailing.com/jp/ir/monthly/", # ファーストリテイリング（active済み）
    "9994": "http://www.yamaya.jp/ir/",                    # やまや（yamaya.jp）
}

# skip確定（月次開示なし確認済み）
SKIP_CONFIRMED = {
    "7326": "保険持株会社。月次速報開示なし。四半期・年次のみ。",
    "6425": "フィリピンIR月次実績を2020年5月に開示停止。その後再開確認なし。",
}


def investigate_company(page, ticker, url, company_name):
    """1社調査。結果 dict を返す。"""
    result = {
        "ticker": ticker,
        "company_name": company_name,
        "url": url,
        "eir_detected": False,
        "eir_codes": [],
        "eir_js_urls": [],
        "download_links": [],
        "monthly_links": [],
        "html_tables": 0,
        "iframe_count": 0,
        "error": None,
        "verdict": "unknown",
    }

    captured_eir_js = []

    def on_request(req):
        m = EIR_JS_RE.search(req.url)
        if m:
            captured_eir_js.append((m.group(1), m.group(2)))

    page.on("request", on_request)

    try:
        try:
            page.goto(url, wait_until="load", timeout=TIMEOUT)
        except Exception as e:
            if "interrupted by another navigation" in str(e):
                # JS/HTTPリダイレクト → 最終ページのload待ち
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT)
                except Exception:
                    pass
            else:
                raise
        time.sleep(SLEEP_SEC)

        # eIR検出
        html = page.content()
        if EIR_PATTERN.search(html):
            result["eir_detected"] = True
            codes = list(set(EIR_CODE_RE.findall(html)))
            result["eir_codes"] = codes

        if captured_eir_js:
            result["eir_js_urls"] = [js[0] for js in captured_eir_js]
            result["eir_codes"] = list(set(result["eir_codes"] + [js[1] for js in captured_eir_js]))
            result["eir_detected"] = True

        # ダウンロードリンク取得
        links = page.query_selector_all("a[href]")
        for link in links:
            try:
                href = link.get_attribute("href") or ""
                text = (link.inner_text() or "").strip()
                if not href:
                    continue
                # 絶対URL化
                if href.startswith("//"):
                    href = "https:" + href
                elif href.startswith("/"):
                    from urllib.parse import urlparse
                    base = urlparse(url)
                    href = f"{base.scheme}://{base.netloc}{href}"
                elif not href.startswith("http"):
                    continue

                is_download = bool(DOWNLOAD_EXT.search(href))
                is_monthly = bool(MONTHLY_TEXT.search(text) or MONTHLY_TEXT.search(href))

                if is_download:
                    result["download_links"].append({
                        "text": text[:80],
                        "url": href,
                        "monthly_match": is_monthly,
                    })
                elif is_monthly:
                    result["monthly_links"].append({
                        "text": text[:80],
                        "url": href,
                    })
            except Exception:
                pass

        # iframeとtable
        result["iframe_count"] = len(page.query_selector_all("iframe"))
        result["html_tables"] = len(page.query_selector_all("table"))

        # verdict 判定
        monthly_dl = [l for l in result["download_links"] if l["monthly_match"]]
        if result["eir_detected"]:
            result["verdict"] = "eir_api"
        elif monthly_dl:
            result["verdict"] = "scrape_links"
            result["best_link"] = monthly_dl[0]["url"]
        elif result["monthly_links"]:
            result["verdict"] = "monthly_page_found"
            result["best_link"] = result["monthly_links"][0]["url"]
        elif result["download_links"]:
            result["verdict"] = "has_downloads_no_monthly"
        else:
            result["verdict"] = "no_links"

    except PWTimeout:
        result["error"] = "timeout"
        result["verdict"] = "error"
    except Exception as e:
        result["error"] = str(e)[:100]
        result["verdict"] = "error"

    return result


def load_index():
    rows = {}
    fieldnames = None
    with open(INDEX_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for r in reader:
            rows[r["ticker"]] = r
    return rows, fieldnames


def save_index(rows, fieldnames):
    with open(INDEX_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in sorted(rows.values(), key=lambda x: x["ticker"]):
            writer.writerow(r)


def main():
    import sys
    # コマンドライン引数でticker指定可能（--オプション以外がticker）
    args = sys.argv[1:]
    only_tickers = set(a for a in args if not a.startswith("--")) or None

    # 既存結果ロード
    results = {}
    if OUT_JSON.exists():
        with open(OUT_JSON, encoding="utf-8") as f:
            existing = json.load(f)
            results = {e["ticker"]: e for e in existing}

    index, fieldnames = load_index()

    # skip確定をまず反映
    for ticker, reason in SKIP_CONFIRMED.items():
        if ticker in index:
            index[ticker]["category"] = "skip"
            index[ticker]["adapter_note"] = f"月次開示なし: {reason}"
            index[ticker]["updated_at"] = "2026-03-16"
            print(f"[SKIP] {ticker} {index[ticker]['company_name']}: {reason}")

    # 調査対象（--failed-only 指定時はエラーのみ再実行）
    failed_only = "--failed-only" in sys.argv
    targets = []
    for ticker, url in COMPANY_IR_URLS.items():
        if ticker in SKIP_CONFIRMED:
            continue
        if only_tickers and ticker not in only_tickers:
            continue
        if ticker not in index:
            print(f"[WARN] {ticker} not in index, skip")
            continue
        # 既に active になっている場合はスキップ
        if index[ticker]["category"] == "active":
            print(f"[DONE] {ticker} {index[ticker]['company_name']}: already active, skip")
            continue
        # failed_only モード: エラーまたはno_linksのみ再実行
        if failed_only and ticker in results and results[ticker].get("verdict") not in ("error", "no_links"):
            print(f"[SKIP] {ticker}: prev={results[ticker].get('verdict')}")
            continue
        targets.append((ticker, url, index[ticker]["company_name"]))

    print(f"\n調査対象: {len(targets)}社\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-http2"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="ja-JP",
        )
        page = ctx.new_page()
        _Stealth().apply_stealth_sync(page)

        for i, (ticker, url, company_name) in enumerate(targets):
            print(f"[{i+1}/{len(targets)}] {ticker} {company_name}: {url}")
            res = investigate_company(page, ticker, url, company_name)

            verdict = res["verdict"]
            print(f"  → verdict={verdict}", end="")

            if verdict == "eir_api":
                codes = res.get("eir_codes", [])
                print(f" eir_codes={codes}", end="")
                # eIR API アダプター設定
                if codes:
                    eir_code = codes[0]
                    index[ticker]["category"] = "active"
                    index[ticker]["type"] = "eir_api"
                    index[ticker]["monthly_page_url"] = url
                    index[ticker]["adapter_note"] = f"eIRシステム。コード={eir_code}"
                    index[ticker]["updated_at"] = "2026-03-16"
            elif verdict == "scrape_links":
                best = res.get("best_link", "")
                monthly_dl = [l for l in res["download_links"] if l["monthly_match"]]
                print(f" links={len(monthly_dl)} best={best[:50]}", end="")
                index[ticker]["category"] = "active"
                index[ticker]["type"] = "scrape_links"
                index[ticker]["monthly_page_url"] = url
                index[ticker]["adapter_note"] = f"月次PDFリンク{len(monthly_dl)}件検出"
                index[ticker]["updated_at"] = "2026-03-16"
            elif verdict == "monthly_page_found":
                best = res.get("best_link", "")
                print(f" page={best[:50]}", end="")
                index[ticker]["category"] = "active"
                index[ticker]["type"] = "scrape_links"
                index[ticker]["monthly_page_url"] = best
                index[ticker]["adapter_note"] = f"月次ページリンク発見: {best[:60]}"
                index[ticker]["updated_at"] = "2026-03-16"
            elif verdict == "no_links":
                dl_count = len(res["download_links"])
                print(f" dl={dl_count} tables={res['html_tables']}", end="")
                index[ticker]["adapter_note"] = f"月次リンクなし。DL={dl_count} tables={res['html_tables']}"
                # categoryはurl_not_foundのままにする（手動確認用）
            elif verdict == "has_downloads_no_monthly":
                dl_count = len(res["download_links"])
                print(f" dl={dl_count}(非月次)", end="")
                index[ticker]["adapter_note"] = f"DLリンク{dl_count}件あるが月次キーワードなし"
            elif verdict == "error":
                print(f" error={res['error']}", end="")

            print()
            results[ticker] = res

        browser.close()

    # JSON保存
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(list(results.values()), f, ensure_ascii=False, indent=2)
    print(f"\n結果保存: {OUT_JSON}")

    # CSV更新
    save_index(index, fieldnames)
    print(f"インデックス更新: {INDEX_CSV}")

    # サマリー
    print("\n=== サマリー ===")
    from collections import Counter
    cats = Counter()
    for t in COMPANY_IR_URLS:
        if t in index:
            cats[index[t]["category"]] += 1
    for k, v in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()

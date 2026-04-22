"""月次開示アダプター統合パイプライン v2。

ir_url.json + download_adapter.json を廃止し、adapter.json に統合。

処理モード:
  --mode migrate    : 既存 GCS データを adapter.json に変換（既存 active はそのまま移行）
  --mode fix-urls   : bad URL 企業の URL を Nikkei + Google (ドメインマッチ) で再取得
  --mode analyze    : URL 有効な企業のページを改良版スクレイパーで再解析
  --mode all        : migrate → fix-urls → analyze を順次実行

  --tickers 1234 5678  : 対象を限定
  --force              : 既存 adapter.json があっても上書き（デフォルト: スキップ）

adapter.json スキーマ:
  ticker, company_name, updated_at,
  company_hp_url,   # 公式HP (Nikkei 取得)
  ir_page_url,      # 月次開示ページ (scrape / search で取得)
  type,             # scrape_links | html_table | direct_url | playwright | unknown
  css_selector, link_text_pattern, link_href_pattern,
  table_selector,   # html_table 用
  status,           # active | needs_review | skip
  skip_reason,
  note,
  url_source,       # migrated | nikkei_google | manual | path_match | keyword_match
  last_checked, last_success
"""
import argparse
import csv
import io
import json
import logging
import os
import re
import sys
import time
import warnings
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse, parse_qs, quote_plus

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from google.cloud import storage
from google.oauth2 import service_account

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

IS_CLOUD_RUN = bool(os.environ.get("CLOUD_RUN_JOB"))
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GCS_BUCKET = "stock_data_1930932"
GCS_MASTER_PATH = "config/monthly_disclosure_master.csv"
KEY_FILE = os.path.join(BASE_DIR, "keys", "gcp-service-account.json")
JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST).strftime("%Y-%m-%d")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9",
}

DOWNLOAD_EXT = re.compile(r"\.(pdf|xlsx|xls|csv)(\?.*)?$", re.IGNORECASE)
MONTHLY_TEXT = re.compile(
    r"月次|monthly|売上速報|月別|受注速報|受注実績|販売台数|輸送実績|旅客数|搭乗実績|稼働実績|出荷量",
    re.IGNORECASE,
)
MONTHLY_TABLE_KW = re.compile(r"月次|月別|売上|受注|販売台数|monthly", re.IGNORECASE)
EIR_MARKERS = re.compile(r"eirPassCore|eir_common\.js|setParts\(|ir-cloud\.com", re.IGNORECASE)
SPA_MARKERS = re.compile(r"__NEXT_DATA__|__nuxt__|react-root|ng-app|ember-application", re.IGNORECASE)

# 無効 URL のドメイン（検索エンジン等）
BAD_DOMAINS = {
    "support.google.com", "google.com", "google.co.jp",
    "duckduckgo.com", "bing.com", "yahoo.co.jp", "yahoo.com",
    "baidu.com",
}

# 情報サイト（Google 検索結果のドメインマッチで除外）
INFO_DOMAINS = {
    "nikkei.com", "kabutan.jp", "minkabu.jp", "yahoo.co.jp", "yahoo.com",
    "google.com", "google.co.jp", "bloomberg.co.jp", "reuters.com",
    "irbank.net", "stockweather.co.jp", "buffett-code.com",
    "twitter.com", "x.com", "facebook.com", "wikipedia.org",
    "invest.co.jp", "traders.co.jp", "ullet.com", "edinet.fsa.go.jp",
    "duckduckgo.com", "bing.com",
}

NIKKEI_URL = "https://www.nikkei.com/nkd/company/gaiyo/?scode={scode}&ba=1"
NIKKEI_EXCLUDE = {"nikkei.com", "nikkei.co.jp", "google.co.jp", "google.com", "twitter.com", "x.com"}

RATE_SEC = 1.5


# ==========================================
# 認証・GCS
# ==========================================

def _apply_ssl_patch():
    import urllib3
    from requests.adapters import HTTPAdapter
    urllib3.disable_warnings()

    class _NoVerify(HTTPAdapter):
        def send(self, req, **kw):
            kw["verify"] = False
            return super().send(req, **kw)

    _orig = requests.Session.__init__

    def _p(self, *a, **kw):
        _orig(self, *a, **kw)
        self.mount("https://", _NoVerify())
        self.verify = False

    requests.Session.__init__ = _p


def _get_credentials():
    if IS_CLOUD_RUN:
        import google.auth
        creds, _ = google.auth.default()
        return creds
    _apply_ssl_patch()
    return service_account.Credentials.from_service_account_file(KEY_FILE)


def _load_json(path: str, bucket) -> dict:
    blob = bucket.blob(path)
    if not blob.exists():
        return {}
    try:
        return json.loads(blob.download_as_text())
    except Exception:
        return {}


def _save_json(path: str, data: dict, bucket) -> None:
    blob = bucket.blob(path)
    blob.upload_from_string(
        json.dumps(data, ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8",
    )


def load_companies(bucket) -> list[dict]:
    blob = bucket.blob(GCS_MASTER_PATH)
    text = blob.download_as_text(encoding="utf-8")
    companies = []
    for row in csv.DictReader(io.StringIO(text)):
        if row["DISCLOSURE_TYPE"].strip() != "c":
            continue
        if "処理不要" in row.get("NOTES", ""):
            continue
        companies.append({
            "ticker": row["TICKER"].strip(),
            "company_name": row["COMPANY_NAME"].strip(),
        })
    return companies


# ==========================================
# URL バリデーション
# ==========================================

def is_bad_url(url: str) -> bool:
    """検索エンジン等の無効 URL かどうか。"""
    if not url:
        return True
    domain = urlparse(url).netloc.lower()
    return any(bad in domain for bad in BAD_DOMAINS)


def domain_matches(result_url: str, official_domain: str) -> bool:
    """検索結果 URL のドメインが公式ドメインと一致するか（サブドメイン許容）。"""
    if not official_domain:
        return False
    result_domain = urlparse(result_url).netloc.lstrip("www.").lower()
    official_clean = official_domain.lstrip("www.").lower()
    return result_domain == official_clean or result_domain.endswith("." + official_clean)


# ==========================================
# Nikkei から公式 HP URL 取得
# ==========================================

def get_nikkei_hp_url(ticker: str, session: requests.Session) -> str:
    """日経会社概要ページから公式 HP URL を取得する。"""
    url = NIKKEI_URL.format(scode=ticker)
    try:
        resp = session.get(url, headers=HEADERS, timeout=12, allow_redirects=True)
        if resp.status_code != 200:
            return ""
    except Exception as e:
        logger.debug("Nikkei fetch 失敗 %s: %s", ticker, e)
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        domain = urlparse(href).netloc.lower()
        if any(ex in domain for ex in NIKKEI_EXCLUDE):
            continue
        return href
    return ""


# ==========================================
# Google 検索（ドメインマッチ版）
# ==========================================

def google_search_monthly_url(company_name: str, company_domain: str) -> tuple[str, str]:
    """Google "会社名 月次" → 公式ドメインにマッチする URL を返す。

    Returns:
        (url, method) — 見つからなければ ("", "not_found")

    Fix vs 旧実装:
        旧: site:ドメイン 指定 → Google が CAPTCHA で support.google.com にリダイレクト → その URL を保存
        新: site: 指定なし → 検索結果の中から company_domain に一致するものを選ぶ
    """
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        logger.warning("curl_cffi 未インストール。pip install curl-cffi")
        return "", "not_found"

    search_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en-US;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.google.com/",
    }

    def _extract(html: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        out = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/url?"):
                qs = parse_qs(urlparse(href).query)
                href = qs.get("q", [""])[0]
            if href.startswith("http"):
                out.append(href)
        return out

    def _search(query: str) -> list[str]:
        u = f"https://www.google.com/search?q={quote_plus(query)}&hl=ja&num=10"
        try:
            resp = cffi_requests.get(u, headers=search_headers, impersonate="chrome124", timeout=15)
            if resp.status_code == 200:
                return _extract(resp.text)
        except Exception as e:
            logger.debug("Google 検索失敗 [%s]: %s", query, e)
        return []

    official_domain = company_domain.lstrip("www.") if company_domain else ""

    # Phase 1: "会社名 月次" → 公式ドメインマッチ
    for kw in ["月次", "月次開示 IR"]:
        query = f"{company_name} {kw}"
        links = _search(query)
        for href in links:
            if official_domain and domain_matches(href, official_domain):
                logger.debug("Google domain-match hit [%s]: %s", query, href)
                return href, "nikkei_google"
        time.sleep(1.2)

    # Phase 2: ドメインが不明 or Phase 1 でマッチなし → 情報サイト除外で1件目
    for kw in ["月次", "月次開示 IR"]:
        query = f"{company_name} {kw}"
        links = _search(query)
        for href in links:
            domain = urlparse(href).netloc.lstrip("www.").lower()
            if not any(skip in domain for skip in INFO_DOMAINS):
                logger.debug("Google fallback hit [%s]: %s", query, href)
                return href, "nikkei_google_fallback"
        time.sleep(1.2)

    return "", "not_found"


# ==========================================
# ページ解析（改良版）
# ==========================================

def _find_download_links(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
    """ダウンロードリンクを改良版ロジックで抽出する。

    改善点:
    - 拡張子なし URL でもリンクテキストが月次キーワードならば候補に含める
    - onclick / data-href / data-url / data-src 属性も検索
    """
    results = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        abs_url = urljoin(base_url, href)

        # 拡張子あり
        if DOWNLOAD_EXT.search(abs_url):
            if abs_url not in seen:
                seen.add(abs_url)
                results.append((text, abs_url))
            continue

        # テキスト or href に月次キーワード → 拡張子なしでも候補
        if MONTHLY_TEXT.search(text) or MONTHLY_TEXT.search(href):
            if href and href not in ("#", "/", "") and not href.startswith("javascript"):
                if abs_url not in seen:
                    seen.add(abs_url)
                    results.append((text, abs_url))

    # onclick 属性内の PDF URL
    for el in soup.find_all(attrs={"onclick": True}):
        onclick = el.get("onclick", "")
        for u in re.findall(r"https?://[^\s'\"]+\.(?:pdf|xlsx|xls|csv)", onclick, re.I):
            if u not in seen:
                seen.add(u)
                results.append(("onclick", u))

    # data-href / data-url 等
    for attr in ["data-href", "data-url", "data-link", "data-pdf", "data-src"]:
        for el in soup.find_all(attrs={attr: True}):
            val = el.get(attr, "")
            abs_url = urljoin(base_url, val)
            if DOWNLOAD_EXT.search(abs_url) and abs_url not in seen:
                seen.add(abs_url)
                results.append((attr, abs_url))

    return results


def _find_monthly_table(soup: BeautifulSoup) -> bool:
    """HTML テーブルに月次データらしきものがあるか。"""
    for tbl in soup.find_all("table"):
        text = tbl.get_text(" ", strip=True)
        if MONTHLY_TABLE_KW.search(text):
            return True
    return False


def _detect_type(html: str, soup: BeautifulSoup, base_url: str) -> tuple[str, list[tuple[str, str]]]:
    """ページタイプと検出リンクを返す。"""
    if not html or len(html) < 100:
        return "unknown", []

    # eIR
    if EIR_MARKERS.search(html):
        return "playwright", []

    # SPA（本文テキストが極端に少ない）
    body_text = soup.get_text(strip=True)
    if len(body_text) < 300 and SPA_MARKERS.search(html):
        return "playwright", []

    # ダウンロードリンク（改良版）
    links = _find_download_links(soup, base_url)
    dl_links = [(t, u) for t, u in links if DOWNLOAD_EXT.search(u)]
    if dl_links:
        return "scrape_links", dl_links

    # 月次キーワードテキストリンク（拡張子なし）
    kw_links = [(t, u) for t, u in links if not DOWNLOAD_EXT.search(u)]
    if kw_links:
        return "scrape_links", kw_links  # 要確認だが候補として登録

    # HTML テーブル
    if _find_monthly_table(soup):
        return "html_table", []

    return "unknown", []


def _infer_patterns(links: list[tuple[str, str]]) -> tuple[str | None, str | None]:
    """リンクリストから link_href_pattern, link_text_pattern を推測する。"""
    hrefs = [urlparse(u).path for _, u in links]
    texts = [t for t, _ in links if t]

    # href パターン: 共通プレフィックス + 拡張子
    exts = set()
    for h in hrefs:
        m = re.search(r"\.(pdf|xlsx|xls|csv)$", h, re.I)
        if m:
            exts.add(m.group(1).lower())
    ext_pat = "|".join(sorted(exts)) if exts else "pdf"

    parts_list = [h.split("/") for h in hrefs]
    min_len = min((len(p) for p in parts_list), default=0)
    common: list[str] = []
    for i in range(min_len):
        segs = {p[i] for p in parts_list}
        if len(segs) == 1:
            common.append(list(segs)[0])
        else:
            break
    if common:
        prefix = "/".join(common)
        href_pat: str | None = re.escape(prefix) + r".*\.(" + ext_pat + r")"
    else:
        href_pat = r"\.(" + ext_pat + r")"

    # text パターン
    text_candidates = ["月次", "月別", "売上", "受注", "monthly", "販売台数"]
    text_pat = None
    non_empty = [t for t in texts if t]
    if non_empty:
        for kw in text_candidates:
            if all(kw.lower() in t.lower() for t in non_empty):
                text_pat = kw
                break

    return href_pat, text_pat


def fetch_page(url: str, session: requests.Session) -> str | None:
    """URL を GET して HTML を返す。"""
    try:
        resp = session.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        logger.debug("fetch 失敗 %s: %s", url, e)
    return None


# ==========================================
# アダプター操作
# ==========================================

def make_default_adapter(ticker: str, company_name: str) -> dict:
    return {
        "ticker": ticker,
        "company_name": company_name,
        "updated_at": TODAY,
        "company_hp_url": "",
        "ir_page_url": "",
        "type": "unknown",
        "css_selector": None,
        "link_text_pattern": None,
        "link_href_pattern": None,
        "table_selector": None,
        "status": "needs_review",
        "skip_reason": None,
        "note": "",
        "url_source": "",
        "last_checked": None,
        "last_success": None,
    }


def migrate_from_old(ticker: str, bucket) -> dict:
    """既存 ir_url.json + download_adapter.json → adapter.json に変換する。"""
    old_adapter = _load_json(f"monthlydata/{ticker}/download_adapter.json", bucket)
    old_ir = _load_json(f"monthlydata/{ticker}/ir_url.json", bucket)

    company_name = old_adapter.get("company_name") or old_ir.get("company_name", ticker)
    a = make_default_adapter(ticker, company_name)

    a["company_hp_url"] = old_ir.get("company_hp_url", "")
    a["ir_page_url"] = old_ir.get("monthly_page_url", "")
    a["url_source"] = "migrated"
    a["note"] = old_adapter.get("note", "")

    skip = old_adapter.get("skip", True)
    if not skip:
        a["status"] = "active"
        a["type"] = "scrape_links"
        a["css_selector"] = old_adapter.get("css_selector")
        a["link_text_pattern"] = old_adapter.get("link_text_pattern")
        a["link_href_pattern"] = old_adapter.get("link_href_pattern")
    else:
        # bad URL チェック
        if is_bad_url(a["ir_page_url"]):
            a["status"] = "needs_review"
            a["skip_reason"] = "bad_url"
            a["ir_page_url"] = ""  # リセット
        else:
            a["status"] = "needs_review"
            a["skip_reason"] = "reanalyze"

    return a


# ==========================================
# モード実装
# ==========================================

def mode_migrate(companies: list[dict], bucket, args) -> None:
    """既存データを adapter.json に一括変換する。"""
    logger.info("=== migrate モード ===")
    done = skip = 0

    for c in companies:
        ticker = c["ticker"]
        adapter_path = f"monthlydata/{ticker}/adapter.json"
        existing = _load_json(adapter_path, bucket)

        if existing and not args.force:
            skip += 1
            continue

        a = migrate_from_old(ticker, bucket)
        _save_json(adapter_path, a, bucket)
        logger.info("  migrate: %s %s → status=%s type=%s", ticker, a["company_name"], a["status"], a["type"])
        done += 1

    logger.info("migrate 完了: %d件変換, %d件スキップ", done, skip)


def mode_fix_urls(companies: list[dict], bucket, args, session: requests.Session) -> None:
    """bad URL / URL なし企業の月次ページ URL を再取得する。"""
    logger.info("=== fix-urls モード ===")
    targets = []
    for c in companies:
        ticker = c["ticker"]
        a = _load_json(f"monthlydata/{ticker}/adapter.json", bucket)
        if not a:
            a = migrate_from_old(ticker, bucket)
        if a.get("status") == "active":
            continue
        if a.get("skip_reason") not in ("bad_url", "reanalyze", None, ""):
            if not args.force:
                continue
        targets.append((ticker, a))

    logger.info("  対象: %d社", len(targets))
    fixed = still_missing = 0

    for i, (ticker, a) in enumerate(targets, 1):
        company_name = a.get("company_name", ticker)

        # 公式 HP URL がなければ Nikkei から取得
        hp_url = a.get("company_hp_url", "")
        if not hp_url:
            hp_url = get_nikkei_hp_url(ticker, session)
            if hp_url:
                a["company_hp_url"] = hp_url
                logger.info("[%d/%d] %s %s  Nikkei HP: %s", i, len(targets), ticker, company_name, hp_url)
            time.sleep(1.0)

        company_domain = urlparse(hp_url).netloc if hp_url else ""

        # Google "会社名 月次" → ドメインマッチ
        found_url, source = google_search_monthly_url(company_name, company_domain)

        if found_url:
            a["ir_page_url"] = found_url
            a["url_source"] = source
            a["status"] = "needs_review"  # analyze で確定させる
            a["skip_reason"] = "reanalyze"
            a["last_checked"] = TODAY
            logger.info("  → URL: %s", found_url)
            fixed += 1
        else:
            a["status"] = "needs_review"
            a["skip_reason"] = "url_not_found"
            logger.info("[%d/%d] %s %s  → URL 未発見", i, len(targets), ticker, company_name)
            still_missing += 1

        a["updated_at"] = TODAY
        _save_json(f"monthlydata/{ticker}/adapter.json", a, bucket)
        time.sleep(RATE_SEC)

    logger.info("fix-urls 完了: %d件修正, %d件未発見", fixed, still_missing)


def mode_analyze(companies: list[dict], bucket, args, session: requests.Session) -> None:
    """URL が有効な企業のページを改良版スクレイパーで解析し type / status を確定する。"""
    logger.info("=== analyze モード ===")
    targets = []
    for c in companies:
        ticker = c["ticker"]
        a = _load_json(f"monthlydata/{ticker}/adapter.json", bucket)
        if not a:
            a = migrate_from_old(ticker, bucket)
        if a.get("status") == "active" and not args.force:
            continue
        if not a.get("ir_page_url") or is_bad_url(a.get("ir_page_url", "")):
            continue
        targets.append((ticker, a))

    logger.info("  対象: %d社", len(targets))
    active_cnt = no_links = playwright_cnt = 0

    for i, (ticker, a) in enumerate(targets, 1):
        company_name = a.get("company_name", ticker)
        url = a["ir_page_url"]

        logger.info("[%d/%d] %s %s  %s", i, len(targets), ticker, company_name, url[:70])
        html = fetch_page(url, session)

        if not html:
            a["status"] = "needs_review"
            a["skip_reason"] = "fetch_failed"
            logger.info("  → fetch 失敗")
        else:
            soup = BeautifulSoup(html, "html.parser")
            page_type, links = _detect_type(html, soup, url)

            a["type"] = page_type
            a["last_checked"] = TODAY

            if page_type == "scrape_links" and links:
                href_pat, text_pat = _infer_patterns(links)
                a["link_href_pattern"] = href_pat
                a["link_text_pattern"] = text_pat
                a["status"] = "active"
                a["skip_reason"] = None
                a["last_success"] = TODAY
                logger.info("  → scrape_links: %d件, href_pat=%s", len(links), href_pat)
                active_cnt += 1

            elif page_type == "html_table":
                a["status"] = "active"
                a["skip_reason"] = None
                a["last_success"] = TODAY
                logger.info("  → html_table")
                active_cnt += 1

            elif page_type == "playwright":
                a["status"] = "needs_review"
                a["skip_reason"] = "playwright_required"
                logger.info("  → playwright（動的）")
                playwright_cnt += 1

            else:
                a["status"] = "needs_review"
                a["skip_reason"] = "no_links_confirmed"
                logger.info("  → リンクなし確認")
                no_links += 1

        a["updated_at"] = TODAY
        _save_json(f"monthlydata/{ticker}/adapter.json", a, bucket)
        time.sleep(RATE_SEC)

    logger.info("analyze 完了: active=%d playwright=%d no_links=%d", active_cnt, playwright_cnt, no_links)


# ==========================================
# main
# ==========================================

def main():
    parser = argparse.ArgumentParser(description="月次開示アダプター統合パイプライン v2")
    parser.add_argument("--mode", choices=["migrate", "fix-urls", "analyze", "all"],
                        default="all", help="実行モード")
    parser.add_argument("--tickers", nargs="*", help="対象ティッカーを限定")
    parser.add_argument("--force", action="store_true", help="既存 adapter.json を上書き")
    args = parser.parse_args()

    creds = _get_credentials()
    gcs = storage.Client(project="gmailpj-357912", credentials=creds)
    bucket = gcs.bucket(GCS_BUCKET)

    companies = load_companies(bucket)
    logger.info("type c 企業: %d社", len(companies))

    if args.tickers:
        companies = [c for c in companies if c["ticker"] in args.tickers]
        logger.info("  絞り込み後: %d社", len(companies))

    session = requests.Session()

    if args.mode in ("migrate", "all"):
        mode_migrate(companies, bucket, args)

    if args.mode in ("fix-urls", "all"):
        mode_fix_urls(companies, bucket, args, session)

    if args.mode in ("analyze", "all"):
        mode_analyze(companies, bucket, args, session)

    logger.info("=== 全処理完了 ===")


if __name__ == "__main__":
    main()

"""
no_links_confirmed / skip 企業の月次開示ページ統合調査スクリプト。

処理フロー（1社ごと）:
  1. 現在の ir_page_url（monthly_page_url）を Playwright でスクレイプ
  2. 月次リンクあり → アダプター更新(active) → 完了
  3. リンクなし/エラー → Google フォールバック
       a. "{社名} 月次 site:{ドメイン}"
       b. 失敗 → "{社名} 月次"（ドメインなし）
       c. 失敗 → "{社名} 月次 IR"（ドメインなし）
  4. 新 URL で再スクレイプ（Playwright）
  5. それでもダメ → no_links_confirmed のまま
  6. GCS adapter.json 更新

使い方:
  PYTHONUTF8=1 uv run python scripts/find_monthly_page_urls.py
  PYTHONUTF8=1 uv run python scripts/find_monthly_page_urls.py 9854 7269   # ticker 指定
  PYTHONUTF8=1 uv run python scripts/find_monthly_page_urls.py --failed-only
  PYTHONUTF8=1 uv run python scripts/find_monthly_page_urls.py --no-gcs    # GCS更新スキップ
"""

# ============================================================
# SSL パッチ（BQ等 googleapis.com への接続で SSLEOFError 回避）
# ============================================================
import urllib3
import requests as _req
from requests.adapters import HTTPAdapter as _HA

urllib3.disable_warnings()


class _NoVerify(_HA):
    def send(self, req, **kw):
        kw["verify"] = False
        return super().send(req, **kw)


_orig_init = _req.Session.__init__


def _patched_init(self, *a, **kw):
    _orig_init(self, *a, **kw)
    self.mount("https://", _NoVerify())
    self.verify = False


_req.Session.__init__ = _patched_init
# ============================================================

import argparse
import csv
import json
import logging
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout
from playwright_stealth import Stealth as _Stealth

# ============================================================
# 定数・設定
# ============================================================

BASE_DIR = Path(__file__).parent.parent
GCS_BUCKET = "stock_data_1930932"
KEY_FILE = BASE_DIR / "keys" / "gcp-service-account.json"
INDEX_CSV = BASE_DIR / "data" / "monthly_adapter_index.csv"
OUT_JSON = BASE_DIR / "data" / "find_monthly_page_urls_results.json"

JST = timezone(timedelta(hours=9))
TODAY = date.today().isoformat()
TIMEOUT_MS = 30000          # Playwright タイムアウト (ms)

IS_CLOUD_RUN = bool(os.environ.get("CLOUD_RUN_JOB"))
GCS_INDEX_PATH = "config/monthly_adapter_index.csv"
GCS_RESULTS_PATH = "config/find_monthly_page_urls_results.json"

# Cloud Run では /tmp/ を使用（コンテナに /app/data/ は存在しない）
if IS_CLOUD_RUN:
    OUT_JSON = Path("/tmp/find_monthly_page_urls_results.json")
SLEEP_SEC = 2.0             # ページ読み込み後の待機
GOOGLE_SLEEP_SEC = 1.2      # Google 検索間インターバル

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# eIR パターン
EIR_PATTERN = re.compile(r"eir-parts\.net|ssl4\.eir-parts|eolparts", re.I)
EIR_CODE_RE = re.compile(r"eir-parts\.net/(?:V4Public/eir/|[^/]+/)(\d+)/", re.I)
EIR_JS_RE = re.compile(r"(https://ssl\d+\.eir-parts\.net/V4Public/eir/(\d+)/[^\"']+\.js)", re.I)

# ダウンロード拡張子
DOWNLOAD_EXT = re.compile(r"\.(pdf|xlsx|xls|csv)(\?.*)?$", re.IGNORECASE)

# 月次キーワード（ページスクレイプ用）
MONTHLY_TEXT = re.compile(
    r"月次|monthly|売上速報|月別|受注速報|受注実績|販売台数|輸送実績|旅客数|搭乗実績|"
    r"稼働実績|出荷量|売上高速報|月次速報|月報|月次販売|月次データ|月次実績|"
    r"sokuhou|m-report|monthly.pdf|月次報告|月次売上|"
    r"月度|KPI|kpi|主要指標|月別実績|月次KPI|速報値|取引台数|台数実績|"
    r"生産台数|輸出台数|出荷台数|台数の推移|販売・生産|生産.*輸出|"
    r"20\d{4}\.pdf",
    re.IGNORECASE,
)

# HTML テーブル月次キーワード
HTML_TABLE_TEXT = re.compile(
    r"月次|売上速報|月別売上|月次実績|月次データ",
    re.IGNORECASE,
)

# Google 検索結果から除外するドメイン（情報サイト・SNS等）
_FALLBACK_SKIP_DOMAINS = {
    "nikkei.com", "kabutan.jp", "minkabu.jp", "yahoo.co.jp", "yahoo.com",
    "google.com", "google.co.jp", "support.google.com", "bloomberg.co.jp", "reuters.com",
    "irbank.net", "stockweather.co.jp", "buffett-code.com",
    "twitter.com", "x.com", "facebook.com", "wikipedia.org",
    "invest.co.jp", "traders.co.jp", "ullet.com", "edinet.fsa.go.jp",
}

# Google 汚染チェック用ドメイン
SKIP_DOMAINS = {"support.google.com", "accounts.google.com", "google.com"}

# Google レートリミット検出用（全リンクがこれらなら CAPTCHA / enablejs ページと判断）
_GOOGLE_BLOCKED_DOMAINS = {
    "support.google.com", "accounts.google.com", "google.com",
    "policies.google.com", "www.google.com",
}

# 持株会社ドメインパターン（このパターンにマッチするリンクを追跡する）
_HOLDINGS_DOMAIN_RE = re.compile(
    r"[-_](holdings|hd|holding|group|grp|hldgs)\.co\.jp|"
    r"(holdings|hd)\.(co\.jp|com)|"
    r"[-_](hd|holdings)\.jp",
    re.IGNORECASE,
)

# 社名サフィックス除去（検索クエリ短縮化）
_COMPANY_SUFFIX_RE = re.compile(
    r"[\s　]*(ホールディングス|ホールディング|ＨＤ|HD|グループ|グループホールディングス|"
    r"ホールデイングス|Holdings|HOLDINGS)$",
    re.IGNORECASE,
)


def _short_name(company_name: str) -> str:
    """社名のサフィックス（ホールディングス等）を除いた短縮名を返す。"""
    return _COMPANY_SUFFIX_RE.sub("", company_name).strip()

# 月次開示廃止・対象外確定済み銘柄（証券会社・開示廃止等）
CONFIRMED_NO_MONTHLY = {
    "7175": "今村証券: 証券会社。月次開示なし",
    "8613": "丸三証券: 証券会社。月次開示なし",
    "8614": "東洋証券: 証券会社。月次開示なし",
    "8622": "水戸証券: 証券会社。月次開示なし",
    "8624": "いちよし証券: 証券会社。月次開示なし",
    "8700": "丸八証券: 証券会社。月次開示なし",
    "8706": "極東証券: 証券会社。月次開示なし",
    "8707": "岩井コスモHD: 証券HD。月次開示確認要",  # 要調査
    "8708": "アイザワ証券グループ: 証券会社。月次開示なし",
    "7198": "SBIアルヒ: 住宅ローン会社。月次開示なし",
    "6535": "アイモバイル: 広告テック。月次開示なし",
    "7345": "アイ・パートナーズフィナンシャル: FP会社。月次開示なし",
}

# URL が空欄のもの用フォールバック HP URL
COMPANY_HP_URLS = {
    "3045": "http://www.kawasaki-corp.co.jp/",
    "4523": "https://www.eisai.com/jp/ir/",
    "6425": "https://www.universal-777.co.jp/ir/",
    "6535": "https://www.i-mobile.co.jp/ir/",
    "7175": "https://www.imamura-sec.co.jp/",
    "7198": "https://www.aruhi-corp.co.jp/ir/",
    "7201": "https://www.nissan-global.com/JP/INVESTORS/",
    "7544": "http://www.three-f.co.jp/ir/",
    "8613": "https://www.marusan-sec.co.jp/ir/",
    "8614": "https://www.toyo-sec.co.jp/ir/",
    "8622": "https://www.mito.co.jp/ir/",
    "8624": "https://www.ichiyoshi.co.jp/ir/",
    "8700": "https://www.maruhachikaisha.co.jp/ir/",
    "8706": "https://www.kyokuto-sec.co.jp/ir/",
    "8707": "https://www.iwaicosmo-hd.jp/ir/",
    "8708": "https://www.aizawa-sec.co.jp/ir/",
    "9412": "https://www.skyperfectjsat.co.jp/ir/",
    "3224": "ゼネラル・オイスター: 月次開示廃止（確認済み）",
    "3359": "cotta: 月次開示廃止（確認済み）",
    "3674": "オークファン: 月次開示廃止（確認済み）",
    "3901": "マークラインズ: 四半期開示のみ（月次開示なし）",
    "5259": "ＢＢＤイニシアティブ: 上場廃止予定",
    "6030": "アドベンチャー: 月次開示廃止",
    "6412": "平和: 月次開示廃止",
    "7359": "東京通信グループ: 月次開示廃止",
    "7805": "プリントネット: 月次開示廃止",
    "8254": "さいか屋: 月次開示廃止",
    "8566": "リコーリース: 月次開示なし",
    "9259": "タカヨシホールディングス: 月次開示廃止",
}

def _get_credentials():
    """Cloud Run: ADC / ローカル: サービスアカウントキー。"""
    if IS_CLOUD_RUN:
        import google.auth
        creds, _ = google.auth.default()
        return creds
    from google.oauth2 import service_account
    return service_account.Credentials.from_service_account_file(str(KEY_FILE))


# ============================================================
# Gemini ユーティリティ
# ============================================================

_GEMINI_MODEL_SEARCH = "gemini-2.5-pro-preview-05-06"   # Google検索結果判定（高精度）
_GEMINI_MODEL_PAGE   = "gemini-2.0-flash"               # ページリンク判定（軽量）

_GEMINI_SKIP_DOMAINS = {
    "kabutan.jp", "minkabu.jp", "yahoo.co.jp", "yahoo.com",
    "nikkei.com", "bloomberg.co.jp", "reuters.com",
    "irbank.net", "buffett-code.com", "stockweather.co.jp",
    "twitter.com", "x.com", "facebook.com", "wikipedia.org",
    "edinet.fsa.go.jp", "ullet.com", "traders.co.jp",
}


def _gemini_call(model_name: str, prompt: str) -> str:
    """Vertex AI Gemini を呼び出してテキストを返す。失敗時は空文字。"""
    try:
        import vertexai
        from vertexai.generative_models import GenerativeModel
        creds = _get_credentials()
        vertexai.init(project="gmailpj-357912", location="us-central1", credentials=creds)
        model = GenerativeModel(model_name)
        response = model.generate_content(prompt)
        return (response.text or "").strip()
    except Exception as e:
        logger.debug("Gemini呼び出しエラー [%s]: %s", model_name, e)
        return ""


def gemini_pick_from_search(search_urls: list[str], company_name: str, ticker: str) -> str:
    """Google検索結果URLリストを Gemini 2.5 Pro に渡し、月次データ公式ページURLを返す。

    Returns: URL or ""
    """
    # 外部情報サイトを事前除外した候補リスト
    candidates = [
        u for u in search_urls
        if not any(skip in urlparse(u).netloc for skip in _GEMINI_SKIP_DOMAINS)
    ][:10]
    if not candidates:
        candidates = search_urls[:10]
    if not candidates:
        return ""

    urls_text = "\n".join(f"- {u}" for u in candidates)
    prompt = f"""あなたは日本株のIR情報に詳しいアシスタントです。
以下は「{company_name}」（証券コード {ticker}）の月次開示ページを探したGoogle検索結果のURL一覧です。

【必須条件】次の条件をすべて満たすURLを1つだけ選んでください：
1. 「{company_name}」の**公式ドメイン**のURL
   （kabutan・minkabu・yahoo・irbank・nikkei・bloomberg・buffett-code などの外部情報サイトは絶対に不可）
2. 月次売上・販売台数・加入者数・稼働率・KPI などの**月次定期開示データ**が掲載されている可能性が高いページ
3. /ir/・/investor/・/finance/ などの投資家向けIRパスを含むものを優先

URL一覧：
{urls_text}

回答ルール：条件を満たすURLが1つでもあれば、そのURLのみを1行で返す。なければ "none" とだけ返す。"""

    answer = _gemini_call(_GEMINI_MODEL_SEARCH, prompt)
    if answer.startswith("http") and "\n" not in answer and " " not in answer:
        logger.info("Gemini(search) → %s", answer)
        return answer
    return ""


def gemini_pick_from_page_links(all_links: list[dict], company_name: str, ticker: str) -> str:
    """ページ内全リンクを Gemini Flash に渡し、月次データページURLを1つ選ばせる。

    Returns: URL or ""
    """
    if not all_links:
        return ""

    # 外部情報サイトを事前除外
    filtered = [
        lnk for lnk in all_links
        if not any(skip in urlparse(lnk["url"]).netloc for skip in _GEMINI_SKIP_DOMAINS)
    ][:80]
    if not filtered:
        filtered = all_links[:80]

    links_text = "\n".join(
        f"- [{lnk['text']}] {lnk['url']}" if lnk.get("text") else f"- {lnk['url']}"
        for lnk in filtered
    )
    prompt = f"""あなたは日本株のIR情報に詳しいアシスタントです。
以下は「{company_name}」（証券コード {ticker}）のIRページのリンク一覧です。

【必須条件】次の条件をすべて満たすリンクのURLを1つだけ選んでください：
1. 企業の**公式サイト**のURL（外部情報サイト不可）
2. 月次売上・販売台数・加入者数・稼働率・重要経営指標など**月次定期開示データ**が掲載されているページ
   （「重要な経営指標」「月次データ」「月次実績」「KPI」「速報」などのリンクテキストを優先）
3. 個別PDFファイルよりも一覧・インデックスページを優先

リンク一覧：
{links_text}

回答ルール：条件を満たすURLが1つでもあれば、そのURLのみを1行で返す。なければ "none" とだけ返す。"""

    answer = _gemini_call(_GEMINI_MODEL_PAGE, prompt)
    if answer.startswith("http") and "\n" not in answer and " " not in answer:
        logger.info("Gemini(page) → %s", answer)
        return answer
    return ""


def _get_gcs_client():
    from google.cloud import storage as _storage
    return _storage.Client(project="gmailpj-357912", credentials=_get_credentials())


_SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.google.com/",
}


# ============================================================
# Google 検索ユーティリティ
# ============================================================

def _extract_links(html: str) -> list[str]:
    """Google 検索結果 HTML からリンク URL を順に返す。"""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/url?"):
            qs = parse_qs(urlparse(href).query)
            href = qs.get("q", [""])[0]
        if href.startswith("http"):
            results.append(href)
    return results


def _is_google_blocked(links: list[str]) -> bool:
    """全リンクが Google ドメインなら CAPTCHA / enablejs ブロックと判定。"""
    if not links:
        return False
    return all(
        urlparse(lnk).netloc.lstrip("www.") in _GOOGLE_BLOCKED_DOMAINS
        for lnk in links
    )


def _google_search_playwright(page, query: str) -> list[str]:
    """Playwright で Google 検索し、リンク一覧を返す。curl_cffi が enablejs で弾かれる場合のフォールバック。"""
    search_url = f"https://www.google.com/search?q={quote_plus(query)}&hl=ja&num=10"
    try:
        page.goto(search_url, wait_until="load", timeout=20000)
        time.sleep(1.5)
        current = page.url
        # sorry / CAPTCHA ページ
        if "sorry" in current or "captcha" in current:
            logger.warning("Playwright Google CAPTCHA: %s", current)
            return []
        links = []
        for a in page.query_selector_all("a[href]"):
            href = a.get_attribute("href") or ""
            if href.startswith("/url?"):
                qs = parse_qs(urlparse(href).query)
                href = qs.get("q", [""])[0]
            if href.startswith("http"):
                links.append(href)
        return links
    except Exception as e:
        logger.debug("Playwright Google検索失敗 [%s]: %s", query, e)
        return []


def _google_search(query: str, pw_page=None) -> list[str]:
    """curl_cffi で Google 検索。enablejs ブロック時は Playwright にフォールバック。"""
    from curl_cffi import requests as cffi_requests

    search_url = f"https://www.google.com/search?q={quote_plus(query)}&hl=ja&num=10"
    links: list[str] = []
    try:
        resp = cffi_requests.get(
            search_url,
            headers=_SEARCH_HEADERS,
            impersonate="chrome136",
            timeout=15,
        )
        if resp.status_code == 200:
            links = _extract_links(resp.text)
    except Exception as e:
        logger.debug("Google検索失敗 [%s]: %s", query, e)

    # enablejs / CAPTCHA ブロック検出 → Playwright にフォールバック
    if (not links or _is_google_blocked(links)) and pw_page is not None:
        logger.debug("Google enablejs検出 → Playwright フォールバック [%s]", query)
        links = _google_search_playwright(pw_page, query)

    return links


def google_fallback_search(
    company_name: str, domain: str, ticker: str = "", pw_page=None
) -> str:
    """Google 4段フォールバック + Gemini で月次ページ URL を探す。

    フロー:
      a. "{短縮社名} 月次 site:{ドメイン}"
      b. "{短縮社名} 月次"（ドメインなし）
      c. "{短縮社名} 月次 IR"（ドメインなし）
      d. "{短縮社名} 月次開示"（ドメインなし）
      e. 全段失敗時 → 蓄積した検索結果を Gemini 2.5 Pro で判定

    enablejs / CAPTCHA 検出時: "RATE_LIMITED" を返す（no_links_confirmed にしない）

    Returns:
        見つかった URL、ブロックされた場合は "RATE_LIMITED"、見つからなければ空文字列
    """
    base_domain = domain.lstrip("www.")
    short = _short_name(company_name)
    # 短縮名と元の名前が同じなら検索を重複させない
    names = [short] if short == company_name else [short, company_name]

    blocked_count = 0
    all_raw_search_urls: list[str] = []  # 全検索クエリの生URL蓄積（Gemini用）

    def _search_and_filter(query: str) -> tuple[str, bool]:
        """検索してフィルタ済みURLを返す。ブロック時は ("BLOCKED", True)。"""
        links = _google_search(query, pw_page)
        all_raw_search_urls.extend(links)   # Gemini用に全URL蓄積
        if not links:
            return "", False
        if _is_google_blocked(links):
            return "BLOCKED", True
        for href in links:
            rd = urlparse(href).netloc.lstrip("www.")
            if not any(skip in rd for skip in _FALLBACK_SKIP_DOMAINS):
                return href, False
        return "", False

    # a. site: ドメイン指定（短縮名）
    if base_domain:
        query = f"{short} 月次 site:{base_domain}"
        url, blocked = _search_and_filter(query)
        if blocked:
            blocked_count += 1
        elif url:
            logger.debug("Google site: hit [%s]: %s", query, url)
            return url
        time.sleep(GOOGLE_SLEEP_SEC)

    # c,d,b の順（IR/開示を先に、素の「月次」を最後）
    keywords = ["月次 IR", "月次開示", "月次"]
    for name in names:
        for kw in keywords:
            query = f"{name} {kw}"
            url, blocked = _search_and_filter(query)
            if blocked:
                blocked_count += 1
                if blocked_count >= 2:
                    logger.warning("Google RATE_LIMITED (連続ブロック2回) [%s]", company_name)
                    return "RATE_LIMITED"
            elif url:
                logger.debug("Google hit [%s]: %s", query, url)
                return url
            time.sleep(GOOGLE_SLEEP_SEC)

    if blocked_count > 0:
        return "RATE_LIMITED"

    # e. 全段失敗 → Gemini 2.5 Pro で検索結果から判定
    if all_raw_search_urls and ticker:
        logger.info("Gemini(search) フォールバック [%s]", company_name)
        gemini_url = gemini_pick_from_search(all_raw_search_urls, company_name, ticker)
        if gemini_url:
            logger.info("Gemini(search) hit [%s]: %s", company_name, gemini_url)
            return gemini_url

    return ""


# ============================================================
# Playwright スクレイプ
# ============================================================

def scrape_page(page, ticker: str, url: str, company_name: str) -> dict:
    """Playwright で 1 URL を調査し、verdict を返す。

    Returns:
        dict with keys: ticker, url, verdict, eir_detected, eir_codes,
                        download_links, monthly_links, html_tables,
                        has_monthly_table, error, best_link, best_page
    """
    result = {
        "ticker": ticker,
        "company_name": company_name,
        "url": url,
        "eir_detected": False,
        "eir_codes": [],
        "eir_js_urls": [],
        "download_links": [],
        "monthly_links": [],
        "holdings_links": [],   # 持株会社・グループ系ドメインへのリンク
        "html_tables": 0,
        "iframe_count": 0,
        "has_monthly_table": False,
        "all_page_links": [],   # 全ページリンク（Gemini判定用）
        "error": None,
        "verdict": "unknown",
    }

    if not url:
        result["verdict"] = "no_url"
        result["error"] = "URL未設定"
        return result

    # Google 汚染チェック
    parsed = urlparse(url)
    if parsed.hostname in SKIP_DOMAINS:
        result["verdict"] = "google_contamination"
        result["error"] = f"Google汚染URL: {url}"
        return result

    captured_eir_js: list[tuple[str, str]] = []

    def on_request(req):
        m = EIR_JS_RE.search(req.url)
        if m:
            captured_eir_js.append((m.group(1), m.group(2)))

    page.on("request", on_request)

    try:
        try:
            page.goto(url, wait_until="load", timeout=TIMEOUT_MS)
        except Exception as e:
            if "interrupted by another navigation" in str(e):
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_MS)
                except Exception:
                    pass
            else:
                raise
        time.sleep(SLEEP_SEC)

        # リダイレクト後 Google 汚染チェック
        current_url = page.url
        if urlparse(current_url).hostname in SKIP_DOMAINS:
            result["verdict"] = "google_contamination"
            result["error"] = f"リダイレクト後Google汚染: {current_url}"
            return result

        html = page.content()

        # eIR 検出
        if EIR_PATTERN.search(html):
            result["eir_detected"] = True
            result["eir_codes"] = list(set(EIR_CODE_RE.findall(html)))

        if captured_eir_js:
            result["eir_js_urls"] = [js[0] for js in captured_eir_js]
            result["eir_codes"] = list(set(result["eir_codes"] + [js[1] for js in captured_eir_js]))
            result["eir_detected"] = True

        # ダウンロード・月次リンク収集
        links = page.query_selector_all("a[href]")
        for link in links:
            try:
                href = link.get_attribute("href") or ""
                text = (link.inner_text() or "").strip()
                if not href:
                    continue
                if href.startswith("//"):
                    href = "https:" + href
                elif href.startswith("/"):
                    base = urlparse(url)
                    href = f"{base.scheme}://{base.netloc}{href}"
                elif not href.startswith("http"):
                    href = urljoin(url, href)

                if urlparse(href).hostname in SKIP_DOMAINS:
                    continue

                # 全ページリンク収集（Gemini判定用、最大100件）
                if len(result["all_page_links"]) < 100:
                    result["all_page_links"].append({"text": text[:60], "url": href[:150]})

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

                # 持株会社・グループ系ドメインへのリンクを収集
                link_domain = urlparse(href).netloc
                if (link_domain != urlparse(url).netloc
                        and _HOLDINGS_DOMAIN_RE.search(link_domain)
                        and href not in [l["url"] for l in result["holdings_links"]]):
                    result["holdings_links"].append({
                        "text": text[:80],
                        "url": href,
                    })
            except Exception:
                pass

        # HTML table 検出
        result["iframe_count"] = len(page.query_selector_all("iframe"))
        tables = page.query_selector_all("table")
        result["html_tables"] = len(tables)
        for tbl in tables:
            try:
                if HTML_TABLE_TEXT.search(tbl.inner_text() or ""):
                    result["has_monthly_table"] = True
                    break
            except Exception:
                pass

        # サブページ探索（月次ページリンクが見つかったがダウンロードリンクなし）
        if not result["download_links"] and result["monthly_links"]:
            for mlink in result["monthly_links"][:3]:
                sub_url = mlink["url"]
                if sub_url == url:
                    continue
                try:
                    page.goto(sub_url, wait_until="load", timeout=TIMEOUT_MS)
                    time.sleep(1.0)
                    for link in page.query_selector_all("a[href]"):
                        try:
                            href = link.get_attribute("href") or ""
                            text = (link.inner_text() or "").strip()
                            if not href:
                                continue
                            if href.startswith("/"):
                                base = urlparse(sub_url)
                                href = f"{base.scheme}://{base.netloc}{href}"
                            elif not href.startswith("http"):
                                href = urljoin(sub_url, href)
                            if DOWNLOAD_EXT.search(href):
                                result["download_links"].append({
                                    "text": text[:80],
                                    "url": href,
                                    "monthly_match": bool(
                                        MONTHLY_TEXT.search(text) or MONTHLY_TEXT.search(href)
                                    ),
                                    "from_sub": sub_url,
                                })
                        except Exception:
                            pass
                except Exception:
                    pass

        # verdict 判定
        monthly_dl = [l for l in result["download_links"] if l["monthly_match"]]
        if result["eir_detected"] and result["eir_codes"]:
            result["verdict"] = "eir_api"
        elif monthly_dl:
            result["verdict"] = "scrape_links"
            result["best_link"] = monthly_dl[0]["url"]
            result["best_page"] = monthly_dl[0].get("from_sub", url)
        elif result["has_monthly_table"]:
            result["verdict"] = "html_table"
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
        result["error"] = str(e)[:200]
        result["verdict"] = "error"

    return result


# ============================================================
# インデックス CSV 読み書き
# ============================================================

def load_index() -> tuple[dict, list]:
    import io
    if IS_CLOUD_RUN:
        client = _get_gcs_client()
        blob = client.bucket(GCS_BUCKET).blob(GCS_INDEX_PATH)
        text = blob.download_as_text(encoding="utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        fieldnames = reader.fieldnames or []
        rows = {r["ticker"]: r for r in reader}
        logger.info("GCSからインデックス読み込み: %d行", len(rows))
        return rows, fieldnames
    rows: dict[str, dict] = {}
    fieldnames: list = []
    with open(INDEX_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for r in reader:
            rows[r["ticker"]] = r
    return rows, fieldnames


def save_index(rows: dict, fieldnames: list) -> None:
    import io
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in sorted(rows.values(), key=lambda x: x["ticker"]):
        writer.writerow(r)
    csv_text = buf.getvalue()
    if IS_CLOUD_RUN:
        client = _get_gcs_client()
        blob = client.bucket(GCS_BUCKET).blob(GCS_INDEX_PATH)
        blob.upload_from_string(csv_text.encode("utf-8-sig"), content_type="text/csv")
        logger.info("GCSインデックス更新: gs://%s/%s", GCS_BUCKET, GCS_INDEX_PATH)
    else:
        with open(INDEX_CSV, "w", encoding="utf-8-sig", newline="") as f:
            f.write(csv_text)


# ============================================================
# GCS アップロード
# ============================================================

def upload_adapter(ticker: str, adapter: dict) -> bool:
    """adapter.json を GCS にアップロード（GCS Python クライアント経由）。"""
    content = json.dumps(adapter, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        client = _get_gcs_client()
        blob = client.bucket(GCS_BUCKET).blob(f"monthlydata/{ticker}/adapter.json")
        blob.upload_from_string(content, content_type="application/json")
        return True
    except Exception as e:
        logger.error("GCSアップロード失敗 %s: %s", ticker, e)
        return False


# ============================================================
# verdict → adapter / index 更新ヘルパー
# ============================================================

def apply_verdict(
    verdict: str,
    res: dict,
    ticker: str,
    company_name: str,
    url: str,
    index: dict,
) -> dict | None:
    """verdict に応じて index を更新し、adapter dict を返す（更新不要なら None）。"""
    adapter = None

    if verdict == "eir_api":
        codes = res.get("eir_codes", [])
        if codes:
            eir_code = codes[0]
            index[ticker]["category"] = "active"
            index[ticker]["type"] = "eir_api"
            index[ticker]["monthly_page_url"] = url
            index[ticker]["adapter_note"] = f"eIRシステム。コード={eir_code}"
            index[ticker]["updated_at"] = TODAY
            adapter = {
                "ticker": ticker,
                "company_name": company_name,
                "updated_at": TODAY,
                "type": "eir_api",
                "eir_company_code": eir_code,
                "ir_page_url": url,
                "status": "active",
                "note": f"eIRシステム検出。コード={eir_code}",
                "last_checked": TODAY,
            }

    elif verdict == "scrape_links":
        best = res.get("best_link", "")
        best_page = res.get("best_page", url)
        monthly_dl = [l for l in res.get("download_links", []) if l.get("monthly_match")]
        index[ticker]["category"] = "active"
        index[ticker]["type"] = "scrape_links"
        index[ticker]["monthly_page_url"] = best_page
        index[ticker]["adapter_note"] = f"月次PDFリンク{len(monthly_dl)}件検出"
        index[ticker]["updated_at"] = TODAY
        adapter = {
            "ticker": ticker,
            "company_name": company_name,
            "updated_at": TODAY,
            "type": "scrape_links",
            "ir_page_url": best_page,
            "link_text_pattern": None,
            "link_href_pattern": None,
            "status": "active",
            "note": f"月次PDFリンク{len(monthly_dl)}件検出 best={best}",
            "last_checked": TODAY,
        }

    elif verdict == "html_table":
        index[ticker]["category"] = "active"
        index[ticker]["type"] = "html_table"
        index[ticker]["monthly_page_url"] = url
        index[ticker]["adapter_note"] = "HTML表に月次データあり"
        index[ticker]["updated_at"] = TODAY
        adapter = {
            "ticker": ticker,
            "company_name": company_name,
            "updated_at": TODAY,
            "type": "html_table",
            "ir_page_url": url,
            "table_selector": None,
            "status": "active",
            "note": "HTML表に月次データ検出。table_selector要調整",
            "last_checked": TODAY,
        }

    elif verdict == "monthly_page_found":
        best = res.get("best_link", "")
        index[ticker]["category"] = "active"
        index[ticker]["type"] = "scrape_links"
        index[ticker]["monthly_page_url"] = best
        index[ticker]["adapter_note"] = f"月次ページリンク発見: {best[:60]}"
        index[ticker]["updated_at"] = TODAY
        adapter = {
            "ticker": ticker,
            "company_name": company_name,
            "updated_at": TODAY,
            "type": "scrape_links",
            "ir_page_url": best,
            "status": "active",
            "note": f"月次ページ発見（サブページ）: {best}",
            "last_checked": TODAY,
        }

    return adapter


# ============================================================
# メイン
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="no_links_confirmed / skip 企業の月次開示ページ統合調査スクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "tickers",
        nargs="*",
        metavar="TICKER",
        help="調査する ticker を指定（省略時は全 no_links_confirmed）",
    )
    parser.add_argument(
        "--failed-only",
        action="store_true",
        help="前回エラー（error/no_links/no_url/unknown）のみ再実行",
    )
    parser.add_argument(
        "--no-gcs",
        action="store_true",
        help="GCS アップロードをスキップ（ローカル確認用）",
    )
    parser.add_argument(
        "--categories",
        default="no_links_confirmed,skip",
        help="対象カテゴリのカンマ区切り (デフォルト: no_links_confirmed,skip)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    only_tickers = set(args.tickers) if args.tickers else None
    target_categories = set(c.strip() for c in args.categories.split(","))

    # 既存結果ロード
    results: dict[str, dict] = {}
    if OUT_JSON.exists():
        with open(OUT_JSON, encoding="utf-8") as f:
            existing = json.load(f)
            results = {e["ticker"]: e for e in existing}

    index, fieldnames = load_index()

    # 調査対象を構築
    targets: list[tuple[str, str, str]] = []  # (ticker, url, company_name)
    for ticker, row in sorted(index.items()):
        cat = row.get("category", "")
        if cat not in target_categories:
            continue
        if only_tickers and ticker not in only_tickers:
            continue

        # 確定スキップ（証券会社等で月次なし確認済み）
        if ticker in CONFIRMED_NO_MONTHLY and ticker != "8707":
            if not only_tickers or ticker not in only_tickers:
                continue

        # ETF / 廃止 / REIT は除外
        note = row.get("adapter_note", "")
        if any(x in note for x in ["ETF", "上場廃止", "REIT", "投資法人"]):
            continue

        # URL 決定
        url = row.get("monthly_page_url", "").strip()
        if not url:
            url = COMPANY_HP_URLS.get(ticker, "")

        company_name = row.get("company_name", "")

        # failed_only: 前回エラーのみ再実行
        if args.failed_only and ticker in results:
            prev_verdict = results[ticker].get("verdict", "")
            if prev_verdict not in ("error", "no_links", "no_url", "unknown"):
                print(f"[SKIP] {ticker}: prev={prev_verdict}")
                continue

        targets.append((ticker, url, company_name))

    print(f"\n調査対象: {len(targets)}社\n")
    if not targets:
        print("対象がありません。")
        return

    activated = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-http2", "--no-sandbox"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="ja-JP",
        )
        page = ctx.new_page()
        _Stealth().apply_stealth_sync(page)
        # Google 検索 Playwright フォールバック用ページ
        google_page = ctx.new_page()
        _Stealth().apply_stealth_sync(google_page)

        def _try_activate(res2, ticker, company_name, new_url):
            """verdict → active 処理の共通ヘルパー。"""
            v = res2["verdict"]
            if v in ("eir_api", "scrape_links", "html_table", "monthly_page_found"):
                adapter = apply_verdict(v, res2, ticker, company_name, new_url, index)
                if adapter and not args.no_gcs:
                    ok = upload_adapter(ticker, adapter)
                    print(f"  GCS upload: {'OK' if ok else 'FAIL'}")
                if adapter:
                    return True
            return False

        for i, (ticker, url, company_name) in enumerate(targets):
            print(f"[{i+1}/{len(targets)}] {ticker} {company_name}: {url[:70] if url else '(URLなし)'}")

            # ---- Step 1: 現在の URL でスクレイプ ----
            res = scrape_page(page, ticker, url, company_name)
            verdict = res["verdict"]
            print(f"  Step1 verdict={verdict}", end="")

            # ---- Step 2: リンクあり → 即時更新 ----
            if verdict in ("eir_api", "scrape_links", "html_table", "monthly_page_found"):
                print(f" → active")
                if _try_activate(res, ticker, company_name, url):
                    activated += 1

            # ---- Step 3: リンクなし → 持株会社ドメイン追跡 ----
            elif verdict in ("no_links", "error", "no_url", "google_contamination",
                             "has_downloads_no_monthly"):

                found = False

                # Step 3a: 持株会社・グループ系ドメインへのリンクを追う
                holdings_links = res.get("holdings_links", [])
                if holdings_links:
                    print(f" → 持株会社リンク追跡({len(holdings_links)}件)", end="")
                    for hl in holdings_links[:3]:
                        h_url = hl["url"]
                        # /ir/ を付与して IR ページに誘導
                        ir_candidates = [h_url]
                        parsed_h = urlparse(h_url)
                        base_h = f"{parsed_h.scheme}://{parsed_h.netloc}"
                        for ir_path in ["/ir/", "/ir", "/investor/"]:
                            ir_candidates.append(base_h + ir_path)
                        for cand in ir_candidates:
                            res_h = scrape_page(page, ticker, cand, company_name)
                            v_h = res_h["verdict"]
                            if v_h in ("eir_api", "scrape_links", "html_table", "monthly_page_found"):
                                print(f"\n  持株会社HIT: {cand[:70]}")
                                print(f"  verdict={v_h} → active")
                                if _try_activate(res_h, ticker, company_name, cand):
                                    activated += 1
                                res = res_h
                                verdict = v_h
                                found = True
                                break
                        if found:
                            break
                    if not found:
                        print(f" (持株会社でも未発見)", end="")

                # Step 3b: Google フォールバック（全段失敗時はGemini 2.5 Proで判定）
                if not found:
                    domain = urlparse(url).netloc if url else ""
                    print(f" → Google検索フォールバック", end="")
                    new_url = google_fallback_search(
                        company_name, domain, ticker=ticker, pw_page=google_page
                    )

                    if new_url == "RATE_LIMITED":
                        print(f" → Google RATE_LIMITED（スキップ・no_links_confirmed 維持）")
                    elif new_url and new_url != url:
                        print(f"\n  GoogleHit: {new_url[:70]}")
                        res2 = scrape_page(page, ticker, new_url, company_name)
                        verdict2 = res2["verdict"]
                        print(f"  Step4 verdict={verdict2}", end="")

                        if verdict2 in ("eir_api", "scrape_links", "html_table", "monthly_page_found"):
                            print(f" → active")
                            if _try_activate(res2, ticker, company_name, new_url):
                                activated += 1
                            res = res2
                            verdict = verdict2
                            found = True
                        else:
                            print(f" → 未発見")
                    else:
                        print(f" → Google/Gemini(search) でも未発見", end="")

                # Step 3c: Gemini（ページリンクから月次ページ選定）← 最後の砦
                if not found:
                    all_page_links = res.get("all_page_links", [])
                    if all_page_links:
                        print(f" → Gemini(page)", end="")
                        gemini_url = gemini_pick_from_page_links(all_page_links, company_name, ticker)
                        if gemini_url and gemini_url != url:
                            print(f"\n  GeminiPageHit: {gemini_url[:70]}")
                            res_gp = scrape_page(page, ticker, gemini_url, company_name)
                            vgp = res_gp["verdict"]
                            print(f"  GeminiStep verdict={vgp}", end="")
                            if vgp in ("eir_api", "scrape_links", "html_table", "monthly_page_found"):
                                print(f" → active")
                                if _try_activate(res_gp, ticker, company_name, gemini_url):
                                    activated += 1
                                res = res_gp
                                verdict = vgp
                                found = True
                            else:
                                print(f" → 未発見（no_links_confirmed 維持）")
                        else:
                            print(f" → Gemini(page) も未発見（no_links_confirmed 維持）")
                    else:
                        print(f" → no_links_confirmed 維持")

            else:
                # unknown 等
                print()

            # ---- Step 6: 調査結果を記録 ----
            results[ticker] = res

            # クラッシュ対策: 10社ごとに中間保存
            if (i + 1) % 10 == 0:
                with open(OUT_JSON, "w", encoding="utf-8") as f:
                    json.dump(list(results.values()), f, ensure_ascii=False, indent=2)
                save_index(index, fieldnames)
                print(f"  [中間保存] {i+1}社完了")

        browser.close()

    # 最終保存
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(list(results.values()), f, ensure_ascii=False, indent=2)
    print(f"\n結果保存: {OUT_JSON}")

    save_index(index, fieldnames)
    print(f"インデックス更新: {INDEX_CSV}")

    # サマリー
    print(f"\n=== サマリー ===")
    print(f"調査: {len(targets)}社 → 新規active: {activated}社")
    verdicts = Counter(r.get("verdict", "?") for r in results.values())
    print("\nverdict 別:")
    for k, v in sorted(verdicts.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")

    cats = Counter(index[t].get("category", "?") for t in index)
    print("\nカテゴリ別:")
    for k, v in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()

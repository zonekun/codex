"""月次開示アダプター一括更新スクリプト (Cloud Run 月次実行)

旧スクリプト:
  - find_monthly_page_urls.py : URL探索 → url_adapter.json 更新
  - create_adapters_auto.py   : URL発見 → ページ解析 → アダプター生成（旧フォーマット）
を統合。

対象: monthly_adapter_index.csv の active 以外の全社
      (no_links_confirmed / skip / adapter未作成)

フロー（1社ごと）:
  1. URL不明 → Google 3段フォールバック検索
  2. URL判明 → Playwright でページ解析
  3. ページ解析結果:
     - eIR SPA検出    → type=eir_api でアダプター生成
     - DLリンクあり   → type=scrape_links + href/textパターン推定
     - リンクなし     → no_links_confirmed 維持
  4. 結果をまとめたランログを GCS に保存（常時）
  5. --dry-run なし → GCS url_adapter.json を更新（新フォーマット）
  6. インデックス CSV は常時更新（--dry-run でもスキップしない）

使い方:
  PYTHONUTF8=1 uv run python scripts/update_monthly_adapters.py
  PYTHONUTF8=1 uv run python scripts/update_monthly_adapters.py --tickers 9887 2702
  PYTHONUTF8=1 uv run python scripts/update_monthly_adapters.py --no-gcs
  PYTHONUTF8=1 uv run python scripts/update_monthly_adapters.py --dry-run --limit 10
  PYTHONUTF8=1 uv run python scripts/update_monthly_adapters.py --categories no_links_confirmed
"""

# ============================================================
# SSL パッチ（BQ等 googleapis.com への接続で SSLEOFError 回避）
# ============================================================
# M-6: import 時にグローバル patch すると module import しただけで
# requests.Session の挙動が変わる副作用があるため、明示関数化して
# __main__ 実行時のみ適用する。モジュール利用側からも必要なら
# _install_ssl_patch() を呼び出せる。
import urllib3
import requests as _req
from requests.adapters import HTTPAdapter as _HA


class _NoVerify(_HA):
    """全 https リクエストで verify=False を強制する HTTPAdapter。"""

    def send(self, req, **kw):
        kw["verify"] = False
        return super().send(req, **kw)


_SSL_PATCH_INSTALLED = False


def _install_ssl_patch() -> None:
    """requests.Session に SSL 無効化 patch を一度だけ適用する（M-6 冪等化）。"""
    global _SSL_PATCH_INSTALLED
    if _SSL_PATCH_INSTALLED:
        return
    urllib3.disable_warnings()
    _orig_init = _req.Session.__init__

    def _patched_init(self, *a, **kw):
        _orig_init(self, *a, **kw)
        self.mount("https://", _NoVerify())
        self.verify = False

    _req.Session.__init__ = _patched_init
    _SSL_PATCH_INSTALLED = True
# ============================================================

import argparse
import csv
import json
import logging
import os
import random
import re
import sys
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout
from playwright_stealth import Stealth as _Stealth
from bs4 import BeautifulSoup

# ============================================================
# 定数・設定
# ============================================================

IS_CLOUD_RUN = bool(os.environ.get("CLOUD_RUN_JOB"))
# Cloud Run Jobs タスク分割（並列実行でIPを分散してGoogle レートリミット回避）
# 各タスクが担当会社を分割処理: companies[task_index::task_count]
TASK_INDEX = int(os.environ.get("CLOUD_RUN_TASK_INDEX", 0))
TASK_COUNT = int(os.environ.get("CLOUD_RUN_TASK_COUNT", 1))

BASE_DIR = Path(__file__).parent.parent
# src.monthly import のため BASE_DIR を sys.path に追加
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
GCS_BUCKET = "stock_data_1930932"
KEY_FILE = BASE_DIR / "keys" / "gcp-service-account.json"
INDEX_CSV = BASE_DIR / "data" / "monthly_adapter_index.csv"
TMP_DIR = BASE_DIR / "data" / "tmp"

JST = timezone(timedelta(hours=9))
TODAY = date.today().isoformat()
TIMEOUT_MS = 30000       # Playwright タイムアウト (ms)
SLEEP_SEC = 2.0          # ページ読み込み後の待機
# Google 検索間インターバル: Cloud Run は新鮮 IP なので短め、ローカルは長め
GOOGLE_SLEEP_SEC = 5.0 if IS_CLOUD_RUN else 30.0
RATE_LIMIT_SEC = 1.5     # 企業間スリープ
# レートリミット待機: Cloud Run は新鮮 IP なので短め、ローカルは長め
_RATE_LIMIT_SLEEP_FIRST = 30.0 if IS_CLOUD_RUN else 300.0
_RATE_LIMIT_SLEEP_SECOND = 60.0 if IS_CLOUD_RUN else 600.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# L-1 責務分割 初段: 共通キーワード定数を src/monthly/keywords.py から import
# （将来的に download_monthly.py / extract_monthly_data.py も同モジュール移行予定）
from src.monthly.keywords import (
    DEFAULT_HREF_PATTERN,
    DOWNLOAD_EXT,
    EIR_CODE_RE,
    EIR_JS_RE,
    EIR_PATTERN,
    GOOGLE_CONTAMINATION_DOMAINS as _GOOGLE_CONTAMINATION_DOMAINS,
    HTML_TABLE_MONTH_HEADER,
    HTML_TABLE_TEXT,
    IR_SUBPAGE_KEYWORDS,
    MONTHLY_TEXT,
    NON_MONTHLY_EXCLUDE,
    SEARCH_HEADERS as _SEARCH_HEADERS,
    SKIP_DOMAINS,
)

# Playwright ブラウザ起動オプション（update_monthly_adapters 固有）
_BROWSER_ARGS = ["--disable-http2", "--no-sandbox"]
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


def _now_jst() -> str:
    """現在時刻を JST ISO 文字列で返す。"""
    return datetime.now(JST).isoformat()


def _normalize_url(url: str) -> str:
    """URLからタブ・改行・制御文字を除去して正規化する。

    9048 名古屋鉄道対応: hrefにタブ文字が混入してホスト名解決失敗する問題を修正。
    """
    if not url:
        return url
    # タブ・改行・キャリッジリターン・制御文字（U+0000〜U+001F, U+007F）を除去
    url = re.sub(r'[\t\r\n\x00-\x1f\x7f]', '', url)
    url = url.strip()
    return url


def _detect_monthly_table(page_or_frame) -> bool:
    """Playwright Page/Frame 内のテーブルが月次開示データか判定する（L-2 集約）。

    2 つの判定パターンを OR 評価:
      - A: table の innerText に HTML_TABLE_TEXT（月次/売上速報 等）が含まれる
      - B: table の innerText に月ヘッダー（4月/5月…）が 3 種以上 + 数値データ

    iframe 内も再帰走査する（frames 属性がある Page 時のみ）。

    Args:
        page_or_frame: Playwright Page または Frame オブジェクト。

    Returns:
        月次テーブルが 1 つでも検出されれば True。
    """
    try:
        for tbl in page_or_frame.query_selector_all("table"):
            try:
                text = tbl.inner_text() or ""
                # A: 月次キーワード
                if HTML_TABLE_TEXT.search(text):
                    return True
                # B: 月ヘッダー + 数値
                month_matches = set(HTML_TABLE_MONTH_HEADER.findall(text))
                if len(month_matches) >= 3 and re.search(r"[\d,]+\.?\d*\s*%?", text):
                    return True
            except Exception:
                pass
    except Exception:
        pass

    # iframe 走査（Page のみ）
    try:
        frames = getattr(page_or_frame, "frames", None)
        if frames and len(frames) > 1:
            for frame in frames[1:]:  # 0 はメインフレーム
                try:
                    for tbl in frame.query_selector_all("table"):
                        try:
                            text = tbl.inner_text() or ""
                            if HTML_TABLE_TEXT.search(text):
                                return True
                            month_matches = set(HTML_TABLE_MONTH_HEADER.findall(text))
                            if len(month_matches) >= 3 and re.search(r"[\d,]+\.?\d*\s*%?", text):
                                return True
                        except Exception:
                            pass
                except Exception:
                    pass
    except Exception:
        pass
    return False


def _abs_url(base: str, href: str) -> str | None:
    """href を絶対 URL に正規化する (M-4: 全探索ループから集約)。

    - 制御文字・タブ除去 (_normalize_url)
    - "//..." → "https://..."
    - "/..." → base の scheme/netloc を補完
    - 相対パス → urljoin
    - Google 汚染ドメインは None

    Args:
        base: 基準 URL (scheme + netloc を持つ完全な URL)
        href: a[href] 生値

    Returns:
        絶対 URL。href が空・正規化失敗・Google 汚染時は None。
    """
    if not href:
        return None
    href = _normalize_url(href)
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        base_parsed = urlparse(base)
        href = f"{base_parsed.scheme}://{base_parsed.netloc}{href}"
    elif not href.startswith("http"):
        href = urljoin(base, href)
    if urlparse(href).hostname in _GOOGLE_CONTAMINATION_DOMAINS:
        return None
    return href


# ============================================================
# GCP 認証
# ============================================================

def _get_gcs_client():
    """Cloud Run なら ADC、ローカルなら key file で GCS クライアントを返す。"""
    from google.cloud import storage

    if IS_CLOUD_RUN:
        import google.auth
        creds, project = google.auth.default()
        return storage.Client(project=project, credentials=creds)
    else:
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(str(KEY_FILE))
        return storage.Client(project="gmailpj-357912", credentials=creds)


# ============================================================
# GCS ユーティリティ
# ============================================================

def _load_json_from_gcs(bucket, path: str) -> dict:
    """GCS から JSON を読む。存在しない場合は空 dict。"""
    blob = bucket.blob(path)
    if not blob.exists():
        return {}
    try:
        return json.loads(blob.download_as_text())
    except Exception:
        return {}


def _save_json_to_gcs(bucket, path: str, data: dict) -> None:
    """GCS に JSON を保存する。"""
    blob = bucket.blob(path)
    blob.upload_from_string(
        json.dumps(data, ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8",
    )
    logger.debug("GCS保存: gs://%s/%s", GCS_BUCKET, path)


# ============================================================
# 対象企業の読み込み
# ============================================================

def load_target_companies(args: argparse.Namespace) -> list[dict]:
    """monthly_adapter_index.csv から処理対象企業を返す。

    - confirmed_no_monthly は除外（月次開示なしが確定済み）
    - active 銘柄はスクレイピングせずランログに already_active として記録する
    - --tickers で特定銘柄指定
    """
    if not INDEX_CSV.exists():
        logger.error("INDEX_CSV が見つかりません: %s", INDEX_CSV)
        return []

    only_tickers = set(args.tickers) if args.tickers else None
    only_categories = set(args.categories) if getattr(args, "categories", None) else None

    companies: list[dict] = []

    with open(INDEX_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row.get("ticker", "").strip()
            category = row.get("category", "").strip()

            # 月次開示なし確定 → 完全除外
            if category == "confirmed_no_monthly":
                continue

            # ティッカー絞り込み
            if only_tickers and ticker not in only_tickers:
                continue

            # カテゴリ絞り込み
            if only_categories and category not in only_categories:
                continue

            # ETF / 廃止 / REIT は除外
            note = row.get("adapter_note", "")
            if any(x in note for x in ["ETF", "上場廃止", "REIT", "投資法人"]):
                continue

            companies.append({
                "ticker": ticker,
                "company_name": row.get("company_name", "").strip(),
                "monthly_page_url": row.get("monthly_page_url", "").strip(),
                "domain": row.get("domain", "").strip(),
                "category": category,
                "adapter_note": note,
                "already_active": category == "active",  # active 銘柄はスクレイピングスキップ
            })

    active_cnt = sum(1 for c in companies if c["already_active"])
    scrape_cnt = len(companies) - active_cnt
    logger.info("処理対象: %d社（active=%d 記録のみ / 要スクレイピング=%d）",
                len(companies), active_cnt, scrape_cnt)
    return companies


# ============================================================
# Yahoo Japan 検索ユーティリティ（Google は reCAPTCHA が厳しく使用不能のため置換）
# ============================================================

def _extract_yahoo_links(html: str) -> list[dict]:
    """Yahoo Japan 検索結果 HTML から外部リンク URL + タイトルを順に返す。

    Yahoo は検索結果を直接URLで出力する（リダイレクタ無し）。
    Yahoo 自身のドメイン（search.yahoo.co.jp 等）は除外する。
    """
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    seen: set[str] = set()

    yahoo_internal = (
        "search.yahoo.co.jp", "login.yahoo.co.jp", "www.yahoo.co.jp",
        "chiebukuro.yahoo.co.jp", "map.yahoo.co.jp", "shopping.yahoo.co.jp",
        "realestate.yahoo.co.jp", "promo.yahoo.co.jp", "help.yahoo.co.jp",
    )

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        netloc = urlparse(href).netloc
        if netloc in yahoo_internal or netloc.endswith(".yahoo.co.jp"):
            continue
        if href in seen:
            continue
        seen.add(href)
        title = a.get_text(" ", strip=True)[:200]
        results.append({"url": href, "title": title})
    return results


def _yahoo_search(query: str, max_results: int = 50) -> list[dict]:
    """curl_cffi で Yahoo Japan 検索し、ページングで ~max_results 件取得する。

    1ページ10件なので `&b=1, &b=11, ...` で複数ページ取得。
    """
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        logger.warning("curl_cffi 未インストール。pip install curl_cffi")
        return []

    all_results: list[dict] = []
    seen: set[str] = set()
    pages_needed = (max_results + 9) // 10

    for page_idx in range(pages_needed):
        b = page_idx * 10 + 1
        search_url = (
            f"https://search.yahoo.co.jp/search?"
            f"p={quote_plus(query)}&ei=UTF-8&b={b}"
        )
        try:
            resp = cffi_requests.get(
                search_url,
                headers=_SEARCH_HEADERS,
                impersonate="chrome124",
                timeout=15,
            )
            if resp.status_code != 200:
                break
            page_results = _extract_yahoo_links(resp.text)
            for r in page_results:
                if r["url"] not in seen:
                    seen.add(r["url"])
                    all_results.append(r)
                    if len(all_results) >= max_results:
                        return all_results
            if not page_results:
                break
            time.sleep(random.uniform(0.8, 1.5))
        except Exception as e:
            logger.debug("Yahoo検索失敗 [%s b=%d]: %s", query, b, e)
            break
    return all_results


def _yahoo_search_pw(query: str, pw_context, max_results: int = 50) -> list[dict]:
    """Playwright で Yahoo Japan 検索しリンクを返す。curl_cffi 失敗時のフォールバック。"""
    all_results: list[dict] = []
    seen: set[str] = set()
    pages_needed = (max_results + 9) // 10

    for page_idx in range(pages_needed):
        b = page_idx * 10 + 1
        search_url = (
            f"https://search.yahoo.co.jp/search?"
            f"p={quote_plus(query)}&ei=UTF-8&b={b}"
        )
        page = None
        try:
            page = pw_context.new_page()
            _Stealth().apply_stealth_sync(page)
            time.sleep(random.uniform(0.8, 1.6))
            page.goto(search_url, timeout=20000, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=10000)
            html = page.content()
            page_results = _extract_yahoo_links(html)
            for r in page_results:
                if r["url"] not in seen:
                    seen.add(r["url"])
                    all_results.append(r)
                    if len(all_results) >= max_results:
                        return all_results
            if not page_results:
                break
        except Exception as e:
            logger.debug("Playwright Yahoo検索失敗 [%s b=%d]: %s", query, b, e)
            break
        finally:
            if page:
                try:
                    page.close()
                except Exception:
                    pass
    return all_results


# ============================================================
# Google 検索（H-1 知見 048 テンプレで復元）
# ============================================================
# 後方互換: 旧 Yahoo スタブ経由の動作は curl_cffi Google → Playwright Google →
# Yahoo フォールバックの順で試すように改修。


def _extract_google_links(html: str) -> list[dict]:
    """Google 検索結果 HTML から外部リンク URL + タイトルを順に返す。

    2026-04-21 案 A 改修: Google は 2025-09 以降 DOM を変更し単純な `<a href>` 直取り
    では結果が取れなくなった。以下の 2 段戦略で抽出する:
      (1) `h3` の親 `<a>` を取る（h3 はタイトル、親 a はリンク先の構造が比較的安定）
      (2) フォールバック: 従来の全 `<a href>` スキャン（一部ブロックで拾える場合向け）

    `/url?q=` 形式のリダイレクトをデコードし、Google 自身・レートリミット系の
    汚染ドメインは除外する。
    """
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    seen: set[str] = set()

    def _consider(a_tag) -> None:
        """1 つの <a> 要素を結果リストに追加する（重複・汚染は除外）。"""
        href = a_tag.get("href", "")
        if not href:
            return
        if href.startswith("/url?"):
            qs = parse_qs(urlparse(href).query)
            href = qs.get("q", [""])[0]
        if not href.startswith("http"):
            return
        netloc = urlparse(href).netloc.lstrip("www.")
        if any(netloc == d or netloc.endswith("." + d) for d in _GOOGLE_CONTAMINATION_DOMAINS):
            return
        if href in seen:
            return
        seen.add(href)
        title = a_tag.get_text(" ", strip=True)[:200]
        results.append({"url": href, "title": title})

    # 1) h3 の親 <a> ベース（Google の新 DOM で最も安定）
    for h3 in soup.find_all("h3"):
        parent = h3.find_parent("a")
        if parent is not None:
            _consider(parent)

    # 2) フォールバック: 全 <a href> スキャン（旧 DOM 互換 & udm=14 プレーンHTML 向け）
    for a in soup.find_all("a", href=True):
        _consider(a)

    return results


def _google_search_cffi(query: str) -> list[dict]:
    """curl_cffi + chrome124 で Google 検索（知見 048 方法1）。

    2026-04-21 案 A 改修: `udm=14` を付与して "10 blue links" モード（AI Overview 除外）
    を要求。JS レンダリング依存を外し、プレーン HTML 寄りの応答を引き出す。

    Returns:
        検索結果 dict のリスト（{url, title}）。失敗時は空 list。
    """
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        logger.warning("curl_cffi 未インストール → Google curl_cffi 検索スキップ")
        return []
    # udm=14: AI Overview を除外した伝統的 10 blue links モード
    search_url = f"https://www.google.com/search?q={quote_plus(query)}&udm=14&hl=ja&gl=jp&num=30"
    try:
        resp = cffi_requests.get(
            search_url,
            headers=_SEARCH_HEADERS,
            impersonate="chrome124",
            timeout=15,
        )
        if resp.status_code != 200:
            logger.debug("Google curl_cffi HTTP %d [%s]", resp.status_code, query)
            return []
        return _extract_google_links(resp.text)
    except Exception as e:
        logger.debug("Google curl_cffi 失敗 [%s]: %s", query, e)
        return []


def _google_search_pw_raw(query: str, pw_context) -> list[dict]:
    """Playwright で Google 検索（curl_cffi 失敗時のフォールバック）。

    2026-04-21 案 A 改修: `udm=14` 付与で AI Overview 除外。
    """
    results: list[dict] = []
    # udm=14: AI Overview を除外した伝統的 10 blue links モード
    search_url = f"https://www.google.com/search?q={quote_plus(query)}&udm=14&hl=ja&gl=jp&num=30"
    page = None
    try:
        page = pw_context.new_page()
        _Stealth().apply_stealth_sync(page)
        time.sleep(random.uniform(0.8, 1.6))
        page.goto(search_url, timeout=20000, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        html = page.content()
        results = _extract_google_links(html)
    except Exception as e:
        logger.debug("Playwright Google失敗 [%s]: %s", query, e)
    finally:
        if page:
            try:
                page.close()
            except Exception:
                pass
    return results


# ============================================================
# SearXNG 検索（案 B: 公開 SearXNG インスタンス HTTP 直叩き）
# ============================================================
# 2026-04-21 案 B: Google/Yahoo が DOM 変更 + UA detection で実効ゼロのため、
# 公開 SearXNG の JSON API をメタ検索経路として追加する。
#
# 前提（2026-04-21 調査）:
#   - searx.space 上の 80 インスタンス中、JSON API を有効にしているのは少数派
#   - 動作確認済 (JSON format で 5 件以上返るインスタンス):
#       - https://searxng.cups.moe/      (IR/月次 KPI を高精度で返す、優先)
#       - https://search.wdpserver.com/  (google/bing/brave/ddg 有効)
#       - https://search.seddens.net/    (同上)
#       - https://searx.oloke.xyz/       (同上)
#       - https://searx.perennialte.ch/  (70 engine のコンパクト構成)
#       - https://etsi.me/               (日本語 OK、EC ドメインが多い)
#       - https://search.mdosch.de/      (日本語クエリで英語結果が出る傾向、最後に回す)
#   - レート制限は厳しめ (短時間に連続リクエストで 429、数分クールダウン推奨)
#
# 恒久ルール:
#   - 各 query で上から順に試行し、最初の JSON 成功をそのまま返す
#   - 429/他の失敗は次のインスタンスへ即座にスワップ
#   - 全インスタンス失敗時は空 list（呼び出し元で yahoo へフォールバック）
_SEARXNG_INSTANCES: list[str] = [
    "https://searxng.cups.moe/",
    "https://search.wdpserver.com/",
    "https://search.seddens.net/",
    "https://searx.oloke.xyz/",
    "https://searx.perennialte.ch/",
    "https://etsi.me/",
    "https://search.mdosch.de/",
]


def _searxng_search(query: str, max_results: int = 30) -> list[dict]:
    """公開 SearXNG インスタンスの JSON API で検索する（案 B）。

    複数インスタンスを順に叩き、最初に成功した結果を返す。
    Google/Bing/Brave/DuckDuckGo のメタ検索で汚染が少ない。

    Args:
        query: 検索クエリ文字列。
        max_results: 返却する結果上限。

    Returns:
        dict のリスト（{url, title}）。全インスタンス失敗時は空 list。
    """
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        logger.warning("curl_cffi 未インストール → SearXNG 検索スキップ")
        return []

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
            "Gecko/20100101 Firefox/128.0"
        ),
        "Accept": "application/json",
        "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
    }

    for instance in _SEARXNG_INSTANCES:
        search_url = f"{instance.rstrip('/')}/search"
        params = {
            "q": query,
            "format": "json",
            "language": "ja",
            "safesearch": "0",
        }
        try:
            resp = cffi_requests.get(
                search_url,
                params=params,
                headers=headers,
                impersonate="firefox135",
                timeout=10,
                verify=False,
            )
        except Exception as e:
            logger.debug("SearXNG %s 接続失敗 [%s]: %s", instance, query, e)
            continue

        if resp.status_code != 200:
            logger.debug(
                "SearXNG %s HTTP %d [%s]", instance, resp.status_code, query
            )
            continue
        ct = resp.headers.get("content-type", "")
        if "json" not in ct:
            logger.debug(
                "SearXNG %s non-JSON 応答 ct=%s [%s]", instance, ct[:30], query
            )
            continue
        try:
            data = json.loads(resp.text)
        except Exception as e:
            logger.debug("SearXNG %s JSON decode 失敗 [%s]: %s", instance, query, e)
            continue

        raw_results = data.get("results") or []
        out: list[dict] = []
        seen: set[str] = set()
        for item in raw_results:
            url = item.get("url") or ""
            if not url or url in seen:
                continue
            # Google 汚染ドメインは SearXNG 側でも排除（保険）
            netloc = urlparse(url).netloc.lstrip("www.")
            if any(
                netloc == d or netloc.endswith("." + d)
                for d in _GOOGLE_CONTAMINATION_DOMAINS
            ):
                continue
            seen.add(url)
            out.append(
                {
                    "url": url,
                    "title": (item.get("title") or "")[:200],
                }
            )
            if len(out) >= max_results:
                break
        if out:
            logger.debug(
                "SearXNG %s OK %d results [%s]", instance, len(out), query
            )
            return out
        # 結果ゼロのインスタンスは次を試す
        logger.debug("SearXNG %s 結果 0 件 [%s]", instance, query)

    return []


# search_source を run log に記録するためのスレッドローカル（最後の検索ソース）
_LAST_SEARCH_SOURCE = {"value": None}


def _google_search(query: str) -> list[str]:
    """Google curl_cffi → SearXNG → Yahoo フォールバック（Playwright は _search で扱う）。"""
    res = _google_search_cffi(query)
    if res:
        _LAST_SEARCH_SOURCE["value"] = "google_cffi"
        return [r["url"] for r in res]
    # SearXNG フォールバック（案 B）
    sres = _searxng_search(query)
    if sres:
        _LAST_SEARCH_SOURCE["value"] = "searxng"
        return [r["url"] for r in sres]
    # Yahoo フォールバック
    yres = _yahoo_search(query)
    if yres:
        _LAST_SEARCH_SOURCE["value"] = "yahoo_cffi"
    return [r["url"] for r in yres]


def _google_search_pw(query: str, pw_context) -> list[str]:
    """Playwright Google → SearXNG → Playwright Yahoo フォールバック。"""
    res = _google_search_pw_raw(query, pw_context)
    if res:
        _LAST_SEARCH_SOURCE["value"] = "google_pw"
        return [r["url"] for r in res]
    # 2026-04-21 案 B: Yahoo PW の前に SearXNG を試す
    sres = _searxng_search(query)
    if sres:
        _LAST_SEARCH_SOURCE["value"] = "searxng"
        return [r["url"] for r in sres]
    yres = _yahoo_search_pw(query, pw_context)
    if yres:
        _LAST_SEARCH_SOURCE["value"] = "yahoo_pw"
    return [r["url"] for r in yres]


# 後方互換のためエイリアス（上部の IS_CLOUD_RUN 判定ベースの変数を参照）
_RATE_LIMIT_SLEEP_SEC = _RATE_LIMIT_SLEEP_FIRST
_RATE_LIMIT_SLEEP_SEC2 = _RATE_LIMIT_SLEEP_SECOND

# 会社名サフィックス除去（短縮名生成用）
_COMPANY_SUFFIX_RE = re.compile(
    r"[\s　]*(?:ホールディングス|ホールディング|ＨＤ|ＨＤ．|HD|グループホールディングス|"
    r"グループ|株式会社|（株）|\(株\)|Co\.,?Ltd\.?|Corporation|Corp\.?|Inc\.?)[\s　]*$",
    re.IGNORECASE,
)


def _short_name(company_name: str) -> str:
    """会社名からホールディングス等のサフィックスを除いた短縮名を返す。"""
    return _COMPANY_SUFFIX_RE.sub("", company_name).strip()


def _is_rate_limited(links: list[str]) -> bool:
    """全リンクが Google 汚染ドメインのみ → レートリミット判定。

    H-3: 知見 049 と同一集合 (_GOOGLE_CONTAMINATION_DOMAINS) を使う。
    netloc は lstrip("www.") で正規化して比較する。
    """
    if not links:
        return False

    def _hit(netloc: str) -> bool:
        n = netloc.lstrip("www.")
        return any(n == d or n.endswith("." + d) for d in _GOOGLE_CONTAMINATION_DOMAINS)

    return all(_hit(urlparse(lnk).netloc) for lnk in links)


def _is_ir_specific_domain(url: str) -> bool:
    """URLが IRページ専用ドメイン（トップページではなくIRパス）かどうか判定。
    site: 検索を使う価値があるのはこの場合のみ。

    M-3: domain が scheme 無しの netloc のみ（"example.com"）で渡される場合、
    urlparse は path に全部入ってしまうため scheme を補完してから判定する。
    """
    if not url:
        return False
    normalized = url if "://" in url else f"https://{url}"
    parsed = urlparse(normalized)
    path = parsed.path.rstrip("/")
    # パスが空またはルートのみ → 単なるトップページ → site: 検索は無意味
    return bool(path) and path not in ("/ir", "")


def google_fallback_search(
    company_name: str,
    domain: str,
    tried_urls: set[str] | None = None,
    skip_domains: set[str] | None = None,
    pw_context=None,
    ticker: str | None = None,
) -> str:
    """Google 多段検索で月次ページ URL を探す（H-1 以降 curl_cffi Google 優先）。

    tried_urls: 完全一致でスキップする URL セット
    skip_domains: ドメインごとスキップ（スクレイプ失敗ドメインの再採用を防ぐ）
    pw_context: Playwright BrowserContext（stealth済み）
    ticker: 証券コード（ログ用途）

    フロー: _search が curl_cffi Google → Playwright Google → Yahoo の順で試行。
      Stage 1: "{短縮社名} 月次"
      Stage 2: "{社名} 月次 site:{ドメイン}"
      Stage 3: "{社名} 月次開示"
      Stage 4: "{社名} 月次 IR"
      Stage 5: "{社名} 月次売上"
      Stage 6: "{社名} monthly IR"
    """
    tried_urls = tried_urls or set()
    skip_domains = skip_domains or set()
    base_domain = domain.lstrip("www.") if domain else ""
    short = _short_name(company_name)

    def _first_valid(links: list[str]) -> str:
        for href in links:
            if href in tried_urls:
                continue
            rd = urlparse(href).netloc.lstrip("www.")
            if any(rd == sd or rd.endswith("." + sd) for sd in skip_domains):
                continue
            if not any(skip in rd for skip in SKIP_DOMAINS):
                return href
        return ""

    def _search(query: str) -> list[str]:
        """curl_cffi Google → Playwright Google → SearXNG → Yahoo の順で試行する。

        H-1: 初期実装（Google curl → Playwright Google → Yahoo curl → Yahoo PW）
        2026-04-21 案 B: Google DOM 変更で curl/PW 両方とも実効ゼロのため、
                        SearXNG メタ検索を Yahoo 直前に追加。
        """
        # 1) curl_cffi Google（軽量）
        res = _google_search_cffi(query)
        if res:
            _LAST_SEARCH_SOURCE["value"] = "google_cffi"
            return [r["url"] for r in res]
        # 2) Playwright Google（BOT 検知時の実ブラウザ迂回）
        if pw_context is not None:
            pw_res = _google_search_pw_raw(query, pw_context)
            if pw_res:
                _LAST_SEARCH_SOURCE["value"] = "google_pw"
                return [r["url"] for r in pw_res]
        # 3) SearXNG（案 B: 公開インスタンスの JSON メタ検索）
        sres = _searxng_search(query)
        if sres:
            _LAST_SEARCH_SOURCE["value"] = "searxng"
            return [r["url"] for r in sres]
        # 4) Yahoo（curl_cffi）
        yres = _yahoo_search(query)
        if yres:
            _LAST_SEARCH_SOURCE["value"] = "yahoo_cffi"
            return [r["url"] for r in yres]
        # 5) Yahoo（Playwright）
        if pw_context is not None:
            ypw = _yahoo_search_pw(query, pw_context)
            if ypw:
                _LAST_SEARCH_SOURCE["value"] = "yahoo_pw"
                return [r["url"] for r in ypw]
        _LAST_SEARCH_SOURCE["value"] = "none"
        return []

    def _log_links(links: list[str]) -> None:
        for lnk in links[:5]:
            rd = urlparse(lnk).netloc.lstrip("www.")
            reason = ""
            if lnk in tried_urls:
                reason = "tried"
            elif any(rd == sd or rd.endswith("." + sd) for sd in skip_domains):
                reason = f"skip_domain({rd})"
            elif any(skip in rd for skip in SKIP_DOMAINS):
                reason = f"SKIP_DOMAINS({rd})"
            logger.info("  [%s] %s", reason or "OK", lnk)

    # ステージ構築
    stages: list[str] = []
    stages.append(f"{short} 月次")
    if base_domain and _is_ir_specific_domain(domain):
        stages.append(f"{company_name} 月次 site:{base_domain}")
    stages += [
        f"{company_name} 月次開示",
        f"{company_name} 月次 IR",
        f"{company_name} 月次売上",
        f"{company_name} monthly IR",
    ]

    for i, query in enumerate(stages, 1):
        links = _search(query)
        logger.info("Google Stage%d [%s]: %d件取得", i, query, len(links))
        _log_links(links)

        if _is_rate_limited(links):
            # 1回目: Playwright で再試行（まだ試してない場合）
            if pw_context is not None:
                logger.warning("Google レートリミット検出 (Stage%d)。Playwright で再試行...", i)
                time.sleep(random.uniform(5.0, 10.0))
                links = _google_search_pw(query, pw_context)
                logger.info("Google Stage%d Playwright再試行: %d件取得", i, len(links))
                if not _is_rate_limited(links):
                    hit = _first_valid(links)
                    if hit:
                        logger.info("Google Stage%d hit (PW再試行) [%s]: %s", i, query, hit)
                        return hit
                    sleep_sec = random.uniform(30.0, 60.0)
                    logger.info("  次ステージまで %.0f秒待機...", sleep_sec)
                    time.sleep(sleep_sec)
                    continue

            # まだブロック中: 5分待機後に curl_cffi で再試行
            logger.warning(
                "Google レートリミット継続。%.0f秒待機後にリトライ（時間はかかります）...",
                _RATE_LIMIT_SLEEP_SEC,
            )
            time.sleep(_RATE_LIMIT_SLEEP_SEC)
            links = _search(query)
            logger.info("Google Stage%d 5分後リトライ: %d件取得", i, len(links))

            if _is_rate_limited(links):
                # さらに10分待機して最終リトライ
                logger.warning(
                    "Google レートリミット継続。さらに%.0f秒待機後に最終リトライ...",
                    _RATE_LIMIT_SLEEP_SEC2,
                )
                time.sleep(_RATE_LIMIT_SLEEP_SEC2)
                links = _search(query)
                logger.info("Google Stage%d 10分後最終リトライ: %d件取得", i, len(links))

                if _is_rate_limited(links):
                    logger.warning("Google レートリミット解消せず。スキップ（no_links_confirmed にしない）")
                    return "RATE_LIMITED"

        hit = _first_valid(links)
        if hit:
            logger.info("Google Stage%d hit [%s]: %s", i, query, hit)
            return hit

        # 検索間: 30〜60秒ランダム待機（人間らしく）
        sleep_sec = random.uniform(30.0, 60.0)
        logger.info("  次ステージまで %.0f秒待機...", sleep_sec)
        time.sleep(sleep_sec)

    return ""


# ============================================================
# パターン推定（create_adapters_auto.py から移植）
# ============================================================

def _infer_href_pattern(hrefs: list[str]) -> str | None:
    """href リストから共通パターン（正規表現）を推測する。"""
    if not hrefs:
        return None

    paths = [urlparse(u).path for u in hrefs]

    # 拡張子を収集
    exts: set[str] = set()
    for p in paths:
        m = re.search(r"\.(pdf|xlsx|xls|csv)$", p, re.IGNORECASE)
        if m:
            exts.add(m.group(1).lower())
    ext_pat = "|".join(sorted(exts)) if exts else "pdf"

    if len(paths) == 1:
        # 1件: YYYYMM 形式の日付数字をプレースホルダーに置換してからエスケープ
        p = paths[0]
        p_normalized = re.sub(r"20\d{2}[01]\d", "YYYYMM_PH", p)
        p_esc = re.escape(p_normalized)
        return p_esc.replace("YYYYMM_PH", r"\d{6}")

    # 複数件: 共通プレフィックスディレクトリを求める
    parts_list = [p.split("/") for p in paths]
    min_len = min(len(p) for p in parts_list)
    common: list[str] = []
    for i in range(min_len):
        segments = {p[i] for p in parts_list}
        if len(segments) == 1:
            common.append(list(segments)[0])
        else:
            break

    if common:
        prefix = "/".join(common)
        return re.escape(prefix) + r".*\.(" + ext_pat + r")"

    return r"\.(" + ext_pat + r")"


def _pick_best_monthly_subpage(
    monthly_links: list[dict], origin_url: str
) -> dict | None:
    """サブページ候補から同一ドメイン + ルート以外 + 月次キーワード含有で最適URLを選ぶ。

    scrape_and_classify のサブページ探索で monthly_links[0] を無条件採用していた
    （link_*_pattern=None 保存）落とし穴への対策。

    Args:
        monthly_links: scrape_and_classify が収集した {"text", "url"} のリスト
        origin_url: 呼び出し元ページ URL（同一ドメイン判定に使用）

    Returns:
        最適な候補 dict（スコアが最も高いもの）。候補が皆無なら None。
    """
    if not monthly_links:
        return None
    try:
        origin_netloc = urlparse(origin_url).netloc.lstrip("www.")
    except Exception:
        origin_netloc = ""

    scored: list[tuple[int, dict]] = []
    for link in monthly_links:
        url = link.get("url", "")
        text = link.get("text", "")
        if not url:
            continue
        try:
            parsed = urlparse(url)
        except Exception:
            continue
        netloc = parsed.netloc.lstrip("www.")
        path = parsed.path.rstrip("/")

        score = 0
        # 同一ドメイン優先（+3）
        if origin_netloc and netloc == origin_netloc:
            score += 3
        # ルートパスは避ける（-2）
        if path in ("", "/"):
            score -= 2
        # 月次キーワード: URL に含まれる (+2), テキストに含まれる (+1)
        if MONTHLY_TEXT.search(url):
            score += 2
        if MONTHLY_TEXT.search(text):
            score += 1
        # origin と完全一致は除外
        if url == origin_url:
            continue
        scored.append((score, link))

    if not scored:
        return None
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1]


def _infer_text_pattern(texts: list[str]) -> str | None:
    """リンクテキストから共通パターンを推測する。"""
    if not texts:
        return None

    candidates = ["月次", "月別", "売上", "業績", "monthly", "PDF", "開示", "報告"]
    found: list[str] = []
    non_empty = [t for t in texts if t]
    if not non_empty:
        return None
    for kw in candidates:
        if all(kw.lower() in t.lower() for t in non_empty):
            found.append(re.escape(kw))
        if len(found) >= 2:
            break

    if found:
        return "|".join(found)

    # 日付パターン（YYYY年MM月 等）が共通なら \d{4} を返す
    if all(re.search(r"\d{4}", t) for t in non_empty):
        return r"\d{4}"

    return None


# ============================================================
# Playwright ページ解析 + 分類
# ============================================================

def scrape_and_classify(context, ticker: str, url: str, company_name: str) -> dict:
    """Playwright で 1 URL を解析し、verdict と adapter_fields を返す。

    Args:
        context: Playwright BrowserContext
        ticker: 銘柄コード
        url: 調査 URL
        company_name: 会社名

    Returns:
        dict with keys:
          verdict: "active" | "no_links_confirmed" | "error"
          type: "eir_api" | "scrape_links" | "html_table" | None
          eir_code: str | None
          download_links: list[dict]
          monthly_links: list[dict]
          adapter_fields: dict（build_adapter に渡す追加フィールド）
          error: str | None
    """
    result: dict = {
        "ticker": ticker,
        "company_name": company_name,
        "url": url,
        "verdict": "no_links_confirmed",
        "type": None,
        "eir_code": None,
        "download_links": [],
        "monthly_links": [],
        "adapter_fields": {},
        "error": None,
    }

    if not url:
        result["error"] = "URL未設定"
        return result

    # Google 汚染チェック
    parsed = urlparse(url)
    if parsed.hostname in _GOOGLE_CONTAMINATION_DOMAINS:
        result["error"] = f"Google汚染URL: {url}"
        return result

    # H-4: main-page を 1 回だけ goto（Pattern 1/2/3 の re-goto 削減）。
    # サブページ探索は別 page インスタンスで行い、main_page は最初の url のまま温存する。
    page = context.new_page()
    _Stealth().apply_stealth_sync(page)
    sub_page = None  # 遅延作成（サブページ探索が不要な場合の余計な page 生成を避ける）
    captured_eir_js: list[tuple[str, str]] = []

    def on_request(req):
        m = EIR_JS_RE.search(req.url)
        if m:
            captured_eir_js.append((m.group(1), m.group(2)))

    page.on("request", on_request)

    def _get_sub_page():
        """サブページ探索用の別 page インスタンスを遅延作成して返す。"""
        nonlocal sub_page
        if sub_page is None:
            sub_page = context.new_page()
            _Stealth().apply_stealth_sync(sub_page)
        return sub_page

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
        if urlparse(current_url).hostname in _GOOGLE_CONTAMINATION_DOMAINS:
            result["error"] = f"リダイレクト後Google汚染: {current_url}"
            return result

        html = page.content()

        # ---- eIR 検出 ----
        eir_detected = False
        eir_codes: list[str] = []

        if EIR_PATTERN.search(html):
            eir_detected = True
            eir_codes = list(set(EIR_CODE_RE.findall(html)))

        if captured_eir_js:
            eir_detected = True
            eir_codes = list(set(eir_codes + [js[1] for js in captured_eir_js]))

        # eIR キーワード（_detect_page_type の eir 判定を Playwright 版に統合）
        if "eirPassCore" in html or "eir_common.js" in html or "setParts(" in html:
            eir_detected = True

        if eir_detected and eir_codes:
            # eIR 検出でも、ページ内に月次 HTML テーブルが直接存在する場合は html_table を優先
            # （eIR ウィジェットが装飾用で、実データは HTML テーブルのケース: 8153 モスフード等）
            soup_for_eir = BeautifulSoup(html, "html.parser")
            has_inline_monthly_table = False
            for tbl in soup_for_eir.find_all("table"):
                cells_text = tbl.get_text(" ", strip=True)
                month_matches = re.findall(r"\d+月", cells_text)
                if len(set(month_matches)) >= 3 and re.search(r"\d{2,3}\.\d", cells_text):
                    has_inline_monthly_table = True
                    break
            if has_inline_monthly_table:
                logger.info("  eIR検出だが月次HTMLテーブルあり → html_table 優先")
                result["verdict"] = "active"
                result["type"] = "html_table"
                return result
            result["verdict"] = "active"
            result["type"] = "eir_api"
            result["eir_code"] = eir_codes[0]
            result["adapter_fields"] = {"eir_company_code": eir_codes[0]}
            return result

        # ---- ダウンロード・月次リンク収集 ----
        # URL 自体が月次ページを示す場合（例: /ir/monthly/, /monthly.html）は
        # そのページ上の全 DL リンクを月次扱いにする
        url_is_monthly_page = bool(MONTHLY_TEXT.search(url))

        # URL が月次ページの場合 networkidle まで追加待機（JS レンダリング対策）
        if url_is_monthly_page:
            try:
                page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
                time.sleep(1.0)
            except Exception:
                pass

        links = page.query_selector_all("a[href]")
        download_links: list[dict] = []
        monthly_links: list[dict] = []

        for link in links:
            try:
                href = link.get_attribute("href") or ""
                text = (link.inner_text() or "").strip()
                # M-4: URL 正規化 + Google 汚染チェックを _abs_url に集約
                href = _abs_url(url, href)
                if not href:
                    continue

                is_download = bool(DOWNLOAD_EXT.search(href))
                # URL が月次ページなら全 DL リンクを月次扱い
                # ただし NON_MONTHLY_EXCLUDE に該当するテキストは除外
                # （9005東急・3083スターシーズ・3041ビューティカダンHD対応）
                is_monthly_text = bool(MONTHLY_TEXT.search(text) or MONTHLY_TEXT.search(href))
                is_excluded = bool(NON_MONTHLY_EXCLUDE.search(text))
                is_monthly = (url_is_monthly_page or is_monthly_text) and not is_excluded

                if is_download:
                    download_links.append({
                        "text": text[:80],
                        "url": href,
                        "monthly_match": is_monthly,
                    })
                elif is_monthly:
                    monthly_links.append({
                        "text": text[:80],
                        "url": href,
                    })
            except Exception:
                pass

        result["download_links"] = download_links
        result["monthly_links"] = monthly_links

        # ---- HTML table 検出 (L-2: iframe 含む統合判定) ----
        has_monthly_table = _detect_monthly_table(page)

        # ---- サブページ探索（月次ページリンクあり、DLリンクなし） ----
        # H-4: sub_page（別 page インスタンス）で遷移し、main page を url のまま温存する
        if not download_links and monthly_links:
            sp = _get_sub_page()
            for mlink in monthly_links[:3]:
                sub_url = mlink["url"]
                if sub_url == url:
                    continue
                sub_is_monthly = bool(MONTHLY_TEXT.search(sub_url))
                try:
                    sp.goto(sub_url, wait_until="load", timeout=TIMEOUT_MS)
                    time.sleep(1.0)
                    for link in sp.query_selector_all("a[href]"):
                        try:
                            href = link.get_attribute("href") or ""
                            text = (link.inner_text() or "").strip()
                            href = _abs_url(sub_url, href)
                            if not href:
                                continue
                            if DOWNLOAD_EXT.search(href):
                                # NON_MONTHLY_EXCLUDE に該当するテキストは除外
                                is_excluded = bool(NON_MONTHLY_EXCLUDE.search(text))
                                is_monthly_match = (sub_is_monthly or bool(
                                    MONTHLY_TEXT.search(text) or MONTHLY_TEXT.search(href)
                                )) and not is_excluded
                                download_links.append({
                                    "text": text[:80],
                                    "url": href,
                                    "monthly_match": is_monthly_match,
                                    "from_sub": sub_url,
                                })
                        except Exception:
                            pass
                except Exception:
                    pass

        # ---- Pattern 1: IR サブページ探索（DLリンクも月次リンクもなし時） ----
        # メインIRページにリンクがない場合、IR関連サブリンクを1階層だけ辿る
        # 例: 1873 日本ハウスHD → /company/ir/ → "2026年" → /irlist/?y=2026 にPDFあり
        # H-4: main page は url のまま温存（サブページは sub_page で辿る）→ re-goto 不要
        if not download_links and not monthly_links and not has_monthly_table:
            logger.info("[%s] メインページにリンクなし。IRサブページ探索開始", ticker)

            sub_candidates: list[dict] = []
            for link in page.query_selector_all("a[href]"):
                try:
                    href = link.get_attribute("href") or ""
                    text = (link.inner_text() or "").strip()
                    href = _abs_url(url, href)
                    if not href:
                        continue
                    # 同一ドメインのIR関連リンクのみ対象
                    if urlparse(href).netloc != urlparse(url).netloc:
                        continue
                    if href == url:
                        continue
                    if IR_SUBPAGE_KEYWORDS.search(href) or IR_SUBPAGE_KEYWORDS.search(text):
                        sub_candidates.append({"url": href, "text": text})
                except Exception:
                    pass

            # 最大5件のサブページを探索
            sp = _get_sub_page()
            for sub_cand in sub_candidates[:5]:
                sub_url = sub_cand["url"]
                logger.info("[%s] IRサブページ探索: %s (%s)", ticker, sub_url, sub_cand["text"][:40])
                sub_is_monthly = bool(MONTHLY_TEXT.search(sub_url))
                try:
                    sp.goto(sub_url, wait_until="load", timeout=TIMEOUT_MS)
                    time.sleep(1.0)
                    for link in sp.query_selector_all("a[href]"):
                        try:
                            href = link.get_attribute("href") or ""
                            text = (link.inner_text() or "").strip()
                            href = _abs_url(sub_url, href)
                            if not href:
                                continue
                            if DOWNLOAD_EXT.search(href):
                                is_excluded = bool(NON_MONTHLY_EXCLUDE.search(text))
                                is_monthly_match = (sub_is_monthly or bool(
                                    MONTHLY_TEXT.search(text) or MONTHLY_TEXT.search(href)
                                )) and not is_excluded
                                download_links.append({
                                    "text": text[:80],
                                    "url": href,
                                    "monthly_match": is_monthly_match,
                                    "from_sub": sub_url,
                                })
                        except Exception:
                            pass
                    # サブページ内の月次リンクも収集
                    for link in sp.query_selector_all("a[href]"):
                        try:
                            href = link.get_attribute("href") or ""
                            text = (link.inner_text() or "").strip()
                            href = _abs_url(sub_url, href)
                            if not href:
                                continue
                            if not DOWNLOAD_EXT.search(href) and (MONTHLY_TEXT.search(text) or MONTHLY_TEXT.search(href)):
                                monthly_links.append({
                                    "text": text[:80],
                                    "url": href,
                                    "from_sub": sub_url,
                                })
                        except Exception:
                            pass
                    # DLリンクが見つかったら探索終了
                    if any(lk.get("monthly_match") for lk in download_links):
                        logger.info("[%s] IRサブページからDLリンク発見", ticker)
                        break
                except Exception:
                    pass

        # ---- Pattern 2: iframe 内コンテンツ探索（XJ-Storage / eIR ウィジェット） ----
        # iframe 内に月次PDFリンクやeIRウィジェットが埋め込まれているケース
        # 例: 3561 力の源HD → iframe 内に XJ-Storage ウィジェット
        # H-4: main page は url のまま温存されているので re-goto 不要
        if not download_links and not has_monthly_table and result["type"] != "eir_api":
            try:
                frames = page.frames
                if len(frames) > 1:  # frames[0] はメインフレーム
                    logger.info("[%s] iframe %d件検出。子フレーム解析開始", ticker, len(frames) - 1)
                    for frame in frames[1:]:
                        try:
                            frame_url = frame.url
                            # eIR 検出（iframe 内）
                            frame_html = frame.content()
                            if EIR_PATTERN.search(frame_html) or EIR_PATTERN.search(frame_url):
                                frame_eir_codes = list(set(EIR_CODE_RE.findall(frame_html)))
                                if not frame_eir_codes:
                                    frame_eir_codes = list(set(EIR_CODE_RE.findall(frame_url)))
                                if frame_eir_codes:
                                    logger.info("[%s] iframe内eIR検出: code=%s", ticker, frame_eir_codes[0])
                                    result["verdict"] = "active"
                                    result["type"] = "eir_api"
                                    result["eir_code"] = frame_eir_codes[0]
                                    result["adapter_fields"] = {"eir_company_code": frame_eir_codes[0]}
                                    break

                            # XJ-Storage 検出
                            if "xj-storage" in frame_url.lower() or "xj-storage" in frame_html.lower():
                                logger.info("[%s] iframe内XJ-Storage検出: %s", ticker, frame_url[:100])
                                # XJ-Storage 内のDLリンク収集
                                for link in frame.query_selector_all("a[href]"):
                                    try:
                                        href = link.get_attribute("href") or ""
                                        text = (link.inner_text() or "").strip()
                                        href = _abs_url(frame_url, href)
                                        if not href:
                                            continue
                                        if DOWNLOAD_EXT.search(href):
                                            is_monthly_match = bool(
                                                MONTHLY_TEXT.search(text) or MONTHLY_TEXT.search(href)
                                            ) and not bool(NON_MONTHLY_EXCLUDE.search(text))
                                            download_links.append({
                                                "text": text[:80],
                                                "url": href,
                                                "monthly_match": is_monthly_match,
                                                "from_iframe": frame_url,
                                            })
                                    except Exception:
                                        pass

                            # 汎用: iframe 内の DL リンク収集
                            for link in frame.query_selector_all("a[href]"):
                                try:
                                    href = link.get_attribute("href") or ""
                                    text = (link.inner_text() or "").strip()
                                    href = _abs_url(frame_url, href)
                                    if not href:
                                        continue
                                    if DOWNLOAD_EXT.search(href):
                                        is_monthly_match = bool(
                                            MONTHLY_TEXT.search(text) or MONTHLY_TEXT.search(href)
                                        ) and not bool(NON_MONTHLY_EXCLUDE.search(text))
                                        if is_monthly_match:
                                            download_links.append({
                                                "text": text[:80],
                                                "url": href,
                                                "monthly_match": True,
                                                "from_iframe": frame_url,
                                            })
                                except Exception:
                                    pass
                        except Exception as e:
                            logger.debug("[%s] iframe解析エラー: %s", ticker, str(e)[:100])
                            pass
            except Exception as e:
                logger.debug("[%s] iframe探索エラー: %s", ticker, str(e)[:100])

        # ---- Pattern 3: HTML テーブル月ヘッダー検出 ----
        # L-2: 最初の HTML table 検出 (_detect_monthly_table) で既に A+B パターン両方
        # + iframe も評価済みのため Pattern 3 の重複走査は不要。残置して明示ログのみ。
        if (
            not download_links
            and not has_monthly_table
            and result["type"] != "eir_api"
        ):
            logger.debug(
                "[%s] _detect_monthly_table は False。HTMLテーブル月ヘッダーも未検出",
                ticker,
            )

        # ---- verdict 判定 ----
        monthly_dl = [lk for lk in download_links if lk.get("monthly_match")]

        if monthly_dl:
            # DLリンクあり → パターン推定
            hrefs = [lk["url"] for lk in monthly_dl]
            texts = [lk["text"] for lk in monthly_dl]
            href_pat = _infer_href_pattern(hrefs) or DEFAULT_HREF_PATTERN
            text_pat = _infer_text_pattern(texts)
            best_page = monthly_dl[0].get("from_sub", url)

            result["verdict"] = "active"
            result["type"] = "scrape_links"
            result["adapter_fields"] = {
                "ir_page_url": best_page,
                "link_href_pattern": href_pat,
                "link_text_pattern": text_pat,
                "note": f"月次PDFリンク{len(monthly_dl)}件検出 best={monthly_dl[0]['url']}",
            }

        elif has_monthly_table:
            result["verdict"] = "active"
            result["type"] = "html_table"
            result["adapter_fields"] = {
                "ir_page_url": url,
                "css_selector": None,
                "note": "HTML表に月次データ検出。table_selector要調整",
            }

        elif monthly_links:
            # 月次ページリンクあり → 最適サブページを選出し 1 階層踏み込んで DL リンクを収集する
            # 旧実装は monthly_links[0] を即採用 + link_*_pattern=None で保存していたため
            # download_monthly が DL リンクを絞り込めず全件ダウンロードになるバグ（6 銘柄問題）を修正。
            best_cand = _pick_best_monthly_subpage(monthly_links, url)
            sub_download_links: list[dict] = []
            best_sub_url = best_cand["url"] if best_cand else ""
            if best_cand:
                sub_url = best_cand["url"]
                sub_is_monthly = bool(MONTHLY_TEXT.search(sub_url))
                sp = _get_sub_page()
                try:
                    sp.goto(sub_url, wait_until="load", timeout=TIMEOUT_MS)
                    time.sleep(1.0)
                    for link in sp.query_selector_all("a[href]"):
                        try:
                            href = link.get_attribute("href") or ""
                            text = (link.inner_text() or "").strip()
                            href = _abs_url(sub_url, href)
                            if not href:
                                continue
                            if DOWNLOAD_EXT.search(href):
                                is_excluded = bool(NON_MONTHLY_EXCLUDE.search(text))
                                is_monthly_match = (sub_is_monthly or bool(
                                    MONTHLY_TEXT.search(text) or MONTHLY_TEXT.search(href)
                                )) and not is_excluded
                                sub_download_links.append({
                                    "text": text[:80],
                                    "url": href,
                                    "monthly_match": is_monthly_match,
                                    "from_sub": sub_url,
                                })
                        except Exception:
                            pass
                except Exception as e:
                    logger.debug("[%s] subpage drill エラー: %s", ticker, str(e)[:100])

            monthly_dl_sub = [lk for lk in sub_download_links if lk.get("monthly_match")]
            if monthly_dl_sub:
                hrefs = [lk["url"] for lk in monthly_dl_sub]
                texts = [lk["text"] for lk in monthly_dl_sub]
                href_pat = _infer_href_pattern(hrefs) or DEFAULT_HREF_PATTERN
                text_pat = _infer_text_pattern(texts)
                result["download_links"].extend(sub_download_links)
                result["verdict"] = "active"
                result["type"] = "scrape_links"
                result["adapter_fields"] = {
                    "ir_page_url": best_sub_url,
                    "link_href_pattern": href_pat,
                    "link_text_pattern": text_pat,
                    "note": f"サブページ踏み込み{len(monthly_dl_sub)}件検出 best={monthly_dl_sub[0]['url']}",
                }
            else:
                # サブページを踏み込んだが DL リンクゼロ → skip 降格（CSV で manual_follow_up 化）
                result["verdict"] = "no_links_confirmed"
                result["type"] = None
                result["adapter_fields"] = {
                    "ir_page_url": best_sub_url or url,
                    "skip_reason": "subpage_no_links",
                    "note": (
                        f"月次ページリンク {len(monthly_links)} 件検出、サブページ踏み込み後に"
                        f"DLリンク無し（best={best_sub_url or 'N/A'}）"
                    ),
                }

        elif url_is_monthly_page:
            # URL が月次ページを示しているが DL リンク・HTML テーブルが取れなかった
            # （JS 遅延読み込み・SPA 等の可能性）→ 月次ページとして登録し要確認
            result["verdict"] = "active"
            result["type"] = "html_table"
            result["adapter_fields"] = {
                "ir_page_url": url,
                "css_selector": None,
                "note": "URL自体が月次ページだがコンテンツ未検出（JS遅延/SPA疑い）。要手動確認",
            }

        else:
            # リンクなし
            result["verdict"] = "no_links_confirmed"
            result["type"] = None

    except PWTimeout:
        result["error"] = "timeout"
        result["verdict"] = "error"
    except Exception as e:
        result["error"] = str(e)[:200]
        result["verdict"] = "error"
    finally:
        try:
            page.close()
        except Exception:
            pass
        # H-4: sub_page が作成済みなら併せてクローズ
        if sub_page is not None:
            try:
                sub_page.close()
            except Exception:
                pass

    return result


# ============================================================
# url_adapter.json 生成
# ============================================================

def _validate_scrape_links_adapter(adapter: dict) -> dict:
    """scrape_links 型 adapter の link_*_pattern None を検知し、skip に降格させる。

    H-5: download_monthly.py は link_href_pattern が None/欠落だと
    全 a[href] を対象にしてしまう。link_text_pattern も link_href_pattern も
    両方 None の状態でアダプターを active にすると無差別にファイルを拾ってしまうため、
    このケースは status=skip に落として CSV 側で manual_follow_up_required に分類する。

    href_pattern のデフォルト保証: 片方でも有効なら、href_pattern 欠落時に
    DEFAULT_HREF_PATTERN（`\\.(pdf|xlsx|xls|csv)$`）を補完する。

    Args:
        adapter: build_adapter 生成直後の adapter dict。

    Returns:
        検証済み adapter dict（必要に応じて status/skip_reason を書き換えた新 dict）。
    """
    if adapter.get("type") != "scrape_links":
        return adapter
    if adapter.get("status") != "active":
        return adapter
    href_pat = adapter.get("link_href_pattern")
    text_pat = adapter.get("link_text_pattern")
    if not href_pat and not text_pat:
        adapter = dict(adapter)
        adapter["status"] = "skip"
        adapter["skip_reason"] = "pattern_inference_failed"
        adapter["note"] = (
            (adapter.get("note") or "") + " | link_*_pattern 推定失敗 → 要手動確認"
        ).strip(" |")
        return adapter
    # href_pattern だけ欠落: デフォルト補完
    if not href_pat:
        adapter = dict(adapter)
        adapter["link_href_pattern"] = DEFAULT_HREF_PATTERN
    return adapter


def build_adapter(
    ticker: str,
    company_name: str,
    url: str,
    classify_result: dict,
) -> dict:
    """分類結果から新フォーマット url_adapter.json を生成する。

    Args:
        ticker: 銘柄コード
        company_name: 会社名
        url: 月次IRページ URL
        classify_result: scrape_and_classify の戻り値

    Returns:
        url_adapter.json の dict（新フォーマット）
    """
    verdict = classify_result.get("verdict", "no_links_confirmed")
    adapter_type = classify_result.get("type")
    fields = classify_result.get("adapter_fields", {})
    error = classify_result.get("error")

    adapter: dict = {
        "ticker": ticker,
        "company_name": company_name,
        "ir_page_url": fields.get("ir_page_url", url),
        "updated_at": _now_jst(),
        "last_checked": TODAY,
    }

    if verdict == "active":
        adapter["status"] = "active"
        adapter["type"] = adapter_type

        if adapter_type == "eir_api":
            adapter["eir_company_code"] = fields.get("eir_company_code")
            adapter["note"] = fields.get("note", f"eIRシステム検出。コード={fields.get('eir_company_code')}")

        elif adapter_type == "scrape_links":
            adapter["css_selector"] = fields.get("css_selector")
            adapter["link_text_pattern"] = fields.get("link_text_pattern")
            adapter["link_href_pattern"] = fields.get("link_href_pattern")
            adapter["note"] = fields.get("note", "スクレイプリンク型")

        elif adapter_type == "html_table":
            adapter["css_selector"] = fields.get("css_selector")
            adapter["table_selector"] = fields.get("table_selector")
            adapter["note"] = fields.get("note", "HTML表型。table_selector要調整")

        else:
            adapter["note"] = fields.get("note", "")

    elif verdict == "error":
        adapter["status"] = "skip"
        adapter["type"] = None
        adapter["skip_reason"] = f"スクレイプエラー: {error}"
        adapter["note"] = f"error: {error}"

    else:
        # no_links_confirmed（サブページ踏み込みゼロの場合に scrape_links 由来の
        # skip_reason/note を尊重する）
        adapter["status"] = "skip"
        adapter["type"] = None
        adapter["skip_reason"] = fields.get(
            "skip_reason", "月次開示ページ・ダウンロードリンク未発見"
        )
        adapter["note"] = fields.get("note", "no_links_confirmed")

    # H-5: pattern None 書き込み防止（scrape_links に限定して検証）
    adapter = _validate_scrape_links_adapter(adapter)
    return adapter


# ============================================================
# アダプター検証（dead link 除去）
# ============================================================

def validate_adapter_links(adapter: dict, ticker: str) -> dict:
    """アダプターの links リストから 404 になる URL を除外して返す。

    2686 ジーフット・4343 イオンファンタジー対応:
    既存アダプターの links に http_404 のリンクが残り、download_monthly で全件失敗する問題を修正。

    - scrape_links タイプのみ対象
    - links[].url に HEAD リクエストを送り、404 ならリストから除外
    - 全件除外された場合は adapter["status"] を "links_all_dead" にマーク
    """
    if adapter.get("type") != "scrape_links":
        return adapter

    links = adapter.get("links", [])
    if not links:
        return adapter

    # L-3: curl_cffi + chrome124 で HEAD → bot 判定回避（405 時は GET フォールバック）
    try:
        from curl_cffi import requests as _cffi_req
        _has_cffi = True
    except ImportError:
        import requests as _req_val
        _has_cffi = False

    def _probe(url: str) -> int | None:
        """URL の HTTP ステータスを返す。到達不能は None。"""
        if _has_cffi:
            try:
                r = _cffi_req.head(
                    url, headers=_SEARCH_HEADERS, impersonate="chrome124",
                    timeout=10, allow_redirects=True,
                )
                if r.status_code == 405:  # HEAD 不許可なら GET で確認
                    r = _cffi_req.get(
                        url, headers=_SEARCH_HEADERS, impersonate="chrome124",
                        timeout=10, allow_redirects=True, stream=True,
                    )
                return r.status_code
            except Exception:
                return None
        try:
            r = _req_val.head(url, timeout=10, allow_redirects=True, verify=False)
            return r.status_code
        except Exception:
            return None

    valid_links = []
    dead_count = 0
    for link in links:
        url = link.get("url", "")
        if not url:
            continue
        status = _probe(url)
        if status == 404:
            logger.info("[%s] 404 dead link 除外: %s", ticker, url[:80])
            dead_count += 1
            continue
        if status is None:
            logger.debug("[%s] リンク疎通確認失敗（保持）: %s", ticker, url[:60])
        valid_links.append(link)

    adapter = dict(adapter)
    adapter["links"] = valid_links
    if dead_count > 0:
        logger.info("[%s] dead link %d件除外 → 残り%d件", ticker, dead_count, len(valid_links))
    if not valid_links:
        adapter["status"] = "links_all_dead"
        logger.warning("[%s] 全リンクが404のため links_all_dead", ticker)
    return adapter


def _run_revalidate(args: argparse.Namespace, bucket) -> None:
    """--revalidate モード: active なアダプターの links を HTTP HEAD で再検証する。

    dead link を除外して GCS に書き戻す。全件 dead の場合は links_all_dead に変更し、
    インデックス CSV の category を no_links_confirmed に更新する。

    使い方:
        python scripts/update_monthly_adapters.py --revalidate
        python scripts/update_monthly_adapters.py --revalidate --tickers 2686 4343
    """
    logger.info("=== --revalidate モード開始 ===")

    # active 銘柄を CSV から取得
    if not INDEX_CSV.exists():
        logger.error("INDEX_CSV が見つかりません: %s", INDEX_CSV)
        return

    only_tickers = set(args.tickers) if args.tickers else None

    active_tickers: list[dict] = []
    with open(INDEX_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row.get("ticker", "").strip()
            category = row.get("category", "").strip()
            if category != "active":
                continue
            if only_tickers and ticker not in only_tickers:
                continue
            active_tickers.append({
                "ticker": ticker,
                "company_name": row.get("company_name", "").strip(),
            })

    if args.limit and args.limit > 0:
        active_tickers = active_tickers[: args.limit]

    logger.info("再検証対象: %d社（active）", len(active_tickers))

    revalidate_results: list[dict] = []
    dead_all_tickers: list[str] = []

    for company in active_tickers:
        ticker = company["ticker"]
        logger.info("[%s] %s 再検証中...", ticker, company["company_name"])

        if args.no_gcs or bucket is None:
            logger.warning("[%s] --no-gcs のため GCS url_adapter.json を読み込めません。スキップ", ticker)
            continue

        # #A 物理分離 (2026-04-20): url_adapter.json → url_adapter.json
        adapter = _load_json_from_gcs(bucket, f"monthly/meta/{ticker}/url_adapter.json")
        if not adapter:
            logger.info("[%s] url_adapter.json が存在しないためスキップ", ticker)
            continue

        if adapter.get("type") != "scrape_links":
            logger.info("[%s] type=%s は links 検証対象外。スキップ", ticker, adapter.get("type"))
            continue

        original_links_count = len(adapter.get("links", []))
        adapter = validate_adapter_links(adapter, ticker)
        adapter["updated_at"] = _now_jst()
        new_links_count = len(adapter.get("links", []))

        result = {
            "ticker": ticker,
            "company_name": company["company_name"],
            "original_links": original_links_count,
            "valid_links": new_links_count,
            "dead_links": original_links_count - new_links_count,
            "status": adapter.get("status", "active"),
        }
        revalidate_results.append(result)

        if adapter.get("status") == "links_all_dead":
            dead_all_tickers.append(ticker)
            logger.warning("[%s] links_all_dead → no_links_confirmed に変更", ticker)

        if not args.dry_run:
            try:
                _save_json_to_gcs(bucket, f"monthly/meta/{ticker}/url_adapter.json", adapter)
                logger.info("[%s] GCS 書き戻し OK", ticker)
            except Exception as e:
                logger.error("[%s] GCS 書き戻し失敗: %s", ticker, e)

        time.sleep(RATE_LIMIT_SEC)

    # links_all_dead になった銘柄のインデックスCSVを更新
    if dead_all_tickers and not args.dry_run:
        rows = []
        fieldnames = []
        with open(INDEX_CSV, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            for row in reader:
                if row.get("ticker", "").strip() in dead_all_tickers:
                    row["category"] = "no_links_confirmed"
                    row["updated_at"] = TODAY
                    row["adapter_note"] = "revalidate: links_all_dead"
                rows.append(row)

        import tempfile, shutil, time as _time
        with tempfile.NamedTemporaryFile("w", newline="", encoding="utf-8-sig",
                                         suffix=".csv", delete=False) as tf:
            tmp_path = tf.name
            writer = csv.DictWriter(tf, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        for _ in range(10):
            try:
                shutil.move(tmp_path, str(INDEX_CSV))
                break
            except PermissionError:
                _time.sleep(3)
        logger.info("インデックス CSV 更新: %d社を no_links_confirmed に変更", len(dead_all_tickers))

    # サマリー
    logger.info("")
    logger.info("=== --revalidate 完了サマリー ===")
    logger.info("  再検証対象: %d社", len(revalidate_results))
    for r in revalidate_results:
        logger.info(
            "  [%s] %s: original=%d valid=%d dead=%d status=%s",
            r["ticker"], r["company_name"],
            r["original_links"], r["valid_links"], r["dead_links"], r["status"],
        )
    logger.info("  links_all_dead → no_links_confirmed: %d社 %s",
                len(dead_all_tickers), dead_all_tickers)
    if args.dry_run:
        logger.info("  --dry-run: GCS 書き戻しをスキップしました")
    logger.info("=================================")


# ============================================================
# GCS 保存 / ローカル保存
# ============================================================

def upload_adapter(bucket, ticker: str, adapter: dict, no_gcs: bool) -> bool:
    """url_adapter.json を GCS にアップロード、または --no-gcs 時はローカル保存。

    Args:
        bucket: GCS Bucket オブジェクト（no_gcs=True 時は None でよい）
        ticker: 銘柄コード
        adapter: url_adapter.json の dict
        no_gcs: True なら GCS スキップ、ローカル保存

    Returns:
        成功 True / 失敗 False
    """
    if no_gcs:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        local_path = TMP_DIR / f"adapter_{ticker}.json"
        with open(local_path, "w", encoding="utf-8") as f:
            json.dump(adapter, f, ensure_ascii=False, indent=2)
        logger.info("  ローカル保存: %s", local_path)
        return True

    try:
        # #A 物理分離 (2026-04-20): url_adapter.json → url_adapter.json
        gcs_path = f"monthly/meta/{ticker}/url_adapter.json"
        _save_json_to_gcs(bucket, gcs_path, adapter)
        logger.info("  GCS upload OK: gs://%s/%s", GCS_BUCKET, gcs_path)
        return True
    except Exception as e:
        logger.error("  GCS upload FAIL [%s]: %s", ticker, e)
        return False


# ============================================================
# 既存 url_adapter.json の active チェック
# ============================================================

def is_already_active(bucket, ticker: str, no_gcs: bool) -> bool:
    """GCS の url_adapter.json が active かどうかを確認する。

    Args:
        bucket: GCS Bucket（no_gcs=True 時は None）
        ticker: 銘柄コード
        no_gcs: True なら常に False を返す（GCS 確認スキップ）

    Returns:
        active なら True
    """
    if no_gcs or bucket is None:
        return False
    existing = _load_json_from_gcs(bucket, f"monthly/meta/{ticker}/url_adapter.json")
    return existing.get("status") == "active"


# ============================================================
# ランログ保存
# ============================================================

LOG_GCS_PREFIX = "monthly/log"


def save_run_log(bucket, results_log: list[dict], no_gcs: bool) -> str:
    """処理結果をまとめた JSON ランログを保存する。

    常時呼び出す（--dry-run でも --no-gcs でも保存する）。

    Args:
        bucket: GCS Bucket（no_gcs=True 時は None）
        results_log: 1社ごとの処理結果リスト
        no_gcs: True ならローカル保存

    Returns:
        保存先パス（GCS URI またはローカルパス文字列）
    """
    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")

    # L-4: search_source 別内訳（curl_cffi/Playwright/Yahoo どのパスで URL 確定したか）
    search_source_counts: dict[str, int] = {}
    for r in results_log:
        src = r.get("search_source") or "n/a"
        search_source_counts[src] = search_source_counts.get(src, 0) + 1

    log_data = {
        "run_at": _now_jst(),
        "total": len(results_log),
        "activated": sum(1 for r in results_log if r.get("verdict") == "active"),
        "no_links": sum(1 for r in results_log if r.get("verdict") == "no_links_confirmed"),
        "error": sum(1 for r in results_log if r.get("verdict") == "error"),
        "url_not_found": sum(1 for r in results_log if r.get("verdict") == "url_not_found"),
        "search_source_breakdown": search_source_counts,
        "results": results_log,
    }

    filename = f"adapters_log_{ts}.json"

    if no_gcs or bucket is None:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        local_path = TMP_DIR / filename
        with open(local_path, "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
        logger.info("ランログ ローカル保存: %s", local_path)
        return str(local_path)
    else:
        gcs_path = f"{LOG_GCS_PREFIX}/{filename}"
        _save_json_to_gcs(bucket, gcs_path, log_data)
        gcs_uri = f"gs://{GCS_BUCKET}/{gcs_path}"
        logger.info("ランログ GCS 保存: %s", gcs_uri)
        logger.info("ダウンロード: gcloud storage cp %s ./data/tmp/", gcs_uri)
        return gcs_uri


# ============================================================
# インデックス CSV 差分更新（Step 4 統合）
# ============================================================

def update_index_csv(results_log: list[dict]) -> None:
    """results_log の内容で monthly_adapter_index.csv を差分更新する。

    - already_active はスクレイピングしていないため CSV 変更なし
    - それ以外（active/no_links_confirmed/error 等）は category/skip/updated_at を更新
    """
    if not INDEX_CSV.exists():
        logger.warning("INDEX_CSV が見つかりません。CSV 更新スキップ: %s", INDEX_CSV)
        return

    # 更新対象を verdict ごとにマップ（already_active は除外）
    update_map: dict[str, dict] = {}
    for r in results_log:
        verdict = r.get("verdict", "")
        if verdict == "already_active":
            continue
        adapter = r.get("adapter") or {}
        update_map[r["ticker"]] = {
            "category": verdict if verdict != "active" else "active",
            "skip": verdict != "active",
            "monthly_page_url": r.get("url", ""),
            "adapter_note": adapter.get("note", adapter.get("skip_reason", "")),
            "updated_at": r.get("processed_at", "")[:10],
            "type": adapter.get("type", ""),
        }

    if not update_map:
        logger.info("CSV 差分更新: 更新対象なし")
        return

    # CSV 読み込み → 更新 → 書き戻し
    rows = []
    fieldnames = []
    with open(INDEX_CSV, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for row in reader:
            ticker = row.get("ticker", "").strip()
            if ticker in update_map:
                upd = update_map[ticker]
                row["category"] = upd["category"]
                row["skip"] = str(upd["skip"])
                row["monthly_page_url"] = upd["monthly_page_url"]
                row["adapter_note"] = upd["adapter_note"]
                row["updated_at"] = upd["updated_at"]
                if "type" in fieldnames:
                    row["type"] = upd["type"]
            rows.append(row)

    import tempfile, shutil, time as _time
    with tempfile.NamedTemporaryFile("w", newline="", encoding="utf-8-sig",
                                     suffix=".csv", delete=False) as tf:
        tmp_path = tf.name
        writer = csv.DictWriter(tf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    for _ in range(10):
        try:
            shutil.move(tmp_path, str(INDEX_CSV))
            break
        except PermissionError:
            _time.sleep(3)
    else:
        logger.warning("CSV 書き込みロック解除失敗。%s に保存済み", tmp_path)
        return

    logger.info("monthly_adapter_index.csv 差分更新完了: %d社", len(update_map))


# ============================================================
# argparse
# ============================================================

def parse_args() -> argparse.Namespace:
    """コマンドライン引数を解析する。"""
    parser = argparse.ArgumentParser(
        description="月次開示アダプター一括更新スクリプト（統合版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "tickers",
        nargs="*",
        metavar="TICKER",
        help="処理する ticker を指定（省略時は --categories 対象全社）",
    )
    parser.add_argument(
        "--no-gcs",
        action="store_true",
        help="GCS 保存をスキップ。adapter_*.json を data/tmp/ にローカル保存",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="url_adapter.json を GCS に書かずにランログだけ保存する（テスト用）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="GCS に active な url_adapter.json があっても上書きする",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        dest="headless",
        help="Playwright をヘッドレスモードで起動（デフォルト ON）",
    )
    parser.add_argument(
        "--no-headless",
        action="store_false",
        dest="headless",
        help="Playwright をヘッドレスモードをオフにする（ローカルデバッグ用）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="最大処理件数（0 で無制限）",
    )
    parser.add_argument(
        "--categories",
        nargs="+",
        metavar="CAT",
        help="処理するカテゴリを指定（例: no_links_confirmed skip）。省略時は active 以外の全カテゴリ",
    )
    parser.add_argument(
        "--revalidate",
        action="store_true",
        help="active なアダプターの links を HTTP HEAD で再検証し、dead link を除外して GCS に書き戻す",
    )
    return parser.parse_args()


# ============================================================
# メイン
# ============================================================

def main() -> None:
    """メインエントリポイント。"""
    # M-6: SSL パッチは main() 内で明示適用する（モジュール import 時の副作用を回避）
    _install_ssl_patch()

    args = parse_args()

    logger.info("=== update_monthly_adapters.py 開始 ===")
    logger.info("実行環境: %s", "Cloud Run" if IS_CLOUD_RUN else "ローカル")
    logger.info("オプション: no_gcs=%s dry_run=%s force=%s headless=%s limit=%s revalidate=%s",
                args.no_gcs, args.dry_run, args.force, args.headless, args.limit,
                getattr(args, "revalidate", False))

    # GCS クライアント
    bucket = None
    if not args.no_gcs:
        try:
            gcs = _get_gcs_client()
            bucket = gcs.bucket(GCS_BUCKET)
            logger.info("GCS 接続 OK: gs://%s", GCS_BUCKET)
        except Exception as e:
            logger.error("GCS 接続失敗: %s", e)
            logger.info("--no-gcs モードで続行します")
            args.no_gcs = True

    # ---- --revalidate モード ----
    if getattr(args, "revalidate", False):
        _run_revalidate(args, bucket)
        return

    # 対象企業読み込み
    companies = load_target_companies(args)
    if not companies:
        logger.info("処理対象企業がありません。終了します。")
        return

    # Cloud Run タスク分割（並列実行でIP分散 → Google レートリミット回避）
    if TASK_COUNT > 1:
        companies = companies[TASK_INDEX::TASK_COUNT]
        logger.info(
            "タスク分割: TASK_INDEX=%d / TASK_COUNT=%d → %d社を担当",
            TASK_INDEX, TASK_COUNT, len(companies),
        )

    # --limit 適用
    if args.limit and args.limit > 0:
        companies = companies[: args.limit]
        logger.info("--limit %d 適用: %d社に絞り込み", args.limit, len(companies))

    logger.info("処理開始: %d社", len(companies))

    stats = {
        "total": len(companies),
        "skipped_active": 0,
        "url_not_found": 0,
        "activated": 0,
        "no_links": 0,
        "error": 0,
        "gcs_ok": 0,
        "gcs_fail": 0,
        "rate_limited_skip": 0,  # M-1: Google レートリミット継続でスキップした社数
    }
    results_log: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=args.headless,
            args=_BROWSER_ARGS,
        )
        context = browser.new_context(
            user_agent=_USER_AGENT,
            locale="ja-JP",
        )

        for i, company in enumerate(companies):
            ticker = company["ticker"]
            company_name = company["company_name"]
            url = company.get("monthly_page_url", "").strip()

            logger.info(
                "[%d/%d] %s %s  url=%s",
                i + 1, len(companies),
                ticker, company_name,
                url[:70] if url else "(URLなし)",
            )

            try:
                # ---- already_active（CSV で active 登録済み）→ スクレイピングせずランログ記録 ----
                if company.get("already_active") and not args.force:
                    logger.info("  → already_active（スクレイピングスキップ）")
                    stats["skipped_active"] += 1
                    results_log.append({
                        "ticker": ticker,
                        "company_name": company_name,
                        "url": url,
                        "verdict": "already_active",
                        "type": None,
                        "eir_code": None,
                        "adapter": None,
                        "download_links_count": 0,
                        "monthly_links_count": 0,
                        "error": None,
                        "processed_at": _now_jst(),
                    })
                    continue

                # ---- Step 1: URL が不明なら curl_cffi Google → PW → Yahoo フォールバック検索 ----
                if not url or url in ("", "not_found"):
                    domain = company.get("domain", "")
                    logger.info("  URL不明 → Google 多段検索開始")
                    _LAST_SEARCH_SOURCE["value"] = None
                    url = google_fallback_search(company_name, domain, pw_context=context, ticker=ticker)
                    if url == "RATE_LIMITED":
                        logger.warning("  Google レートリミット → no_links_confirmed に変更せずスキップ")
                        stats["rate_limited_skip"] = stats.get("rate_limited_skip", 0) + 1
                        continue
                    if not url:
                        logger.info("  → URL未発見 → no_links_confirmed 維持")
                        stats["url_not_found"] += 1
                        continue

                logger.info("  URL確定: %s", url[:100])

                # ---- Step 2: Playwright でページ解析 ----
                classify_result = scrape_and_classify(context, ticker, url, company_name)
                verdict = classify_result.get("verdict", "no_links_confirmed")
                logger.info(
                    "  verdict=%s type=%s error=%s",
                    verdict,
                    classify_result.get("type"),
                    classify_result.get("error"),
                )

                # ---- Step 2b: no_links_confirmed → Google 再検索で別 URL を試す ----
                if verdict == "no_links_confirmed":
                    domain = company.get("domain", "")
                    tried = {url}
                    # 失敗したドメインをスキップして別ドメインの URL を探す
                    failed_domain = urlparse(url).netloc.lstrip("www.")
                    new_url = google_fallback_search(
                        company_name, domain,
                        tried_urls=tried,
                        skip_domains={failed_domain} if failed_domain else None,
                        pw_context=context,
                        ticker=ticker,
                    )
                    if new_url == "RATE_LIMITED":
                        logger.warning("  Google レートリミット → no_links_confirmed に変更せずスキップ")
                        stats["rate_limited_skip"] = stats.get("rate_limited_skip", 0) + 1
                        continue
                    elif new_url and new_url != url:
                        logger.info("  Google 再検索 → 別 URL 発見: %s", new_url)
                        classify_result = scrape_and_classify(context, ticker, new_url, company_name)
                        verdict = classify_result.get("verdict", "no_links_confirmed")
                        if verdict != "no_links_confirmed":
                            url = new_url
                        logger.info(
                            "  (再スクレイプ) verdict=%s type=%s",
                            verdict,
                            classify_result.get("type"),
                        )
                    else:
                        logger.info("  Google 再検索: 新規 URL なし")

                # ---- Step 3: アダプター生成 ----
                adapter = build_adapter(ticker, company_name, url, classify_result)

                # M-2: build_adapter は "links" キーを生成しないため、新規生成経路
                # での validate_adapter_links 呼び出しは no-op。--revalidate 専用化。

                # ランログに追記（常時）
                results_log.append({
                    "ticker": ticker,
                    "company_name": company_name,
                    "url": url,
                    "verdict": verdict,
                    "type": classify_result.get("type"),
                    "eir_code": classify_result.get("eir_code"),
                    "adapter": adapter,
                    "download_links_count": len(classify_result.get("download_links", [])),
                    "monthly_links_count": len(classify_result.get("monthly_links", [])),
                    "error": classify_result.get("error"),
                    "processed_at": _now_jst(),
                    "search_source": _LAST_SEARCH_SOURCE.get("value"),
                })

                # url_adapter.json 書き込み（--dry-run なし時のみ）
                if not args.dry_run:
                    ok = upload_adapter(bucket, ticker, adapter, args.no_gcs)
                    if ok:
                        stats["gcs_ok"] += 1
                    else:
                        stats["gcs_fail"] += 1
                else:
                    logger.info("  --dry-run: url_adapter.json 書き込みスキップ")

                if verdict == "active":
                    stats["activated"] += 1
                    logger.info("  → active (%s)", classify_result.get("type"))
                elif verdict == "error":
                    stats["error"] += 1
                else:
                    stats["no_links"] += 1

            except Exception as e:
                logger.error("  [%s] 予期しないエラー: %s", ticker, e, exc_info=True)
                stats["error"] += 1

            time.sleep(RATE_LIMIT_SEC)

        browser.close()

    # ---- ランログ保存（常時） ----
    log_path = save_run_log(bucket, results_log, args.no_gcs)

    # ---- インデックス CSV 差分更新（Step 4 統合） ----
    # --dry-run でも CSV 更新は実行する（url_adapter.json のみスキップ）
    update_index_csv(results_log)

    # ---- サマリー ----
    logger.info("")
    logger.info("=== 完了サマリー ===")
    logger.info("  処理対象:                %d社", stats["total"])
    logger.info("  スキップ（active済み）:  %d社", stats["skipped_active"])
    logger.info("  URL未発見:               %d社", stats["url_not_found"])
    logger.info("  active 化:               %d社", stats["activated"])
    logger.info("  no_links_confirmed:      %d社", stats["no_links"])
    logger.info("  エラー:                  %d社", stats["error"])
    logger.info("  レートリミットskip:       %d社", stats.get("rate_limited_skip", 0))
    # L-4: search_source 別内訳（どのエンジンで URL を確定したか）
    search_source_counts: dict[str, int] = {}
    for r in results_log:
        src = r.get("search_source") or "n/a"
        search_source_counts[src] = search_source_counts.get(src, 0) + 1
    if search_source_counts:
        logger.info("  検索エンジン別内訳:")
        for src in sorted(search_source_counts):
            logger.info("    - %-28s %d社", src, search_source_counts[src])
    if not args.dry_run:
        logger.info("  GCS保存OK:               %d社", stats["gcs_ok"])
        logger.info("  GCS保存NG:               %d社", stats["gcs_fail"])
    else:
        logger.info("  url_adapter.json書き込み:    スキップ（--dry-run）")
        logger.info("  CSV更新:                 実行済み")
    logger.info("  ランログ:                %s", log_path)
    logger.info("===================")


if __name__ == "__main__":
    main()

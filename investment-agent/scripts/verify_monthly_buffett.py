"""バフェットコードの月次開示銘柄一覧(533社)をBQ TDNET_DOCUMENTS_ENHANCED と突合する.

BQ では MAIN_CATEGORY='月次開示' だけでなく、
SUB_CATEGORIES に '月次開示' が含まれる行も対象とする。

Usage:
    PYTHONUTF8=1 uv run python scripts/verify_monthly_buffett.py

出力:
    data/monthly_coverage_buffett.csv   突合結果（全533社）
    data/monthly_missing_buffett.csv    BQに存在しない銘柄のみ
"""

import json
import os
import re
import time
import warnings
from datetime import date

from bs4 import BeautifulSoup
from google.cloud import bigquery
from google.oauth2 import service_account

warnings.filterwarnings("ignore")

# ============================================================
# 設定
# ============================================================

BUFFETT_URL  = "https://www.buffett-code.com/monthly_reports"
CACHE_PATH   = "data/cache/buffett_monthly.json"
OUT_ALL      = "data/monthly_coverage_buffett.csv"
OUT_MISSING  = "data/monthly_missing_buffett.csv"
KEY_PATH     = "keys/gcp-service-account.json"
PROJECT      = "gmailpj-357912"
TABLE_ID     = f"{PROJECT}.STOCK.TDNET_DOCUMENTS_ENHANCED"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ============================================================
# バフェットコードから533社一覧を取得
# ============================================================

def fetch_buffett_monthly() -> list[dict]:
    """バフェットコードの月次開示一覧ページから銘柄リストを取得する.

    Returns:
        [{"ticker": "1234", "company_name": "○○会社"}, ...]
    """
    # キャッシュがあれば再利用
    if os.path.exists(CACHE_PATH):
        print(f"キャッシュ使用: {CACHE_PATH}")
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)

    print(f"バフェットコードにアクセス: {BUFFETT_URL}")
    html = _fetch_html(BUFFETT_URL)
    if not html:
        raise RuntimeError("バフェットコードのHTMLが取得できませんでした")

    companies = _parse_buffett_html(html)
    print(f"  → {len(companies)} 社取得")

    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(companies, f, ensure_ascii=False, indent=2)
    print(f"キャッシュ保存: {CACHE_PATH}")

    return companies


def _fetch_html(url: str) -> str | None:
    """curl_cffi で HTML を取得。失敗時は requests にフォールバック。"""
    try:
        from curl_cffi import requests as curl_requests
        resp = curl_requests.get(
            url,
            timeout=30,
            headers={"User-Agent": _UA},
            impersonate="chrome124",
            verify=False,  # CAファイルパス問題を回避
        )
        if resp.status_code == 200:
            print(f"  curl_cffi 成功 (status={resp.status_code})")
            return resp.text
        print(f"  curl_cffi 失敗 (status={resp.status_code}, body_preview={resp.text[:200]})")
    except Exception as e:
        print(f"  curl_cffi エラー: {e}")

    # フォールバック: 通常 requests
    try:
        import requests as req
        resp = req.get(url, headers={"User-Agent": _UA}, timeout=30, verify=False)
        if resp.status_code == 200:
            print(f"  requests フォールバック 成功 (status={resp.status_code})")
            return resp.text
        print(f"  requests フォールバック 失敗 (status={resp.status_code})")
    except Exception as e:
        print(f"  requests フォールバック エラー: {e}")

    return None


def _parse_buffett_html(html: str) -> list[dict]:
    """バフェットコードの月次開示ページから銘柄コード・会社名を抽出する."""
    soup = BeautifulSoup(html, "html.parser")
    companies: list[dict] = []
    seen: set[str] = set()

    # パターン1: /companies/NNNN 形式のリンク（標準的なバフェットコードの構造）
    for a in soup.find_all("a", href=re.compile(r"/companies/\d{4}[A-Z0-9]?")):
        m = re.search(r"/companies/(\d{4}[A-Z0-9]?)", a["href"])
        if not m:
            continue
        ticker = m.group(1)[:4]  # 4桁に切り詰め
        if ticker in seen:
            continue
        seen.add(ticker)
        name = a.get_text(strip=True) or ""
        companies.append({"ticker": ticker, "company_name": name})

    if companies:
        return companies

    # パターン2: テキスト内の 4桁数字（フォールバック）
    print("  パターン1で取得できず、パターン2（数字抽出）を試みます")
    for tag in soup.find_all(string=re.compile(r"\b\d{4}\b")):
        for m in re.finditer(r"\b(\d{4})\b", tag):
            ticker = m.group(1)
            if ticker in seen:
                continue
            seen.add(ticker)
            companies.append({"ticker": ticker, "company_name": ""})

    return companies


# ============================================================
# BQ クエリ：月次開示として認識されているティッカーを取得
# ============================================================

def get_bq_monthly_tickers() -> set[str]:
    """BQ TDNET_DOCUMENTS_ENHANCED から月次開示 ticker セットを返す.

    MAIN_CATEGORY = '月次開示'  OR  SUB_CATEGORIES に '月次開示' が含まれる行が対象。
    直近12ヶ月のデータで判定。
    """
    # Windows 環境で oauth2.googleapis.com への SSL EOF が発生することがある
    # requests.Session 全体に SSL 検証無効パッチを当てて回避
    import urllib3
    import requests as _requests
    from requests.adapters import HTTPAdapter as _HTTPAdapter
    urllib3.disable_warnings()

    class _NoVerifyAdapter(_HTTPAdapter):
        def send(self, request, **kwargs):
            kwargs["verify"] = False
            return super().send(request, **kwargs)

    _original_session_init = _requests.Session.__init__

    def _patched_session_init(self, *args, **kwargs):
        _original_session_init(self, *args, **kwargs)
        self.mount("https://", _NoVerifyAdapter())
        self.verify = False

    _requests.Session.__init__ = _patched_session_init

    creds = service_account.Credentials.from_service_account_file(KEY_PATH)
    client = bigquery.Client(project=PROJECT, credentials=creds)

    cutoff = date.today().replace(year=date.today().year - 1).isoformat()

    query = f"""
    SELECT DISTINCT TICKER
    FROM `{TABLE_ID}`
    WHERE SUBMISSION_DATE >= '{cutoff}'
      AND (
        MAIN_CATEGORY = '月次開示'
        OR EXISTS (
          SELECT 1 FROM UNNEST(SUB_CATEGORIES) sc WHERE sc = '月次開示'
        )
      )
    """
    print(f"BQ クエリ実行中（{cutoff} 以降）...")
    rows = client.query(query).result()
    tickers = {row.TICKER for row in rows}
    print(f"  → BQ 月次開示ティッカー数: {len(tickers)} 件")
    return tickers


# ============================================================
# 突合・出力
# ============================================================

def run() -> None:
    os.makedirs("data", exist_ok=True)

    # 1. バフェットコード一覧取得
    buffett_companies = fetch_buffett_monthly()
    buffett_tickers   = {c["ticker"] for c in buffett_companies}
    print(f"\nバフェットコード銘柄数: {len(buffett_tickers)} 社")

    # 2. BQ月次開示銘柄取得
    bq_tickers = get_bq_monthly_tickers()

    # 3. 突合
    matched   = buffett_tickers & bq_tickers
    missing   = buffett_tickers - bq_tickers   # バフェットコードにあるがBQに未登録
    bq_only   = bq_tickers - buffett_tickers   # BQにあるがバフェットコードにない

    coverage  = len(matched) / len(buffett_tickers) * 100 if buffett_tickers else 0

    print(f"\n=== 突合結果 ===")
    print(f"バフェットコード (基準):  {len(buffett_tickers)} 社")
    print(f"BQ 月次開示 (直近12ヶ月): {len(bq_tickers)} 社")
    print(f"一致 (カバー済み):         {len(matched)} 社")
    print(f"未カバー (BQ未登録):       {len(missing)} 社")
    print(f"BQのみ (バフェット外):     {len(bq_only)} 社")
    print(f"カバレッジ:                {coverage:.1f}%")

    # 4. CSV 出力（全社）
    ticker_to_name = {c["ticker"]: c["company_name"] for c in buffett_companies}
    with open(OUT_ALL, "w", encoding="utf-8-sig", newline="") as f:
        f.write("TICKER,COMPANY_NAME,IN_BQ,STATUS\n")
        for ticker in sorted(buffett_tickers):
            name   = ticker_to_name.get(ticker, "")
            in_bq  = ticker in bq_tickers
            status = "カバー済み" if in_bq else "未カバー"
            f.write(f"{ticker},{name},{in_bq},{status}\n")
        for ticker in sorted(bq_only):
            f.write(f"{ticker},(BQのみ),True,BQのみ\n")
    print(f"\n全社出力: {OUT_ALL}")

    # 5. CSV 出力（未カバーのみ）
    with open(OUT_MISSING, "w", encoding="utf-8-sig", newline="") as f:
        f.write("TICKER,COMPANY_NAME\n")
        for ticker in sorted(missing):
            name = ticker_to_name.get(ticker, "")
            f.write(f"{ticker},{name}\n")
    print(f"未カバー出力: {OUT_MISSING}")

    # 6. 先頭20件表示
    missing_sorted = sorted(missing)
    print(f"\n未カバー先頭20件: {missing_sorted[:20]}")
    if bq_only:
        print(f"BQのみ先頭10件: {sorted(bq_only)[:10]}")


if __name__ == "__main__":
    run()

"""地方証券取引所（名証・福証・札証）の単独上場銘柄コレクター。

東証との重複上場銘柄を除き、STOCK.STOCK_CODE_LIST テーブルに登録する。

データソース:
    - 名証 (NSE): https://www.nse.or.jp/listing/search/  (Playwright 必須)
    - 福証 (FSE): https://www.fse.or.jp/listed/single.php (requests)
    - 札証 (SSE): https://www.sse.or.jp/listing/list      (requests)

事前準備（名証のみ）:
    uv add playwright
    uv run playwright install chromium
"""
from __future__ import annotations

import re
import time
from typing import Any

import pandas as pd
from curl_cffi import requests
from bs4 import BeautifulSoup

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

from src.core.config import settings
from src.core.logger import get_logger

log = get_logger(__name__)

# ─── 業種マッピング ───────────────────────────────────────────────

# TSE 33業種: コード → 名称
_TSE_33: dict[str, str] = {
    "0050": "水産・農林業",
    "1050": "鉱業",
    "2050": "建設業",
    "3050": "食料品",
    "3100": "繊維製品",
    "3150": "パルプ・紙",
    "3200": "化学",
    "3250": "医薬品",
    "3300": "石油・石炭製品",
    "3350": "ゴム製品",
    "3400": "ガラス・土石製品",
    "3450": "鉄鋼",
    "3500": "非鉄金属",
    "3550": "金属製品",
    "3600": "機械",
    "3650": "電気機器",
    "3700": "輸送用機器",
    "3750": "精密機器",
    "3800": "その他製品",
    "4050": "電気・ガス業",
    "5050": "陸運業",
    "5100": "海運業",
    "5150": "空運業",
    "5200": "倉庫・運輸関連業",
    "5250": "情報・通信業",
    "6050": "卸売業",
    "6100": "小売業",
    "7050": "銀行業",
    "7100": "証券、商品先物取引業",
    "7150": "保険業",
    "7200": "その他金融業",
    "8050": "不動産業",
    "9050": "サービス業",
}

# TSE 17業種: コード → 名称
_TSE_17: dict[str, str] = {
    "1": "食品",
    "2": "エネルギー資源",
    "3": "建設・資材",
    "4": "素材・化学",
    "5": "医薬品・バイオ",
    "6": "自動車・輸送機",
    "7": "鉄鋼・非鉄",
    "8": "機械",
    "9": "電機・精密",
    "10": "IT・サービス他",
    "11": "電力・ガス",
    "12": "運輸・物流",
    "13": "商社・卸売",
    "14": "小売",
    "15": "銀行",
    "16": "金融（除く銀行）",
    "17": "不動産",
}

# 33業種コード → 17業種コード
_33_TO_17: dict[str, str] = {
    "0050": "1",  "1050": "2",  "2050": "3",  "3050": "1",
    "3100": "4",  "3150": "4",  "3200": "4",  "3250": "5",
    "3300": "2",  "3350": "4",  "3400": "3",  "3450": "7",
    "3500": "7",  "3550": "3",  "3600": "8",  "3650": "9",
    "3700": "6",  "3750": "9",  "3800": "10", "4050": "11",
    "5050": "12", "5100": "12", "5150": "12", "5200": "12",
    "5250": "10", "6050": "13", "6100": "14", "7050": "15",
    "7100": "16", "7150": "16", "7200": "16", "8050": "17",
    "9050": "10",
}

# 名称 → 33業種コード（逆引き）
_NAME_TO_33: dict[str, str] = {v: k for k, v in _TSE_33.items()}

# 各取引所固有の業種表記 → TSE名称（None = マッピング不可 → NULL）
_ALIAS: dict[str, str | None] = {
    # 建設
    "建設": "建設業",
    "建設業": "建設業",
    # 食料品
    "食品": "食料品",
    "食料品": "食料品",
    # 農林水産
    "農業": "水産・農林業",
    "農林・水産": "水産・農林業",
    "水産・農林業": "水産・農林業",
    # 不動産
    "不動産": "不動産業",
    "不動産業": "不動産業",
    # 情報・通信
    "情報通信": "情報・通信業",
    "情報・通信業": "情報・通信業",
    "情報サービス": "情報・通信業",
    "IT": "情報・通信業",
    # 小売
    "小売": "小売業",
    "小売業": "小売業",
    # 卸売
    "卸売": "卸売業",
    "卸売業": "卸売業",
    # 電気・ガス
    "電気・ガス": "電気・ガス業",
    "電気・ガス業": "電気・ガス業",
    "電力": "電気・ガス業",
    # 医薬品
    "医薬品": "医薬品",
    "製薬": "医薬品",
    # 機械・輸送
    "機械": "機械",
    "輸送機器": "輸送用機器",
    "輸送用機器": "輸送用機器",
    "自動車": "輸送用機器",
    # 金融
    "銀行": "銀行業",
    "銀行業": "銀行業",
    "証券": "証券、商品先物取引業",
    "証券、商品先物取引業": "証券、商品先物取引業",
    "保険": "保険業",
    "保険業": "保険業",
    "その他金融業": "その他金融業",
    # エネルギー
    "石油": "石油・石炭製品",
    "石油・石炭製品": "石油・石炭製品",
    # 運輸
    "陸運": "陸運業",
    "陸運業": "陸運業",
    "海運": "海運業",
    "海運業": "海運業",
    "空運": "空運業",
    "空運業": "空運業",
    "倉庫・運輸": "倉庫・運輸関連業",
    "倉庫・運輸関連業": "倉庫・運輸関連業",
    # 素材
    "電気機器": "電気機器",
    "精密機器": "精密機器",
    "繊維製品": "繊維製品",
    "化学": "化学",
    "鉄鋼": "鉄鋼",
    "非鉄金属": "非鉄金属",
    "金属製品": "金属製品",
    "ゴム製品": "ゴム製品",
    "ガラス・土石製品": "ガラス・土石製品",
    "パルプ・紙": "パルプ・紙",
    "その他製品": "その他製品",
    "鉱業": "鉱業",
    "サービス業": "サービス業",
    # マッピング不可（NULL）
    "製造": None,
    "製造業": None,
    "金融": None,
}


def _map_industry(
    raw: str | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    """業種名を TSE 33/17業種コードに変換する。

    Args:
        raw: 各取引所サイトから取得した業種名。

    Returns:
        (33業種コード, 33業種名, 17業種コード, 17業種名)。
        変換できない場合はすべて None。
    """
    if not raw:
        return None, None, None, None

    name = raw.strip()

    # エイリアス変換（_ALIAS に存在する場合）
    if name in _ALIAS:
        tse_name = _ALIAS[name]  # None の場合はマッピング不可
    else:
        tse_name = name  # 見つからなければそのまま TSE 名称として試みる

    code33 = _NAME_TO_33.get(tse_name) if tse_name else None
    if not code33:
        log.debug("industry_unmapped", raw=raw)
        return None, None, None, None

    code17 = _33_TO_17.get(code33)
    return code33, _TSE_33[code33], code17, _TSE_17.get(code17 or "")


# ─── コードバリデーション ─────────────────────────────────────────

# 例: "1234" (4桁数字) または "123A" (3桁 + 大文字英字)
_TICKER_RE = re.compile(r"^\d{3}[\dA-Z]$")


def _is_valid_ticker(s: str) -> bool:
    """4文字の有効な銘柄コードか判定する。"""
    return bool(_TICKER_RE.match(s))


# ─── 福証スクレイパー ─────────────────────────────────────────────

_FSE_URL = "https://www.fse.or.jp/listed/single.php"


def scrape_fse() -> list[dict[str, Any]]:
    """福証の単独上場銘柄を取得する。

    出典: https://www.fse.or.jp/listed/single.php

    ページ構造（1社 = 1 <table>）:
        行0: <th>業種</th><td>建設業</td><th>コード</th><td>1771</td>
             <th>決算期</th><td>0930</td><th>市場区分</th><td>本則</td>
        行1: <th>会社名</th><td><a><img alt="会社名"/></a></td>
        行2: <th>事業内容</th><td>...</td>
    """
    log.info("scrape_fse_start", url=_FSE_URL)
    resp = requests.get(_FSE_URL, timeout=30, headers={"User-Agent": _UA}, impersonate="chrome124")
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding
    soup = BeautifulSoup(resp.text, "html.parser")
    records: list[dict[str, Any]] = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        # 行0: th/td 交互ペア → 辞書に変換
        row0_els = rows[0].find_all(["th", "td"])
        row0 = {
            row0_els[i].get_text(strip=True): row0_els[i + 1].get_text(strip=True)
            for i in range(0, len(row0_els) - 1, 2)
        }
        ticker = re.sub(r"\s+", "", row0.get("コード", ""))[:4]
        if not _is_valid_ticker(ticker):
            continue

        # 行1: 会社名は <img alt> から取得
        name: str | None = None
        img = rows[1].find("img")
        if img:
            name = img.get("alt", "").strip() or None
        if not name:
            a = rows[1].find("a")
            if a:
                name = a.get_text(strip=True) or None

        records.append({
            "ticker": ticker,
            "name": name,
            "industry": row0.get("業種"),
            "market": row0.get("市場区分"),
        })

    log.info("scrape_fse_done", count=len(records))
    return records


# ─── 札証スクレイパー ─────────────────────────────────────────────

_SSE_URL = "https://www.sse.or.jp/listing/list"


def scrape_sse() -> list[dict[str, Any]]:
    """札証の単独上場銘柄を取得する。

    出典: https://www.sse.or.jp/listing/list

    ページ構造（dl/dt/dd 形式）:
        <dt>単独上場会社 - 本則市場</dt>
        <dd>
          <dl><dt>1449</dt><dd><a><img alt="株式会社FUJIジャパン"/></a></dd></dl>
          ...
        </dd>
        <dt>単独上場会社 - アンビシャス</dt>
        ...
        <dt>重複上場会社 - ...</dt>  ← スキップ

    注意: 業種情報はこのページから取得できないため NULL になる。
    """
    log.info("scrape_sse_start", url=_SSE_URL)
    resp = requests.get(_SSE_URL, timeout=30, headers={"User-Agent": _UA}, impersonate="chrome124")
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding
    soup = BeautifulSoup(resp.text, "html.parser")
    records: list[dict[str, Any]] = []

    # "単独上場会社 - ..." の dt を探し、次の dd から銘柄を取得
    for dt in soup.find_all("dt"):
        section = dt.get_text(strip=True)
        if "単独上場会社" not in section or "重複" in section:
            continue

        # 市場区分を section 名から抽出
        if "アンビシャス" in section or "Ambitious" in section:
            market = "アンビシャス"
        else:
            market = "本則市場"

        # 対応する dd を取得
        dd = dt.find_next_sibling("dd")
        if dd is None:
            continue

        # dd 内の dl 要素を走査（dt=ticker, dd=img alt）
        for company_dl in dd.find_all("dl"):
            company_dt = company_dl.find("dt")
            company_dd = company_dl.find("dd")
            if company_dt is None:
                continue

            ticker = re.sub(r"\s+", "", company_dt.get_text(strip=True))[:4]
            if not _is_valid_ticker(ticker):
                continue

            # 会社名: img alt から取得
            name: str | None = None
            if company_dd:
                img = company_dd.find("img")
                if img:
                    name = img.get("alt", "").strip() or None
                if not name:
                    a = company_dd.find("a")
                    if a:
                        name = a.get_text(strip=True) or None

            records.append({
                "ticker": ticker,
                "name": name,
                "industry": None,  # 一覧ページに業種情報なし
                "market": market,
            })

    log.info("scrape_sse_done", count=len(records))
    return records


# ─── 名証スクレイパー（Playwright） ──────────────────────────────

_NSE_LIST_URL = (
    "https://www.nse.or.jp/listing/search/list.html"
    "?schOrder_Target=stockCode&schOrder_Rule=ASC"
    "&schList_Count=100&schKey_Code=&schKey_name_j="
    "&schKey_Industry=&schKey_Month=&schKey_Unit=&page={page}"
)

# 銘柄名（コード）列から銘柄コードを抽出する正規表現
# NSE のコード表示形式: （1234 0）または（123A0）のように4文字コード + "0" が付く
_NSE_CODE_RE = re.compile(r"（(\w{5})）")


def scrape_nse() -> list[dict[str, Any]]:
    """名証の上場銘柄を取得する（Playwright 使用）。

    出典: https://www.nse.or.jp/listing/search/list.html
    ページが JavaScript で描画されるため Playwright が必須。

    テーブル列構成:
        0: 単独フラグ（空/"☆"）
        1: 銘柄名（コード）  ← 例: 光フードサービス（138A0）
        2: 市場区分         ← プレミア/メイン/ネクスト/その他
        3: 業種             ← 建設業/食料品/－（ETF等）
        4: 決算期
        5: 売買単位

    コード形式: NSE は 4文字コードの末尾に "0" を付加して表示する。
        例: TSE コード "138A" → NSE 表示 "138A0"
        → 先頭4文字を取得して TICKER とする。

    事前準備:
        uv add playwright
        uv run playwright install chromium
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright が未インストールです。\n"
            "  uv add playwright\n"
            "  uv run playwright install chromium\n"
            "を実行してから再試行してください。"
        ) from exc

    records: list[dict[str, Any]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_extra_http_headers({"Accept-Language": "ja-JP,ja;q=0.9"})

        page_num = 0
        while True:
            page_num += 1
            url = _NSE_LIST_URL.format(page=page_num)
            log.info("nse_fetch_page", page=page_num, url=url)
            page.goto(url, wait_until="networkidle", timeout=60_000)
            time.sleep(1)

            # メイン結果テーブルを特定（"銘柄名（コード）" ヘッダーを含むもの）
            try:
                page.wait_for_selector("table", timeout=15_000)
            except Exception:
                log.warning("nse_table_timeout", page=page_num)
                break

            html = page.content()
            soup = BeautifulSoup(html, "html.parser")

            result_table = None
            for t in soup.find_all("table"):
                ths = [th.get_text(strip=True) for th in t.find_all("th")]
                if any("銘柄名" in h for h in ths):
                    result_table = t
                    break

            if result_table is None:
                log.warning("nse_result_table_not_found", page=page_num)
                break

            rows = result_table.find_all("tr")[1:]  # ヘッダー行をスキップ
            log.info("nse_page_rows", page=page_num, rows=len(rows))

            if not rows:
                break

            page_records = 0
            for row in rows:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if len(cells) < 4:
                    continue

                # コードを銘柄名列（cells[1]）から抽出: 例 "光フードサービス（138A0）"
                name_with_code = cells[1]
                m = _NSE_CODE_RE.search(name_with_code)
                if not m:
                    continue
                raw_code = m.group(1)[:4]  # 5文字コードの先頭4文字
                if not _is_valid_ticker(raw_code):
                    continue

                # 銘柄名 = コード部分を除去
                name = _NSE_CODE_RE.sub("", name_with_code).strip() or None

                # 市場区分
                market = cells[2] if cells[2] not in ("", "　") else None

                # 業種 (ETFなどは "－" が入る)
                industry_raw = cells[3] if len(cells) > 3 else None
                industry = industry_raw if industry_raw and industry_raw != "－" else None

                records.append({
                    "ticker": raw_code,
                    "name": name,
                    "industry": industry,
                    "market": market,
                })
                page_records += 1

            # 100件未満なら最終ページ
            if page_records < 100 or page_num >= 50:
                break

        browser.close()

    log.info("scrape_nse_done", count=len(records))
    return records


# ─── BQ ロード ────────────────────────────────────────────────────

def get_tse_tickers(bq_client: Any) -> set[str]:
    """東証上場銘柄コード一覧を BQ から取得する（重複チェック用）。"""
    result = bq_client.query(
        "SELECT TICKER FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST` WHERE EXCHANGE = 'TSE'"
    ).result()
    return {row.TICKER for row in result}


def build_df(
    records: list[dict[str, Any]],
    exchange: str,
    tse_tickers: set[str],
) -> pd.DataFrame:
    """スクレイピング結果を BQ ロード用 DataFrame に変換する。

    - TSE 重複上場銘柄を除外
    - 業種コードを TSE 33/17 分類にマッピング
    - SIZE_CODE / SIZE_CATEGORY は NULL（TOPIX 固有のため）
    """
    rows = []
    skipped_tse = 0
    for r in records:
        ticker = str(r.get("ticker", "")).strip()[:4]
        if not _is_valid_ticker(ticker):
            continue
        if ticker in tse_tickers:
            skipped_tse += 1
            continue
        i33c, i33n, i17c, i17n = _map_industry(r.get("industry"))
        rows.append({
            "TICKER": ticker,
            "EXCHANGE": exchange,
            "STOCK_NAME": r.get("name"),
            "MARKET_CATEGORY": r.get("market"),
            "INDUSTRY_33_CODE": i33c,
            "INDUSTRY_33_CATEGORY": i33n,
            "INDUSTRY_17_CODE": i17c,
            "INDUSTRY_17_CATEGORY": i17n,
            "SIZE_CODE": None,
            "SIZE_CATEGORY": None,
        })

    log.info(
        "build_df_done",
        exchange=exchange,
        records=len(rows),
        skipped_tse=skipped_tse,
    )
    return pd.DataFrame(rows)


def load_to_bq(
    df: pd.DataFrame,
    exchange: str,
    bq_client: Any,
    dry_run: bool = False,
) -> None:
    """DataFrame を STOCK_CODE_LIST にロードする（既存レコードは削除→再挿入）。

    Args:
        df:         ロードする DataFrame。
        exchange:   取引所コード（"NSE" / "FSE" / "SSE"）。
        bq_client:  google.cloud.bigquery.Client インスタンス。
        dry_run:    True の場合 BQ 書き込みをスキップし標準出力に表示する。
    """
    table_id = "gmailpj-357912.STOCK.STOCK_CODE_LIST"

    if dry_run:
        log.info("dry_run_skip", exchange=exchange, rows=len(df))
        print(df.to_string(index=False))
        return

    if df.empty:
        log.warning("no_records_to_load", exchange=exchange)
        return

    # 既存レコードを削除
    bq_client.query(f"DELETE FROM `{table_id}` WHERE EXCHANGE = '{exchange}'").result()
    log.info("bq_deleted", exchange=exchange)

    # 新規レコードを挿入
    from google.cloud import bigquery

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        schema=[
            bigquery.SchemaField("TICKER",               "STRING", mode="REQUIRED"),
            bigquery.SchemaField("EXCHANGE",             "STRING", mode="REQUIRED"),
            bigquery.SchemaField("STOCK_NAME",           "STRING", mode="NULLABLE"),
            bigquery.SchemaField("MARKET_CATEGORY",      "STRING", mode="NULLABLE"),
            bigquery.SchemaField("INDUSTRY_33_CODE",     "STRING", mode="NULLABLE"),
            bigquery.SchemaField("INDUSTRY_33_CATEGORY", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("INDUSTRY_17_CODE",     "STRING", mode="NULLABLE"),
            bigquery.SchemaField("INDUSTRY_17_CATEGORY", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("SIZE_CODE",            "STRING", mode="NULLABLE"),
            bigquery.SchemaField("SIZE_CATEGORY",        "STRING", mode="NULLABLE"),
        ],
    )
    job = bq_client.load_table_from_dataframe(df, table_id, job_config=job_config)
    job.result()
    log.info("bq_loaded", exchange=exchange, rows=len(df))

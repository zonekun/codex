# -*- coding: utf-8 -*-
"""TDnet 適時開示ポーリング + XBRL 抽出 連動モジュール.

yanoshin RSS/JSON API → XBRL ZIP ダウンロード → extract_pipeline で PL 抽出。
zaraba_earnings.py の watch から呼び出される。

ポーリングソースは二層構成:
  1. yanoshin RSS/JSON（高速、非公式）
  2. TDnet HTML 直接（フォールバック、ページング対応）
"""

import io
import sys
import time
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

JST = timezone(timedelta(hours=+9), "JST")
CACHE_BASE = Path("/tmp/zaraba_cache") if sys.platform != "win32" else Path(r"C:\tmp\zaraba_cache")

TDNET_BASE_URL = "https://www.release.tdnet.info/inbs/"
YANOSHIN_BASE_URL = "https://webapi.yanoshin.jp/webapi/tdnet/list/"

# 決算短信を判定するキーワード
EARNINGS_KEYWORDS = ["決算短信", "四半期決算短信"]


# ====================================================================
# データモデル
# ====================================================================
@dataclass
class Disclosure:
    """TDnet 適時開示レコード."""

    id: str
    pubdate: str  # "YYYY-MM-DD HH:MM:SS"
    company_code: str  # 4桁
    company_name: str
    title: str
    document_url: str
    url_xbrl: str | None = None

    @property
    def is_earnings(self) -> bool:
        """決算短信かどうか."""
        return any(kw in self.title for kw in EARNINGS_KEYWORDS)

    @property
    def has_xbrl(self) -> bool:
        """XBRLが利用可能か."""
        return self.url_xbrl is not None and len(self.url_xbrl) > 0


@dataclass
class ExtractedEarnings:
    """XBRL から抽出した決算数値."""

    company_code: str
    company_name: str
    disclosure_id: str
    pubdate: str
    net_sales: int | None = None
    operating_profit: int | None = None
    ordinary_profit: int | None = None
    profit: int | None = None
    earnings_per_share: float | None = None
    raw_extract: dict[str, Any] = field(default_factory=dict)


# ====================================================================
# ポーラー基底クラス
# ====================================================================
class TdnetPoller(ABC):
    """TDnet ポーリングの共通インターフェース."""

    @abstractmethod
    def fetch_recent(self, target_date: str) -> list[Disclosure]:
        """指定日の開示一覧を取得する.

        Args:
            target_date: YYYYMMDD 形式

        Returns:
            Disclosure のリスト
        """
        ...


# ====================================================================
# yanoshin ポーラー（第一選択）
# ====================================================================
class YanoshinPoller(TdnetPoller):
    """yanoshin JSON API を使ったポーラー."""

    def __init__(self) -> None:
        try:
            from curl_cffi import requests as curl_requests
            self._session = curl_requests.Session(impersonate="chrome124")
        except ImportError:
            import requests
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            })
        self._use_curl_cffi = "curl_cffi" in type(self._session).__module__

    def fetch_recent(self, target_date: str) -> list[Disclosure]:
        """yanoshin JSON API から当日の開示一覧を取得."""
        url = f"{YANOSHIN_BASE_URL}{target_date}-{target_date}.json?limit=9999"
        try:
            if self._use_curl_cffi:
                resp = self._session.get(url, timeout=30)
            else:
                resp = self._session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("yanoshin_fetch_failed", error=str(e))
            return []

        results: list[Disclosure] = []
        for item in data.get("items", []):
            t = item.get("Tdnet", {})
            if not t:
                continue

            doc = t.get("document_url", "")
            if "rd.php?" in doc:
                doc = doc.split("rd.php?", 1)[1]

            code = (t.get("company_code") or "")[:4]

            # url_xbrl のリダイレクトも解除
            xbrl_url = t.get("url_xbrl") or ""
            if "rd.php?" in xbrl_url:
                xbrl_url = xbrl_url.split("rd.php?", 1)[1]

            results.append(Disclosure(
                id=t.get("id", ""),
                pubdate=t.get("pubdate", ""),
                company_code=code,
                company_name=t.get("company_name", ""),
                title=t.get("title", ""),
                document_url=doc,
                url_xbrl=xbrl_url or None,
            ))

        return results


# ====================================================================
# TDnet HTML ポーラー（フォールバック）
# ====================================================================
class TdnetHtmlPoller(TdnetPoller):
    """TDnet 公式 HTML スクレイピングによるポーラー."""

    PAGE_INTERVAL = 0.5  # ページ取得間隔（秒）

    def __init__(self) -> None:
        try:
            from curl_cffi import requests as curl_requests
            self._session = curl_requests.Session(impersonate="chrome124")
        except ImportError:
            import requests
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            })
        self._use_curl_cffi = "curl_cffi" in type(self._session).__module__

    def _fetch_page(self, date_str: str, page: int) -> list[Disclosure]:
        """1ページ分の開示一覧をHTMLから取得."""
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin

        page_str = f"{page:03d}"
        url = f"{TDNET_BASE_URL}I_list_{page_str}_{date_str}.html"

        try:
            resp = self._session.get(url, timeout=30)
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
        except Exception as e:
            logger.warning("tdnet_html_fetch_failed", page=page, error=str(e))
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        table = None
        for t in soup.find_all("table"):
            if t.find("td", class_=lambda c: c and "kjTime" in c):
                table = t
                break
        if not table:
            return []

        results: list[Disclosure] = []
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if not tds:
                continue

            rec: dict[str, str | None] = {}
            for td in tds:
                cls = " ".join(td.get("class", []))
                text = td.get_text(strip=True)

                if "kjTime" in cls:
                    rec["time"] = text
                elif "kjCode" in cls:
                    rec["company_code"] = text[:4]
                elif "kjName" in cls:
                    rec["company_name"] = text
                elif "kjTitle" in cls:
                    rec["title"] = text
                    a_tag = td.find("a")
                    if a_tag:
                        rec["document_url"] = urljoin(TDNET_BASE_URL, a_tag.get("href", ""))
                elif "kjXbrl" in cls:
                    a_tag = td.find("a")
                    if a_tag:
                        rec["url_xbrl"] = urljoin(TDNET_BASE_URL, a_tag.get("href", ""))

            if rec.get("company_code") and rec.get("title") and rec.get("document_url"):
                time_val = rec.get("time", "00:00")
                pubdate = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} {time_val}:00"
                fn = rec["document_url"].rstrip("/").split("/")[-1]
                disc_id = fn.replace(".pdf", "")

                results.append(Disclosure(
                    id=disc_id,
                    pubdate=pubdate,
                    company_code=rec["company_code"],
                    company_name=rec.get("company_name", ""),
                    title=rec.get("title", ""),
                    document_url=rec["document_url"],
                    url_xbrl=rec.get("url_xbrl"),
                ))

        return results

    def fetch_recent(self, target_date: str) -> list[Disclosure]:
        """全ページをループして開示一覧を取得."""
        all_results: list[Disclosure] = []
        page = 1
        while True:
            page_results = self._fetch_page(target_date, page)
            if not page_results:
                break
            all_results.extend(page_results)
            page += 1
            time.sleep(self.PAGE_INTERVAL)
        return all_results


# ====================================================================
# XBRL ダウンロード & 抽出
# ====================================================================

# TDnet 決算短信 iXBRL のタグ名 → J-Quants カラム マッピング
# namespace は tse-ed-t: （EDINET の jppfs_cor: とは異なる）
TDNET_TAG_MAP: dict[str, list[str]] = {
    "NET_SALES": [
        "NetSales", "OperatingRevenue", "GrossOperatingRevenues",
        "Revenue", "SalesIFRS", "RevenueIFRS",
        "NetSalesAndOperatingRevenue",
        "NetSalesOfCompletedConstructionContracts",
        "OrdinaryIncomeBNK",       # 銀行業: 経常収益
        "OperatingIncomeINS",      # 保険業: 経常収益
        "BusinessRevenue",         # 事業収益（アニコム等）
        "OperatingRevenue1", "OperatingRevenue2",
        "RevenuesFromExternalCustomers",
    ],
    "OPERATING_PROFIT": [
        "OperatingIncome", "OperatingProfit",
        "OperatingIncomeIFRS", "OperatingProfitIFRS",
        "OperatingProfitLossIFRS",
        "BusinessProfitIFRS", "BusinessProfitLossIFRS",  # IFRS事業利益
        "CoreOperatingIncomeIFRS",                        # IFRS中核営業利益
        "OperatingIncomeLoss", "OperatingIncomeLossIFRS",
        "OperatingIncomeLossUSGAAP",
    ],
    "ORDINARY_PROFIT": ["OrdinaryIncome"],
    "PROFIT": [
        "ProfitAttributableToOwnersOfParent", "NetIncome",
        "ProfitLoss", "NetIncomeAttributableToOwnersOfParent",
        "ProfitIFRS", "ProfitLossIFRS",
        "ProfitLossAttributableToOwnersOfParent",
        "ProfitLossAttributableToOwnersOfParentIFRS",
    ],
    "EARNINGS_PER_SHARE": [
        "NetIncomePerShare", "BasicEarningsPerShare",
        "BasicEarningsLossPerShare", "BasicEarningsPerShareIFRS",
        "BasicEarningsLossPerShareIFRS",
    ],
    # 予想値: タグ名は実績と同一。contextRef の ForecastMember で区別する
    # 調査確定 2026-04-09: TDnet iXBRL サンプル検証済み（9972 1Q, 3382 FY, 4829 3Q）
    "FORECAST_OP": [
        "OperatingIncome", "OperatingProfit",
        "OperatingIncomeIFRS", "OperatingProfitIFRS",
        "BusinessProfitIFRS", "BusinessProfitLossIFRS",
    ],
    "NEXT_YEAR_FORECAST_OP": [
        "OperatingIncome", "OperatingProfit",
        "OperatingIncomeIFRS", "OperatingProfitIFRS",
        "BusinessProfitIFRS", "BusinessProfitLossIFRS",
    ],
    "FORECAST_DIV_ANN": ["DividendPerShare"],
}

# 営業収入タグ（NET_SALESに加算して営業収益を算出）
TDNET_OPERATING_REVENUE_ADD_TAGS = ["OperatingRevenue2", "OperatingRevenue"]

# 当期 context パターン（Current + Consolidated + Result）
TDNET_CURRENT_PATTERNS = ["Current", "ThisQuarter"]
TDNET_EXCLUDE_PATTERNS = ["Prior"]
TDNET_PREFER_CONSOLIDATED = ["ConsolidatedMember"]  # 連結優先、なければ単体も許容
# 予想値の context パターン（タグ名は実績と同一、context で区別）
# FORECAST_OP: CurrentYearDuration_ConsolidatedMember_ForecastMember
# NEXT_YEAR_FORECAST_OP: NextYearDuration_ConsolidatedMember_ForecastMember
# FORECAST_DIV_ANN: CurrentYearDuration_AnnualMember_NonConsolidatedMember_ForecastMember
TDNET_FORECAST_CURRENT_PATTERNS = ["CurrentYearDuration", "CurrentAccumulatedQ"]
# 翌期見通しは通期のみを使う。NextAccumulatedQ* は翌期1Q/2Qなどの短期予想で、
# FY実績と比較すると 7931 のように「翌期↓-81%」等の誤判定になる。
TDNET_FORECAST_NEXTYEAR_PATTERNS = ["NextYearDuration"]
TDNET_FORECAST_DIV_ANN_PATTERNS = ["AnnualMember"]  # 年間配当は AnnualMember を含む


def _parse_ixbrl(html_bytes: bytes) -> dict[str, list[dict[str, Any]]]:
    """TDnet inline XBRL (iXBRL) をパースし要素をローカル名でグループ化.

    属性の出現順序に依存しないパース。scale 属性にも対応。
    """
    import re

    text = html_bytes.decode("utf-8", errors="replace")
    elements: dict[str, list[dict[str, Any]]] = {}

    # ix:nonFraction タグ全体を抽出（属性順序非依存）
    tag_pattern = re.compile(
        r'<ix:nonFraction\s([^>]*)>([^<]*)</ix:nonFraction>',
        re.DOTALL,
    )
    attr_pattern = re.compile(r'(\w+)="([^"]*)"')

    for m in tag_pattern.finditer(text):
        attrs_str = m.group(1)
        raw_value = m.group(2).strip().replace(",", "").replace("，", "")

        # 属性を辞書化
        attrs = dict(attr_pattern.findall(attrs_str))
        full_name = attrs.get("name", "")
        context = attrs.get("contextRef", "")
        decimals = attrs.get("decimals", "")
        scale = attrs.get("scale", "0")

        if not full_name or not context or not raw_value:
            continue

        # namespace prefix を除去
        local_name = full_name.split(":")[-1] if ":" in full_name else full_name

        # scale 適用（scale="6" → ×10^6、scale="-2" → ×10^-2）
        try:
            scale_factor = 10 ** int(scale)
            scaled_value = float(raw_value) * scale_factor
        except (ValueError, TypeError):
            scaled_value = raw_value

        if local_name not in elements:
            elements[local_name] = []
        elements[local_name].append({
            "value": str(scaled_value),
            "context": context,
            "decimals": decimals,
            "scale": scale,
        })

    return elements


def _extract_tdnet_pl(
    elements: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any] | None]:
    """TDnet iXBRL パース結果から PL 項目 + 予想値を抽出."""
    result: dict[str, dict[str, Any] | None] = {}

    for jq_col, tag_candidates in TDNET_TAG_MAP.items():
        is_forecast = jq_col.startswith("FORECAST") or jq_col.startswith("NEXT_YEAR")
        is_nextyear = jq_col.startswith("NEXT_YEAR")
        is_div = "DIV" in jq_col
        found = None

        for tag_name in tag_candidates:
            if tag_name not in elements:
                continue

            entries = elements[tag_name]
            valid_entries = []
            fallback_entries = []  # 単体（NonConsolidated）のフォールバック
            for e in entries:
                ctx = e["context"]
                # 前期は除外
                if "Prior" in ctx:
                    continue
                if is_forecast:
                    # ForecastMember 必須
                    if "ForecastMember" not in ctx:
                        continue
                    if is_nextyear:
                        # NEXT_YEAR_*: NextYearDuration を含む context のみ
                        if not any(p in ctx for p in TDNET_FORECAST_NEXTYEAR_PATTERNS):
                            continue
                    else:
                        # FORECAST_*: CurrentYearDuration を含む context のみ
                        if not any(p in ctx for p in TDNET_FORECAST_CURRENT_PATTERNS):
                            continue
                    if is_div:
                        # 配当: AnnualMember を含む context のみ（中間・期末ではなく年間）
                        if not any(p in ctx for p in TDNET_FORECAST_DIV_ANN_PATTERNS):
                            continue
                    valid_entries.append(e)
                else:
                    if any(p in ctx for p in TDNET_CURRENT_PATTERNS):
                        # ForecastMember を含む context は実績では除外
                        if "ForecastMember" in ctx:
                            continue
                        # 連結を優先
                        if any(p in ctx for p in TDNET_PREFER_CONSOLIDATED):
                            valid_entries.append(e)
                        else:
                            fallback_entries.append(e)
            # 連結がなければ単体にフォールバック
            if not valid_entries:
                valid_entries = fallback_entries

            if not valid_entries:
                continue

            # 翌期予想: NextYearDuration（通期）を NextAccumulatedQ*Duration（累計）より優先
            if is_nextyear and len(valid_entries) > 1:
                yearly = [e for e in valid_entries if "NextYearDuration" in e["context"]]
                if yearly:
                    valid_entries = yearly

            entry = valid_entries[0]
            try:
                raw_val = entry["value"]
                if jq_col == "EARNINGS_PER_SHARE":
                    found = {"value": float(raw_val), "tag": tag_name, "context": entry["context"]}
                else:
                    found = {"value": int(float(raw_val)), "tag": tag_name, "context": entry["context"]}
            except (ValueError, TypeError):
                continue
            break

        result[jq_col] = found

    # NET_SALES: 営業収入を加算して営業収益を算出
    # ベースタグが NetSales* の場合のみ（OperatingRevenue*/GrossOperating* は既に合算済み）
    ns = result.get("NET_SALES")
    if ns is not None and ns["tag"].startswith("NetSales"):
        for add_tag in TDNET_OPERATING_REVENUE_ADD_TAGS:
            if add_tag not in elements:
                continue
            # NET_SALES と同じタグなら二重加算しない
            if add_tag == ns["tag"]:
                continue
            for e in elements[add_tag]:
                ctx = e["context"]
                if "Prior" in ctx or "ForecastMember" in ctx:
                    continue
                if any(p in ctx for p in TDNET_CURRENT_PATTERNS):
                    try:
                        add_val = int(float(e["value"]))
                        if add_val != 0:
                            result["NET_SALES"] = {
                                "value": ns["value"] + add_val,
                                "tag": f'{ns["tag"]}+{add_tag}',
                                "context": ns["context"],
                            }
                    except (ValueError, TypeError):
                        pass
                    break
            break

    return result


class XbrlExtractor:
    """TDnet XBRL ZIP のダウンロードと PL 抽出."""

    def __init__(self, target_date: str) -> None:
        self._xbrl_dir = CACHE_BASE / target_date / "xbrl"
        self._xbrl_dir.mkdir(parents=True, exist_ok=True)

        # HTTP セッション
        try:
            from curl_cffi import requests as curl_requests
            self._session = curl_requests.Session(impersonate="chrome124")
        except ImportError:
            import requests
            self._session = requests.Session()

    def download_xbrl_zip(self, disclosure: Disclosure) -> Path | None:
        """XBRL ZIP をダウンロードしてローカルに保存.

        Returns:
            保存先パス。失敗時は None。
        """
        if not disclosure.has_xbrl:
            return None

        zip_path = self._xbrl_dir / f"{disclosure.id}_xbrl.zip"
        if zip_path.exists():
            return zip_path

        try:
            resp = self._session.get(disclosure.url_xbrl, timeout=30)
            resp.raise_for_status()
            zip_path.write_bytes(resp.content)
            logger.info("xbrl_downloaded", code=disclosure.company_code, path=str(zip_path))
            return zip_path
        except Exception as e:
            logger.warning("xbrl_download_failed", code=disclosure.company_code, error=str(e))
            return None

    def extract_from_zip(self, zip_path: Path, company_code: str) -> dict[str, Any] | None:
        """ZIP 内の iXBRL をパースし PL 項目 + 予想値を抽出.

        Returns:
            _extract_tdnet_pl() の戻り値。失敗時は None。
        """
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                # TDnet 決算短信は inline XBRL（-ixbrl.htm）
                # Summary フォルダを優先（決算サマリー）
                ixbrl_names = [n for n in zf.namelist() if n.endswith("-ixbrl.htm")]
                summary = [n for n in ixbrl_names if "Summary" in n]
                target_files = summary if summary else ixbrl_names
                # フォールバック: 通常の .xbrl
                if not target_files:
                    target_files = [n for n in zf.namelist() if n.endswith(".xbrl")]
                if not target_files:
                    logger.warning("no_xbrl_in_zip", path=str(zip_path))
                    return None

                xbrl_bytes = zf.read(target_files[0])
        except (zipfile.BadZipFile, KeyError) as e:
            logger.warning("zip_read_failed", path=str(zip_path), error=str(e))
            return None

        elements = _parse_ixbrl(xbrl_bytes)
        if not elements:
            return None

        return _extract_tdnet_pl(elements)

    def process_disclosure(self, disclosure: Disclosure) -> ExtractedEarnings | None:
        """開示レコード → XBRL ダウンロード → PL 抽出 を一気通貫で実行.

        Returns:
            ExtractedEarnings。XBRL なし or 抽出失敗時は None。
        """
        if not disclosure.is_earnings or not disclosure.has_xbrl:
            return None

        zip_path = self.download_xbrl_zip(disclosure)
        if not zip_path:
            return None

        raw = self.extract_from_zip(zip_path, disclosure.company_code)
        if not raw:
            return None

        return ExtractedEarnings(
            company_code=disclosure.company_code,
            company_name=disclosure.company_name,
            disclosure_id=disclosure.id,
            pubdate=disclosure.pubdate,
            net_sales=raw.get("NET_SALES", {}).get("value") if raw.get("NET_SALES") else None,
            operating_profit=raw.get("OPERATING_PROFIT", {}).get("value") if raw.get("OPERATING_PROFIT") else None,
            ordinary_profit=raw.get("ORDINARY_PROFIT", {}).get("value") if raw.get("ORDINARY_PROFIT") else None,
            profit=raw.get("PROFIT", {}).get("value") if raw.get("PROFIT") else None,
            earnings_per_share=raw.get("EARNINGS_PER_SHARE", {}).get("value") if raw.get("EARNINGS_PER_SHARE") else None,
            raw_extract=raw,
        )


# ====================================================================
# ファクトリ
# ====================================================================
def create_poller(source: str = "yanoshin") -> TdnetPoller:
    """ポーラーインスタンスを生成する.

    Args:
        source: "yanoshin" or "tdnet_html"
    """
    if source == "tdnet_html":
        return TdnetHtmlPoller()
    return YanoshinPoller()

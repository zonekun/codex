#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TDnet XBRL + BQ 四半期推移ツール.

指定銘柄の当日TDnet XBRLを取得し、BQの過去データと組み合わせて
四半期推移テーブル（standalone）を表示する。

Usage:
    PYTHONUTF8=1 python scripts/xbrl_lookup.py 6445
"""

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_SCRIPT_DIR))
sys.path.insert(0, str(_PROJECT_ROOT))

import structlog
from google.cloud import bigquery
from google.oauth2 import service_account
from rich.console import Console
from rich.table import Table
from rich.text import Text

from scripts.zaraba_tdnet_poller import TdnetHtmlPoller, XbrlExtractor
from src.core.config import settings

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)
log = structlog.get_logger(__name__)

JST = timezone(timedelta(hours=9))
PROJECT_ID = "gmailpj-357912"
DATASET = "STOCK"

Q_ORDER = {"1Q": 1, "2Q": 2, "3Q": 3, "FY": 4}
COLS = ["net_sales", "op", "odp", "np", "eps"]


def _bq_client() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(
        settings.google_application_credentials
    )
    return bigquery.Client(project=PROJECT_ID, credentials=creds)


def _quarter_from_doc(doc_type: str) -> str:
    if "1Q" in doc_type:
        return "1Q"
    if "2Q" in doc_type:
        return "2Q"
    if "3Q" in doc_type:
        return "3Q"
    return "FY"


def _quarter_from_title(title: str) -> str:
    if "第1四半期" in title or "第１四半期" in title:
        return "1Q"
    if "第2四半期" in title or "第２四半期" in title or "中間" in title:
        return "2Q"
    if "第3四半期" in title or "第３四半期" in title:
        return "3Q"
    return "FY"


def _fetch_bq_cumulative(client: bigquery.Client, ticker: str) -> list[dict]:
    """BQ fin_summary から過去3年の累計値を取得."""
    sql = f"""
    SELECT
        LOCAL_CODE, DISCLOSED_DATE, TYPE_OF_DOCUMENT,
        NET_SALES, OPERATING_PROFIT, ORDINARY_PROFIT, PROFIT, EARNINGS_PER_SHARE,
        CURRENT_FISCAL_YEAR_END_DATE
    FROM `{PROJECT_ID}.{DATASET}.fin_summary`
    WHERE LOCAL_CODE LIKE '{ticker[:4]}%'
      AND TYPE_OF_DOCUMENT LIKE '%FinancialStatements%'
      AND DISCLOSED_DATE >= DATE_SUB(CURRENT_DATE(), INTERVAL 4 YEAR)
    ORDER BY CURRENT_FISCAL_YEAR_END_DATE, DISCLOSED_DATE
    """
    rows = client.query(sql).result()
    return [dict(r) for r in rows]


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _build_standalone(cumulative_rows: list[dict], xbrl_row: dict | None = None) -> list[dict]:
    """累計値からQ standalone を計算.

    Returns:
        list of {fy_end, quarter, net_sales, op, odp, np, eps, source}
    """
    # 決算期ごとにグループ化
    by_fy: dict[str, list[dict]] = defaultdict(list)
    for r in cumulative_rows:
        fy = str(r["CURRENT_FISCAL_YEAR_END_DATE"])
        q = _quarter_from_doc(r["TYPE_OF_DOCUMENT"])
        by_fy[fy].append({
            "quarter": q,
            "net_sales": _to_float(r.get("NET_SALES")),
            "op": _to_float(r.get("OPERATING_PROFIT")),
            "odp": _to_float(r.get("ORDINARY_PROFIT")),
            "np": _to_float(r.get("PROFIT")),
            "eps": _to_float(r.get("EARNINGS_PER_SHARE")),
        })

    # XBRL行を追加
    if xbrl_row:
        fy = xbrl_row["fy_end"]
        by_fy[fy].append({
            "quarter": xbrl_row["quarter"],
            "net_sales": xbrl_row.get("net_sales"),
            "op": xbrl_row.get("op"),
            "odp": xbrl_row.get("odp"),
            "np": xbrl_row.get("np"),
            "eps": xbrl_row.get("eps"),
            "source": "xbrl",
        })

    results: list[dict] = []
    for fy_end in sorted(by_fy.keys()):
        quarters = by_fy[fy_end]
        quarters.sort(key=lambda x: Q_ORDER.get(x["quarter"], 9))

        # 重複Q除去（XBRLを優先）
        seen: dict[str, dict] = {}
        for q_row in quarters:
            qn = q_row["quarter"]
            if qn in seen and q_row.get("source") == "xbrl":
                seen[qn] = q_row
            elif qn not in seen:
                seen[qn] = q_row
        quarters = [seen[k] for k in sorted(seen, key=lambda x: Q_ORDER.get(x, 9))]

        prev: dict[str, float | None] = {c: None for c in COLS}
        for i, q_row in enumerate(quarters):
            qn = q_row["quarter"]
            standalone: dict[str, float | None] = {}

            if qn == "1Q":
                for c in COLS:
                    standalone[c] = q_row[c]
            else:
                for c in COLS:
                    cur = q_row[c]
                    prv = prev[c]
                    if cur is not None and prv is not None:
                        standalone[c] = cur - prv
                    else:
                        standalone[c] = None

            # 全列Noneの行はスキップ（前Q不足でstandalone計算不可）
            if all(standalone.get(c) is None for c in COLS):
                for c in COLS:
                    prev[c] = q_row[c]
                continue

            q_label = "4Q" if qn == "FY" else qn
            results.append({
                "fy_end": fy_end,
                "quarter": q_label,
                "q_order": Q_ORDER.get(qn, 9),
                **{c: standalone[c] for c in COLS},
                "source": q_row.get("source", "bq"),
            })

            # 次のQのために累計値を保存
            for c in COLS:
                prev[c] = q_row[c]

    return results


def _yoy(cur: float | None, prev: float | None) -> str:
    if cur is None or prev is None:
        return "-"
    if prev == 0:
        return "-"
    if prev < 0 and cur >= 0:
        return "黒転"
    if prev >= 0 and cur < 0:
        return "赤転"
    pct = (cur - prev) / abs(prev) * 100
    return f"{pct:+.1f}%"


def _margin(numerator: float | None, sales: float | None) -> str:
    if numerator is None or sales is None or sales == 0:
        return "-"
    return f"{numerator / sales * 100:.1f}%"


def _fmt_millions(v: float | None) -> str:
    if v is None:
        return "-"
    m = v / 1e6
    return f"{m:,.0f}"


def _fmt_eps(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:.2f}"


def _fmt_forecast(v: float | None) -> str:
    if v is None:
        return "NA"
    m = v / 1e6
    return f"{m:,.0f}"


def _fmt_forecast_eps(v: float | None) -> str:
    if v is None:
        return "NA"
    return f"{v:.2f}"


def _render_table(
    ticker: str,
    name: str,
    rows: list[dict],
    forecast: dict | None = None,
) -> None:
    """rich Table で四半期推移を表示."""
    console = Console(width=160)
    table = Table(
        title=f"[bold]{ticker} {name}[/bold] 四半期推移（百万円）",
        show_lines=True,
        padding=(0, 1),
    )
    table.add_column("決算期", style="bold", width=8)
    table.add_column("Q", width=3, justify="right")
    table.add_column("売上高", justify="right", width=8)
    table.add_column("営業利益", justify="right", width=8)
    table.add_column("経常利益", justify="right", width=8)
    table.add_column("純利益", justify="right", width=8)
    table.add_column("EPS", justify="right", width=7)
    table.add_column("営業率", justify="right", width=6)
    table.add_column("経常率", justify="right", width=6)
    table.add_column("売上YoY", justify="right", width=8)
    table.add_column("営業YoY", justify="right", width=8)
    table.add_column("経常YoY", justify="right", width=8)
    table.add_column("純利YoY", justify="right", width=8)

    # 翌期予想行（FY開示時のみ）
    if forecast:
        fy_label = forecast["fy_end"][:4] + "/" + forecast["fy_end"][5:7]
        table.add_row(
            f"[yellow]{fy_label}[/yellow]",
            "[yellow]予[/yellow]",
            Text(_fmt_forecast(forecast.get("net_sales")), style="yellow"),
            Text(_fmt_forecast(forecast.get("op")), style="yellow"),
            Text(_fmt_forecast(forecast.get("odp")), style="yellow"),
            Text(_fmt_forecast(forecast.get("np")), style="yellow"),
            Text(_fmt_forecast_eps(forecast.get("eps")), style="yellow"),
            "", "", "", "", "", "",
        )

    # 前年同Qマップ構築
    by_fy_q: dict[tuple[str, str], dict] = {}
    for r in rows:
        by_fy_q[(r["fy_end"], r["quarter"])] = r

    # FY一覧（降順）
    fy_list = sorted(set(r["fy_end"] for r in rows), reverse=True)

    # 前年FYマップ
    all_fys = sorted(set(r["fy_end"] for r in rows))
    prev_fy_map: dict[str, str | None] = {}
    for i, fy in enumerate(all_fys):
        prev_fy_map[fy] = all_fys[i - 1] if i > 0 else None

    for fy_end in fy_list:
        fy_rows = [r for r in rows if r["fy_end"] == fy_end]
        fy_rows.sort(key=lambda x: x["q_order"], reverse=True)

        fy_label = fy_end[:4] + "/" + fy_end[5:7]
        prev_fy = prev_fy_map.get(fy_end)

        for j, r in enumerate(fy_rows):
            q_label = r["quarter"]
            if r.get("source") == "xbrl":
                q_label = f"[bold cyan]{q_label}[/bold cyan]"

            # 前年同Q
            prev_r = by_fy_q.get((prev_fy, r["quarter"])) if prev_fy else None

            sales_yoy = _yoy(r["net_sales"], prev_r["net_sales"] if prev_r else None)
            op_yoy = _yoy(r["op"], prev_r["op"] if prev_r else None)
            odp_yoy = _yoy(r["odp"], prev_r["odp"] if prev_r else None)
            np_yoy = _yoy(r["np"], prev_r["np"] if prev_r else None)

            def _color_yoy(s: str) -> Text:
                if s in ("黒転", "-"):
                    return Text(s)
                if s == "赤転":
                    return Text(s, style="red")
                try:
                    val = float(s.replace("%", "").replace("+", ""))
                    if val > 0:
                        return Text(s)
                    return Text(s, style="red")
                except ValueError:
                    return Text(s)

            table.add_row(
                fy_label if j == 0 else "",
                q_label,
                _fmt_millions(r["net_sales"]),
                _fmt_millions(r["op"]),
                _fmt_millions(r["odp"]),
                _fmt_millions(r["np"]),
                _fmt_eps(r["eps"]),
                _margin(r["op"], r["net_sales"]),
                _margin(r["odp"], r["net_sales"]),
                _color_yoy(sales_yoy),
                _color_yoy(op_yoy),
                _color_yoy(odp_yoy),
                _color_yoy(np_yoy),
            )

    console.print(table)


def lookup(ticker: str) -> None:
    """指定銘柄のXBRL + BQ四半期推移を表示."""
    target_date = datetime.now(tz=JST).strftime("%Y%m%d")
    tk4 = ticker[:4]
    bq = _bq_client()
    poller = TdnetHtmlPoller()
    print(f"TDnet {target_date} の開示一覧を取得中...")
    discs = poller.fetch_recent(target_date)
    print(f"  取得件数: {len(discs)}")
    extractor = XbrlExtractor(target_date)

    # BQ履歴
    cum_rows = _fetch_bq_cumulative(bq, tk4)
    if not cum_rows:
        print(f"\n[{tk4}] BQにデータなし")
        return

    # XBRL最新
    matches = [d for d in discs if d.company_code == tk4 and d.is_earnings and d.has_xbrl]
    xbrl_row = None
    forecast_row = None
    company_name = ""

    if matches:
        disc = matches[0]
        company_name = disc.company_name
        zip_path = extractor.download_xbrl_zip(disc)
        if zip_path:
            raw = extractor.extract_from_zip(zip_path, disc.company_code)
            if raw:
                q = _quarter_from_title(disc.title)
                fy_ends = sorted(set(str(r["CURRENT_FISCAL_YEAR_END_DATE"]) for r in cum_rows))
                latest_fy = fy_ends[-1] if fy_ends else ""

                latest_bq_q = max(
                    (r for r in cum_rows if str(r["CURRENT_FISCAL_YEAR_END_DATE"]) == latest_fy),
                    key=lambda r: Q_ORDER.get(_quarter_from_doc(r["TYPE_OF_DOCUMENT"]), 0),
                    default=None,
                )
                latest_bq_quarter = _quarter_from_doc(latest_bq_q["TYPE_OF_DOCUMENT"]) if latest_bq_q else ""

                if q == "FY" and latest_bq_quarter == "FY":
                    fy_end = latest_fy
                elif Q_ORDER.get(q, 0) > Q_ORDER.get(latest_bq_quarter, 0):
                    fy_end = latest_fy
                else:
                    from dateutil.relativedelta import relativedelta
                    prev_end = datetime.strptime(latest_fy, "%Y-%m-%d")
                    new_end = prev_end + relativedelta(years=1)
                    fy_end = new_end.strftime("%Y-%m-%d")

                def _xval(key: str) -> float | None:
                    entry = raw.get(key)
                    if entry and entry.get("value") is not None:
                        return float(entry["value"])
                    return None

                xbrl_row = {
                    "fy_end": fy_end,
                    "quarter": q,
                    "net_sales": _xval("NET_SALES"),
                    "op": _xval("OPERATING_PROFIT"),
                    "odp": _xval("ORDINARY_PROFIT"),
                    "np": _xval("PROFIT"),
                    "eps": _xval("EARNINGS_PER_SHARE"),
                }

                # FY開示時は翌期会社予想を抽出
                if q == "FY":
                    from dateutil.relativedelta import relativedelta as _rd
                    next_fy_dt = datetime.strptime(fy_end, "%Y-%m-%d") + _rd(years=1)
                    next_fy_end = next_fy_dt.strftime("%Y-%m-%d")
                    fc_sales = _xval("NEXT_YEAR_FORECAST_NET_SALES")
                    fc_op = _xval("NEXT_YEAR_FORECAST_OP")
                    fc_odp = _xval("NEXT_YEAR_FORECAST_ODP")
                    fc_np = _xval("NEXT_YEAR_FORECAST_NP")
                    fc_eps = _xval("NEXT_YEAR_FORECAST_EPS")
                    if any(v is not None for v in [fc_sales, fc_op, fc_odp, fc_np, fc_eps]):
                        forecast_row = {
                            "fy_end": next_fy_end,
                            "net_sales": fc_sales,
                            "op": fc_op,
                            "odp": fc_odp,
                            "np": fc_np,
                            "eps": fc_eps,
                        }

    if not company_name:
        company_name = cum_rows[0].get("LOCAL_CODE", tk4)[:4] if cum_rows else tk4

    standalone = _build_standalone(cum_rows, xbrl_row)
    _render_table(tk4, company_name, standalone, forecast=forecast_row)


def main() -> None:
    parser = argparse.ArgumentParser(description="TDnet XBRL + BQ 四半期推移")
    parser.add_argument("ticker", help="銘柄コード（4桁）")
    args = parser.parse_args()

    lookup(args.ticker)


if __name__ == "__main__":
    main()

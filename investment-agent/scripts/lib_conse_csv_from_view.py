"""V_CONSENSUS_MERGED VIEW → ローカルCSV出力（ヘッダなし・Shift-JIS・6列）。

update_conse_ifis.py / update_conse_quick.py から共通で呼ばれる。
C案: PERIOD_REL（CURRENT/NEXT）はPython側でfin_summaryルックアップにより判定。
"""

from __future__ import annotations

import csv
import os
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from google.cloud import bigquery

log = structlog.get_logger()

VIEW = "gmailpj-357912.STOCK.V_CONSENSUS_MERGED"


def _fetch_current_fy_map(bq_client: bigquery.Client) -> dict[str, str]:
    """V_LATEST_DISCLOSURE VIEWから各銘柄の当期FY（YYYYMM）を返す。"""
    sql = "SELECT TICKER, CURRENT_FY FROM `gmailpj-357912.STOCK.V_LATEST_DISCLOSURE`"
    rows = list(bq_client.query(sql).result())
    return {row["TICKER"]: row["CURRENT_FY"] for row in rows}


def export_consensus_csv(bq_client: bigquery.Client, csv_path: str) -> int:
    """BQ VIEW から最新コンセンサスを取得し、C案PERIOD_REL判定後にCSV出力。

    出力形式: ヘッダなし・cp932・6列（TICKER, 1Q_CURRENT, 2Q_CURRENT, 3Q_CURRENT, FY_CURRENT, FY_NEXT）
    値は ORD_PROFIT（経常利益、百万円）。

    Args:
        bq_client: BigQuery クライアント
        csv_path: 出力先CSVパス

    Returns:
        書き出した行数
    """
    sql = f"""
        SELECT TICKER, FY, QUARTER, ORD_PROFIT
        FROM `{VIEW}`
        ORDER BY TICKER, FY, QUARTER
    """
    rows = list(bq_client.query(sql).result())
    log.info("view_query_done", view=VIEW, rows=len(rows))

    current_fy_map = _fetch_current_fy_map(bq_client)
    log.info("fy_lookup_done", tickers=len(current_fy_map))

    ticker_q_data: dict[str, dict[str, int]] = {}
    ticker_fy_profits: dict[str, list[tuple[str, int]]] = {}

    for row in rows:
        ticker = row["TICKER"]
        fy = row["FY"]
        quarter = row["QUARTER"]
        ord_profit = row["ORD_PROFIT"]

        if ord_profit is None:
            continue

        if quarter in ("1Q", "2Q", "3Q"):
            if ticker not in ticker_q_data:
                ticker_q_data[ticker] = {}
            ticker_q_data[ticker][f"{quarter}_CURRENT"] = ord_profit
        elif quarter == "FY":
            if ticker not in ticker_fy_profits:
                ticker_fy_profits[ticker] = []
            ticker_fy_profits[ticker].append((fy, ord_profit))

    all_tickers = set(ticker_q_data.keys()) | set(ticker_fy_profits.keys())
    ticker_data: dict[str, dict[str, int]] = {}

    for ticker in all_tickers:
        ticker_data[ticker] = ticker_q_data.get(ticker, {}).copy()

        current_fy = current_fy_map.get(ticker)
        if not current_fy or ticker not in ticker_fy_profits:
            continue

        future_fys = sorted(
            [(fy, p) for fy, p in ticker_fy_profits[ticker] if fy >= current_fy]
        )
        if len(future_fys) >= 1:
            ticker_data[ticker]["FY_CURRENT"] = future_fys[0][1]
        if len(future_fys) >= 2:
            ticker_data[ticker]["FY_NEXT"] = future_fys[1][1]

    keys = ["1Q_CURRENT", "2Q_CURRENT", "3Q_CURRENT", "FY_CURRENT", "FY_NEXT"]
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    with open(csv_path, mode="w", newline="", encoding="cp932", errors="replace") as f:
        writer = csv.writer(f)
        for ticker in sorted(ticker_data):
            data = ticker_data[ticker]
            row_data = [ticker] + [
                str(data[k]) if data.get(k) is not None else ""
                for k in keys
            ]
            writer.writerow(row_data)

    count = len(ticker_data)
    log.info("csv_exported", path=csv_path, tickers=count)
    return count

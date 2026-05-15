"""Backtest: FY conservative guidance repeat pattern — long on selloff, exit on 3-day reversal.

Train (2018-2021): identify companies with >= 2 pattern hits.
Test  (2022-2026): when those companies show weak guidance + selloff, enter long
                   next trading day, exit after HOLD_DAYS trading days.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import structlog
from google.cloud import bigquery
from google.oauth2 import service_account

PROJECT_ID = "gmailpj-357912"
KEY_FILE = "keys/gcp-service-account.json"

log = structlog.get_logger()

ONEWAY_COST_BPS = 2
TAX_RATE = 0.20315
HOLD_DAYS = 3


@dataclass
class Trade:
    """One backtest trade."""

    ticker: str
    stock_name: str
    entry_date: date
    entry_price: float
    exit_date: date | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    gross_ret: float | None = None
    net_ret: float | None = None
    hold_days: int = 0


@dataclass
class BacktestResult:
    """Aggregate backtest metrics."""

    trades: list[Trade] = field(default_factory=list)
    n_trades: int = 0
    n_wins: int = 0
    win_rate: float = 0.0
    avg_ret_gross: float = 0.0
    avg_ret_net: float = 0.0
    total_ret_gross: float = 0.0
    sharpe_annual: float = 0.0
    max_drawdown: float = 0.0
    avg_hold_days: float = 0.0
    after_tax_annual: float = 0.0


def configure_logging() -> None:
    """Configure structlog console output."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_bq_client() -> bigquery.Client:
    """Build a BigQuery client with explicit service-account credentials."""
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return bigquery.Client(project=PROJECT_ID, credentials=creds)


def parse_date(value: str) -> date:
    """Parse a YYYY-MM-DD string as a date."""
    return datetime.strptime(value, "%Y-%m-%d").date()


def load_candidate_years(path: Path) -> list[dict[str, Any]]:
    """Load candidate_years.csv from the screener output."""
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    log.info("candidate_years_loaded", path=str(path), rows=len(rows))
    return rows


def identify_qualifying_companies(
    rows: list[dict[str, Any]],
    train_end: date,
    min_hits: int,
) -> set[str]:
    """Identify tickers with >= min_hits PATTERN_HIT in training period."""
    hit_counts: dict[str, int] = {}
    for row in rows:
        disclosed = row.get("INITIAL_DISCLOSED_DATE", "")
        if not disclosed:
            continue
        if parse_date(disclosed) > train_end:
            continue
        if row.get("PATTERN_HIT") == "True":
            ticker = row["TICKER"]
            hit_counts[ticker] = hit_counts.get(ticker, 0) + 1

    qualifying = {t for t, c in hit_counts.items() if c >= min_hits}
    log.info(
        "qualifying_companies",
        total_with_hits=len(hit_counts),
        qualifying=len(qualifying),
        min_hits=min_hits,
        train_end=str(train_end),
    )
    return qualifying


def identify_test_trades(
    rows: list[dict[str, Any]],
    qualifying: set[str],
    test_start: date,
) -> list[dict[str, Any]]:
    """Find test-period events for qualifying companies with weak guidance + selloff."""
    trades = []
    for row in rows:
        ticker = row.get("TICKER", "")
        if ticker not in qualifying:
            continue
        disclosed = row.get("INITIAL_DISCLOSED_DATE", "")
        if not disclosed:
            continue
        if parse_date(disclosed) < test_start:
            continue
        if row.get("WEAK_GUIDANCE") != "True" or row.get("SELLOFF") != "True":
            continue
        next_date = row.get("NEXT_PRICE_DATE", "")
        next_close = row.get("NEXT_CLOSE", "")
        if not next_date or not next_close:
            continue
        trades.append(row)

    log.info("test_trades_identified", count=len(trades))
    return trades


def fetch_company_profiles(
    client: bigquery.Client,
    tickers: set[str],
) -> dict[str, dict[str, Any]]:
    """Fetch latest YF_STOCK_INFO snapshot for given tickers."""
    if not tickers:
        return {}

    ticker_list = ", ".join(f"'{t}'" for t in tickers)
    sql = f"""
    SELECT
      TICKER,
      MARKET_CAP,
      TRAILING_PE,
      FORWARD_PE,
      PRICE_TO_BOOK,
      RETURN_ON_ASSETS,
      RETURN_ON_EQUITY,
      PROFIT_MARGINS,
      OPERATING_MARGINS
    FROM `gmailpj-357912.STOCK.YF_STOCK_INFO`
    WHERE TICKER IN ({ticker_list})
      AND LOADED_DATE = (
        SELECT MAX(LOADED_DATE)
        FROM `gmailpj-357912.STOCK.YF_STOCK_INFO`
      )
    """
    profiles: dict[str, dict[str, Any]] = {}
    for row in client.query(sql).result():
        profiles[str(row["TICKER"])] = dict(row.items())

    log.info("company_profiles_fetched", count=len(profiles))
    return profiles


def filter_by_profile(
    trades: list[dict[str, Any]],
    profiles: dict[str, dict[str, Any]],
    min_market_cap: int | None = None,
    min_roa: float | None = None,
    min_roe: float | None = None,
    max_per: float | None = None,
    max_pbr: float | None = None,
) -> list[dict[str, Any]]:
    """Filter trades by company profile criteria."""
    filtered = []
    for row in trades:
        ticker = row["TICKER"]
        prof = profiles.get(ticker)
        if not prof:
            continue
        mc = prof.get("MARKET_CAP")
        if min_market_cap is not None and (mc is None or mc < min_market_cap):
            continue
        roa = prof.get("RETURN_ON_ASSETS")
        if min_roa is not None and (roa is None or roa < min_roa):
            continue
        roe = prof.get("RETURN_ON_EQUITY")
        if min_roe is not None and (roe is None or roe < min_roe):
            continue
        per = prof.get("TRAILING_PE")
        if max_per is not None and (per is None or per <= 0 or per > max_per):
            continue
        pbr = prof.get("PRICE_TO_BOOK")
        if max_pbr is not None and (pbr is None or pbr <= 0 or pbr > max_pbr):
            continue
        filtered.append(row)

    log.info(
        "profile_filter_applied",
        before=len(trades),
        after=len(filtered),
        min_market_cap=min_market_cap,
        min_roa=min_roa,
        min_roe=min_roe,
        max_per=max_per,
        max_pbr=max_pbr,
    )
    return filtered


def fetch_exit_prices(
    client: bigquery.Client,
    trades: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Fetch exit price N trading days after entry from BQ."""
    if not trades:
        return {}

    conditions = []
    for row in trades:
        ticker = row["TICKER"]
        entry_date = row["NEXT_PRICE_DATE"]
        conditions.append(
            f"STRUCT('{ticker}' AS ticker, DATE '{entry_date}' AS entry_date)"
        )

    values_clause = ",\n    ".join(conditions)

    sql = f"""
    WITH trade_entries AS (
      SELECT ticker, entry_date
      FROM UNNEST([
        {values_clause}
      ])
    ),
    future_prices AS (
      SELECT
        te.ticker,
        te.entry_date,
        p.YEARDATE AS exit_date,
        p.CLOSE AS exit_price,
        ROW_NUMBER() OVER (
          PARTITION BY te.ticker, te.entry_date
          ORDER BY p.YEARDATE ASC
        ) AS rn
      FROM trade_entries te
      JOIN `gmailpj-357912.STOCK.STOCK_PRICE` p
        ON p.TICKER = te.ticker
       AND p.YEARDATE > te.entry_date
       AND p.YEARDATE <= DATE_ADD(te.entry_date, INTERVAL 14 DAY)
    )
    SELECT ticker, entry_date,
           '{HOLD_DAYS}d_hold' AS exit_reason,
           exit_date, exit_price
    FROM future_prices
    WHERE rn = {HOLD_DAYS}
    """

    results: dict[tuple[str, str], dict[str, Any]] = {}
    for bq_row in client.query(sql).result():
        key = (str(bq_row["ticker"]), str(bq_row["entry_date"]))
        results[key] = dict(bq_row.items())

    log.info("exit_prices_fetched", count=len(results))
    return results


def compute_trades(
    trade_rows: list[dict[str, Any]],
    exit_prices: dict[tuple[str, str], dict[str, Any]],
) -> list[Trade]:
    """Compute trade-level P&L."""
    trades: list[Trade] = []
    for row in trade_rows:
        ticker = row["TICKER"]
        entry_date_str = row["NEXT_PRICE_DATE"]
        entry_price = float(row["NEXT_CLOSE"])

        key = (ticker, entry_date_str)
        exit_info = exit_prices.get(key)
        if not exit_info or exit_info.get("exit_price") is None:
            continue

        exit_price = float(exit_info["exit_price"])
        exit_date = exit_info["exit_date"]
        if isinstance(exit_date, str):
            exit_date = parse_date(exit_date)
        entry_date = parse_date(entry_date_str)

        gross_ret = exit_price / entry_price - 1
        roundtrip_cost = ONEWAY_COST_BPS * 2 / 10000
        net_ret = gross_ret - roundtrip_cost
        hold_days = (exit_date - entry_date).days

        trades.append(Trade(
            ticker=ticker,
            stock_name=row.get("STOCK_NAME", ""),
            entry_date=entry_date,
            entry_price=entry_price,
            exit_date=exit_date,
            exit_price=exit_price,
            exit_reason=exit_info.get("exit_reason", ""),
            gross_ret=gross_ret,
            net_ret=net_ret,
            hold_days=hold_days,
        ))

    trades.sort(key=lambda t: t.entry_date)
    log.info("trades_computed", count=len(trades))
    return trades


def compute_metrics(trades: list[Trade]) -> BacktestResult:
    """Compute aggregate backtest metrics."""
    result = BacktestResult(trades=trades)
    if not trades:
        return result

    result.n_trades = len(trades)
    result.n_wins = sum(1 for t in trades if t.net_ret is not None and t.net_ret > 0)
    result.win_rate = result.n_wins / result.n_trades

    gross_rets = [t.gross_ret for t in trades if t.gross_ret is not None]
    net_rets = [t.net_ret for t in trades if t.net_ret is not None]
    hold_days_list = [t.hold_days for t in trades if t.hold_days > 0]

    result.avg_ret_gross = float(np.mean(gross_rets)) if gross_rets else 0.0
    result.avg_ret_net = float(np.mean(net_rets)) if net_rets else 0.0
    result.avg_hold_days = float(np.mean(hold_days_list)) if hold_days_list else 0.0

    if net_rets:
        arr = np.array(net_rets)
        avg_hold = result.avg_hold_days if result.avg_hold_days > 0 else 60
        trades_per_year = 245 / avg_hold
        annual_ret = float(np.mean(arr)) * trades_per_year
        annual_std = float(np.std(arr, ddof=1)) * np.sqrt(trades_per_year)
        result.total_ret_gross = float(np.sum(np.array(gross_rets)))
        result.sharpe_annual = annual_ret / annual_std if annual_std > 0 else 0.0
        result.after_tax_annual = annual_ret * (1 - TAX_RATE) if annual_ret > 0 else annual_ret

        equity = np.cumprod(1 + arr)
        peak = np.maximum.accumulate(equity)
        drawdowns = (peak - equity) / peak
        result.max_drawdown = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    return result


def write_trades_csv(trades: list[Trade], path: Path) -> None:
    """Write trade-level results to CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "ticker", "stock_name", "entry_date", "entry_price",
        "exit_date", "exit_price", "exit_reason",
        "gross_ret", "net_ret", "hold_days",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for t in trades:
            writer.writerow({
                "ticker": t.ticker,
                "stock_name": t.stock_name,
                "entry_date": t.entry_date.isoformat() if t.entry_date else "",
                "entry_price": f"{t.entry_price:.1f}" if t.entry_price else "",
                "exit_date": t.exit_date.isoformat() if t.exit_date else "",
                "exit_price": f"{t.exit_price:.1f}" if t.exit_price else "",
                "exit_reason": t.exit_reason,
                "gross_ret": f"{t.gross_ret:.4f}" if t.gross_ret is not None else "",
                "net_ret": f"{t.net_ret:.4f}" if t.net_ret is not None else "",
                "hold_days": t.hold_days,
            })
    log.info("trades_csv_written", path=str(path), rows=len(trades))


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Backtest FY conservative guidance repeat pattern."
    )
    parser.add_argument(
        "--candidate-csv",
        type=Path,
        default=Path("data/output/fy_conservative_guidance/candidate_years.csv"),
    )
    parser.add_argument("--train-end", default="2021-12-31")
    parser.add_argument("--test-start", default="2022-01-01")
    parser.add_argument("--min-hits", type=int, default=2)
    parser.add_argument("--hold-days", type=int, default=HOLD_DAYS)
    parser.add_argument("--min-market-cap", type=int, default=None)
    parser.add_argument("--min-roa", type=float, default=None)
    parser.add_argument("--min-roe", type=float, default=None)
    parser.add_argument("--max-per", type=float, default=None)
    parser.add_argument("--max-pbr", type=float, default=None)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/output/fy_conservative_guidance"),
    )
    return parser


def run_single(
    rows: list[dict[str, Any]],
    qualifying: set[str],
    test_start: date,
    client: bigquery.Client,
    hold_days: int,
    output_dir: Path,
    profiles: dict[str, dict[str, Any]] | None = None,
    min_market_cap: int | None = None,
    min_roa: float | None = None,
    min_roe: float | None = None,
    max_per: float | None = None,
    max_pbr: float | None = None,
) -> BacktestResult:
    """Run backtest for a single hold-days setting."""
    global HOLD_DAYS
    HOLD_DAYS = hold_days

    test_trades = identify_test_trades(rows, qualifying, test_start)
    if not test_trades:
        log.warning("no_test_trades", hold_days=hold_days)
        return BacktestResult()

    has_profile_filter = any(
        v is not None for v in [min_market_cap, min_roa, min_roe, max_per, max_pbr]
    )
    if has_profile_filter and profiles:
        test_trades = filter_by_profile(
            test_trades, profiles,
            min_market_cap=min_market_cap,
            min_roa=min_roa,
            min_roe=min_roe,
            max_per=max_per,
            max_pbr=max_pbr,
        )
        if not test_trades:
            log.warning("no_trades_after_profile_filter", hold_days=hold_days)
            return BacktestResult()

    exit_prices = fetch_exit_prices(client, test_trades)
    trades = compute_trades(test_trades, exit_prices)
    metrics = compute_metrics(trades)

    trades_path = output_dir / f"backtest_trades_{hold_days}d.csv"
    write_trades_csv(trades, trades_path)

    log.info(
        "backtest_done",
        hold_days_setting=hold_days,
        n_trades=metrics.n_trades,
        n_wins=metrics.n_wins,
        win_rate=f"{metrics.win_rate:.1%}",
        avg_ret_gross=f"{metrics.avg_ret_gross:.2%}",
        avg_ret_net=f"{metrics.avg_ret_net:.2%}",
        sharpe_annual=f"{metrics.sharpe_annual:.2f}",
        max_drawdown=f"{metrics.max_drawdown:.2%}",
        avg_hold_days=f"{metrics.avg_hold_days:.0f}",
        after_tax_annual=f"{metrics.after_tax_annual:.2%}",
        pass_sharpe=metrics.sharpe_annual >= 0.5,
        pass_winrate=metrics.win_rate >= 0.55,
        pass_trades=metrics.n_trades >= 20,
    )
    return metrics


def main(argv: list[str] | None = None) -> int:
    """Run the backtest."""
    configure_logging()
    args = build_parser().parse_args(argv)

    try:
        rows = load_candidate_years(args.candidate_csv)
        train_end = parse_date(args.train_end)
        test_start = parse_date(args.test_start)

        if args.min_hits == 0:
            qualifying = {row["TICKER"] for row in rows if row.get("TICKER")}
            log.info("train_filter_disabled", all_tickers=len(qualifying))
        else:
            qualifying = identify_qualifying_companies(rows, train_end, args.min_hits)
            if not qualifying:
                log.warning("no_qualifying_companies")
                return 0

        client = get_bq_client()

        has_profile_filter = any(
            v is not None
            for v in [args.min_market_cap, args.min_roa, args.min_roe,
                       args.max_per, args.max_pbr]
        )
        profiles = (
            fetch_company_profiles(client, qualifying)
            if has_profile_filter
            else None
        )

        run_single(
            rows, qualifying, test_start, client, args.hold_days, args.output_dir,
            profiles=profiles,
            min_market_cap=args.min_market_cap,
            min_roa=args.min_roa,
            min_roe=args.min_roe,
            max_per=args.max_per,
            max_pbr=args.max_pbr,
        )
    except Exception:
        log.exception("backtest_failed")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

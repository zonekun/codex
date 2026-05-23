"""Backtest: Supply-chain earnings cascade.

Hypothesis: When a leader (demand driver) reports earnings, the follower
(supply-chain dependent) drifts in the same direction before its own
earnings announcement.

Entry: After leader reports, enter follower in same direction as leader's
       earnings-day return.
Exit:  (A) day before follower earnings, or (B) day after follower earnings.

Variants tested:
  - Entry timing: conservative (T+1 open) / aggressive (T+0 open, model-based)
  - Direction: long-short / long-only / short-only
  - Exit: pre-follower-earnings / post-follower-earnings
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import structlog
from google.cloud import bigquery
from google.oauth2 import service_account

PROJECT_ID = "gmailpj-357912"
KEY_FILE = "keys/gcp-service-account.json"

ONEWAY_COST_BPS = 2
BORROW_RATE = 0.02
TAX_RATE = 0.20315
MAX_HOLD_DAYS = 20

log = structlog.get_logger()


@dataclass
class Trade:
    """Single backtest trade."""

    pair_id: str
    leader_code: str
    follower_code: str
    direction: str  # "long" or "short"
    leader_earn_date: date
    leader_return: float
    entry_date: date
    entry_price: float
    exit_date: date | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    gross_ret: float = 0.0
    net_ret: float = 0.0
    hold_days: int = 0
    follower_earn_date: date | None = None


@dataclass
class BacktestResult:
    """Aggregate metrics for one condition set."""

    label: str
    trades: list[Trade] = field(default_factory=list)
    n_trades: int = 0
    n_wins: int = 0
    win_rate: float = 0.0
    avg_ret_gross: float = 0.0
    avg_ret_net: float = 0.0
    sharpe_annual: float = 0.0
    max_drawdown: float = 0.0
    avg_hold_days: float = 0.0
    profit_factor: float = 0.0
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
    """Build BigQuery client with service-account credentials."""
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return bigquery.Client(project=PROJECT_ID, credentials=creds)


def load_association_pairs(path: Path) -> list[dict[str, str]]:
    """Load association pairs CSV."""
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        pairs = list(reader)
    log.info("pairs_loaded", count=len(pairs))
    return pairs


@dataclass
class EarningsEvent:
    """Single earnings disclosure event."""

    ticker: str
    disclosed_date: date
    is_after_close: bool  # True if disclosed after market close (15:30+)


def fetch_earnings_dates(
    client: bigquery.Client,
    tickers: set[str],
    date_from: date,
    date_to: date,
) -> dict[str, list[EarningsEvent]]:
    """Fetch earnings disclosure dates and times from fin_summary.

    Uses FinancialStatements documents only (quarterly/annual results).
    Returns dict: ticker -> sorted list of EarningsEvent.
    """
    from datetime import time as dt_time

    # 東証引け時間: 2024-11-05以降 15:30、それ以前 15:00
    EXTENDED_HOURS_START = date(2024, 11, 5)
    MARKET_CLOSE_OLD = dt_time(15, 0)
    MARKET_CLOSE_NEW = dt_time(15, 30)

    sql = """
    SELECT DISTINCT LOCAL_CODE AS TICKER, DISCLOSED_DATE, DISCLOSED_TIME
    FROM `gmailpj-357912.STOCK.fin_summary`
    WHERE LOCAL_CODE IN UNNEST(@tickers)
      AND DISCLOSED_DATE BETWEEN @date_from AND @date_to
      AND TYPE_OF_DOCUMENT LIKE '%FinancialStatements%'
    ORDER BY LOCAL_CODE, DISCLOSED_DATE
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("tickers", "STRING", list(tickers)),
            bigquery.ScalarQueryParameter("date_from", "DATE", date_from),
            bigquery.ScalarQueryParameter("date_to", "DATE", date_to),
        ]
    )
    result: dict[str, list[EarningsEvent]] = defaultdict(list)
    for row in client.query(sql, job_config=job_config).result():
        disc_date = row["DISCLOSED_DATE"]
        disc_time = row["DISCLOSED_TIME"]
        market_close = MARKET_CLOSE_NEW if disc_date >= EXTENDED_HOURS_START else MARKET_CLOSE_OLD
        is_after = disc_time is None or disc_time >= market_close
        result[row["TICKER"]].append(EarningsEvent(
            ticker=row["TICKER"],
            disclosed_date=disc_date,
            is_after_close=is_after,
        ))
    log.info("earnings_dates_fetched", tickers_with_data=len(result))
    return dict(result)


def fetch_stock_prices(
    client: bigquery.Client,
    tickers: set[str],
    date_from: date,
    date_to: date,
) -> dict[str, dict[date, dict[str, int]]]:
    """Fetch OPEN/CLOSE prices for tickers.

    Returns dict: ticker -> date -> {OPEN, CLOSE}.
    """
    sql = """
    SELECT TICKER, YEARDATE, OPEN, CLOSE
    FROM `gmailpj-357912.STOCK.STOCK_PRICE`
    WHERE TICKER IN UNNEST(@tickers)
      AND YEARDATE BETWEEN @date_from AND @date_to
      AND CLOSE IS NOT NULL
    ORDER BY TICKER, YEARDATE
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("tickers", "STRING", list(tickers)),
            bigquery.ScalarQueryParameter("date_from", "DATE", date_from),
            bigquery.ScalarQueryParameter("date_to", "DATE", date_to),
        ]
    )
    prices: dict[str, dict[date, dict[str, int]]] = defaultdict(dict)
    for row in client.query(sql, job_config=job_config).result():
        prices[row["TICKER"]][row["YEARDATE"]] = {
            "OPEN": row["OPEN"],
            "CLOSE": row["CLOSE"],
        }
    log.info("prices_fetched", tickers_with_data=len(prices))
    return dict(prices)


def get_trading_days(prices: dict[str, dict[date, Any]]) -> list[date]:
    """Extract sorted list of all trading days from price data."""
    all_dates: set[date] = set()
    for ticker_prices in prices.values():
        all_dates.update(ticker_prices.keys())
    return sorted(all_dates)


def next_trading_day(trading_days: list[date], d: date, offset: int = 1) -> date | None:
    """Get the trading day N days after d."""
    import bisect
    idx = bisect.bisect_right(trading_days, d)
    target = idx + offset - 1
    if 0 <= target < len(trading_days):
        return trading_days[target]
    return None


def prev_trading_day(trading_days: list[date], d: date, offset: int = 1) -> date | None:
    """Get the trading day N days before d."""
    import bisect
    idx = bisect.bisect_left(trading_days, d)
    target = idx - offset
    if 0 <= target < len(trading_days):
        return trading_days[target]
    return None


def compute_leader_return(
    prices: dict[date, dict[str, int]],
    event: EarningsEvent,
    trading_days: list[date],
) -> tuple[float | None, date]:
    """Compute leader's earnings reaction return.

    For after-close announcements: next day close / earn_date close - 1
    For intraday announcements: earn_date close / prev_day close - 1

    Returns (return, reaction_date) where reaction_date is the day the
    market first reflects the earnings news.
    """
    earn_date = event.disclosed_date
    if event.is_after_close:
        # After-close: reaction is next trading day
        reaction_date = next_trading_day(trading_days, earn_date, 1)
        if reaction_date is None:
            return None, earn_date
        if reaction_date not in prices or earn_date not in prices:
            return None, earn_date
        base_close = prices[earn_date]["CLOSE"]
        reaction_close = prices[reaction_date]["CLOSE"]
        if base_close is None or reaction_close is None or base_close == 0:
            return None, earn_date
        return (reaction_close - base_close) / base_close, reaction_date
    else:
        # Intraday: reaction is same day
        prev_day = prev_trading_day(trading_days, earn_date)
        if prev_day is None:
            return None, earn_date
        if earn_date not in prices or prev_day not in prices:
            return None, earn_date
        prev_close = prices[prev_day]["CLOSE"]
        cur_close = prices[earn_date]["CLOSE"]
        if prev_close is None or cur_close is None or prev_close == 0:
            return None, earn_date
        return (cur_close - prev_close) / prev_close, earn_date


def generate_trades(
    pairs: list[dict[str, str]],
    earnings: dict[str, list[EarningsEvent]],
    prices: dict[str, dict[date, dict[str, int]]],
    trading_days: list[date],
    entry_mode: str,  # "conservative" or "aggressive"
    exit_mode: str,  # "pre" or "post"
    direction_mode: str,  # "ls", "long", "short"
) -> list[Trade]:
    """Generate trade signals for all pairs."""
    import bisect

    trades: list[Trade] = []

    for pair in pairs:
        leader = pair["leader_code"]
        follower = pair["follower_code"]
        pair_id = pair["pair_id"]

        if leader not in earnings or follower not in prices:
            continue

        leader_prices = prices.get(leader, {})
        follower_prices = prices.get(follower, {})
        follower_events = earnings.get(follower, [])
        follower_earn_dates = [e.disclosed_date for e in follower_events]

        for leader_event in earnings[leader]:
            leader_ret, reaction_date = compute_leader_return(
                leader_prices, leader_event, trading_days
            )
            if leader_ret is None:
                continue

            # Determine direction
            if leader_ret > 0:
                direction = "long"
            elif leader_ret < 0:
                direction = "short"
            else:
                continue

            # Direction filter
            if direction_mode == "long" and direction != "long":
                continue
            if direction_mode == "short" and direction != "short":
                continue

            # Entry timing: always enter after reaction is observable
            if entry_mode == "conservative":
                # T+1 after reaction_date (safest)
                entry_date = next_trading_day(trading_days, reaction_date, 1)
            else:
                # Aggressive: enter on reaction_date close (same day as reaction)
                # This is valid because we observe the reaction intraday
                entry_date = reaction_date

            if entry_date is None or entry_date not in follower_prices:
                continue

            # Entry price: open for conservative, close for aggressive
            if entry_mode == "conservative":
                entry_price = follower_prices[entry_date]["OPEN"]
            else:
                entry_price = follower_prices[entry_date]["CLOSE"]

            if entry_price is None or entry_price == 0:
                continue

            # Find next follower earnings date after leader earnings
            follower_earn_date = None
            for fed in follower_earn_dates:
                if fed > leader_event.disclosed_date:
                    follower_earn_date = fed
                    break

            # Determine exit date
            if exit_mode == "pre":
                if follower_earn_date is None:
                    exit_date = next_trading_day(trading_days, entry_date, MAX_HOLD_DAYS)
                    exit_reason = "timeout"
                else:
                    exit_date = prev_trading_day(trading_days, follower_earn_date, 1)
                    exit_reason = "pre_earn"
            else:  # post
                if follower_earn_date is None:
                    exit_date = next_trading_day(trading_days, entry_date, MAX_HOLD_DAYS)
                    exit_reason = "timeout"
                else:
                    exit_date = next_trading_day(trading_days, follower_earn_date, 1)
                    exit_reason = "post_earn"

            if exit_date is None or exit_date not in follower_prices:
                continue

            # Skip if exit before or same as entry
            if exit_date <= entry_date:
                continue

            # Hold days via bisect (O(log N) instead of O(N))
            idx_entry = bisect.bisect_right(trading_days, entry_date)
            idx_exit = bisect.bisect_right(trading_days, exit_date)
            hold_days_count = idx_exit - idx_entry

            if hold_days_count > MAX_HOLD_DAYS:
                exit_date = next_trading_day(trading_days, entry_date, MAX_HOLD_DAYS)
                if exit_date is None or exit_date not in follower_prices:
                    continue
                exit_reason = "timeout"
                hold_days_count = MAX_HOLD_DAYS

            exit_price = follower_prices[exit_date]["CLOSE"]
            if exit_price is None or exit_price == 0:
                continue

            # Compute return
            if direction == "long":
                gross_ret = (exit_price - entry_price) / entry_price
            else:
                gross_ret = (entry_price - exit_price) / entry_price

            # Net return (costs)
            cost = ONEWAY_COST_BPS * 2 / 10000  # round-trip
            if direction == "short":
                cost += BORROW_RATE * hold_days_count / 252
            net_ret = gross_ret - cost

            trades.append(Trade(
                pair_id=pair_id,
                leader_code=leader,
                follower_code=follower,
                direction=direction,
                leader_earn_date=leader_event.disclosed_date,
                leader_return=leader_ret,
                entry_date=entry_date,
                entry_price=entry_price,
                exit_date=exit_date,
                exit_price=exit_price,
                exit_reason=exit_reason,
                gross_ret=gross_ret,
                net_ret=net_ret,
                hold_days=hold_days_count,
                follower_earn_date=follower_earn_date,
            ))

    trades.sort(key=lambda t: t.entry_date)
    log.info("trades_generated", count=len(trades), entry=entry_mode, exit=exit_mode, dir=direction_mode)
    return trades


def compute_metrics(trades: list[Trade], label: str) -> BacktestResult:
    """Compute aggregate backtest metrics."""
    result = BacktestResult(label=label, trades=trades)
    if not trades:
        return result

    result.n_trades = len(trades)
    result.n_wins = sum(1 for t in trades if t.net_ret > 0)
    result.win_rate = result.n_wins / result.n_trades

    gross_rets = [t.gross_ret for t in trades]
    net_rets = [t.net_ret for t in trades]

    result.avg_ret_gross = float(np.mean(gross_rets))
    result.avg_ret_net = float(np.mean(net_rets))
    result.avg_hold_days = float(np.mean([t.hold_days for t in trades]))

    # Sharpe (annualized, per-trade basis — overestimates when trades overlap)
    if len(net_rets) > 1 and np.std(net_rets) > 0:
        trades_per_year = 252 / result.avg_hold_days if result.avg_hold_days > 0 else 50
        result.sharpe_annual = float(
            np.mean(net_rets) / np.std(net_rets) * np.sqrt(min(trades_per_year, 252))
        )

    # Profit factor
    wins_sum = sum(r for r in net_rets if r > 0)
    losses_sum = abs(sum(r for r in net_rets if r < 0))
    result.profit_factor = wins_sum / losses_sum if losses_sum > 0 else float('inf')

    # Max drawdown (cumulative equity curve)
    equity = np.cumsum(net_rets)
    running_max = np.maximum.accumulate(equity)
    drawdowns = equity - running_max
    result.max_drawdown = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0.0

    # After-tax annual return estimate
    annual_ret = result.avg_ret_net * (252 / result.avg_hold_days if result.avg_hold_days > 0 else 50)
    result.after_tax_annual = annual_ret * (1 - TAX_RATE) if annual_ret > 0 else annual_ret

    return result


def print_results(results: list[BacktestResult]) -> None:
    """Print comparison table of all conditions."""
    print("\n" + "=" * 120)
    print(f"{'Condition':<40} {'N':>5} {'WR%':>6} {'AvgGross':>9} {'AvgNet':>9} "
          f"{'Sharpe':>7} {'PF':>6} {'MDD':>8} {'HoldD':>6} {'TaxAnn%':>8}")
    print("-" * 120)

    for r in results:
        print(
            f"{r.label:<40} {r.n_trades:>5} {r.win_rate*100:>5.1f}% "
            f"{r.avg_ret_gross*100:>8.3f}% {r.avg_ret_net*100:>8.3f}% "
            f"{r.sharpe_annual:>7.2f} {r.profit_factor:>6.2f} "
            f"{r.max_drawdown*100:>7.2f}% {r.avg_hold_days:>5.1f} "
            f"{r.after_tax_annual*100:>7.2f}%"
        )
    print("=" * 120)


def print_yearly_breakdown(trades: list[Trade], label: str) -> None:
    """Print year-by-year performance for a given set of trades."""
    by_year: dict[int, list[Trade]] = defaultdict(list)
    for t in trades:
        by_year[t.entry_date.year].append(t)

    print(f"\n--- Year-by-year: {label} ---")
    print(f"{'Year':<6} {'N':>5} {'WR%':>6} {'AvgNet':>9} {'TotalNet':>10}")
    for year in sorted(by_year.keys()):
        yr_trades = by_year[year]
        n = len(yr_trades)
        wr = sum(1 for t in yr_trades if t.net_ret > 0) / n if n > 0 else 0
        avg_net = float(np.mean([t.net_ret for t in yr_trades]))
        total_net = sum(t.net_ret for t in yr_trades)
        print(f"{year:<6} {n:>5} {wr*100:>5.1f}% {avg_net*100:>8.3f}% {total_net*100:>9.3f}%")


def save_trades_csv(trades: list[Trade], path: Path) -> None:
    """Save individual trades to CSV for further analysis."""
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "pair_id", "leader_code", "follower_code", "direction",
            "leader_earn_date", "leader_return", "entry_date", "entry_price",
            "exit_date", "exit_price", "exit_reason", "gross_ret", "net_ret",
            "hold_days", "follower_earn_date",
        ])
        for t in trades:
            writer.writerow([
                t.pair_id, t.leader_code, t.follower_code, t.direction,
                t.leader_earn_date, f"{t.leader_return:.4f}",
                t.entry_date, t.entry_price,
                t.exit_date, t.exit_price, t.exit_reason,
                f"{t.gross_ret:.6f}", f"{t.net_ret:.6f}",
                t.hold_days, t.follower_earn_date or "",
            ])
    log.info("trades_saved", path=str(path), count=len(trades))


def fetch_size_category(
    client: bigquery.Client,
    tickers: set[str],
) -> dict[str, str]:
    """Fetch TOPIX size category for tickers from STOCK_CODE_LIST.

    Returns dict: ticker -> SIZE_CATEGORY (e.g. 'TOPIX Core30', 'TOPIX Large70',
    'TOPIX Mid400', 'TOPIX Small 1', 'TOPIX Small 2', '-').
    """
    sql = """
    SELECT TICKER, SIZE_CATEGORY
    FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
    WHERE TICKER IN UNNEST(@tickers)
      AND EXCHANGE = 'TSE'
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("tickers", "STRING", list(tickers)),
        ]
    )
    result: dict[str, str] = {}
    for row in client.query(sql, job_config=job_config).result():
        result[row["TICKER"]] = row["SIZE_CATEGORY"] or "-"
    log.info("size_category_fetched", count=len(result))
    return result


def stratify_analysis(
    trades: list[Trade],
    pairs: list[dict[str, str]],
    size_map: dict[str, str],
) -> None:
    """Run stratification analysis on trades across 3 dimensions."""
    if not trades:
        log.warning("no_trades_for_stratification")
        return

    # Build pair lookup
    pair_lookup: dict[str, dict[str, str]] = {p["pair_id"]: p for p in pairs}

    # === Axis 1: dependency_pct ===
    dep_bins = [
        ("dep_0-10%", 0.0, 10.0),
        ("dep_10-20%", 10.0, 20.0),
        ("dep_20-50%", 20.0, 50.0),
        ("dep_50%+", 50.0, 999.0),
    ]
    dep_groups: dict[str, list[Trade]] = {label: [] for label, _, _ in dep_bins}
    for t in trades:
        pair_info = pair_lookup.get(t.pair_id)
        if not pair_info:
            continue
        try:
            dep_pct = float(pair_info.get("dependency_pct", "0") or "0")
        except ValueError:
            continue
        for label, lo, hi in dep_bins:
            if lo <= dep_pct < hi:
                dep_groups[label].append(t)
                break

    # === Axis 2: abs(leader_return) ===
    abs_rets = sorted(abs(t.leader_return) for t in trades)
    if len(abs_rets) >= 4:
        q33 = abs_rets[len(abs_rets) // 3]
        q66 = abs_rets[2 * len(abs_rets) // 3]
    else:
        q33, q66 = 0.03, 0.07

    lr_bins = [
        (f"lr_abs<{q33*100:.1f}%", 0.0, q33),
        (f"lr_abs_{q33*100:.1f}-{q66*100:.1f}%", q33, q66),
        (f"lr_abs>{q66*100:.1f}%", q66, 999.0),
    ]
    lr_groups: dict[str, list[Trade]] = {label: [] for label, _, _ in lr_bins}
    for t in trades:
        abs_lr = abs(t.leader_return)
        for label, lo, hi in lr_bins:
            if lo <= abs_lr < hi:
                lr_groups[label].append(t)
                break

    # === Axis 3: follower size category ===
    size_order = ["TOPIX Core30", "TOPIX Large70", "TOPIX Mid400", "TOPIX Small 1", "TOPIX Small 2", "-"]
    size_labels = {
        "TOPIX Core30": "Core30+Large70",
        "TOPIX Large70": "Core30+Large70",
        "TOPIX Mid400": "Mid400",
        "TOPIX Small 1": "Small1",
        "TOPIX Small 2": "Small2",
        "-": "Other/NA",
    }
    size_groups: dict[str, list[Trade]] = {v: [] for v in size_labels.values()}
    for t in trades:
        cat = size_map.get(t.follower_code, "-")
        group_name = size_labels.get(cat, "Other/NA")
        size_groups[group_name].append(t)

    # Print results
    print("\n" + "=" * 100)
    print("STRATIFICATION ANALYSIS (best long-only condition)")
    print("=" * 100)

    for axis_name, groups in [
        ("dependency_pct", dep_groups),
        ("abs(leader_return) tercile", lr_groups),
        ("follower TOPIX size", size_groups),
    ]:
        print(f"\n--- {axis_name} ---")
        print(f"  {'Stratum':<25} {'N':>5} {'WR%':>6} {'AvgNet':>9} "
              f"{'Sharpe':>7} {'PF':>6} {'HoldD':>6}")
        print(f"  {'-'*75}")
        for label, group_trades in groups.items():
            if not group_trades:
                print(f"  {label:<25} {'---':>5}")
                continue
            r = compute_metrics(group_trades, label)
            print(
                f"  {label:<25} {r.n_trades:>5} {r.win_rate*100:>5.1f}% "
                f"{r.avg_ret_net*100:>8.3f}% {r.sharpe_annual:>7.2f} "
                f"{r.profit_factor:>6.2f} {r.avg_hold_days:>5.1f}"
            )
    print("=" * 100)

    # === Pair-level reproducibility ===
    pair_reproducibility(trades, pair_lookup)


def pair_reproducibility(
    trades: list[Trade],
    pair_lookup: dict[str, dict[str, str]],
) -> None:
    """Analyze pair-level win-rate stability and identify reliable pairs."""
    from collections import Counter

    # Group trades by pair_id
    by_pair: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_pair[t.pair_id].append(t)

    # Pair-level stats (only pairs with N >= 3 trades)
    MIN_TRADES = 3
    pair_stats: list[dict[str, Any]] = []
    for pair_id, pair_trades in by_pair.items():
        if len(pair_trades) < MIN_TRADES:
            continue
        n = len(pair_trades)
        wins = sum(1 for t in pair_trades if t.net_ret > 0)
        wr = wins / n
        avg_net = float(np.mean([t.net_ret for t in pair_trades]))
        pair_info = pair_lookup.get(pair_id, {})
        dep_pct = float(pair_info.get("dependency_pct", "0") or "0")
        pair_stats.append({
            "pair_id": pair_id,
            "leader": pair_info.get("leader_code", "?"),
            "follower": pair_info.get("follower_code", "?"),
            "leader_name": pair_info.get("leader_name", ""),
            "follower_name": pair_info.get("follower_name", ""),
            "n": n,
            "wins": wins,
            "wr": wr,
            "avg_net": avg_net,
            "dep_pct": dep_pct,
            "trades": pair_trades,
        })

    print("\n" + "=" * 100)
    print("PAIR-LEVEL REPRODUCIBILITY")
    print("=" * 100)

    total_pairs = len(by_pair)
    qualified_pairs = len(pair_stats)
    print(f"\n  Total pairs with trades: {total_pairs}")
    print(f"  Pairs with N>={MIN_TRADES} trades: {qualified_pairs}")

    # Win-rate distribution
    wr_bins = [(0, 0.33, "WR<33%"), (0.33, 0.50, "WR 33-50%"),
               (0.50, 0.67, "WR 50-67%"), (0.67, 0.80, "WR 67-80%"),
               (0.80, 1.01, "WR 80%+")]
    print(f"\n  --- Win-rate distribution (N>={MIN_TRADES}) ---")
    print(f"  {'Bin':<12} {'Pairs':>6} {'Trades':>7} {'AvgNet':>9}")
    print(f"  {'-'*40}")
    for lo, hi, label in wr_bins:
        bin_pairs = [p for p in pair_stats if lo <= p["wr"] < hi]
        n_trades = sum(p["n"] for p in bin_pairs)
        avg_net = float(np.mean([p["avg_net"] for p in bin_pairs])) if bin_pairs else 0
        print(f"  {label:<12} {len(bin_pairs):>6} {n_trades:>7} {avg_net*100:>8.3f}%")

    # Filter: "reliable" pairs (WR >= 60%, N >= 4)
    RELIABLE_WR = 0.60
    RELIABLE_N = 4
    reliable = [p for p in pair_stats if p["wr"] >= RELIABLE_WR and p["n"] >= RELIABLE_N]
    reliable_trades = [t for p in reliable for t in p["trades"]]

    print(f"\n  --- Reliable pairs (WR>={RELIABLE_WR*100:.0f}% & N>={RELIABLE_N}) ---")
    print(f"  Pairs: {len(reliable)} / {qualified_pairs} ({len(reliable)/qualified_pairs*100:.1f}%)")
    print(f"  Trades: {len(reliable_trades)} / {len(trades)} ({len(reliable_trades)/len(trades)*100:.1f}%)")
    if reliable_trades:
        r = compute_metrics(reliable_trades, "reliable_only")
        print(f"  Sharpe={r.sharpe_annual:.2f}  WR={r.win_rate*100:.1f}%  "
              f"AvgNet={r.avg_ret_net*100:.3f}%  PF={r.profit_factor:.2f}  N={r.n_trades}")

    # Year-by-year consistency: pairs that are profitable in >= 3 different years
    YEAR_CONSISTENCY = 3
    consistent = []
    for p in pair_stats:
        yearly_rets: dict[int, list[float]] = defaultdict(list)
        for t in p["trades"]:
            yearly_rets[t.entry_date.year].append(t.net_ret)
        profitable_years = sum(1 for yr_rets in yearly_rets.values() if sum(yr_rets) > 0)
        years_active = len(yearly_rets)
        p["profitable_years"] = profitable_years
        p["years_active"] = years_active
        if profitable_years >= YEAR_CONSISTENCY and years_active >= YEAR_CONSISTENCY:
            consistent.append(p)

    consistent_trades = [t for p in consistent for t in p["trades"]]
    print(f"\n  --- Year-consistent pairs (profitable in >={YEAR_CONSISTENCY} years, active >={YEAR_CONSISTENCY} years) ---")
    print(f"  Pairs: {len(consistent)} / {qualified_pairs} ({len(consistent)/qualified_pairs*100:.1f}%)")
    print(f"  Trades: {len(consistent_trades)} / {len(trades)} ({len(consistent_trades)/len(trades)*100:.1f}%)")
    if consistent_trades:
        r = compute_metrics(consistent_trades, "consistent_only")
        print(f"  Sharpe={r.sharpe_annual:.2f}  WR={r.win_rate*100:.1f}%  "
              f"AvgNet={r.avg_ret_net*100:.3f}%  PF={r.profit_factor:.2f}  N={r.n_trades}")

    # Top 20 most reliable pairs
    top_reliable = sorted(pair_stats, key=lambda p: (p["wr"], p["avg_net"]), reverse=True)[:20]
    print(f"\n  --- Top 20 pairs by WR (N>={MIN_TRADES}) ---")
    print(f"  {'Pair':<18} {'Leader':<10} {'Follower':<10} {'N':>3} {'WR%':>5} "
          f"{'AvgNet':>8} {'Dep%':>5} {'ProfYrs':>7}")
    print(f"  {'-'*85}")
    for p in top_reliable:
        print(f"  {p['pair_id']:<18} {p['leader']:<10} {p['follower']:<10} "
              f"{p['n']:>3} {p['wr']*100:>4.0f}% {p['avg_net']*100:>7.2f}% "
              f"{p['dep_pct']:>5.1f} {p['profitable_years']:>3}/{p['years_active']}")

    print("=" * 100)


def walk_forward_filter(trades: list[Trade]) -> None:
    """Walk-forward validation: filter trades by trailing pair WR.

    For each trade, check the pair's past N trades. Only "enter" if
    trailing WR >= threshold. This simulates a dynamic pair-selection
    rule without look-ahead bias.
    """
    # Trades must be sorted by entry_date (guaranteed by generate_trades)
    configs = [
        # (lookback_n, min_wr_threshold, label)
        (3, 0.67, "WF_N3_WR67"),
        (4, 0.75, "WF_N4_WR75"),
        (5, 0.60, "WF_N5_WR60"),
        (5, 0.80, "WF_N5_WR80"),
    ]

    print("\n" + "=" * 100)
    print("WALK-FORWARD PAIR FILTER (no look-ahead)")
    print("=" * 100)
    print(f"\n  Baseline: {len(trades)} trades")
    baseline = compute_metrics(trades, "baseline")
    print(f"  Sharpe={baseline.sharpe_annual:.2f}  WR={baseline.win_rate*100:.1f}%  "
          f"AvgNet={baseline.avg_ret_net*100:.3f}%  PF={baseline.profit_factor:.2f}")

    print(f"\n  {'Config':<15} {'N_pass':>7} {'%kept':>6} {'WR%':>6} {'AvgNet':>9} "
          f"{'Sharpe':>7} {'PF':>6}")
    print(f"  {'-'*65}")

    for lookback_n, min_wr, label in configs:
        # Track each pair's trailing history
        pair_history: dict[str, list[bool]] = defaultdict(list)
        passed_trades: list[Trade] = []

        for t in trades:
            history = pair_history[t.pair_id]
            # Check if we have enough history and WR meets threshold
            if len(history) >= lookback_n:
                recent = history[-lookback_n:]
                trailing_wr = sum(recent) / len(recent)
                if trailing_wr >= min_wr:
                    passed_trades.append(t)
            # Always update history (regardless of whether we entered)
            pair_history[t.pair_id].append(t.net_ret > 0)

        if not passed_trades:
            print(f"  {label:<15} {'0':>7} {'0.0%':>6}")
            continue

        r = compute_metrics(passed_trades, label)
        pct_kept = len(passed_trades) / len(trades) * 100
        print(f"  {label:<15} {r.n_trades:>7} {pct_kept:>5.1f}% {r.win_rate*100:>5.1f}% "
              f"{r.avg_ret_net*100:>8.3f}% {r.sharpe_annual:>7.2f} {r.profit_factor:>6.2f}")

        # Year-by-year for best config
        if label == "WF_N5_WR60":
            by_year: dict[int, list[Trade]] = defaultdict(list)
            for t in passed_trades:
                by_year[t.entry_date.year].append(t)
            print(f"\n  Year-by-year ({label}):")
            print(f"  {'Year':<6} {'N':>5} {'WR%':>6} {'AvgNet':>9}")
            for year in sorted(by_year.keys()):
                yr = by_year[year]
                n = len(yr)
                wr = sum(1 for t in yr if t.net_ret > 0) / n
                avg = float(np.mean([t.net_ret for t in yr]))
                print(f"  {year:<6} {n:>5} {wr*100:>5.1f}% {avg*100:>8.3f}%")
            print()

    print("=" * 100)


def main() -> None:
    """Run supply-chain cascade backtest."""
    configure_logging()

    parser = argparse.ArgumentParser(description="Supply-chain cascade backtest")
    parser.add_argument("--from", dest="date_from", default="2020-04-01",
                        help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", default="2026-04-30",
                        help="Backtest end date (YYYY-MM-DD)")
    parser.add_argument("--pairs", default="data/master/association_pairs.csv",
                        help="Path to association pairs CSV")
    parser.add_argument("--output", default="data/csv/bt_supply_chain_cascade.csv",
                        help="Output trades CSV path")
    parser.add_argument("--stratify", action="store_true",
                        help="Run stratification analysis on best condition")
    args = parser.parse_args()

    date_from = datetime.strptime(args.date_from, "%Y-%m-%d").date()
    date_to = datetime.strptime(args.date_to, "%Y-%m-%d").date()

    # Load pairs
    pairs = load_association_pairs(Path(args.pairs))

    # Collect all unique tickers
    all_tickers: set[str] = set()
    for p in pairs:
        lc = p["leader_code"]
        fc = p["follower_code"]
        # Only JP 4-digit tickers (skip foreign tickers for now)
        if lc.isdigit() and len(lc) == 4:
            all_tickers.add(lc)
        if fc.isdigit() and len(fc) == 4:
            all_tickers.add(fc)

    log.info("unique_tickers", count=len(all_tickers))

    # Fetch data from BQ (single batch)
    client = get_bq_client()

    # Add buffer for prev/next day lookups
    buffer = timedelta(days=40)
    earnings = fetch_earnings_dates(client, all_tickers, date_from - buffer, date_to + buffer)
    prices = fetch_stock_prices(client, all_tickers, date_from - buffer, date_to + buffer)

    trading_days = get_trading_days(prices)
    log.info("trading_days", count=len(trading_days))

    # Filter pairs to JP-only for BT
    jp_pairs = [
        p for p in pairs
        if p["leader_code"].isdigit() and len(p["leader_code"]) == 4
        and p["follower_code"].isdigit() and len(p["follower_code"]) == 4
    ]
    log.info("jp_pairs", count=len(jp_pairs))

    # Run all 12 conditions
    conditions = [
        ("conservative", "pre", "ls"),
        ("conservative", "pre", "long"),
        ("conservative", "pre", "short"),
        ("conservative", "post", "ls"),
        ("conservative", "post", "long"),
        ("conservative", "post", "short"),
        ("aggressive", "pre", "ls"),
        ("aggressive", "pre", "long"),
        ("aggressive", "pre", "short"),
        ("aggressive", "post", "ls"),
        ("aggressive", "post", "long"),
        ("aggressive", "post", "short"),
    ]

    all_results: list[BacktestResult] = []
    best_trades: list[Trade] = []
    best_sharpe = float("-inf")

    for entry_mode, exit_mode, dir_mode in conditions:
        label = f"{entry_mode[:4]}_{exit_mode}_{dir_mode}"
        trades = generate_trades(
            jp_pairs, earnings, prices, trading_days,
            entry_mode=entry_mode,
            exit_mode=exit_mode,
            direction_mode=dir_mode,
        )
        result = compute_metrics(trades, label)
        all_results.append(result)

        if result.sharpe_annual > best_sharpe:
            best_sharpe = result.sharpe_annual
            best_trades = trades

    # Print comparison
    print_results(all_results)

    # Year-by-year for top conditions
    for r in sorted(all_results, key=lambda x: x.sharpe_annual, reverse=True)[:3]:
        if r.trades:
            print_yearly_breakdown(r.trades, r.label)

    # Save best trades
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_trades_csv(best_trades, output_path)

    # Summary by pair type
    print("\n--- By pair_type (best condition) ---")
    by_type: dict[str, list[Trade]] = defaultdict(list)
    for t in best_trades:
        pair_info = next((p for p in jp_pairs if p["pair_id"] == t.pair_id), None)
        if pair_info:
            by_type[pair_info["pair_type"]].append(t)
    for ptype, type_trades in sorted(by_type.items()):
        r = compute_metrics(type_trades, ptype)
        print(f"  {ptype:<10} N={r.n_trades:>4} WR={r.win_rate*100:.1f}% "
              f"AvgNet={r.avg_ret_net*100:.3f}% Sharpe={r.sharpe_annual:.2f}")

    # Stratification analysis
    if args.stratify:
        # Find best long-only condition trades
        long_results = [r for r in all_results if r.label.endswith("_long")]
        if long_results:
            best_long = max(long_results, key=lambda x: x.sharpe_annual)
            strat_trades = best_long.trades
            log.info("stratify_target", label=best_long.label, n_trades=len(strat_trades))
        else:
            strat_trades = best_trades

        # Fetch follower size categories
        follower_tickers = {t.follower_code for t in strat_trades}
        size_map = fetch_size_category(client, follower_tickers)

        stratify_analysis(strat_trades, jp_pairs, size_map)

        # Walk-forward validation
        walk_forward_filter(strat_trades)


if __name__ == "__main__":
    main()

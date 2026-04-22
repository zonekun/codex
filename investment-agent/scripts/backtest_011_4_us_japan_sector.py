"""Backtest: 011-4 US Sector ETF -> Japan Sector ETF Long-Short with Kill Switch.

Paper: SIG-FIN-036-13
Knowledge: docs/knowledges/analysis/011-4_us_japan_sector_leadlag.md
Kill Switch: docs/knowledges/strategies/002_rolling_ic_strategy_kill_switch.md

Promotion from PoC (poc_011_4b, poc_011_4_pass2, poc_011_4_deep_dive) to
production-grade backtest with Kill Switch integration.

Tasks:
    1. Kill Switch parameter optimisation (threshold x window combinations)
    2. Final backtest with best Kill Switch params (2018-2025, 8 years)
    3. Kill Switch timeline (activation/deactivation events)
    4. Comparison: Kill Switch ON vs OFF

Outputs:
    C:\\tmp\\backtest_011_4\\kill_switch_optimization.csv
    C:\\tmp\\backtest_011_4\\final_metrics.csv
    C:\\tmp\\backtest_011_4\\kill_switch_timeline.csv
    C:\\tmp\\backtest_011_4\\returns_daily_final.csv
"""
from __future__ import annotations

import sys
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

warnings.filterwarnings("ignore", category=FutureWarning)

import structlog

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
JST = ZoneInfo("Asia/Tokyo")

OUTPUT_DIR = Path(r"C:\tmp\backtest_011_4")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Cache directories from prior PoC runs
DEEP_DIVE_DIR = Path(r"C:\tmp\poc_011_4_deep_dive")
PASS2_DIR = Path(r"C:\tmp\poc_011_4_pass2")

# US sector ETFs
US_TICKERS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]
N_US = len(US_TICKERS)  # 11

# Japan TOPIX-17 sector ETFs
JP_TICKERS = [str(t) for t in range(1617, 1634)]  # 1617..1633
N_JP = len(JP_TICKERS)  # 17

N_TOTAL = N_US + N_JP  # 28

# Cyclical / Defensive classification (from paper)
US_CYCLICAL = {"XLB", "XLE", "XLF", "XLRE"}
US_DEFENSIVE = {"XLK", "XLP", "XLU", "XLV"}
JP_CYCLICAL = {"1618", "1625", "1629", "1631"}
JP_DEFENSIVE = {"1617", "1621", "1627", "1630"}

# Periods
WARMUP_START = pd.Timestamp("2016-01-01")
WARMUP_END = pd.Timestamp("2017-12-31")
OOS_START = pd.Timestamp("2018-01-01")
OOS_END = pd.Timestamp("2025-12-31")  # 8 years, exclude 2026-Q1 (Iran war)

# Strategy params (best from Pass 2)
WINDOW_L = 60
K_EIG = 3
LAMBDA_REG = 0.9
Q_DEFAULT = 3

# Cost / tax
ONEWAY_COST_BPS = 1  # ETF large-cap, high liquidity
BORROW_RATE_ANN = 0.0075  # 0.75% annualised borrow cost for ETF short selling
TAX_RATE = 0.20315

# XLC inception date
XLC_INCEPTION = pd.Timestamp("2018-06-18")

# Kill Switch parameter grid
KS_THRESHOLDS = [-0.01, -0.02, -0.03, -0.05]
KS_WINDOWS_WEEKS = [8, 12, 26]


# ---------------------------------------------------------------------------
# Data loading (from deep_dive / pass2 cache, NO BQ queries)
# ---------------------------------------------------------------------------
def load_us_data() -> pd.DataFrame:
    """Load US sector ETF daily prices from deep dive cache."""
    cache_path = DEEP_DIVE_DIR / "us_sector_prices_extended.csv"
    if cache_path.exists():
        log.info("US data from deep dive cache", path=str(cache_path))
        return pd.read_csv(cache_path, parse_dates=["Date"], encoding="utf-8")

    # Fallback to pass2 cache
    cache_path = PASS2_DIR / "us_sector_prices_pass2.csv"
    if cache_path.exists():
        log.info("US data from pass2 cache", path=str(cache_path))
        return pd.read_csv(cache_path, parse_dates=["Date"], encoding="utf-8")

    raise FileNotFoundError("No US cache found. Run deep_dive or pass2 first.")


def load_jp_data() -> pd.DataFrame:
    """Load Japan TOPIX-17 ETF daily prices from deep dive cache."""
    cache_path = DEEP_DIVE_DIR / "jp_sector_prices_extended.parquet"
    if cache_path.exists():
        log.info("JP data from deep dive cache", path=str(cache_path))
        return pd.read_parquet(cache_path)

    cache_path = PASS2_DIR / "jp_sector_prices_pass2.parquet"
    if cache_path.exists():
        log.info("JP data from pass2 cache", path=str(cache_path))
        return pd.read_parquet(cache_path)

    raise FileNotFoundError("No JP cache found. Run deep_dive or pass2 first.")


# ---------------------------------------------------------------------------
# Return construction (same as deep_dive)
# ---------------------------------------------------------------------------
def build_us_returns(df_us: pd.DataFrame) -> pd.DataFrame:
    """Build US sector close-to-close log returns (wide: date x ticker)."""
    df = df_us.copy()
    df = df.sort_values(["Ticker", "Date"]).drop_duplicates(["Ticker", "Date"], keep="last")
    pivot_close = df.pivot(index="Date", columns="Ticker", values="Close").sort_index()
    pivot_close = pivot_close.ffill(limit=3)

    if "XLC" in pivot_close.columns:
        xlc_mask = pivot_close.index < XLC_INCEPTION
        pivot_close.loc[xlc_mask, "XLC"] = np.nan

    log_ret = np.log(pivot_close / pivot_close.shift(1))
    return log_ret


def build_jp_returns(df_jp: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build Japan sector open-to-close and close-to-close log returns."""
    df = df_jp.copy()
    df = df.sort_values(["TICKER", "DATE"]).drop_duplicates(["TICKER", "DATE"], keep="last")
    pivot_open = df.pivot(index="DATE", columns="TICKER", values="ADJ_OPEN").sort_index()
    pivot_close = df.pivot(index="DATE", columns="TICKER", values="ADJ_CLOSE").sort_index()
    pivot_open = pivot_open.ffill(limit=5)
    pivot_close = pivot_close.ffill(limit=5)
    pivot_open = pivot_open[JP_TICKERS]
    pivot_close = pivot_close[JP_TICKERS]
    oc = np.log(pivot_close / pivot_open)
    c2c = np.log(pivot_close / pivot_close.shift(1))
    return oc, c2c


def align_common_dates(
    us_c2c: pd.DataFrame,
    jp_oc: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Align US day t with JP day t+1 on common business day pairs."""
    us_dates = us_c2c.dropna(how="all").index.sort_values()
    jp_dates = jp_oc.dropna(how="all").index.sort_values()
    jp_dates_sorted = sorted(jp_dates)

    pairs: list[tuple] = []
    jp_idx = 0
    for us_d in us_dates:
        target = us_d + pd.Timedelta(days=1)
        while jp_idx < len(jp_dates_sorted) and jp_dates_sorted[jp_idx] < target:
            jp_idx += 1
        if jp_idx >= len(jp_dates_sorted):
            break
        jp_d = jp_dates_sorted[jp_idx]
        if (jp_d - us_d).days <= 7:
            pairs.append((us_d, jp_d))

    if not pairs:
        raise ValueError("No aligned date pairs found")

    pair_df = pd.DataFrame(pairs, columns=["US_DATE", "JP_DATE"])
    pair_df = pair_df.sort_values("US_DATE").drop_duplicates("JP_DATE", keep="last")
    pair_df = pair_df.sort_values("US_DATE").reset_index(drop=True)

    us_dates_aligned = pd.DatetimeIndex(pair_df["US_DATE"].values)
    jp_dates_aligned = pd.DatetimeIndex(pair_df["JP_DATE"].values)

    us_aligned = us_c2c.loc[us_dates_aligned].copy()
    us_aligned.index = range(len(us_aligned))

    jp_aligned = jp_oc.loc[jp_dates_aligned].copy()
    jp_aligned.index = range(len(jp_aligned))

    date_info = pd.DataFrame({
        "US_DATE": us_dates_aligned.values,
        "JP_DATE": jp_dates_aligned.values,
    })

    log.info(
        "Date alignment",
        total_pairs=len(pair_df),
        us_range=f"{us_dates_aligned[0].date()}..{us_dates_aligned[-1].date()}",
        jp_range=f"{jp_dates_aligned[0].date()}..{jp_dates_aligned[-1].date()}",
    )

    return us_aligned, jp_aligned, date_info


# ---------------------------------------------------------------------------
# V0 / C0 construction
# ---------------------------------------------------------------------------
def build_V0() -> np.ndarray:
    """Construct V0 in R^(28 x 3): global, country-spread, cyclical/defensive."""
    N = N_TOTAL
    v1 = np.ones(N) / np.sqrt(N)

    v2_raw = np.zeros(N)
    v2_raw[:N_US] = 1.0 / np.sqrt(N_US)
    v2_raw[N_US:] = -1.0 / np.sqrt(N_JP)
    v2_raw = v2_raw - np.dot(v2_raw, v1) * v1
    v2 = v2_raw / np.linalg.norm(v2_raw)

    v3_raw = np.zeros(N)
    for i, t in enumerate(US_TICKERS):
        if t in US_CYCLICAL:
            v3_raw[i] = 1.0
        elif t in US_DEFENSIVE:
            v3_raw[i] = -1.0
    for j, t in enumerate(JP_TICKERS):
        if t in JP_CYCLICAL:
            v3_raw[N_US + j] = 1.0
        elif t in JP_DEFENSIVE:
            v3_raw[N_US + j] = -1.0
    v3_raw = v3_raw - np.dot(v3_raw, v1) * v1
    v3_raw = v3_raw - np.dot(v3_raw, v2) * v2
    v3 = v3_raw / np.linalg.norm(v3_raw)

    V0 = np.column_stack([v1, v2, v3])
    return V0


def compute_C0(
    us_c2c_warm: np.ndarray,
    jp_oc_warm: np.ndarray,
    V0: np.ndarray,
) -> np.ndarray:
    """Compute C0 target correlation matrix from warm-up period."""
    Z = np.concatenate([us_c2c_warm, jp_oc_warm], axis=1)
    Z = Z - Z.mean(axis=0, keepdims=True)
    std = Z.std(axis=0, keepdims=True)
    std[std == 0] = 1.0
    Z = Z / std

    C_full = (Z.T @ Z) / max(Z.shape[0] - 1, 1)

    D0 = np.diag(np.diag(V0.T @ C_full @ V0))
    C_raw = V0 @ D0 @ V0.T
    Delta = np.diag(C_raw).copy()
    Delta[Delta <= 0] = 1e-8
    Delta_inv_sqrt = np.diag(1.0 / np.sqrt(Delta))
    C0 = Delta_inv_sqrt @ C_raw @ Delta_inv_sqrt

    return C0


# ---------------------------------------------------------------------------
# Regularised PCA predictor
# ---------------------------------------------------------------------------
def compute_predictor(
    C_window: np.ndarray,
    C0: np.ndarray,
    K: int,
    lam: float,
) -> np.ndarray:
    """Compute B_t = V_JP @ V_US.T from regularised PCA."""
    C_reg = (1 - lam) * C_window + lam * C0
    C_reg = (C_reg + C_reg.T) / 2.0

    vals, vecs = np.linalg.eigh(C_reg)
    order = np.argsort(vals)[::-1][:K]
    V = vecs[:, order]

    V_US = V[:N_US, :]
    V_JP = V[N_US:, :]
    B = V_JP @ V_US.T

    return B


# ---------------------------------------------------------------------------
# Long-Short weight construction
# ---------------------------------------------------------------------------
def build_ls_weights(signal: np.ndarray, q: int) -> np.ndarray:
    """Build long-short equal-weight portfolio. Long top-q, Short bottom-q."""
    ranking = np.argsort(-signal)  # descending
    weights = np.zeros(len(signal))
    weights[ranking[:q]] = 1.0 / q
    weights[ranking[-q:]] = -1.0 / q
    return weights


# ---------------------------------------------------------------------------
# Core backtest engine (with signal output for IC)
# ---------------------------------------------------------------------------
def run_backtest_with_signals(
    us_vals: np.ndarray,
    jp_vals: np.ndarray,
    date_info: pd.DataFrame,
    V0: np.ndarray,
    C0: np.ndarray,
) -> pd.DataFrame:
    """Run rolling long-short backtest over OOS period. Returns per-day signal + return.

    Uses fixed params: WINDOW_L, K_EIG, LAMBDA_REG, Q_DEFAULT.
    Also returns per-sector signals for IC computation.
    """
    jp_dates = date_info["JP_DATE"].values
    oos_mask = (jp_dates >= np.datetime64(OOS_START)) & (jp_dates <= np.datetime64(OOS_END))
    oos_indices = np.where(oos_mask)[0]

    log.info("Backtest setup", oos_days=len(oos_indices), window_L=WINDOW_L)

    records = []
    for t_idx in oos_indices:
        if t_idx < WINDOW_L:
            continue

        jp_date = pd.Timestamp(jp_dates[t_idx])
        us_date = pd.Timestamp(date_info["US_DATE"].values[t_idx])

        # Rolling correlation window
        window = slice(t_idx - WINDOW_L, t_idx)
        us_win = us_vals[window]
        jp_win = jp_vals[window]

        Z_win = np.concatenate([us_win, jp_win], axis=1)
        Z_win = Z_win - Z_win.mean(axis=0, keepdims=True)
        std_win = Z_win.std(axis=0, keepdims=True)
        std_win[std_win == 0] = 1.0
        Z_win = Z_win / std_win
        C_win = (Z_win.T @ Z_win) / max(Z_win.shape[0] - 1, 1)

        B_reg = compute_predictor(C_win, C0, K_EIG, LAMBDA_REG)

        # Standardise today's US c2c return
        us_today = us_vals[t_idx]
        us_mu = us_vals[window].mean(axis=0)
        us_sig = us_vals[window].std(axis=0)
        us_sig[us_sig == 0] = 1.0
        z_us = (us_today - us_mu) / us_sig

        z_pred = B_reg @ z_us
        w = build_ls_weights(z_pred, Q_DEFAULT)
        ret = float(np.dot(w, jp_vals[t_idx]))

        rec: dict[str, object] = {
            "JP_DATE": jp_date,
            "US_DATE": us_date,
            "ret_gross": ret,
        }
        # Per-sector signals + realised returns (for IC)
        for j, ticker in enumerate(JP_TICKERS):
            rec[f"sig_{ticker}"] = float(z_pred[j])
            rec[f"real_{ticker}"] = float(jp_vals[t_idx, j])

        records.append(rec)

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Rolling IC computation
# ---------------------------------------------------------------------------
def compute_rolling_ic(
    ret_df: pd.DataFrame,
    window_weeks: int,
) -> pd.DataFrame:
    """Compute rolling IC with specified window (in weeks).

    IC = Spearman correlation between signal and realised OC return across 17 JP sectors.
    Rolling IC = moving average of weekly IC over window_weeks.
    """
    df = ret_df.copy()
    df["JP_DATE"] = pd.to_datetime(df["JP_DATE"])
    df = df.sort_values("JP_DATE").reset_index(drop=True)

    sig_cols = [f"sig_{t}" for t in JP_TICKERS]
    real_cols = [f"real_{t}" for t in JP_TICKERS]

    # Daily IC
    daily_ics = []
    for _, row in df.iterrows():
        sig_vec = row[sig_cols].values.astype(float)
        real_vec = row[real_cols].values.astype(float)
        if np.all(np.isfinite(sig_vec)) and np.all(np.isfinite(real_vec)):
            corr, _ = spearmanr(sig_vec, real_vec)
            daily_ics.append({"JP_DATE": row["JP_DATE"], "ic_daily": corr})
        else:
            daily_ics.append({"JP_DATE": row["JP_DATE"], "ic_daily": np.nan})

    ic_df = pd.DataFrame(daily_ics)
    ic_df["JP_DATE"] = pd.to_datetime(ic_df["JP_DATE"])

    # Weekly IC: average daily IC within each ISO week
    ic_df["year_week"] = (
        ic_df["JP_DATE"].dt.isocalendar().year.astype(str)
        + "-W"
        + ic_df["JP_DATE"].dt.isocalendar().week.astype(str).str.zfill(2)
    )

    weekly = ic_df.groupby("year_week").agg(
        week_date=("JP_DATE", "last"),
        ic_weekly=("ic_daily", "mean"),
        n_days=("ic_daily", "count"),
    ).reset_index()
    weekly = weekly.sort_values("week_date").reset_index(drop=True)

    # Rolling average
    min_periods = max(window_weeks // 2, 1)
    weekly[f"ic_rolling_{window_weeks}w"] = (
        weekly["ic_weekly"].rolling(window_weeks, min_periods=min_periods).mean()
    )

    return weekly


# ---------------------------------------------------------------------------
# Kill Switch application
# ---------------------------------------------------------------------------
def apply_kill_switch(
    ret_df: pd.DataFrame,
    rolling_ic_df: pd.DataFrame,
    ic_col: str,
    threshold: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply Kill Switch to daily returns based on rolling IC threshold.

    When rolling IC < threshold, position is flattened (return = 0).
    Recovery: IC >= threshold.

    Returns:
        - ret_df with additional column 'ret_ks' (Kill Switch applied)
        - timeline_df with activation/deactivation events
    """
    df = ret_df.copy()
    df["JP_DATE"] = pd.to_datetime(df["JP_DATE"])

    # Build IC lookup: map each JP_DATE to the most recent rolling IC
    ic = rolling_ic_df.copy()
    ic["week_date"] = pd.to_datetime(ic["week_date"])
    ic = ic.dropna(subset=[ic_col]).sort_values("week_date").reset_index(drop=True)

    # For each trading day, find the latest available rolling IC
    ic_values = ic[["week_date", ic_col]].values
    ic_dates = ic["week_date"].values

    df = df.sort_values("JP_DATE").reset_index(drop=True)

    kill_active = False
    ret_ks = []
    ks_status = []
    timeline_events: list[dict] = []

    for _, row in df.iterrows():
        jp_date = row["JP_DATE"]

        # Find latest IC before this date
        mask = ic_dates <= np.datetime64(jp_date)
        if mask.any():
            latest_idx = np.where(mask)[0][-1]
            current_ic = float(ic_values[latest_idx, 1])
        else:
            current_ic = np.nan

        prev_active = kill_active

        if np.isnan(current_ic):
            # No IC data yet, trade normally
            kill_active = False
        elif current_ic < threshold:
            kill_active = True
        else:
            kill_active = False

        # Record state transitions
        if kill_active and not prev_active:
            timeline_events.append({
                "date": jp_date,
                "event": "KILL_ON",
                "ic_value": current_ic,
                "threshold": threshold,
            })
        elif not kill_active and prev_active:
            timeline_events.append({
                "date": jp_date,
                "event": "KILL_OFF",
                "ic_value": current_ic,
                "threshold": threshold,
            })

        if kill_active:
            ret_ks.append(0.0)
            ks_status.append("FLAT")
        else:
            ret_ks.append(row["ret_gross"])
            ks_status.append("ACTIVE")

    df["ret_ks"] = ret_ks
    df["ks_status"] = ks_status

    timeline_df = pd.DataFrame(timeline_events) if timeline_events else pd.DataFrame(
        columns=["date", "event", "ic_value", "threshold"]
    )

    return df, timeline_df


# ---------------------------------------------------------------------------
# Metrics computation (3-stage)
# ---------------------------------------------------------------------------
def compute_metrics(
    returns: np.ndarray,
    label: str = "",
) -> dict:
    """Compute 3-stage metrics: gross, after-cost+borrow, after-tax."""
    s = pd.Series(returns).dropna()
    n_days = len(s)
    if n_days == 0:
        return {
            "label": label, "n_days": 0,
            "AR_gross": np.nan, "Vol": np.nan,
            "Sharpe_gross": np.nan, "MDD_gross": np.nan,
            "AR_net": np.nan, "Sharpe_net": np.nan, "MDD_net": np.nan,
            "AR_tax": np.nan, "Sharpe_tax": np.nan,
            "win_rate": np.nan,
        }

    # Gross
    mu_gross = s.mean() * 252
    vol = s.std(ddof=1) * np.sqrt(252)
    sharpe_gross = mu_gross / vol if vol > 0 else np.nan
    cum_gross = (1 + s).cumprod()
    mdd_gross = float(((cum_gross / cum_gross.cummax()) - 1).min())
    win_rate = float((s > 0).sum() / n_days)

    # After cost + borrow (only on active days for KS version)
    # For KS version, when flat, no cost incurred
    active_days = (s != 0).sum()
    total_cost_drag = active_days * (2 * ONEWAY_COST_BPS / 10_000 + BORROW_RATE_ANN / 252)
    mu_net = (s.sum() - total_cost_drag) / n_days * 252
    sharpe_net = mu_net / vol if vol > 0 else np.nan
    s_net = s.copy()
    s_net[s != 0] -= (2 * ONEWAY_COST_BPS / 10_000 + BORROW_RATE_ANN / 252)
    cum_net = (1 + s_net).cumprod()
    mdd_net = float(((cum_net / cum_net.cummax()) - 1).min())

    # After tax
    mu_tax = mu_net * (1 - TAX_RATE) if mu_net > 0 else mu_net
    sharpe_tax = mu_tax / vol if vol > 0 else np.nan

    return {
        "label": label,
        "n_days": n_days,
        "active_days": int(active_days),
        "AR_gross": mu_gross,
        "Vol": vol,
        "Sharpe_gross": sharpe_gross,
        "MDD_gross": mdd_gross,
        "win_rate": win_rate,
        "AR_net": mu_net,
        "Sharpe_net": sharpe_net,
        "MDD_net": mdd_net,
        "AR_tax": mu_tax,
        "Sharpe_tax": sharpe_tax,
    }


def compute_metrics_no_ks(
    returns: np.ndarray,
    label: str = "",
) -> dict:
    """Compute 3-stage metrics WITHOUT Kill Switch (standard: cost every day)."""
    s = pd.Series(returns).dropna()
    n_days = len(s)
    if n_days == 0:
        return {"label": label, "n_days": 0}

    mu_gross = s.mean() * 252
    vol = s.std(ddof=1) * np.sqrt(252)
    sharpe_gross = mu_gross / vol if vol > 0 else np.nan
    cum_gross = (1 + s).cumprod()
    mdd_gross = float(((cum_gross / cum_gross.cummax()) - 1).min())
    win_rate = float((s > 0).sum() / n_days)

    daily_cost = 2 * ONEWAY_COST_BPS / 10_000
    daily_borrow = BORROW_RATE_ANN / 252
    s_net = s - daily_cost - daily_borrow
    mu_net = s_net.mean() * 252
    sharpe_net = mu_net / vol if vol > 0 else np.nan
    cum_net = (1 + s_net).cumprod()
    mdd_net = float(((cum_net / cum_net.cummax()) - 1).min())

    mu_tax = mu_net * (1 - TAX_RATE) if mu_net > 0 else mu_net
    sharpe_tax = mu_tax / vol if vol > 0 else np.nan

    return {
        "label": label,
        "n_days": n_days,
        "active_days": n_days,
        "AR_gross": mu_gross,
        "Vol": vol,
        "Sharpe_gross": sharpe_gross,
        "MDD_gross": mdd_gross,
        "win_rate": win_rate,
        "AR_net": mu_net,
        "Sharpe_net": sharpe_net,
        "MDD_net": mdd_net,
        "AR_tax": mu_tax,
        "Sharpe_tax": sharpe_tax,
    }


# ---------------------------------------------------------------------------
# Year-by-year metrics
# ---------------------------------------------------------------------------
def compute_yearly_metrics(
    df: pd.DataFrame,
    ret_col: str,
    label_prefix: str = "",
    ks_status_col: str | None = None,
) -> pd.DataFrame:
    """Compute yearly metrics, optionally with Kill Switch stats."""
    df = df.copy()
    df["JP_DATE"] = pd.to_datetime(df["JP_DATE"])
    df["year"] = df["JP_DATE"].dt.year

    rows = []
    for year in sorted(df["year"].unique()):
        subset = df[df["year"] == year]
        rets = subset[ret_col].values
        m = compute_metrics(rets, label=f"{label_prefix}{year}")
        m["year"] = year

        if ks_status_col and ks_status_col in subset.columns:
            flat_days = int((subset[ks_status_col] == "FLAT").sum())
            m["flat_days"] = flat_days
        else:
            m["flat_days"] = 0

        rows.append(m)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    """Run full backtest with Kill Switch optimisation."""
    ts0 = datetime.now(tz=JST)
    log.info("011-4 Backtest with Kill Switch start", ts=ts0.isoformat())

    # ===== 1. Load data from cache =====
    df_us = load_us_data()
    df_jp = load_jp_data()
    log.info("Data loaded", us_rows=len(df_us), jp_rows=len(df_jp))

    # 2. Build returns
    us_c2c = build_us_returns(df_us)
    jp_oc, _ = build_jp_returns(df_jp)
    log.info("Returns built", us_c2c_shape=us_c2c.shape, jp_oc_shape=jp_oc.shape)

    # 3. Align common dates
    us_aligned, jp_aligned, date_info = align_common_dates(us_c2c, jp_oc)
    us_vals = np.nan_to_num(us_aligned.values, nan=0.0)
    jp_vals = np.nan_to_num(jp_aligned.values, nan=0.0)

    # 4. Build V0 / C0
    V0 = build_V0()
    jp_dates = date_info["JP_DATE"].values
    warmup_mask = (jp_dates >= np.datetime64(WARMUP_START)) & (jp_dates <= np.datetime64(WARMUP_END))
    warmup_count = int(warmup_mask.sum())
    log.info("Warm-up period", days=warmup_count)

    if warmup_count < 100:
        log.error("Insufficient warm-up data", days=warmup_count)
        return 1

    us_warm = us_vals[warmup_mask]
    jp_warm = jp_vals[warmup_mask]
    C0 = compute_C0(us_warm, jp_warm, V0)

    # ===== 5. Run backtest (no Kill Switch, raw returns + signals) =====
    log.info("Running base backtest (2018-2025)")
    ret_df = run_backtest_with_signals(us_vals, jp_vals, date_info, V0, C0)
    log.info("Base backtest done", n_days=len(ret_df))

    # ===== 6. Kill Switch parameter optimisation (Task 1) =====
    log.info("Starting Kill Switch optimisation")

    # Compute no-KS baseline metrics
    no_ks_metrics = compute_metrics_no_ks(ret_df["ret_gross"].values, label="NO_KS")
    log.info("No-KS baseline", sharpe_tax=f"{no_ks_metrics.get('Sharpe_tax', 'N/A'):.4f}")

    opt_rows = [no_ks_metrics]

    best_sharpe_tax = float("-inf")
    best_params: dict = {}
    best_ret_df: pd.DataFrame = pd.DataFrame()
    best_timeline: pd.DataFrame = pd.DataFrame()

    no_ks_mdd = no_ks_metrics.get("MDD_net", -1.0)

    for window_w in KS_WINDOWS_WEEKS:
        # Compute rolling IC with this window
        rolling_ic = compute_rolling_ic(ret_df, window_w)
        ic_col = f"ic_rolling_{window_w}w"

        for threshold in KS_THRESHOLDS:
            label = f"KS_w{window_w}_t{threshold}"
            log.info(f"Testing {label}")

            ret_ks, timeline = apply_kill_switch(ret_df, rolling_ic, ic_col, threshold)
            m = compute_metrics(ret_ks["ret_ks"].values, label=label)
            m["ks_window_weeks"] = window_w
            m["ks_threshold"] = threshold

            # Count max consecutive flat days
            ks_flat = (ret_ks["ks_status"] == "FLAT").astype(int)
            max_consec_flat = 0
            current_flat = 0
            for val in ks_flat:
                if val == 1:
                    current_flat += 1
                    max_consec_flat = max(max_consec_flat, current_flat)
                else:
                    current_flat = 0
            m["max_consec_flat_days"] = max_consec_flat
            m["total_flat_days"] = int(ks_flat.sum())

            opt_rows.append(m)

            sharpe_tax = m.get("Sharpe_tax", float("-inf"))

            if isinstance(sharpe_tax, (int, float)) and not np.isnan(sharpe_tax):
                # Select: highest tax Sharpe (MDD improvement is secondary)
                if sharpe_tax > best_sharpe_tax:
                    best_sharpe_tax = sharpe_tax
                    best_params = {"window_weeks": window_w, "threshold": threshold}
                    best_ret_df = ret_ks.copy()
                    best_timeline = timeline.copy()

    # Save optimisation results
    opt_df = pd.DataFrame(opt_rows)
    opt_df.to_csv(OUTPUT_DIR / "kill_switch_optimization.csv", index=False, encoding="utf-8")
    log.info("Kill Switch optimisation complete", best_params=best_params, best_sharpe_tax=f"{best_sharpe_tax:.4f}")

    # ===== 7. Final backtest with best KS params (Task 2) =====
    log.info("=== Final backtest with best Kill Switch ===", **best_params)

    # Final metrics (full period)
    final_metrics = compute_metrics(best_ret_df["ret_ks"].values, label="FINAL_WITH_KS")
    final_metrics["ks_window_weeks"] = best_params.get("window_weeks", "N/A")
    final_metrics["ks_threshold"] = best_params.get("threshold", "N/A")

    # No-KS comparison
    final_no_ks = compute_metrics_no_ks(ret_df["ret_gross"].values, label="FINAL_NO_KS")

    final_df = pd.DataFrame([final_metrics, final_no_ks])
    final_df.to_csv(OUTPUT_DIR / "final_metrics.csv", index=False, encoding="utf-8")

    # Year-by-year with KS
    yearly_ks = compute_yearly_metrics(best_ret_df, "ret_ks", label_prefix="KS_", ks_status_col="ks_status")
    yearly_no_ks = compute_yearly_metrics(ret_df, "ret_gross", label_prefix="NOKS_")

    yearly_all = pd.concat([yearly_ks, yearly_no_ks], ignore_index=True)
    yearly_all.to_csv(OUTPUT_DIR / "yearly_metrics.csv", index=False, encoding="utf-8")

    # Kill Switch timeline
    best_timeline.to_csv(OUTPUT_DIR / "kill_switch_timeline.csv", index=False, encoding="utf-8")

    # Daily returns
    out_cols = ["JP_DATE", "US_DATE", "ret_gross", "ret_ks", "ks_status"]
    best_ret_df[out_cols].to_csv(OUTPUT_DIR / "returns_daily_final.csv", index=False, encoding="utf-8")

    # ===== 8. Print report =====
    print("\n" + "=" * 80)
    print("011-4 BACKTEST WITH KILL SWITCH - FINAL RESULTS")
    print("=" * 80)

    print(f"\n--- Kill Switch Recommended Parameters ---")
    print(f"  Window: {best_params.get('window_weeks', 'N/A')} weeks")
    print(f"  Threshold: {best_params.get('threshold', 'N/A')}")

    print(f"\n--- Kill Switch Optimisation Grid ---")
    print(f"{'Label':<25} {'Sharpe_g':>10} {'Sharpe_n':>10} {'Sharpe_t':>10} {'MDD_net':>10} {'Flat%':>8}")
    for _, row in opt_df.iterrows():
        sg = row.get("Sharpe_gross", np.nan)
        sn = row.get("Sharpe_net", np.nan)
        st = row.get("Sharpe_tax", np.nan)
        mdd = row.get("MDD_net", np.nan)
        n = row.get("n_days", 0)
        flat = row.get("total_flat_days", 0)
        flat_pct = flat / n * 100 if n > 0 else 0
        print(f"  {row.get('label', ''):<23} {sg:>10.4f} {sn:>10.4f} {st:>10.4f} {mdd:>10.4f} {flat_pct:>7.1f}%")

    print(f"\n--- Final Full-Period Metrics (2018-2025) ---")
    print(f"{'Metric':<25} {'With KS':>15} {'Without KS':>15} {'Delta':>10}")
    for key in ["Sharpe_gross", "Sharpe_net", "Sharpe_tax", "AR_gross", "AR_net", "AR_tax", "MDD_gross", "MDD_net", "Vol", "win_rate"]:
        v_ks = final_metrics.get(key, np.nan)
        v_noks = final_no_ks.get(key, np.nan)
        if isinstance(v_ks, (int, float)) and isinstance(v_noks, (int, float)):
            delta = v_ks - v_noks
            print(f"  {key:<23} {v_ks:>15.4f} {v_noks:>15.4f} {delta:>+10.4f}")

    print(f"\n--- Year-by-Year Sharpe (Tax-Adjusted) ---")
    print(f"{'Year':<8} {'With KS':>10} {'Without KS':>12} {'Flat Days':>12}")
    for _, row in yearly_ks.iterrows():
        yr = int(row["year"])
        st_ks = row.get("Sharpe_tax", np.nan)
        noks_row = yearly_no_ks[yearly_no_ks["year"] == yr]
        st_noks = noks_row.iloc[0]["Sharpe_tax"] if len(noks_row) > 0 else np.nan
        flat = int(row.get("flat_days", 0))
        st_ks_s = f"{st_ks:.3f}" if not np.isnan(st_ks) else "N/A"
        st_noks_s = f"{st_noks:.3f}" if not np.isnan(st_noks) else "N/A"
        print(f"  {yr:<6} {st_ks_s:>10} {st_noks_s:>12} {flat:>12}")

    print(f"\n--- Kill Switch Timeline ---")
    if len(best_timeline) > 0:
        for _, row in best_timeline.iterrows():
            event_str = "ACTIVATED" if row["event"] == "KILL_ON" else "DEACTIVATED"
            print(f"  {row['date'].strftime('%Y-%m-%d')}: {event_str} (IC={row['ic_value']:.4f})")
    else:
        print("  No Kill Switch events during the period.")

    print("\n" + "=" * 80)

    ts1 = datetime.now(tz=JST)
    elapsed = (ts1 - ts0).total_seconds()
    log.info("Backtest complete", elapsed_s=f"{elapsed:.1f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

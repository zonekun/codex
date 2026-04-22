"""PoC: 011-4 Deep Dive — Monthly/Quarterly granularity + 2025-2026Q1 OOS extension.

Paper: SIG-FIN-036-13
Base: scripts/factor_model/poc_011_4_pass2.py (Pass 2)

Goals:
    - Monthly Sharpe time-series (2023-01 to 2026-03)
    - Quarterly Sharpe (2023-Q1 to 2026-Q1), 3-stage metrics
    - Year-by-year extended metrics (2018-2025 + 2026-Q1)
    - Rolling IC (12-week, weekly) time-series
    - Complete OOS through 2026-03-31

Outputs:
    C:\\tmp\\poc_011_4_deep_dive\\monthly_metrics.csv
    C:\\tmp\\poc_011_4_deep_dive\\quarterly_metrics.csv
    C:\\tmp\\poc_011_4_deep_dive\\yearly_metrics_extended.csv
    C:\\tmp\\poc_011_4_deep_dive\\rolling_ic.csv
    C:\\tmp\\poc_011_4_deep_dive\\returns_daily_extended.csv
"""
from __future__ import annotations

import sys
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account
from scipy.stats import spearmanr

warnings.filterwarnings("ignore", category=FutureWarning)

import structlog

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
JST = ZoneInfo("Asia/Tokyo")

PROJECT = "gmailpj-357912"
KEY_PATH = Path(r"C:\gdrive\claude\investment-agent\keys\gcp-service-account.json")

OUTPUT_DIR = Path(r"C:\tmp\poc_011_4_deep_dive")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

PASS2_CACHE = Path(r"C:\tmp\poc_011_4_pass2")

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

# Extended periods
WARMUP_START = pd.Timestamp("2016-01-01")
WARMUP_END = pd.Timestamp("2017-12-31")
OOS_START = pd.Timestamp("2018-01-01")
OOS_END_EXTENDED = pd.Timestamp("2026-03-31")

# Default params (Pass 1b best)
WINDOW_L = 60
K_EIG = 3
LAMBDA_REG = 0.9
Q_DEFAULT = 3

# Cost / tax
ONEWAY_COST_BPS = 1
BORROW_RATE_ANN = 0.0075  # 0.75% annualised
TAX_RATE = 0.20315

# XLC inception date (2018-06-18)
XLC_INCEPTION = pd.Timestamp("2018-06-18")

BQ_COST_LOG = OUTPUT_DIR / "bq_cost.log"


# ---------------------------------------------------------------------------
# Data fetching (extended to 2026-04-11)
# ---------------------------------------------------------------------------
def fetch_us_data_extended() -> pd.DataFrame:
    """Fetch US sector ETF daily prices: reuse Pass 2 cache + extend via yfinance."""
    cache_path = OUTPUT_DIR / "us_sector_prices_extended.csv"
    if cache_path.exists():
        log.info("US extended data from cache", path=str(cache_path))
        return pd.read_csv(cache_path, parse_dates=["Date"], encoding="utf-8")

    # Load Pass 2 cache (2016-01-01 to 2024-12-31)
    pass2_path = PASS2_CACHE / "us_sector_prices_pass2.csv"
    if not pass2_path.exists():
        raise FileNotFoundError(f"Pass 2 US cache not found: {pass2_path}")

    df_old = pd.read_csv(pass2_path, parse_dates=["Date"], encoding="utf-8")
    log.info("Loaded Pass 2 US cache", rows=len(df_old), max_date=str(df_old["Date"].max().date()))

    # Download extension (2025-01-01 to 2026-04-12)
    import yfinance as yf

    log.info("Downloading US sector ETFs extension from yfinance")
    raw = yf.download(US_TICKERS, start="2025-01-01", end="2026-04-12", auto_adjust=True)

    close = raw["Close"][US_TICKERS].copy()
    open_ = raw["Open"][US_TICKERS].copy()

    close_long = close.stack().reset_index()
    close_long.columns = ["Date", "Ticker", "Close"]
    open_long = open_.stack().reset_index()
    open_long.columns = ["Date", "Ticker", "Open"]

    df_new = pd.merge(close_long, open_long, on=["Date", "Ticker"], how="outer")
    df_new = df_new.sort_values(["Ticker", "Date"]).reset_index(drop=True)
    log.info("Downloaded US extension", rows=len(df_new), max_date=str(df_new["Date"].max().date()))

    # Combine
    df = pd.concat([df_old, df_new], ignore_index=True)
    df = df.drop_duplicates(subset=["Date", "Ticker"], keep="last")
    df = df.sort_values(["Ticker", "Date"]).reset_index(drop=True)

    df.to_csv(cache_path, index=False, encoding="utf-8")
    log.info("US extended data cached", path=str(cache_path), rows=len(df))
    return df


def fetch_jp_data_extended() -> pd.DataFrame:
    """Fetch Japan TOPIX-17 ETF: reuse Pass 2 cache + extend via BQ."""
    cache_path = OUTPUT_DIR / "jp_sector_prices_extended.parquet"
    if cache_path.exists():
        log.info("JP extended data from cache", path=str(cache_path))
        return pd.read_parquet(cache_path)

    # Load Pass 2 cache
    pass2_path = PASS2_CACHE / "jp_sector_prices_pass2.parquet"
    if not pass2_path.exists():
        raise FileNotFoundError(f"Pass 2 JP cache not found: {pass2_path}")

    df_old = pd.read_parquet(pass2_path)
    max_date_old = df_old["DATE"].max()
    log.info("Loaded Pass 2 JP cache", rows=len(df_old), max_date=str(max_date_old))

    # Query BQ for extension
    creds = service_account.Credentials.from_service_account_file(str(KEY_PATH))
    client = bigquery.Client(project=PROJECT, credentials=creds)

    tickers_str = ", ".join(f"'{t}'" for t in JP_TICKERS)
    query = f"""
    SELECT
        DATE,
        TICKER,
        ADJ_OPEN,
        ADJ_CLOSE,
        VOLUME
    FROM `{PROJECT}.STOCK.STOCK_PRICE_JQUANTS`
    WHERE TICKER IN ({tickers_str})
      AND DATE > '{max_date_old.strftime('%Y-%m-%d')}'
      AND DATE <= '2026-04-11'
      AND ADJ_OPEN IS NOT NULL
      AND ADJ_CLOSE IS NOT NULL
    ORDER BY TICKER, DATE
    """

    log.info("Querying BQ for JP extension", from_date=str(max_date_old))
    job = client.query(query)
    df_new = job.to_dataframe()

    # Log BQ cost
    total_bytes = job.total_bytes_processed or 0
    cost_usd = total_bytes / 1e12 * 6.25
    with open(BQ_COST_LOG, "w", encoding="utf-8") as f:
        f.write(f"BQ bytes: {total_bytes}, cost_usd: {cost_usd:.4f}\n")
    log.info("BQ extension done", rows=len(df_new), cost_usd=f"{cost_usd:.4f}")

    df_new["DATE"] = pd.to_datetime(df_new["DATE"])

    # Combine
    df = pd.concat([df_old, df_new], ignore_index=True)
    df = df.drop_duplicates(subset=["DATE", "TICKER"], keep="last")
    df = df.sort_values(["TICKER", "DATE"]).reset_index(drop=True)

    df.to_parquet(cache_path, index=False)
    log.info("JP extended data cached", rows=len(df))
    return df


# ---------------------------------------------------------------------------
# Return construction (same as Pass 2)
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

    pairs = []
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
# V0 / C0 construction (same as Pass 2)
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
# Regularised PCA predictor (same as Pass 2)
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
# Long-Short weight construction (same as Pass 2)
# ---------------------------------------------------------------------------
def build_ls_weights(signal: np.ndarray, q: int) -> np.ndarray:
    """Build long-short equal-weight portfolio. Long top-q, Short bottom-q."""
    ranking = np.argsort(-signal)  # descending
    weights = np.zeros(len(signal))
    weights[ranking[:q]] = 1.0 / q
    weights[ranking[-q:]] = -1.0 / q
    return weights


# ---------------------------------------------------------------------------
# Core backtest engine (extended: also returns signal for IC)
# ---------------------------------------------------------------------------
def run_backtest_extended(
    us_vals: np.ndarray,
    jp_vals: np.ndarray,
    date_info: pd.DataFrame,
    V0: np.ndarray,
    C0: np.ndarray,
    warmup_end: pd.Timestamp,
    oos_start: pd.Timestamp,
    oos_end: pd.Timestamp,
    window_L: int,
    K: int,
    lam: float,
    q: int,
) -> pd.DataFrame:
    """Run rolling long-short backtest. Returns JP_DATE, ret, + per-sector signals."""
    jp_dates = date_info["JP_DATE"].values
    oos_mask = (jp_dates >= np.datetime64(oos_start)) & (jp_dates <= np.datetime64(oos_end))
    oos_indices = np.where(oos_mask)[0]

    records = []
    for t_idx in oos_indices:
        if t_idx < window_L:
            continue

        jp_date = pd.Timestamp(jp_dates[t_idx])

        # Window for rolling correlation
        window = slice(t_idx - window_L, t_idx)
        us_win = us_vals[window]
        jp_win = jp_vals[window]

        Z_win = np.concatenate([us_win, jp_win], axis=1)
        Z_win = Z_win - Z_win.mean(axis=0, keepdims=True)
        std_win = Z_win.std(axis=0, keepdims=True)
        std_win[std_win == 0] = 1.0
        Z_win = Z_win / std_win
        C_win = (Z_win.T @ Z_win) / max(Z_win.shape[0] - 1, 1)

        B_reg = compute_predictor(C_win, C0, K, lam)

        # Standardise today's US c2c return
        us_today = us_vals[t_idx]
        us_mu = us_vals[window].mean(axis=0)
        us_sig = us_vals[window].std(axis=0)
        us_sig[us_sig == 0] = 1.0
        z_us = (us_today - us_mu) / us_sig

        z_pred = B_reg @ z_us
        w = build_ls_weights(z_pred, q)
        ret = np.dot(w, jp_vals[t_idx])

        rec = {"JP_DATE": jp_date, "ret": ret}
        # Store signal and realized return per JP sector for IC
        for j, ticker in enumerate(JP_TICKERS):
            rec[f"sig_{ticker}"] = z_pred[j]
            rec[f"real_{ticker}"] = jp_vals[t_idx, j]
        records.append(rec)

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Metrics computation (3-stage, same as Pass 2)
# ---------------------------------------------------------------------------
def compute_metrics_series(returns: np.ndarray, label: str = "") -> dict:
    """Compute 3-stage metrics for a return series."""
    s = pd.Series(returns).dropna()
    n_days = len(s)
    if n_days == 0:
        return {"period": label, "n_days": 0, "Sharpe_gross": np.nan,
                "Sharpe_net": np.nan, "Sharpe_tax": np.nan,
                "AR_gross": np.nan, "AR_net": np.nan, "AR_tax": np.nan,
                "MDD_gross": np.nan, "MDD_net": np.nan,
                "Vol": np.nan, "win_rate": np.nan}

    # Gross
    mu_gross = s.mean() * 252
    vol = s.std(ddof=1) * np.sqrt(252)
    sharpe_gross = mu_gross / vol if vol > 0 else np.nan
    cum_gross = (1 + s).cumprod()
    mdd_gross = float(((cum_gross / cum_gross.cummax()) - 1).min())
    win_rate = float((s > 0).sum() / n_days)

    # After cost + borrow
    daily_cost = 2 * ONEWAY_COST_BPS / 10_000
    daily_borrow = BORROW_RATE_ANN / 252
    s_net = s - daily_cost - daily_borrow
    mu_net = s_net.mean() * 252
    sharpe_net = mu_net / vol if vol > 0 else np.nan
    cum_net = (1 + s_net).cumprod()
    mdd_net = float(((cum_net / cum_net.cummax()) - 1).min())

    # After tax
    mu_tax = mu_net * (1 - TAX_RATE) if mu_net > 0 else mu_net
    sharpe_tax = mu_tax / vol if vol > 0 else np.nan

    return {
        "period": label,
        "n_days": n_days,
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
# Monthly metrics
# ---------------------------------------------------------------------------
def compute_monthly_metrics(ret_df: pd.DataFrame) -> pd.DataFrame:
    """Compute monthly metrics (2023-01 to 2026-03)."""
    df = ret_df.copy()
    df["JP_DATE"] = pd.to_datetime(df["JP_DATE"])
    df = df[df["JP_DATE"] >= "2023-01-01"]
    df["ym"] = df["JP_DATE"].dt.to_period("M")

    rows = []
    for ym in sorted(df["ym"].unique()):
        subset = df[df["ym"] == ym]
        m = compute_metrics_series(subset["ret"].values, label=str(ym))
        rows.append(m)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Quarterly metrics
# ---------------------------------------------------------------------------
def compute_quarterly_metrics(ret_df: pd.DataFrame) -> pd.DataFrame:
    """Compute quarterly metrics (2023-Q1 to 2026-Q1)."""
    df = ret_df.copy()
    df["JP_DATE"] = pd.to_datetime(df["JP_DATE"])
    df = df[df["JP_DATE"] >= "2023-01-01"]
    df["yq"] = df["JP_DATE"].dt.to_period("Q")

    rows = []
    for yq in sorted(df["yq"].unique()):
        subset = df[df["yq"] == yq]
        m = compute_metrics_series(subset["ret"].values, label=str(yq))
        rows.append(m)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Yearly metrics (extended)
# ---------------------------------------------------------------------------
def compute_yearly_metrics(ret_df: pd.DataFrame) -> pd.DataFrame:
    """Compute year-by-year metrics for full extended period."""
    df = ret_df.copy()
    df["JP_DATE"] = pd.to_datetime(df["JP_DATE"])
    df["year"] = df["JP_DATE"].dt.year

    rows = []
    for year in sorted(df["year"].unique()):
        subset = df[df["year"] == year]
        m = compute_metrics_series(subset["ret"].values, label=str(year))
        rows.append(m)

    # Also add 2026-Q1 as a separate row
    q1_2026 = df[(df["JP_DATE"] >= "2026-01-01") & (df["JP_DATE"] <= "2026-03-31")]
    if len(q1_2026) > 0:
        m = compute_metrics_series(q1_2026["ret"].values, label="2026-Q1")
        rows.append(m)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Rolling IC (12-week, weekly)
# ---------------------------------------------------------------------------
def compute_rolling_ic(ret_df: pd.DataFrame) -> pd.DataFrame:
    """Compute weekly rolling IC (12-week window) from 2023-01 to 2026-03.

    IC = Spearman correlation between signal and realized OC return across 17 JP sectors.
    Rolling IC = 12-week moving average of weekly IC.
    """
    df = ret_df.copy()
    df["JP_DATE"] = pd.to_datetime(df["JP_DATE"])
    df = df[df["JP_DATE"] >= "2023-01-01"].copy()
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
    ic_df["year_week"] = ic_df["JP_DATE"].dt.isocalendar().year.astype(str) + "-W" + ic_df["JP_DATE"].dt.isocalendar().week.astype(str).str.zfill(2)
    ic_df["week_end"] = ic_df["JP_DATE"]  # use last date in week as label

    weekly = ic_df.groupby("year_week").agg(
        week_date=("JP_DATE", "last"),
        ic_weekly=("ic_daily", "mean"),
        n_days=("ic_daily", "count"),
    ).reset_index()
    weekly = weekly.sort_values("week_date").reset_index(drop=True)

    # 12-week rolling average
    weekly["ic_rolling_12w"] = weekly["ic_weekly"].rolling(12, min_periods=6).mean()

    return weekly[["week_date", "year_week", "ic_weekly", "ic_rolling_12w", "n_days"]]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ts0 = datetime.now(tz=JST)
    log.info("011-4 Deep Dive start", ts=ts0.isoformat())

    # ===== 1. Fetch extended data =====
    df_us = fetch_us_data_extended()
    df_jp = fetch_jp_data_extended()

    log.info("Data loaded", us_rows=len(df_us), jp_rows=len(df_jp))

    # 2. Build returns
    us_c2c = build_us_returns(df_us)
    jp_oc, jp_c2c = build_jp_returns(df_jp)

    log.info("Returns built", us_c2c_shape=us_c2c.shape, jp_oc_shape=jp_oc.shape)

    # 3. Align common dates
    us_aligned, jp_aligned, date_info = align_common_dates(us_c2c, jp_oc)

    # Fill NaN with 0 for computation
    us_vals = np.nan_to_num(us_aligned.values, nan=0.0)
    jp_vals = np.nan_to_num(jp_aligned.values, nan=0.0)

    # 4. Build V0
    V0 = build_V0()

    # 5. Compute C0 from warm-up period (2016-2017)
    jp_dates = date_info["JP_DATE"].values
    warmup_mask = (jp_dates >= np.datetime64(WARMUP_START)) & (jp_dates <= np.datetime64(WARMUP_END))
    warmup_count = int(warmup_mask.sum())
    log.info("Warm-up period", start=str(WARMUP_START.date()), end=str(WARMUP_END.date()), days=warmup_count)

    if warmup_count < 100:
        log.error("Insufficient warm-up data", days=warmup_count)
        return 1

    us_warm = us_vals[warmup_mask]
    jp_warm = jp_vals[warmup_mask]
    C0 = compute_C0(us_warm, jp_warm, V0)

    # ===== 6. Extended backtest (2018-01 to 2026-03) =====
    log.info("Running extended backtest")
    ret_ext = run_backtest_extended(
        us_vals, jp_vals, date_info, V0, C0,
        WARMUP_END, OOS_START, OOS_END_EXTENDED,
        WINDOW_L, K_EIG, LAMBDA_REG, Q_DEFAULT,
    )
    log.info("Extended backtest done", n_days=len(ret_ext))

    # ===== 7. Save daily returns =====
    ret_ext.to_csv(OUTPUT_DIR / "returns_daily_extended.csv", index=False, encoding="utf-8")

    # ===== 8. Monthly metrics =====
    monthly_df = compute_monthly_metrics(ret_ext)
    monthly_df.to_csv(OUTPUT_DIR / "monthly_metrics.csv", index=False, encoding="utf-8")
    log.info("Monthly metrics computed", months=len(monthly_df))

    # ===== 9. Quarterly metrics =====
    quarterly_df = compute_quarterly_metrics(ret_ext)
    quarterly_df.to_csv(OUTPUT_DIR / "quarterly_metrics.csv", index=False, encoding="utf-8")
    log.info("Quarterly metrics computed", quarters=len(quarterly_df))

    # ===== 10. Yearly metrics (extended) =====
    yearly_df = compute_yearly_metrics(ret_ext)
    yearly_df.to_csv(OUTPUT_DIR / "yearly_metrics_extended.csv", index=False, encoding="utf-8")
    log.info("Yearly metrics computed", years=len(yearly_df))

    # ===== 11. Rolling IC =====
    rolling_ic_df = compute_rolling_ic(ret_ext)
    rolling_ic_df.to_csv(OUTPUT_DIR / "rolling_ic.csv", index=False, encoding="utf-8")
    log.info("Rolling IC computed", weeks=len(rolling_ic_df))

    # ===== 12. Print report =====
    print("\n" + "=" * 70)
    print("011-4 DEEP DIVE RESULTS")
    print("=" * 70)

    print("\n--- Quarterly Sharpe (2023-Q1 to 2026-Q1) ---")
    print(f"{'Quarter':<12} {'Sharpe_g':>10} {'Sharpe_n':>10} {'Sharpe_t':>10} {'AR_gross':>10} {'MDD_net':>10} {'N':>5}")
    for _, row in quarterly_df.iterrows():
        sg = row.get("Sharpe_gross", np.nan)
        sn = row.get("Sharpe_net", np.nan)
        st = row.get("Sharpe_tax", np.nan)
        ar = row.get("AR_gross", np.nan)
        mdd = row.get("MDD_net", np.nan)
        n = int(row.get("n_days", 0))
        sg_s = f"{sg:.3f}" if not np.isnan(sg) else "N/A"
        sn_s = f"{sn:.3f}" if not np.isnan(sn) else "N/A"
        st_s = f"{st:.3f}" if not np.isnan(st) else "N/A"
        ar_s = f"{ar:.3f}" if not np.isnan(ar) else "N/A"
        mdd_s = f"{mdd:.3f}" if not np.isnan(mdd) else "N/A"
        print(f"{row['period']:<12} {sg_s:>10} {sn_s:>10} {st_s:>10} {ar_s:>10} {mdd_s:>10} {n:>5}")

    print("\n--- Year-by-Year Metrics (Extended) ---")
    print(f"{'Year':<12} {'Sharpe_g':>10} {'Sharpe_n':>10} {'Sharpe_t':>10} {'AR_tax':>10} {'MDD_net':>10} {'N':>5}")
    for _, row in yearly_df.iterrows():
        sg = row.get("Sharpe_gross", np.nan)
        sn = row.get("Sharpe_net", np.nan)
        st = row.get("Sharpe_tax", np.nan)
        ar_t = row.get("AR_tax", np.nan)
        mdd = row.get("MDD_net", np.nan)
        n = int(row.get("n_days", 0))
        sg_s = f"{sg:.3f}" if not np.isnan(sg) else "N/A"
        sn_s = f"{sn:.3f}" if not np.isnan(sn) else "N/A"
        st_s = f"{st:.3f}" if not np.isnan(st) else "N/A"
        ar_s = f"{ar_t:.3f}" if not np.isnan(ar_t) else "N/A"
        mdd_s = f"{mdd:.3f}" if not np.isnan(mdd) else "N/A"
        print(f"{row['period']:<12} {sg_s:>10} {sn_s:>10} {st_s:>10} {ar_s:>10} {mdd_s:>10} {n:>5}")

    print("\n--- Monthly Sharpe (gross, 2023-01 to 2026-03) ---")
    for _, row in monthly_df.iterrows():
        sg = row.get("Sharpe_gross", np.nan)
        wr = row.get("win_rate", np.nan)
        n = int(row.get("n_days", 0))
        sg_s = f"{sg:+.2f}" if not np.isnan(sg) else "N/A"
        wr_s = f"{wr:.0%}" if not np.isnan(wr) else "N/A"
        print(f"  {row['period']}: Sharpe_g={sg_s}  WinRate={wr_s}  N={n}")

    print("\n--- Rolling IC Summary ---")
    if len(rolling_ic_df) > 0:
        ic_2024 = rolling_ic_df[rolling_ic_df["week_date"].dt.year == 2024]["ic_rolling_12w"]
        ic_2025 = rolling_ic_df[rolling_ic_df["week_date"].dt.year == 2025]["ic_rolling_12w"]
        ic_2026q1 = rolling_ic_df[
            (rolling_ic_df["week_date"].dt.year == 2026) &
            (rolling_ic_df["week_date"].dt.month <= 3)
        ]["ic_rolling_12w"]

        print(f"  2024 avg IC(12w): {ic_2024.mean():.4f} (min={ic_2024.min():.4f}, max={ic_2024.max():.4f})")
        if len(ic_2025) > 0:
            print(f"  2025 avg IC(12w): {ic_2025.mean():.4f} (min={ic_2025.min():.4f}, max={ic_2025.max():.4f})")
        if len(ic_2026q1) > 0:
            print(f"  2026-Q1 avg IC(12w): {ic_2026q1.mean():.4f} (min={ic_2026q1.min():.4f}, max={ic_2026q1.max():.4f})")

        # When did IC first cross zero?
        negative_weeks = rolling_ic_df[rolling_ic_df["ic_rolling_12w"] < 0]
        if len(negative_weeks) > 0:
            first_neg = negative_weeks.iloc[0]
            print(f"  IC first < 0: {first_neg['week_date'].strftime('%Y-%m-%d')} (IC={first_neg['ic_rolling_12w']:.4f})")

    # BQ cost
    if BQ_COST_LOG.exists():
        print(f"\nBQ cost: {BQ_COST_LOG.read_text(encoding='utf-8').strip()}")

    ts1 = datetime.now(tz=JST)
    elapsed = (ts1 - ts0).total_seconds()
    log.info("Done", elapsed_s=f"{elapsed:.1f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

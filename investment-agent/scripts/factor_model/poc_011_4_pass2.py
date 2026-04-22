"""PoC: 011-4 Pass 2 — US→JP sector lead-lag, period extension + parameter sensitivity.

Paper: SIG-FIN-036-13
Knowledge: docs/knowledges/analysis/011-4_us_japan_sector_leadlag.md
Base: scripts/factor_model/poc_011_4b_us_japan_longshort.py (Pass 1b)

Pass 2 goals:
    - Extend OOS period to 2018-2024 (7 years, warm-up 2016-2017)
    - Parameter sensitivity: lambda, K, L (one-at-a-time)
    - Year-by-year Sharpe stability
    - Single-factor alpha (TOPIX excess return)
    - Final PASS/FAIL determination

Outputs:
    C:\\tmp\\poc_011_4_pass2\\metrics_full_period.csv
    C:\\tmp\\poc_011_4_pass2\\metrics_by_year.csv
    C:\\tmp\\poc_011_4_pass2\\sensitivity_lambda.csv
    C:\\tmp\\poc_011_4_pass2\\sensitivity_K.csv
    C:\\tmp\\poc_011_4_pass2\\sensitivity_L.csv
    C:\\tmp\\poc_011_4_pass2\\returns_daily.csv
    C:\\tmp\\poc_011_4_pass2\\factor_alpha.csv
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

warnings.filterwarnings("ignore", category=FutureWarning)

import structlog

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
JST = ZoneInfo("Asia/Tokyo")

PROJECT = "gmailpj-357912"
KEY_PATH = Path(r"C:\gdrive\claude\investment-agent\keys\gcp-service-account.json")

OUTPUT_DIR = Path(r"C:\tmp\poc_011_4_pass2")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CACHE_DIR = OUTPUT_DIR  # cache data here

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

# Pass 2 periods
WARMUP_START = pd.Timestamp("2016-01-01")
WARMUP_END = pd.Timestamp("2017-12-31")
OOS_START = pd.Timestamp("2018-01-01")
OOS_END = pd.Timestamp("2024-12-31")

# Default params (Pass 1b best)
WINDOW_L = 60
K_EIG = 3
LAMBDA_REG = 0.9
Q_DEFAULT = 3

# Cost / tax
ONEWAY_COST_BPS = 1
BORROW_RATE_ANN = 0.0075  # 0.75% annualised
TAX_RATE = 0.20315

# Sensitivity test ranges
LAMBDA_RANGE = [0.5, 0.7, 0.8, 0.9, 0.95]
K_RANGE = [1, 2, 3, 5]
L_RANGE = [30, 60, 120]

# XLC inception date (2018-06-18)
XLC_INCEPTION = pd.Timestamp("2018-06-18")

BQ_COST_LOG = OUTPUT_DIR / "bq_cost.log"


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------
def fetch_us_data() -> pd.DataFrame:
    """Fetch US sector ETF daily prices from yfinance (2016-01-01 to 2025-01-01)."""
    cache_path = CACHE_DIR / "us_sector_prices_pass2.csv"
    if cache_path.exists():
        log.info("US data from cache", path=str(cache_path))
        return pd.read_csv(cache_path, parse_dates=["Date"], encoding="utf-8")

    import yfinance as yf

    log.info("Downloading US sector ETFs from yfinance", tickers=US_TICKERS)
    raw = yf.download(US_TICKERS, start="2016-01-01", end="2025-01-01", auto_adjust=True)

    close = raw["Close"][US_TICKERS].copy()
    open_ = raw["Open"][US_TICKERS].copy()

    close_long = close.stack().reset_index()
    close_long.columns = ["Date", "Ticker", "Close"]
    open_long = open_.stack().reset_index()
    open_long.columns = ["Date", "Ticker", "Open"]

    df = pd.merge(close_long, open_long, on=["Date", "Ticker"], how="outer")
    df = df.sort_values(["Ticker", "Date"]).reset_index(drop=True)

    df.to_csv(cache_path, index=False, encoding="utf-8")
    log.info("US data cached", path=str(cache_path), rows=len(df))
    return df


def fetch_jp_data() -> pd.DataFrame:
    """Fetch Japan TOPIX-17 ETF daily prices from BQ (2016-01-01 to 2025-01-01)."""
    cache_path = CACHE_DIR / "jp_sector_prices_pass2.parquet"
    if cache_path.exists():
        log.info("JP data from cache", path=str(cache_path))
        return pd.read_parquet(cache_path)

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
      AND DATE >= '2016-01-01'
      AND DATE <= '2024-12-31'
      AND ADJ_OPEN IS NOT NULL
      AND ADJ_CLOSE IS NOT NULL
    ORDER BY TICKER, DATE
    """

    log.info("Querying BQ for JP sector ETFs")
    job = client.query(query)
    df = job.to_dataframe()

    # Log BQ cost
    total_bytes = job.total_bytes_processed or 0
    cost_usd = total_bytes / 1e12 * 6.25
    with open(BQ_COST_LOG, "w", encoding="utf-8") as f:
        f.write(f"BQ bytes: {total_bytes}, cost_usd: {cost_usd:.4f}\n")
    log.info("BQ query done", rows=len(df), bytes=total_bytes, cost_usd=f"{cost_usd:.4f}")

    # Convert DATE to datetime64 (BQ returns dbdate which parquet can't handle)
    df["DATE"] = pd.to_datetime(df["DATE"])

    df.to_parquet(cache_path, index=False)
    log.info("JP data cached", path=str(cache_path), rows=len(df))
    return df


def fetch_topix_data() -> pd.Series:
    """Fetch TOPIX ETF (1306.T) close-to-close returns for single-factor alpha."""
    cache_path = CACHE_DIR / "topix_prices_pass2.csv"
    if cache_path.exists():
        df = pd.read_csv(cache_path, parse_dates=["Date"], encoding="utf-8")
        return df.set_index("Date")["Close"]

    import yfinance as yf

    log.info("Downloading TOPIX ETF (1306.T) from yfinance")
    raw = yf.download("1306.T", start="2016-01-01", end="2025-01-01", auto_adjust=True)
    close = raw["Close"].squeeze()
    df = pd.DataFrame({"Date": close.index, "Close": close.values})
    df.to_csv(cache_path, index=False, encoding="utf-8")
    return df.set_index("Date")["Close"]


# ---------------------------------------------------------------------------
# Return construction
# ---------------------------------------------------------------------------
def build_us_returns(df_us: pd.DataFrame) -> pd.DataFrame:
    """Build US sector close-to-close log returns (wide: date x ticker)."""
    df = df_us.copy()
    df = df.sort_values(["Ticker", "Date"]).drop_duplicates(["Ticker", "Date"], keep="last")
    pivot_close = df.pivot(index="Date", columns="Ticker", values="Close").sort_index()
    pivot_close = pivot_close.ffill(limit=3)

    # Handle XLC: only available from 2018-06-18
    if "XLC" in pivot_close.columns:
        xlc_mask = pivot_close.index < XLC_INCEPTION
        pivot_close.loc[xlc_mask, "XLC"] = np.nan

    log_ret = np.log(pivot_close / pivot_close.shift(1))
    # Keep all 11 columns; NaN for XLC before inception is handled downstream
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
    """Align US day t with JP day t+1 on common business day pairs.

    When multiple US dates map to the same JP date (e.g. US Fri + JP closed Mon
    -> both map to JP Tue), keep only the latest US date (closest to JP open).
    """
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

    # Deduplicate: keep only the latest US date for each JP date
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
# V0 construction
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


# ---------------------------------------------------------------------------
# C0 construction
# ---------------------------------------------------------------------------
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
# Core backtest engine (parameterised)
# ---------------------------------------------------------------------------
def run_backtest_core(
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
    """Run rolling long-short backtest with given parameters.

    Returns DataFrame with columns: JP_DATE, strategy_return.
    """
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

        records.append({"JP_DATE": jp_date, "ret": ret})

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Metrics computation (3-stage)
# ---------------------------------------------------------------------------
def compute_metrics_series(
    returns: np.ndarray,
    label: str = "",
) -> dict:
    """Compute 3-stage metrics for a return series."""
    s = pd.Series(returns).dropna()
    n_days = len(s)
    if n_days == 0:
        return {"strategy": label, "n_days": 0}

    # Gross
    mu_gross = s.mean() * 252
    vol = s.std(ddof=1) * np.sqrt(252)
    sharpe_gross = mu_gross / vol if vol > 0 else np.nan
    cum_gross = (1 + s).cumprod()
    mdd_gross = float(((cum_gross / cum_gross.cummax()) - 1).min())

    # After cost + borrow
    daily_cost = 2 * ONEWAY_COST_BPS / 10_000  # round-trip
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
        "strategy": label,
        "n_days": n_days,
        "AR_gross": mu_gross,
        "Vol": vol,
        "Sharpe_gross": sharpe_gross,
        "MDD_gross": mdd_gross,
        "AR_net": mu_net,
        "Sharpe_net": sharpe_net,
        "MDD_net": mdd_net,
        "AR_tax": mu_tax,
        "Sharpe_tax": sharpe_tax,
    }


# ---------------------------------------------------------------------------
# Year-by-year metrics
# ---------------------------------------------------------------------------
def compute_yearly_metrics(ret_df: pd.DataFrame) -> pd.DataFrame:
    """Compute metrics for each year in the OOS period."""
    ret_df = ret_df.copy()
    ret_df["year"] = ret_df["JP_DATE"].dt.year

    rows = []
    for year in sorted(ret_df["year"].unique()):
        subset = ret_df[ret_df["year"] == year]
        m = compute_metrics_series(subset["ret"].values, label=f"year_{year}")
        m["year"] = year
        rows.append(m)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Single-factor alpha (TOPIX)
# ---------------------------------------------------------------------------
def compute_single_factor_alpha(
    ret_df: pd.DataFrame,
    topix_close: pd.Series,
) -> pd.DataFrame:
    """Regress strategy returns on TOPIX returns. Report alpha + Newey-West t."""
    # Align dates
    topix_ret = np.log(topix_close / topix_close.shift(1)).dropna()

    merged = ret_df.copy()
    merged["JP_DATE"] = pd.to_datetime(merged["JP_DATE"])
    merged = merged.set_index("JP_DATE")

    # Match TOPIX returns to strategy dates
    topix_ret.index = pd.to_datetime(topix_ret.index)
    # Normalize both to date-only for matching
    merged.index = merged.index.normalize()
    topix_ret.index = topix_ret.index.normalize()
    common = merged.index.intersection(topix_ret.index)
    if len(common) < 30:
        log.warning("Too few common dates for alpha regression", n=len(common))
        return pd.DataFrame()

    y = merged.loc[common, "ret"].values
    x = topix_ret.loc[common].values
    X = np.column_stack([np.ones(len(x)), x])

    # OLS
    beta_hat = np.linalg.lstsq(X, y, rcond=None)[0]
    resid = y - X @ beta_hat
    n = len(y)

    # Newey-West standard errors (lag = int(n^(1/3)))
    nw_lag = max(1, int(n ** (1 / 3)))
    k = X.shape[1]
    # Meat matrix: S = (1/n) * sum of Bartlett-weighted outer products
    S = np.zeros((k, k))
    for lag in range(nw_lag + 1):
        bartlett = 1.0 - lag / (nw_lag + 1)
        Gamma = np.zeros((k, k))
        for t in range(lag, n):
            Gamma += np.outer(X[t] * resid[t], X[t - lag] * resid[t - lag])
        Gamma /= n
        if lag == 0:
            S += Gamma
        else:
            S += bartlett * (Gamma + Gamma.T)

    XtX_inv = np.linalg.inv(X.T @ X / n)
    V_nw = (XtX_inv @ S @ XtX_inv) / n
    se_nw = np.sqrt(np.diag(V_nw))

    alpha_ann = beta_hat[0] * 252
    # t-stat is scale-invariant (same for daily or annualised)
    t_stat = beta_hat[0] / se_nw[0] if se_nw[0] > 0 else np.nan
    alpha_se_ann = alpha_ann / t_stat if t_stat != 0 else np.nan

    result = pd.DataFrame([{
        "alpha_daily": beta_hat[0],
        "alpha_ann": alpha_ann,
        "alpha_se_ann": alpha_se_ann,
        "alpha_t_nw": t_stat,
        "beta_topix": beta_hat[1],
        "n_obs": n,
        "nw_lag": nw_lag,
    }])

    log.info(
        "Single-factor alpha",
        alpha_ann=f"{alpha_ann:.4f}",
        t_stat=f"{t_stat:.2f}",
        beta=f"{beta_hat[1]:.4f}",
    )

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ts0 = datetime.now(tz=JST)
    log.info("011-4 Pass 2 start", ts=ts0.isoformat())

    # ===== 1. Fetch data =====
    df_us = fetch_us_data()
    df_jp = fetch_jp_data()

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

    # ===== 6. Main backtest (default params: L=60, K=3, lambda=0.9, q=3) =====
    log.info("Running main backtest (default params)")
    ret_main = run_backtest_core(
        us_vals, jp_vals, date_info, V0, C0,
        WARMUP_END, OOS_START, OOS_END,
        WINDOW_L, K_EIG, LAMBDA_REG, Q_DEFAULT,
    )
    log.info("Main backtest done", n_days=len(ret_main))

    # ===== 7. Full-period metrics =====
    metrics_full = compute_metrics_series(ret_main["ret"].values, label="REG_PCA_LS_q3_default")
    metrics_full_df = pd.DataFrame([metrics_full])
    metrics_full_df.to_csv(OUTPUT_DIR / "metrics_full_period.csv", index=False, encoding="utf-8")
    log.info("Full-period metrics", sharpe_tax=f"{metrics_full.get('Sharpe_tax', 'N/A')}")

    # ===== 8. Year-by-year metrics =====
    yearly_df = compute_yearly_metrics(ret_main)
    yearly_df.to_csv(OUTPUT_DIR / "metrics_by_year.csv", index=False, encoding="utf-8")
    log.info("Yearly metrics computed", years=len(yearly_df))

    # ===== 9. Sensitivity: lambda =====
    log.info("Running sensitivity: lambda")
    sens_lambda_rows = []
    for lam in LAMBDA_RANGE:
        ret = run_backtest_core(
            us_vals, jp_vals, date_info, V0, C0,
            WARMUP_END, OOS_START, OOS_END,
            WINDOW_L, K_EIG, lam, Q_DEFAULT,
        )
        m = compute_metrics_series(ret["ret"].values, label=f"lambda={lam}")
        m["lambda"] = lam
        sens_lambda_rows.append(m)
        log.info(f"  lambda={lam}: Sharpe_tax={m.get('Sharpe_tax', 'N/A')}")

    sens_lambda_df = pd.DataFrame(sens_lambda_rows)
    sens_lambda_df.to_csv(OUTPUT_DIR / "sensitivity_lambda.csv", index=False, encoding="utf-8")

    # ===== 10. Sensitivity: K =====
    log.info("Running sensitivity: K")
    sens_K_rows = []
    for k in K_RANGE:
        ret = run_backtest_core(
            us_vals, jp_vals, date_info, V0, C0,
            WARMUP_END, OOS_START, OOS_END,
            WINDOW_L, k, LAMBDA_REG, Q_DEFAULT,
        )
        m = compute_metrics_series(ret["ret"].values, label=f"K={k}")
        m["K"] = k
        sens_K_rows.append(m)
        log.info(f"  K={k}: Sharpe_tax={m.get('Sharpe_tax', 'N/A')}")

    sens_K_df = pd.DataFrame(sens_K_rows)
    sens_K_df.to_csv(OUTPUT_DIR / "sensitivity_K.csv", index=False, encoding="utf-8")

    # ===== 11. Sensitivity: L =====
    log.info("Running sensitivity: L")
    sens_L_rows = []
    for L in L_RANGE:
        ret = run_backtest_core(
            us_vals, jp_vals, date_info, V0, C0,
            WARMUP_END, OOS_START, OOS_END,
            L, K_EIG, LAMBDA_REG, Q_DEFAULT,
        )
        m = compute_metrics_series(ret["ret"].values, label=f"L={L}")
        m["L"] = L
        sens_L_rows.append(m)
        log.info(f"  L={L}: Sharpe_tax={m.get('Sharpe_tax', 'N/A')}")

    sens_L_df = pd.DataFrame(sens_L_rows)
    sens_L_df.to_csv(OUTPUT_DIR / "sensitivity_L.csv", index=False, encoding="utf-8")

    # ===== 12. Save daily returns =====
    ret_main.to_csv(OUTPUT_DIR / "returns_daily.csv", index=False, encoding="utf-8")

    # ===== 13. Single-factor alpha =====
    try:
        topix_close = fetch_topix_data()
        alpha_df = compute_single_factor_alpha(ret_main, topix_close)
        if len(alpha_df) > 0:
            alpha_df.to_csv(OUTPUT_DIR / "factor_alpha.csv", index=False, encoding="utf-8")
    except Exception as e:
        log.warning("Factor alpha computation failed", error=str(e))
        alpha_df = pd.DataFrame()

    # ===== 14. Final judgment =====
    sharpe_tax_full = metrics_full.get("Sharpe_tax", float("nan"))

    # Condition 1: Full-period tax-adjusted Sharpe >= 1.0
    cond1 = sharpe_tax_full >= 1.0 if not np.isnan(sharpe_tax_full) else False

    # Condition 2: 5+ of 7 years with gross Sharpe > 0
    yearly_gross_positive = (yearly_df["Sharpe_gross"] > 0).sum()
    total_years = len(yearly_df)
    cond2 = yearly_gross_positive >= 5

    # Condition 3: Parameter robustness — adjacent params keep tax Sharpe >= 0.7
    def check_robustness(sens_df: pd.DataFrame, param_col: str, default_val: float) -> bool:
        """Check if adjacent parameter values maintain Sharpe_tax >= 0.7."""
        if param_col not in sens_df.columns:
            return False
        sorted_vals = sorted(sens_df[param_col].unique())
        if default_val not in sorted_vals:
            return True  # If default not in list, can't check adjacency
        idx = sorted_vals.index(default_val)
        adjacent_indices = [i for i in [idx - 1, idx, idx + 1] if 0 <= i < len(sorted_vals)]
        for ai in adjacent_indices:
            val = sorted_vals[ai]
            row = sens_df[sens_df[param_col] == val]
            if len(row) > 0:
                st = row.iloc[0].get("Sharpe_tax", float("nan"))
                if np.isnan(st) or st < 0.7:
                    return False
        return True

    cond3_lambda = check_robustness(sens_lambda_df, "lambda", LAMBDA_REG)
    cond3_K = check_robustness(sens_K_df, "K", float(K_EIG))
    cond3_L = check_robustness(sens_L_df, "L", float(WINDOW_L))
    cond3 = cond3_lambda and cond3_K and cond3_L

    all_pass = cond1 and cond2 and cond3
    verdict = "PASS" if all_pass else "FAIL"

    # Print report
    print("\n" + "=" * 70)
    print("011-4 Pass 2 RESULTS")
    print("=" * 70)

    print(f"\n--- Full Period Metrics (OOS {OOS_START.date()} - {OOS_END.date()}) ---")
    for k, v in metrics_full.items():
        if k == "strategy":
            continue
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}")
        else:
            print(f"  {k}: {v}")

    print(f"\n--- Year-by-Year Sharpe ---")
    for _, row in yearly_df.iterrows():
        yr = int(row.get("year", 0))
        sg = row.get("Sharpe_gross", float("nan"))
        sn = row.get("Sharpe_net", float("nan"))
        st = row.get("Sharpe_tax", float("nan"))
        print(f"  {yr}: gross={sg:.3f}  net={sn:.3f}  tax={st:.3f}")

    print(f"\n--- Sensitivity: lambda ---")
    for _, row in sens_lambda_df.iterrows():
        print(f"  lambda={row.get('lambda', '')}: Sharpe_tax={row.get('Sharpe_tax', 'N/A'):.4f}")

    print(f"\n--- Sensitivity: K ---")
    for _, row in sens_K_df.iterrows():
        print(f"  K={row.get('K', '')}: Sharpe_tax={row.get('Sharpe_tax', 'N/A'):.4f}")

    print(f"\n--- Sensitivity: L ---")
    for _, row in sens_L_df.iterrows():
        print(f"  L={row.get('L', '')}: Sharpe_tax={row.get('Sharpe_tax', 'N/A'):.4f}")

    if len(alpha_df) > 0:
        print(f"\n--- Single-Factor Alpha (TOPIX) ---")
        print(f"  alpha_ann: {alpha_df.iloc[0]['alpha_ann']:.4f}")
        print(f"  t-stat (NW): {alpha_df.iloc[0]['alpha_t_nw']:.2f}")
        print(f"  beta_topix: {alpha_df.iloc[0]['beta_topix']:.4f}")

    print(f"\n--- JUDGMENT ---")
    print(f"  Condition 1 (tax Sharpe >= 1.0): {'PASS' if cond1 else 'FAIL'} (value: {sharpe_tax_full:.4f})")
    print(f"  Condition 2 (5+/7 years gross Sharpe > 0): {'PASS' if cond2 else 'FAIL'} ({yearly_gross_positive}/{total_years})")
    print(f"  Condition 3 (param robustness): {'PASS' if cond3 else 'FAIL'} (lambda={cond3_lambda}, K={cond3_K}, L={cond3_L})")
    print(f"\n  === VERDICT: {verdict} ===")
    print("=" * 70)

    # BQ cost
    if BQ_COST_LOG.exists():
        print(f"\nBQ cost: {BQ_COST_LOG.read_text(encoding='utf-8').strip()}")

    ts1 = datetime.now(tz=JST)
    elapsed = (ts1 - ts0).total_seconds()
    log.info("Done", elapsed_s=f"{elapsed:.1f}", verdict=verdict)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

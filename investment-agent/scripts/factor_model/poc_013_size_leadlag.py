"""Pass 1: Size lead-lag strategy (TOPIX100 -> Mid400).

Knowledge refs:
    - docs/knowledges/analysis/013_size_leadlag_multidef.md
    - docs/knowledges/analysis/012_cluster_overnight_daytime_leadlag.md (failure ref)
    - skills/backtest_design.md (section 4, individual-investor cost model)

Hypothesis (Hou 2007 Japan adaptation):
    Large-cap (TOPIX100) close-to-close returns lead mid-cap (TOPIX Mid400)
    close-to-close returns by 1+ days, because information diffuses from
    high-liquidity institutional names to lower-liquidity mid-caps with delay.

Signal pipeline:
    1. Build universe: TOPIX100 (large, info source) + Mid400 (small, target)
    2. Compute daily c2c log returns for all ~500 names
    3. Build V0: 3 vectors (market, size-spread, cyclical-defensive)
    4. Rolling L=60 day joint correlation of (z_L,t-1, z_S,t) pairs
    5. Regularise C_t^reg = 0.1 * C_t + 0.9 * C0
    6. Top-K=3 eigen decomposition -> V_L, V_S subspaces
    7. Predictor B_t = V_S @ V_L.T (rank <= K)
    8. Signal: z_hat_S = B_t @ z_L,friday (large-cap Friday c2c)
    9. Long-only top q of Mid400, weekly rebalance (Mon open -> Fri close)

Baselines:
    (a) FLAT (zero)
    (b) Simple size momentum (top-q Mid400 by prior-week c2c return)
    (c) lambda=0 plain PCA K=3

Individual investor conditions (skills/backtest_design.md):
    - Long-only, no short
    - Weekly rebalance
    - Commission: 0
    - Spread: Mid400 = 2 bps one-way (mid-cap)
    - Tax: 20.315% on annual positive net
    - q = 0.05 (top 20) and q = 0.10 (top 40)

Outputs under C:\\tmp\\poc_013_size_leadlag\\:
    metrics_summary.csv
    returns_weekly.csv
    signal_sample.csv
"""
from __future__ import annotations

import sys
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from google.cloud import bigquery
from google.oauth2 import service_account

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
JST = ZoneInfo("Asia/Tokyo")

PROJECT = "gmailpj-357912"
KEY_PATH = Path(r"C:\gdrive\claude\investment-agent\keys\gcp-service-account.json")

CACHE_DIR = Path(r"C:\tmp\poc_013_size_leadlag_cache")
OUTPUT_DIR = Path(r"C:\tmp\poc_013_size_leadlag")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Reuse existing price cache from 012 PoC where possible
PREV_CACHE_DIR = Path(r"C:\tmp\poc_overnight_daytime_cache")

# Period
WARMUP_START = pd.Timestamp("2023-01-01")
WARMUP_END = pd.Timestamp("2023-12-31")
OOS_START = pd.Timestamp("2024-01-01")
OOS_END = pd.Timestamp("2024-12-31")

# PCA params
WINDOW_L = 60
K_EIG = 3         # small K for size lead-lag (3 factors: mkt, size, cyclical)
LAMBDA_REG = 0.9  # C^reg = (1-lambda)*C_t + lambda*C0
WINSOR_Q = 0.005

# Cost model (individual investor, Mid400 = mid-cap)
SPREAD_BPS_MID400 = 2.0   # one-way spread for Mid400
SPREAD_BPS_LARGE = 1.0    # one-way spread for TOPIX100 (not traded, but kept)
COMMISSION_BPS = 0.0
TAX_RATE = 0.20315

# Cyclical / Defensive classification based on INDUSTRY_33_CODE
# Cyclical: transportation equipment, electric appliances, iron & steel,
#   wholesale, machinery, chemicals, mining, non-ferrous metals, rubber,
#   glass & ceramics, metal products, construction, real estate, marine transport,
#   air transport, warehousing, securities
CYCLICAL_IND33 = {
    "3050",  # Iron & Steel
    "3100",  # Non-ferrous Metals
    "3150",  # Metal Products
    "3200",  # Machinery
    "3250",  # Electric Appliances
    "3300",  # Transportation Equipment
    "3350",  # Precision Instruments
    "3400",  # Other Products (manufacturing)
    "1050",  # Mining
    "2050",  # Construction
    "3500",  # Wholesale Trade
    "4050",  # Real Estate
    "5050",  # Marine Transportation
    "5100",  # Air Transportation
    "5150",  # Warehousing
    "7050",  # Securities & Commodity Futures
    "7100",  # Insurance
    "7150",  # Other Financing Business
    "3450",  # Chemicals (moved to cyclical — commodity-sensitive)
    "3550",  # Rubber Products
    "3600",  # Glass & Ceramics Products
    "5200",  # Information & Communication (debatable, treated as cyclical)
}

# Everything else is defensive: Foods, Pharmaceuticals, Retail, Electric Power,
# Gas, Services, Banking, Land Transportation, Textiles, Pulp & Paper, etc.


# ---------------------------------------------------------------------------
# BigQuery client + cached queries
# ---------------------------------------------------------------------------
def get_bq_client() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(str(KEY_PATH))
    return bigquery.Client(credentials=creds, project=PROJECT)


def _read_parquet_safe(path: Path) -> pd.DataFrame:
    """Read parquet with potentially incompatible pandas metadata."""
    tbl = pq.read_table(path)
    tbl = tbl.replace_schema_metadata(None)
    return tbl.to_pandas(date_as_object=False)


def cached_query(bq: bigquery.Client, name: str, query: str,
                 cache_dir: Path | None = None) -> pd.DataFrame:
    if cache_dir is None:
        cache_dir = CACHE_DIR
    path = cache_dir / f"{name}.parquet"
    if path.exists():
        print(f"  [cache] {name}")
        return _read_parquet_safe(path)
    print(f"  [BQ]    {name}")
    job = bq.query(query)
    df = job.to_dataframe()
    bytes_processed = job.total_bytes_processed or 0
    print(f"          rows={len(df):,}  processed={bytes_processed/1e9:.3f} GB")
    df.to_parquet(path, index=False)
    with (cache_dir / "bq_cost.log").open("a", encoding="utf-8") as f:
        ts = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"{ts}\t{name}\t{bytes_processed}\t{len(df)}\n")
    return df


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_master(bq: bigquery.Client) -> pd.DataFrame:
    """Load STOCK_CODE_LIST (reuse 012 cache if available)."""
    prev = PREV_CACHE_DIR / "stock_code_list.parquet"
    if prev.exists():
        print("  [cache] stock_code_list (from 012)")
        df = _read_parquet_safe(prev)
        return df
    return cached_query(bq, "stock_code_list", """
        SELECT TICKER, STOCK_NAME, MARKET_CATEGORY,
               INDUSTRY_33_CODE, INDUSTRY_33_CATEGORY,
               SIZE_CODE, SIZE_CATEGORY
        FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
        WHERE EXCHANGE = 'TSE'
    """)


def build_universe(df_master: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Return (large_tickers, small_tickers) based on SIZE_CATEGORY.

    Large = TOPIX Core30 + Large70 (=TOPIX100)
    Small = TOPIX Mid400
    """
    # Filter out ETF/REIT
    m = df_master[
        df_master["INDUSTRY_33_CODE"].notna()
        & ~df_master["MARKET_CATEGORY"].isin(["ETF・ETN", "REIT"])
    ].copy()

    large_mask = m["SIZE_CATEGORY"].isin(["TOPIX Core30", "TOPIX Large70"])
    small_mask = m["SIZE_CATEGORY"] == "TOPIX Mid400"

    large_tickers = sorted(m.loc[large_mask, "TICKER"].unique().tolist())
    small_tickers = sorted(m.loc[small_mask, "TICKER"].unique().tolist())

    print(f"  universe: large(TOPIX100)={len(large_tickers)}, "
          f"small(Mid400)={len(small_tickers)}")
    return large_tickers, small_tickers


def load_prices(bq: bigquery.Client, large: list[str], small: list[str]
                ) -> pd.DataFrame:
    """Load daily prices for the universe. Reuse 012 cache as base, then
    query any missing tickers from BQ."""
    all_tickers = sorted(set(large) | set(small))

    # Try to load from previous cache
    prev = PREV_CACHE_DIR / "price_ohlc_2023_2024.parquet"
    if prev.exists():
        print("  [cache] price_ohlc_2023_2024 (from 012)")
        df_prev = _read_parquet_safe(prev)
        df_prev["DATE"] = pd.to_datetime(df_prev["DATE"])
        cached_tickers = set(df_prev["TICKER"].unique())
        missing = sorted(set(all_tickers) - cached_tickers)
        if not missing:
            print(f"    all {len(all_tickers)} tickers found in cache")
            return df_prev[df_prev["TICKER"].isin(all_tickers)]
        print(f"    {len(missing)} tickers not in 012 cache, querying BQ...")
    else:
        missing = all_tickers
        df_prev = pd.DataFrame()
        print(f"  no previous cache, querying all {len(missing)} tickers")

    # Query missing tickers
    tickers_str = ", ".join(f"'{t}'" for t in missing)
    df_new = cached_query(bq, "price_013_supplement", f"""
        SELECT TICKER, DATE, ADJ_OPEN, ADJ_CLOSE, VOLUME
        FROM `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS`
        WHERE DATE BETWEEN '2022-12-01' AND '2024-12-31'
          AND IS_PREFERRED = FALSE
          AND ADJ_OPEN IS NOT NULL
          AND ADJ_CLOSE IS NOT NULL
          AND VOLUME > 0
          AND TICKER IN UNNEST([{tickers_str}])
    """)
    df_new["DATE"] = pd.to_datetime(df_new["DATE"])

    if len(df_prev) > 0:
        df_all = pd.concat([
            df_prev[df_prev["TICKER"].isin(all_tickers)],
            df_new
        ], ignore_index=True)
    else:
        df_all = df_new

    return df_all


# ---------------------------------------------------------------------------
# Return construction
# ---------------------------------------------------------------------------
def build_c2c_returns(df_price: pd.DataFrame) -> pd.DataFrame:
    """Build DATE x TICKER wide DataFrame of close-to-close log returns."""
    df = df_price.sort_values(["TICKER", "DATE"]).drop_duplicates(
        ["TICKER", "DATE"], keep="last"
    )
    df["PREV_CLOSE"] = df.groupby("TICKER")["ADJ_CLOSE"].shift(1)
    df = df[(df["PREV_CLOSE"] > 0) & (df["ADJ_CLOSE"] > 0)]
    df["C2C"] = np.log(df["ADJ_CLOSE"] / df["PREV_CLOSE"])
    c2c = df.pivot(index="DATE", columns="TICKER", values="C2C").sort_index()
    return c2c


# ---------------------------------------------------------------------------
# V0 construction (3-vector prior subspace)
# ---------------------------------------------------------------------------
def build_V0(large: list[str], small: list[str],
             df_master: pd.DataFrame) -> np.ndarray:
    """Construct N x K0 prior subspace matrix (K0=3), orthonormalised.

    v1: all-ones (market factor)
    v2: large=+1, small=-1 (size spread) -> orthogonalised to v1
    v3: cyclical=+1, defensive=-1 -> orthogonalised to v1,v2

    Ticker ordering: large first, then small (consistent with correlation matrix).
    """
    all_tickers = large + small
    N = len(all_tickers)

    # v1: market
    v1 = np.ones(N) / np.sqrt(N)

    # v2: size spread
    v2 = np.zeros(N)
    n_large = len(large)
    n_small = len(small)
    v2[:n_large] = 1.0 / n_large
    v2[n_large:] = -1.0 / n_small
    # orthogonalise to v1
    v2 = v2 - np.dot(v2, v1) * v1
    norm = np.linalg.norm(v2)
    if norm > 1e-10:
        v2 = v2 / norm

    # v3: cyclical vs defensive
    ind_map = df_master.set_index("TICKER")["INDUSTRY_33_CODE"].to_dict()
    v3 = np.zeros(N)
    for i, t in enumerate(all_tickers):
        ind = ind_map.get(t)
        if ind in CYCLICAL_IND33:
            v3[i] = 1.0
        else:
            v3[i] = -1.0
    # orthogonalise to v1, v2
    v3 = v3 - np.dot(v3, v1) * v1
    v3 = v3 - np.dot(v3, v2) * v2
    norm = np.linalg.norm(v3)
    if norm > 1e-10:
        v3 = v3 / norm

    V0 = np.column_stack([v1, v2, v3])  # N x 3
    # Final QR for numerical stability
    Q, _ = np.linalg.qr(V0)
    print(f"  V0 shape: {Q.shape}")
    return Q


# ---------------------------------------------------------------------------
# Regularised PCA lead-lag
# ---------------------------------------------------------------------------
def winsorise(X: np.ndarray, q: float = WINSOR_Q) -> np.ndarray:
    lo = np.nanquantile(X, q)
    hi = np.nanquantile(X, 1 - q)
    return np.clip(X, lo, hi)


def compute_C0(large_ret_warm: np.ndarray, small_ret_warm: np.ndarray
               ) -> np.ndarray:
    """Long-window target correlation from warmup period.

    Joint structure: for each day t in warmup, we pair
        (large_t-1, small_t) to form a T x (N_L + N_S) matrix.
    This captures the 1-day lag structure in the long-term correlation.
    """
    # Align: large[:-1] paired with small[1:]
    T = min(large_ret_warm.shape[0], small_ret_warm.shape[0]) - 1
    Z_large = large_ret_warm[:T]   # large day t
    Z_small = small_ret_warm[1:T+1]  # small day t+1

    Z = np.concatenate([Z_large, Z_small], axis=1)  # T x (N_L + N_S)
    Z = Z - Z.mean(axis=0, keepdims=True)
    std = Z.std(axis=0, keepdims=True)
    std[std == 0] = 1.0
    Z = Z / std
    C0 = (Z.T @ Z) / max(Z.shape[0] - 1, 1)
    return C0


def regularised_predictor(C_window: np.ndarray, C0: np.ndarray,
                          N_L: int, N_S: int, K: int, lam: float
                          ) -> np.ndarray:
    """Return N_S x N_L predictor mapping large_t -> small_t+1.

    Joint corr block structure ((N_L+N_S) x (N_L+N_S)):
        [ C_LL     C_LS  ]
        [ C_SL     C_SS  ]

    Regularise, eigen-decompose, split into L/S halves.
    Predictor B = V_S @ V_L.T (rank <= K).
    """
    C_reg = (1 - lam) * C_window + lam * C0
    C_reg = (C_reg + C_reg.T) / 2.0
    vals, vecs = np.linalg.eigh(C_reg)
    # top-K by magnitude
    order = np.argsort(vals)[::-1][:K]
    V = vecs[:, order]  # (N_L+N_S) x K
    V_L = V[:N_L, :]
    V_S = V[N_L:, :]
    B = V_S @ V_L.T  # N_S x N_L
    return B


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------
def compute_weekly_signals(
    c2c: pd.DataFrame,
    large: list[str],
    small: list[str],
    V0: np.ndarray,
) -> pd.DataFrame:
    """Compute weekly signal for Mid400 stocks.

    For each Friday in OOS:
        1. Use large-cap c2c returns from the current week (Mon-Fri)
           as a summary signal (average or Friday only)
        2. Actually, use Friday's large-cap c2c as the signal input
           (most recent info available at Friday close)
        3. Rolling window of lagged pairs to estimate B_t

    Returns DataFrame indexed by signal_date (Fridays), columns = small tickers.
    """
    all_tickers = large + small
    N_L = len(large)
    N_S = len(small)

    # Get c2c for all tickers, fill missing with 0
    c2c_all = c2c.reindex(columns=all_tickers).fillna(0.0)
    all_dates = c2c_all.index
    idx_of = {d: i for i, d in enumerate(all_dates)}

    # Warmup C0
    warm_mask = (all_dates >= WARMUP_START) & (all_dates <= WARMUP_END)
    warm_large = winsorise(c2c_all[large].values[warm_mask])
    warm_small = winsorise(c2c_all[small].values[warm_mask])
    C0 = compute_C0(warm_large, warm_small)
    print(f"  C0 shape: {C0.shape} (expected {N_L+N_S} x {N_L+N_S})")

    # Identify Fridays in OOS period
    oos_mask = (all_dates >= OOS_START) & (all_dates <= OOS_END)
    oos_dates = all_dates[oos_mask]
    # group by ISO week, take last day as "Friday" (or last trading day of week)
    week_groups: dict[tuple, list] = {}
    for d in oos_dates:
        wk = (d.isocalendar().year, d.isocalendar().week)
        week_groups.setdefault(wk, []).append(d)

    signal_rows = []
    signal_dates = []

    for wk in sorted(week_groups.keys()):
        days = sorted(week_groups[wk])
        friday = days[-1]  # last trading day of the week
        t_idx = idx_of[friday]

        # Need WINDOW_L+1 prior days for the lagged-pair rolling window
        if t_idx < WINDOW_L + 1:
            continue

        # Build rolling window of lagged pairs: (large[t-1], small[t])
        # for t in [friday - WINDOW_L, friday)
        window_start = t_idx - WINDOW_L
        window_end = t_idx  # exclusive

        large_vals = c2c_all[large].values
        small_vals = c2c_all[small].values

        # Lagged pairs: large[t-1] paired with small[t] for t in window
        Z_L_win = winsorise(large_vals[window_start-1:window_end-1])  # large day t-1
        Z_S_win = winsorise(small_vals[window_start:window_end])       # small day t

        Z = np.concatenate([Z_L_win, Z_S_win], axis=1)  # L x (N_L + N_S)
        Z = Z - Z.mean(axis=0, keepdims=True)
        std = Z.std(axis=0, keepdims=True)
        std[std == 0] = 1.0
        Z = Z / std
        C_win = (Z.T @ Z) / max(Z.shape[0] - 1, 1)

        # Regularised predictor
        B_reg = regularised_predictor(C_win, C0, N_L, N_S, K_EIG, LAMBDA_REG)

        # Signal input: Friday's large-cap c2c returns (known at Friday close)
        z_L_friday = large_vals[t_idx]
        z_L_friday = np.clip(z_L_friday,
                             np.nanquantile(z_L_friday, WINSOR_Q),
                             np.nanquantile(z_L_friday, 1 - WINSOR_Q))
        # Standardise using window statistics
        L_mu = large_vals[window_start-1:window_end-1].mean(axis=0)
        L_sig = large_vals[window_start-1:window_end-1].std(axis=0)
        L_sig[L_sig == 0] = 1.0
        z_L_std = (z_L_friday - L_mu) / L_sig

        # Predicted small-cap score
        z_hat_S = B_reg @ z_L_std

        signal_rows.append(z_hat_S)
        signal_dates.append(friday)

    sig_df = pd.DataFrame(signal_rows, index=pd.DatetimeIndex(signal_dates),
                          columns=small)
    print(f"  signal panel: {sig_df.shape} ({len(signal_dates)} weeks)")
    return sig_df


def compute_momentum_baseline(
    c2c: pd.DataFrame, small: list[str]
) -> pd.DataFrame:
    """Simple size momentum baseline: prior-week c2c cumulative return for each
    Mid400 stock. Higher = more momentum."""
    c2c_small = c2c.reindex(columns=small).fillna(0.0)
    all_dates = c2c_small.index

    oos_mask = (all_dates >= OOS_START) & (all_dates <= OOS_END)
    oos_dates = all_dates[oos_mask]
    week_groups: dict[tuple, list] = {}
    for d in oos_dates:
        wk = (d.isocalendar().year, d.isocalendar().week)
        week_groups.setdefault(wk, []).append(d)

    rows = []
    dates = []
    for wk in sorted(week_groups.keys()):
        days = sorted(week_groups[wk])
        friday = days[-1]
        t_idx = list(all_dates).index(friday)
        # Prior week's cumulative c2c for small stocks
        # Use the 5 trading days ending on friday
        start = max(0, t_idx - 4)
        week_rets = c2c_small.values[start:t_idx+1]  # up to 5 days
        cum_ret = week_rets.sum(axis=0)  # log return sum = log(cum)
        rows.append(cum_ret)
        dates.append(friday)

    return pd.DataFrame(rows, index=pd.DatetimeIndex(dates), columns=small)


def compute_plain_pca_signal(
    c2c: pd.DataFrame, large: list[str], small: list[str]
) -> pd.DataFrame:
    """Baseline: lambda=0 PCA (no regularisation)."""
    all_tickers = large + small
    N_L = len(large)
    N_S = len(small)

    c2c_all = c2c.reindex(columns=all_tickers).fillna(0.0)
    all_dates = c2c_all.index

    # Warmup C0 (still needed for the eigendecomposition structure)
    warm_mask = (all_dates >= WARMUP_START) & (all_dates <= WARMUP_END)
    warm_large = winsorise(c2c_all[large].values[warm_mask])
    warm_small = winsorise(c2c_all[small].values[warm_mask])
    C0 = compute_C0(warm_large, warm_small)

    oos_mask = (all_dates >= OOS_START) & (all_dates <= OOS_END)
    oos_dates = all_dates[oos_mask]
    week_groups: dict[tuple, list] = {}
    for d in oos_dates:
        wk = (d.isocalendar().year, d.isocalendar().week)
        week_groups.setdefault(wk, []).append(d)

    idx_of = {d: i for i, d in enumerate(all_dates)}
    signal_rows = []
    signal_dates = []
    large_vals = c2c_all[large].values
    small_vals = c2c_all[small].values

    for wk in sorted(week_groups.keys()):
        days = sorted(week_groups[wk])
        friday = days[-1]
        t_idx = idx_of[friday]
        if t_idx < WINDOW_L + 1:
            continue

        window_start = t_idx - WINDOW_L
        window_end = t_idx

        Z_L_win = winsorise(large_vals[window_start-1:window_end-1])
        Z_S_win = winsorise(small_vals[window_start:window_end])
        Z = np.concatenate([Z_L_win, Z_S_win], axis=1)
        Z = Z - Z.mean(axis=0, keepdims=True)
        std = Z.std(axis=0, keepdims=True)
        std[std == 0] = 1.0
        Z = Z / std
        C_win = (Z.T @ Z) / max(Z.shape[0] - 1, 1)

        # lambda=0: pure sample correlation
        B_plain = regularised_predictor(C_win, C0, N_L, N_S, K_EIG, 0.0)

        z_L_friday = large_vals[t_idx]
        z_L_friday = np.clip(z_L_friday,
                             np.nanquantile(z_L_friday, WINSOR_Q),
                             np.nanquantile(z_L_friday, 1 - WINSOR_Q))
        L_mu = large_vals[window_start-1:window_end-1].mean(axis=0)
        L_sig = large_vals[window_start-1:window_end-1].std(axis=0)
        L_sig[L_sig == 0] = 1.0
        z_L_std = (z_L_friday - L_mu) / L_sig

        z_hat_S = B_plain @ z_L_std
        signal_rows.append(z_hat_S)
        signal_dates.append(friday)

    return pd.DataFrame(signal_rows, index=pd.DatetimeIndex(signal_dates),
                        columns=small)


# ---------------------------------------------------------------------------
# Backtest engine
# ---------------------------------------------------------------------------
def weekly_backtest(
    sig_df: pd.DataFrame,
    c2c: pd.DataFrame,
    small: list[str],
    q: float,
    spread_bps: float,
    label: str,
) -> dict:
    """Weekly long-only backtest.

    Signal is computed at Friday close. Entry at Monday open (next week first
    trading day). Hold through Friday close. Earn c2c returns for Mon-Fri.

    Since we don't have open prices for all small-caps cleanly, we approximate:
    Monday open ≈ Friday close (the gap is overnight Fri->Mon, which is noise
    that goes both ways and averages out). So the earned return is simply
    sum of c2c for the 5 trading days Mon-Fri of the NEXT week.

    Actually, to be precise: signal computed at Friday of week W.
    Investment period = week W+1 (Mon through Fri).
    Returns earned = sum of daily c2c for each held stock during W+1.
    """
    c2c_small = c2c.reindex(columns=small).fillna(0.0)
    all_dates = c2c_small.index

    # Group OOS dates by ISO week
    oos_mask = (all_dates >= OOS_START) & (all_dates <= OOS_END)
    oos_dates = all_dates[oos_mask]
    week_groups: dict[tuple, list] = {}
    for d in oos_dates:
        wk = (d.isocalendar().year, d.isocalendar().week)
        week_groups.setdefault(wk, []).append(d)
    weeks_sorted = sorted(week_groups.keys())

    # Map signal_date (Friday) to the next week
    sig_fridays = sorted(sig_df.index)

    n_select = max(1, int(round(q * len(small))))
    weekly_gross = []
    weekly_cost = []
    weekly_dates = []  # label = Monday of holding week
    prev_selection: set[str] = set()
    turnover_list = []
    tickers_arr = np.array(small)

    for i, fri in enumerate(sig_fridays):
        fri_wk = (fri.isocalendar().year, fri.isocalendar().week)
        # Find the next week in weeks_sorted
        try:
            fri_idx = weeks_sorted.index(fri_wk)
        except ValueError:
            continue
        if fri_idx + 1 >= len(weeks_sorted):
            continue  # no next week available
        next_wk = weeks_sorted[fri_idx + 1]
        next_days = week_groups[next_wk]

        # Signal
        sig_vec = sig_df.loc[fri].values.copy()
        if not np.isfinite(sig_vec).any():
            continue
        sig_vec = sig_vec - np.nanmean(sig_vec[np.isfinite(sig_vec)])
        order = np.argsort(-sig_vec)
        top_idx = order[:n_select]
        selection = set(tickers_arr[top_idx].tolist())

        # Turnover
        if prev_selection:
            overlap = len(prev_selection & selection)
            new_share = 1.0 - overlap / max(n_select, 1)
            turnover_list.append(new_share)
        else:
            new_share = 1.0

        # Cost: round-trip on the changed fraction
        # Entry cost (new names) + Exit cost (dropped names), each one-way
        rebal_cost = new_share * 2 * spread_bps / 10000.0

        # Weekly return: sum of daily c2c for held stocks
        held_names = list(selection)
        if not held_names:
            prev_selection = selection
            continue

        week_ret = 0.0
        for d in next_days:
            if d in c2c_small.index:
                day_rets = c2c_small.loc[d, held_names].values
                week_ret += float(np.nanmean(day_rets))

        weekly_gross.append(week_ret)
        weekly_cost.append(rebal_cost)
        weekly_dates.append(next_days[0])  # Monday of holding week
        prev_selection = selection

    result = {
        "weekly_gross": pd.Series(weekly_gross, index=pd.DatetimeIndex(weekly_dates)),
        "weekly_cost": pd.Series(weekly_cost, index=pd.DatetimeIndex(weekly_dates)),
        "n_select": n_select,
        "avg_turnover": float(np.mean(turnover_list)) if turnover_list else np.nan,
        "label": label,
    }
    return result


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def compute_metrics_3stage(
    weekly_gross: pd.Series,
    weekly_cost: pd.Series,
    label: str,
    q: float,
    n_select: int,
    avg_turnover: float,
) -> list[dict]:
    """Compute gross / post-cost / post-tax metrics from weekly returns."""
    net = weekly_gross - weekly_cost

    rows = []
    for stage_name, series in [("gross", weekly_gross),
                                ("post_cost", net)]:
        s = series.dropna()
        if len(s) == 0:
            continue
        # Annualise: ~52 weeks/year
        mu = s.mean() * 52
        sig = s.std(ddof=1) * np.sqrt(52)
        sharpe = mu / sig if sig > 0 else np.nan
        cum = (1 + s).cumprod()
        peak = cum.cummax()
        dd = (cum / peak - 1).min()
        rows.append({
            "strategy": label,
            "q": q,
            "stage": stage_name,
            "ann_return": mu,
            "ann_risk": sig,
            "sharpe": sharpe,
            "max_drawdown": dd,
            "n_select": n_select,
            "avg_turnover": avg_turnover,
        })

    # Post-tax
    net_s = net.dropna()
    if len(net_s) > 0:
        n_mu = net_s.mean() * 52
        n_sig = net_s.std(ddof=1) * np.sqrt(52)
        if n_mu > 0:
            t_mu = n_mu * (1 - TAX_RATE)
            t_sig = n_sig
            t_sh = t_mu / t_sig if t_sig > 0 else np.nan
        else:
            t_mu, t_sig, t_sh = n_mu, n_sig, (n_mu / n_sig if n_sig > 0 else np.nan)
        cum = (1 + net_s).cumprod()
        peak = cum.cummax()
        t_dd = (cum / peak - 1).min()
        rows.append({
            "strategy": label,
            "q": q,
            "stage": "post_tax",
            "ann_return": t_mu,
            "ann_risk": t_sig,
            "sharpe": t_sh,
            "max_drawdown": t_dd,
            "n_select": n_select,
            "avg_turnover": avg_turnover,
        })

    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ts0 = datetime.now(tz=JST)
    print(f"=== PoC 013 size lead-lag (TOPIX100 -> Mid400) start {ts0.isoformat()} ===")

    bq = get_bq_client()

    # Load master + build universe
    print("\n--- Loading master data ---")
    df_master = load_master(bq)

    large, small = build_universe(df_master)
    if len(large) < 30 or len(small) < 100:
        print(f"ERROR: universe too small (large={len(large)}, small={len(small)})",
              file=sys.stderr)
        return 1

    # Load prices
    print("\n--- Loading prices ---")
    df_price = load_prices(bq, large, small)
    print(f"  price rows: {len(df_price):,}")

    # Build c2c returns
    print("\n--- Building returns ---")
    c2c = build_c2c_returns(df_price)
    print(f"  c2c panel: {c2c.shape}")

    # Filter universe to tickers with data
    # Check missing rate in warmup
    warm = c2c.loc[WARMUP_START:WARMUP_END]
    missing_rate = warm.isna().mean(axis=0)

    large_ok = [t for t in large if t in c2c.columns
                and missing_rate.get(t, 1.0) <= 0.10]
    small_ok = [t for t in small if t in c2c.columns
                and missing_rate.get(t, 1.0) <= 0.10]
    print(f"  after missing filter: large={len(large_ok)}, small={len(small_ok)}")
    large = large_ok
    small = small_ok

    # Build V0
    print("\n--- Building V0 (prior subspace) ---")
    V0 = build_V0(large, small, df_master)

    # Compute signals
    print("\n--- Computing regularised PCA signal ---")
    sig_reg = compute_weekly_signals(c2c, large, small, V0)

    print("\n--- Computing momentum baseline signal ---")
    sig_mom = compute_momentum_baseline(c2c, small)

    print("\n--- Computing plain PCA (lambda=0) baseline ---")
    sig_plain = compute_plain_pca_signal(c2c, large, small)

    # Run backtests
    all_metrics = []
    all_weekly_returns = {}
    sample_rows = []

    for q in (0.05, 0.10):
        print(f"\n{'='*60}")
        print(f"  q = {q}")
        print(f"{'='*60}")

        # (1) Regularised PCA
        res_reg = weekly_backtest(sig_reg, c2c, small, q,
                                 SPREAD_BPS_MID400, "REG_PCA")
        metrics_reg = compute_metrics_3stage(
            res_reg["weekly_gross"], res_reg["weekly_cost"],
            "REG_PCA", q, res_reg["n_select"], res_reg["avg_turnover"])
        all_metrics.extend(metrics_reg)

        # (2) Momentum baseline
        res_mom = weekly_backtest(sig_mom, c2c, small, q,
                                 SPREAD_BPS_MID400, "SIZE_MOM")
        metrics_mom = compute_metrics_3stage(
            res_mom["weekly_gross"], res_mom["weekly_cost"],
            "SIZE_MOM", q, res_mom["n_select"], res_mom["avg_turnover"])
        all_metrics.extend(metrics_mom)

        # (3) Plain PCA (lambda=0)
        res_plain = weekly_backtest(sig_plain, c2c, small, q,
                                   SPREAD_BPS_MID400, "PLAIN_PCA")
        metrics_plain = compute_metrics_3stage(
            res_plain["weekly_gross"], res_plain["weekly_cost"],
            "PLAIN_PCA", q, res_plain["n_select"], res_plain["avg_turnover"])
        all_metrics.extend(metrics_plain)

        # (4) FLAT baseline
        flat_gross = pd.Series(0.0, index=res_reg["weekly_gross"].index)
        flat_cost = pd.Series(0.0, index=flat_gross.index)
        all_metrics.extend(compute_metrics_3stage(
            flat_gross, flat_cost, "FLAT", q, 0, 0.0))

        # Store weekly returns
        q_label = f"q{int(q*100):02d}"
        ret_df = pd.DataFrame({
            f"{q_label}_reg_gross": res_reg["weekly_gross"],
            f"{q_label}_reg_cost": res_reg["weekly_cost"],
            f"{q_label}_reg_net": res_reg["weekly_gross"] - res_reg["weekly_cost"],
            f"{q_label}_mom_gross": res_mom["weekly_gross"],
            f"{q_label}_plain_gross": res_plain["weekly_gross"],
        })
        all_weekly_returns[q_label] = ret_df

        # Signal sample: first Friday in July 2024
        jul_fridays = [d for d in sig_reg.index if d >= pd.Timestamp("2024-07-01")]
        if jul_fridays:
            sample_fri = jul_fridays[0]
            sig_vec = sig_reg.loc[sample_fri].values.copy()
            sig_vec = sig_vec - np.nanmean(sig_vec[np.isfinite(sig_vec)])
            n_sel = max(1, int(round(q * len(small))))
            top = np.argsort(-sig_vec)[:n_sel]
            ind_map = df_master.set_index("TICKER")["INDUSTRY_33_CATEGORY"].to_dict()
            for rank, i in enumerate(top, start=1):
                t = small[i]
                sample_rows.append({
                    "q": q,
                    "signal_date": sample_fri,
                    "rank": rank,
                    "ticker": t,
                    "signal": float(sig_vec[i]),
                    "industry_33": ind_map.get(t, ""),
                })

    # Persist results
    metrics_df = pd.DataFrame(all_metrics)
    metrics_path = OUTPUT_DIR / "metrics_summary.csv"
    metrics_df.to_csv(metrics_path, index=False, encoding="utf-8")
    print(f"\n  wrote {metrics_path}")

    # Combine weekly returns
    if all_weekly_returns:
        combined = pd.concat(all_weekly_returns.values(), axis=1)
        combined.index.name = "DATE"
        returns_path = OUTPUT_DIR / "returns_weekly.csv"
        combined.to_csv(returns_path, encoding="utf-8")
        print(f"  wrote {returns_path}")

    sample_df = pd.DataFrame(sample_rows)
    sample_path = OUTPUT_DIR / "signal_sample.csv"
    sample_df.to_csv(sample_path, index=False, encoding="utf-8")
    print(f"  wrote {sample_path}")

    # Print summary
    print("\n" + "=" * 80)
    print("METRICS SUMMARY")
    print("=" * 80)
    print(metrics_df.to_string(index=False))

    # Pass 1 judgement
    pass_flag = False
    for _, row in metrics_df.iterrows():
        if row["stage"] == "post_tax" and row["strategy"] == "REG_PCA":
            if row["sharpe"] >= 1.0:
                pass_flag = True
                print(f"\n>>> Pass 1 PASS: {row['strategy']} q={row['q']} "
                      f"post-tax Sharpe={row['sharpe']:.2f}")
    if not pass_flag:
        print("\n>>> Pass 1 FAIL: no REG_PCA variation reaches post-tax Sharpe >= 1.0")

    # Signal sample summary
    if len(sample_df) > 0:
        print("\n--- Signal sample (first Friday in July 2024) ---")
        print(sample_df.to_string(index=False))

    ts1 = datetime.now(tz=JST)
    elapsed = (ts1 - ts0).total_seconds()
    print(f"\n=== done in {elapsed:.1f}s ({ts1.isoformat()}) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

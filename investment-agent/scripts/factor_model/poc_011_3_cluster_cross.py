"""Pass 1: Cluster cross lead-lag strategy (011-3).

Knowledge refs:
    - docs/knowledges/analysis/011-3_cluster_cross_leadlag.md
    - docs/knowledges/analysis/011-1_cluster_overnight_daytime_leadlag.md (failure)
    - docs/knowledges/analysis/011-2_size_leadlag_multidef.md (failure, Sharpe 0.47)
    - docs/knowledges/analysis/010_factor_model_residual_corr.md (clustering)
    - skills/backtest_design.md section 4 (individual investor cost model)

Hypothesis:
    Shocks in residual-correlation cluster j propagate to cluster k (j != k)
    with a 1+ day delay. The low-rank matrix B_t = V_follow @ V_lead.T (20x20)
    captures cross-cluster information diffusion, analogous to the US-sector ->
    Japan-sector lead-lag in paper SIG-FIN-036-13.

Signal pipeline:
    1. Load 20 residual-correlation clusters from 2023 warm-up
    2. Build cluster representative c2c returns (equal-weight average)
    3. Rolling L=60 day joint correlation of (z_lead,t-1, z_follow,t)
       where z_lead = 20 cluster reps at t-1, z_follow = 20 cluster reps at t
    4. Regularise C_t^reg = 0.1 * C_t + 0.9 * C0
    5. Top-K=3 eigen decomposition -> V_lead, V_follow subspaces
    6. Predictor B_t = V_follow @ V_lead.T (20x20, rank <= 3)
    7. Signal: z_hat_follow = B_t @ z_lead,friday
    8. Assign cluster-level signal to all member stocks
    9. Long-only top q of universe, weekly rebalance (Fri signal -> Mon-Fri hold)

V0 design (K0=3):
    v1: all 40 elements equal-weight (market)
    v2: lead=+1, follow=-1 (temporal spread)
    v3: cyclical clusters=+1, defensive=-1

Baselines:
    (a) FLAT
    (b) Simple cluster momentum (long stocks in prior-week's best clusters)
    (c) lambda=0 plain PCA K=3

Individual investor conditions:
    - Long-only
    - Weekly rebalance
    - Commission: 0
    - Spread: 2 bps one-way (mid-cap mix)
    - Tax: 20.315% on annual positive net
    - q = 0.05 and q = 0.10

Outputs under C:\\tmp\\poc_011_3_cluster_cross\\:
    metrics_summary.csv
    returns_weekly.csv
    signal_sample.csv
    cluster_signal_matrix.csv (B_t snapshot)
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
from scipy.cluster.hierarchy import fcluster, linkage

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
JST = ZoneInfo("Asia/Tokyo")

PROJECT = "gmailpj-357912"
KEY_PATH = Path(r"C:\gdrive\claude\investment-agent\keys\gcp-service-account.json")

# Reuse existing cache from 011-1 PoC (price, master, shares, topix, clusters)
PREV_CACHE_DIR = Path(r"C:\tmp\poc_overnight_daytime_cache")
PREV_OUTPUT_DIR = Path(r"C:\tmp\poc_overnight_daytime")
CACHE_DIR = Path(r"C:\tmp\poc_011_3_cluster_cross_cache")
OUTPUT_DIR = Path(r"C:\tmp\poc_011_3_cluster_cross")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Universe params
MARKET_CAP_MIN = 50_000_000_000  # 500B JPY
MISSING_RATE_MAX = 0.05

# Period
WARMUP_START = pd.Timestamp("2023-01-01")
WARMUP_END = pd.Timestamp("2023-12-31")
OOS_START = pd.Timestamp("2024-01-01")
OOS_END = pd.Timestamp("2024-12-31")

# PCA params
N_CLUSTERS = 20
WINDOW_L = 60
K_EIG = 3       # low-rank dimension for 20x20 cluster cross lead-lag
LAMBDA_REG = 0.9
WINSOR_Q = 0.005

# Cost model
SPREAD_BPS = 2.0   # one-way, mid-cap mix
COMMISSION_BPS = 0.0
TAX_RATE = 0.20315

# Cyclical industry codes (same as 011-2)
CYCLICAL_IND33 = {
    "3050", "3100", "3150", "3200", "3250", "3300", "3350", "3400",
    "1050", "2050", "3500", "4050", "5050", "5100", "5150",
    "7050", "7100", "7150", "3450", "3550", "3600", "5200",
}


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------
def _read_parquet_safe(path: Path) -> pd.DataFrame:
    """Read parquet with potentially incompatible pandas metadata."""
    tbl = pq.read_table(path)
    tbl = tbl.replace_schema_metadata(None)
    return tbl.to_pandas(date_as_object=False)


def get_bq_client() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(str(KEY_PATH))
    return bigquery.Client(credentials=creds, project=PROJECT)


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
def load_data(bq: bigquery.Client) -> dict[str, pd.DataFrame]:
    """Load price, master, shares, topix from previous cache or BQ."""
    result: dict[str, pd.DataFrame] = {}

    # price
    prev_price = PREV_CACHE_DIR / "price_ohlc_2023_2024.parquet"
    if prev_price.exists():
        print("  [cache] price_ohlc_2023_2024 (from 011-1)")
        df_price = _read_parquet_safe(prev_price)
    else:
        df_price = cached_query(bq, "price_ohlc_2023_2024", """
            SELECT TICKER, DATE, ADJ_OPEN, ADJ_CLOSE, VOLUME
            FROM `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS`
            WHERE DATE BETWEEN '2022-12-01' AND '2024-12-31'
              AND IS_PREFERRED = FALSE
              AND ADJ_OPEN IS NOT NULL
              AND ADJ_CLOSE IS NOT NULL
              AND VOLUME > 0
        """)
    df_price["DATE"] = pd.to_datetime(df_price["DATE"])
    result["price"] = df_price

    # master
    prev_master = PREV_CACHE_DIR / "stock_code_list.parquet"
    if prev_master.exists():
        print("  [cache] stock_code_list (from 011-1)")
        result["master"] = _read_parquet_safe(prev_master)
    else:
        result["master"] = cached_query(bq, "stock_code_list", """
            SELECT TICKER, STOCK_NAME, MARKET_CATEGORY,
                   INDUSTRY_33_CODE, INDUSTRY_33_CATEGORY,
                   SIZE_CODE, SIZE_CATEGORY
            FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
            WHERE EXCHANGE = 'TSE'
        """)

    # shares
    prev_shares = PREV_CACHE_DIR / "fin_summary_shares.parquet"
    if prev_shares.exists():
        print("  [cache] fin_summary_shares (from 011-1)")
        result["shares"] = _read_parquet_safe(prev_shares)
    else:
        result["shares"] = cached_query(bq, "fin_summary_shares", """
            SELECT
                LOCAL_CODE AS TICKER,
                CURRENT_PERIOD_END_DATE AS PERIOD_END,
                NUMBER_OF_ISSUED_AND_OUTSTANDING_SHARES_AT_THE_END_OF_FISCAL_YEAR_INCLUDING_TREASURY_STOCK AS SHARES_ISSUED,
                NUMBER_OF_TREASURY_STOCK_AT_THE_END_OF_FISCAL_YEAR AS TREASURY_STOCK
            FROM `gmailpj-357912.STOCK.fin_summary`
            WHERE NUMBER_OF_ISSUED_AND_OUTSTANDING_SHARES_AT_THE_END_OF_FISCAL_YEAR_INCLUDING_TREASURY_STOCK IS NOT NULL
              AND CURRENT_PERIOD_END_DATE <= '2023-12-31'
        """)
    result["shares"]["PERIOD_END"] = pd.to_datetime(result["shares"]["PERIOD_END"])

    # topix
    prev_topix = PREV_CACHE_DIR / "topix.parquet"
    if prev_topix.exists():
        print("  [cache] topix (from 011-1)")
        result["topix"] = _read_parquet_safe(prev_topix)
    else:
        result["topix"] = cached_query(bq, "topix", """
            SELECT DATE, CLOSE, OPEN
            FROM `gmailpj-357912.STOCK.INDEX_PRICE`
            WHERE INDEX_CODE = '0000'
              AND DATE BETWEEN '2022-12-01' AND '2024-12-31'
            ORDER BY DATE
        """)
    result["topix"]["DATE"] = pd.to_datetime(result["topix"]["DATE"])

    return result


# ---------------------------------------------------------------------------
# Return + universe construction
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


def select_universe(
    c2c: pd.DataFrame,
    df_price: pd.DataFrame,
    df_master: pd.DataFrame,
    df_shares: pd.DataFrame,
) -> list[str]:
    """Pick universe: market cap >= 500B, low missingness, ETF/REIT excluded."""
    warm = c2c.loc[WARMUP_START:WARMUP_END]

    # missing rate
    missing_rate = warm.isna().mean(axis=0)
    low_missing = set(missing_rate[missing_rate <= MISSING_RATE_MAX].index)

    # market cap at 2023-01
    df_2023 = df_price[(df_price["DATE"] >= WARMUP_START) & (df_price["DATE"] <= WARMUP_END)]
    first_day_px = (
        df_2023.sort_values(["TICKER", "DATE"])
        .groupby("TICKER")
        .first()[["ADJ_CLOSE"]]
        .reset_index()
    )

    cutoff = pd.Timestamp("2022-12-31")
    sh = df_shares[df_shares["PERIOD_END"] <= cutoff].copy()
    idx = sh.groupby("TICKER")["PERIOD_END"].idxmax()
    sh = sh.loc[idx, ["TICKER", "SHARES_ISSUED", "TREASURY_STOCK"]].copy()
    sh["TREASURY_STOCK"] = sh["TREASURY_STOCK"].fillna(0)
    sh["SHARES_OUT"] = sh["SHARES_ISSUED"] - sh["TREASURY_STOCK"]

    mcap = first_day_px.merge(sh[["TICKER", "SHARES_OUT"]], on="TICKER", how="inner")
    mcap["MCAP"] = mcap["ADJ_CLOSE"] * mcap["SHARES_OUT"]
    large = set(mcap[mcap["MCAP"] >= MARKET_CAP_MIN]["TICKER"])

    m = df_master[
        df_master["INDUSTRY_33_CODE"].notna()
        & ~df_master["MARKET_CATEGORY"].isin(["ETF・ETN", "REIT"])
    ]
    master_set = set(m["TICKER"])

    tickers = sorted((low_missing & large & master_set) & set(c2c.columns))
    print(f"  universe: {len(tickers)} tickers (mcap>=500B, missing<={MISSING_RATE_MAX})")
    return tickers


# ---------------------------------------------------------------------------
# Clustering (reuse cache or recompute)
# ---------------------------------------------------------------------------
def load_or_compute_clusters(
    c2c: pd.DataFrame,
    tickers: list[str],
    df_master: pd.DataFrame,
    df_topix: pd.DataFrame,
) -> pd.Series:
    """Load cluster assignments from 011-1 cache, or recompute."""
    cache_path = PREV_OUTPUT_DIR / "clusters_2023.csv"
    if cache_path.exists():
        print(f"  [cache] clusters from {cache_path}")
        df_cl = pd.read_csv(cache_path, index_col=0, encoding="utf-8")
        cluster_id = df_cl["CLUSTER"]
        cluster_id.index = cluster_id.index.astype(str)
        # Filter to our universe
        valid = cluster_id.index.isin(tickers)
        cluster_id = cluster_id[valid]
        print(f"    {len(cluster_id)} tickers with cluster labels "
              f"({cluster_id.nunique()} clusters)")
        return cluster_id

    # Recompute: 3-factor OLS residuals -> Ward clustering
    print("  computing clusters from scratch (3-factor OLS)...")
    warm = c2c.loc[WARMUP_START:WARMUP_END, tickers].fillna(0.0)

    topix = df_topix.sort_values("DATE").copy()
    topix["MKT"] = np.log(topix["CLOSE"] / topix["CLOSE"].shift(1))
    topix = topix.set_index("DATE")["MKT"]
    mkt = topix.reindex(warm.index).fillna(0.0)

    ti = df_master.set_index("TICKER")
    ind_map = ti["INDUSTRY_33_CODE"].to_dict()
    size_map = ti["SIZE_CODE"].to_dict()

    ind_codes = sorted({ind_map[t] for t in tickers if ind_map.get(t) is not None})
    size_codes = sorted({size_map[t] for t in tickers if size_map.get(t) is not None})

    ind_factors = pd.DataFrame(index=warm.index)
    for code in ind_codes:
        members = [t for t in tickers if ind_map.get(t) == code]
        if len(members) >= 2:
            ind_factors[f"IND_{code}"] = warm[members].mean(axis=1)

    size_factors = pd.DataFrame(index=warm.index)
    for code in size_codes:
        members = [t for t in tickers if size_map.get(t) == code]
        if len(members) >= 2:
            size_factors[f"SIZE_{code}"] = warm[members].mean(axis=1)

    residuals = pd.DataFrame(index=warm.index, columns=tickers, dtype=float)
    groups: dict[tuple, list[str]] = {}
    for t in tickers:
        key = (ind_map.get(t), size_map.get(t))
        groups.setdefault(key, []).append(t)

    for (ind_code, size_code), members in groups.items():
        if ind_code is None or size_code is None:
            residuals[members] = warm[members].values
            continue
        ind_col = f"IND_{ind_code}"
        size_col = f"SIZE_{size_code}"
        cols_x = ["const", "MKT"]
        X = pd.DataFrame({"const": 1.0, "MKT": mkt.values}, index=warm.index)
        if ind_col in ind_factors.columns:
            X[ind_col] = ind_factors[ind_col].values
            cols_x.append(ind_col)
        if size_col in size_factors.columns:
            X[size_col] = size_factors[size_col].values
            cols_x.append(size_col)
        X_mat = X[cols_x].values
        Y = warm[members].values
        beta, *_ = np.linalg.lstsq(X_mat, Y, rcond=None)
        resid = Y - X_mat @ beta
        residuals[members] = resid

    residuals = residuals.astype(float)
    corr = residuals.corr().values
    np.fill_diagonal(corr, 1.0)
    corr = np.clip(corr, -1.0, 1.0)
    dist = 1.0 - corr
    dist = (dist + dist.T) / 2.0
    np.fill_diagonal(dist, 0.0)
    iu = np.triu_indices_from(dist, k=1)
    cond = np.clip(dist[iu], 0.0, None)
    link = linkage(cond, method="ward")
    labels = fcluster(link, t=N_CLUSTERS, criterion="maxclust")
    cluster_id = pd.Series(labels, index=tickers, name="CLUSTER")
    print(f"    {cluster_id.nunique()} clusters computed")
    return cluster_id


# ---------------------------------------------------------------------------
# Cluster representative returns
# ---------------------------------------------------------------------------
def build_cluster_returns(
    c2c: pd.DataFrame,
    cluster_id: pd.Series,
) -> pd.DataFrame:
    """Build 20-cluster representative returns (equal-weight average of members).

    Returns DataFrame: DATE x cluster_id (1..20).
    """
    tickers = cluster_id.index.tolist()
    c2c_sub = c2c.reindex(columns=tickers).fillna(0.0)

    cluster_ret = pd.DataFrame(index=c2c_sub.index)
    for cl in sorted(cluster_id.unique()):
        members = cluster_id[cluster_id == cl].index.tolist()
        valid = [t for t in members if t in c2c_sub.columns]
        if valid:
            cluster_ret[cl] = c2c_sub[valid].mean(axis=1)
        else:
            cluster_ret[cl] = 0.0

    print(f"  cluster returns panel: {cluster_ret.shape} "
          f"({len(cluster_ret.columns)} clusters x {len(cluster_ret)} days)")
    return cluster_ret


# ---------------------------------------------------------------------------
# V0 construction for 40-dim joint space
# ---------------------------------------------------------------------------
def build_V0_joint(n_clusters: int, cluster_id: pd.Series,
                   df_master: pd.DataFrame) -> np.ndarray:
    """Build 40 x K0 prior subspace for joint (lead, follow) space.

    v1: all 40 elements equal-weight (market)
    v2: lead=+1, follow=-1 (temporal spread)
    v3: cyclical clusters=+1, defensive=-1

    Cluster ordering: 1..n_clusters for lead, then 1..n_clusters for follow.
    """
    N = 2 * n_clusters  # 40

    # v1: market
    v1 = np.ones(N) / np.sqrt(N)

    # v2: lead=+1, follow=-1
    v2 = np.zeros(N)
    v2[:n_clusters] = 1.0 / n_clusters
    v2[n_clusters:] = -1.0 / n_clusters
    v2 = v2 - np.dot(v2, v1) * v1
    norm = np.linalg.norm(v2)
    if norm > 1e-10:
        v2 = v2 / norm

    # v3: cyclical/defensive based on majority industry in each cluster
    ind_map = df_master.set_index("TICKER")["INDUSTRY_33_CODE"].to_dict()
    clusters_sorted = sorted(cluster_id.unique())

    v3 = np.zeros(N)
    for i, cl in enumerate(clusters_sorted):
        members = cluster_id[cluster_id == cl].index.tolist()
        n_cyc = sum(1 for t in members if ind_map.get(t) in CYCLICAL_IND33)
        frac_cyc = n_cyc / max(len(members), 1)
        sign = 1.0 if frac_cyc >= 0.5 else -1.0
        v3[i] = sign                # lead side
        v3[n_clusters + i] = sign   # follow side

    # orthogonalise to v1, v2
    v3 = v3 - np.dot(v3, v1) * v1
    v3 = v3 - np.dot(v3, v2) * v2
    norm = np.linalg.norm(v3)
    if norm > 1e-10:
        v3 = v3 / norm

    V0 = np.column_stack([v1, v2, v3])
    Q, _ = np.linalg.qr(V0)
    print(f"  V0 shape: {Q.shape}")
    return Q


# ---------------------------------------------------------------------------
# Regularised PCA
# ---------------------------------------------------------------------------
def winsorise(X: np.ndarray, q: float = WINSOR_Q) -> np.ndarray:
    lo = np.nanquantile(X, q)
    hi = np.nanquantile(X, 1 - q)
    return np.clip(X, lo, hi)


def compute_C0(cluster_ret_warm: np.ndarray) -> np.ndarray:
    """Long-window target correlation for joint (lead_t-1, follow_t) pairs.

    Input: T x n_clusters array of cluster returns during warmup.
    Output: 2*n_clusters x 2*n_clusters correlation matrix.
    """
    nc = cluster_ret_warm.shape[1]
    T = cluster_ret_warm.shape[0] - 1

    Z_lead = cluster_ret_warm[:T]      # day t-1
    Z_follow = cluster_ret_warm[1:T+1]  # day t

    Z = np.concatenate([Z_lead, Z_follow], axis=1)  # T x 2*nc
    Z = Z - Z.mean(axis=0, keepdims=True)
    std = Z.std(axis=0, keepdims=True)
    std[std == 0] = 1.0
    Z = Z / std
    C0 = (Z.T @ Z) / max(Z.shape[0] - 1, 1)
    return C0


def regularised_predictor(
    C_window: np.ndarray,
    C0: np.ndarray,
    n_clusters: int,
    K: int,
    lam: float,
) -> np.ndarray:
    """Return n_clusters x n_clusters predictor B_t.

    Joint corr block structure (2*nc x 2*nc):
        [ C_lead,lead     C_lead,follow  ]
        [ C_follow,lead   C_follow,follow]

    Regularise, eigen-decompose top-K, split into lead/follow halves.
    Predictor B = V_follow @ V_lead.T (rank <= K).
    """
    C_reg = (1 - lam) * C_window + lam * C0
    C_reg = (C_reg + C_reg.T) / 2.0
    vals, vecs = np.linalg.eigh(C_reg)
    # top-K by magnitude
    order = np.argsort(vals)[::-1][:K]
    V = vecs[:, order]  # 2*nc x K
    V_lead = V[:n_clusters, :]
    V_follow = V[n_clusters:, :]
    B = V_follow @ V_lead.T  # nc x nc
    return B


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------
def compute_weekly_signals(
    cluster_ret: pd.DataFrame,
    lam: float,
) -> tuple[pd.DataFrame, dict]:
    """Compute weekly cluster-level signals.

    For each Friday in OOS:
        1. Rolling L=60 day window of lagged pairs (lead_t-1, follow_t)
        2. Regularised PCA -> B_t (20x20)
        3. Signal = B_t @ z_lead_friday

    Returns:
        sig_df: DataFrame indexed by signal_date (Fridays), columns = cluster IDs
        bt_snapshots: dict of B_t matrices for selected weeks
    """
    clusters_sorted = sorted(cluster_ret.columns)
    nc = len(clusters_sorted)
    all_dates = cluster_ret.index
    idx_of = {d: i for i, d in enumerate(all_dates)}

    # Warmup C0
    warm_mask = (all_dates >= WARMUP_START) & (all_dates <= WARMUP_END)
    warm_vals = winsorise(cluster_ret.values[warm_mask])
    C0 = compute_C0(warm_vals)
    print(f"  C0 shape: {C0.shape} (expected {2*nc} x {2*nc})")

    # Identify Fridays in OOS
    oos_mask = (all_dates >= OOS_START) & (all_dates <= OOS_END)
    oos_dates = all_dates[oos_mask]
    week_groups: dict[tuple, list] = {}
    for d in oos_dates:
        wk = (d.isocalendar().year, d.isocalendar().week)
        week_groups.setdefault(wk, []).append(d)

    signal_rows = []
    signal_dates = []
    bt_snapshots = {}

    for wk in sorted(week_groups.keys()):
        days = sorted(week_groups[wk])
        friday = days[-1]
        t_idx = idx_of[friday]

        if t_idx < WINDOW_L + 1:
            continue

        window_start = t_idx - WINDOW_L
        window_end = t_idx

        vals = cluster_ret.values
        # Lagged pairs: lead[t-1] paired with follow[t]
        Z_lead_win = winsorise(vals[window_start-1:window_end-1])
        Z_follow_win = winsorise(vals[window_start:window_end])

        Z = np.concatenate([Z_lead_win, Z_follow_win], axis=1)
        Z = Z - Z.mean(axis=0, keepdims=True)
        std = Z.std(axis=0, keepdims=True)
        std[std == 0] = 1.0
        Z = Z / std
        C_win = (Z.T @ Z) / max(Z.shape[0] - 1, 1)

        B = regularised_predictor(C_win, C0, nc, K_EIG, lam)

        # Signal input: Friday's cluster returns (known at Friday close)
        z_friday = vals[t_idx]
        z_friday = np.clip(z_friday,
                           np.nanquantile(z_friday, WINSOR_Q),
                           np.nanquantile(z_friday, 1 - WINSOR_Q))
        # Standardise using window lead-side statistics
        lead_mu = vals[window_start-1:window_end-1].mean(axis=0)
        lead_sig = vals[window_start-1:window_end-1].std(axis=0)
        lead_sig[lead_sig == 0] = 1.0
        z_std = (z_friday - lead_mu) / lead_sig

        z_hat = B @ z_std
        signal_rows.append(z_hat)
        signal_dates.append(friday)

        # Snapshot B_t for July 2024
        if friday >= pd.Timestamp("2024-07-01") and not bt_snapshots:
            bt_snapshots[str(friday.date())] = B.copy()

    sig_df = pd.DataFrame(signal_rows, index=pd.DatetimeIndex(signal_dates),
                          columns=clusters_sorted)
    print(f"  signal panel: {sig_df.shape} ({len(signal_dates)} weeks)")
    return sig_df, bt_snapshots


def compute_momentum_signal(cluster_ret: pd.DataFrame) -> pd.DataFrame:
    """Baseline: simple cluster momentum (prior-week cumulative return)."""
    clusters_sorted = sorted(cluster_ret.columns)
    all_dates = cluster_ret.index

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
        start = max(0, t_idx - 4)
        week_rets = cluster_ret.values[start:t_idx+1].sum(axis=0)
        rows.append(week_rets)
        dates.append(friday)

    return pd.DataFrame(rows, index=pd.DatetimeIndex(dates),
                        columns=clusters_sorted)


# ---------------------------------------------------------------------------
# Map cluster signal to individual stocks
# ---------------------------------------------------------------------------
def expand_signal_to_stocks(
    cluster_signal: pd.DataFrame,
    cluster_id: pd.Series,
) -> pd.DataFrame:
    """Expand cluster-level signal to individual stock level.

    Each stock in cluster k gets the same signal value as cluster k.

    Returns DataFrame: signal_date x ticker.
    """
    tickers = sorted(cluster_id.index.tolist())
    clusters_sorted = sorted(cluster_signal.columns)

    # Build ticker->cluster mapping
    result = pd.DataFrame(index=cluster_signal.index, columns=tickers, dtype=float)
    for cl in clusters_sorted:
        members = [t for t in cluster_id[cluster_id == cl].index if t in tickers]
        if members:
            for t in members:
                result[t] = cluster_signal[cl].values

    return result


# ---------------------------------------------------------------------------
# Backtest engine (weekly long-only)
# ---------------------------------------------------------------------------
def weekly_backtest(
    stock_signal: pd.DataFrame,
    c2c: pd.DataFrame,
    tickers: list[str],
    q: float,
    spread_bps: float,
    label: str,
) -> dict:
    """Weekly long-only backtest.

    Signal computed at Friday close. Entry at Monday open (next week).
    Hold through Friday close. Earn c2c returns for Mon-Fri of next week.
    """
    c2c_sub = c2c.reindex(columns=tickers).fillna(0.0)
    all_dates = c2c_sub.index

    oos_mask = (all_dates >= OOS_START) & (all_dates <= OOS_END)
    oos_dates = all_dates[oos_mask]
    week_groups: dict[tuple, list] = {}
    for d in oos_dates:
        wk = (d.isocalendar().year, d.isocalendar().week)
        week_groups.setdefault(wk, []).append(d)
    weeks_sorted = sorted(week_groups.keys())

    sig_fridays = sorted(stock_signal.index)
    n_select = max(1, int(round(q * len(tickers))))
    tickers_arr = np.array(tickers)

    weekly_gross = []
    weekly_cost = []
    weekly_dates = []
    prev_selection: set[str] = set()
    turnover_list = []

    for fri in sig_fridays:
        fri_wk = (fri.isocalendar().year, fri.isocalendar().week)
        try:
            fri_idx = weeks_sorted.index(fri_wk)
        except ValueError:
            continue
        if fri_idx + 1 >= len(weeks_sorted):
            continue
        next_wk = weeks_sorted[fri_idx + 1]
        next_days = week_groups[next_wk]

        # Get signal for this Friday, only for our tickers
        sig_vec = np.array([
            stock_signal.loc[fri, t] if t in stock_signal.columns else np.nan
            for t in tickers
        ])
        finite = np.isfinite(sig_vec)
        if finite.sum() < 10:
            continue
        sig_vec[~finite] = np.nanmin(sig_vec[finite]) - 1.0
        sig_vec = sig_vec - np.nanmean(sig_vec[finite])

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

        # Cost: round-trip on changed fraction
        rebal_cost = new_share * 2 * spread_bps / 10000.0

        # Weekly return
        held_names = list(selection)
        if not held_names:
            prev_selection = selection
            continue

        week_ret = 0.0
        for d in next_days:
            if d in c2c_sub.index:
                day_rets = c2c_sub.loc[d, held_names].values
                week_ret += float(np.nanmean(day_rets))

        weekly_gross.append(week_ret)
        weekly_cost.append(rebal_cost)
        weekly_dates.append(next_days[0])
        prev_selection = selection

    return {
        "weekly_gross": pd.Series(weekly_gross, index=pd.DatetimeIndex(weekly_dates)),
        "weekly_cost": pd.Series(weekly_cost, index=pd.DatetimeIndex(weekly_dates)),
        "n_select": n_select,
        "avg_turnover": float(np.mean(turnover_list)) if turnover_list else np.nan,
        "label": label,
    }


# ---------------------------------------------------------------------------
# Metrics (3-stage)
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
    for stage_name, series in [("gross", weekly_gross), ("post_cost", net)]:
        s = series.dropna()
        if len(s) == 0:
            continue
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
    print(f"=== PoC 011-3 cluster cross lead-lag start {ts0.isoformat()} ===")

    bq = get_bq_client()

    # Load data
    print("\n--- Loading data ---")
    data = load_data(bq)

    # Build c2c returns
    print("\n--- Building returns ---")
    c2c = build_c2c_returns(data["price"])
    print(f"  c2c panel: {c2c.shape}")

    # Universe
    print("\n--- Selecting universe ---")
    tickers = select_universe(c2c, data["price"], data["master"], data["shares"])
    if len(tickers) < 100:
        print(f"ERROR: universe too small ({len(tickers)})", file=sys.stderr)
        return 1

    # Clusters
    print("\n--- Loading/computing clusters ---")
    cluster_id = load_or_compute_clusters(c2c, tickers, data["master"], data["topix"])
    # Restrict tickers to those with cluster labels
    tickers = [t for t in tickers if t in cluster_id.index]
    print(f"  final universe: {len(tickers)} tickers in {cluster_id.loc[tickers].nunique()} clusters")

    # Cluster representative returns
    print("\n--- Building cluster representative returns ---")
    cluster_ret = build_cluster_returns(c2c, cluster_id.loc[tickers])

    # V0
    print("\n--- Building V0 (prior subspace) ---")
    V0 = build_V0_joint(cluster_id.loc[tickers].nunique(), cluster_id.loc[tickers],
                        data["master"])

    # Compute signals
    print("\n--- Computing regularised PCA cluster cross signal ---")
    sig_cluster_reg, bt_snapshots = compute_weekly_signals(cluster_ret, LAMBDA_REG)

    print("\n--- Computing plain PCA (lambda=0) cluster cross signal ---")
    sig_cluster_plain, _ = compute_weekly_signals(cluster_ret, 0.0)

    print("\n--- Computing cluster momentum baseline ---")
    sig_cluster_mom = compute_momentum_signal(cluster_ret)

    # Expand to stock level
    print("\n--- Expanding signals to stock level ---")
    stock_sig_reg = expand_signal_to_stocks(sig_cluster_reg, cluster_id.loc[tickers])
    stock_sig_plain = expand_signal_to_stocks(sig_cluster_plain, cluster_id.loc[tickers])
    stock_sig_mom = expand_signal_to_stocks(sig_cluster_mom, cluster_id.loc[tickers])

    # Run backtests
    all_metrics = []
    all_weekly_returns = {}
    sample_rows = []

    for q in (0.05, 0.10):
        print(f"\n{'='*60}")
        print(f"  q = {q}")
        print(f"{'='*60}")

        # (1) Regularised PCA
        res_reg = weekly_backtest(stock_sig_reg, c2c, tickers, q,
                                 SPREAD_BPS, "REG_PCA")
        all_metrics.extend(compute_metrics_3stage(
            res_reg["weekly_gross"], res_reg["weekly_cost"],
            "REG_PCA", q, res_reg["n_select"], res_reg["avg_turnover"]))

        # (2) Cluster momentum baseline
        res_mom = weekly_backtest(stock_sig_mom, c2c, tickers, q,
                                 SPREAD_BPS, "CLUSTER_MOM")
        all_metrics.extend(compute_metrics_3stage(
            res_mom["weekly_gross"], res_mom["weekly_cost"],
            "CLUSTER_MOM", q, res_mom["n_select"], res_mom["avg_turnover"]))

        # (3) Plain PCA (lambda=0)
        res_plain = weekly_backtest(stock_sig_plain, c2c, tickers, q,
                                   SPREAD_BPS, "PLAIN_PCA")
        all_metrics.extend(compute_metrics_3stage(
            res_plain["weekly_gross"], res_plain["weekly_cost"],
            "PLAIN_PCA", q, res_plain["n_select"], res_plain["avg_turnover"]))

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
        jul_fridays = [d for d in sig_cluster_reg.index
                       if d >= pd.Timestamp("2024-07-01")]
        if jul_fridays:
            sample_fri = jul_fridays[0]
            cl_sig = sig_cluster_reg.loc[sample_fri]
            ind_map = data["master"].set_index("TICKER")["INDUSTRY_33_CATEGORY"].to_dict()
            # expand to stocks and rank
            sig_vec = np.array([
                stock_sig_reg.loc[sample_fri, t]
                if t in stock_sig_reg.columns else np.nan
                for t in tickers
            ])
            sig_vec_dm = sig_vec - np.nanmean(sig_vec[np.isfinite(sig_vec)])
            n_sel = max(1, int(round(q * len(tickers))))
            top = np.argsort(-np.nan_to_num(sig_vec_dm, nan=-999))[:n_sel]
            for rank, i in enumerate(top, start=1):
                t = tickers[i]
                cl = cluster_id.get(t, "?")
                sample_rows.append({
                    "q": q,
                    "signal_date": sample_fri,
                    "rank": rank,
                    "ticker": t,
                    "cluster": int(cl) if isinstance(cl, (int, np.integer)) else cl,
                    "cluster_signal": float(cl_sig.get(cl, np.nan)),
                    "stock_signal": float(sig_vec_dm[i]),
                    "industry_33": ind_map.get(t, ""),
                })

    # Save B_t snapshot
    if bt_snapshots:
        for date_str, B in bt_snapshots.items():
            clusters_sorted = sorted(cluster_ret.columns)
            bt_df = pd.DataFrame(B, index=clusters_sorted, columns=clusters_sorted)
            bt_df.index.name = "from_cluster"
            bt_df.columns.name = "to_cluster"
            bt_path = OUTPUT_DIR / "cluster_signal_matrix.csv"
            bt_df.to_csv(bt_path, encoding="utf-8")
            print(f"\n  wrote {bt_path} (B_t snapshot {date_str})")
            # Print interpretation
            print(f"\n--- B_t matrix ({date_str}): top 5 cross-cluster propagation paths ---")
            flat = []
            for i, ci in enumerate(clusters_sorted):
                for j, cj in enumerate(clusters_sorted):
                    if i != j:
                        flat.append((ci, cj, abs(B[i, j]), B[i, j]))
            flat.sort(key=lambda x: x[2], reverse=True)
            for ci, cj, absv, val in flat[:5]:
                direction = "+" if val > 0 else "-"
                print(f"    cluster {cj} -> cluster {ci}: {direction}{absv:.4f}")

    # Persist
    metrics_df = pd.DataFrame(all_metrics)
    metrics_path = OUTPUT_DIR / "metrics_summary.csv"
    metrics_df.to_csv(metrics_path, index=False, encoding="utf-8")
    print(f"\n  wrote {metrics_path}")

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

    # Summary
    print("\n" + "=" * 80)
    print("METRICS SUMMARY")
    print("=" * 80)
    print(metrics_df.to_string(index=False))

    # Pass 1 judgement
    pass_flag = False
    best_sharpe = -999.0
    for _, row in metrics_df.iterrows():
        if row["stage"] == "post_tax" and row["strategy"] == "REG_PCA":
            sh = row["sharpe"]
            if sh > best_sharpe:
                best_sharpe = sh
            if sh >= 1.0:
                pass_flag = True
                print(f"\n>>> Pass 1 PASS: {row['strategy']} q={row['q']} "
                      f"post-tax Sharpe={sh:.2f}")
    if not pass_flag:
        print(f"\n>>> Pass 1 FAIL: best REG_PCA post-tax Sharpe = {best_sharpe:.2f} "
              f"(threshold = 1.0)")

    # Signal sample
    if len(sample_df) > 0:
        print("\n--- Signal sample (first Friday in July 2024) ---")
        print(sample_df.head(20).to_string(index=False))

    ts1 = datetime.now(tz=JST)
    elapsed = (ts1 - ts0).total_seconds()
    print(f"\n=== done in {elapsed:.1f}s ({ts1.isoformat()}) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

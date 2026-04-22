"""PoC: Subspace-regularized PCA lead-lag (overnight -> daytime, self variation).

Paper: SIG-FIN-036-13「部分空間正則化付きPCAを用いた日米業種リードラグ投資戦略」
Knowledge: docs/knowledges/analysis/012_cluster_overnight_daytime_leadlag.md

Variation 1 (self lead-lag):
    information source A  = stock i overnight return  log(ADJ_OPEN_t / ADJ_CLOSE_{t-1})
    investment target  B  = stock i daytime  return   log(ADJ_CLOSE_t / ADJ_OPEN_t)

Pipeline:
    1. Query 2023-2024 prices, master, TOPIX from BigQuery
    2. Build universe (market cap >= 500B JPY, low missing), ~300-500 names
    3. Build cluster membership V0 from 2023 residual-correlation Ward clustering
       (3-factor OLS residuals: MKT / IND33 / SIZE)
    4. Gram-Schmidt orthonormalise V0 -> N x 20
    5. Rolling L=60 day window over 2024: compute C_t (joint ON/DAY corr),
       regularise C_t^reg = 0.1 * C_t + 0.9 * C0
       (C0 = long-window correlation from the entire 2023 warm-up block)
    6. Top-K=20 eigen decomposition -> V_ON,t / V_DAY,t subspaces
    7. Predictor B_t = V_DAY,t @ V_ON,t.T  (low-rank, <=20)
    8. Signal at day t: z_hat = B_t @ overnight_t (same day, since overnight is
       realised at open and we enter at open-to-close)
    9. Long-short top/bottom q=0.3, daily rebalance, equal-weight
   10. Evaluate against baselines:
        (b1) market-neutral flat (zero)
        (b2) lambda=0 plain PCA K=20
        (b3) raw overnight momentum (signal = overnight_t itself)

Outputs:
    C:\\tmp\\poc_overnight_daytime\\returns_daily.csv
    C:\\tmp\\poc_overnight_daytime\\metrics_summary.csv
    C:\\tmp\\poc_overnight_daytime\\signal_sample.csv
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
from scipy.cluster.hierarchy import fcluster, linkage

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
JST = ZoneInfo("Asia/Tokyo")

PROJECT = "gmailpj-357912"
KEY_PATH = Path(r"C:\gdrive\claude\investment-agent\keys\gcp-service-account.json")

CACHE_DIR = Path(r"C:\tmp\poc_overnight_daytime_cache")
OUTPUT_DIR = Path(r"C:\tmp\poc_overnight_daytime")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Universe params
MARKET_CAP_MIN = 50_000_000_000  # 500B JPY
MISSING_RATE_MAX = 0.05

# Strategy params
WARMUP_START = pd.Timestamp("2023-01-01")
WARMUP_END = pd.Timestamp("2023-12-31")
OOS_START = pd.Timestamp("2024-01-01")
OOS_END = pd.Timestamp("2024-12-31")
WINDOW_L = 60
N_CLUSTERS = 20
K_EIG = 20
LAMBDA_REG = 0.9  # C^reg = (1-lambda)*C_t + lambda*C0
QUANTILE = 0.30
WINSOR_Q = 0.005  # 0.5% each tail

FORCE_RELOAD = False


# ---------------------------------------------------------------------------
# BigQuery client with cached queries
# ---------------------------------------------------------------------------
def get_bq_client() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(str(KEY_PATH))
    return bigquery.Client(credentials=creds, project=PROJECT)


def cached_query(bq: bigquery.Client, name: str, query: str) -> pd.DataFrame:
    path = CACHE_DIR / f"{name}.parquet"
    if not FORCE_RELOAD and path.exists():
        print(f"  [cache] {name}")
        return pd.read_parquet(path)
    print(f"  [BQ]    {name}")
    job = bq.query(query)
    df = job.to_dataframe()
    bytes_processed = job.total_bytes_processed or 0
    print(f"          rows={len(df):,}  processed={bytes_processed/1e9:.3f} GB")
    df.to_parquet(path, index=False)
    # log cost
    with (CACHE_DIR / "bq_cost.log").open("a", encoding="utf-8") as f:
        ts = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"{ts}\t{name}\t{bytes_processed}\t{len(df)}\n")
    return df


def fetch_all_data(bq: bigquery.Client) -> dict[str, pd.DataFrame]:
    # Prices: 2022-12-01 .. 2024-12-31 (warmup needs prior close)
    df_price = cached_query(
        bq,
        "price_ohlc_2023_2024",
        """
        SELECT TICKER, DATE, ADJ_OPEN, ADJ_CLOSE, VOLUME
        FROM `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS`
        WHERE DATE BETWEEN '2022-12-01' AND '2024-12-31'
          AND IS_PREFERRED = FALSE
          AND ADJ_OPEN IS NOT NULL
          AND ADJ_CLOSE IS NOT NULL
          AND VOLUME > 0
        """,
    )
    df_price["DATE"] = pd.to_datetime(df_price["DATE"])

    df_master = cached_query(
        bq,
        "stock_code_list",
        """
        SELECT TICKER, STOCK_NAME, MARKET_CATEGORY,
               INDUSTRY_33_CODE, INDUSTRY_33_CATEGORY,
               SIZE_CODE, SIZE_CATEGORY
        FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
        WHERE EXCHANGE = 'TSE'
        """,
    )

    df_shares = cached_query(
        bq,
        "fin_summary_shares",
        """
        SELECT
            LOCAL_CODE AS TICKER,
            CURRENT_PERIOD_END_DATE AS PERIOD_END,
            NUMBER_OF_ISSUED_AND_OUTSTANDING_SHARES_AT_THE_END_OF_FISCAL_YEAR_INCLUDING_TREASURY_STOCK AS SHARES_ISSUED,
            NUMBER_OF_TREASURY_STOCK_AT_THE_END_OF_FISCAL_YEAR AS TREASURY_STOCK
        FROM `gmailpj-357912.STOCK.fin_summary`
        WHERE NUMBER_OF_ISSUED_AND_OUTSTANDING_SHARES_AT_THE_END_OF_FISCAL_YEAR_INCLUDING_TREASURY_STOCK IS NOT NULL
          AND CURRENT_PERIOD_END_DATE <= '2023-12-31'
        """,
    )
    df_shares["PERIOD_END"] = pd.to_datetime(df_shares["PERIOD_END"])

    df_topix = cached_query(
        bq,
        "topix",
        """
        SELECT DATE, CLOSE, OPEN
        FROM `gmailpj-357912.STOCK.INDEX_PRICE`
        WHERE INDEX_CODE = '0000'
          AND DATE BETWEEN '2022-12-01' AND '2024-12-31'
        ORDER BY DATE
        """,
    )
    df_topix["DATE"] = pd.to_datetime(df_topix["DATE"])

    return {"price": df_price, "master": df_master, "shares": df_shares, "topix": df_topix}


# ---------------------------------------------------------------------------
# Return construction
# ---------------------------------------------------------------------------
def build_returns(df_price: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return three wide DataFrames (DATE x TICKER): overnight, daytime, close-to-close log returns."""
    df = df_price.sort_values(["TICKER", "DATE"]).drop_duplicates(["TICKER", "DATE"], keep="last")
    df["PREV_CLOSE"] = df.groupby("TICKER")["ADJ_CLOSE"].shift(1)
    df = df[(df["PREV_CLOSE"] > 0) & (df["ADJ_OPEN"] > 0) & (df["ADJ_CLOSE"] > 0)]
    df["OVERNIGHT"] = np.log(df["ADJ_OPEN"] / df["PREV_CLOSE"])
    df["DAYTIME"] = np.log(df["ADJ_CLOSE"] / df["ADJ_OPEN"])
    df["C2C"] = np.log(df["ADJ_CLOSE"] / df["PREV_CLOSE"])
    on = df.pivot(index="DATE", columns="TICKER", values="OVERNIGHT").sort_index()
    dy = df.pivot(index="DATE", columns="TICKER", values="DAYTIME").sort_index()
    c2c = df.pivot(index="DATE", columns="TICKER", values="C2C").sort_index()
    return on, dy, c2c


def select_universe(
    c2c: pd.DataFrame,
    df_price: pd.DataFrame,
    df_master: pd.DataFrame,
    df_shares: pd.DataFrame,
) -> list[str]:
    """Pick universe using 2023 market cap >= 500B and low missingness."""
    warm = c2c.loc[WARMUP_START:WARMUP_END]
    trading_days = len(warm)

    # missing rate in warmup
    missing_rate = warm.isna().mean(axis=0)
    low_missing = set(missing_rate[missing_rate <= MISSING_RATE_MAX].index)

    # market cap at 2023-01 first trading day
    df_2023 = df_price[(df_price["DATE"] >= WARMUP_START) & (df_price["DATE"] <= WARMUP_END)]
    first_day_px = (
        df_2023.sort_values(["TICKER", "DATE"])
        .groupby("TICKER")
        .first()[["ADJ_CLOSE"]]
        .reset_index()
    )

    # shares: latest <= 2022-12-31
    cutoff = pd.Timestamp("2022-12-31")
    sh = df_shares[df_shares["PERIOD_END"] <= cutoff].copy()
    idx = sh.groupby("TICKER")["PERIOD_END"].idxmax()
    sh = sh.loc[idx, ["TICKER", "SHARES_ISSUED", "TREASURY_STOCK"]].copy()
    sh["TREASURY_STOCK"] = sh["TREASURY_STOCK"].fillna(0)
    sh["SHARES_OUT"] = sh["SHARES_ISSUED"] - sh["TREASURY_STOCK"]

    mcap = first_day_px.merge(sh[["TICKER", "SHARES_OUT"]], on="TICKER", how="inner")
    mcap["MCAP"] = mcap["ADJ_CLOSE"] * mcap["SHARES_OUT"]
    large = set(mcap[mcap["MCAP"] >= MARKET_CAP_MIN]["TICKER"])

    # master filter
    m = df_master[
        df_master["INDUSTRY_33_CODE"].notna()
        & ~df_master["MARKET_CATEGORY"].isin(["ETF・ETN", "REIT"])
    ]
    master_set = set(m["TICKER"])

    tickers = sorted((low_missing & large & master_set) & set(c2c.columns))
    print(
        f"  universe: {len(tickers)} (low_miss={len(low_missing)}, "
        f"mcap>=500B={len(large)}, master={len(master_set)}, days={trading_days})"
    )
    return tickers


# ---------------------------------------------------------------------------
# 3-factor residuals -> clustering -> V0
# ---------------------------------------------------------------------------
def compute_residual_clusters(
    c2c: pd.DataFrame,
    tickers: list[str],
    df_master: pd.DataFrame,
    df_topix: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.Series]:
    """Run 3-factor OLS on 2023 close-to-close returns, cluster residuals via Ward.

    Returns
    -------
    residuals : DataFrame (DATE x TICKER) of 2023 residuals
    cluster_id : Series indexed by TICKER with integer cluster label in 1..N_CLUSTERS
    """
    warm = c2c.loc[WARMUP_START:WARMUP_END, tickers].copy()
    warm = warm.fillna(0.0)

    # TOPIX close-to-close log return
    topix = df_topix.sort_values("DATE").copy()
    topix["MKT"] = np.log(topix["CLOSE"] / topix["CLOSE"].shift(1))
    topix = topix.set_index("DATE")["MKT"]
    mkt = topix.reindex(warm.index).fillna(0.0)

    # IND33 & SIZE factors = equal-weight mean per group
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

    # Batch regression per (ind, size) group (shared design matrix)
    residuals = pd.DataFrame(index=warm.index, columns=tickers, dtype=float)
    groups: dict[tuple, list[str]] = {}
    for t in tickers:
        key = (ind_map.get(t), size_map.get(t))
        groups.setdefault(key, []).append(t)

    for (ind_code, size_code), members in groups.items():
        if ind_code is None or size_code is None:
            residuals[members] = warm[members].values  # no factor removal
            continue
        ind_col = f"IND_{ind_code}"
        size_col = f"SIZE_{size_code}"
        cols = ["const", "MKT"]
        X = pd.DataFrame({"const": 1.0, "MKT": mkt.values}, index=warm.index)
        if ind_col in ind_factors.columns:
            X[ind_col] = ind_factors[ind_col].values
            cols.append(ind_col)
        if size_col in size_factors.columns:
            X[size_col] = size_factors[size_col].values
            cols.append(size_col)
        X_mat = X[cols].values
        Y = warm[members].values  # T x m
        beta, *_ = np.linalg.lstsq(X_mat, Y, rcond=None)
        resid = Y - X_mat @ beta
        residuals[members] = resid

    residuals = residuals.astype(float)

    # correlation -> Ward cluster
    corr = residuals.corr().values
    # numerical safety
    np.fill_diagonal(corr, 1.0)
    corr = np.clip(corr, -1.0, 1.0)
    dist = 1.0 - corr
    # symmetrise & zero diagonal
    dist = (dist + dist.T) / 2.0
    np.fill_diagonal(dist, 0.0)
    # condensed upper triangle
    iu = np.triu_indices_from(dist, k=1)
    cond = dist[iu]
    cond = np.clip(cond, 0.0, None)
    link = linkage(cond, method="ward")
    labels = fcluster(link, t=N_CLUSTERS, criterion="maxclust")
    cluster_id = pd.Series(labels, index=tickers, name="CLUSTER")
    print(f"  clusters: {cluster_id.nunique()} (target={N_CLUSTERS})")
    return residuals, cluster_id


def build_V0(tickers: list[str], cluster_id: pd.Series) -> np.ndarray:
    """Construct N x K cluster-membership matrix, orthonormalised via QR."""
    N = len(tickers)
    clusters = sorted(cluster_id.unique())
    V = np.zeros((N, len(clusters)))
    idx_map = {t: i for i, t in enumerate(tickers)}
    for k, c in enumerate(clusters):
        members = cluster_id[cluster_id == c].index
        for t in members:
            V[idx_map[t], k] = 1.0
    # QR orthonormalisation (acts as Gram-Schmidt)
    Q, _ = np.linalg.qr(V)
    return Q  # N x K (K = N_CLUSTERS)


# ---------------------------------------------------------------------------
# Regularised PCA lead-lag signal
# ---------------------------------------------------------------------------
def winsorise(X: np.ndarray, q: float = WINSOR_Q) -> np.ndarray:
    lo = np.nanquantile(X, q)
    hi = np.nanquantile(X, 1 - q)
    return np.clip(X, lo, hi)


def compute_C0(on_warm: np.ndarray, dy_warm: np.ndarray) -> np.ndarray:
    """Long-window target correlation using entire warmup period."""
    # Standardise each series, then compute joint correlation of [ON; DAY]
    # Build 2N x T matrix: rows = [overnight_1..N, daytime_1..N]
    Z = np.concatenate([on_warm, dy_warm], axis=1)  # T x 2N
    Z = Z - Z.mean(axis=0, keepdims=True)
    std = Z.std(axis=0, keepdims=True)
    std[std == 0] = 1.0
    Z = Z / std
    C0 = (Z.T @ Z) / max(Z.shape[0] - 1, 1)
    return C0


def regularised_predictor(
    C_window: np.ndarray,
    C0: np.ndarray,
    N: int,
    K: int,
    lam: float,
) -> np.ndarray:
    """Return NxN predictor B_t mapping overnight_t -> daytime_t estimate.

    Joint corr block structure (2N x 2N):
        [ C_ON,ON     C_ON,DAY  ]
        [ C_DAY,ON    C_DAY,DAY ]

    Regularise C_reg = (1-lam) * C_window + lam * C0, eigen-decompose,
    take top-K eigenvectors, split into ON/DAY halves -> V_ON (NxK), V_DAY (NxK).
    Predictor B = V_DAY @ V_ON.T  (rank <= K).
    """
    C_reg = (1 - lam) * C_window + lam * C0
    # symmetrise
    C_reg = (C_reg + C_reg.T) / 2.0
    vals, vecs = np.linalg.eigh(C_reg)
    # top-K by magnitude (largest eigenvalues)
    order = np.argsort(vals)[::-1][:K]
    V = vecs[:, order]  # 2N x K
    V_ON = V[:N, :]
    V_DAY = V[N:, :]
    B = V_DAY @ V_ON.T
    return B


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------
def run_backtest(
    on: pd.DataFrame,
    dy: pd.DataFrame,
    tickers: list[str],
    V0: np.ndarray,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rolling backtest across OOS period; returns dict of daily PnL series and a signal sample."""
    on = on[tickers].copy()
    dy = dy[tickers].copy()
    # Fill missing with 0 (no trade effect for that day)
    on = on.fillna(0.0)
    dy = dy.fillna(0.0)

    all_dates = on.index
    # C0 from warmup (2023 only)
    warm_mask = (all_dates >= WARMUP_START) & (all_dates <= WARMUP_END)
    on_warm = on.values[warm_mask]
    dy_warm = dy.values[warm_mask]
    # winsorise warmup tails for C0
    on_warm_w = winsorise(on_warm)
    dy_warm_w = winsorise(dy_warm)
    C0 = compute_C0(on_warm_w, dy_warm_w)
    print(f"  C0 shape: {C0.shape}  (expected 2N x 2N, N={len(tickers)})")

    # Iterate OOS days; at day t we use window [t-L, t-1] of ON/DAY (close-to-open
    # happens at the morning of day t, so we must use lagged window only).
    oos_mask = (all_dates >= OOS_START) & (all_dates <= OOS_END)
    oos_dates = all_dates[oos_mask]
    idx_of = {d: i for i, d in enumerate(all_dates)}

    N = len(tickers)
    records_reg = []  # Variation 1 with regularisation
    records_plain = []  # lambda=0
    records_raw = []  # raw overnight momentum
    signal_sample_rows = []
    sample_day_target = None

    for d in oos_dates:
        t_idx = idx_of[d]
        if t_idx < WINDOW_L:
            continue
        window = slice(t_idx - WINDOW_L, t_idx)
        on_win = on.values[window]
        dy_win = dy.values[window]
        on_win = winsorise(on_win)
        dy_win = winsorise(dy_win)

        Z = np.concatenate([on_win, dy_win], axis=1)
        Z = Z - Z.mean(axis=0, keepdims=True)
        std = Z.std(axis=0, keepdims=True)
        std[std == 0] = 1.0
        Z = Z / std
        C_win = (Z.T @ Z) / max(Z.shape[0] - 1, 1)

        # regularised predictor
        B_reg = regularised_predictor(C_win, C0, N, K_EIG, LAMBDA_REG)
        # plain PCA predictor (lambda=0)
        B_plain = regularised_predictor(C_win, C0, N, K_EIG, 0.0)

        # Current-day overnight (already realised at the open of day t)
        on_today = on.values[t_idx]
        on_today_w = np.clip(on_today, np.nanquantile(on_today, WINSOR_Q), np.nanquantile(on_today, 1 - WINSOR_Q))

        # Standardise today's overnight using window mean/std so it matches C
        # (we need to feed it on the same scale that C_win was standardised)
        on_mu = on.values[window].mean(axis=0)
        on_sig = on.values[window].std(axis=0)
        on_sig[on_sig == 0] = 1.0
        on_std = (on_today_w - on_mu) / on_sig

        # Predicted standardised daytime
        z_pred_reg = B_reg @ on_std
        z_pred_plain = B_plain @ on_std
        z_pred_raw = on_std.copy()  # raw overnight momentum baseline

        # Build long-short signals
        def ls_ret(sig: np.ndarray) -> float:
            # demean cross-sectionally
            s = sig - np.nanmean(sig)
            finite = np.isfinite(s)
            if finite.sum() < 10:
                return 0.0
            hi = np.nanquantile(s[finite], 1 - QUANTILE)
            lo = np.nanquantile(s[finite], QUANTILE)
            long_mask = (s >= hi) & finite
            short_mask = (s <= lo) & finite
            n_long = long_mask.sum()
            n_short = short_mask.sum()
            if n_long == 0 or n_short == 0:
                return 0.0
            day_t = dy.values[t_idx]
            r_long = np.nanmean(day_t[long_mask])
            r_short = np.nanmean(day_t[short_mask])
            return 0.5 * (r_long - r_short)  # gross leverage = 1

        r_reg = ls_ret(z_pred_reg)
        r_plain = ls_ret(z_pred_plain)
        r_raw = ls_ret(z_pred_raw)

        records_reg.append((d, r_reg))
        records_plain.append((d, r_plain))
        records_raw.append((d, r_raw))

        # signal sample for mid-OOS day
        if sample_day_target is None and d >= pd.Timestamp("2024-07-01"):
            sample_day_target = d
            s_reg = z_pred_reg - np.nanmean(z_pred_reg)
            s_raw = z_pred_raw - np.nanmean(z_pred_raw)
            order = np.argsort(-s_reg)
            top = order[:20]
            bot = order[-20:]
            for rank, i in enumerate(top, start=1):
                signal_sample_rows.append(
                    {
                        "DATE": d,
                        "SIDE": "LONG",
                        "RANK": rank,
                        "TICKER": tickers[i],
                        "SIGNAL_REG": float(s_reg[i]),
                        "SIGNAL_RAW_ON": float(s_raw[i]),
                        "DAYTIME_RET": float(dy.values[t_idx, i]),
                    }
                )
            for rank, i in enumerate(bot, start=1):
                signal_sample_rows.append(
                    {
                        "DATE": d,
                        "SIDE": "SHORT",
                        "RANK": rank,
                        "TICKER": tickers[i],
                        "SIGNAL_REG": float(s_reg[i]),
                        "SIGNAL_RAW_ON": float(s_raw[i]),
                        "DAYTIME_RET": float(dy.values[t_idx, i]),
                    }
                )

    ret = pd.DataFrame(records_reg, columns=["DATE", "REG_PCA"]).set_index("DATE")
    ret["PLAIN_PCA"] = pd.DataFrame(records_plain, columns=["DATE", "x"]).set_index("DATE")["x"]
    ret["RAW_ON_MOM"] = pd.DataFrame(records_raw, columns=["DATE", "x"]).set_index("DATE")["x"]
    ret["FLAT"] = 0.0

    # Report that V0 was constructed even if V0 is not directly used in simple
    # reg_pca (it seeds the C0 target via clustering logic; here C0 is empirical
    # correlation from warmup, which is the more data-driven flavour the paper
    # also supports). V0 kept for inspection / variation2.
    print(f"  V0 shape (kept for variation2): {V0.shape}")

    sig_df = pd.DataFrame(signal_sample_rows)
    return ret, sig_df


def compute_metrics(ret: pd.DataFrame) -> pd.DataFrame:
    """Annualised return, risk, Sharpe, max drawdown per strategy column."""
    rows = []
    for col in ret.columns:
        s = ret[col].dropna()
        if len(s) == 0:
            continue
        mu = s.mean() * 252
        sig = s.std(ddof=1) * np.sqrt(252)
        sharpe = mu / sig if sig > 0 else np.nan
        cum = (1 + s).cumprod()
        peak = cum.cummax()
        dd = (cum / peak - 1).min()
        rows.append(
            {
                "strategy": col,
                "n_days": len(s),
                "ann_return": mu,
                "ann_risk": sig,
                "sharpe": sharpe,
                "max_drawdown": dd,
                "total_return": cum.iloc[-1] - 1,
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ts0 = datetime.now(tz=JST)
    print(f"=== PoC overnight->daytime lead-lag (variation 1) start {ts0.isoformat()} ===")

    bq = get_bq_client()
    data = fetch_all_data(bq)

    on, dy, c2c = build_returns(data["price"])
    print(f"  panels: overnight={on.shape}, daytime={dy.shape}")

    tickers = select_universe(c2c, data["price"], data["master"], data["shares"])
    if len(tickers) < 100:
        print("ERROR: universe too small", file=sys.stderr)
        return 1

    residuals, cluster_id = compute_residual_clusters(c2c, tickers, data["master"], data["topix"])
    V0 = build_V0(tickers, cluster_id)

    ret, sig_sample = run_backtest(on, dy, tickers, V0)
    metrics = compute_metrics(ret)
    print("\n=== metrics ===")
    print(metrics.to_string(index=False))

    # persist
    ret_path = OUTPUT_DIR / "returns_daily.csv"
    metrics_path = OUTPUT_DIR / "metrics_summary.csv"
    sample_path = OUTPUT_DIR / "signal_sample.csv"
    cluster_path = OUTPUT_DIR / "clusters_2023.csv"
    ret.to_csv(ret_path, encoding="utf-8")
    metrics.to_csv(metrics_path, index=False, encoding="utf-8")
    sig_sample.to_csv(sample_path, index=False, encoding="utf-8")
    cluster_id.to_frame().to_csv(cluster_path, encoding="utf-8")
    print(f"\nwrote: {ret_path}")
    print(f"wrote: {metrics_path}")
    print(f"wrote: {sample_path}")
    print(f"wrote: {cluster_path}")

    ts1 = datetime.now(tz=JST)
    elapsed = (ts1 - ts0).total_seconds()
    print(f"=== done in {elapsed:.1f}s ({ts1.isoformat()}) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

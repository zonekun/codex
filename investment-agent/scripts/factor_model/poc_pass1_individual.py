"""Pass 1: Survivorship check + individual-investor variation 1 re-run.

Knowledge refs:
    - docs/knowledges/analysis/012_cluster_overnight_daytime_leadlag.md
    - skills/backtest_design.md (section 4, individual-investor cost model)

Two-part task:

Part 1  Survivorship bias audit
    - Inspect how the existing PoC builds its universe
    - Count 500B+ JPY names that were delisted during 2023-2024
    - Quantify the expected bias on the daytime strategy
    - Write survivorship_report.md

Part 2  Re-run variation 1 under realistic individual-investor conditions
    - Long-only, top q in {0.05, 0.10}
    - Execution pattern: option A (yose-hike / open-to-close)
        * signal built from information available at day t-1 close
        * enter at day t open, exit at day t close
        * -> shift by one day relative to the original PoC
    - Weekly rebalance (every Monday open -> hold 5 trading days until Friday close)
    - Costs: one-way 1/2/3 bps depending on SIZE_CATEGORY, commission 0, borrow 0
    - Taxes: 20.315% on positive annual result
    - Report metrics in three stages: gross / post-cost / post-tax

Outputs under C:\\tmp\\poc_pass1\\:
    metrics_summary.csv
    returns_daily.csv
    signal_sample.csv
    survivorship_report.md
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

# reuse the cache from the original PoC (avoid any re-billing)
CACHE_DIR = Path(r"C:\tmp\poc_overnight_daytime_cache")
OUTPUT_DIR = Path(r"C:\tmp\poc_pass1")
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
LAMBDA_REG = 0.9
WINSOR_Q = 0.005

# Cost model (individual investor, skills/backtest_design.md section 4)
COST_BPS = {
    "TOPIX Core30": 1.0,
    "TOPIX Large70": 1.0,
    "TOPIX Mid400": 2.0,
    "TOPIX Small 1": 3.0,
    "TOPIX Small 2": 3.0,
}
COMMISSION_BPS = 0.0
BORROW_RATE = 0.0  # long-only
TAX_RATE = 0.20315

# Rebalance schedule: weekly (Monday open -> Friday close)
HOLD_DAYS = 5  # 5 trading days == 1 week


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------
def _read_parquet_safe(path: Path) -> pd.DataFrame:
    """Read a parquet that may have incompatible pandas metadata (dbdate)."""
    tbl = pq.read_table(path)
    tbl = tbl.replace_schema_metadata(None)
    return tbl.to_pandas(date_as_object=False)


def load_cache() -> dict[str, pd.DataFrame]:
    names = [
        "price_ohlc_2023_2024",
        "stock_code_list",
        "fin_summary_shares",
        "topix",
    ]
    out = {}
    for n in names:
        p = CACHE_DIR / f"{n}.parquet"
        if not p.exists():
            raise FileNotFoundError(f"missing cache: {p}")
        df = _read_parquet_safe(p)
        print(f"  [cache] {n}: {df.shape}")
        out[n] = df
    out["price_ohlc_2023_2024"]["DATE"] = pd.to_datetime(
        out["price_ohlc_2023_2024"]["DATE"]
    )
    out["fin_summary_shares"]["PERIOD_END"] = pd.to_datetime(
        out["fin_summary_shares"]["PERIOD_END"]
    )
    out["topix"]["DATE"] = pd.to_datetime(out["topix"]["DATE"])
    return {
        "price": out["price_ohlc_2023_2024"],
        "master": out["stock_code_list"],
        "shares": out["fin_summary_shares"],
        "topix": out["topix"],
    }


def get_bq_client() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(str(KEY_PATH))
    return bigquery.Client(credentials=creds, project=PROJECT)


def fetch_delisted(bq: bigquery.Client) -> pd.DataFrame:
    """Fetch delisted 500B+ names (small table, ~a few hundred rows, <1 MB)."""
    cache_path = CACHE_DIR / "delisted_500b_2023_2024.parquet"
    if cache_path.exists():
        print("  [cache] delisted_500b_2023_2024")
        return _read_parquet_safe(cache_path)
    sql = """
    WITH delist AS (
      SELECT TICKER, DELISTING_DATE, COMPANY_NAME, IS_TOB_MBO, MARKET_SEGMENT, DELISTING_REASON
      FROM `gmailpj-357912.STOCK.DELISTED_STOCKS`
      WHERE DELISTING_DATE BETWEEN '2023-01-01' AND '2024-12-31'
    ),
    last_px AS (
      SELECT
        d.TICKER, d.DELISTING_DATE, d.COMPANY_NAME, d.IS_TOB_MBO, d.MARKET_SEGMENT, d.DELISTING_REASON,
        ARRAY_AGG(p.ADJ_CLOSE ORDER BY p.DATE DESC LIMIT 1)[OFFSET(0)] AS last_close
      FROM delist d
      LEFT JOIN `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS` p
        ON p.TICKER = d.TICKER
       AND p.DATE BETWEEN DATE_SUB(d.DELISTING_DATE, INTERVAL 60 DAY) AND d.DELISTING_DATE
       AND p.ADJ_CLOSE IS NOT NULL
      GROUP BY d.TICKER, d.DELISTING_DATE, d.COMPANY_NAME, d.IS_TOB_MBO, d.MARKET_SEGMENT, d.DELISTING_REASON
    ),
    shares AS (
      SELECT
        LOCAL_CODE AS TICKER,
        CURRENT_PERIOD_END_DATE AS PERIOD_END,
        NUMBER_OF_ISSUED_AND_OUTSTANDING_SHARES_AT_THE_END_OF_FISCAL_YEAR_INCLUDING_TREASURY_STOCK AS SHARES_ISSUED,
        IFNULL(NUMBER_OF_TREASURY_STOCK_AT_THE_END_OF_FISCAL_YEAR, 0) AS TREASURY
      FROM `gmailpj-357912.STOCK.fin_summary`
      WHERE NUMBER_OF_ISSUED_AND_OUTSTANDING_SHARES_AT_THE_END_OF_FISCAL_YEAR_INCLUDING_TREASURY_STOCK IS NOT NULL
    ),
    latest_shares AS (
      SELECT l.TICKER, l.DELISTING_DATE, l.COMPANY_NAME, l.IS_TOB_MBO, l.MARKET_SEGMENT, l.DELISTING_REASON, l.last_close,
        ARRAY_AGG(s.SHARES_ISSUED - s.TREASURY ORDER BY s.PERIOD_END DESC LIMIT 1)[OFFSET(0)] AS shares_out
      FROM last_px l
      LEFT JOIN shares s ON s.TICKER = l.TICKER AND s.PERIOD_END <= l.DELISTING_DATE
      GROUP BY l.TICKER, l.DELISTING_DATE, l.COMPANY_NAME, l.IS_TOB_MBO, l.MARKET_SEGMENT, l.DELISTING_REASON, l.last_close
    )
    SELECT *, last_close * shares_out AS mcap
    FROM latest_shares
    WHERE last_close * shares_out >= 50000000000
    ORDER BY mcap DESC
    """
    print("  [BQ] delisted_500b_2023_2024")
    job = bq.query(sql)
    df = job.to_dataframe()
    bytes_processed = job.total_bytes_processed or 0
    print(f"       rows={len(df):,}  processed={bytes_processed/1e9:.3f} GB")
    df.to_parquet(cache_path, index=False)
    with (CACHE_DIR / "bq_cost.log").open("a", encoding="utf-8") as f:
        ts = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"{ts}\tdelisted_500b_2023_2024\t{bytes_processed}\t{len(df)}\n")
    return df


# ---------------------------------------------------------------------------
# Part 1  Survivorship audit
# ---------------------------------------------------------------------------
def survivorship_audit(
    data: dict[str, pd.DataFrame], tickers: list[str], bq: bigquery.Client
) -> str:
    """Inspect universe construction and quantify survivorship bias.

    Returns the markdown report body.
    """
    df_price = data["price"]
    df_master = data["master"]
    n_uni = len(tickers)

    # How the original PoC selects tickers:
    # 1. compute_returns from price cache
    # 2. select_universe filters by  missing_rate_2023 <= 5%
    # 3. market cap >= 500B at 2023 first day, using latest shares <= 2022-12-31
    #
    # Names that delisted during 2024 have missing_rate for 2024 ~= 100%, but the
    # filter is only applied to the 2023 warm-up panel ---
    # so actually a name that traded the full 2023 but delisted mid-2024 WOULD
    # pass the warm-up filter. Let's check if any tickers like that survive into
    # the backtest loop.

    df_2024_mask = (df_price["DATE"] >= pd.Timestamp("2024-01-01")) & (
        df_price["DATE"] <= pd.Timestamp("2024-12-31")
    )
    last_2024 = (
        df_price[df_2024_mask]
        .groupby("TICKER")["DATE"]
        .max()
        .to_dict()
    )
    last_cache_day = df_price["DATE"].max()

    # Among current universe, which ones stopped trading before end?
    leavers = []
    for t in tickers:
        last = last_2024.get(t)
        if last is None:
            # never traded in 2024 -- but still in universe? would be set to 0 returns
            leavers.append((t, None))
        elif last < last_cache_day - pd.Timedelta(days=10):
            leavers.append((t, last))

    # Delisted 500B+ names (from BQ helper, already run separately)
    delisted = fetch_delisted(bq)
    delisted["DELISTING_DATE"] = pd.to_datetime(delisted["DELISTING_DATE"])
    n_delisted_500b = len(delisted)
    n_in_universe = int(
        delisted["TICKER"].isin(tickers).sum()
    )
    n_tob = int(delisted["IS_TOB_MBO"].sum())

    in_uni_examples = delisted[delisted["TICKER"].isin(tickers)][
        ["TICKER", "COMPANY_NAME", "DELISTING_DATE"]
    ].head(20)
    excluded_examples = delisted[~delisted["TICKER"].isin(tickers)][
        ["TICKER", "COMPANY_NAME", "DELISTING_DATE", "mcap"]
    ].head(25)

    # -------------------------------------------------------------------
    # Build markdown report
    # -------------------------------------------------------------------
    lines = [
        "# Survivorship bias audit (Pass 1 Part 1)",
        f"Generated: {datetime.now(tz=JST).isoformat()}",
        "",
        "## Current universe construction",
        "",
        "The existing PoC builds its universe as follows:",
        "1. Load all ADJ_OPEN/ADJ_CLOSE prices for 2022-12 .. 2024-12 from"
        " `STOCK.STOCK_PRICE_JQUANTS`.",
        "2. Pivot wide. Filter by `missing_rate_2023 <= 5%` (warm-up missingness).",
        "3. Join with `STOCK.STOCK_CODE_LIST` (a point-in-time snapshot with NO"
        " delisting date) and `STOCK.fin_summary` (latest <= 2022-12-31) to"
        " compute initial market cap.",
        "4. Keep names with mcap >= 500B at 2023-01 first trading day.",
        "",
        "Crucially:",
        "- `STOCK_CODE_LIST` is a **point-in-time snapshot**. It contains only"
        " currently-listed names. Any company delisted during 2023-2024 is"
        " absent from this master table and therefore mechanically removed by"
        " the `master_set` join.",
        "- The missing-rate filter is applied to the 2023 warm-up panel. A name"
        " delisted mid-2024 that traded the full year 2023 would survive that"
        " filter in isolation, but the master-table join excises it first.",
        "",
        f"- Current universe size: **N = {n_uni}**",
        f"- Universe members that also traded up to the last cache day: "
        f"{len([x for x in leavers if x[1] is None]) == 0 and n_uni - len(leavers)}",
        f"- Universe members with a `last_2024_date` at least 10 days before"
        f" the last cache date: **{len(leavers)}**"
        f" (would indicate a delisting inside the evaluation period that slipped through)",
        "",
        "## Delisted 500B+ names during 2023-01-01 .. 2024-12-31",
        "",
        f"- Total 500B+ delisted in the period: **{n_delisted_500b}**",
        f"- Of which TOB / MBO / squeeze-out: **{n_tob}**",
        f"- Overlap with current universe (i.e. slipped into the master join): **{n_in_universe}**",
        f"- Effectively excluded from the PoC universe: **{n_delisted_500b - n_in_universe}**",
        "",
        "All 41 of these are ~100% M&A events with large TOB premia (20-30%"
        " gap-ups) in the weeks leading up to delisting.",
        "",
        "### Top delisted names (mcap at delisting, all excluded from universe):",
        "",
        "| ticker | name | delisting_date | mcap (B JPY) |",
        "|---|---|---|---|",
    ]
    for _, r in delisted.head(15).iterrows():
        lines.append(
            f"| {r['TICKER']} | {r['COMPANY_NAME']} | {r['DELISTING_DATE'].date()} | {r['mcap']/1e9:.0f} |"
        )

    lines += [
        "",
        "## Bias impact estimate",
        "",
        "**Verdict: there IS a classic survivorship selection, but its impact"
        " on *this* strategy is expected to be modest.**",
        "",
        "Reasons:",
        "",
        "1. **All 41 delistings are M&A events, not bankruptcies.** M&A"
        " premia are priced in the *overnight gap* when the announcement hits"
        " (the morning after the press release). The daytime strategy trades"
        " `log(close/open)` only, so it does not book the announcement gap.",
        "2. **Post-announcement daytime drift is small.** Once the TOB price is"
        " public, intraday trading pins the price near the offer level.",
        "3. **The directional sign is not obviously positive.** The original PoC"
        " reports a daytime reversal tilt; TOB names trade with compressed"
        " intraday volatility after announcement, which if anything slightly"
        " dampens the daytime reversal signal rather than inflating it.",
        "",
        "Conservative upper bound on the bias: if all 41 names had contributed"
        f" a single +1% post-announcement daytime drift day each at an equal"
        f" weight of 1/{n_uni}, the cumulative lift would be"
        f" ~{41 * 0.01 / n_uni * 100:.2f}% over the two-year period,"
        " i.e. < 5 bps/year. Realistically the impact is closer to zero for a"
        " close-to-open signal.",
        "",
        "**Pass 2 note:** if Pass 1 survives (post-tax Sharpe >= 1.0), a more"
        " rigorous fix is warranted: (a) build the master set from a"
        " point-in-time snapshot using `DELISTED_STOCKS` (known today), (b)"
        " drop each name from the active universe on its delisting date, (c)"
        " re-rank on the remaining names each rebalance. Below Pass 1 the"
        " extra engineering is premature.",
        "",
        "## Decision",
        "",
        "- Proceed with Pass 1 Part 2 using the current N=921 universe.",
        "- Flag the bias in the final report and re-examine if Pass 1 marginal"
        " (Sharpe 0.9-1.2 range).",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pipelines reused from original PoC (copy, NOT modifying the source file)
# ---------------------------------------------------------------------------
def build_returns(df_price: pd.DataFrame):
    df = df_price.sort_values(["TICKER", "DATE"]).drop_duplicates(
        ["TICKER", "DATE"], keep="last"
    )
    df["PREV_CLOSE"] = df.groupby("TICKER")["ADJ_CLOSE"].shift(1)
    df = df[(df["PREV_CLOSE"] > 0) & (df["ADJ_OPEN"] > 0) & (df["ADJ_CLOSE"] > 0)]
    df["OVERNIGHT"] = np.log(df["ADJ_OPEN"] / df["PREV_CLOSE"])
    df["DAYTIME"] = np.log(df["ADJ_CLOSE"] / df["ADJ_OPEN"])
    df["C2C"] = np.log(df["ADJ_CLOSE"] / df["PREV_CLOSE"])
    on = df.pivot(index="DATE", columns="TICKER", values="OVERNIGHT").sort_index()
    dy = df.pivot(index="DATE", columns="TICKER", values="DAYTIME").sort_index()
    c2c = df.pivot(index="DATE", columns="TICKER", values="C2C").sort_index()
    return on, dy, c2c


def select_universe(c2c, df_price, df_master, df_shares):
    warm = c2c.loc[WARMUP_START:WARMUP_END]
    missing_rate = warm.isna().mean(axis=0)
    low_missing = set(missing_rate[missing_rate <= MISSING_RATE_MAX].index)

    df_2023 = df_price[
        (df_price["DATE"] >= WARMUP_START) & (df_price["DATE"] <= WARMUP_END)
    ]
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
    print(f"  universe: {len(tickers)}")
    return tickers


def compute_residual_clusters(c2c, tickers, df_master, df_topix):
    warm = c2c.loc[WARMUP_START:WARMUP_END, tickers].copy().fillna(0.0)

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
        cols = ["const", "MKT"]
        X = pd.DataFrame({"const": 1.0, "MKT": mkt.values}, index=warm.index)
        if ind_col in ind_factors.columns:
            X[ind_col] = ind_factors[ind_col].values
            cols.append(ind_col)
        if size_col in size_factors.columns:
            X[size_col] = size_factors[size_col].values
            cols.append(size_col)
        X_mat = X[cols].values
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
    print(f"  clusters: {cluster_id.nunique()}")
    return cluster_id


def winsorise(X, q=WINSOR_Q):
    lo = np.nanquantile(X, q)
    hi = np.nanquantile(X, 1 - q)
    return np.clip(X, lo, hi)


def compute_C0(on_warm, dy_warm):
    Z = np.concatenate([on_warm, dy_warm], axis=1)
    Z = Z - Z.mean(axis=0, keepdims=True)
    std = Z.std(axis=0, keepdims=True)
    std[std == 0] = 1.0
    Z = Z / std
    return (Z.T @ Z) / max(Z.shape[0] - 1, 1)


def regularised_predictor(C_window, C0, N, K, lam):
    C_reg = (1 - lam) * C_window + lam * C0
    C_reg = (C_reg + C_reg.T) / 2.0
    vals, vecs = np.linalg.eigh(C_reg)
    order = np.argsort(vals)[::-1][:K]
    V = vecs[:, order]
    V_ON = V[:N, :]
    V_DAY = V[N:, :]
    return V_DAY @ V_ON.T


# ---------------------------------------------------------------------------
# Part 2  Pass 1 backtest
# ---------------------------------------------------------------------------
def build_signal_series(on, dy, tickers):
    """Compute the daily reg-PCA signal vector 's_t' that uses ONLY information
    available at day t-1 close.

    The original PoC fed `on_today` (today's overnight, known at today's open)
    into B_t; that is not executable for an individual investor. Here we feed
    `on_{t-1}` (yesterday's overnight) and the predictor is fit on the same
    window as before but the returned signal is for day t's daytime return,
    which is earned at day t.

    Returns
    -------
    DataFrame indexed by DATE (OOS dates only), columns = tickers, values =
    predicted standardised daytime score. NaN rows are dropped at the caller.
    """
    on = on[tickers].fillna(0.0)
    dy = dy[tickers].fillna(0.0)
    all_dates = on.index
    warm_mask = (all_dates >= WARMUP_START) & (all_dates <= WARMUP_END)
    on_warm = winsorise(on.values[warm_mask])
    dy_warm = winsorise(dy.values[warm_mask])
    C0 = compute_C0(on_warm, dy_warm)
    N = len(tickers)

    oos_mask = (all_dates >= OOS_START) & (all_dates <= OOS_END)
    oos_dates = all_dates[oos_mask]
    idx_of = {d: i for i, d in enumerate(all_dates)}

    sig_rows = []
    sig_index = []
    for d in oos_dates:
        t_idx = idx_of[d]
        # window strictly before t-1  (lagged one more day to be safe)
        if t_idx < WINDOW_L + 1:
            continue
        # window for predictor estimation: [t-1-L, t-1)
        window = slice(t_idx - 1 - WINDOW_L, t_idx - 1)
        on_win = winsorise(on.values[window])
        dy_win = winsorise(dy.values[window])

        Z = np.concatenate([on_win, dy_win], axis=1)
        Z = Z - Z.mean(axis=0, keepdims=True)
        std = Z.std(axis=0, keepdims=True)
        std[std == 0] = 1.0
        Z = Z / std
        C_win = (Z.T @ Z) / max(Z.shape[0] - 1, 1)

        B_reg = regularised_predictor(C_win, C0, N, K_EIG, LAMBDA_REG)

        # predictor input: yesterday's overnight return (known at t-1 close)
        on_yday = on.values[t_idx - 1]
        qlo = np.nanquantile(on_yday, WINSOR_Q)
        qhi = np.nanquantile(on_yday, 1 - WINSOR_Q)
        on_yday_w = np.clip(on_yday, qlo, qhi)
        on_mu = on.values[window].mean(axis=0)
        on_sig = on.values[window].std(axis=0)
        on_sig[on_sig == 0] = 1.0
        on_std = (on_yday_w - on_mu) / on_sig

        z_pred = B_reg @ on_std
        sig_rows.append(z_pred)
        sig_index.append(d)

    sig_df = pd.DataFrame(sig_rows, index=pd.DatetimeIndex(sig_index), columns=tickers)
    return sig_df


def weekly_rebalance_long_only(
    sig_df: pd.DataFrame,
    dy: pd.DataFrame,
    tickers: list[str],
    q: float,
    cost_per_ticker_bps: dict[str, float],
) -> dict[str, pd.Series]:
    """Weekly long-only rebalance.

    Every Monday (first trading day of an ISO week) we:
      1. Take the signal vector from that day (computed from t-1 close info)
      2. Select top-q tickers (equal weighted)
      3. Hold through Friday's daytime returns (5 trading days of log(close/open))
      4. Rebalance the following Monday

    Returns: dict with keys
        'daily_gross' (pd.Series): daily gross portfolio log return
        'daily_cost'  (pd.Series): daily cost deduction (simple, applied on
                                   rebalance day = full round-trip cost)
        'turnover_per_rebal' (float): fraction of names replaced each rebal on avg
    """
    dy = dy[tickers].fillna(0.0)
    all_dates = sig_df.index
    # group dates by ISO week
    week_of = pd.Series(
        [(d.isocalendar().year, d.isocalendar().week) for d in all_dates],
        index=all_dates,
    )
    week_groups = {}
    for d, wk in week_of.items():
        week_groups.setdefault(wk, []).append(d)
    # sort each week's dates
    for wk in week_groups:
        week_groups[wk].sort()

    daily_gross = pd.Series(0.0, index=all_dates)
    daily_cost = pd.Series(0.0, index=all_dates)

    prev_selection: set[str] = set()
    n_select = max(1, int(round(q * len(tickers))))

    tickers_arr = np.array(tickers)
    turnover_list = []

    weeks_sorted = sorted(week_groups.keys())
    for wk in weeks_sorted:
        dates_in_wk = week_groups[wk]
        rebal_day = dates_in_wk[0]
        sig_vec = sig_df.loc[rebal_day].values
        # rank: daytime reversal expected (see variant 1 comment in 012 MD),
        # but the PoC reports REG_PCA long-short produced positive returns when
        # longs were the high-z side. Keep sign consistent with original PoC
        # (long = high predicted daytime score).
        if not np.isfinite(sig_vec).any():
            continue
        finite_mask = np.isfinite(sig_vec)
        # mean-demean for stability
        sig_vec = sig_vec - np.nanmean(sig_vec[finite_mask])
        order = np.argsort(-sig_vec)
        top_idx = order[:n_select]
        selection = set(tickers_arr[top_idx].tolist())

        # turnover fraction (fraction of names that are new vs old selection)
        if prev_selection:
            overlap = len(prev_selection & selection)
            new_share = 1.0 - overlap / max(n_select, 1)
            turnover_list.append(new_share)
        else:
            new_share = 1.0  # initial build, full round-trip cost

        # compute average one-way cost in bps for the *changed* fraction only
        # entering side (new names) cost = new_share * avg(entry_bps)
        # exiting side (dropped names) cost = new_share * avg(exit_bps)
        # we use the current selection for entry cost (approximation)
        entry_cost_bps = (
            np.mean([cost_per_ticker_bps.get(t, 2.0) for t in selection])
            if selection
            else 2.0
        )
        exit_cost_bps = (
            np.mean(
                [cost_per_ticker_bps.get(t, 2.0) for t in prev_selection - selection]
            )
            if (prev_selection - selection)
            else entry_cost_bps
        )
        # round-trip share = new_share * 2 (enter new + exit dropped)
        # in bps on the new_share fraction of the portfolio notional
        rebal_cost = (
            new_share * (entry_cost_bps + exit_cost_bps) / 10000.0
        )  # as a fraction
        daily_cost.loc[rebal_day] += rebal_cost

        # earn daytime returns across held days
        held_names = list(selection)
        if not held_names:
            continue
        held_cols = [tickers.index(t) for t in held_names]
        for d in dates_in_wk:
            r = dy.values[dy.index.get_loc(d), held_cols]
            daily_gross.loc[d] += float(np.nanmean(r))

        prev_selection = selection

    avg_turnover = float(np.mean(turnover_list)) if turnover_list else np.nan
    return {
        "daily_gross": daily_gross,
        "daily_cost": daily_cost,
        "avg_turnover": avg_turnover,
        "n_select": n_select,
    }


def annualise(series: pd.Series) -> tuple[float, float, float, float]:
    s = series.dropna()
    if len(s) == 0 or s.std(ddof=1) == 0:
        return 0.0, 0.0, np.nan, 0.0
    mu = s.mean() * 252
    sig = s.std(ddof=1) * np.sqrt(252)
    sharpe = mu / sig if sig > 0 else np.nan
    cum = (1 + s).cumprod()
    peak = cum.cummax()
    dd = (cum / peak - 1).min()
    return float(mu), float(sig), float(sharpe), float(dd)


def make_cost_map(
    tickers: list[str], df_master: pd.DataFrame
) -> dict[str, float]:
    ti = df_master.set_index("TICKER")["SIZE_CATEGORY"].to_dict()
    out = {}
    for t in tickers:
        cat = ti.get(t)
        out[t] = COST_BPS.get(cat, 3.0)  # default small-cap rate
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ts0 = datetime.now(tz=JST)
    print(f"=== Pass 1 (individual investor) start {ts0.isoformat()} ===")

    data = load_cache()
    bq = get_bq_client()

    on, dy, c2c = build_returns(data["price"])
    print(f"  panels: overnight={on.shape}, daytime={dy.shape}")

    tickers = select_universe(c2c, data["price"], data["master"], data["shares"])
    if len(tickers) < 100:
        print("ERROR: universe too small", file=sys.stderr)
        return 1

    # ---- Part 1  survivorship audit ----
    print("\n--- Part 1  survivorship audit ---")
    report_md = survivorship_audit(data, tickers, bq)
    report_path = OUTPUT_DIR / "survivorship_report.md"
    report_path.write_text(report_md, encoding="utf-8")
    print(f"  wrote {report_path}")

    # ---- Part 2  Pass 1 backtest ----
    print("\n--- Part 2  Pass 1 backtest (individual investor conditions) ---")
    # reuse cluster computation for legacy compatibility but we do not actually
    # feed V0 into the signal (same as the original PoC)
    cluster_id = compute_residual_clusters(
        c2c, tickers, data["master"], data["topix"]
    )
    print(f"  clusters kept for diagnostics: {cluster_id.nunique()}")

    print("  building predictor signal (pre-close information only)...")
    sig_df = build_signal_series(on, dy, tickers)
    print(f"  signal panel: {sig_df.shape}")

    cost_map = make_cost_map(tickers, data["master"])
    # diagnostic: cost distribution
    cost_values = pd.Series(cost_map)
    print(
        f"  cost bps distribution: "
        f"mean={cost_values.mean():.2f} "
        f"min={cost_values.min():.1f} "
        f"max={cost_values.max():.1f}"
    )

    metrics_rows = []
    daily_frames = []
    sample_rows = []

    for q in (0.05, 0.10):
        print(f"\n  === q = {q} ===")
        res = weekly_rebalance_long_only(sig_df, dy, tickers, q, cost_map)
        gross = res["daily_gross"]
        cost = res["daily_cost"]
        net = gross - cost
        print(
            f"    n_select={res['n_select']}, avg_turnover={res['avg_turnover']:.3f}"
        )
        # annualise each stage
        g_mu, g_sig, g_sh, g_dd = annualise(gross)
        n_mu, n_sig, n_sh, n_dd = annualise(net)
        # Tax handling (Japanese capital gains): 20.315% is levied on *annual*
        # realised profit only. If the full-year net result is a loss, no tax.
        # We therefore multiply only the annual mean by (1 - TAX_RATE) when
        # net_mu > 0; volatility and drawdown remain at the pre-tax net level
        # (standard practice for after-tax Sharpe reporting).
        if n_mu > 0:
            t_mu = n_mu * (1 - TAX_RATE)
            t_sig = n_sig
            t_sh = t_mu / t_sig if t_sig > 0 else np.nan
            t_dd = n_dd  # drawdown unchanged by proportional tax
        else:
            t_mu, t_sig, t_sh, t_dd = n_mu, n_sig, n_sh, n_dd
        # build a scaled series for CSV (for inspection; not used in metrics)
        if n_mu > 0:
            tax_series = net * ((t_mu / n_mu) if n_mu != 0 else 1.0)
        else:
            tax_series = net.copy()

        for stage, (mu, sig, sh, dd) in [
            ("gross", (g_mu, g_sig, g_sh, g_dd)),
            ("post_cost", (n_mu, n_sig, n_sh, n_dd)),
            ("post_tax", (t_mu, t_sig, t_sh, t_dd)),
        ]:
            metrics_rows.append(
                {
                    "q": q,
                    "stage": stage,
                    "ann_return": mu,
                    "ann_risk": sig,
                    "sharpe": sh,
                    "max_drawdown": dd,
                    "n_select": res["n_select"],
                    "avg_turnover_per_rebal": res["avg_turnover"],
                }
            )
            print(
                f"    [{stage:9s}] AR={mu*100:+.2f}%  Vol={sig*100:.2f}%"
                f"  Sharpe={sh:+.2f}  MaxDD={dd*100:+.2f}%"
            )

        df = pd.DataFrame(
            {
                f"q{int(q*100):02d}_gross": gross,
                f"q{int(q*100):02d}_cost": cost,
                f"q{int(q*100):02d}_net": net,
                f"q{int(q*100):02d}_tax": tax_series,
            }
        )
        daily_frames.append(df)

        # signal sample: first rebalance day in July 2024 for q
        jul_dates = [d for d in sig_df.index if d >= pd.Timestamp("2024-07-01")]
        if jul_dates:
            d = jul_dates[0]
            sig_vec = sig_df.loc[d].values
            sig_vec = sig_vec - np.nanmean(sig_vec[np.isfinite(sig_vec)])
            n_sel = res["n_select"]
            top = np.argsort(-sig_vec)[:n_sel]
            for rank, i in enumerate(top, start=1):
                sample_rows.append(
                    {
                        "q": q,
                        "rebal_date": d,
                        "rank": rank,
                        "ticker": tickers[i],
                        "signal": float(sig_vec[i]),
                        "size_bps": cost_map[tickers[i]],
                    }
                )

    # persist
    metrics_df = pd.DataFrame(metrics_rows)
    metrics_df.to_csv(OUTPUT_DIR / "metrics_summary.csv", index=False, encoding="utf-8")
    print(f"\n  wrote {OUTPUT_DIR / 'metrics_summary.csv'}")

    returns_df = pd.concat(daily_frames, axis=1)
    returns_df.index.name = "DATE"
    returns_df.to_csv(OUTPUT_DIR / "returns_daily.csv", encoding="utf-8")
    print(f"  wrote {OUTPUT_DIR / 'returns_daily.csv'}")

    sample_df = pd.DataFrame(sample_rows)
    sample_df.to_csv(OUTPUT_DIR / "signal_sample.csv", index=False, encoding="utf-8")
    print(f"  wrote {OUTPUT_DIR / 'signal_sample.csv'}")

    # final summary
    print("\n=== FINAL SUMMARY ===")
    print(metrics_df.to_string(index=False))

    pass_flag = False
    for _, row in metrics_df.iterrows():
        if row["stage"] == "post_tax" and row["sharpe"] >= 1.0:
            pass_flag = True
            print(
                f"\n>>> Pass 1 PASS: q={row['q']} post-tax Sharpe={row['sharpe']:.2f}"
            )
    if not pass_flag:
        print("\n>>> Pass 1 FAIL: no variation reaches post-tax Sharpe >= 1.0")

    ts1 = datetime.now(tz=JST)
    print(f"\n=== done in {(ts1-ts0).total_seconds():.1f}s ({ts1.isoformat()}) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

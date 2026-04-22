"""PoC: 011-4b US Sector ETF -> Japan Sector ETF lead-lag LONG-SHORT version.

Paper: SIG-FIN-036-13
Knowledge: docs/knowledges/analysis/011-4_us_japan_sector_leadlag.md
Base: scripts/factor_model/poc_011_4_us_japan_sector.py (Pass 1a, long-only)

Pass 1b: Long-Short version
    - Long top-q sectors, Short bottom-q sectors (equal weight)
    - Sigma(w) = 0, Sigma(|w|) = 2 (market-neutral)
    - q candidates: top/bottom 3, 5, 6
    - Borrow cost: 0.75% annualised (ETF short selling)
    - Daily + Weekly rebalance

Pipeline (same as Pass 1a, data from cache):
    1. Load US sector ETF prices from cache CSV
    2. Load Japan TOPIX-17 ETF prices from cache parquet
    3. Align to common business days (US close day t -> JP next biz day t+1)
    4. Build V0 (K0=3): global, country-spread, cyclical/defensive
    5. Compute C0 from warm-up period (2020-2022)
    6. Rolling L=60 window: C_t^reg = 0.1*C_t + 0.9*C0
    7. Top-K=3 eigenvectors -> B_t = V_JP @ V_US.T
    8. Signal: z_hat_JP = B_t @ z_US,t -> long top-q, short bottom-q
    9. Evaluate: top/bottom 3/5/6, daily/weekly rebalance
   10. Baselines: FLAT, simple US momentum (L/S), lambda=0 PCA (L/S)

Outputs:
    C:\\tmp\\poc_011_4b_us_japan_longshort\\metrics_summary.csv
    C:\\tmp\\poc_011_4b_us_japan_longshort\\returns_daily.csv
    C:\\tmp\\poc_011_4b_us_japan_longshort\\returns_weekly.csv
    C:\\tmp\\poc_011_4b_us_japan_longshort\\signal_sample.csv
"""
from __future__ import annotations

import sys
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
JST = ZoneInfo("Asia/Tokyo")

CACHE_DIR_1A = Path(r"C:\tmp\poc_011_4_us_japan_sector")  # Pass 1a cache
OUTPUT_DIR = Path(r"C:\tmp\poc_011_4b_us_japan_longshort")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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

# Strategy params
WARMUP_START = pd.Timestamp("2020-01-01")
WARMUP_END = pd.Timestamp("2022-12-31")
OOS_START = pd.Timestamp("2023-01-01")
OOS_END = pd.Timestamp("2024-12-31")
WINDOW_L = 60
K_EIG = 3
LAMBDA_REG = 0.9  # C^reg = (1-lambda)*C_t + lambda*C0

# Cost / tax
ONEWAY_COST_BPS = 1  # ETF large-cap, high liquidity
BORROW_RATE_ANN = 0.0075  # 0.75% annualised borrow cost for ETF short selling
TAX_RATE = 0.20315

# Top/Bottom N candidates for long-short
Q_LIST = [3, 5, 6]

import structlog

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Data loading (from Pass 1a cache)
# ---------------------------------------------------------------------------
def load_us_data() -> pd.DataFrame:
    """Load US sector ETF daily prices from Pass 1a cache."""
    cache_path = CACHE_DIR_1A / "us_sector_prices.csv"
    if not cache_path.exists():
        raise FileNotFoundError(f"US cache not found: {cache_path}. Run Pass 1a first.")
    log.info("US data from cache", path=str(cache_path))
    df = pd.read_csv(cache_path, parse_dates=["Date"], encoding="utf-8")
    return df


def load_jp_data() -> pd.DataFrame:
    """Load Japan TOPIX-17 ETF daily prices from Pass 1a cache."""
    cache_path = CACHE_DIR_1A / "jp_sector_prices.parquet"
    if not cache_path.exists():
        raise FileNotFoundError(f"JP cache not found: {cache_path}. Run Pass 1a first.")
    log.info("JP data from cache", path=str(cache_path))
    return pd.read_parquet(cache_path)


# ---------------------------------------------------------------------------
# Return construction (same as Pass 1a)
# ---------------------------------------------------------------------------
def build_us_returns(df_us: pd.DataFrame) -> pd.DataFrame:
    """Build US sector close-to-close log returns (wide: date x ticker)."""
    df = df_us.copy()
    df = df.sort_values(["Ticker", "Date"]).drop_duplicates(["Ticker", "Date"], keep="last")
    pivot_close = df.pivot(index="Date", columns="Ticker", values="Close").sort_index()
    pivot_close = pivot_close.ffill(limit=3)
    log_ret = np.log(pivot_close / pivot_close.shift(1))
    log_ret = log_ret[US_TICKERS]
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

    us_dates_aligned = pd.DatetimeIndex([p[0] for p in pairs])
    jp_dates_aligned = pd.DatetimeIndex([p[1] for p in pairs])

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
        total_pairs=len(pairs),
        us_range=f"{us_dates_aligned[0].date()}..{us_dates_aligned[-1].date()}",
        jp_range=f"{jp_dates_aligned[0].date()}..{jp_dates_aligned[-1].date()}",
    )

    return us_aligned, jp_aligned, date_info


# ---------------------------------------------------------------------------
# V0 construction (paper section 3.1)
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
    log.info("V0 constructed", shape=V0.shape, orthogonality_check=float(np.max(np.abs(V0.T @ V0 - np.eye(3)))))
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

    log.info("C0 computed", shape=C0.shape, trace=float(np.trace(C0)))
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
    """Compute B_t = V_JP @ V_US.T from regularised PCA. Returns B_t in R^(17 x 11)."""
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
    """Build long-short equal-weight portfolio.

    Long top-q, Short bottom-q. Sigma(w)=0, Sigma(|w|)=2.
    Each long position: +1/q, each short position: -1/q.
    """
    n = len(signal)
    ranking = np.argsort(-signal)  # descending
    weights = np.zeros(n)
    weights[ranking[:q]] = 1.0 / q      # long top-q
    weights[ranking[-q:]] = -1.0 / q    # short bottom-q
    return weights


# ---------------------------------------------------------------------------
# Rolling backtest (Long-Short version)
# ---------------------------------------------------------------------------
def run_backtest(
    us_c2c_aligned: pd.DataFrame,
    jp_oc_aligned: pd.DataFrame,
    date_info: pd.DataFrame,
    V0: np.ndarray,
    C0: np.ndarray,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run rolling long-short backtest over OOS period."""
    T = len(us_c2c_aligned)
    us_vals = us_c2c_aligned.values
    jp_vals = jp_oc_aligned.values

    us_vals = np.nan_to_num(us_vals, nan=0.0)
    jp_vals = np.nan_to_num(jp_vals, nan=0.0)

    jp_dates = date_info["JP_DATE"].values
    warmup_mask = jp_dates <= np.datetime64(WARMUP_END)
    oos_mask = (jp_dates >= np.datetime64(OOS_START)) & (jp_dates <= np.datetime64(OOS_END))

    oos_indices = np.where(oos_mask)[0]

    log.info(
        "Backtest setup",
        warmup_days=int(warmup_mask.sum()),
        oos_days=len(oos_indices),
        window_L=WINDOW_L,
    )

    records = []
    signal_sample_rows = []
    sample_captured = False

    for t_idx in oos_indices:
        if t_idx < WINDOW_L:
            continue

        jp_date = pd.Timestamp(jp_dates[t_idx])
        us_date = pd.Timestamp(date_info["US_DATE"].values[t_idx])

        # Window for rolling correlation
        window = slice(t_idx - WINDOW_L, t_idx)
        us_win = us_vals[window]
        jp_win = jp_vals[window]

        Z_win = np.concatenate([us_win, jp_win], axis=1)
        Z_win = Z_win - Z_win.mean(axis=0, keepdims=True)
        std_win = Z_win.std(axis=0, keepdims=True)
        std_win[std_win == 0] = 1.0
        Z_win = Z_win / std_win
        C_win = (Z_win.T @ Z_win) / max(Z_win.shape[0] - 1, 1)

        # Predictors
        B_reg = compute_predictor(C_win, C0, K_EIG, LAMBDA_REG)
        B_plain = compute_predictor(C_win, C0, K_EIG, 0.0)

        # Standardise today's US c2c return
        us_today = us_vals[t_idx]
        us_mu = us_vals[window].mean(axis=0)
        us_sig = us_vals[window].std(axis=0)
        us_sig[us_sig == 0] = 1.0
        z_us = (us_today - us_mu) / us_sig

        # Predicted JP signals
        z_pred_reg = B_reg @ z_us
        z_pred_plain = B_plain @ z_us

        # Simple US momentum baseline (cross-correlation)
        us_jp_cross = C_win[N_US:, :N_US]
        z_pred_mom = us_jp_cross @ z_us

        # Actual JP oc return
        jp_today = jp_vals[t_idx]

        row: dict[str, object] = {"JP_DATE": jp_date, "US_DATE": us_date}

        for q in Q_LIST:
            suffix = f"q{q}"

            # Regularised PCA long-short
            w_reg = build_ls_weights(z_pred_reg, q)
            ret_reg = np.dot(w_reg, jp_today)
            row[f"REG_PCA_LS_{suffix}"] = ret_reg

            # Plain PCA long-short (lambda=0)
            w_plain = build_ls_weights(z_pred_plain, q)
            ret_plain = np.dot(w_plain, jp_today)
            row[f"PLAIN_PCA_LS_{suffix}"] = ret_plain

            # Simple US momentum long-short
            w_mom = build_ls_weights(z_pred_mom, q)
            ret_mom = np.dot(w_mom, jp_today)
            row[f"US_MOM_LS_{suffix}"] = ret_mom

        # FLAT baseline
        row["FLAT"] = 0.0

        records.append(row)

        # Signal sample
        if not sample_captured and jp_date >= pd.Timestamp("2023-07-01"):
            sample_captured = True
            ranking_reg = np.argsort(-z_pred_reg)
            for j in range(N_JP):
                signal_sample_rows.append({
                    "JP_DATE": jp_date,
                    "US_DATE": us_date,
                    "JP_TICKER": JP_TICKERS[j],
                    "SIGNAL_REG": float(z_pred_reg[j]),
                    "SIGNAL_PLAIN": float(z_pred_plain[j]),
                    "SIGNAL_MOM": float(z_pred_mom[j]),
                    "ACTUAL_OC_RET": float(jp_today[j]),
                    "RANK_REG": int(np.argsort(np.argsort(-z_pred_reg))[j]) + 1,
                })

    ret_daily = pd.DataFrame(records).set_index("JP_DATE")
    ret_daily.index.name = "DATE"
    ret_daily.pop("US_DATE")

    sig_sample = pd.DataFrame(signal_sample_rows)

    # Weekly rebalance version
    ret_weekly = build_weekly_rebalance(us_vals, jp_vals, date_info, C0, oos_indices)

    return ret_daily, ret_weekly, sig_sample


def build_weekly_rebalance(
    us_vals: np.ndarray,
    jp_vals: np.ndarray,
    date_info: pd.DataFrame,
    C0: np.ndarray,
    oos_indices: np.ndarray,
) -> pd.DataFrame:
    """Weekly rebalance: compute signal once per week, hold all week (L/S)."""
    jp_dates = date_info["JP_DATE"].values
    records = []
    current_weights: dict[str, np.ndarray | None] = {f"q{q}": None for q in Q_LIST}
    rebal_counter = 0

    for t_idx in oos_indices:
        if t_idx < WINDOW_L:
            continue

        jp_date = pd.Timestamp(jp_dates[t_idx])
        jp_today = jp_vals[t_idx]

        if rebal_counter % 5 == 0:
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

            us_today = us_vals[t_idx]
            us_mu = us_vals[window].mean(axis=0)
            us_sig = us_vals[window].std(axis=0)
            us_sig[us_sig == 0] = 1.0
            z_us = (us_today - us_mu) / us_sig

            z_pred_reg = B_reg @ z_us

            for q in Q_LIST:
                current_weights[f"q{q}"] = build_ls_weights(z_pred_reg, q)

        rebal_counter += 1

        row: dict[str, object] = {"DATE": jp_date}
        for q in Q_LIST:
            suffix = f"q{q}"
            w = current_weights[suffix]
            if w is not None:
                row[f"REG_PCA_LS_W_{suffix}"] = np.dot(w, jp_today)
            else:
                row[f"REG_PCA_LS_W_{suffix}"] = 0.0
        row["FLAT"] = 0.0
        records.append(row)

    ret_weekly = pd.DataFrame(records).set_index("DATE")
    return ret_weekly


# ---------------------------------------------------------------------------
# Metrics computation (3-stage: gross / net / tax)
# ---------------------------------------------------------------------------
def compute_metrics(ret: pd.DataFrame, cost_bps: float = ONEWAY_COST_BPS) -> pd.DataFrame:
    """Compute 3-stage metrics: gross, after-cost+borrow, after-tax.

    For long-short:
        - Trading cost: assume 100% daily turnover (conservative) -> 2 * cost_bps / 10000 per day
        - Borrow cost: short side weight = 1.0 (Sigma(|w_short|)=1) -> daily = BORROW_RATE_ANN / 252
    """
    rows = []
    for col in ret.columns:
        if col in ("FLAT", "US_DATE"):
            continue
        s = ret[col].dropna()
        if len(s) == 0:
            continue

        n_days = len(s)

        # Gross
        mu_gross = s.mean() * 252
        vol = s.std(ddof=1) * np.sqrt(252)
        sharpe_gross = mu_gross / vol if vol > 0 else np.nan
        cum_gross = (1 + s).cumprod()
        mdd_gross = ((cum_gross / cum_gross.cummax()) - 1).min()

        # After cost + borrow
        # Trading cost: round-trip per day (long + short sides both turn over)
        daily_cost = 2 * cost_bps / 10_000
        # Borrow cost: short side has |w| = 1.0, daily = annual / 252
        daily_borrow = BORROW_RATE_ANN / 252
        s_net = s - daily_cost - daily_borrow
        mu_net = s_net.mean() * 252
        sharpe_net = mu_net / vol if vol > 0 else np.nan
        cum_net = (1 + s_net).cumprod()
        mdd_net = ((cum_net / cum_net.cummax()) - 1).min()

        # After tax (on positive returns only)
        mu_tax = mu_net * (1 - TAX_RATE) if mu_net > 0 else mu_net
        sharpe_tax = mu_tax / vol if vol > 0 else np.nan

        rows.append({
            "strategy": col,
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
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ts0 = datetime.now(tz=JST)
    log.info("011-4b US->JP sector lead-lag LONG-SHORT PoC start", ts=ts0.isoformat())

    # 1. Load data from Pass 1a cache (NO BQ queries)
    df_us = load_us_data()
    df_jp = load_jp_data()

    # 2. Build returns
    us_c2c = build_us_returns(df_us)
    jp_oc, jp_c2c = build_jp_returns(df_jp)

    log.info("Returns built", us_c2c_shape=us_c2c.shape, jp_oc_shape=jp_oc.shape)

    # 3. Align common dates
    us_aligned, jp_aligned, date_info = align_common_dates(us_c2c, jp_oc)

    # 4. Build V0
    V0 = build_V0()

    # 5. Compute C0
    jp_dates = date_info["JP_DATE"].values
    warmup_mask = (jp_dates >= np.datetime64(WARMUP_START)) & (jp_dates <= np.datetime64(WARMUP_END))

    us_warm = us_aligned.values[warmup_mask]
    jp_warm = jp_aligned.values[warmup_mask]
    us_warm = np.nan_to_num(us_warm, nan=0.0)
    jp_warm = np.nan_to_num(jp_warm, nan=0.0)

    C0 = compute_C0(us_warm, jp_warm, V0)

    # 6. Run long-short backtest
    ret_daily, ret_weekly, sig_sample = run_backtest(
        us_aligned, jp_aligned, date_info, V0, C0,
    )

    # 7. Compute metrics
    metrics_daily = compute_metrics(ret_daily)
    metrics_weekly = compute_metrics(ret_weekly, cost_bps=ONEWAY_COST_BPS)

    metrics_all = pd.concat([metrics_daily, metrics_weekly], ignore_index=True)

    log.info("=== METRICS ===")
    print("\n" + metrics_all.to_string(index=False))

    # 8. Save outputs
    ret_daily.to_csv(OUTPUT_DIR / "returns_daily.csv", encoding="utf-8")
    ret_weekly.to_csv(OUTPUT_DIR / "returns_weekly.csv", encoding="utf-8")
    metrics_all.to_csv(OUTPUT_DIR / "metrics_summary.csv", index=False, encoding="utf-8")
    sig_sample.to_csv(OUTPUT_DIR / "signal_sample.csv", index=False, encoding="utf-8")

    log.info("Outputs saved", dir=str(OUTPUT_DIR))

    # 9. Pass/Fail judgment
    reg_metrics = metrics_all[metrics_all["strategy"].str.startswith("REG_PCA")]
    if len(reg_metrics) > 0:
        best_row = reg_metrics.loc[reg_metrics["Sharpe_tax"].idxmax()]
        best_sharpe_tax = best_row["Sharpe_tax"]
        best_strategy = best_row["strategy"]
        passed = best_sharpe_tax >= 1.0
        verdict = "PASS" if passed else "FAIL"
        log.info(
            "Pass 1b judgment",
            verdict=verdict,
            best_strategy=best_strategy,
            sharpe_tax=f"{best_sharpe_tax:.4f}",
            threshold=1.0,
        )
    else:
        verdict = "FAIL"
        best_sharpe_tax = float("nan")
        log.info("No REG_PCA strategies found", verdict=verdict)

    ts1 = datetime.now(tz=JST)
    elapsed = (ts1 - ts0).total_seconds()
    log.info("Done", elapsed_s=f"{elapsed:.1f}", ts=ts1.isoformat())

    print(f"\n=== VERDICT: {verdict} (best tax-adjusted Sharpe = {best_sharpe_tax:.4f}) ===")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

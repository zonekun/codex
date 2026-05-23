"""TOB予測ポートフォリオ バックテスト 2022-2025.

毎年5月末にRFモデルで予測確率上位N%銘柄を等ウェイトでロング、翌5月末まで保持。
TOPIX比較。

Usage:
    PYTHONUTF8=1 python scripts/tob_prediction/run_backtest.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CREDENTIALS_PATH = PROJECT_ROOT / "keys" / "gcp-service-account.json"
BQ_PROJECT = "gmailpj-357912"
CACHE_DIR = Path("C:/tmp/tob_prediction")
OUTPUT_DIR = PROJECT_ROOT / "data/output/tob_prediction"
EVAL_YEARS = [2022, 2023, 2024, 2025]
TOP_PCTS = [0.05, 0.15, 0.25]
TRANSACTION_COST = 0.001


def _bq_client() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(
        str(CREDENTIALS_PATH),
        scopes=["https://www.googleapis.com/auth/bigquery"],
    )
    return bigquery.Client(project=BQ_PROJECT, credentials=creds)


def load_predictions() -> pd.DataFrame:
    dfs = []
    for year in EVAL_YEARS:
        p = OUTPUT_DIR / f"predictions_{year}.csv"
        if not p.exists():
            raise FileNotFoundError(f"{p} not found. Run train_rf.py first.")
        df = pd.read_csv(p, encoding="utf-8", dtype={"TICKER": str})
        df["year"] = year
        dfs.append(df)
    return pd.concat(dfs, ignore_index=True)


def load_prices(client: bigquery.Client) -> pd.DataFrame:
    cache = CACHE_DIR / "bt_prices.csv"
    if cache.exists():
        return pd.read_csv(cache, encoding="utf-8", dtype={"TICKER": str}, parse_dates=["DATE"])
    sql = """
    SELECT TICKER, DATE, ADJ_CLOSE
    FROM `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS`
    WHERE DATE >= '2022-05-20' AND DATE <= '2026-06-05'
      AND IS_PREFERRED = FALSE
      AND ADJ_CLOSE IS NOT NULL AND ADJ_CLOSE > 0
    ORDER BY TICKER, DATE
    """
    df = client.query(sql).to_dataframe()
    df["TICKER"] = df["TICKER"].astype(str)
    df.to_csv(cache, index=False, encoding="utf-8")
    return df


def load_topix(client: bigquery.Client) -> pd.DataFrame:
    cache = CACHE_DIR / "bt_topix.csv"
    if cache.exists():
        return pd.read_csv(cache, encoding="utf-8", parse_dates=["DATE"])
    sql = """
    SELECT DATE, CLOSE
    FROM `gmailpj-357912.STOCK.INDEX_PRICE`
    WHERE INDEX_CODE = '0000'
      AND DATE >= '2022-05-20' AND DATE <= '2026-06-05'
    ORDER BY DATE
    """
    df = client.query(sql).to_dataframe()
    df.to_csv(cache, index=False, encoding="utf-8")
    return df


def find_ref_date(dates: pd.Series, year: int, month: int, *, last: bool = True) -> pd.Timestamp:
    m_dates = dates[(dates.dt.year == year) & (dates.dt.month == month)]
    if m_dates.empty:
        m_dates = dates[(dates.dt.year == year) & (dates.dt.month == month - 1)]
    return m_dates.max() if last else m_dates.min()


def compute_annual_return(
    tickers: list[str],
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
    price_df: pd.DataFrame,
) -> tuple[float, int]:
    rets: list[float] = []
    for tk in tickers:
        tk_p = price_df[price_df["TICKER"] == tk].set_index("DATE")["ADJ_CLOSE"].sort_index()
        if tk_p.empty:
            continue
        entry_p = tk_p[tk_p.index >= entry_date]
        if entry_p.empty:
            continue
        exit_p = tk_p[tk_p.index <= exit_date]
        if exit_p.empty:
            continue
        rets.append((exit_p.iloc[-1] / entry_p.iloc[0]) - 1)
    if not rets:
        return np.nan, 0
    return np.mean(rets) - 2 * TRANSACTION_COST, len(rets)


def main() -> None:
    client = _bq_client()
    preds = load_predictions()
    prices = load_prices(client)
    topix = load_topix(client).sort_values("DATE").reset_index(drop=True)

    print(f"Predictions: {len(preds)}, Prices: {len(prices)}, TOPIX: {len(topix)}")

    all_dates = prices["DATE"].drop_duplicates().sort_values()

    def topix_ret(entry: pd.Timestamp, exit_: pd.Timestamp) -> float:
        ep = topix[topix["DATE"] >= entry]
        xp = topix[topix["DATE"] <= exit_]
        if ep.empty or xp.empty:
            return np.nan
        return (xp["CLOSE"].iloc[-1] / ep["CLOSE"].iloc[0]) - 1

    results = []
    print()
    for year in EVAL_YEARS:
        entry_date = find_ref_date(all_dates, year, 6, last=False)
        exit_date = find_ref_date(all_dates, year + 1, 5, last=True)
        year_preds = preds[preds["year"] == year].sort_values("prob", ascending=False)
        n_total = len(year_preds)
        tr = topix_ret(entry_date, exit_date)

        for pct in TOP_PCTS:
            n_select = max(1, int(n_total * pct))
            selected = year_preds.head(n_select)["TICKER"].tolist()
            port_ret, n_traded = compute_annual_return(selected, entry_date, exit_date, prices)
            alpha = port_ret - tr if not np.isnan(port_ret) else np.nan
            results.append({
                "year": year,
                "top_pct": f"Top {int(pct * 100)}%",
                "n_selected": n_select,
                "n_traded": n_traded,
                "port_ret": port_ret,
                "topix_ret": tr,
                "alpha": alpha,
            })
            print(
                f"{year} Top {int(pct * 100):>2d}%: "
                f"n={n_traded:>3d}, ret={port_ret:+.1%}, "
                f"TOPIX={tr:+.1%}, alpha={alpha:+.1%}"
            )

    bt = pd.DataFrame(results)
    print()

    for pct_label in ["Top 5%", "Top 15%", "Top 25%"]:
        sub = bt[bt["top_pct"] == pct_label]
        cum = np.prod(1 + sub["port_ret"].values) - 1
        avg_alpha = sub["alpha"].mean()
        wins = int((sub["alpha"] > 0).sum())
        print(f"{pct_label}: cum={cum:+.1%}, avg_alpha={avg_alpha:+.1%}, wins={wins}/{len(sub)}")

    topix_cum = np.prod(1 + bt.groupby("year")["topix_ret"].first().values) - 1
    print(f"TOPIX:    cum={topix_cum:+.1%}")

    print("\n=== TOBヒット分析 ===")
    for year in EVAL_YEARS:
        yp = preds[preds["year"] == year].sort_values("prob", ascending=False)
        base_rate = yp["label"].mean()
        for pct in [0.05]:
            n = max(1, int(len(yp) * pct))
            sel = yp.head(n)
            hit_rate = sel["label"].mean()
            lift = hit_rate / base_rate if base_rate > 0 else 0
            print(
                f"{year} Top5%: {int(sel['label'].sum())}/{n} hits "
                f"({hit_rate:.1%}), base={base_rate:.1%}, lift={lift:.1f}x"
            )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / "bt_results.csv"
    bt.to_csv(out, index=False, encoding="utf-8")
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()

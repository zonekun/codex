"""factor_model_residual_corr ノートブックの子ツール: 個別株の独特な動きを抽出.

親ツール `scripts/factor_model/factor_model_residual_corr.ipynb` が出力した
2025年betas (`factor_betas_all_years.csv`) とクラスタ情報 (`clusters_2025.csv`)
を使い、指定期間 (--start-date 以降) の各銘柄のリターンから
**ファクター効果 (MKT / IND33 / SIZE) を除去した残差**を計算し、
同じクラスタ内の中央値からの逸脱を算出してランキング抽出する.

- 銘柄ごとに daily log return を計算
- 2025年の beta を当てて日次残差を算出: eps_t = r_t - (alpha + β_mkt·MKT_t + β_ind·IND_t + β_size·SIZE_t)
- 期間合計の残差 (因子除去後リターン) でクラスタ中央値との差を計算
- 2025年の raw cluster逸脱 (参考) を添えて寸評を生成

出力:
    C:\\tmp\\factor_model_output\\unique_movers_residual_<START>_<END>.csv
Dropbox:
    /stock/temp/unique_movers_residual_<START>_<END>.csv （--dropbox指定時）
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import structlog
from google.cloud import bigquery
from google.oauth2 import service_account

log = structlog.get_logger()

INPUT_DIR_DEFAULT = Path(r"G:\マイドライブ\analysis\factor_model")
OUTPUT_DIR = Path(r"C:\tmp\factor_model_output")
BQ_PROJECT = "gmailpj-357912"
SA_KEY_PATH = Path(r"G:\マイドライブ\claude\investment-agent\keys\gcp-service-account.json")
TOPIX_INDEX_CODE = "0000"

DBX_APP_KEY = "t8feblcw74hoeky"
DBX_APP_SECRET = "fcjgc37d034pw1n"
DBX_REFRESH_TOKEN = "XwOxZlA8jPUAAAAAAAAAAZxnT4qRFtWLcShpKy3cNjTf3euIMqEZxCNieAQiLSDw"
DBX_FOLDER = "/stock/temp"


def get_bq_client() -> bigquery.Client:
    """サービスアカウント明示指定で BQ クライアントを返す."""
    creds = service_account.Credentials.from_service_account_file(str(SA_KEY_PATH))
    return bigquery.Client(project=BQ_PROJECT, credentials=creds)


def resolve_trading_dates(
    client: bigquery.Client, start_date: str, benchmark_year: int
) -> dict[str, str]:
    """分析・比較期間の実際の取引日を解決する."""
    sql = f"""
    WITH d AS (
      SELECT DISTINCT DATE
      FROM `{BQ_PROJECT}.STOCK.STOCK_PRICE_JQUANTS`
      WHERE DATE BETWEEN DATE '{benchmark_year - 1}-12-01'
                     AND CURRENT_DATE('Asia/Tokyo')
    )
    SELECT
      (SELECT MAX(DATE) FROM d WHERE DATE < DATE '{start_date}') AS analysis_base,
      (SELECT MAX(DATE) FROM d) AS analysis_end,
      (SELECT MAX(DATE) FROM d WHERE DATE < DATE '{benchmark_year}-01-01') AS bench_base,
      (SELECT MAX(DATE) FROM d WHERE DATE <= DATE '{benchmark_year}-12-31') AS bench_end
    """
    row = next(iter(client.query(sql).result()))
    return {k: str(v) for k, v in dict(row).items()}


def fetch_daily_log_returns(
    client: bigquery.Client, tickers: list[str], base_date: str, end_date: str
) -> pd.DataFrame:
    """期間中の daily log return (DATE × TICKER) を返す.

    base_date の終値からスタートし、base_date 翌営業日以降を日次 log return として返す.
    """
    sql = f"""
    SELECT TICKER, DATE, ADJ_CLOSE
    FROM `{BQ_PROJECT}.STOCK.STOCK_PRICE_JQUANTS`
    WHERE DATE BETWEEN DATE '{base_date}' AND DATE '{end_date}'
      AND TICKER IN UNNEST(@tickers)
      AND ADJ_CLOSE IS NOT NULL
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("tickers", "STRING", tickers)]
    )
    df = client.query(sql, job_config=job_config).to_dataframe()
    df["DATE"] = pd.to_datetime(df["DATE"])
    wide = df.pivot_table(index="DATE", columns="TICKER", values="ADJ_CLOSE").sort_index()
    log_ret = np.log(wide / wide.shift(1)).iloc[1:]
    return log_ret


def fetch_topix_log_returns(
    client: bigquery.Client, base_date: str, end_date: str
) -> pd.Series:
    """TOPIX の daily log return を返す."""
    sql = f"""
    SELECT DATE, CLOSE
    FROM `{BQ_PROJECT}.STOCK.INDEX_PRICE`
    WHERE DATE BETWEEN DATE '{base_date}' AND DATE '{end_date}'
      AND INDEX_CODE = '{TOPIX_INDEX_CODE}'
      AND CLOSE IS NOT NULL
    ORDER BY DATE
    """
    df = client.query(sql).to_dataframe()
    df["DATE"] = pd.to_datetime(df["DATE"])
    df = df.set_index("DATE")
    return np.log(df["CLOSE"] / df["CLOSE"].shift(1)).iloc[1:]


def fetch_master(client: bigquery.Client, tickers: list[str]) -> pd.DataFrame:
    """STOCK_CODE_LIST から INDUSTRY_33_CODE / SIZE_CODE を取得."""
    sql = f"""
    SELECT TICKER, INDUSTRY_33_CODE, SIZE_CODE
    FROM `{BQ_PROJECT}.STOCK.STOCK_CODE_LIST`
    WHERE TICKER IN UNNEST(@tickers)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("tickers", "STRING", tickers)]
    )
    return client.query(sql, job_config=job_config).to_dataframe()


def compute_group_factor_daily(
    stock_log_ret: pd.DataFrame, master: pd.DataFrame, group_col: str
) -> pd.DataFrame:
    """各銘柄のグループ因子リターン (DATE × TICKER) を返す.

    銘柄 i の因子リターンは「i が属するグループの equal-weighted 平均 daily log return」.
    親ツールの factor 計算と同一の定義.
    """
    ticker_to_group = master.set_index("TICKER")[group_col].to_dict()
    grouped_daily_mean: dict[str, pd.Series] = {}
    tickers_in_price = list(stock_log_ret.columns)
    for g, members in (
        master[master["TICKER"].isin(tickers_in_price)]
        .groupby(group_col)["TICKER"]
        .apply(list)
        .items()
    ):
        grouped_daily_mean[g] = stock_log_ret[members].mean(axis=1)

    out = pd.DataFrame(index=stock_log_ret.index, columns=tickers_in_price, dtype=float)
    for t in tickers_in_price:
        g = ticker_to_group.get(t)
        if g is not None and g in grouped_daily_mean:
            out[t] = grouped_daily_mean[g]
    return out


def compute_period_residual(
    stock_log_ret: pd.DataFrame,
    topix_log_ret: pd.Series,
    ind_daily: pd.DataFrame,
    size_daily: pd.DataFrame,
    betas: pd.DataFrame,
) -> pd.DataFrame:
    """期間合計の factor-removed residual を TICKER 単位で返す.

    Returns:
        DataFrame: TICKER / period_return / period_expected / period_residual.
    """
    idx = stock_log_ret.index
    mkt = topix_log_ret.reindex(idx).fillna(0.0)
    T = len(idx)

    tickers = [t for t in stock_log_ret.columns if t in set(betas["TICKER"])]
    b = betas.set_index("TICKER").loc[tickers]

    stock_cum = stock_log_ret[tickers].sum(axis=0)
    mkt_cum = float(mkt.sum())
    ind_cum = ind_daily[tickers].sum(axis=0)
    size_cum = size_daily[tickers].sum(axis=0)

    expected = (
        T * b["alpha"]
        + b["beta_mkt"] * mkt_cum
        + b["beta_ind33"] * ind_cum
        + b["beta_size"] * size_cum
    )

    residual = stock_cum - expected
    return pd.DataFrame(
        {
            "TICKER": tickers,
            "period_return": stock_cum.values,
            "period_expected": expected.values,
            "period_residual": residual.values,
        }
    )


def save_to_dropbox(data_bytes: bytes, filename: str) -> None:
    """Dropbox にアップロード."""
    import dropbox
    from dropbox.files import WriteMode

    upload_path = f"{DBX_FOLDER}/{filename}"
    dbx = dropbox.Dropbox(
        app_key=DBX_APP_KEY,
        app_secret=DBX_APP_SECRET,
        oauth2_refresh_token=DBX_REFRESH_TOKEN,
    )
    dbx.files_upload(data_bytes, upload_path, mode=WriteMode("overwrite"))
    log.info("dropbox_uploaded", path=upload_path, size_bytes=len(data_bytes))


def make_commentary(row: pd.Series) -> str:
    """寸評を生成.

    - 主指標: period_residual (ファクター除去後) と クラスタ中央値 residual の差
    - 参考: 2025 raw cluster 逸脱方向
    """
    pr = row["period_residual"] * 100
    cmr = row["cluster_median_residual"] * 100
    dev = row["deviation_residual"] * 100
    raw25 = row["raw_resid_2025"] * 100 if pd.notna(row["raw_resid_2025"]) else None

    dir_word = "上振れ" if dev > 0 else "下振れ"
    context_parts = [
        f"因子除去後リターン{pr:+.1f}% / 同クラスタ中央値{cmr:+.1f}% → {dir_word}差{abs(dev):.1f}pt",
    ]
    if raw25 is None:
        context_parts.append("2025比較不可")
    elif abs(raw25) < 5:
        context_parts.append(f"2025はクラスタ平均並み(+{raw25:.1f}pt) → 最近になって逸脱")
    elif (raw25 > 0) == (dev > 0):
        context_parts.append(f"2025も同方向({raw25:+.1f}pt) → 傾向継続・加速")
    else:
        context_parts.append(f"2025は逆方向({raw25:+.1f}pt) → 転換")
    return ". ".join(context_parts)


def main() -> None:
    """CLI エントリポイント."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--start-date", type=str, default="2026-04-01")
    ap.add_argument("--benchmark-year", type=int, default=2025)
    ap.add_argument("--top-n", type=int, default=30)
    ap.add_argument("--input-dir", type=Path, default=INPUT_DIR_DEFAULT)
    ap.add_argument("--dropbox", action="store_true")
    ap.add_argument(
        "--beta-cap",
        type=float,
        default=5.0,
        help="|β| 上限。超える銘柄は多重共線性で暴走しているとみなし除外 (default=5.0)",
    )
    args = ap.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    clusters = pd.read_csv(
        args.input_dir / f"clusters_{args.benchmark_year}.csv", encoding="utf-8"
    )
    clusters["TICKER"] = clusters["TICKER"].astype(str)
    log.info("clusters_loaded", rows=len(clusters))

    betas_all = pd.read_csv(
        args.input_dir / "factor_betas_all_years.csv", encoding="utf-8"
    )
    betas_all["TICKER"] = betas_all["TICKER"].astype(str)
    betas = betas_all[betas_all["YEAR"] == args.benchmark_year][
        ["TICKER", "alpha", "beta_mkt", "beta_ind33", "beta_size"]
    ].copy()
    log.info("betas_loaded", year=args.benchmark_year, rows=len(betas))

    # β が極端な銘柄は多重共線性で OLS が暴走している → モデル外として除外
    before = len(betas)
    bmax = args.beta_cap
    mask = (
        betas[["beta_mkt", "beta_ind33", "beta_size"]].abs().max(axis=1) <= bmax
    ) & betas[["beta_mkt", "beta_ind33", "beta_size"]].notna().all(axis=1)
    unstable = betas.loc[~mask, "TICKER"].tolist()
    betas = betas.loc[mask].copy()
    log.info(
        "betas_filtered",
        beta_cap=bmax,
        kept=len(betas),
        dropped=before - len(betas),
        sample_dropped=unstable[:10],
    )

    client = get_bq_client()

    dates = resolve_trading_dates(client, args.start_date, args.benchmark_year)
    log.info("dates_resolved", **dates)
    print(f"\n=== 期間 ===")
    print(f"  分析 (因子除去後残差): {dates['analysis_base']} → {dates['analysis_end']}")
    print(f"  比較 (raw 2025)     : {dates['bench_base']} → {dates['bench_end']}")

    tickers = clusters["TICKER"].unique().tolist()

    master = fetch_master(client, tickers)
    master["TICKER"] = master["TICKER"].astype(str)
    log.info("master_loaded", rows=len(master))

    # --- 分析期間 (2026-04-01以降) の residual ---
    stock_daily = fetch_daily_log_returns(
        client, tickers, dates["analysis_base"], dates["analysis_end"]
    )
    topix_daily = fetch_topix_log_returns(
        client, dates["analysis_base"], dates["analysis_end"]
    )
    ind_daily = compute_group_factor_daily(stock_daily, master, "INDUSTRY_33_CODE")
    size_daily = compute_group_factor_daily(stock_daily, master, "SIZE_CODE")

    resid_df = compute_period_residual(
        stock_daily, topix_daily, ind_daily, size_daily, betas
    )
    log.info("residual_computed", rows=len(resid_df))

    merged = clusters.merge(resid_df, on="TICKER", how="inner")
    merged["cluster_median_residual"] = merged.groupby("CLUSTER")[
        "period_residual"
    ].transform("median")
    merged["deviation_residual"] = (
        merged["period_residual"] - merged["cluster_median_residual"]
    )
    merged["abs_deviation"] = merged["deviation_residual"].abs()

    # --- 2025 raw cluster 逸脱 (参考) ---
    bench_prices_sql = f"""
    SELECT TICKER,
      MAX(IF(DATE = DATE '{dates['bench_base']}', ADJ_CLOSE, NULL)) AS base_close,
      MAX(IF(DATE = DATE '{dates['bench_end']}', ADJ_CLOSE, NULL)) AS end_close
    FROM `{BQ_PROJECT}.STOCK.STOCK_PRICE_JQUANTS`
    WHERE DATE IN (DATE '{dates['bench_base']}', DATE '{dates['bench_end']}')
      AND TICKER IN UNNEST(@tickers)
    GROUP BY TICKER
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("tickers", "STRING", tickers)]
    )
    r25 = client.query(bench_prices_sql, job_config=job_config).to_dataframe()
    r25 = r25.dropna()
    r25["r_2025"] = r25["end_close"] / r25["base_close"] - 1.0
    merged = merged.merge(r25[["TICKER", "r_2025"]], on="TICKER", how="left")
    merged["r_2025_cluster"] = merged.groupby("CLUSTER")["r_2025"].transform("median")
    merged["raw_resid_2025"] = merged["r_2025"] - merged["r_2025_cluster"]

    top = merged.sort_values("abs_deviation", ascending=False).head(args.top_n).copy()
    top["commentary"] = top.apply(make_commentary, axis=1)

    out_cols = [
        "TICKER",
        "STOCK_NAME",
        "INDUSTRY_33",
        "CLUSTER",
        "period_return",
        "period_expected",
        "period_residual",
        "cluster_median_residual",
        "deviation_residual",
        "r_2025",
        "r_2025_cluster",
        "raw_resid_2025",
        "commentary",
    ]
    top_out = top[out_cols].reset_index(drop=True)

    fname = (
        f"unique_movers_residual_{dates['analysis_base']}"
        f"_to_{dates['analysis_end']}_vs_{args.benchmark_year}.csv"
    )
    out_path = OUTPUT_DIR / fname
    top_out.to_csv(out_path, index=False, encoding="utf-8-sig")
    log.info("saved_csv", path=str(out_path), rows=len(top_out))

    disp = top_out.copy()
    for c in [
        "period_return",
        "period_expected",
        "period_residual",
        "cluster_median_residual",
        "deviation_residual",
        "r_2025",
        "r_2025_cluster",
        "raw_resid_2025",
    ]:
        disp[c] = (disp[c] * 100).map(
            lambda x: f"{x:+.1f}%" if pd.notna(x) else "NA"
        )
    print(f"\n=== TOP{args.top_n} 因子除去後の独特な動き ===")
    print(disp.to_string(index=False))

    if args.dropbox:
        csv_str = top_out.to_csv(index=False)
        save_to_dropbox(csv_str.encode("utf-8-sig"), fname)
        print(f"\nDropbox: {DBX_FOLDER}/{fname}")


if __name__ == "__main__":
    main()

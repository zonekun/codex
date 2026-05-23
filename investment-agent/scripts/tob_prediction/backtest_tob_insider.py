"""TOBインサイダースクリーナー バックテスト — 発表前検出率の測定.

過去 TOB 銘柄（DELISTED_STOCKS_TOB_ENHANCE）に対して初動スコアを遡及計算し、
「IR発表 N 日前までにスコアが閾値を超えていた割合（検出率）」を計測する。

Usage:
    PYTHONUTF8=1 python scripts/tob_prediction/backtest_tob_insider.py
    PYTHONUTF8=1 python scripts/tob_prediction/backtest_tob_insider.py --since 2022-01-01
    PYTHONUTF8=1 python scripts/tob_prediction/backtest_tob_insider.py --force-reload
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

import numpy as np
import pandas as pd
import structlog
from google.cloud import bigquery
from google.oauth2 import service_account

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "tob_prediction"))

from screen_tob_insider import (  # noqa: E402
    add_cross_section_scores,
    compute_ticker_scores,
    CREDENTIALS_PATH,
    BQ_PROJECT,
    TABLE_PRICE,
    TABLE_MASTER,
    MIN_DATA_DAYS,
)

CACHE_DIR = Path("C:/tmp/tob_insider_screener")
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
JST = timezone(timedelta(hours=+9), "JST")

TABLE_TOB_ENHANCE = f"{BQ_PROJECT}.STOCK.DELISTED_STOCKS_TOB_ENHANCE"

DETECTION_WINDOWS = [10, 15, 30, 60, 90]   # 発表N日前までの検出窓（10/15日は学術上の黄金期間）
SCORE_THRESHOLDS = [0.001, 0.005, 0.01, 0.02, 0.05, 0.10]  # 初動スコア閾値
BACKTEST_FETCH_DAYS = 600              # 各銘柄の取得窓（カレンダー日数）
SCORE_VARIANTS = ["momentum_score", "momentum_score_v2"]  # Phase 3-A: v1/v2 比較

log = structlog.get_logger()
_bq: bigquery.Client | None = None


def _get_bq() -> bigquery.Client:
    global _bq
    if _bq is None:
        creds = service_account.Credentials.from_service_account_file(
            str(CREDENTIALS_PATH),
            scopes=["https://www.googleapis.com/auth/bigquery"],
        )
        _bq = bigquery.Client(project=BQ_PROJECT, credentials=creds)
    return _bq


def fetch_tob_dates(since: str) -> pd.DataFrame:
    """DELISTED_STOCKS_TOB_ENHANCE から対象 TOB 銘柄とIR日を取得."""
    sql = f"""
    SELECT TICKER, IR_FIRST_RELEASE_DATE
    FROM `{TABLE_TOB_ENHANCE}`
    WHERE IR_FIRST_RELEASE_DATE >= @since
      AND IR_FIRST_RELEASE_DATE IS NOT NULL
    ORDER BY TICKER
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("since", "DATE", since),
    ])
    df = _get_bq().query(sql, job_config=cfg).to_dataframe()
    df["TICKER"] = df["TICKER"].astype(str)
    df["IR_FIRST_RELEASE_DATE"] = df["IR_FIRST_RELEASE_DATE"].astype(str)
    log.info("tob_dates_fetched", rows=len(df), since=since)
    return df


def fetch_ohlcv_for_tob(tob_dates: pd.DataFrame, force_reload: bool = False) -> pd.DataFrame:
    """TOB 銘柄の OHLCV を一括取得。各銘柄の発表日前 BACKTEST_FETCH_DAYS 日分。

    BQ の DATE BETWEEN を銘柄ごとに JOIN する 1 クエリ方式。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    since = tob_dates["IR_FIRST_RELEASE_DATE"].min()
    until = tob_dates["IR_FIRST_RELEASE_DATE"].max()
    cache_path = CACHE_DIR / f"backtest_ohlcv_{since}_{until}.parquet"

    if cache_path.exists() and not force_reload:
        log.info("backtest_cache_hit", path=str(cache_path))
        df = pd.read_parquet(cache_path)
        df["TICKER"] = df["TICKER"].astype(str)
        df["DATE"] = df["DATE"].astype(str)
        return df

    log.info("backtest_bq_fetch_start", tickers=len(tob_dates))
    sql = f"""
    SELECT
      CAST(p.DATE AS STRING) AS DATE,
      p.TICKER,
      p.ADJ_OPEN,
      p.ADJ_HIGH,
      p.ADJ_LOW,
      p.ADJ_CLOSE,
      p.ADJ_VOLUME
    FROM `{TABLE_PRICE}` p
    INNER JOIN UNNEST(@tickers) AS t ON p.TICKER = t
    INNER JOIN `{TABLE_TOB_ENHANCE}` e ON p.TICKER = e.TICKER
    WHERE p.IS_PREFERRED = FALSE
      AND p.ADJ_CLOSE IS NOT NULL
      AND p.ADJ_VOLUME IS NOT NULL
      AND p.DATE >= DATE_SUB(e.IR_FIRST_RELEASE_DATE, INTERVAL {BACKTEST_FETCH_DAYS} DAY)
      AND p.DATE <= e.IR_FIRST_RELEASE_DATE
    ORDER BY p.TICKER, p.DATE
    """
    tickers_list = tob_dates["TICKER"].tolist()
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("tickers", "STRING", tickers_list),
    ])
    df = _get_bq().query(sql, job_config=cfg).to_dataframe()
    df["TICKER"] = df["TICKER"].astype(str)
    df["DATE"] = df["DATE"].astype(str)
    df.to_parquet(cache_path, index=False)
    log.info("backtest_bq_fetch_done", rows=len(df), tickers=df["TICKER"].nunique())
    return df


def run_backtest(
    ohlcv: pd.DataFrame,
    tob_dates: pd.DataFrame,
) -> pd.DataFrame:
    """各 TOB 銘柄の検出窓別最大スコアを計算（v1: momentum_score, v2: momentum_score_v2 両方）.

    注意: クロスセクション rank は OHLCV に含まれる銘柄群（バックテストでは TOB銘柄群）内で
    計算される。本番運用（screen_tob_insider.py）では TSE 全銘柄横断で機能する。
    """
    tob_map = dict(zip(tob_dates["TICKER"], tob_dates["IR_FIRST_RELEASE_DATE"]))

    # ── ① 各銘柄ごとに時系列スコアを計算 ───────────────────────────────────────
    parts: list[pd.DataFrame] = []
    skipped = 0
    for ticker, grp in ohlcv.groupby("TICKER"):
        if str(ticker) not in tob_map:
            continue
        scored = compute_ticker_scores(grp)
        if scored.empty:
            skipped += 1
            continue
        parts.append(scored)

    if not parts:
        log.warning("no_scored_tickers")
        return pd.DataFrame()

    all_scored = pd.concat(parts, ignore_index=True)

    # ── ② クロスセクション rank と v2 スコアを付加 ────────────────────────────
    all_scored = add_cross_section_scores(all_scored)

    # ── ③ 各 TOB銘柄 × 検出窓 で max スコアを集計（groupby 1パス, cr#217 Minor#4）
    # 検出窓は営業日換算（cr#217 Minor#5）
    rows: list[dict] = []
    for ticker, ticker_scored in all_scored.groupby("TICKER"):
        tob_date_str = tob_map.get(str(ticker))
        if tob_date_str is None:
            continue
        tob_date_ts = pd.to_datetime(tob_date_str)

        for window in DETECTION_WINDOWS:
            w_end = (tob_date_ts - pd.tseries.offsets.BDay(1)).strftime("%Y-%m-%d")
            w_start = (tob_date_ts - pd.tseries.offsets.BDay(window)).strftime("%Y-%m-%d")

            window_rows = ticker_scored[
                (ticker_scored["DATE"] >= w_start)
                & (ticker_scored["DATE"] <= w_end)
            ]

            row: dict = {
                "TICKER": ticker,
                "IR_FIRST_RELEASE_DATE": tob_date_str,
                "window_days": window,
            }
            if window_rows.empty:
                for variant in SCORE_VARIANTS:
                    row[f"max_{variant}"] = np.nan
                row["max_dormancy_score"] = np.nan
                row["max_ignition_score"] = np.nan
            else:
                for variant in SCORE_VARIANTS:
                    row[f"max_{variant}"] = float(window_rows[variant].max())
                row["max_dormancy_score"] = float(window_rows["dormancy_score"].max())
                row["max_ignition_score"] = float(window_rows["ignition_score"].max())
            rows.append(row)

    log.info("backtest_done", tickers=len(tob_map), skipped=skipped, rows=len(rows))
    return pd.DataFrame(rows)


def report_detection_rates(bt: pd.DataFrame) -> pd.DataFrame:
    """検出率テーブルを計算して表示・返却（v1/v2 両方）."""
    records: list[dict] = []

    for variant in SCORE_VARIANTS:
        score_col = f"max_{variant}"
        if score_col not in bt.columns:
            continue
        for window in DETECTION_WINDOWS:
            df_w = bt[bt["window_days"] == window].dropna(subset=[score_col])
            total = len(df_w)
            if total == 0:
                continue
            for threshold in SCORE_THRESHOLDS:
                detected = int((df_w[score_col] >= threshold).sum())
                records.append({
                    "variant": variant,
                    "window_days": window,
                    "threshold": threshold,
                    "detected": detected,
                    "total": int(total),
                    "detection_rate": detected / total,
                })

    summary = pd.DataFrame(records)

    print(f"\n{'='*68}")
    print("  TOBインサイダースクリーナー バックテスト結果（v1=現行 / v2=Phase 3-A 改善）")
    print(f"{'='*68}")
    print(
        f"{'variant':<22}  {'窓(日)':>6}  {'閾値':>6}"
        f"  {'検出':>6}  {'対象':>6}  {'検出率':>8}"
    )
    print("-" * 68)
    for _, r in summary.iterrows():
        print(
            f"{r['variant']:<22}  {int(r['window_days']):>6}  {r['threshold']:>6.3f}"
            f"  {r['detected']:>6}  {r['total']:>6}  {r['detection_rate']:>8.1%}"
        )

    # v1 vs v2 比較（30日窓・閾値 0.005）の差分
    pivot = (
        summary.pivot_table(
            index=["window_days", "threshold"],
            columns="variant",
            values="detection_rate",
        )
        .reset_index()
    )
    if "momentum_score" in pivot.columns and "momentum_score_v2" in pivot.columns:
        pivot["delta"] = pivot["momentum_score_v2"] - pivot["momentum_score"]
        print(f"\n--- v1 vs v2 検出率差分 ---")
        print(pivot.round(4).to_string(index=False))

    # 30日窓 v2 スコア分布
    df_30 = bt[bt["window_days"] == 30].dropna(subset=["max_momentum_score_v2"])
    if not df_30.empty:
        print(f"\n--- 30日窓 max_momentum_score_v2 分布 ---")
        print(df_30["max_momentum_score_v2"].describe().round(4).to_string())

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="TOBインサイダー バックテスト")
    parser.add_argument(
        "--since", default="2020-01-01",
        help="バックテスト対象 IR 発表日の下限 YYYY-MM-DD",
    )
    parser.add_argument("--force-reload", action="store_true", help="BQキャッシュを再取得")
    args = parser.parse_args()

    errors = 0

    tob_dates = fetch_tob_dates(since=args.since)
    ohlcv = fetch_ohlcv_for_tob(tob_dates, force_reload=args.force_reload)

    bt = run_backtest(ohlcv, tob_dates)
    if bt.empty:
        log.error("backtest_empty")
        sys.exit(1)

    summary = report_detection_rates(bt)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now_str = datetime.now(tz=JST).strftime("%Y%m%d_%H%M%S")
    bt_path = OUTPUT_DIR / f"tob_insider_backtest_{now_str}.csv"
    bt.to_csv(bt_path, index=False, encoding="utf-8-sig")
    log.info("backtest_saved", path=str(bt_path), rows=len(bt))

    log.info("done", errors=errors)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()

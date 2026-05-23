"""TOBインサイダースクリーナー 全銘柄バックテスト — 偽陽性率測定 + cs_rank 純効果分解.

評価期間内の TSE 全銘柄スコアを計算し、TOB銘柄(positive) vs 非TOB銘柄(negative) で
v1 / v2 / v2_dormant_only の3バリアントの TPR/FPR を比較する。

Usage:
    PYTHONUTF8=1 python scripts/tob_prediction/backtest_full_universe.py
    PYTHONUTF8=1 python scripts/tob_prediction/backtest_full_universe.py --since 2024-01-01
    PYTHONUTF8=1 python scripts/tob_prediction/backtest_full_universe.py --force-reload
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
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
    BQ_PROJECT,
    CREDENTIALS_PATH,
    attach_topix,
    compute_all_scores,
    fetch_topix,
)

CACHE_DIR = Path("C:/tmp/tob_insider_screener")
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
JST = timezone(timedelta(hours=+9), "JST")

TABLE_PRICE = f"{BQ_PROJECT}.STOCK.STOCK_PRICE_JQUANTS"
TABLE_TOB_ENHANCE = f"{BQ_PROJECT}.STOCK.DELISTED_STOCKS_TOB_ENHANCE"

DETECTION_WINDOWS = [10, 15, 30, 60, 90]
SCORE_THRESHOLDS = [0.001, 0.005, 0.01, 0.02, 0.05, 0.10]
SCORE_VARIANTS = [
    "momentum_score",                  # v1: 現行（dormant_bonus, 時系列rank only）
    "momentum_score_v2_dormant_only",  # v1.5: dormant_factor のみ（cs_rank なし）
    "momentum_score_v2",               # v2: dormant_factor + cs_rank
    "momentum_score_v3",               # v3: v1 + AR/CAR (TOPIX 控除) を ignition 5項目目に
]
FETCH_LOOKBACK_DAYS = 400

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


def fetch_ohlcv_full(
    date_from: str, date_to: str, force_reload: bool = False
) -> pd.DataFrame:
    """全銘柄 OHLCV を取得（**廃止銘柄含む** = STOCK_CODE_LIST との JOIN なし）.

    screen_tob_insider.fetch_ohlcv は STOCK_CODE_LIST.EXCHANGE='TSE' で
    現役 TSE 銘柄のみに絞るため、TOB済み廃止銘柄が落ちる（200/201 が廃止後除外され
    n_TOB=35 になる事象を回避）。バックテスト用は STOCK_PRICE_JQUANTS のみで取得。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"ohlcv_full_{date_from}_{date_to}.parquet"

    if cache_path.exists() and not force_reload:
        log.info("ohlcv_full_cache_hit", path=str(cache_path))
        df = pd.read_parquet(cache_path)
        df["TICKER"] = df["TICKER"].astype(str)
        df["DATE"] = df["DATE"].astype(str)
        return df

    log.info("ohlcv_full_bq_fetch_start", date_from=date_from, date_to=date_to)
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
    WHERE p.DATE BETWEEN @date_from AND @date_to
      AND p.IS_PREFERRED = FALSE
      AND p.ADJ_CLOSE IS NOT NULL
      AND p.ADJ_VOLUME IS NOT NULL
    ORDER BY p.TICKER, p.DATE
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("date_from", "DATE", date_from),
        bigquery.ScalarQueryParameter("date_to", "DATE", date_to),
    ])
    df = _get_bq().query(sql, job_config=cfg).to_dataframe()
    df["TICKER"] = df["TICKER"].astype(str)
    df["DATE"] = df["DATE"].astype(str)
    df.to_parquet(cache_path, index=False)
    log.info("ohlcv_full_bq_fetch_done", rows=len(df), tickers=df["TICKER"].nunique())
    return df


def fetch_tob_dates(since: str, until: str) -> pd.DataFrame:
    """評価期間内の TOB 銘柄リストを取得."""
    sql = f"""
    SELECT TICKER, IR_FIRST_RELEASE_DATE
    FROM `{TABLE_TOB_ENHANCE}`
    WHERE IR_FIRST_RELEASE_DATE BETWEEN @since AND @until
      AND IR_FIRST_RELEASE_DATE IS NOT NULL
    ORDER BY TICKER
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("since", "DATE", since),
        bigquery.ScalarQueryParameter("until", "DATE", until),
    ])
    df = _get_bq().query(sql, job_config=cfg).to_dataframe()
    df["TICKER"] = df["TICKER"].astype(str)
    df["IR_FIRST_RELEASE_DATE"] = df["IR_FIRST_RELEASE_DATE"].astype(str)
    log.info("tob_dates_fetched", rows=len(df), since=since, until=until)
    return df


def compute_rolling_max(
    all_scored: pd.DataFrame,
    windows: list[int],
    variants: list[str],
) -> pd.DataFrame:
    """各銘柄 × 日付について、過去 N 営業日の max スコアをバリアントごとに計算."""
    all_scored = all_scored.sort_values(["TICKER", "DATE"]).reset_index(drop=True)
    for variant in variants:
        if variant not in all_scored.columns:
            log.warning("variant_missing", variant=variant)
            continue
        for window in windows:
            col = f"max_{variant}_{window}d"
            all_scored[col] = (
                all_scored.groupby("TICKER")[variant]
                .transform(lambda s: s.rolling(window=window, min_periods=1).max())
            )
    return all_scored


def _snap_to_trading_day(
    tob_dates: pd.DataFrame, all_scored: pd.DataFrame
) -> pd.DataFrame:
    """各 TOB の「IR - 1営業日」を、その銘柄の実取引日（最寄り過去）にスナップ.

    merge_asof で銘柄ごとの最寄り過去取引日を見つける。取引データが無い銘柄は drop。
    """
    tob = tob_dates.copy()
    tob["target_dt"] = (
        pd.to_datetime(tob["IR_FIRST_RELEASE_DATE"]) - pd.tseries.offsets.BDay(1)
    )

    universe = all_scored[["TICKER", "DATE"]].drop_duplicates()
    universe["DATE_dt"] = pd.to_datetime(universe["DATE"])
    universe = universe.sort_values("DATE_dt").reset_index(drop=True)

    tob_sorted = tob.sort_values("target_dt").reset_index(drop=True)
    snapped = pd.merge_asof(
        tob_sorted,
        universe,
        by="TICKER",
        left_on="target_dt",
        right_on="DATE_dt",
        direction="backward",
    )
    snapped = snapped.dropna(subset=["DATE"]).copy()
    snapped = snapped.rename(columns={"DATE": "eval_date"})
    return snapped[["TICKER", "IR_FIRST_RELEASE_DATE", "eval_date"]]


def build_evaluation_rows(
    all_scored: pd.DataFrame,
    tob_dates: pd.DataFrame,
    windows: list[int],
    variants: list[str],
) -> pd.DataFrame:
    """評価行を構築（eval_date = IR - 1営業日, ただし実取引日へスナップ）.

    is_tob フラグは (TICKER, DATE) が「その銘柄自身の TOB IR 前日相当」と一致するか否か。
    他銘柄の同 eval_date に居合わせた銘柄は is_tob=False で FPR 母集団となる。
    """
    snapped = _snap_to_trading_day(tob_dates, all_scored)
    log.info(
        "snap_result",
        original=len(tob_dates),
        snapped=len(snapped),
        dropped=len(tob_dates) - len(snapped),
    )

    eval_date_set = set(snapped["eval_date"])
    tob_pair_set = set(zip(snapped["TICKER"], snapped["eval_date"]))

    log.info("eval_dates_unique", count=len(eval_date_set), tob_pairs=len(tob_pair_set))

    eval_rows = all_scored[all_scored["DATE"].isin(eval_date_set)].copy()
    log.info("eval_rows_extracted", rows=len(eval_rows))

    pair_index = pd.MultiIndex.from_arrays(
        [eval_rows["TICKER"], eval_rows["DATE"]]
    )
    eval_rows["is_tob"] = pair_index.isin(tob_pair_set)

    cols = ["TICKER", "DATE", "is_tob"]
    for variant in variants:
        for window in windows:
            col = f"max_{variant}_{window}d"
            if col in eval_rows.columns:
                cols.append(col)
    return eval_rows[cols].copy()


def report_tpr_fpr(eval_df: pd.DataFrame) -> pd.DataFrame:
    """TPR/FPR を variant × window × threshold ごとに計算してテーブル表示."""
    records: list[dict] = []
    tob_df = eval_df[eval_df["is_tob"]]
    non_tob_df = eval_df[~eval_df["is_tob"]]
    n_tob = len(tob_df)
    n_non_tob = len(non_tob_df)

    log.info("eval_split", n_tob=n_tob, n_non_tob=n_non_tob)

    for variant in SCORE_VARIANTS:
        for window in DETECTION_WINDOWS:
            col = f"max_{variant}_{window}d"
            if col not in eval_df.columns:
                continue
            for threshold in SCORE_THRESHOLDS:
                tpr = (tob_df[col] >= threshold).sum() / n_tob if n_tob else 0.0
                fpr = (non_tob_df[col] >= threshold).sum() / n_non_tob if n_non_tob else 0.0
                records.append({
                    "variant": variant,
                    "window_days": window,
                    "threshold": threshold,
                    "tpr": tpr,
                    "fpr": fpr,
                    "lift": (tpr / fpr) if fpr > 0 else float("inf"),
                })
    summary = pd.DataFrame(records)

    # 30日窓・閾値0.005 のバリアント比較（cs_rank 純効果分解の主要指標）
    main = summary[(summary["window_days"] == 30) & (summary["threshold"] == 0.005)]
    print(f"\n{'='*72}")
    print(f"  全銘柄バックテスト TPR/FPR  n_TOB={n_tob}  n_NON_TOB={n_non_tob}")
    print(f"  --- 30日窓・閾値0.005 バリアント比較（cs_rank 純効果分解）---")
    print(f"{'='*72}")
    print(f"{'variant':<36}  {'TPR':>7}  {'FPR':>7}  {'lift':>7}")
    print("-" * 64)
    for _, r in main.iterrows():
        print(
            f"{r['variant']:<36}"
            f"  {r['tpr']:>7.1%}  {r['fpr']:>7.1%}  {r['lift']:>7.2f}"
        )

    # window × variant の TPR-FPR ヒートマップ（閾値0.005固定）
    pivot = (
        summary[summary["threshold"] == 0.005]
        .assign(net=lambda d: d["tpr"] - d["fpr"])
        .pivot_table(
            index="window_days",
            columns="variant",
            values="net",
        )
    )
    print(f"\n--- TPR-FPR 差分（閾値0.005, 窓 × variant）---")
    print(pivot.round(3).to_string())

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="TOBインサイダー 全銘柄バックテスト")
    parser.add_argument("--since", default="2024-01-01", help="評価期間下限 (YYYY-MM-DD)")
    parser.add_argument("--until", default="2026-05-14", help="評価期間上限 (YYYY-MM-DD)")
    parser.add_argument("--force-reload", action="store_true", help="BQキャッシュを再取得")
    args = parser.parse_args()

    errors = 0

    # 1. 全銘柄 OHLCV 取得（評価期間 + lookback）
    date_from = (
        date.fromisoformat(args.since) - timedelta(days=FETCH_LOOKBACK_DAYS)
    ).isoformat()
    date_to = args.until
    log.info("fetch_ohlcv_start", date_from=date_from, date_to=date_to)
    df = fetch_ohlcv_full(date_from, date_to, force_reload=args.force_reload)
    if df.empty:
        log.error("empty_ohlcv")
        sys.exit(1)

    # 2. TOPIX 取得して OHLCV にマージ（V3 AR/CAR 計算用）
    topix = fetch_topix(date_from, date_to, force_reload=args.force_reload)
    df = attach_topix(df, topix)
    log.info("topix_attached", topix_rows=len(topix), na_ratio=round(float(df["TOPIX_CLOSE"].isna().mean()), 3))

    # 3. スコア計算（cs_rank・v2/v2_dormant_only・v3 を内部で付加）
    log.info("compute_all_scores_start", tickers=df["TICKER"].nunique())
    all_scored = compute_all_scores(df)
    if all_scored.empty:
        log.error("no_scores_computed")
        sys.exit(1)

    # 評価期間以降のみ評価対象（lookback 部分はスコア計算用なので除外）
    all_scored = all_scored[all_scored["DATE"] >= args.since].reset_index(drop=True)

    # 3. window 内 rolling max スコア計算
    log.info(
        "compute_rolling_max_start",
        windows=DETECTION_WINDOWS,
        variants=len(SCORE_VARIANTS),
    )
    all_scored = compute_rolling_max(all_scored, DETECTION_WINDOWS, SCORE_VARIANTS)

    # 4. TOB 銘柄リスト取得
    tob_dates = fetch_tob_dates(since=args.since, until=args.until)

    # 5. 評価行構築
    eval_df = build_evaluation_rows(
        all_scored, tob_dates, DETECTION_WINDOWS, SCORE_VARIANTS,
    )

    if eval_df.empty:
        log.error("no_eval_rows")
        sys.exit(1)

    # 6. TPR/FPR レポート
    summary = report_tpr_fpr(eval_df)

    # 7. 保存
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now_str = datetime.now(tz=JST).strftime("%Y%m%d_%H%M%S")
    eval_path = OUTPUT_DIR / f"tob_full_universe_eval_{now_str}.csv"
    summary_path = OUTPUT_DIR / f"tob_full_universe_summary_{now_str}.csv"
    eval_df.to_csv(eval_path, index=False, encoding="utf-8-sig")
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    log.info("saved", eval=str(eval_path), summary=str(summary_path))

    log.info("done", errors=errors)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()

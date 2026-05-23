"""TOB予測 ML データセット生成 — Phase 3-C-1.

全銘柄 OHLCV + TOPIX + STOCK_CODE_LIST から、TOB予測 ML 用の (特徴量, ラベル)
データセットを生成し parquet として保存する。

特徴量 (約30変数):
  - 既存スコア (compute_all_scores 出力): momentum_score, dormancy_score,
    ignition_score, vol_ratio_20d, bb_width_rank, bb_width_cs_rank, vol_level,
    vol_rank_120d, vol_cs_rank, range_rank_120d, dormant_days, dormant_factor,
    bb_width, vol_score, bb_score, donchian_score, candle_score, darvas_break,
    car, car_score, ignition_score_v3, momentum_score_v3,
    dormancy_score_v2, momentum_score_v2,
    dormancy_score_v2_dormant_only, momentum_score_v2_dormant_only
  - 追加軽量: turnover_yen, ret_5d, ret_10d, ret_20d, vol_10d, vol_20d, topix_corr_60d
  - マスタ (categorical): INDUSTRY_33_CODE, INDUSTRY_17_CODE, MARKET_CATEGORY, SIZE_CODE

ラベル:
  - positive: IR_FIRST_RELEASE_DATE - [1, K] 営業日（K=positive_window, 実取引日にスナップ）
  - negative: 他銘柄の同 DATE。TOB後の同銘柄サンプルは drop（leak 防止）

Usage:
    PYTHONUTF8=1 python scripts/tob_prediction/build_ml_dataset.py
    PYTHONUTF8=1 python scripts/tob_prediction/build_ml_dataset.py --positive-window 15
    PYTHONUTF8=1 python scripts/tob_prediction/build_ml_dataset.py --since 2023-01-01 --until 2026-05-14
    PYTHONUTF8=1 python scripts/tob_prediction/build_ml_dataset.py --force-reload
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
from backtest_full_universe import fetch_ohlcv_full, fetch_tob_dates  # noqa: E402

DATASET_DIR = PROJECT_ROOT / "data" / "cache" / "tob_ml"
TABLE_MASTER = f"{BQ_PROJECT}.STOCK.STOCK_CODE_LIST"
JST = timezone(timedelta(hours=+9), "JST")

FETCH_LOOKBACK_DAYS = 400
DEFAULT_SINCE = "2024-01-01"   # 既存 parquet キャッシュ (ohlcv_full_2022-11-27_*) と整合
DEFAULT_UNTIL = "2026-05-14"
DEFAULT_POSITIVE_WINDOW = 30

EXISTING_SCORE_COLS = [
    "momentum_score",
    "dormancy_score",
    "ignition_score",
    "vol_ratio_20d",
    "bb_width_rank",
    "bb_width_cs_rank",
    "vol_level",
    "vol_rank_120d",
    "vol_cs_rank",
    "range_rank_120d",
    "dormant_days",
    "dormant_factor",
    "bb_width",
    "vol_score",
    "bb_score",
    "donchian_score",
    "candle_score",
    "darvas_break",
    "car",
    "car_score",
    "ignition_score_v3",
    "momentum_score_v3",
    "dormancy_score_v2",
    "momentum_score_v2",
    "dormancy_score_v2_dormant_only",
    "momentum_score_v2_dormant_only",
]
EXTRA_FEATURE_COLS = [
    "turnover_yen",
    "ret_5d",
    "ret_10d",
    "ret_20d",
    "vol_10d",
    "vol_20d",
    "topix_corr_60d",
]
MASTER_COLS = [
    "INDUSTRY_33_CODE",
    "INDUSTRY_17_CODE",
    "MARKET_CATEGORY",
    "SIZE_CODE",
]

log = structlog.get_logger()
_bq: bigquery.Client | None = None


def _get_bq() -> bigquery.Client:
    """BQ クライアント singleton."""
    global _bq
    if _bq is None:
        creds = service_account.Credentials.from_service_account_file(
            str(CREDENTIALS_PATH),
            scopes=["https://www.googleapis.com/auth/bigquery"],
        )
        _bq = bigquery.Client(project=BQ_PROJECT, credentials=creds)
    return _bq


def fetch_master() -> pd.DataFrame:
    """STOCK_CODE_LIST から TSE 銘柄マスタを取得（業種・市場区分・規模）.

    Returns:
        TICKER, INDUSTRY_33_CODE, INDUSTRY_17_CODE, MARKET_CATEGORY, SIZE_CODE
        すべて STRING 型。廃止済み銘柄が含まれない場合は LEFT JOIN で NaN になる。
    """
    sql = f"""
    SELECT TICKER, INDUSTRY_33_CODE, INDUSTRY_17_CODE, MARKET_CATEGORY, SIZE_CODE
    FROM `{TABLE_MASTER}`
    WHERE EXCHANGE = 'TSE'
    """
    df = _get_bq().query(sql).to_dataframe()
    df["TICKER"] = df["TICKER"].astype(str)
    log.info("master_fetched", rows=len(df))
    return df


def compute_extra_features(df: pd.DataFrame) -> pd.DataFrame:
    """追加軽量特徴量を per-ticker で計算.

    Args:
        df: ohlcv + topix attached DataFrame (DATE, TICKER, ADJ_CLOSE, ADJ_VOLUME, TOPIX_CLOSE)

    Returns:
        TICKER, DATE と EXTRA_FEATURE_COLS の DataFrame
    """
    log.info("compute_extra_features_start", tickers=df["TICKER"].nunique())
    df = df.sort_values(["TICKER", "DATE"]).reset_index(drop=True)

    parts: list[pd.DataFrame] = []
    for i, (ticker, grp) in enumerate(df.groupby("TICKER", sort=False), start=1):
        grp = grp.sort_values("DATE").reset_index(drop=True)
        c = grp["ADJ_CLOSE"].astype(float)
        v = grp["ADJ_VOLUME"].astype(float)
        topix = grp["TOPIX_CLOSE"].astype(float)
        ret = c.pct_change()
        topix_ret = topix.pct_change()

        out = grp[["DATE", "TICKER"]].copy()
        out["turnover_yen"] = (c * v).values
        out["ret_5d"] = c.pct_change(5).values
        out["ret_10d"] = c.pct_change(10).values
        out["ret_20d"] = c.pct_change(20).values
        out["vol_10d"] = ret.rolling(10, min_periods=10).std().values
        out["vol_20d"] = ret.rolling(20, min_periods=20).std().values
        out["topix_corr_60d"] = (
            ret.rolling(60, min_periods=60).corr(topix_ret).values
        )
        parts.append(out)

        if i % 1000 == 0:
            log.info("extra_features_progress", processed=i)

    result = pd.concat(parts, ignore_index=True)
    log.info("compute_extra_features_done", rows=len(result))
    return result


def _snap_tob_to_trading_days(
    tob_dates: pd.DataFrame, universe: pd.DataFrame
) -> pd.DataFrame:
    """各 TOB の「IR - 1営業日」を、その銘柄の最寄り過去取引日にスナップ.

    Args:
        tob_dates: TICKER, IR_FIRST_RELEASE_DATE
        universe: TICKER, DATE 列を持つ DataFrame（重複可。内部で drop_duplicates）

    Returns:
        TICKER, IR_FIRST_RELEASE_DATE, eval_date
    """
    tob = tob_dates.copy()
    tob["target_dt"] = (
        pd.to_datetime(tob["IR_FIRST_RELEASE_DATE"]) - pd.tseries.offsets.BDay(1)
    )

    uni = universe[["TICKER", "DATE"]].drop_duplicates()
    uni["DATE_dt"] = pd.to_datetime(uni["DATE"])
    uni = uni.sort_values("DATE_dt").reset_index(drop=True)

    tob_sorted = tob.sort_values("target_dt").reset_index(drop=True)
    snapped = pd.merge_asof(
        tob_sorted,
        uni,
        by="TICKER",
        left_on="target_dt",
        right_on="DATE_dt",
        direction="backward",
    )
    snapped = snapped.dropna(subset=["DATE"]).copy()
    snapped = snapped.rename(columns={"DATE": "eval_date"})
    return snapped[["TICKER", "IR_FIRST_RELEASE_DATE", "eval_date"]]


def build_labels(
    all_scored: pd.DataFrame,
    tob_dates: pd.DataFrame,
    positive_window: int,
) -> pd.DataFrame:
    """ラベル付与（実取引日ベース）.

    Args:
        all_scored: TICKER, DATE 列を持つ特徴量済みデータ
        tob_dates: TOB銘柄リスト
        positive_window: positive 営業日数（[1, positive_window]）

    Returns:
        TICKER, DATE, is_positive (bool), drop_post_tob (bool)
        - is_positive: 該当銘柄の「IR_FIRST_RELEASE_DATE - 1営業日（実取引日スナップ）」を
          終点とする positive_window 営業日窓に含まれるか
        - drop_post_tob: DATE >= IR_FIRST_RELEASE_DATE （leak 防止のため学習対象から除外）
    """
    # universe（各銘柄の実取引日）を構築し、TICKER内連番を付与
    uni = (
        all_scored[["TICKER", "DATE"]]
        .drop_duplicates()
        .sort_values(["TICKER", "DATE"])
        .reset_index(drop=True)
    )
    uni["row_idx"] = uni.groupby("TICKER").cumcount()

    snapped = _snap_tob_to_trading_days(tob_dates, uni)
    log.info(
        "tob_snap",
        original=len(tob_dates),
        snapped=len(snapped),
        dropped=len(tob_dates) - len(snapped),
    )

    # snapped に row_idx をマージ
    uni_idx = uni.set_index(["TICKER", "DATE"])["row_idx"]
    snapped["eval_row_idx"] = snapped.set_index(["TICKER", "eval_date"]).index.map(
        uni_idx
    )
    snapped = snapped.dropna(subset=["eval_row_idx"])
    snapped["eval_row_idx"] = snapped["eval_row_idx"].astype(int)

    # positive ペア (TICKER, DATE) を構築
    positive_pairs: set[tuple[str, str]] = set()
    for _, row in snapped.iterrows():
        ticker = row["TICKER"]
        end_idx = int(row["eval_row_idx"])
        start_idx = max(0, end_idx - positive_window + 1)
        ticker_dates = uni[(uni["TICKER"] == ticker) & (uni["row_idx"] >= start_idx) & (uni["row_idx"] <= end_idx)]["DATE"].tolist()
        for d in ticker_dates:
            positive_pairs.add((ticker, d))

    log.info("positive_pairs_built", n=len(positive_pairs))

    # post-TOB 除外フラグ
    ir_map: dict[str, str] = dict(
        zip(tob_dates["TICKER"], tob_dates["IR_FIRST_RELEASE_DATE"])
    )

    labels = all_scored[["TICKER", "DATE"]].copy()
    pair_arr = list(zip(labels["TICKER"], labels["DATE"]))
    labels["is_positive"] = pd.Series(pair_arr).isin(positive_pairs).values

    ir_series = labels["TICKER"].map(ir_map)
    labels["drop_post_tob"] = (
        ir_series.notna() & (labels["DATE"] >= ir_series.fillna("9999-99-99"))
    )

    log.info(
        "labels_built",
        rows=len(labels),
        positive=int(labels["is_positive"].sum()),
        post_tob_dropped=int(labels["drop_post_tob"].sum()),
    )
    return labels


def build_dataset(
    since: str,
    until: str,
    positive_window: int,
    force_reload: bool = False,
) -> pd.DataFrame:
    """データセット構築のメインパイプライン.

    Args:
        since: 評価期間下限（lookback を加味して BQ 取得は FETCH_LOOKBACK_DAYS 前から）
        until: 評価期間上限
        positive_window: positive ラベル窓（営業日）
        force_reload: BQ キャッシュを無視

    Returns:
        TICKER, DATE, 特徴量, is_positive のデータセット（drop_post_tob 行は除外済み）
    """
    date_from = (
        date.fromisoformat(since) - timedelta(days=FETCH_LOOKBACK_DAYS)
    ).isoformat()
    date_to = until

    # 1. OHLCV + TOPIX 取得
    ohlcv = fetch_ohlcv_full(date_from, date_to, force_reload=force_reload)
    if ohlcv.empty:
        raise RuntimeError("empty ohlcv")
    topix = fetch_topix(date_from, date_to, force_reload=force_reload)
    ohlcv = attach_topix(ohlcv, topix)

    # 2. 既存スコア計算
    log.info("compute_all_scores_start", tickers=ohlcv["TICKER"].nunique())
    all_scored = compute_all_scores(ohlcv)
    if all_scored.empty:
        raise RuntimeError("no scores computed")

    # 評価期間以降に絞る（lookback は内部で使われ済み）
    all_scored = all_scored[all_scored["DATE"] >= since].reset_index(drop=True)
    ohlcv_eval = ohlcv[ohlcv["DATE"] >= since].reset_index(drop=True)

    # 3. 追加特徴量
    extra = compute_extra_features(ohlcv_eval)

    # 4. マスタ取得 → 結合
    master = fetch_master()

    # 5. 既存スコア + 追加 + マスタを結合
    feats = all_scored.merge(extra, on=["TICKER", "DATE"], how="left")
    feats = feats.merge(master, on="TICKER", how="left")

    # 6. ラベル付与
    tob_dates = fetch_tob_dates(since=since, until=until)
    labels = build_labels(all_scored, tob_dates, positive_window)
    feats = feats.merge(labels, on=["TICKER", "DATE"], how="left")

    # 7. post-TOB 行を除外（leak 防止）
    before = len(feats)
    feats = feats[~feats["drop_post_tob"].fillna(False)].reset_index(drop=True)
    log.info("post_tob_filtered", before=before, after=len(feats))

    # 必要列のみ残す
    keep_cols = (
        ["TICKER", "DATE", "ADJ_CLOSE"]
        + EXISTING_SCORE_COLS
        + EXTRA_FEATURE_COLS
        + MASTER_COLS
        + ["is_positive"]
    )
    keep_cols = [c for c in keep_cols if c in feats.columns]
    feats = feats[keep_cols].copy()

    return feats


def main() -> None:
    """エントリポイント."""
    parser = argparse.ArgumentParser(description="TOB予測 ML データセット生成")
    parser.add_argument("--since", default=DEFAULT_SINCE, help="評価期間下限 (YYYY-MM-DD)")
    parser.add_argument("--until", default=DEFAULT_UNTIL, help="評価期間上限 (YYYY-MM-DD)")
    parser.add_argument(
        "--positive-window", type=int, default=DEFAULT_POSITIVE_WINDOW,
        help="positive ラベル窓（営業日, [1,K]）",
    )
    parser.add_argument(
        "--force-reload", action="store_true", help="BQキャッシュを再取得",
    )
    args = parser.parse_args()

    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    ds = build_dataset(
        since=args.since,
        until=args.until,
        positive_window=args.positive_window,
        force_reload=args.force_reload,
    )

    now_str = datetime.now(tz=JST).strftime("%Y%m%d_%H%M%S")
    out_path = (
        DATASET_DIR
        / f"dataset_{args.since}_{args.until}_pw{args.positive_window}_{now_str}.parquet"
    )
    ds.to_parquet(out_path, index=False)

    pos = int(ds["is_positive"].fillna(False).sum())
    neg = int((~ds["is_positive"].fillna(False)).sum())
    log.info(
        "dataset_saved",
        path=str(out_path),
        rows=len(ds),
        positive=pos,
        negative=neg,
        ratio=f"1:{(neg / pos) if pos else float('inf'):.0f}",
        cols=len(ds.columns),
    )
    print(f"\n=== Dataset saved ===")
    print(f"path: {out_path}")
    print(f"rows: {len(ds):,}")
    print(f"positive: {pos:,}  /  negative: {neg:,}  (1:{(neg / pos) if pos else 0:.0f})")
    print(f"cols: {len(ds.columns)}")


if __name__ == "__main__":
    main()

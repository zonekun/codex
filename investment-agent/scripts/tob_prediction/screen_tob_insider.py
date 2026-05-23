"""TOBインサイダー疑い検出スクリーナー — 低ボラ横ばいからの初動スコアリング.

アルゴリズム:
  初動スコア = 静止スコア × 発火スコア
  - 静止スコア: BBwidth 120日%ile + 出来高水準 + 60日値幅 + 静止継続日数ボーナス
  - 発火スコア: 出来高/20日平均 + Upper BB距離 + Donchian60日高値距離 + 陽線比（連続値加重和）
  詳細: docs/knowledges/analysis/015_tob_insider_screener.md

Usage:
    # 当日スクリーニング（18:30以降実行推奨）
    PYTHONUTF8=1 python scripts/tob_prediction/screen_tob_insider.py

    # 指定日
    PYTHONUTF8=1 python scripts/tob_prediction/screen_tob_insider.py --date 2026-03-10

    # smoke test: 8141 新光商事 2026-03-01〜2026-03-15 の初動スコア確認
    PYTHONUTF8=1 python scripts/tob_prediction/screen_tob_insider.py --smoke-test

    # BQキャッシュ強制リロード
    PYTHONUTF8=1 python scripts/tob_prediction/screen_tob_insider.py --force-reload
"""

from __future__ import annotations

import argparse
import os
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

CREDENTIALS_PATH = PROJECT_ROOT / "keys" / "gcp-service-account.json"
BQ_PROJECT = "gmailpj-357912"
TABLE_PRICE = f"{BQ_PROJECT}.STOCK.STOCK_PRICE_JQUANTS"
TABLE_MASTER = f"{BQ_PROJECT}.STOCK.STOCK_CODE_LIST"
_IS_CLOUD_RUN = bool(os.environ.get("CLOUD_RUN_JOB"))
CACHE_DIR = Path("/tmp/tob_insider_screener") if _IS_CLOUD_RUN else Path("C:/tmp/tob_insider_screener")
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
JST = timezone(timedelta(hours=+9), "JST")

# アルゴリズムパラメータ（暫定値; Phase 2 キャリブレーション後に確定）
BB_WINDOW = 20
ATR_WINDOW = 20
DORMANCY_WINDOW = 120
DONCHIAN_DAYS = 60
VOL_MA_DAYS = 20
RANGE_DAYS = 60
DORMANT_THRESH = 0.20
MIN_DATA_DAYS = 180
FETCH_DAYS = 400        # カレンダー日数（取引日約 280 日分）
VOL_RATIO_CAP = 5.0
DARVAS_BOX_DAYS = 20

# Phase 3-A: アルゴリズム改善
DORMANT_FACTOR_MIN = 0.5    # dormant_days=0 でも残す最低係数（B案: ゼロ問題対応）
CS_RANK_ALPHA = 0.5         # 時系列rank と クロスセクションrank の重み（α=時系列側）

# Phase 3-B: AR/CAR（V1+AR）
CAR_WINDOW = 5              # CAR 累積期間（営業日）
TABLE_INDEX_PRICE = f"{BQ_PROJECT}.STOCK.INDEX_PRICE"
TOPIX_INDEX_CODE = "0000"

SMOKE_TICKER = "8141"
SMOKE_DATE_FROM = "2026-03-01"
SMOKE_DATE_TO = "2026-03-15"
SMOKE_TOP_N = 50
SMOKE_SCORE_MIN = 0.3

log = structlog.get_logger()
_bq: bigquery.Client | None = None


def _get_bq() -> bigquery.Client:
    """BQ クライアント singleton.

    Cloud Run 環境では ADC（Application Default Credentials）を使用。
    ローカルではサービスアカウントキーファイルを使用。
    """
    global _bq
    if _bq is None:
        if _IS_CLOUD_RUN:
            # Cloud Run: ADC 自動適用（bq-loader SA に権限付与済み）
            _bq = bigquery.Client(project=BQ_PROJECT)
        else:
            if not CREDENTIALS_PATH.exists():
                log.error("credentials_not_found", path=str(CREDENTIALS_PATH))
                sys.exit(1)
            creds = service_account.Credentials.from_service_account_file(
                str(CREDENTIALS_PATH),
                scopes=["https://www.googleapis.com/auth/bigquery"],
            )
            _bq = bigquery.Client(project=BQ_PROJECT, credentials=creds)
    return _bq


def fetch_ohlcv(date_from: str, date_to: str, force_reload: bool = False) -> pd.DataFrame:
    """TSE 全銘柄 OHLCV を BQ から 1 クエリ取得。parquet キャッシュあれば再利用.

    Args:
        date_from: 取得開始日 YYYY-MM-DD
        date_to: 取得終了日 YYYY-MM-DD
        force_reload: True のとき既存キャッシュを無視して BQ から再取得

    Returns:
        DATE(str), TICKER(str), STOCK_NAME, ADJ_OPEN/HIGH/LOW/CLOSE/VOLUME 列を含む DataFrame
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"ohlcv_{date_from}_{date_to}.parquet"

    if cache_path.exists() and not force_reload:
        log.info("cache_hit", path=str(cache_path))
        df = pd.read_parquet(cache_path)
        df["TICKER"] = df["TICKER"].astype(str)
        df["DATE"] = df["DATE"].astype(str)
        return df

    log.info("bq_fetch_start", date_from=date_from, date_to=date_to)
    sql = f"""
    SELECT
      CAST(p.DATE AS STRING) AS DATE,
      p.TICKER,
      m.STOCK_NAME,
      p.ADJ_OPEN,
      p.ADJ_HIGH,
      p.ADJ_LOW,
      p.ADJ_CLOSE,
      p.ADJ_VOLUME
    FROM `{TABLE_PRICE}` p
    INNER JOIN `{TABLE_MASTER}` m
      ON p.TICKER = m.TICKER
    WHERE p.DATE BETWEEN @date_from AND @date_to
      AND p.IS_PREFERRED = FALSE
      AND m.EXCHANGE = 'TSE'
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
    log.info("bq_fetch_done", rows=len(df), tickers=df["TICKER"].nunique())
    return df


def fetch_topix(
    date_from: str, date_to: str, force_reload: bool = False
) -> pd.DataFrame:
    """TOPIX (INDEX_CODE='0000') 日次終値を取得.

    Returns:
        DATE(str), TOPIX_CLOSE(float64) の DataFrame
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"topix_{date_from}_{date_to}.parquet"

    if cache_path.exists() and not force_reload:
        log.info("topix_cache_hit", path=str(cache_path))
        df = pd.read_parquet(cache_path)
        df["DATE"] = df["DATE"].astype(str)
        return df

    log.info("topix_fetch_start", date_from=date_from, date_to=date_to)
    sql = f"""
    SELECT CAST(DATE AS STRING) AS DATE, CLOSE AS TOPIX_CLOSE
    FROM `{TABLE_INDEX_PRICE}`
    WHERE INDEX_CODE = @code
      AND DATE BETWEEN @date_from AND @date_to
      AND CLOSE IS NOT NULL
    ORDER BY DATE
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("code", "STRING", TOPIX_INDEX_CODE),
        bigquery.ScalarQueryParameter("date_from", "DATE", date_from),
        bigquery.ScalarQueryParameter("date_to", "DATE", date_to),
    ])
    df = _get_bq().query(sql, job_config=cfg).to_dataframe()
    df["DATE"] = df["DATE"].astype(str)
    df.to_parquet(cache_path, index=False)
    log.info("topix_fetch_done", rows=len(df))
    return df


def attach_topix(ohlcv: pd.DataFrame, topix: pd.DataFrame) -> pd.DataFrame:
    """OHLCV に TOPIX_CLOSE 列を左結合で付加（DATE キー）."""
    return ohlcv.merge(topix, on="DATE", how="left")


def _consecutive_true(mask: pd.Series) -> pd.Series:
    """True が続く連続日数を返す（False で 0 リセット）.

    例: [F, F, T, T, T, F, T, T] → [0, 0, 1, 2, 3, 0, 1, 2]
    """
    s = mask.astype(float)
    cumsum_all = s.cumsum()
    cumsum_at_reset = cumsum_all.where(s == 0).ffill().fillna(0.0)
    return (cumsum_all - cumsum_at_reset).astype(int)


def compute_ticker_scores(grp: pd.DataFrame) -> pd.DataFrame:
    """単一銘柄の全日付スコアを計算して返す.

    Args:
        grp: 単一銘柄の OHLCV DataFrame（DATE, TICKER, STOCK_NAME, ADJ_* 列）

    Returns:
        スコア列を追加した DataFrame。データ不足（< MIN_DATA_DAYS）なら空 DataFrame。
    """
    grp = grp.sort_values("DATE").reset_index(drop=True)
    if len(grp) < MIN_DATA_DAYS:
        return pd.DataFrame()

    c = grp["ADJ_CLOSE"]
    o = grp["ADJ_OPEN"]
    h = grp["ADJ_HIGH"]
    lo = grp["ADJ_LOW"]
    v = grp["ADJ_VOLUME"]

    # ── Bollinger Band / ATR ──────────────────────────────────────────────────
    sma = c.rolling(BB_WINDOW, min_periods=BB_WINDOW).mean()
    std = c.rolling(BB_WINDOW, min_periods=BB_WINDOW).std()
    upper_bb = sma + 2.0 * std
    bb_width = (4.0 * std) / sma.replace(0.0, np.nan)

    prev_c = c.shift(1)
    tr = pd.concat(
        [h - lo, (h - prev_c).abs(), (lo - prev_c).abs()], axis=1
    ).max(axis=1)
    atr = tr.rolling(ATR_WINDOW, min_periods=ATR_WINDOW).mean()
    safe_atr = atr.replace(0.0, np.nan)

    # ── 静止スコア（dormancy）────────────────────────────────────────────────
    bb_width_rank = bb_width.rolling(DORMANCY_WINDOW, min_periods=DORMANCY_WINDOW).rank(pct=True)
    vol_rank = v.rolling(DORMANCY_WINDOW, min_periods=DORMANCY_WINDOW).rank(pct=True)

    range_60 = (
        h.rolling(RANGE_DAYS, min_periods=RANGE_DAYS).max()
        - lo.rolling(RANGE_DAYS, min_periods=RANGE_DAYS).min()
    ) / sma.replace(0.0, np.nan)
    range_rank = range_60.rolling(DORMANCY_WINDOW, min_periods=DORMANCY_WINDOW).rank(pct=True)

    dormant_days = _consecutive_true(bb_width_rank < DORMANT_THRESH)
    dormant_bonus = np.sqrt((dormant_days / DORMANCY_WINDOW).clip(lower=0.0, upper=1.0))

    # Phase 3-A: dormant_factor は最低 DORMANT_FACTOR_MIN を保証（B案）
    dormant_factor = DORMANT_FACTOR_MIN + (1.0 - DORMANT_FACTOR_MIN) * dormant_bonus

    dormancy = (
        (1.0 - bb_width_rank).fillna(0.0)
        * (1.0 - vol_rank).fillna(0.0)
        * (1.0 - range_rank).fillna(0.0)
        * dormant_bonus
    )

    # ── 発火スコア（ignition）連続値加重和方式 ────────────────────────────────
    vol_ma = v.rolling(VOL_MA_DAYS, min_periods=VOL_MA_DAYS).mean()
    vol_ratio = v / vol_ma.replace(0.0, np.nan)
    vol_score = (vol_ratio / VOL_RATIO_CAP).clip(lower=0.0, upper=1.0)

    # Donchian: 前日までの 60 日高値（当日除外）
    max_60d_prev = c.shift(1).rolling(DONCHIAN_DAYS, min_periods=DONCHIAN_DAYS).max()

    bb_score = ((c - upper_bb) / safe_atr).clip(lower=0.0, upper=1.0)
    donchian_score = ((c - max_60d_prev) / safe_atr).clip(lower=0.0, upper=1.0)
    candle_score = ((c - o) / (h - lo + 1e-9)).clip(lower=0.0, upper=1.0)
    ignition = (vol_score + bb_score + donchian_score + candle_score) / 4.0

    # ── 初動スコア ─────────────────────────────────────────────────────────────
    momentum = dormancy * ignition

    # ── Phase 3-B: V1+AR (CAR_WINDOW 営業日累積 abnormal return, A案) ────────
    # TOPIX_CLOSE があれば AR/CAR を計算し、ignition の 5 項目目として組み込む
    if "TOPIX_CLOSE" in grp.columns:
        topix_close = grp["TOPIX_CLOSE"].astype(float)
        stock_ret = c.pct_change()
        topix_ret = topix_close.pct_change()
        ar = stock_ret - topix_ret
        car = ar.rolling(CAR_WINDOW, min_periods=CAR_WINDOW).sum()
        # ATR_pct で標準化（期待 N 日ボラ ≈ daily_atr_pct × √N）
        atr_pct = atr / c.replace(0.0, np.nan)
        expected_n_day_vol = atr_pct * np.sqrt(float(CAR_WINDOW))
        car_score = (car / expected_n_day_vol.replace(0.0, np.nan)).clip(
            lower=0.0, upper=1.0
        )
        ignition_v3 = (
            vol_score + bb_score + donchian_score + candle_score + car_score.fillna(0.0)
        ) / 5.0
        momentum_v3 = dormancy * ignition_v3
        car_values = car.values
        car_score_values = car_score.values
    else:
        ignition_v3 = pd.Series(np.nan, index=grp.index)
        momentum_v3 = pd.Series(np.nan, index=grp.index)
        car_values = np.full(len(grp), np.nan)
        car_score_values = np.full(len(grp), np.nan)

    # ── Darvas Box（簡易実装: N 日高値上抜け）────────────────────────────────
    box_top = h.shift(1).rolling(DARVAS_BOX_DAYS, min_periods=DARVAS_BOX_DAYS).max()
    darvas_break = (c > box_top).astype(int)

    base_cols = ["DATE", "TICKER", "ADJ_CLOSE"]
    if "STOCK_NAME" in grp.columns:
        base_cols.insert(2, "STOCK_NAME")
    result = grp[base_cols].copy()
    result["momentum_score"] = momentum.values
    result["dormancy_score"] = dormancy.values
    result["ignition_score"] = ignition.values
    result["vol_ratio_20d"] = vol_ratio.values
    result["bb_width_rank"] = bb_width_rank.values
    result["vol_rank_120d"] = vol_rank.values
    result["dormant_days"] = dormant_days.values
    result["vol_score"] = vol_score.values
    result["bb_score"] = bb_score.values
    result["donchian_score"] = donchian_score.values
    result["candle_score"] = candle_score.values
    result["darvas_break"] = darvas_break.values

    # Phase 3-A: クロスセクション rank 用の生値と range_rank/dormant_factor を保持
    # vol_level は per-ticker 正規化値（時価総額バイアス回避; cr#217 Major#1）
    vol_level_norm = v / v.rolling(
        DORMANCY_WINDOW, min_periods=DORMANCY_WINDOW
    ).median().replace(0.0, np.nan)
    result["bb_width"] = bb_width.values
    result["vol_level"] = vol_level_norm.values
    result["range_rank_120d"] = range_rank.values
    result["dormant_factor"] = dormant_factor.values

    # Phase 3-B: V1+AR
    result["car"] = car_values
    result["car_score"] = car_score_values
    result["ignition_score_v3"] = ignition_v3.values
    result["momentum_score_v3"] = momentum_v3.values
    return result


def compute_all_scores(df: pd.DataFrame) -> pd.DataFrame:
    """全銘柄のスコアを計算して返す（全日付分）.

    Args:
        df: fetch_ohlcv() の返り値

    Returns:
        全銘柄・全日付のスコア DataFrame。`add_cross_section_scores` 経由で
        v2 列（`bb_width_cs_rank`, `vol_cs_rank`, `dormancy_score_v2`,
        `momentum_score_v2`）を含む。skipped（データ不足）はログに記録。
    """
    parts: list[pd.DataFrame] = []
    skipped = 0
    tickers = df["TICKER"].unique()
    log.info("compute_all_scores_start", tickers=len(tickers))

    for i, (ticker, grp) in enumerate(df.groupby("TICKER"), start=1):
        scored = compute_ticker_scores(grp)
        if scored.empty:
            skipped += 1
        else:
            parts.append(scored)
        if i % 1000 == 0:
            log.info("compute_progress", processed=i, total=len(tickers), skipped=skipped)

    log.info(
        "compute_all_scores_done",
        processed=len(tickers),
        skipped=skipped,
        scored=len(tickers) - skipped,
    )
    if not parts:
        return pd.DataFrame()

    all_scored = pd.concat(parts, ignore_index=True)
    return add_cross_section_scores(all_scored)


def _combine_ts_cs(
    ts_rank: pd.Series,
    cs_rank: pd.Series,
    alpha: float,
) -> pd.Series:
    """時系列rank と クロスセクションrank を優先順位付き合成 (cr#217 Major#2).

    1. 両方有り → α×(1-ts) + (1-α)×(1-cs)
    2. ts のみ有り → (1-ts)
    3. cs のみ有り → (1-cs)
    4. 両方 NaN → 0
    """
    ts_c = 1.0 - ts_rank
    cs_c = 1.0 - cs_rank
    both = alpha * ts_c + (1.0 - alpha) * cs_c
    fallback = ts_c.fillna(cs_c).fillna(0.0)
    return both.where(both.notna(), fallback)


def add_cross_section_scores(
    all_scored: pd.DataFrame,
    alpha: float = CS_RANK_ALPHA,
) -> pd.DataFrame:
    """Phase 3-A: クロスセクション rank と v2 スコアを付加.

    各 DATE で全銘柄横断の BBwidth / 出来高水準（per-ticker 正規化済）を rank し、
    時系列 rank と _combine_ts_cs() で合成する。

    range_rank は per-ticker 正規化済み（range_60/sma で銘柄間の桁差を吸収）のため
    cross-section rank は実装しない（cr#217 Minor#6）。
    """
    # daily_count は bb_width 非 NULL ベース（cr#217 Minor#3）
    daily_count = all_scored.groupby("DATE")["bb_width"].transform("count")
    min_universe = 50

    bb_cs = all_scored.groupby("DATE")["bb_width"].rank(pct=True)
    vol_cs = all_scored.groupby("DATE")["vol_level"].rank(pct=True)
    bb_cs = bb_cs.where(daily_count >= min_universe)
    vol_cs = vol_cs.where(daily_count >= min_universe)

    all_scored["bb_width_cs_rank"] = bb_cs
    all_scored["vol_cs_rank"] = vol_cs

    # cs_rank マスク率の可視化（cr#217 Minor#7）
    log.info(
        "cs_rank_nan_ratio",
        bb=round(float(bb_cs.isna().mean()), 3),
        vol=round(float(vol_cs.isna().mean()), 3),
        min_universe=min_universe,
    )

    bb_combined = _combine_ts_cs(all_scored["bb_width_rank"], bb_cs, alpha)
    vol_combined = _combine_ts_cs(all_scored["vol_rank_120d"], vol_cs, alpha)
    range_complement = (1.0 - all_scored["range_rank_120d"]).fillna(0.0)

    all_scored["dormancy_score_v2"] = (
        bb_combined * vol_combined * range_complement * all_scored["dormant_factor"]
    )
    all_scored["momentum_score_v2"] = (
        all_scored["dormancy_score_v2"] * all_scored["ignition_score"]
    )

    # cs_rank 純効果切り分け用: dormant_factor のみ適用（cs_rank なし）
    bb_ts_only = (1.0 - all_scored["bb_width_rank"]).fillna(0.0)
    vol_ts_only = (1.0 - all_scored["vol_rank_120d"]).fillna(0.0)
    all_scored["dormancy_score_v2_dormant_only"] = (
        bb_ts_only * vol_ts_only * range_complement * all_scored["dormant_factor"]
    )
    all_scored["momentum_score_v2_dormant_only"] = (
        all_scored["dormancy_score_v2_dormant_only"] * all_scored["ignition_score"]
    )
    return all_scored


def screen(all_scored: pd.DataFrame, target_date: str, top_n: int = 100) -> pd.DataFrame:
    """target_date 時点のスコアで上位 top_n 件を返す.

    Args:
        all_scored: compute_all_scores() の返り値
        target_date: YYYY-MM-DD
        top_n: 上位表示件数

    Returns:
        rank 列付き上位銘柄 DataFrame
    """
    filtered = all_scored[all_scored["DATE"] <= target_date]
    if filtered.empty:
        return pd.DataFrame()

    # 銘柄ごとに target_date 以前の最終行を取得
    latest = (
        filtered
        .sort_values("DATE")
        .groupby("TICKER", as_index=False)
        .last()
    )
    result = (
        latest
        .sort_values("momentum_score", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )
    result.insert(0, "rank", range(1, len(result) + 1))
    return result


def run_smoke_test(all_scored: pd.DataFrame) -> bool:
    """smoke test: 8141 が 2026-03-01〜03-15 で Top50 入り or スコア>0.3 を確認.

    Args:
        all_scored: compute_all_scores() の返り値

    Returns:
        True if passed
    """
    log.info(
        "smoke_test_start",
        ticker=SMOKE_TICKER,
        from_=SMOKE_DATE_FROM,
        to=SMOKE_DATE_TO,
    )

    ticker_scored = all_scored[all_scored["TICKER"] == SMOKE_TICKER].sort_values("DATE")
    if ticker_scored.empty:
        log.error("smoke_ticker_not_found", ticker=SMOKE_TICKER)
        return False

    smoke_dates = [
        d.strftime("%Y-%m-%d")
        for d in pd.date_range(SMOKE_DATE_FROM, SMOKE_DATE_TO, freq="B")
    ]

    rows: list[dict] = []
    passed = False

    for d_str in smoke_dates:
        row_8141 = ticker_scored[ticker_scored["DATE"] <= d_str]
        if row_8141.empty:
            continue
        score_8141 = float(row_8141.iloc[-1]["momentum_score"])

        top50 = screen(all_scored, d_str, top_n=SMOKE_TOP_N)
        rank = 999
        if not top50.empty and SMOKE_TICKER in top50["TICKER"].values:
            rank = int(top50.loc[top50["TICKER"] == SMOKE_TICKER, "rank"].values[0])

        ok = score_8141 > SMOKE_SCORE_MIN or rank <= SMOKE_TOP_N
        if ok:
            passed = True
        rows.append({"date": d_str, "score": score_8141, "rank": rank, "ok": ok})

    # コンソール出力
    print(f"\n{'='*56}")
    print(f"  smoke test: {SMOKE_TICKER} 新光商事  {SMOKE_DATE_FROM} 〜 {SMOKE_DATE_TO}")
    print(f"  基準: Top{SMOKE_TOP_N}入り or スコア>{SMOKE_SCORE_MIN}")
    print(f"{'='*56}")
    print(f"{'日付':<14}{'スコア':>8}{'ランク':>8}  判定")
    print("-" * 40)
    for r in rows:
        mark = "OK" if r["ok"] else "--"
        print(f"{r['date']:<14}{r['score']:>8.4f}{r['rank']:>8}  {mark}")

    if passed:
        print("\n✓ smoke test PASSED")
        log.info("smoke_test_passed")
    else:
        print("\n✗ smoke test FAILED  → パラメータ調整（DORMANT_THRESH / VOL_RATIO_CAP 等）を検討")
        log.warning("smoke_test_failed")

    # 8141 スコア時系列（smoke 期間前後 180 日）
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        ts = ticker_scored[ticker_scored["DATE"] >= "2025-09-01"]
        if not ts.empty:
            fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
            dates = pd.to_datetime(ts["DATE"])
            axes[0].plot(dates, ts["momentum_score"], label="初動スコア")
            axes[0].axhline(SMOKE_SCORE_MIN, color="red", linestyle="--", alpha=0.5)
            axes[0].axvspan(
                pd.to_datetime(SMOKE_DATE_FROM),
                pd.to_datetime(SMOKE_DATE_TO),
                alpha=0.1, color="orange", label="smoke期間",
            )
            axes[0].set_title(f"{SMOKE_TICKER} 初動スコア時系列")
            axes[0].legend(fontsize=8)
            axes[1].plot(dates, ts["dormancy_score"], color="blue", label="静止スコア")
            axes[1].legend(fontsize=8)
            axes[2].plot(dates, ts["ignition_score"], color="green", label="発火スコア")
            axes[2].legend(fontsize=8)
            fig.tight_layout()
            plot_path = OUTPUT_DIR / f"smoke_test_{SMOKE_TICKER}.png"
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            fig.savefig(plot_path, dpi=120)
            plt.close(fig)
            log.info("plot_saved", path=str(plot_path))
    except Exception as e:  # noqa: BLE001
        log.warning("plot_skipped", reason=str(e))

    return passed


def main() -> None:
    """エントリポイント."""
    parser = argparse.ArgumentParser(description="TOBインサイダー疑い検出スクリーナー")
    parser.add_argument(
        "--date", default=None,
        help="スクリーニング対象日 YYYY-MM-DD（デフォルト: 今日 JST）",
    )
    parser.add_argument("--top-n", type=int, default=100, help="上位表示件数")
    parser.add_argument(
        "--force-reload", action="store_true",
        help="BQ キャッシュを無視して再取得",
    )
    parser.add_argument(
        "--smoke-test", action="store_true",
        help="smoke test モード（8141 2026-03-01〜03-15）",
    )
    args = parser.parse_args()

    errors = 0

    # ── 対象日決定 ────────────────────────────────────────────────────────────
    today_jst = datetime.now(tz=JST).date()
    if args.date:
        target_date = args.date
    elif args.smoke_test:
        target_date = SMOKE_DATE_TO
    else:
        target_date = today_jst.isoformat()

    # 18:30 未満の警告（当日モード）
    if not args.smoke_test and not args.date:
        now_jst = datetime.now(tz=JST)
        if now_jst.hour < 18 or (now_jst.hour == 18 and now_jst.minute < 30):
            log.warning(
                "early_execution",
                time=now_jst.strftime("%H:%M JST"),
                note="STOCK_PRICE_JQUANTS は 18:00 更新。当日データ未反映の可能性あり",
            )

    # ── BQ 取得期間 ───────────────────────────────────────────────────────────
    tgt = date.fromisoformat(target_date)
    date_from = (tgt - timedelta(days=FETCH_DAYS)).isoformat()
    date_to = target_date

    # ── データ取得 ────────────────────────────────────────────────────────────
    df = fetch_ohlcv(date_from, date_to, force_reload=args.force_reload)
    if df.empty:
        log.error("empty_ohlcv", date_from=date_from, date_to=date_to)
        sys.exit(1)

    # ── スコア計算（全銘柄・全日付）────────────────────────────────────────────
    all_scored = compute_all_scores(df)
    if all_scored.empty:
        log.error("no_scores_computed")
        sys.exit(1)

    # ── smoke test ────────────────────────────────────────────────────────────
    if args.smoke_test:
        passed = run_smoke_test(all_scored)
        sys.exit(0 if passed else 1)

    # ── 通常スクリーニング ─────────────────────────────────────────────────────
    result = screen(all_scored, target_date, top_n=args.top_n)
    if result.empty:
        log.error("no_results", target_date=target_date)
        errors += 1
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"tob_insider_screen_{target_date.replace('-', '')}.csv"
    result.to_csv(out_path, index=False, encoding="utf-8-sig")
    log.info("output_saved", path=str(out_path), rows=len(result))

    display_cols = [
        "rank", "TICKER", "STOCK_NAME", "ADJ_CLOSE",
        "momentum_score", "dormancy_score", "ignition_score",
        "vol_ratio_20d", "bb_width_rank", "dormant_days",
    ]
    print(f"\n=== TOBインサイダースクリーニング: {target_date} ===")
    print(result[display_cols].head(20).to_string(index=False))
    print(f"\n全 {len(result)} 件 → {out_path}")

    log.info("done", errors=errors, output=str(out_path))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()

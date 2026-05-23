#!/usr/bin/env python3
"""VCP スクリーナー バックテスト — 過去 TOB 対象銘柄への retrospective 適用.

screen_vcp_jp.py の VCP ロジックを、過去の TOB 案件（IS_PAPER_TOB_LABEL=TRUE）に
対して遡及的に適用し、アナウンスN営業日前の検出率 (TPR/FPR/lift) を測定する。

評価指標:
  TPR@N  = {TOB銘柄のうちアナウンスN営業日前にVCP actionable hitした割合}
  FPR    = {7件/日 ÷ 4597 TSE銘柄 = 0.152%/日} (TSE全件スキャン実測値)
  lift@N = TPR@N ÷ FPR

バックテスト設計:
  - ANN_DATE: DELISTED_STOCKS_TOB_ENHANCE.IR_FIRST_RELEASE_DATE を優先、
              なければ DELISTED_STOCKS.TOB_ANNOUNCEMENT_DATE を使用
  - eval_date = ANN_DATE - N 営業日 (numpy.busday_offset で計算)
  - eval_date 時点の OHLCV スライスに VCP ロジックを適用
  - hit = execution_state IN ('Pre-breakout', 'Breakout', 'Early-post-breakout')

Usage:
    # 標準実行（30/60/90 営業日窓）
    PYTHONUTF8=1 python scripts/tob_prediction/backtest_vcp_tob.py

    # 特定銘柄のみ（動作確認用）
    PYTHONUTF8=1 python scripts/tob_prediction/backtest_vcp_tob.py --tickers 8141 3228

    # 最初の 10 件のみ（スモークテスト）
    PYTHONUTF8=1 python scripts/tob_prediction/backtest_vcp_tob.py --limit 10

    # カスタム窓
    PYTHONUTF8=1 python scripts/tob_prediction/backtest_vcp_tob.py --windows 30 60

参照:
  docs/plans/analysis-015_insider_pattern_mismatch_20260521_205454.md Phase 0-4
  docs/knowledges/analysis/015_tob_insider_screener.md
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import structlog
from google.cloud import bigquery

# ─── パス設定 ───────────────────────────────────────────────────────────────
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parents[1]
_VCP_SKILL_DIR = Path(
    os.environ.get(
        "VCP_SKILL_DIR",
        "C:/tmp/claude-trading-skills/skills/vcp-screener/scripts",
    )
)

if not _VCP_SKILL_DIR.exists():
    print(
        f"ERROR: VCP skill directory not found: {_VCP_SKILL_DIR}\n"
        "  Run: git clone https://github.com/tradermonty/claude-trading-skills "
        "C:/tmp/claude-trading-skills",
        file=sys.stderr,
    )
    sys.exit(1)

sys.path.insert(0, str(_VCP_SKILL_DIR))
sys.path.insert(0, str(_THIS_DIR))

# VCP calculators
from calculators.relative_strength_calculator import calculate_relative_strength
from calculators.trend_template_calculator import calculate_trend_template
from calculators.vcp_pattern_calculator import calculate_vcp_pattern
from calculators.volume_pattern_calculator import calculate_volume_pattern
from calculators.pivot_proximity_calculator import calculate_pivot_proximity
from calculators.execution_state import compute_execution_state
from calculators.pattern_classifier import classify_pattern
from scorer import calculate_composite_score

# ローカルモジュール
from screen_tob_insider import (
    BQ_PROJECT,
    _get_bq,
)
from screen_vcp_jp import (
    pre_filter_stock,
    passes_trend_filter,
    is_stale_price,
)

# ─── 定数 ───────────────────────────────────────────────────────────────────
_JST = timezone(timedelta(hours=9), "JST")
_DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "data" / "output"

TABLE_PRICE: str = f"{BQ_PROJECT}.STOCK.STOCK_PRICE_JQUANTS"
TABLE_DELISTED: str = f"{BQ_PROJECT}.STOCK.DELISTED_STOCKS"
TABLE_ENHANCE: str = f"{BQ_PROJECT}.STOCK.DELISTED_STOCKS_TOB_ENHANCE"
TABLE_INDEX: str = f"{BQ_PROJECT}.STOCK.INDEX_PRICE"

YEAR_TRADING_DAYS: int = 260   # 52週分の取引日
AVG_VOL_DAYS: int = 65         # 平均出来高の計算期間

# TSE全件スキャン実測値（2026-05-22） — FPR 計算ベース
_TSE_TOTAL_STOCKS: int = 4597
_TSE_DAILY_HITS: int = 7       # Breakout + Early-post-breakout
DAILY_HIT_RATE: float = _TSE_DAILY_HITS / _TSE_TOTAL_STOCKS

# actionable hit と見なす execution_state
HIT_STATES: frozenset[str] = frozenset(
    ["Pre-breakout", "Breakout", "Early-post-breakout"]
)

log = structlog.get_logger()


# ─── BQ ヘルパー ─────────────────────────────────────────────────────────────

def load_tob_cases() -> pd.DataFrame:
    """TOB 対象銘柄リストを BQ から取得.

    DELISTED_STOCKS (IS_PAPER_TOB_LABEL=TRUE) を基に、
    IR_FIRST_RELEASE_DATE（あれば）or TOB_ANNOUNCEMENT_DATE を ANN_DATE として使用。

    Returns:
        TICKER, COMPANY_NAME, ANN_DATE (date), TOB_ANNOUNCEMENT_DATE, IR_FIRST_RELEASE_DATE
        を含む DataFrame。ANN_DATE でソート済み。
    """
    sql = f"""
    SELECT
        d.TICKER,
        d.COMPANY_NAME,
        COALESCE(e.IR_FIRST_RELEASE_DATE, d.TOB_ANNOUNCEMENT_DATE) AS ANN_DATE,
        d.TOB_ANNOUNCEMENT_DATE,
        e.IR_FIRST_RELEASE_DATE
    FROM `{TABLE_DELISTED}` d
    LEFT JOIN `{TABLE_ENHANCE}` e
        ON d.TICKER = e.TICKER
    WHERE d.IS_PAPER_TOB_LABEL = TRUE
        AND COALESCE(e.IR_FIRST_RELEASE_DATE, d.TOB_ANNOUNCEMENT_DATE) IS NOT NULL
    ORDER BY ANN_DATE
    """
    log.info("loading_tob_cases")
    df = _get_bq().query(sql).to_dataframe()
    df["TICKER"] = df["TICKER"].astype(str)
    df["ANN_DATE"] = pd.to_datetime(df["ANN_DATE"]).dt.date
    df["TOB_ANNOUNCEMENT_DATE"] = pd.to_datetime(df["TOB_ANNOUNCEMENT_DATE"]).dt.date
    df["IR_FIRST_RELEASE_DATE"] = pd.to_datetime(df["IR_FIRST_RELEASE_DATE"]).dt.date
    log.info("tob_cases_loaded", count=len(df))
    return df


def fetch_ohlcv_for_tickers(
    tickers: list[str],
    date_from: str,
    date_to: str,
) -> pd.DataFrame:
    """指定銘柄の OHLCV を STOCK_PRICE_JQUANTS から直接取得.

    STOCK_CODE_LIST との JOIN なし（廃止済み銘柄も取得可能）。

    Args:
        tickers: ティッカーリスト。
        date_from: 取得開始日 YYYY-MM-DD。
        date_to: 取得終了日 YYYY-MM-DD。

    Returns:
        DATE(str), TICKER(str), ADJ_OPEN/HIGH/LOW/CLOSE/VOLUME 列を含む DataFrame。
        TICKER, DATE 昇順ソート済み。
    """
    log.info(
        "ohlcv_fetch_start",
        tickers=len(tickers),
        date_from=date_from,
        date_to=date_to,
    )
    sql = f"""
    SELECT
        CAST(DATE AS STRING) AS DATE,
        TICKER,
        ADJ_OPEN,
        ADJ_HIGH,
        ADJ_LOW,
        ADJ_CLOSE,
        ADJ_VOLUME
    FROM `{TABLE_PRICE}`
    WHERE DATE BETWEEN @date_from AND @date_to
        AND TICKER IN UNNEST(@tickers)
        AND IS_PREFERRED = FALSE
        AND ADJ_CLOSE IS NOT NULL
        AND ADJ_VOLUME IS NOT NULL
    ORDER BY TICKER, DATE
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("date_from", "DATE", date_from),
            bigquery.ScalarQueryParameter("date_to", "DATE", date_to),
            bigquery.ArrayQueryParameter("tickers", "STRING", tickers),
        ]
    )
    df = _get_bq().query(sql, job_config=cfg).to_dataframe()
    df["TICKER"] = df["TICKER"].astype(str)
    df["DATE"] = df["DATE"].astype(str)
    log.info("ohlcv_fetch_done", rows=len(df), unique_tickers=df["TICKER"].nunique())
    return df


def fetch_topix_range(date_from: str, date_to: str) -> pd.DataFrame:
    """TOPIX 日次終値を取得.

    Returns:
        DATE(str), TOPIX_CLOSE(float) を含む DataFrame。DATE 昇順。
    """
    sql = f"""
    SELECT CAST(DATE AS STRING) AS DATE, CLOSE AS TOPIX_CLOSE
    FROM `{TABLE_INDEX}`
    WHERE INDEX_CODE = @code
        AND DATE BETWEEN @date_from AND @date_to
        AND CLOSE IS NOT NULL
    ORDER BY DATE
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("code", "STRING", "0000"),
            bigquery.ScalarQueryParameter("date_from", "DATE", date_from),
            bigquery.ScalarQueryParameter("date_to", "DATE", date_to),
        ]
    )
    df = _get_bq().query(sql, job_config=cfg).to_dataframe()
    df["DATE"] = df["DATE"].astype(str)
    log.info("topix_fetch_done", rows=len(df))
    return df


# ─── スライス・変換ヘルパー ──────────────────────────────────────────────────

def compute_quote_at_date(
    ohlcv_df: pd.DataFrame,
    ticker: str,
    eval_date: str,
) -> Optional[dict]:
    """指定評価日時点のクォートデータを計算する.

    JQuantsBQClient.get_batch_quotes() と同等の計算を eval_date 基準で実施。

    Args:
        ohlcv_df: fetch_ohlcv_for_tickers() の結果 DataFrame。
        ticker: 銘柄コード。
        eval_date: 評価日 YYYY-MM-DD。

    Returns:
        {"symbol", "price", "yearHigh", "yearLow", "avgVolume", "marketCap",
         "name", "sector"} の dict。データ不足時は None。
    """
    grp = ohlcv_df[
        (ohlcv_df["TICKER"] == ticker) & (ohlcv_df["DATE"] <= eval_date)
    ].sort_values("DATE")

    if grp.empty or len(grp) < 10:
        return None

    last = grp.iloc[-1]
    price = float(last["ADJ_CLOSE"])
    if price <= 0:
        return None

    recent_year = grp.tail(YEAR_TRADING_DAYS)
    year_high = float(recent_year["ADJ_HIGH"].max())
    year_low = float(recent_year["ADJ_LOW"].min())

    recent_vol = grp.tail(AVG_VOL_DAYS)
    avg_volume = float(recent_vol["ADJ_VOLUME"].mean()) if not recent_vol.empty else 0.0

    return {
        "symbol": ticker,
        "price": price,
        "yearHigh": year_high,
        "yearLow": year_low,
        "avgVolume": avg_volume,
        "marketCap": 0,
        "name": ticker,
        "sector": "Unknown",
    }


def compute_historical_at_date(
    ohlcv_df: pd.DataFrame,
    ticker: str,
    eval_date: str,
    days: int = 260,
) -> list[dict]:
    """指定評価日時点の historical データを FMP 降順形式で返す.

    Args:
        ohlcv_df: fetch_ohlcv_for_tickers() の結果 DataFrame。
        ticker: 銘柄コード。
        eval_date: 評価日 YYYY-MM-DD。
        days: 直近 N 取引日分。

    Returns:
        [{"date": str, "open": float, ...}, ...] の降順リスト（最新が先頭）。
    """
    grp = ohlcv_df[
        (ohlcv_df["TICKER"] == ticker) & (ohlcv_df["DATE"] <= eval_date)
    ].sort_values("DATE").tail(days)

    historical = []
    for _, row in grp.iterrows():
        close = float(row["ADJ_CLOSE"])
        historical.append({
            "date": str(row["DATE"]),
            "open": float(row["ADJ_OPEN"]) if pd.notna(row["ADJ_OPEN"]) else close,
            "high": float(row["ADJ_HIGH"]) if pd.notna(row["ADJ_HIGH"]) else close,
            "low": float(row["ADJ_LOW"]) if pd.notna(row["ADJ_LOW"]) else close,
            "close": close,
            "adjClose": close,
            "volume": float(row["ADJ_VOLUME"]) if pd.notna(row["ADJ_VOLUME"]) else 0.0,
        })

    historical.reverse()  # FMP は降順（最新が先頭）
    return historical


def compute_topix_at_date(
    topix_df: pd.DataFrame,
    eval_date: str,
    days: int = 260,
) -> list[dict]:
    """指定評価日時点の TOPIX historical を FMP 降順形式で返す.

    Args:
        topix_df: fetch_topix_range() の結果 DataFrame。
        eval_date: 評価日 YYYY-MM-DD。
        days: 直近 N 取引日分。

    Returns:
        [{"date": str, "close": float, ...}, ...] の降順リスト。
    """
    sub = topix_df[topix_df["DATE"] <= eval_date].sort_values("DATE").tail(days)

    historical = []
    for _, row in sub.iterrows():
        close = float(row["TOPIX_CLOSE"])
        historical.append({
            "date": str(row["DATE"]),
            "open": close, "high": close, "low": close,
            "close": close, "adjClose": close, "volume": 0.0,
        })
    historical.reverse()
    return historical


# ─── VCP ロジック ────────────────────────────────────────────────────────────

def run_vcp_analysis(
    ticker: str,
    historical: list[dict],
    quote: dict,
    sp500_history: list[dict],
    min_avg_volume: int = 30_000,
    trend_min_score: float = 85.0,
    tt_min_above_low: float = 25.0,
    ext_threshold: float = 8.0,
    min_contractions: int = 2,
    t1_depth_min: float = 10.0,
    contraction_ratio: float = 0.70,
    atr_multiplier: float = 1.5,
    min_contraction_days: int = 5,
    lookback_days: int = 120,
    breakout_volume_ratio: float = 1.5,
    max_sma200_extension: float = 50.0,
    wide_and_loose_threshold: float = 15.0,
    hit_states: frozenset[str] = HIT_STATES,
) -> dict:
    """1 銘柄の VCP スクリーニング full pipeline を実行する.

    screen_vcp_jp.py の Phase 1〜3 ロジックを単一銘柄に対して適用。

    Returns:
        {
            "pre_filter_passed": bool,
            "tt_passed": bool,
            "valid_vcp": bool,
            "execution_state": str | None,
            "composite_score": float | None,
            "hit": bool,             # execution_state in hit_states
            "fail_reason": str,      # pre_filter/tt_failed/vcp_invalid/hit
        }
    """
    result_base: dict = {
        "pre_filter_passed": False,
        "tt_passed": False,
        "valid_vcp": False,
        "execution_state": None,
        "composite_score": None,
        "hit": False,
        "fail_reason": "unknown",
    }

    # Phase 1: Pre-filter
    pf_passed, _ = pre_filter_stock(quote, min_avg_volume=min_avg_volume)
    result_base["pre_filter_passed"] = pf_passed
    if not pf_passed:
        result_base["fail_reason"] = "pre_filter"
        return result_base

    if not historical or len(historical) < 50:
        result_base["fail_reason"] = "insufficient_data"
        return result_base

    if is_stale_price(historical, threshold=1.0):
        result_base["fail_reason"] = "stale_price"
        return result_base

    # Phase 2: Trend Template
    rs_result = calculate_relative_strength(historical, sp500_history)
    rs_rank = rs_result.get("rs_rank_estimate", 0)

    tt_result = calculate_trend_template(
        historical, quote, rs_rank=rs_rank, ext_threshold=ext_threshold,
        max_sma200_extension=max_sma200_extension,
    )
    tt_passed = passes_trend_filter(
        tt_result, trend_min_score,
        min_above_low_pct=tt_min_above_low, quote=quote,
    )
    result_base["tt_passed"] = tt_passed
    if not tt_passed:
        result_base["fail_reason"] = "tt_failed"
        return result_base

    # Phase 3: VCP 全分析
    vcp_result = calculate_vcp_pattern(
        historical, lookback_days=lookback_days, atr_multiplier=atr_multiplier,
        min_contraction_days=min_contraction_days, min_contractions=min_contractions,
        t1_depth_min=t1_depth_min, contraction_ratio=contraction_ratio,
        wide_and_loose_threshold=wide_and_loose_threshold,
    )
    contractions = vcp_result.get("contractions", [])
    pivot_price = vcp_result.get("pivot_price")

    vol_result = calculate_volume_pattern(
        historical, pivot_price=pivot_price, contractions=contractions,
        breakout_volume_ratio=breakout_volume_ratio,
    )

    last_low = contractions[-1].get("low_price") if contractions else None
    piv_result = calculate_pivot_proximity(
        current_price=quote["price"], pivot_price=pivot_price,
        last_contraction_low=last_low,
        breakout_volume=vol_result.get("breakout_volume_detected", False),
    )

    sma200_tt = tt_result.get("sma200")
    sma200_distance_pct: Optional[float] = None
    if sma200_tt and sma200_tt > 0:
        sma200_distance_pct = (quote["price"] - sma200_tt) / sma200_tt * 100

    exec_state_result = compute_execution_state(
        distance_from_pivot_pct=piv_result.get("distance_from_pivot_pct"),
        price=quote["price"], sma50=tt_result.get("sma50"), sma200=sma200_tt,
        sma200_distance_pct=sma200_distance_pct, last_contraction_low=last_low,
        breakout_volume=vol_result.get("breakout_volume_detected", False),
        max_sma200_extension=max_sma200_extension,
    )
    execution_state = exec_state_result["state"]

    valid_vcp = vcp_result.get("valid_vcp", False)
    wide_and_loose = vcp_result.get("wide_and_loose", False)
    final_depth = contractions[-1].get("depth_pct") if contractions else None

    pattern_type = classify_pattern(
        valid_vcp=valid_vcp, num_contractions=vcp_result.get("num_contractions", 0),
        final_contraction_depth=final_depth, execution_state=execution_state,
        dry_up_ratio=vol_result.get("dry_up_ratio"), wide_and_loose=wide_and_loose,
    )
    composite = calculate_composite_score(
        trend_score=tt_result.get("score", 0), contraction_score=vcp_result.get("score", 0),
        volume_score=vol_result.get("score", 0), pivot_score=piv_result.get("score", 0),
        rs_score=rs_result.get("score", 0), valid_vcp=valid_vcp,
        execution_state=execution_state, pattern_type=pattern_type,
        wide_and_loose=wide_and_loose, sma200_extension_pct=sma200_distance_pct,
    )

    hit = execution_state in hit_states
    result_base.update({
        "valid_vcp": valid_vcp,
        "execution_state": execution_state,
        "composite_score": round(composite["composite_score"], 1),
        "hit": hit,
        "fail_reason": "hit" if hit else f"state={execution_state}",
    })
    return result_base


# ─── ビジネス日計算 ──────────────────────────────────────────────────────────

def business_days_before(ann_date: date, n_bd: int) -> date:
    """ann_date の n_bd 営業日前の日付を返す（日本市場の祝日は未考慮）.

    Args:
        ann_date: アナウンス日。
        n_bd: 営業日数。

    Returns:
        eval_date (date)。
    """
    result = np.busday_offset(ann_date.isoformat(), -n_bd, roll="backward")
    return date.fromisoformat(str(result))


# ─── CLI ────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """コマンドライン引数のパース."""
    parser = argparse.ArgumentParser(
        description="VCP スクリーナー バックテスト — 過去 TOB 対象銘柄への retrospective 適用"
    )
    parser.add_argument(
        "--windows",
        type=int,
        nargs="+",
        default=[30, 60, 90],
        help="評価窓（アナウンスN営業日前）のリスト (default: 30 60 90)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="処理する TOB ケースの最大件数（スモークテスト用）",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="処理対象を指定銘柄に限定（例: 8141 3228）",
    )
    parser.add_argument(
        "--min-avg-volume",
        type=int,
        default=30_000,
        help="Pre-filter: 平均出来高の最低値（株数）(default: 30000)",
    )
    parser.add_argument(
        "--trend-min-score",
        type=float,
        default=85.0,
        help="Trend Template 最低スコア (default: 85.0)",
    )
    parser.add_argument(
        "--tt-min-above-low",
        type=float,
        default=25.0,
        help="TT c5: 52週安値からの最低上昇率 %% (default: 25.0)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(_DEFAULT_OUTPUT_DIR),
        help=f"出力先ディレクトリ (default: {_DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--daily-hit-rate",
        type=float,
        default=DAILY_HIT_RATE,
        help=f"FPR 計算ベース（TSE 日次ヒット率）(default: {DAILY_HIT_RATE:.5f})",
    )
    return parser.parse_args()


# ─── メイン ─────────────────────────────────────────────────────────────────

def main() -> None:
    """バックテスト メインエントリポイント."""
    args = parse_args()
    now_jst = datetime.now(tz=_JST)
    ts = now_jst.strftime("%Y%m%d_%H%M%S")

    print("=" * 70)
    print("VCP バックテスト — 過去 TOB 対象銘柄への retrospective 適用")
    print(f"実行日時: {now_jst.strftime('%Y-%m-%d %H:%M:%S JST')}")
    print(f"評価窓: {args.windows} 営業日前")
    print("=" * 70)
    print()

    # ── Step 1: TOB ケース取得 ────────────────────────────────────────────────
    print("Step 1: TOB ケース取得 (BQ)")
    tob_df = load_tob_cases()

    if args.tickers:
        tob_df = tob_df[tob_df["TICKER"].isin([str(t) for t in args.tickers])]
        print(f"  銘柄フィルタ: {args.tickers} → {len(tob_df)} 件")
    if args.limit:
        tob_df = tob_df.head(args.limit)
        print(f"  件数制限: 先頭 {args.limit} 件")

    print(f"  対象: {len(tob_df)} 件")
    if tob_df.empty:
        print("ERROR: 対象 TOB ケースが 0 件です", file=sys.stderr)
        sys.exit(1)

    tickers = tob_df["TICKER"].tolist()
    ann_dates = tob_df.set_index("TICKER")["ANN_DATE"].to_dict()

    # ── Step 2: 評価日の計算とデータ取得範囲の決定 ──────────────────────────
    print()
    print("Step 2: データ取得範囲の計算")

    max_window = max(args.windows)
    eval_date_map: dict[str, dict[int, date]] = {}  # {ticker: {window: eval_date}}

    min_eval_date: date = date(9999, 12, 31)
    max_ann_date: date = date(1900, 1, 1)

    for ticker in tickers:
        ann = ann_dates[ticker]
        eval_date_map[ticker] = {}
        for w in args.windows:
            ed = business_days_before(ann, w)
            eval_date_map[ticker][w] = ed
            if ed < min_eval_date:
                min_eval_date = ed
            if ann > max_ann_date:
                max_ann_date = ann

    # OHLCV 取得範囲: 最古 eval_date の 400 日前〜最新 ann_date
    ohlcv_from = (min_eval_date - timedelta(days=400)).isoformat()
    ohlcv_to = max_ann_date.isoformat()
    print(f"  eval_date 範囲: {min_eval_date} 〜 (最大窓={max_window}bd)")
    print(f"  OHLCV 取得範囲: {ohlcv_from} 〜 {ohlcv_to}")

    # ── Step 3: OHLCV / TOPIX 一括取得 ───────────────────────────────────────
    print()
    print("Step 3: OHLCV / TOPIX 取得 (BQ 2クエリ)")

    ohlcv_df = fetch_ohlcv_for_tickers(tickers, ohlcv_from, ohlcv_to)
    topix_df = fetch_topix_range(ohlcv_from, ohlcv_to)

    # OHLCV にデータが存在するティッカーを確認
    ohlcv_tickers = set(ohlcv_df["TICKER"].unique())
    missing_tickers = [t for t in tickers if t not in ohlcv_tickers]
    if missing_tickers:
        print(f"  WARN: OHLCV なし ({len(missing_tickers)} 件): {missing_tickers[:10]}")

    # ── Step 4: バックテスト実行 ──────────────────────────────────────────────
    print()
    print(f"Step 4: VCP バックテスト ({len(tickers)} 銘柄 × {len(args.windows)} 窓)")

    rows: list[dict] = []
    total = len(tickers) * len(args.windows)
    done = 0

    for ticker in tickers:
        ann = ann_dates[ticker]
        company = tob_df.loc[tob_df["TICKER"] == ticker, "COMPANY_NAME"].iloc[0]

        for window in args.windows:
            done += 1
            eval_date = eval_date_map[ticker][window]
            eval_date_str = eval_date.isoformat()

            if done % 50 == 0 or done == total:
                print(f"  進捗: {done}/{total} ({done/total:.0%})")

            # データ不足チェック
            if ticker not in ohlcv_tickers:
                rows.append({
                    "ticker": ticker, "company": company,
                    "ann_date": ann, "window_bd": window,
                    "eval_date": eval_date,
                    "pre_filter_passed": False, "tt_passed": False,
                    "valid_vcp": False, "execution_state": None,
                    "composite_score": None, "hit": False,
                    "fail_reason": "no_ohlcv",
                })
                continue

            # データをスライス
            quote = compute_quote_at_date(ohlcv_df, ticker, eval_date_str)
            if quote is None:
                rows.append({
                    "ticker": ticker, "company": company,
                    "ann_date": ann, "window_bd": window,
                    "eval_date": eval_date,
                    "pre_filter_passed": False, "tt_passed": False,
                    "valid_vcp": False, "execution_state": None,
                    "composite_score": None, "hit": False,
                    "fail_reason": "insufficient_ohlcv",
                })
                continue

            historical = compute_historical_at_date(ohlcv_df, ticker, eval_date_str)
            sp500_history = compute_topix_at_date(topix_df, eval_date_str)

            # VCP 分析
            try:
                vcp_res = run_vcp_analysis(
                    ticker=ticker,
                    historical=historical,
                    quote=quote,
                    sp500_history=sp500_history,
                    min_avg_volume=args.min_avg_volume,
                    trend_min_score=args.trend_min_score,
                    tt_min_above_low=args.tt_min_above_low,
                )
            except Exception as exc:  # pylint: disable=broad-except
                log.warning("vcp_analysis_error", ticker=ticker, eval_date=eval_date_str, error=str(exc))
                vcp_res = {
                    "pre_filter_passed": False, "tt_passed": False,
                    "valid_vcp": False, "execution_state": None,
                    "composite_score": None, "hit": False,
                    "fail_reason": f"error: {exc}",
                }

            rows.append({
                "ticker": ticker, "company": company,
                "ann_date": ann, "window_bd": window,
                "eval_date": eval_date,
                **vcp_res,
            })

    # ── Step 5: 結果集計 ──────────────────────────────────────────────────────
    print()
    print("Step 5: 結果集計")

    results_df = pd.DataFrame(rows)

    # 結果 CSV 出力
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"vcp_backtest_tob_{ts}.csv"
    results_df.to_csv(str(csv_path), index=False, encoding="utf-8")
    print(f"  詳細 CSV: {csv_path}")

    # 窓ごとの TPR / lift 計算
    print()
    print("=" * 70)
    print("VCP バックテスト 結果サマリ")
    print("=" * 70)
    print(f"  対象 TOB 件数: {len(tickers)}")
    print(f"  FPR ベース（TSE 日次ヒット率）: {args.daily_hit_rate:.4%}")
    print()

    summary_rows: list[dict] = []
    for window in args.windows:
        wdf = results_df[results_df["window_bd"] == window]
        n_total = len(wdf)
        n_hit = wdf["hit"].sum()
        n_no_ohlcv = (wdf["fail_reason"] == "no_ohlcv").sum()
        n_valid = n_total - n_no_ohlcv  # OHLCV あり = 有効ケース数
        tpr = n_hit / n_valid if n_valid > 0 else 0.0
        lift = tpr / args.daily_hit_rate if args.daily_hit_rate > 0 else 0.0

        # 通過率の内訳
        n_pf = wdf["pre_filter_passed"].sum()
        n_tt = wdf["tt_passed"].sum()
        n_vcp = wdf["valid_vcp"].sum()

        print(f"■ 窓 {window} 営業日前 (eval_date ≈ ann_date - {window}bd)")
        print(f"    有効ケース: {n_valid} / {n_total}  (OHLCV 欠損: {n_no_ohlcv})")
        print(f"    Pre-filter 通過: {n_pf} / {n_valid}  "
              f"→ TT 通過: {n_tt} / {n_pf}  "
              f"→ valid_vcp: {n_vcp}")
        print(f"    HIT (execution_state ∈ {set(HIT_STATES)}): {n_hit}")
        print(f"    TPR@{window}bd = {tpr:.2%}")
        print(f"    Lift@{window}bd = {lift:.1f}x  (FPR={args.daily_hit_rate:.4%})")
        print()

        summary_rows.append({
            "window_bd": window,
            "n_total": n_total,
            "n_valid": n_valid,
            "n_no_ohlcv": n_no_ohlcv,
            "n_pre_filter": int(n_pf),
            "n_tt_passed": int(n_tt),
            "n_valid_vcp": int(n_vcp),
            "n_hit": int(n_hit),
            "tpr": round(tpr, 4),
            "fpr": round(args.daily_hit_rate, 6),
            "lift": round(lift, 2),
        })

    # ゴールデンサンプル v1 確認（8141）
    gs_rows = results_df[results_df["ticker"] == "8141"]
    if not gs_rows.empty:
        print("─" * 70)
        print("ゴールデンサンプル v1 チェック (8141 新光商事)")
        print("  必達条件: 2026-03 ブレイク日 ±5 営業日以内に hit ✅")
        for _, r in gs_rows.iterrows():
            hit_mark = "✅ HIT" if r["hit"] else "❌ MISS"
            print(f"  窓 {r['window_bd']:2d}bd: eval={r['eval_date']}  "
                  f"state={r['execution_state'] or 'N/A'}  "
                  f"score={r['composite_score'] or 'N/A'}  {hit_mark}")
        print()

    # ゴールデンサンプル v2 確認（3228）
    gs2_rows = results_df[results_df["ticker"] == "3228"]
    if not gs2_rows.empty:
        print("─" * 70)
        print("ゴールデンサンプル v2 チェック (3228 三栄建築設計)")
        for _, r in gs2_rows.iterrows():
            hit_mark = "✅ HIT" if r["hit"] else "❌ MISS"
            print(f"  窓 {r['window_bd']:2d}bd: eval={r['eval_date']}  "
                  f"state={r['execution_state'] or 'N/A'}  "
                  f"score={r['composite_score'] or 'N/A'}  {hit_mark}")
        print()

    # execution_state 分布
    print("─" * 70)
    print("execution_state 分布 (全窓合計)")
    state_counts = (
        results_df[results_df["execution_state"].notna()]
        ["execution_state"]
        .value_counts()
    )
    for state, cnt in state_counts.items():
        print(f"  {state}: {cnt}")
    print()

    # サマリ CSV
    summary_df = pd.DataFrame(summary_rows)
    summary_path = output_dir / f"vcp_backtest_tob_summary_{ts}.csv"
    summary_df.to_csv(str(summary_path), index=False, encoding="utf-8")
    print(f"  サマリ CSV: {summary_path}")
    print()
    print("完了")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
VCP Stock Screener (J-Quants/BQ 版) — 東証銘柄向け Minervini VCP スクリーナー.

tradermonty/claude-trading-skills vcp-screener を J-Quants/BigQuery に移植した版。
データ取得のみ JQuantsBQClient に差し替え、計算ロジックはオリジナルをそのまま利用する。

前提:
  - C:/tmp/claude-trading-skills/ に vcp-screener スキルがクローン済み
  - BQ 認証: keys/gcp-service-account.json が存在すること
  - FMP API キーは不要（BQ 経由）

Usage:
    # TSE 全銘柄スクリーニング (Top 100 候補)
    PYTHONUTF8=1 python scripts/tob_prediction/screen_vcp_jp.py

    # カスタム銘柄
    PYTHONUTF8=1 python scripts/tob_prediction/screen_vcp_jp.py --universe 8141 8163 6248

    # Full TSE (全銘柄, 時間がかかる)
    PYTHONUTF8=1 python scripts/tob_prediction/screen_vcp_jp.py --full-sp500

    # Minervini strict モード (Pre-breakout / Breakout のみ)
    PYTHONUTF8=1 python scripts/tob_prediction/screen_vcp_jp.py --strict

Output:
    - JSON: data/output/vcp_jp_YYYY-MM-DD_HHMMSS.json
    - Markdown: data/output/vcp_jp_YYYY-MM-DD_HHMMSS.md

変更点 (オリジナル screen_vcp.py との差分):
    1. sys.path に VCP スキルディレクトリを追加
    2. FMPClient → JQuantsBQClient に差し替え (2 行)
    3. "S&P 500" → "TSE" 表記変更
    4. デフォルト出力先を data/output/ に変更

参照:
  - docs/plans/analysis-015_insider_pattern_mismatch_20260521_205454.md Step i-a
  - C:/tmp/claude-trading-skills/skills/vcp-screener/SKILL.md
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# JST タイムゾーン (CLAUDE.md §7: タイムゾーン非明示の日時取得・出力は禁止)
_JST = timezone(timedelta(hours=9), "JST")

# -------------------------------------------------------------------------
# パス設定: VCP スキルのディレクトリとローカル tob_prediction を両方追加
# -------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
# 環境変数 VCP_SKILL_DIR でオーバーライド可能 (他端末・CI 対応)
# デフォルト: C:/tmp/claude-trading-skills/skills/vcp-screener/scripts
_VCP_SKILL_DIR = Path(
    os.environ.get(
        "VCP_SKILL_DIR",
        "C:/tmp/claude-trading-skills/skills/vcp-screener/scripts",
    )
)

if not _VCP_SKILL_DIR.exists():
    print(
        f"ERROR: VCP skill directory not found: {_VCP_SKILL_DIR}\n"
        "  Run: git clone https://github.com/tradermonty/claude-trading-skills C:/tmp/claude-trading-skills",
        file=sys.stderr,
    )
    sys.exit(1)

# VCP calculators / scorer / report_generator を先に追加（sys.path の優先順位）
sys.path.insert(0, str(_VCP_SKILL_DIR))
# jquants_bq_client を tob_prediction から import するため後から追加
sys.path.insert(0, str(_THIS_DIR))

from calculators.execution_state import compute_execution_state
from calculators.pattern_classifier import classify_pattern
from calculators.pivot_proximity_calculator import calculate_pivot_proximity
from calculators.relative_strength_calculator import (
    calculate_relative_strength,
    rank_relative_strength_universe,
)
from calculators.trend_template_calculator import calculate_trend_template
from calculators.vcp_pattern_calculator import calculate_vcp_pattern
from calculators.volume_pattern_calculator import calculate_volume_pattern

# ★ FMPClient → JQuantsBQClient に差し替え (変更点 2)
from jquants_bq_client import JQuantsBQClient
from report_generator import generate_json_report, generate_markdown_report
from scorer import calculate_composite_score

# デフォルト出力先 (変更点 4)
_PROJECT_ROOT = _THIS_DIR.parents[1]
_DEFAULT_OUTPUT_DIR = str(_PROJECT_ROOT / "data" / "output")


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="VCP Stock Screener (J-Quants/BQ 版) — Minervini Volatility Contraction Pattern"
    )

    # --api-key は互換性のために残すが未使用
    parser.add_argument(
        "--api-key",
        help="未使用 (FMPClient 互換パラメータ。J-Quants/BQ 版では BQ 認証を使用)",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="評価基準日 YYYY-MM-DD（省略時は今日 JST）。過去日を指定してバックテスト的に検証可能",
    )
    parser.add_argument(
        "--min-avg-volume",
        type=int,
        default=30000,
        help=(
            "Pre-filter: 平均出来高の最低値（株数）。"
            "日本株デフォルト=30,000株（≒3,000万円/日@1,000円）。"
            "US 版のデフォルト 200,000 は日本株には高すぎる (default: 30000)"
        ),
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=100,
        help="Pre-filter 後の VCP 分析最大銘柄数 (default: 100)",
    )
    parser.add_argument(
        "--top", type=int, default=20, help="レポートに含む上位件数 (default: 20)"
    )
    parser.add_argument(
        "--output-dir",
        default=_DEFAULT_OUTPUT_DIR,
        help=f"レポート出力先ディレクトリ (default: {_DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--universe", nargs="+", help="スクリーニング対象銘柄コード (例: 8141 8163 6248)"
    )
    parser.add_argument(
        "--full-sp500",
        action="store_true",
        help="TSE 全銘柄をスクリーニング (時間がかかる)",
    )
    parser.add_argument(
        "--mode",
        choices=["all", "prebreakout"],
        default="all",
        help="出力モード: 'all' = 全件, 'prebreakout' = entry_ready のみ (default: all)",
    )
    parser.add_argument(
        "--max-above-pivot",
        type=float,
        default=3.0,
        help="entry_ready 判定: ピボットからの最大上昇率 %% (default: 3.0)",
    )
    parser.add_argument(
        "--max-risk", type=float, default=15.0, help="entry_ready 判定: 最大リスク %% (default: 15.0)"
    )
    parser.add_argument(
        "--no-require-valid-vcp",
        action="store_true",
        help="valid_vcp=True を entry_ready の必須条件にしない",
    )
    parser.add_argument(
        "--min-atr-pct",
        type=float,
        default=1.0,
        help="ストール銘柄除外: 日次平均レンジの最低 %% (default: 1.0)",
    )
    parser.add_argument(
        "--ext-threshold",
        type=float,
        default=8.0,
        help="SMA50 乖離率 %% (extended ペナルティ開始点, default: 8.0)",
    )
    parser.add_argument(
        "--min-contractions",
        type=int,
        default=2,
        help="有効 VCP の最低収縮回数 (default: 2)",
    )
    parser.add_argument(
        "--t1-depth-min",
        type=float,
        default=10.0,
        help="T1 収縮の最低深さ %% (default: 10.0)",
    )
    parser.add_argument(
        "--breakout-volume-ratio",
        type=float,
        default=1.5,
        help="ブレイク出来高 / 50日平均 の最低倍率 (default: 1.5)",
    )
    parser.add_argument(
        "--trend-min-score",
        type=float,
        default=85.0,
        help="トレンドテンプレート最低スコア (default: 85.0)",
    )
    parser.add_argument(
        "--tt-min-above-low",
        type=float,
        default=25.0,
        help=(
            "TT c5 criterion: 52 週安値からの最低上昇率 %% (default: 25.0)。"
            "20.0 に下げると c5 が 20%%〜25%% の銘柄にも +14.3pt が加算される。"
            "JP 株の Stage2 判定を緩和したい場合は --tt-min-above-low 20 --trend-min-score 50 を組合せる"
        ),
    )
    parser.add_argument(
        "--atr-multiplier",
        type=float,
        default=1.5,
        help="ZigZag スイング検出の ATR 倍率 (default: 1.5)",
    )
    parser.add_argument(
        "--contraction-ratio",
        type=float,
        default=0.70,
        help="収縮比率の最大値 (default: 0.70)",
    )
    parser.add_argument(
        "--min-contraction-days",
        type=int,
        default=5,
        help="1 収縮あたりの最低日数 (default: 5)",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=120,
        help="VCP パターン検索ウィンドウ日数 (default: 120)",
    )
    parser.add_argument(
        "--max-sma200-extension",
        type=float,
        default=50.0,
        help="SMA200 乖離率上限 %% (Overextended 状態閾値, default: 50.0)",
    )
    parser.add_argument(
        "--wide-and-loose-threshold",
        type=float,
        default=15.0,
        help="最終収縮深さ %% (wide-and-loose フラグ閾値, default: 15.0)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Minervini strict モード: valid_vcp=True かつ "
            "execution_state が Pre-breakout / Breakout の銘柄のみ"
        ),
    )

    args = parser.parse_args()

    if not (2 <= args.min_contractions <= 4):
        parser.error("--min-contractions must be 2-4")
    if not (1.0 <= args.t1_depth_min <= 50.0):
        parser.error("--t1-depth-min must be 1.0-50.0")
    if not (0.5 <= args.breakout_volume_ratio <= 10.0):
        parser.error("--breakout-volume-ratio must be 0.5-10.0")
    if not (0 <= args.trend_min_score <= 100):
        parser.error("--trend-min-score must be 0-100")
    if not (5.0 <= args.tt_min_above_low <= 50.0):
        parser.error("--tt-min-above-low must be 5.0-50.0")
    if not (0.5 <= args.atr_multiplier <= 5.0):
        parser.error("--atr-multiplier must be 0.5-5.0")
    if not (0.1 <= args.contraction_ratio <= 1.0):
        parser.error("--contraction-ratio must be 0.1-1.0")
    if not (1 <= args.min_contraction_days <= 30):
        parser.error("--min-contraction-days must be 1-30")
    if not (30 <= args.lookback_days <= 365):
        parser.error("--lookback-days must be 30-365")

    return args


def passes_trend_filter(
    tt_result: dict,
    trend_min_score: float = 85.0,
    min_above_low_pct: float = 25.0,
    quote: Optional[dict] = None,
) -> bool:
    """Phase 2 トレンドテンプレートフィルターを通過するか判定.

    raw_score (extended ペナルティ前) を使用するため、CLI の --trend-min-score で
    計算機ハードコード値をオーバーライドできる。

    min_above_low_pct < 25.0 の場合、TT 計算機にハードコードされた c5 閾値 (25%) を
    緩和する。quote が渡されていれば c5 を再評価し、緩和後閾値を満たす場合は +14.3pt
    を補正スコアに加算する（元の TT 計算機コードを変更しない非侵襲的な実装）。
    """
    raw_score = tt_result.get("raw_score", 0)

    # c5 緩和: --tt-min-above-low 20 等で閾値を 25% から下げた場合、
    # c5 が元の 25% 判定で落ちていても緩和後閾値を満たすなら +14.3pt 補正
    if min_above_low_pct < 25.0 and quote is not None:
        c5 = tt_result.get("criteria", {}).get("c5_25pct_above_52w_low", {})
        if not c5.get("passed"):
            year_low = float(quote.get("yearLow") or 0)
            price = float(quote.get("price") or 0)
            if year_low > 0 and price > 0:
                pct_above = (price - year_low) / year_low * 100
                if pct_above >= min_above_low_pct:
                    raw_score += 14.3  # c5 相当分を加算

    return raw_score >= trend_min_score


def pre_filter_stock(quote: dict, min_avg_volume: int = 30000) -> tuple:
    """クォートデータだけで行う安価な Pre-filter.

    条件:
    - Price > 10 (JPY の場合は実質 no-filter; 廃止・停止銘柄除外のみ)
    - 52 週安値から 20% 以上上昇
    - 52 週高値から 30% 以内
    - 平均出来高 > min_avg_volume 株
      (US 版デフォルト 200,000 は日本株に高すぎる; JP 版デフォルト 30,000)

    Args:
        quote: get_batch_quotes() の返却値。
        min_avg_volume: 平均出来高の最低値（株数）。

    Returns:
        (passed: bool, stage2_likelihood_score: float)
    """
    price = quote.get("price", 0)
    year_high = quote.get("yearHigh", 0)
    year_low = quote.get("yearLow", 0)
    avg_volume = quote.get("avgVolume", 0)

    if price <= 10:
        return False, 0
    if avg_volume < min_avg_volume:
        return False, 0

    # 52 週安値からの距離
    if year_low <= 0:
        return False, 0
    pct_above_low = (price - year_low) / year_low
    if pct_above_low < 0.20:
        return False, 0

    # 52 週高値からの距離
    if year_high <= 0:
        return False, 0
    pct_below_high = (year_high - price) / year_high
    if pct_below_high > 0.30:
        return False, 0

    # Stage 2 らしさスコア (0-100)
    capped_above_low = min(pct_above_low, 1.0)
    score = capped_above_low * 50 + (1 - pct_below_high) * 50

    return True, score


def analyze_stock(
    symbol: str,
    historical: list[dict],
    quote: dict,
    sp500_history: list[dict],
    sector: str = "Unknown",
    company_name: str = "",
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
) -> Optional[dict]:
    """1 銘柄の VCP 全分析 (Phase 3). 追加 API 呼び出しなし."""
    price = quote.get("price", 0)
    market_cap = quote.get("marketCap", 0)

    # 1. 相対強度 (Trend Template criterion 7 に必要)
    rs_result = calculate_relative_strength(historical, sp500_history)
    rs_rank = rs_result.get("rs_rank_estimate", 0)

    # 2. トレンドテンプレート
    tt_result = calculate_trend_template(
        historical,
        quote,
        rs_rank=rs_rank,
        ext_threshold=ext_threshold,
        max_sma200_extension=max_sma200_extension,
    )

    # 3. VCP パターン検出
    vcp_result = calculate_vcp_pattern(
        historical,
        lookback_days=lookback_days,
        atr_multiplier=atr_multiplier,
        min_contraction_days=min_contraction_days,
        min_contractions=min_contractions,
        t1_depth_min=t1_depth_min,
        contraction_ratio=contraction_ratio,
        wide_and_loose_threshold=wide_and_loose_threshold,
    )

    # 4. 出来高パターン
    pivot_price = vcp_result.get("pivot_price")
    vol_result = calculate_volume_pattern(
        historical,
        pivot_price=pivot_price,
        contractions=vcp_result.get("contractions"),
        breakout_volume_ratio=breakout_volume_ratio,
    )

    # 5. ピボット近接
    last_low = None
    contractions = vcp_result.get("contractions", [])
    if contractions:
        last_low = contractions[-1].get("low_price")

    piv_result = calculate_pivot_proximity(
        current_price=price,
        pivot_price=pivot_price,
        last_contraction_low=last_low,
        breakout_volume=vol_result.get("breakout_volume_detected", False),
    )

    # 6. Execution State
    sma200_tt = tt_result.get("sma200")
    sma200_distance_pct: Optional[float] = None
    if sma200_tt and sma200_tt > 0:
        sma200_distance_pct = (price - sma200_tt) / sma200_tt * 100

    exec_state_result = compute_execution_state(
        distance_from_pivot_pct=piv_result.get("distance_from_pivot_pct"),
        price=price,
        sma50=tt_result.get("sma50"),
        sma200=sma200_tt,
        sma200_distance_pct=sma200_distance_pct,
        last_contraction_low=last_low,
        breakout_volume=vol_result.get("breakout_volume_detected", False),
        max_sma200_extension=max_sma200_extension,
    )
    execution_state = exec_state_result["state"]

    # 7. パターン分類
    valid_vcp = vcp_result.get("valid_vcp", False)
    wide_and_loose = vcp_result.get("wide_and_loose", False)
    final_depth = contractions[-1].get("depth_pct") if contractions else None

    pattern_type = classify_pattern(
        valid_vcp=valid_vcp,
        num_contractions=vcp_result.get("num_contractions", 0),
        final_contraction_depth=final_depth,
        execution_state=execution_state,
        dry_up_ratio=vol_result.get("dry_up_ratio"),
        wide_and_loose=wide_and_loose,
    )

    # 8. 複合スコア (State Caps 込み)
    composite = calculate_composite_score(
        trend_score=tt_result.get("score", 0),
        contraction_score=vcp_result.get("score", 0),
        volume_score=vol_result.get("score", 0),
        pivot_score=piv_result.get("score", 0),
        rs_score=rs_result.get("score", 0),
        valid_vcp=valid_vcp,
        execution_state=execution_state,
        pattern_type=pattern_type,
        wide_and_loose=wide_and_loose,
        sma200_extension_pct=sma200_distance_pct,
    )

    return {
        "symbol": symbol,
        "company_name": company_name,
        "sector": sector,
        "price": price,
        "market_cap": market_cap,
        "composite_score": composite["composite_score"],
        "quality_rating": composite.get("quality_rating", composite["rating"]),
        "rating": composite["rating"],
        "rating_description": composite["rating_description"],
        "guidance": composite["guidance"],
        "valid_vcp": valid_vcp,
        "execution_state": execution_state,
        "execution_state_reasons": exec_state_result.get("reasons", []),
        "pattern_type": pattern_type,
        "wide_and_loose": wide_and_loose,
        "state_cap_applied": composite.get("state_cap_applied", False),
        "cap_reason": composite.get("cap_reason"),
        "sma200_distance_pct": round(sma200_distance_pct, 1)
        if sma200_distance_pct is not None
        else None,
        "distance_from_pivot_pct": piv_result.get("distance_from_pivot_pct"),
        "weakest_component": composite["weakest_component"],
        "weakest_score": composite["weakest_score"],
        "strongest_component": composite["strongest_component"],
        "strongest_score": composite["strongest_score"],
        "trend_template": tt_result,
        "vcp_pattern": vcp_result,
        "volume_pattern": vol_result,
        "pivot_proximity": piv_result,
        "relative_strength": rs_result,
    }


def is_stale_price(
    historical: list[dict],
    lookback: int = 10,
    threshold: float = 1.0,
) -> bool:
    """買収・値付き停止などで価格が硬直している銘柄を検出.

    Args:
        historical: 価格データ (最新が先頭)
        lookback: チェックする直近日数
        threshold: 平均日次レンジ %% の上限 (これ未満なら stale 判定)

    Returns:
        True = stale (スキップ推奨)
    """
    if len(historical) < lookback:
        return False

    recent = historical[:lookback]
    ranges = []
    for bar in recent:
        high = bar.get("high", 0)
        low = bar.get("low", 0)
        close = bar.get("close", 0)
        if close > 0:
            ranges.append((high - low) / close * 100)

    if not ranges:
        return False

    avg_range_pct = sum(ranges) / len(ranges)
    return avg_range_pct < threshold


def compute_entry_ready(
    result: dict,
    max_above_pivot: float = 3.0,
    max_risk: float = 15.0,
    require_valid_vcp: bool = True,
) -> bool:
    """entry_ready フラグを計算する.

    Args:
        result: analyze_stock() の結果 dict
        max_above_pivot: ピボット超過の最大 %%
        max_risk: 最大リスク %%
        require_valid_vcp: valid_vcp=True を必須とするか
    """
    state = result.get("execution_state")
    if state in ("Invalid", "Damaged", "Overextended", "Extended", "Early-post-breakout"):
        return False

    valid_vcp = result.get("valid_vcp", False)
    distance = result.get("distance_from_pivot_pct")
    dry_up_ratio = result.get("volume_pattern", {}).get("dry_up_ratio")
    risk_pct = result.get("pivot_proximity", {}).get("risk_pct")

    if require_valid_vcp and not valid_vcp:
        return False
    if distance is None:
        return False
    if not (-8.0 <= distance <= max_above_pivot):
        return False
    if dry_up_ratio is None or dry_up_ratio > 1.0:
        return False
    trade_status = result.get("pivot_proximity", {}).get("trade_status")
    if trade_status == "BELOW STOP LEVEL":
        return False
    if risk_pct is None or risk_pct <= 0 or risk_pct > max_risk:
        return False
    return True


def main():
    args = parse_arguments()

    if not (0 < args.ext_threshold < 50):
        print("ERROR: --ext-threshold must be between 0 and 50 (exclusive)", file=sys.stderr)
        sys.exit(1)

    print("=" * 70)
    print("VCP Stock Screener (J-Quants/BQ 版 — 東証銘柄)")
    print("Mark Minervini's Volatility Contraction Pattern")
    print("=" * 70)
    print()

    # ★ FMPClient → JQuantsBQClient に差し替え (変更点 2)
    date_label = args.date or "(今日 JST)"
    print(f"J-Quants/BQ クライアント初期化 (評価日: {date_label}, BQ からデータ取得中)...", flush=True)
    client = JQuantsBQClient(date_to=args.date)
    print("  完了")

    # ========================================================================
    # Phase 1: Pre-Filter (API-efficient)
    # ========================================================================
    print()
    print("Phase 1: Pre-Filter")
    print("-" * 70)

    # ユニバース決定
    if args.universe:
        symbols = [str(s) for s in args.universe]
        universe_desc = f"Custom ({len(symbols)} stocks)"
        constituents = None
        print(f"  カスタムユニバース: {len(symbols)} 銘柄")
    else:
        print("  TSE 銘柄リスト取得...", end=" ", flush=True)
        constituents = client.get_sp500_constituents()
        if not constituents:
            print("FAILED")
            print("ERROR: TSE 銘柄リストを取得できませんでした", file=sys.stderr)
            sys.exit(1)
        symbols = [c["symbol"] for c in constituents]
        # ★ "S&P 500" → "TSE" (変更点 3)
        universe_desc = f"TSE ({len(symbols)} stocks)"
        print(f"OK ({len(symbols)} 銘柄)")

    # セクター・銘柄名ルックアップ
    sector_map = {}
    name_map = {}
    if not args.universe and constituents:
        for c in constituents:
            sector_map[c["symbol"]] = c.get("sector", "Unknown")
            name_map[c["symbol"]] = c.get("name", c["symbol"])

    # 一括クォート取得
    print("  クォート取得...", end=" ", flush=True)
    all_quotes = client.get_batch_quotes(symbols)
    print(f"OK ({len(all_quotes)} 件)")

    # Pre-filter 適用
    print("  Pre-filter 適用...", end=" ", flush=True)
    pre_filtered = []
    for sym in symbols:
        quote = all_quotes.get(sym)
        if not quote:
            continue
        passed, likelihood = pre_filter_stock(quote, min_avg_volume=args.min_avg_volume)
        if passed:
            pre_filtered.append((sym, likelihood, quote))

    # Stage 2 らしさ降順でソートし上位を候補に
    pre_filtered.sort(key=lambda x: x[1], reverse=True)
    max_candidates = len(pre_filtered) if args.full_sp500 else args.max_candidates
    candidates = pre_filtered[:max_candidates]

    print(f"{len(pre_filtered)} 通過, 上位 {len(candidates)} を採用")
    print()

    # ========================================================================
    # Phase 2: Trend Template Filter
    # ========================================================================
    print("Phase 2: Trend Template Filter")
    print("-" * 70)

    # TOPIX 履歴取得 (RS 計算用 — "SPY" は JQuantsBQClient 内で TOPIX にマッピング)
    print("  TOPIX 260 日履歴取得...", end=" ", flush=True)
    spy_data = client.get_historical_prices("SPY", days=260)
    sp500_history = spy_data.get("historical", []) if spy_data else []
    if sp500_history:
        print(f"OK ({len(sp500_history)} 日)")
    else:
        print("WARN — TOPIX データなし, RS 計算は制限あり")

    # 候補銘柄の 260 日履歴取得
    candidate_symbols = [c[0] for c in candidates]
    print(f"  {len(candidate_symbols)} 銘柄の 260 日履歴取得...")

    candidate_histories = {}
    for i, sym in enumerate(candidate_symbols):
        if (i + 1) % 20 == 0 or i == len(candidate_symbols) - 1:
            print(f"    進捗: {i + 1}/{len(candidate_symbols)}", flush=True)
        data = client.get_historical_prices(sym, days=260)
        if data and "historical" in data:
            candidate_histories[sym] = data["historical"]

    # 7 点 Trend Template フィルター適用
    print("  7 点 Trend Template 適用...", end=" ", flush=True)
    trend_passed = []

    for sym, likelihood, quote in candidates:
        hist = candidate_histories.get(sym, [])
        if not hist or len(hist) < 50:
            continue

        rs_result = calculate_relative_strength(hist, sp500_history)
        rs_rank = rs_result.get("rs_rank_estimate", 0)

        tt_result = calculate_trend_template(
            hist, quote, rs_rank=rs_rank, ext_threshold=args.ext_threshold
        )
        if passes_trend_filter(
            tt_result,
            args.trend_min_score,
            min_above_low_pct=args.tt_min_above_low,
            quote=quote,
        ):
            trend_passed.append((sym, quote))

    print(f"{len(trend_passed)} 通過")
    print()

    # ========================================================================
    # Phase 3: VCP Detection & Scoring
    # ========================================================================
    print("Phase 3: VCP Detection & Scoring")
    print("-" * 70)

    results = []
    for sym, quote in trend_passed:
        hist = candidate_histories.get(sym, [])
        sector = sector_map.get(sym, "Unknown")
        name = name_map.get(sym, sym)

        # カスタムユニバースの場合クォートから名前・セクターを補完
        if not name or name == sym:
            name = quote.get("name", sym)
        if not sector or sector == "Unknown":
            sector = quote.get("sector", "Unknown")

        # ストール・買収済み銘柄をスキップ
        if is_stale_price(hist, threshold=args.min_atr_pct):
            print(f"  スキップ {sym} (stale price — 買収済み可能性)")
            continue

        print(f"  分析中 {sym}...", end=" ", flush=True)
        analysis = analyze_stock(
            sym,
            hist,
            quote,
            sp500_history,
            sector,
            name,
            ext_threshold=args.ext_threshold,
            min_contractions=args.min_contractions,
            t1_depth_min=args.t1_depth_min,
            contraction_ratio=args.contraction_ratio,
            atr_multiplier=args.atr_multiplier,
            min_contraction_days=args.min_contraction_days,
            lookback_days=args.lookback_days,
            breakout_volume_ratio=args.breakout_volume_ratio,
            max_sma200_extension=args.max_sma200_extension,
            wide_and_loose_threshold=args.wide_and_loose_threshold,
        )

        if analysis:
            score = analysis["composite_score"]
            print(f"Score: {score:.1f} ({analysis['rating']})")
            results.append(analysis)
        else:
            print("FAILED")

    print()

    # 全ユニバースで RS を再ランク付け
    if results:
        rs_map = {r["symbol"]: r["relative_strength"] for r in results}
        ranked_rs = rank_relative_strength_universe(rs_map)
        for r in results:
            r["relative_strength"] = ranked_rs[r["symbol"]]
            composite = calculate_composite_score(
                trend_score=r["trend_template"].get("score", 0),
                contraction_score=r["vcp_pattern"].get("score", 0),
                volume_score=r["volume_pattern"].get("score", 0),
                pivot_score=r["pivot_proximity"].get("score", 0),
                rs_score=r["relative_strength"].get("score", 0),
                valid_vcp=r.get("valid_vcp", False),
                execution_state=r.get("execution_state"),
                pattern_type=r.get("pattern_type"),
                wide_and_loose=r.get("wide_and_loose", False),
                sma200_extension_pct=r.get("sma200_extension_pct"),
            )
            r["composite_score"] = composite["composite_score"]
            r["quality_rating"] = composite.get("quality_rating", composite["rating"])
            r["rating"] = composite["rating"]
            r["rating_description"] = composite["rating_description"]
            r["guidance"] = composite["guidance"]
            r["weakest_component"] = composite["weakest_component"]
            r["weakest_score"] = composite["weakest_score"]
            r["strongest_component"] = composite["strongest_component"]
            r["strongest_score"] = composite["strongest_score"]
            r["state_cap_applied"] = composite.get("state_cap_applied", False)
            r["cap_reason"] = composite.get("cap_reason")

    # entry_ready フラグ計算
    require_vcp = not args.no_require_valid_vcp
    for r in results:
        r["entry_ready"] = compute_entry_ready(
            r,
            max_above_pivot=args.max_above_pivot,
            max_risk=args.max_risk,
            require_valid_vcp=require_vcp,
        )

    # 複合スコア降順でソート
    results.sort(key=lambda x: x["composite_score"], reverse=True)

    # prebreakout フィルター
    if args.mode == "prebreakout":
        total_before = len(results)
        results = [r for r in results if r.get("entry_ready", False)]
        print(f"  Pre-breakout フィルター: {total_before} → {len(results)} 候補")
        print()

    # strict モードフィルター
    if args.strict:
        total_before = len(results)
        results = [
            r
            for r in results
            if r.get("valid_vcp", False)
            and r.get("execution_state") in ("Pre-breakout", "Breakout")
        ]
        print(f"  Strict モードフィルター: {total_before} → {len(results)} 候補")
        print()

    # ========================================================================
    # レポート生成
    # ========================================================================
    print("レポート生成")
    print("-" * 70)

    os.makedirs(args.output_dir, exist_ok=True)
    timestamp = datetime.now(tz=_JST).strftime("%Y-%m-%d_%H%M%S")
    json_file = os.path.join(args.output_dir, f"vcp_jp_{timestamp}.json")
    md_file = os.path.join(args.output_dir, f"vcp_jp_{timestamp}.md")

    api_stats = client.get_api_stats()

    metadata = {
        "generated_at": datetime.now(tz=_JST).strftime("%Y-%m-%d %H:%M:%S JST"),
        "universe_description": universe_desc,
        "max_candidates": max_candidates,
        "ext_threshold": args.ext_threshold,
        "tuning_params": {
            "min_contractions": args.min_contractions,
            "t1_depth_min": args.t1_depth_min,
            "breakout_volume_ratio": args.breakout_volume_ratio,
            "trend_min_score": args.trend_min_score,
            "atr_multiplier": args.atr_multiplier,
            "contraction_ratio": args.contraction_ratio,
            "min_contraction_days": args.min_contraction_days,
            "lookback_days": args.lookback_days,
            "max_sma200_extension": args.max_sma200_extension,
            "wide_and_loose_threshold": args.wide_and_loose_threshold,
            "strict": args.strict,
        },
        "funnel": {
            "universe": len(symbols),
            "pre_filter_passed": len(pre_filtered),
            "trend_template_passed": len(trend_passed),
            "vcp_candidates": len(results),
        },
        "api_stats": api_stats,
    }

    top_results = results[: args.top]

    generate_json_report(top_results, metadata, json_file, all_results=results)
    generate_markdown_report(top_results, metadata, md_file, all_results=results)

    # ========================================================================
    # サマリー表示
    # ========================================================================
    print()
    print("=" * 70)
    print("VCP スクリーニング完了")
    print("=" * 70)

    if results:
        print()
        print(f"上位 {min(5, len(results))} 銘柄:")
        for i, s in enumerate(results[:5], 1):
            pivot = s.get("vcp_pattern", {}).get("pivot_price")
            pivot_str = f"Pivot: {pivot:.0f}" if pivot else ""
            print(
                f"  {i}. {s['symbol']:6} {s.get('company_name', '')[:12]:12} "
                f"Score: {s['composite_score']:5.1f} ({s['rating']}) "
                f"{s.get('execution_state', ''):15} {pivot_str}"
            )
    else:
        print()
        print("  VCP 候補なし（全フィルター通過銘柄ゼロ）")

    print()
    print(f"  JSON レポート:     {json_file}")
    print(f"  Markdown レポート: {md_file}")
    print()
    print("データソース統計:")
    for k, v in api_stats.items():
        print(f"  {k}: {v}")
    print()


if __name__ == "__main__":
    main()

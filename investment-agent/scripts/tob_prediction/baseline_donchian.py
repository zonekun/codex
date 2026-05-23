"""Donchian 60-day Breakout ベースライン スクリーナー（Donchian 4-week rule 拡張版）.

学術論文・古典に忠実な単純ルール。積スコアや独自重み付けを排し、AND条件のみで
判定する。インサイダー検知スクリーナー(v1〜v3)との比較ベースライン Phase 0-3。

ルール (Donchian "4-week rule" を 60 日に拡張 + Keown&Pinkerton 1981 の
abnormal volume 概念を組合せ):
  1. 静止: 過去 60 日のレンジ (高値-安値)/SMA の 120 日 percentile が
     **前日時点で下位 50%** に滞在（ヨコヨコ"だった"条件、shift(1) で当日除外）
  2. 発火: **前日まで close <= 過去 60 営業日 close 最高値** かつ
     **当日 close > 過去 60 営業日 close 最高値**（cross-up 新高値更新）
     注: 伝統的 Donchian は HIGH 系列の最高値だが、本実装は close ベース
     （引け値ブレイクのシグナル性を優先、ヒゲの偽ブレイクを除外）
  3. 出来高: 当日 ADJ_VOLUME が **20 日平均の 2 倍以上**

全条件 AND を満たす銘柄のみ抽出（積スコアによる順位付けなし）。
件数が --top-n を超える場合のみ vol_ratio_20d 降順で絞る。

下落トレンド中の戻り（4073 ジィ・シィ企画型）を**新高値更新条件で排除**できる。
摘出目標は 8141 新光商事型: ヨコヨコ→新高値ブレイク→継続上昇。

Usage:
    PYTHONUTF8=1 python scripts/tob_prediction/baseline_donchian.py
    PYTHONUTF8=1 python scripts/tob_prediction/baseline_donchian.py --date 2026-05-21
    PYTHONUTF8=1 python scripts/tob_prediction/baseline_donchian.py --top-n 200

参照:
  - docs/references/README.md #28 Keown&Pinkerton(1981)
  - docs/plans/analysis-015_insider_pattern_mismatch_20260521_205454.md Phase 0-3
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import structlog

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "tob_prediction"))

from screen_tob_insider import fetch_ohlcv  # noqa: E402

OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
JST = timezone(timedelta(hours=+9), "JST")

# Donchian Breakout ルールのパラメータ（学術古典準拠の固定値）
DONCHIAN_DAYS = 60        # 過去 N 日高値ブレイクの参照期間（Donchian 4-week → 60 日拡張）
SMA_WINDOW = 20           # レンジ正規化用 SMA 期間
RANGE_DAYS = 60           # レンジ計算窓
RANGE_PCT_WINDOW = 120    # レンジ percentile の参照期間
RANGE_PCT_THRESHOLD = 0.50  # 下位 50% で「ヨコヨコ」判定
VOL_MA_DAYS = 20          # 出来高移動平均の期間
VOL_RATIO_TRIGGER = 2.0   # 出来高急増判定（Keown&Pinkerton 1981 の abnormal volume）
FETCH_DAYS = 400          # 取得日数（既存 screen_tob_insider.py と統一）
MIN_DATA_DAYS = 180       # 銘柄あたり最低必要営業日数

log = structlog.get_logger()


def evaluate_ticker(grp: pd.DataFrame) -> pd.DataFrame:
    """単一銘柄の全日付について Donchian Breakout 条件を判定.

    Args:
        grp: 単一銘柄の OHLCV DataFrame（DATE, TICKER, STOCK_NAME, ADJ_* 列）

    Returns:
        条件評価列を追加した DataFrame。データ不足なら空 DataFrame。
    """
    grp = grp.sort_values("DATE").reset_index(drop=True)
    if len(grp) < MIN_DATA_DAYS:
        return pd.DataFrame()

    c = grp["ADJ_CLOSE"]
    h = grp["ADJ_HIGH"]
    lo = grp["ADJ_LOW"]
    v = grp["ADJ_VOLUME"]

    # Donchian: 過去 DONCHIAN_DAYS 日の close 最高値（当日を除外 → shift(1)）
    # close ベース採用理由は module docstring 参照（ヒゲ偽ブレイク除外）
    max_n_prev = c.shift(1).rolling(
        DONCHIAN_DAYS, min_periods=DONCHIAN_DAYS
    ).max()
    # 前日の Donchian 高値（cross-up 判定用、前日 close との比較に使う）
    max_n_prev_prev = max_n_prev.shift(1)

    # レンジ (HIGH-LOW)/SMA の 60 日窓 + 120 日 percentile
    # レンジは実体ボラを HIGH/LOW で測る方がノイズに強い（cross-up は close ベース、
    # 用途別に OHLC を使い分ける）
    sma = c.rolling(SMA_WINDOW, min_periods=SMA_WINDOW).mean()
    range_60 = (
        h.rolling(RANGE_DAYS, min_periods=RANGE_DAYS).max()
        - lo.rolling(RANGE_DAYS, min_periods=RANGE_DAYS).min()
    ) / sma.replace(0.0, np.nan)
    range_pct = range_60.rolling(
        RANGE_PCT_WINDOW, min_periods=RANGE_PCT_WINDOW
    ).rank(pct=True)

    # 出来高比率（20 日平均）
    vol_ma = v.rolling(VOL_MA_DAYS, min_periods=VOL_MA_DAYS).mean()
    vol_ratio = v / vol_ma.replace(0.0, np.nan)

    # Donchian 超過幅（cross-up したときの「どれだけ上抜けたか」の強度指標）
    breakout_excess = (c - max_n_prev) / max_n_prev.replace(0.0, np.nan)

    result = grp[["DATE", "TICKER", "STOCK_NAME", "ADJ_CLOSE"]].copy()
    result["donchian_high_60d"] = max_n_prev.values
    result["breakout_excess"] = breakout_excess.values
    result["range_60d"] = range_60.values
    result["range_pct_120d"] = range_pct.values
    result["vol_ratio_20d"] = vol_ratio.values

    # 静止: 前日時点の 60 日レンジが下位 50% percentile（ヨコヨコ"だった"条件）
    cond_quiet = range_pct.shift(1) < RANGE_PCT_THRESHOLD
    # 発火: 前日まで Donchian 60 日 close 最高値以下、当日上抜け = cross-up
    cond_breakout = (c > max_n_prev) & (c.shift(1) <= max_n_prev_prev)
    # 出来高: 20 日平均の 2 倍以上
    cond_volume = vol_ratio >= VOL_RATIO_TRIGGER

    result["hit"] = (cond_quiet & cond_breakout & cond_volume).fillna(False).values
    return result


def screen(target_date: str, top_n: int) -> pd.DataFrame:
    """target_date 時点で Donchian Breakout 3 条件 AND を満たす銘柄を返す."""
    target_dt = datetime.fromisoformat(target_date).date()
    date_from = (target_dt - timedelta(days=FETCH_DAYS)).isoformat()

    ohlcv = fetch_ohlcv(date_from, target_date)
    log.info(
        "ohlcv_loaded", rows=len(ohlcv), tickers=ohlcv["TICKER"].nunique()
    )

    evaluated_parts: list[pd.DataFrame] = []
    for _, grp in ohlcv.groupby("TICKER"):
        part = evaluate_ticker(grp)
        if not part.empty:
            evaluated_parts.append(part)
    if not evaluated_parts:
        log.warning("no_data")
        return pd.DataFrame()

    evaluated = pd.concat(evaluated_parts, ignore_index=True)
    latest = (
        evaluated[evaluated["DATE"] <= target_date]
        .sort_values("DATE")
        .groupby("TICKER", as_index=False)
        .last()
    )
    log.info(
        "evaluated", tickers=len(latest), hits=int(latest["hit"].sum())
    )

    hits = latest[latest["hit"]].copy()
    hits = hits.sort_values("vol_ratio_20d", ascending=False).reset_index(drop=True)
    if len(hits) > top_n:
        hits = hits.head(top_n)
    hits.insert(0, "rank", range(1, len(hits) + 1))
    return hits


def main() -> None:
    parser = argparse.ArgumentParser(description="Donchian Breakout ベースラインスクリーナー")
    parser.add_argument(
        "--date", default=None,
        help="評価対象日 YYYY-MM-DD（省略時は今日 JST）",
    )
    parser.add_argument("--top-n", type=int, default=100, help="最大出力件数")
    args = parser.parse_args()

    target_date = args.date or datetime.now(tz=JST).date().isoformat()
    log.info("screen_start", target_date=target_date)

    hits = screen(target_date, args.top_n)

    display_cols = [
        "rank", "TICKER", "DATE", "STOCK_NAME", "ADJ_CLOSE",
        "donchian_high_60d", "breakout_excess", "range_pct_120d", "vol_ratio_20d",
    ]

    if hits.empty:
        print(f"\n=== Donchian Breakout ベースライン {target_date} (hits=0) ===")
        print("該当銘柄なし（AND 3 条件を満たす銘柄なし）")
        log.info("done", output=None, hits=0)
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"baseline_donchian_{target_date.replace('-', '')}.csv"
    hits[display_cols].to_csv(out_path, index=False, encoding="utf-8-sig")

    print(f"\n=== Donchian Breakout ベースライン {target_date} (hits={len(hits)}) ===")
    print(hits[display_cols].head(20).to_string(index=False))
    print(f"\n→ {out_path}")
    log.info("done", output=str(out_path), hits=len(hits))


if __name__ == "__main__":
    main()

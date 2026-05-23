"""Bollinger Squeeze ベースライン スクリーナー（Bollinger 2001 準拠）.

学術論文・古典に忠実な単純ルール。積スコアや独自重み付けを排し、AND条件のみで
判定する。インサイダー検知スクリーナー(v1〜v3)との比較ベースライン。

ルール (Bollinger "Bollinger on Bollinger Bands" 2001 + Keown&Pinkerton 1981 の
abnormal volume 概念を組合せ):
  1. 静止: BB幅 (4σ/sma) の直近 120 営業日 percentile が **下位 10%** に
     **前日まで 20 営業日連続** で滞在
  2. 発火: 当日 close が Upper Bollinger Band (sma + 2σ) を **新規に上抜け**
     (前日 close <= upper_bb, 当日 close > upper_bb の cross-up)。
     Bollinger 2001 の "Squeeze release" event に忠実なクロスオーバー判定。
  3. 出来高: 当日 ADJ_VOLUME が **20 日平均の 2 倍以上**

全条件 AND を満たす銘柄のみ抽出（積スコアによる順位付けなし）。
件数が --top-n を超える場合のみ vol_ratio_20d 降順で絞る。

Usage:
    PYTHONUTF8=1 python scripts/tob_prediction/baseline_bb_squeeze.py
    PYTHONUTF8=1 python scripts/tob_prediction/baseline_bb_squeeze.py --date 2026-05-21
    PYTHONUTF8=1 python scripts/tob_prediction/baseline_bb_squeeze.py --top-n 200

参照:
  - docs/references/README.md #28 Keown&Pinkerton(1981)
  - docs/references/README.md #29 Meulbroek(1992)
  - docs/references/README.md #30 Cornell&Sirri(1992)
  - docs/plans/analysis-015_insider_pattern_mismatch_20260521_205454.md Phase 0-2
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

# Bollinger Squeeze ルールのパラメータ（学術古典準拠の固定値）
BB_WINDOW = 20            # Bollinger Band の標準期間 (Bollinger 2001)
BB_PCT_WINDOW = 120       # BB幅 percentile の参照期間（営業日）
BB_PCT_THRESHOLD = 0.10   # 下位 10% で「静止」判定（Bollinger Squeeze の標準）
SQUEEZE_STREAK_DAYS = 20  # 静止が継続している必要がある営業日数
VOL_MA_DAYS = 20          # 出来高移動平均の期間
VOL_RATIO_TRIGGER = 2.0   # 出来高急増判定（Keown&Pinkerton 1981 の abnormal volume）
FETCH_DAYS = 400          # 取得日数（既存 screen_tob_insider.py と統一・バックテスト用途も想定）
MIN_DATA_DAYS = 180       # 銘柄あたり最低必要営業日数

log = structlog.get_logger()


def _consecutive_true(mask: pd.Series) -> pd.Series:
    """True が続く連続日数を返す（False で 0 リセット）."""
    s = mask.astype(float)
    cumsum_all = s.cumsum()
    cumsum_at_reset = cumsum_all.where(s == 0).ffill().fillna(0.0)
    return (cumsum_all - cumsum_at_reset).astype(int)


def evaluate_ticker(grp: pd.DataFrame) -> pd.DataFrame:
    """単一銘柄の全日付について BB Squeeze 条件を判定.

    Args:
        grp: 単一銘柄の OHLCV DataFrame（DATE, TICKER, STOCK_NAME, ADJ_* 列）

    Returns:
        条件評価列を追加した DataFrame。データ不足なら空 DataFrame。
    """
    grp = grp.sort_values("DATE").reset_index(drop=True)
    if len(grp) < MIN_DATA_DAYS:
        return pd.DataFrame()

    c = grp["ADJ_CLOSE"]
    v = grp["ADJ_VOLUME"]

    sma = c.rolling(BB_WINDOW, min_periods=BB_WINDOW).mean()
    std = c.rolling(BB_WINDOW, min_periods=BB_WINDOW).std()
    upper_bb = sma + 2.0 * std
    bb_width = (4.0 * std) / sma.replace(0.0, np.nan)

    bb_width_pct = bb_width.rolling(
        BB_PCT_WINDOW, min_periods=BB_PCT_WINDOW
    ).rank(pct=True)

    squeezed_today = bb_width_pct < BB_PCT_THRESHOLD
    squeeze_streak = _consecutive_true(squeezed_today)

    vol_ma = v.rolling(VOL_MA_DAYS, min_periods=VOL_MA_DAYS).mean()
    vol_ratio = v / vol_ma.replace(0.0, np.nan)

    result = grp[["DATE", "TICKER", "STOCK_NAME", "ADJ_CLOSE"]].copy()
    result["upper_bb"] = upper_bb.values
    result["bb_width"] = bb_width.values
    result["bb_width_pct_120d"] = bb_width_pct.values
    result["squeeze_streak_days"] = squeeze_streak.values
    result["vol_ratio_20d"] = vol_ratio.values

    # 静止: 前日まで SQUEEZE_STREAK_DAYS 連続 Squeeze（Bollinger 2001 release event）
    cond_squeeze = squeeze_streak.shift(1) >= SQUEEZE_STREAK_DAYS
    # 発火: 前日 close <= upper_bb かつ 当日 close > upper_bb （新規ブレイク = cross-up）
    cond_breakout = (c > upper_bb) & (c.shift(1) <= upper_bb.shift(1))
    cond_volume = vol_ratio >= VOL_RATIO_TRIGGER
    result["hit"] = (cond_squeeze & cond_breakout & cond_volume).fillna(False).values
    return result


def screen(target_date: str, top_n: int) -> pd.DataFrame:
    """target_date 時点で BB Squeeze 3条件 AND を満たす銘柄を返す."""
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
    parser = argparse.ArgumentParser(description="BB Squeeze ベースラインスクリーナー")
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
        "upper_bb", "bb_width_pct_120d", "squeeze_streak_days", "vol_ratio_20d",
    ]

    if hits.empty:
        print(f"\n=== BB Squeeze ベースライン {target_date} (hits=0) ===")
        print("該当銘柄なし（AND 3 条件を満たす銘柄なし）")
        log.info("done", output=None, hits=0)
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"baseline_bb_squeeze_{target_date.replace('-', '')}.csv"
    hits[display_cols].to_csv(out_path, index=False, encoding="utf-8-sig")

    print(f"\n=== BB Squeeze ベースライン {target_date} (hits={len(hits)}) ===")
    print(hits[display_cols].head(20).to_string(index=False))
    print(f"\n→ {out_path}")
    log.info("done", output=str(out_path), hits=len(hits))


if __name__ == "__main__":
    main()

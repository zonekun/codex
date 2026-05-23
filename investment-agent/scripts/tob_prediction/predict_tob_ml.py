"""TOB予測 ML モデル推論 — Phase 3-C-3.

学習済みモデル (data/models/tob_ml_lgbm_*.pkl) で指定日の全銘柄を予測し、
予測確率上位 N 件を CSV 出力する。

Usage:
    PYTHONUTF8=1 python scripts/tob_prediction/predict_tob_ml.py
    PYTHONUTF8=1 python scripts/tob_prediction/predict_tob_ml.py --date 2026-05-14
    PYTHONUTF8=1 python scripts/tob_prediction/predict_tob_ml.py --model data/models/tob_ml_lgbm_xxx.pkl
"""

from __future__ import annotations

import argparse
import pickle
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import structlog

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "tob_prediction"))

from build_ml_dataset import (  # noqa: E402
    FETCH_LOOKBACK_DAYS,
    compute_extra_features,
    fetch_master,
)
from screen_tob_insider import (  # noqa: E402
    attach_topix,
    compute_all_scores,
    fetch_topix,
)
from backtest_full_universe import fetch_ohlcv_full  # noqa: E402

MODEL_DIR = PROJECT_ROOT / "data" / "models"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
JST = timezone(timedelta(hours=+9), "JST")

log = structlog.get_logger()


def find_latest_model() -> Path:
    """優先順: tob_ml_lgbm_best.pkl → *best*.pkl → mtime 最新.

    意味ベース名（例: best, n20, smoke_*）と timestamp 名が混在する想定。
    本番運用ではベストモデルが固定パスで参照できるよう best を最優先する。
    """
    fixed_best = MODEL_DIR / "tob_ml_lgbm_best.pkl"
    if fixed_best.exists():
        return fixed_best

    best_matches = sorted(MODEL_DIR.glob("tob_ml_*best*.pkl"))
    if best_matches:
        return best_matches[-1]

    candidates = list(MODEL_DIR.glob("tob_ml_lgbm_*.pkl")) + list(
        MODEL_DIR.glob("tob_ml_xgb_*.pkl")
    )
    if not candidates:
        raise FileNotFoundError(f"No model found in {MODEL_DIR}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_model(path: Path) -> dict:
    with path.open("rb") as f:
        bundle = pickle.load(f)
    log.info(
        "model_loaded",
        path=str(path),
        train_end=bundle.get("train_end"),
        val_metrics=bundle.get("metrics"),
    )
    return bundle


def build_features_for_date(
    target_date: str, force_reload: bool = False
) -> pd.DataFrame:
    """target_date 用の特徴量行（全銘柄, 当日の latest snapshot）を生成."""
    target_dt = date.fromisoformat(target_date)
    date_from = (target_dt - timedelta(days=FETCH_LOOKBACK_DAYS)).isoformat()
    date_to = target_date

    ohlcv = fetch_ohlcv_full(date_from, date_to, force_reload=force_reload)
    topix = fetch_topix(date_from, date_to, force_reload=force_reload)
    ohlcv = attach_topix(ohlcv, topix)

    all_scored = compute_all_scores(ohlcv)
    if all_scored.empty:
        raise RuntimeError("no scores computed")

    extra = compute_extra_features(ohlcv)
    master = fetch_master()

    feats = all_scored.merge(extra, on=["TICKER", "DATE"], how="left")
    feats = feats.merge(master, on="TICKER", how="left")

    # target_date 時点の latest snapshot (per ticker)
    feats = (
        feats[feats["DATE"] <= target_date]
        .sort_values("DATE")
        .groupby("TICKER", as_index=False)
        .last()
    )
    return feats


def main() -> None:
    parser = argparse.ArgumentParser(description="TOB予測 ML 推論")
    parser.add_argument("--date", default=None, help="予測対象日 (YYYY-MM-DD, 省略時は今日 JST)")
    parser.add_argument("--model", default=None, help="モデル pkl path (省略時は最新)")
    parser.add_argument("--top-n", type=int, default=100)
    parser.add_argument("--force-reload", action="store_true")
    args = parser.parse_args()

    target_date = args.date or datetime.now(tz=JST).date().isoformat()
    log.info("predict_start", target_date=target_date)

    model_path = Path(args.model) if args.model else find_latest_model()
    bundle = load_model(model_path)
    model = bundle["model"]
    feat_cols = bundle["feat_cols"]
    cat_cols = bundle["cat_cols"]
    cat_categories = bundle.get("cat_categories", {})

    feats = build_features_for_date(target_date, force_reload=args.force_reload)
    log.info("features_built", rows=len(feats))

    X = feats[feat_cols].copy()
    for c in cat_cols:
        if c in cat_categories:
            X[c] = pd.Categorical(X[c], categories=cat_categories[c])
        else:
            X[c] = X[c].astype("category")

    feats["tob_ml_proba"] = model.predict_proba(X)[:, 1]

    feats_sorted = feats.sort_values("tob_ml_proba", ascending=False).reset_index(drop=True)
    feats_sorted.insert(0, "rank", range(1, len(feats_sorted) + 1))
    top = feats_sorted.head(args.top_n).copy()

    out_cols_priority = [
        "rank", "TICKER", "ADJ_CLOSE", "tob_ml_proba",
        "momentum_score", "momentum_score_v3", "dormancy_score",
        "ignition_score", "vol_ratio_20d", "ret_5d", "ret_20d",
        "INDUSTRY_33_CATEGORY", "MARKET_CATEGORY",
    ]
    out_cols = [c for c in out_cols_priority if c in top.columns]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"tob_ml_screen_{target_date.replace('-', '')}.csv"
    top[out_cols].to_csv(out_path, index=False, encoding="utf-8-sig")

    print(f"\n=== TOB ML 予測 {target_date} (Top {args.top_n}) ===")
    print(top[out_cols].head(20).to_string(index=False))
    print(f"\n→ {out_path}")
    log.info("done", output=str(out_path), rows=len(top))


if __name__ == "__main__":
    main()

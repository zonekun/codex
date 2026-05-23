"""TOB ML leak 検証スクリプト — Phase 3-C OOS lift=14 検証.

確認:
  1. dataset 内の (TICKER, DATE) 重複の有無
  2. val 上位100件の predict_proba と is_positive の対応
  3. positive サンプルの順位分布
  4. SHAP feature importance（モデルがどの特徴量に強く依存しているか）
"""
from __future__ import annotations
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = PROJECT_ROOT / "data" / "models" / "tob_ml_xgb_trial1.pkl"
DATASET_PATH = (
    PROJECT_ROOT / "data" / "cache" / "tob_ml"
    / "dataset_2024-01-01_2026-05-14_pw30_20260521_102352.parquet"
)


def main() -> None:
    print("loading dataset and model ...")
    df = pd.read_parquet(DATASET_PATH)
    df["DATE"] = df["DATE"].astype(str)
    df["TICKER"] = df["TICKER"].astype(str)
    with MODEL_PATH.open("rb") as f:
        bundle = pickle.load(f)
    model = bundle["model"]
    feat_cols = bundle["feat_cols"]
    cat_cols = bundle["cat_cols"]
    cat_cats = bundle["cat_categories"]

    # 1. 重複チェック
    dup = df.duplicated(subset=["TICKER", "DATE"], keep=False)
    print(f"\n[1] Duplicate (TICKER, DATE): {dup.sum():,} rows")
    if dup.sum() > 0:
        print(df[dup].sort_values(["TICKER", "DATE"]).head(10).to_string())

    # 2. val 予測
    val = df[df["DATE"] >= "2025-01-01"].reset_index(drop=True).copy()
    X = val[feat_cols].copy()
    for c in cat_cols:
        X[c] = pd.Categorical(X[c], categories=cat_cats[c])
    val["proba"] = model.predict_proba(X)[:, 1]
    val["is_positive"] = val["is_positive"].fillna(False).astype(bool)

    # 3. 上位 100 件
    print("\n[2] Top 100 predictions:")
    top = val.sort_values("proba", ascending=False).head(100)[
        ["DATE", "TICKER", "proba", "is_positive", "momentum_score_v3",
         "vol_ratio_20d", "INDUSTRY_33_CODE", "MARKET_CATEGORY"]
    ]
    print(top.to_string(index=False))
    print(f"\n  Top100 positive count: {int(top['is_positive'].sum())} / 100")

    # 4. positive サンプルの順位分布
    val["rank"] = val["proba"].rank(ascending=False, method="min")
    pos_ranks = val[val["is_positive"]]["rank"]
    print(f"\n[3] Positive rank distribution (n={len(pos_ranks):,}, total rows={len(val):,}):")
    print(pos_ranks.describe().to_string())
    print(f"  positive in top 1% ({int(len(val)*0.01):,}): {int((pos_ranks <= len(val)*0.01).sum())}")
    print(f"  positive in top 5% ({int(len(val)*0.05):,}): {int((pos_ranks <= len(val)*0.05).sum())}")

    # 5. positive 行を TICKER で集約 - 同一 TICKER がどれだけ上位を占めているか
    print("\n[4] Positive ticker concentration in top 100:")
    print(top[top["is_positive"]]["TICKER"].value_counts().head(10).to_string())

    # 6. val 期間内の TOB銘柄リスト（IR_FIRST_RELEASE_DATE）
    pos_tickers = sorted(val[val["is_positive"]]["TICKER"].unique())
    print(f"\n[5] Unique TOB tickers in val period: {len(pos_tickers)}")
    # それぞれの最大 proba
    max_proba_per_tob = (
        val[val["is_positive"]]
        .groupby("TICKER")["proba"].max()
        .sort_values(ascending=False)
    )
    print(f"  TOB ticker max_proba quantiles:")
    print(max_proba_per_tob.describe().to_string())

    # 7. 銘柄の TOB 直前30日中、何日くらいが上位 5% に入っているか
    threshold_5pct = val.sort_values("proba", ascending=False).iloc[
        int(len(val) * 0.05)
    ]["proba"]
    print(f"\n[6] Threshold for top 5%: proba >= {threshold_5pct:.4f}")
    in_top5_per_tob = (
        val[val["is_positive"]]
        .groupby("TICKER")
        .apply(lambda g: int((g["proba"] >= threshold_5pct).sum()))
    )
    print(f"  per TOB ticker (max=30 days):")
    print(in_top5_per_tob.describe().to_string())


if __name__ == "__main__":
    main()

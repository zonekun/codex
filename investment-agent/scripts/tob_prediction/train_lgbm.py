"""TOB予測 LightGBM モデル学習 — Phase 3-C-2.

学習データ: train_end まで
検証データ: val_start 〜 val_end（OOS）
CV: 学習データ内で GroupKFold（銘柄 group, 5-fold）→ Optuna ハイパーパラメータ最適化
評価: PR-AUC, lift@1%/5%/10%, SHAP

注意:
  STOCK_CODE_LIST マスタ列（INDUSTRY_*/MARKET_CATEGORY/SIZE_CODE）は「現在のスナップショット」
  のため、TOB 後に廃止された銘柄では NaN になる。これを特徴量に含めると「マスタ NaN ＝
  廃止済み」を学習する未来情報リークが発生する（実測: lift@5%=14.4 → 1.86 に低下確認済）。
  デフォルトでマスタ列を除外する。`--include-master` で含めることも可能（leak シミュレーション用）。

Usage:
    PYTHONUTF8=1 python scripts/tob_prediction/train_lgbm.py
    PYTHONUTF8=1 python scripts/tob_prediction/train_lgbm.py --n-trials 20
    PYTHONUTF8=1 python scripts/tob_prediction/train_lgbm.py --include-master  # leak シミュ
"""

from __future__ import annotations

import argparse
import pickle
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import optuna
import pandas as pd
import structlog
from sklearn.metrics import average_precision_score
from sklearn.model_selection import GroupKFold

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = PROJECT_ROOT / "data" / "cache" / "tob_ml"
MODEL_DIR = PROJECT_ROOT / "data" / "models"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
JST = timezone(timedelta(hours=+9), "JST")

DEFAULT_TRAIN_START = None        # None なら dataset の最早日から
DEFAULT_TRAIN_END = "2024-12-31"
DEFAULT_VAL_START = "2025-01-01"
DEFAULT_VAL_END = "2026-05-14"
DEFAULT_N_TRIALS = 20

CATEGORICAL_COLS = [
    "INDUSTRY_33_CODE",
    "INDUSTRY_17_CODE",
    "MARKET_CATEGORY",
    "SIZE_CODE",
]
DROP_COLS_BASE = ["TICKER", "DATE", "is_positive", "ADJ_CLOSE"]

log = structlog.get_logger()


def find_latest_dataset() -> Path:
    """data/cache/tob_ml/ 内の最新 (mtime) dataset_*.parquet を返す.

    ファイル名アルファベット順ではなく更新日時順。'since=2020' と 'since=2024' の
    両方が存在するとき、新しく生成したものを選ぶため。
    """
    candidates = list(DATASET_DIR.glob("dataset_*.parquet"))
    if not candidates:
        raise FileNotFoundError(f"No dataset found in {DATASET_DIR}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_dataset(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df["DATE"] = df["DATE"].astype(str)
    df["TICKER"] = df["TICKER"].astype(str)
    log.info("dataset_loaded", path=str(path), rows=len(df), cols=len(df.columns))
    return df


def prepare_features(
    df: pd.DataFrame,
    cat_categories: dict[str, pd.Index] | None = None,
    include_master: bool = False,
) -> tuple[pd.DataFrame, pd.Series, list[str], list[str]]:
    """特徴量とラベルを分離し category dtype を整える.

    Args:
        include_master: True のときマスタ列（CATEGORICAL_COLS）を特徴量に含める。
          デフォルト False（STOCK_CODE_LIST スナップショットによる leak 回避）。
    """
    drop_cols = list(DROP_COLS_BASE)
    if not include_master:
        drop_cols += CATEGORICAL_COLS
    feat_cols = [c for c in df.columns if c not in drop_cols]
    cat_cols = [c for c in CATEGORICAL_COLS if c in feat_cols]

    X = df[feat_cols].copy()
    for c in cat_cols:
        if cat_categories is not None and c in cat_categories:
            X[c] = pd.Categorical(X[c], categories=cat_categories[c])
        else:
            X[c] = X[c].astype("category")
    y = df["is_positive"].fillna(False).astype(int)
    return X, y, feat_cols, cat_cols


def compute_lift_at_k(y_true: np.ndarray, y_score: np.ndarray, k_pct: float) -> float:
    """lift@K = (上位 K% の positive 率) / (全体 positive 率)."""
    n = len(y_true)
    k = max(1, int(n * k_pct / 100.0))
    order = np.argsort(-y_score)
    top_k_y = y_true[order[:k]]
    pos_rate_all = y_true.mean()
    if pos_rate_all == 0:
        return 0.0
    return float(top_k_y.mean() / pos_rate_all)


def objective(
    trial: optuna.Trial,
    X_train: pd.DataFrame,
    y_train: pd.Series,
    cat_cols: list[str],
    groups: np.ndarray,
) -> float:
    """Optuna objective: GroupKFold 5-fold mean PR-AUC."""
    params = {
        "objective": "binary",
        "metric": "average_precision",
        "verbose": -1,
        "boosting_type": "gbdt",
        "num_leaves": trial.suggest_int("num_leaves", 8, 64),
        "max_depth": trial.suggest_int("max_depth", 3, 8),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.1, log=True),
        "min_child_samples": trial.suggest_int("min_child_samples", 5, 100),
        "reg_alpha": trial.suggest_float("reg_alpha", 1e-3, 10.0, log=True),
        "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 10.0, log=True),
        "feature_fraction": trial.suggest_float("feature_fraction", 0.5, 1.0),
        "bagging_fraction": trial.suggest_float("bagging_fraction", 0.5, 1.0),
        "bagging_freq": 5,
        "scale_pos_weight": trial.suggest_float("scale_pos_weight", 1.0, 200.0, log=True),
        "n_estimators": 1000,
        "random_state": 42,
        "n_jobs": -1,
    }
    gkf = GroupKFold(n_splits=5)
    scores: list[float] = []
    fit_kwargs = {
        "categorical_feature": cat_cols if cat_cols else "auto",
        "callbacks": [lgb.early_stopping(50, verbose=False), lgb.log_evaluation(0)],
    }
    for fold, (tr_idx, val_idx) in enumerate(gkf.split(X_train, y_train, groups=groups)):
        X_tr, X_val = X_train.iloc[tr_idx], X_train.iloc[val_idx]
        y_tr, y_val = y_train.iloc[tr_idx], y_train.iloc[val_idx]
        model = lgb.LGBMClassifier(**params)
        model.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], **fit_kwargs)
        proba = model.predict_proba(X_val)[:, 1]
        scores.append(average_precision_score(y_val, proba))
    return float(np.mean(scores))


def train_final(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    cat_cols: list[str],
    best_params: dict,
) -> lgb.LGBMClassifier:
    """ベストパラメータで最終モデルを学習."""
    params = dict(best_params)
    params.update({
        "objective": "binary",
        "metric": "average_precision",
        "verbose": -1,
        "bagging_freq": 5,
        "n_estimators": 2000,
        "random_state": 42,
        "n_jobs": -1,
    })
    model = lgb.LGBMClassifier(**params)
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        categorical_feature=cat_cols if cat_cols else "auto",
        callbacks=[lgb.early_stopping(100, verbose=True), lgb.log_evaluation(100)],
    )
    return model


def evaluate(model: lgb.LGBMClassifier, X_val: pd.DataFrame, y_val: pd.Series) -> dict:
    """検証データで評価."""
    proba = model.predict_proba(X_val)[:, 1]
    y_arr = y_val.to_numpy()
    return {
        "pr_auc": float(average_precision_score(y_arr, proba)),
        "lift_1pct": compute_lift_at_k(y_arr, proba, 1.0),
        "lift_5pct": compute_lift_at_k(y_arr, proba, 5.0),
        "lift_10pct": compute_lift_at_k(y_arr, proba, 10.0),
        "n_val": int(len(y_val)),
        "n_positive": int(y_arr.sum()),
        "positive_rate": float(y_arr.mean()),
    }


def save_shap(
    model: lgb.LGBMClassifier,
    X_val: pd.DataFrame,
    feat_cols: list[str],
    out_path: Path,
) -> None:
    """SHAP feature importance を可視化して保存."""
    import shap
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n_sample = min(10000, len(X_val))
    X_sample = X_val.sample(n=n_sample, random_state=42)

    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X_sample)
    if isinstance(shap_values, list):
        shap_values = shap_values[1]

    shap.summary_plot(
        shap_values, X_sample,
        feature_names=feat_cols,
        show=False, max_display=20,
    )
    plt.tight_layout()
    plt.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close()
    log.info("shap_saved", path=str(out_path))


def main() -> None:
    parser = argparse.ArgumentParser(description="TOB予測 LightGBM 学習")
    parser.add_argument("--dataset", default=None, help="parquet path (省略時は最新)")
    parser.add_argument(
        "--train-start", default=DEFAULT_TRAIN_START,
        help="学習期間下限 YYYY-MM-DD（省略時は dataset の最早日。コロナ期除外などに使用）",
    )
    parser.add_argument("--train-end", default=DEFAULT_TRAIN_END)
    parser.add_argument("--val-start", default=DEFAULT_VAL_START)
    parser.add_argument("--val-end", default=DEFAULT_VAL_END)
    parser.add_argument("--n-trials", type=int, default=DEFAULT_N_TRIALS)
    parser.add_argument(
        "--include-master", action="store_true",
        help="マスタ列を特徴量に含める（leak シミュレーション用; デフォルト除外）",
    )
    parser.add_argument(
        "--tag", default=None,
        help="モデル名タグ（例: 'best', 'n20', 'smoke_train1y'）。指定時は tob_ml_lgbm_<tag>.pkl で保存。"
             "省略時は tob_ml_lgbm_<timestamp>.pkl",
    )
    args = parser.parse_args()

    ds_path = Path(args.dataset) if args.dataset else find_latest_dataset()
    df = load_dataset(ds_path)

    train_mask = df["DATE"] <= args.train_end
    if args.train_start:
        train_mask &= df["DATE"] >= args.train_start
    train_df = df[train_mask].reset_index(drop=True)
    val_df = df[
        (df["DATE"] >= args.val_start) & (df["DATE"] <= args.val_end)
    ].reset_index(drop=True)
    log.info(
        "split",
        train_start=args.train_start or "(dataset_min)",
        train_end=args.train_end,
        train_rows=len(train_df), train_pos=int(train_df["is_positive"].sum()),
        val_rows=len(val_df), val_pos=int(val_df["is_positive"].sum()),
    )

    if train_df["is_positive"].sum() < 10 or val_df["is_positive"].sum() < 10:
        log.error("insufficient_positive_samples")
        sys.exit(1)

    X_train, y_train, feat_cols, cat_cols = prepare_features(
        train_df, include_master=args.include_master,
    )
    cat_categories = {c: X_train[c].cat.categories for c in cat_cols}
    X_val, y_val, _, _ = prepare_features(
        val_df, cat_categories=cat_categories, include_master=args.include_master,
    )
    log.info(
        "features_prepared",
        n_features=len(feat_cols), cat_cols=cat_cols,
        include_master=args.include_master,
    )

    groups = train_df["TICKER"].to_numpy()

    log.info("optuna_start", n_trials=args.n_trials)
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42),
    )
    study.optimize(
        lambda t: objective(t, X_train, y_train, cat_cols, groups),
        n_trials=args.n_trials,
        show_progress_bar=False,
    )
    log.info("optuna_best", value=study.best_value, params=study.best_params)

    model = train_final(X_train, y_train, X_val, y_val, cat_cols, study.best_params)
    metrics = evaluate(model, X_val, y_val)
    log.info("val_metrics", **metrics)

    print(f"\n=== OOS Validation ({args.val_start} 〜 {args.val_end}) ===")
    print(f"PR-AUC      : {metrics['pr_auc']:.4f}")
    print(f"lift@1%     : {metrics['lift_1pct']:.3f}")
    print(f"lift@5%     : {metrics['lift_5pct']:.3f}  (インサイダー検知 baseline = 1.12)")
    print(f"lift@10%    : {metrics['lift_10pct']:.3f}")
    print(f"n_val       : {metrics['n_val']:,}")
    print(f"n_positive  : {metrics['n_positive']:,}")
    print(f"pos_rate    : {metrics['positive_rate']:.4%}")
    if metrics["lift_5pct"] > 2.0:
        print("\n✓ 完全採用ゾーン (lift > 2.0)")
    elif metrics["lift_5pct"] >= 1.3:
        print("\n△ 部分採用ゾーン (1.3 <= lift < 2.0) — インサイダー検知モデルと並走運用検討")
    else:
        print("\n✗ 撤退ゾーン (lift < 1.3) — 数式モデル限界、別アプローチへ")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=JST).strftime("%Y%m%d_%H%M%S")
    name_suffix = args.tag if args.tag else ts
    model_path = MODEL_DIR / f"tob_ml_lgbm_{name_suffix}.pkl"
    with model_path.open("wb") as f:
        pickle.dump({
            "model": model,
            "backend": "lightgbm",
            "feat_cols": feat_cols,
            "cat_cols": cat_cols,
            "cat_categories": cat_categories,
            "best_params": study.best_params,
            "metrics": metrics,
            "train_start": args.train_start,
            "train_end": args.train_end,
            "val_start": args.val_start,
            "val_end": args.val_end,
            "dataset_path": str(ds_path),
            "include_master": args.include_master,
            "trained_at_jst": ts,
        }, f)
    log.info("model_saved", path=str(model_path))

    shap_path = OUTPUT_DIR / f"tob_ml_shap_{name_suffix}.png"
    try:
        save_shap(model, X_val, feat_cols, shap_path)
    except Exception as e:  # noqa: BLE001
        log.warning("shap_failed", error=str(e))


if __name__ == "__main__":
    main()

"""TOB予測 Random Forest モデル学習・評価.

論文「機械学習による他社株TOBの予測可能性」の再現実装。
Walk-forward 評価で ROC-AUC / PR-AUC を算出。

Usage:
    PYTHONUTF8=1 python scripts/tob_prediction/train_rf.py
    PYTHONUTF8=1 python scripts/tob_prediction/train_rf.py --refresh
    PYTHONUTF8=1 python scripts/tob_prediction/train_rf.py --eval-year 2024
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import optuna
import pandas as pd
import structlog
from google.cloud import bigquery
from google.oauth2 import service_account
from imblearn.over_sampling import SMOTENC
from imblearn.under_sampling import RandomUnderSampler, TomekLinks
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.preprocessing import StandardScaler

logger = structlog.get_logger()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CREDENTIALS_PATH = PROJECT_ROOT / "keys" / "gcp-service-account.json"
BQ_PROJECT = "gmailpj-357912"
CACHE_DIR = Path("C:/tmp/tob_prediction")

EVAL_YEARS = [2022, 2023, 2024, 2025]
TRAIN_WINDOW = 5
OPTUNA_TRIALS = 30
OPTUNA_CV_FOLDS = 3
RUS_RATIO = 0.05  # minority/majority = 1:20
SMOTE_RATIO = 0.1  # minority/majority = 1:10

CONTINUOUS_FEATURES = [
    "equity_ratio",
    "pbr",
    "roe",
    "payout_ratio",
    "ln_market_cap",
    "cash_rich_ratio",
    "forecast_div_yield",
    "forecast_profit_growth",
    "cfo_to_mcap",
    "operating_margin",
    "ret_60d",
    "ret_240d",
    "vol_240d",
    "turnover_ratio",
    "top_shareholder_ratio",
    "individual_ratio",
    "foreign_ratio",
    "financial_inst_ratio",
    "other_corp_ratio",
    "top10_concentration",
]
BINARY_FEATURES = ["has_activist", "top_shareholder_is_public"]


# ---------------------------------------------------------------------------
# BQ data loading
# ---------------------------------------------------------------------------


def _bq_client() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(
        str(CREDENTIALS_PATH),
        scopes=["https://www.googleapis.com/auth/bigquery"],
    )
    return bigquery.Client(project=BQ_PROJECT, credentials=creds)


def _cached(name: str, query_fn, client: bigquery.Client, *, refresh: bool) -> pd.DataFrame:
    path = CACHE_DIR / f"{name}.csv"
    if not refresh and path.exists():
        logger.info("cache_hit", name=name)
        return pd.read_csv(path, encoding="utf-8", parse_dates=True)
    logger.info("querying_bq", name=name)
    df = query_fn(client)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8")
    return df


def _load_labels(client: bigquery.Client) -> pd.DataFrame:
    sql = """
    SELECT TICKER,
      EXTRACT(YEAR FROM TOB_ANNOUNCEMENT_DATE) AS tob_year
    FROM `gmailpj-357912.STOCK.DELISTED_STOCKS`
    WHERE IS_PAPER_TOB_LABEL = TRUE
    """
    return client.query(sql).to_dataframe()


def _load_financials(client: bigquery.Client) -> pd.DataFrame:
    sql = """
    SELECT
      LOCAL_CODE AS TICKER,
      DISCLOSED_DATE,
      DISCLOSURE_NUMBER,
      NET_SALES, OPERATING_PROFIT, PROFIT,
      BOOK_VALUE_PER_SHARE,
      TOTAL_ASSETS, EQUITY, EQUITY_TO_ASSET_RATIO,
      CASH_AND_EQUIVALENTS,
      CASH_FLOWS_FROM_OPERATING_ACTIVITIES AS CFO,
      RESULT_PAYOUT_RATIO_ANNUAL,
      NEXT_YEAR_FORECAST_PROFIT,
      NEXT_YEAR_FORECAST_DIVIDEND_PER_SHARE_ANNUAL
    FROM `gmailpj-357912.STOCK.fin_summary`
    WHERE TYPE_OF_DOCUMENT LIKE 'FYFinancialStatements_%'
      AND DISCLOSED_DATE >= '2014-01-01'
    """
    return client.query(sql).to_dataframe()


def _load_shareholders(client: bigquery.Client) -> pd.DataFrame:
    sql = """
    SELECT
      TICKER, FISCAL_YEAR_END,
      TOP_SHAREHOLDER_RATIO, TOP_SHAREHOLDER_IS_PUBLIC,
      FOREIGN_RATIO, INDIVIDUAL_RATIO, FINANCIAL_INST_RATIO,
      OTHER_CORP_RATIO, TOP10_CONCENTRATION, HAS_ACTIVIST
    FROM `gmailpj-357912.STOCK.SHAREHOLDER_COMPOSITION`
    """
    return client.query(sql).to_dataframe()


def _load_price_features(client: bigquery.Client) -> pd.DataFrame:
    """各年5月末基準の株価特徴量をBQ window関数で計算."""
    sql = """
    WITH daily AS (
      SELECT
        TICKER, DATE, ADJ_CLOSE, ADJ_VOLUME, TURNOVER,
        SAFE_DIVIDE(
          ADJ_CLOSE - LAG(ADJ_CLOSE) OVER (PARTITION BY TICKER ORDER BY DATE),
          LAG(ADJ_CLOSE) OVER (PARTITION BY TICKER ORDER BY DATE)
        ) AS daily_ret
      FROM `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS`
      WHERE DATE >= '2015-06-01' AND DATE <= '2025-05-31'
        AND IS_PREFERRED = FALSE
        AND ADJ_CLOSE IS NOT NULL AND ADJ_CLOSE > 0
    ),
    assigned AS (
      SELECT *,
        CASE WHEN EXTRACT(MONTH FROM DATE) >= 6
             THEN EXTRACT(YEAR FROM DATE) + 1
             ELSE EXTRACT(YEAR FROM DATE)
        END AS ref_year
      FROM daily
    ),
    ranked AS (
      SELECT *,
        ROW_NUMBER() OVER (
          PARTITION BY TICKER, ref_year ORDER BY DATE DESC
        ) AS rk
      FROM assigned
      WHERE ref_year BETWEEN 2016 AND 2025
    )
    SELECT
      TICKER,
      ref_year,
      MAX(CASE WHEN rk = 1 THEN ADJ_CLOSE END) AS close_ref,
      SAFE_DIVIDE(
        MAX(CASE WHEN rk = 1 THEN ADJ_CLOSE END)
          - MAX(CASE WHEN rk = 60 THEN ADJ_CLOSE END),
        NULLIF(MAX(CASE WHEN rk = 60 THEN ADJ_CLOSE END), 0)
      ) AS ret_60d,
      SAFE_DIVIDE(
        MAX(CASE WHEN rk = 1 THEN ADJ_CLOSE END)
          - MAX(CASE WHEN rk = 240 THEN ADJ_CLOSE END),
        NULLIF(MAX(CASE WHEN rk = 240 THEN ADJ_CLOSE END), 0)
      ) AS ret_240d,
      STDDEV(CASE WHEN rk BETWEEN 1 AND 240 THEN daily_ret END) AS vol_240d,
      AVG(CASE WHEN rk <= 60 THEN TURNOVER END) AS avg_turnover_60d,
      AVG(CASE WHEN rk <= 60 THEN ADJ_VOLUME END) AS avg_volume_60d
    FROM ranked
    WHERE rk <= 240
    GROUP BY TICKER, ref_year
    """
    return client.query(sql).to_dataframe()


def _load_industries(client: bigquery.Client) -> pd.DataFrame:
    sql = """
    SELECT TICKER, INDUSTRY_17_CODE
    FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
    WHERE EXCHANGE = 'TSE' AND INDUSTRY_17_CODE IS NOT NULL
    """
    return client.query(sql).to_dataframe()


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------


def build_feature_matrix(
    financials: pd.DataFrame,
    shareholders: pd.DataFrame,
    prices: pd.DataFrame,
    industries: pd.DataFrame,
    labels: pd.DataFrame,
) -> pd.DataFrame:
    """全年度の特徴量マトリクスを構築."""
    for df in [financials, shareholders, prices, industries, labels]:
        df["TICKER"] = df["TICKER"].astype(str)

    label_set = set(zip(labels["TICKER"], labels["tob_year"].astype(int)))

    for col in ["DISCLOSED_DATE", "FISCAL_YEAR_END"]:
        if col in financials.columns:
            financials[col] = pd.to_datetime(financials[col])
        if col in shareholders.columns:
            shareholders[col] = pd.to_datetime(shareholders[col])

    min_year = min(EVAL_YEARS) - TRAIN_WINDOW
    max_year = max(EVAL_YEARS)
    all_years: list[pd.DataFrame] = []

    for year in range(min_year, max_year + 1):
        cutoff = pd.Timestamp(year, 5, 31)

        # --- Price features ---
        pf = prices[prices["ref_year"] == year].copy()
        if pf.empty:
            continue

        # --- Financials: latest FY disclosed before cutoff ---
        fin_m = financials[financials["DISCLOSED_DATE"] <= cutoff].copy()
        if fin_m.empty:
            continue
        fin_m = (
            fin_m.sort_values(["DISCLOSED_DATE", "DISCLOSURE_NUMBER"])
            .groupby("TICKER")
            .last()
            .reset_index()
        )

        # --- Shareholders: latest FISCAL_YEAR_END before cutoff ---
        sh_m = shareholders[shareholders["FISCAL_YEAR_END"] <= cutoff].copy()
        if sh_m.empty:
            continue
        sh_m = (
            sh_m.sort_values("FISCAL_YEAR_END")
            .groupby("TICKER")
            .last()
            .reset_index()
        )

        # --- Merge (industry は LEFT JOIN: 廃止済み銘柄は STOCK_CODE_LIST に無い) ---
        df = pf.merge(fin_m, on="TICKER", how="inner")
        df = df.merge(sh_m, on="TICKER", how="inner", suffixes=("", "_sh"))
        df = df.merge(industries, on="TICKER", how="left")

        if df.empty:
            continue

        # --- Derived features ---
        bps = df["BOOK_VALUE_PER_SHARE"]
        equity = df["EQUITY"]
        valid_bps = (bps > 0) & bps.notna() & (equity > 0) & equity.notna()

        shares_est = pd.Series(np.nan, index=df.index)
        shares_est[valid_bps] = equity[valid_bps] / bps[valid_bps]

        mcap = shares_est * df["close_ref"]

        df["equity_ratio"] = df["EQUITY_TO_ASSET_RATIO"]
        df["pbr"] = np.where(valid_bps, df["close_ref"] / bps, np.nan)
        df["roe"] = np.where(valid_bps, df["PROFIT"] / equity, np.nan)
        df["payout_ratio"] = df["RESULT_PAYOUT_RATIO_ANNUAL"]
        df["ln_market_cap"] = np.where(mcap > 0, np.log(mcap), np.nan)
        df["cash_rich_ratio"] = np.where(
            (df["TOTAL_ASSETS"] > 0) & df["TOTAL_ASSETS"].notna(),
            df["CASH_AND_EQUIVALENTS"] / df["TOTAL_ASSETS"],
            np.nan,
        )
        df["forecast_div_yield"] = np.where(
            df["close_ref"] > 0,
            df["NEXT_YEAR_FORECAST_DIVIDEND_PER_SHARE_ANNUAL"] / df["close_ref"],
            np.nan,
        )
        profit_abs = df["PROFIT"].abs()
        df["forecast_profit_growth"] = np.where(
            (profit_abs > 0) & profit_abs.notna() & df["NEXT_YEAR_FORECAST_PROFIT"].notna(),
            df["NEXT_YEAR_FORECAST_PROFIT"] / profit_abs - 1,
            np.nan,
        )
        df["cfo_to_mcap"] = np.where(mcap > 0, df["CFO"] / mcap, np.nan)
        df["operating_margin"] = np.where(
            (df["NET_SALES"] > 0) & df["NET_SALES"].notna(),
            df["OPERATING_PROFIT"] / df["NET_SALES"],
            np.nan,
        )
        df["turnover_ratio"] = np.where(
            shares_est > 0,
            df["avg_volume_60d"] / shares_est,
            np.nan,
        )

        # --- Shareholder features ---
        df["top_shareholder_ratio"] = df["TOP_SHAREHOLDER_RATIO"]
        df["individual_ratio"] = df["INDIVIDUAL_RATIO"]
        df["foreign_ratio"] = df["FOREIGN_RATIO"]
        df["financial_inst_ratio"] = df["FINANCIAL_INST_RATIO"]
        df["other_corp_ratio"] = df["OTHER_CORP_RATIO"]
        df["top10_concentration"] = df["TOP10_CONCENTRATION"]
        df["has_activist"] = df["HAS_ACTIVIST"].astype("boolean").fillna(False).astype(int)
        df["top_shareholder_is_public"] = df["TOP_SHAREHOLDER_IS_PUBLIC"].astype("boolean").fillna(False).astype(int)

        # --- Label ---
        df["label"] = [1 if (t, year) in label_set else 0 for t in df["TICKER"]]
        df["year"] = year
        n_pos = int(df["label"].sum())
        if n_pos > 0 or year in [y for _, y in label_set]:
            logger.info("year_labels", year=year, n_tickers=len(df), n_pos=n_pos)

        keep_cols = ["TICKER", "year", "label"] + CONTINUOUS_FEATURES + BINARY_FEATURES + ["INDUSTRY_17_CODE"]
        all_years.append(df[keep_cols])

    result = pd.concat(all_years, ignore_index=True)

    # One-hot encode industry
    dummies = pd.get_dummies(result["INDUSTRY_17_CODE"], prefix="ind17")
    result = pd.concat([result.drop(columns=["INDUSTRY_17_CODE"]), dummies], axis=1)

    logger.info(
        "feature_matrix",
        rows=len(result),
        positives=int(result["label"].sum()),
        features=len(CONTINUOUS_FEATURES) + len(BINARY_FEATURES) + len(dummies.columns),
    )
    return result


# ---------------------------------------------------------------------------
# Preprocessing (Steps 4-6)
# ---------------------------------------------------------------------------


def resample(
    X: np.ndarray, y: np.ndarray, cat_indices: list[int]
) -> tuple[np.ndarray, np.ndarray]:
    """RUS(1:20) → TomekLinks → SMOTENC(1:10)."""
    n_pos = int(y.sum())

    # Step 4
    rus = RandomUnderSampler(sampling_strategy=RUS_RATIO, random_state=42)
    X_r, y_r = rus.fit_resample(X, y)

    # Step 5
    tl = TomekLinks()
    X_r, y_r = tl.fit_resample(X_r, y_r)

    # Step 6
    n_pos_now = int(y_r.sum())
    k = min(5, n_pos_now - 1)
    if k < 1:
        logger.warning("smotenc_skipped", n_pos=n_pos_now)
        return X_r, y_r

    smote = SMOTENC(
        categorical_features=cat_indices,
        sampling_strategy=SMOTE_RATIO,
        random_state=42,
        k_neighbors=k,
    )
    X_r, y_r = smote.fit_resample(X_r, y_r)
    return X_r, y_r


# ---------------------------------------------------------------------------
# Optuna hyperparameter tuning
# ---------------------------------------------------------------------------


def tune_hyperparams(
    X_train: np.ndarray,
    y_train: np.ndarray,
    cat_indices: list[int],
    *,
    n_cont: int,
    n_trials: int = OPTUNA_TRIALS,
) -> dict:
    """PR-AUC を最大化する RF ハイパーパラメータを探索."""
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial: optuna.Trial) -> float:
        params = {
            "n_estimators": trial.suggest_int("n_estimators", 100, 500, step=50),
            "max_depth": trial.suggest_int("max_depth", 3, 20),
            "min_samples_leaf": trial.suggest_int("min_samples_leaf", 1, 50),
            "max_features": trial.suggest_categorical(
                "max_features", ["sqrt", "log2"]
            ),
        }
        skf = StratifiedKFold(
            n_splits=OPTUNA_CV_FOLDS, shuffle=True, random_state=42
        )
        scores: list[float] = []
        for tr_idx, va_idx in skf.split(X_train, y_train):
            Xtr, Xva = X_train[tr_idx].copy(), X_train[va_idx].copy()
            ytr, yva = y_train[tr_idx], y_train[va_idx]
            fold_scaler = StandardScaler()
            Xtr[:, :n_cont] = fold_scaler.fit_transform(Xtr[:, :n_cont])
            Xva[:, :n_cont] = fold_scaler.transform(Xva[:, :n_cont])
            Xtr_r, ytr_r = resample(Xtr, ytr, cat_indices)
            clf = RandomForestClassifier(**params, random_state=42, n_jobs=-1)
            clf.fit(Xtr_r, ytr_r)
            prob = clf.predict_proba(Xva)[:, 1]
            if yva.sum() == 0:
                continue
            scores.append(average_precision_score(yva, prob))
        return float(np.mean(scores)) if scores else 0.0

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials)
    logger.info("optuna_done", best_pr_auc=f"{study.best_value:.4f}")
    return study.best_params


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="BQキャッシュ無視")
    parser.add_argument("--eval-year", type=int, help="単年のみ評価")
    parser.add_argument("--trials", type=int, default=OPTUNA_TRIALS)
    args = parser.parse_args()

    eval_years = [args.eval_year] if args.eval_year else EVAL_YEARS

    client = _bq_client()
    labels = _cached("labels", _load_labels, client, refresh=args.refresh)
    financials = _cached("financials", _load_financials, client, refresh=args.refresh)
    shareholders = _cached("shareholders", _load_shareholders, client, refresh=args.refresh)
    prices = _cached("prices", _load_price_features, client, refresh=args.refresh)
    industries = _cached("industries", _load_industries, client, refresh=args.refresh)

    logger.info(
        "data_loaded",
        labels=len(labels),
        financials=len(financials),
        shareholders=len(shareholders),
        prices=len(prices),
        industries=len(industries),
    )

    features = build_feature_matrix(financials, shareholders, prices, industries, labels)

    feature_cols = CONTINUOUS_FEATURES + BINARY_FEATURES + [
        c for c in features.columns if c.startswith("ind17_")
    ]
    n_cont = len(CONTINUOUS_FEATURES)
    cat_indices = list(range(n_cont, len(feature_cols)))

    results: list[dict] = []
    for eval_year in eval_years:
        logger.info("eval_start", year=eval_year)

        train_years = list(range(eval_year - TRAIN_WINDOW, eval_year))
        df_train = features[features["year"].isin(train_years)].dropna(subset=feature_cols)
        df_test = features[features["year"] == eval_year].dropna(subset=feature_cols)

        X_train = df_train[feature_cols].values.astype(np.float64)
        y_train = df_train["label"].values.astype(int)
        X_test = df_test[feature_cols].values.astype(np.float64)
        y_test = df_test["label"].values.astype(int)

        n_pos_tr = int(y_train.sum())
        n_pos_te = int(y_test.sum())
        logger.info(
            "split",
            train=len(X_train),
            train_pos=n_pos_tr,
            test=len(X_test),
            test_pos=n_pos_te,
        )

        if n_pos_tr < 5 or n_pos_te < 2:
            logger.warning("skip_insufficient", year=eval_year)
            continue

        # Optuna (fold内でスケーリング)
        best_params = tune_hyperparams(
            X_train, y_train, cat_indices, n_cont=n_cont, n_trials=args.trials
        )
        logger.info("best_params", **best_params)

        # Final model: scale → resample → fit
        scaler = StandardScaler()
        X_train[:, :n_cont] = scaler.fit_transform(X_train[:, :n_cont])
        X_test[:, :n_cont] = scaler.transform(X_test[:, :n_cont])

        X_train_r, y_train_r = resample(X_train, y_train, cat_indices)
        clf = RandomForestClassifier(**best_params, random_state=42, n_jobs=-1)
        clf.fit(X_train_r, y_train_r)

        y_prob = clf.predict_proba(X_test)[:, 1]
        roc = roc_auc_score(y_test, y_prob)
        pr = average_precision_score(y_test, y_prob)

        logger.info("result", year=eval_year, roc_auc=f"{roc:.4f}", pr_auc=f"{pr:.4f}")

        # Feature importance (top 10)
        imp = pd.Series(clf.feature_importances_, index=feature_cols).sort_values(
            ascending=False
        )
        for feat, val in imp.head(10).items():
            logger.info("importance", feature=feat, value=f"{val:.4f}")

        # Prediction ranking for test year
        ranking = (
            df_test[["TICKER"]].copy().assign(prob=y_prob, label=y_test)
            .sort_values("prob", ascending=False)
        )
        top5pct = int(max(1, len(ranking) * 0.05))
        top_tickers = ranking.head(top5pct)
        hit_rate = top_tickers["label"].mean()
        logger.info(
            "top5pct",
            year=eval_year,
            n=top5pct,
            hits=int(top_tickers["label"].sum()),
            hit_rate=f"{hit_rate:.3f}",
        )

        results.append({
            "year": eval_year,
            "roc_auc": round(roc, 4),
            "pr_auc": round(pr, 4),
            "train_n": len(X_train),
            "train_pos": n_pos_tr,
            "test_n": len(X_test),
            "test_pos": n_pos_te,
            **best_params,
        })

    if not results:
        logger.error("no_results")
        sys.exit(1)

    rdf = pd.DataFrame(results)
    print("\n=== TOB予測 RF Walk-Forward 評価結果 ===")
    print(rdf.to_string(index=False))
    print(f"\nROC-AUC 平均: {rdf['roc_auc'].mean():.4f}")
    print(f"PR-AUC  平均: {rdf['pr_auc'].mean():.4f}")

    out_path = CACHE_DIR / "tob_rf_results.csv"
    rdf.to_csv(out_path, index=False, encoding="utf-8")
    logger.info("saved", path=str(out_path))


if __name__ == "__main__":
    main()

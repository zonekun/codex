"""TOBスクリーニングツール — 予測確率上位銘柄を根拠付きで一覧表示.

Usage:
    PYTHONUTF8=1 python scripts/tob_prediction/screen_tob.py
    PYTHONUTF8=1 python scripts/tob_prediction/screen_tob.py --top-n 50
    PYTHONUTF8=1 python scripts/tob_prediction/screen_tob.py --min-cap 100 --max-cap 3000 --has-activist
    PYTHONUTF8=1 python scripts/tob_prediction/screen_tob.py --multi-year --format csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import structlog
from google.cloud import bigquery
from google.oauth2 import service_account

logger = structlog.get_logger()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CREDENTIALS_PATH = PROJECT_ROOT / "keys" / "gcp-service-account.json"
BQ_PROJECT = "gmailpj-357912"
CACHE_DIR = Path("C:/tmp/tob_prediction")
OUTPUT_DIR = PROJECT_ROOT / "data/output/tob_prediction"

FEATURE_LABELS = {
    "top_shareholder_ratio": ("筆頭株主比率", "↑高いほどTOB候���"),
    "top_shareholder_is_public": ("筆頭が上場企業", "親子上場解消"),
    "individual_ratio": ("個人保有比率", "↓低いほどTOB候補"),
    "ln_market_cap": ("log時価総額", "↓小さいほどTOB候補"),
    "pbr": ("PBR", "↓低いほどTOB候補"),
    "ret_240d": ("240日リターン", "↓低いほどTOB候補"),
    "payout_ratio": ("配当性向", "↓低いほどTOB候補"),
    "foreign_ratio": ("外国人比率", ""),
    "other_corp_ratio": ("事業法人比率", ""),
    "financial_inst_ratio": ("金融機関比率", ""),
    "top10_concentration": ("上��10株主集中度", ""),
    "has_activist": ("アクティビスト", ""),
    "equity_ratio": ("自己資本比率", ""),
    "roe": ("ROE", ""),
    "cash_rich_ratio": ("キャッシュリッチ比", ""),
    "forecast_div_yield": ("予���配当利回り", ""),
    "forecast_profit_growth": ("予想利益成長率", ""),
    "cfo_to_mcap": ("CFO/時価総額", ""),
    "operating_margin": ("営業利益率", ""),
    "ret_60d": ("60日リターン", ""),
    "vol_240d": ("240日ボラ", ""),
    "turnover_ratio": ("出来高回転率", ""),
}


def _bq_client() -> bigquery.Client:
    """BigQuery クライアントを生成."""
    if not CREDENTIALS_PATH.exists():
        logger.error("credentials_not_found", path=str(CREDENTIALS_PATH))
        sys.exit(1)
    creds = service_account.Credentials.from_service_account_file(
        str(CREDENTIALS_PATH),
        scopes=["https://www.googleapis.com/auth/bigquery"],
    )
    return bigquery.Client(project=BQ_PROJECT, credentials=creds)


def detect_latest_year() -> int:
    """predictions_YYYY.csv の最新年を検出."""
    years = []
    for p in OUTPUT_DIR.glob("predictions_*.csv"):
        try:
            y = int(p.stem.split("_")[1])
            years.append(y)
        except (IndexError, ValueError):
            pass
    if not years:
        logger.error("no_predictions_found", output_dir=str(OUTPUT_DIR))
        sys.exit(1)
    return max(years)


def load_predictions(year: int) -> pd.DataFrame:
    """指定年の予測結果CSVを読み込み."""
    p = OUTPUT_DIR / f"predictions_{year}.csv"
    if not p.exists():
        logger.error("predictions_not_found", path=str(p))
        sys.exit(1)
    df = pd.read_csv(p, encoding="utf-8", dtype={"TICKER": str})
    df["year"] = year
    return df


def load_features(year: int) -> pd.DataFrame:
    """特徴量マトリクスをキャッシュから読み込み（根拠表示用）."""
    from scripts.tob_prediction.train_rf import (
        BINARY_FEATURES,
        CONTINUOUS_FEATURES,
        _bq_client as rf_bq_client,
        _cached,
        _load_financials,
        _load_price_features,
        _load_shareholders,
        build_feature_matrix,
        _load_labels,
    )

    client = rf_bq_client()
    labels = _cached("labels", _load_labels, client, refresh=False)
    financials = _cached("financials", _load_financials, client, refresh=False)
    shareholders = _cached("shareholders", _load_shareholders, client, refresh=False)
    prices = _cached("prices", _load_price_features, client, refresh=False)

    features = build_feature_matrix(financials, shareholders, prices, labels)
    year_features = features[features["year"] == year].copy()
    return year_features


def load_company_names(client: bigquery.Client) -> pd.DataFrame:
    """STOCK_CODE_LISTから銘柄名・市場区分を取得（キャッシュ付き）."""
    cache = CACHE_DIR / "company_names.csv"
    if cache.exists():
        return pd.read_csv(cache, encoding="utf-8", dtype={"TICKER": str})
    sql = """
    SELECT TICKER, STOCK_NAME, MARKET_CATEGORY, INDUSTRY_17_CODE
    FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
    WHERE EXCHANGE = 'TSE'
    """
    df = client.query(sql).to_dataframe()
    df["TICKER"] = df["TICKER"].astype(str)
    df.to_csv(cache, index=False, encoding="utf-8")
    return df


def load_delisted_tickers(client: bigquery.Client) -> set[str]:
    """上場廃止銘柄のTICKER集合を取得（キャッシュ付き）."""
    cache = CACHE_DIR / "delisted_tickers.csv"
    if cache.exists():
        df = pd.read_csv(cache, encoding="utf-8", dtype={"TICKER": str})
        return set(df["TICKER"])
    sql = """
    SELECT DISTINCT TICKER
    FROM `gmailpj-357912.STOCK.DELISTED_STOCKS`
    """
    df = client.query(sql).to_dataframe()
    df["TICKER"] = df["TICKER"].astype(str)
    df.to_csv(cache, index=False, encoding="utf-8")
    return set(df["TICKER"])


def load_market_cap(client: bigquery.Client) -> pd.DataFrame:
    """最新の時価総額（YF_STOCK_INFO）."""
    cache = CACHE_DIR / "latest_market_cap.csv"
    if cache.exists():
        return pd.read_csv(cache, encoding="utf-8", dtype={"TICKER": str})
    sql = """
    SELECT TICKER, MARKET_CAP
    FROM `gmailpj-357912.STOCK.YF_STOCK_INFO`
    QUALIFY ROW_NUMBER() OVER (PARTITION BY TICKER ORDER BY LOADED_AT DESC) = 1
    """
    df = client.query(sql).to_dataframe()
    df["TICKER"] = df["TICKER"].astype(str)
    df["market_cap_oku"] = df["MARKET_CAP"] / 1e8
    df.to_csv(cache, index=False, encoding="utf-8")
    return df


def top_reasons(row: pd.Series, feature_cols: list[str], n: int = 3) -> str:
    """特徴量の値ベースでTOB候補の根拠を生成."""
    reasons = []
    ranked_features = [
        "top_shareholder_ratio",
        "top_shareholder_is_public",
        "pbr",
        "ln_market_cap",
        "individual_ratio",
        "ret_240d",
        "payout_ratio",
        "has_activist",
        "foreign_ratio",
        "cash_rich_ratio",
    ]
    for feat in ranked_features:
        if feat not in feature_cols or pd.isna(row.get(feat)):
            continue
        val = row[feat]
        label, hint = FEATURE_LABELS.get(feat, (feat, ""))
        if feat == "top_shareholder_ratio" and val > 0.3:
            reasons.append(f"{label}{val:.0%}")
        elif feat == "top_shareholder_is_public" and val == 1:
            reasons.append("親子上場")
        elif feat == "pbr" and val < 1.0:
            reasons.append(f"PBR{val:.2f}")
        elif feat == "ln_market_cap":
            mcap_oku = np.exp(val) / 1e8
            if mcap_oku < 1000:
                reasons.append(f"時価{mcap_oku:.0f}億")
        elif feat == "has_activist" and val == 1:
            reasons.append("アクティビスト")
        elif feat == "individual_ratio" and val < 0.2:
            reasons.append(f"個人{val:.0%}")
        elif feat == "payout_ratio" and val < 0.2:
            reasons.append(f"配当性向{val:.0%}")
        if len(reasons) >= n:
            break
    return " / ".join(reasons) if reasons else "-"


def main() -> None:
    parser = argparse.ArgumentParser(description="TOBスクリーニングツール")
    parser.add_argument("--top-n", type=int, default=30, help="上位N社表示（デフォルト30）")
    parser.add_argument("--top-pct", type=float, help="上位N%%表示（--top-nより優先）")
    parser.add_argument("--year", type=int, help="予測年（デフォルト: 最新）")
    parser.add_argument("--multi-year", action="store_true", help="直近2年の平均確率で順位付け")
    parser.add_argument("--min-prob", type=float, help="最低予測確率")
    parser.add_argument("--min-cap", type=float, help="最低時価総額（億円）")
    parser.add_argument("--max-cap", type=float, help="最大時価総額（億円）")
    parser.add_argument("--market", help="市場区分 (prime/standard/growth)")
    parser.add_argument("--industry", help="東証17業種コード")
    parser.add_argument("--has-activist", action="store_true", help="アクティビスト保有のみ")
    parser.add_argument("--min-top-ratio", type=float, help="筆頭株主比率N%%以上")
    parser.add_argument("--refresh", action="store_true", help="BQキャッシュ再取得")
    parser.add_argument("--format", choices=["table", "csv"], default="table", help="出力形式")
    args = parser.parse_args()

    if args.refresh:
        for f in CACHE_DIR.glob("company_names.csv"):
            f.unlink()
        for f in CACHE_DIR.glob("delisted_tickers.csv"):
            f.unlink()
        for f in CACHE_DIR.glob("latest_market_cap.csv"):
            f.unlink()

    latest_year = args.year or detect_latest_year()
    logger.info("screening", year=latest_year, multi_year=args.multi_year)

    # Load predictions
    if args.multi_year:
        prev_year = latest_year - 1
        p1 = OUTPUT_DIR / f"predictions_{prev_year}.csv"
        if not p1.exists():
            logger.warning("prev_year_missing", year=prev_year)
            preds = load_predictions(latest_year)
        else:
            df1 = load_predictions(prev_year)
            df2 = load_predictions(latest_year)
            merged = df1[["TICKER", "prob"]].merge(
                df2[["TICKER", "prob"]], on="TICKER", suffixes=("_prev", "_curr")
            )
            merged["prob"] = (merged["prob_prev"] + merged["prob_curr"]) / 2
            merged["year"] = latest_year
            preds = merged[["TICKER", "prob", "year"]]
            logger.info("multi_year_merged", n=len(preds), years=f"{prev_year},{latest_year}")
    else:
        preds = load_predictions(latest_year)

    # Load supplementary data
    client = _bq_client()
    names = load_company_names(client)
    delisted = load_delisted_tickers(client)
    mcap_df = load_market_cap(client)

    # Exclude delisted
    n_before = len(preds)
    preds = preds[~preds["TICKER"].isin(delisted)]
    logger.info("delisted_excluded", removed=n_before - len(preds))

    # Merge company info
    preds = preds.merge(names, on="TICKER", how="left")
    preds = preds.merge(mcap_df[["TICKER", "market_cap_oku"]], on="TICKER", how="left")

    # Load features for reasons
    features = load_features(latest_year)
    feature_cols = [c for c in features.columns if c not in ("TICKER", "year", "label")]
    preds = preds.merge(features[["TICKER"] + feature_cols], on="TICKER", how="left")

    # Apply filters
    if args.min_prob is not None:
        preds = preds[preds["prob"] >= args.min_prob]
    if args.min_cap is not None:
        preds = preds[preds["market_cap_oku"] >= args.min_cap]
    if args.max_cap is not None:
        preds = preds[preds["market_cap_oku"] <= args.max_cap]
    if args.market:
        market_map = {"prime": "プライム", "standard": "スタンダード", "growth": "グロース"}
        segment = market_map.get(args.market.lower(), args.market)
        preds = preds[preds["MARKET_CATEGORY"].str.contains(segment, na=False)]
    if args.industry:
        preds = preds[preds["INDUSTRY_17_CODE"] == args.industry]
    if args.has_activist:
        preds = preds[preds["has_activist"] == 1]
    if args.min_top_ratio is not None:
        preds = preds[preds["top_shareholder_ratio"] >= args.min_top_ratio / 100]

    # Sort and limit
    preds = preds.sort_values("prob", ascending=False)

    if args.top_pct is not None:
        n_show = max(1, int(len(preds) * args.top_pct / 100))
    else:
        n_show = args.top_n

    result = preds.head(n_show).copy()

    # Generate reasons
    result["reasons"] = result.apply(lambda r: top_reasons(r, feature_cols), axis=1)

    # Output
    display_cols = ["TICKER", "STOCK_NAME", "prob", "market_cap_oku", "MARKET_CATEGORY", "reasons"]
    display_names = {
        "TICKER": "コード",
        "STOCK_NAME": "会社名",
        "prob": "TOB確率",
        "market_cap_oku": "時価総額(億)",
        "MARKET_CATEGORY": "市場",
        "reasons": "根拠",
    }
    out = result[[c for c in display_cols if c in result.columns]].rename(columns=display_names)

    if args.format == "csv":
        out.to_csv(sys.stdout, index=False, encoding="utf-8")
    else:
        print(f"\n=== TOBスクリーニング {latest_year} ===")
        print(f"対象: {len(preds)}社中 上位{n_show}社")
        if args.multi_year:
            print(f"方式: 直近2年平均確率")
        print()
        pd.set_option("display.max_colwidth", 40)
        pd.set_option("display.width", 200)
        pd.set_option("display.max_rows", n_show + 5)
        out["TOB確率"] = out["TOB確率"].map("{:.3f}".format)
        out["時価総額(億)"] = out["時価総額(億)"].map(lambda x: f"{x:,.0f}" if pd.notna(x) else "-")
        print(out.to_string(index=False))


if __name__ == "__main__":
    main()

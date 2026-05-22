# -*- coding: utf-8 -*-
"""Screen parent-subsidiary listing names for pre-earnings TOB-expectation runups.

The expensive part is one BigQuery query that creates event-level features from
FIN_SUMMARY and STOCK_PRICE. The result is cached locally; scoring changes reuse
the cache unless --refresh-cache is specified.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account


PROJECT_ID = "gmailpj-357912"
KEY_FILE = Path("keys/gcp-service-account.json")
DEFAULT_INPUT = Path(r"C:\Users\zonekun\Dropbox\stock\temp\oyako.txt")
DEFAULT_CACHE = Path("data/cache/oyako_tob_expectation_events.csv")
DEFAULT_OUTPUT = Path("data/output/oyako_tob_expectation_classification.csv")


EVENT_FEATURE_SQL = """
WITH
events AS (
  SELECT
    LOCAL_CODE AS TICKER,
    DISCLOSURE_NUMBER,
    DISCLOSED_DATE,
    DISCLOSED_TIME,
    TYPE_OF_DOCUMENT,
    TYPE_OF_CURRENT_PERIOD,
    CURRENT_PERIOD_END_DATE,
    ROW_NUMBER() OVER (
      PARTITION BY LOCAL_CODE, DISCLOSED_DATE, TYPE_OF_CURRENT_PERIOD
      ORDER BY DISCLOSED_TIME DESC, DISCLOSURE_NUMBER DESC
    ) AS event_dedup_rn
  FROM `gmailpj-357912.STOCK.FIN_SUMMARY`
  WHERE LOCAL_CODE IN UNNEST(@tickers)
    AND DISCLOSED_DATE >= @start_date
    AND TYPE_OF_DOCUMENT LIKE '%FinancialStatements%'
    AND LOCAL_CODE NOT IN (
      SELECT TICKER
      FROM `gmailpj-357912.STOCK.DELISTED_STOCKS`
      WHERE TICKER IS NOT NULL
    )
),
price_rank AS (
  SELECT
    TICKER,
    YEARDATE,
    CLOSE,
    VOLUME,
    ROW_NUMBER() OVER (PARTITION BY TICKER ORDER BY YEARDATE) AS trade_rn
  FROM `gmailpj-357912.STOCK.STOCK_PRICE`
  WHERE TICKER IN UNNEST(@tickers)
    AND TICKER NOT IN (
      SELECT TICKER
      FROM `gmailpj-357912.STOCK.DELISTED_STOCKS`
      WHERE TICKER IS NOT NULL
    )
    AND YEARDATE >= DATE_SUB(@start_date, INTERVAL 220 DAY)
    AND CLOSE IS NOT NULL
    AND CLOSE > 0
),
anchored AS (
  SELECT
    e.*,
    a.YEARDATE AS anchor_date,
    a.trade_rn AS anchor_rn,
    a.CLOSE AS anchor_close
  FROM events e
  JOIN price_rank a
    ON a.TICKER = e.TICKER
   AND a.YEARDATE <= e.DISCLOSED_DATE
  WHERE e.event_dedup_rn = 1
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY e.TICKER, e.DISCLOSED_DATE, e.TYPE_OF_CURRENT_PERIOD
    ORDER BY a.YEARDATE DESC
  ) = 1
),
event_prices AS (
  SELECT
    a.*,
    p5.YEARDATE AS pre5_date,
    p5.CLOSE AS pre5_close,
    p10.YEARDATE AS pre10_date,
    p10.CLOSE AS pre10_close,
    p20.YEARDATE AS pre20_date,
    p20.CLOSE AS pre20_close,
    p40.YEARDATE AS pre40_date,
    p40.CLOSE AS pre40_close,
    q1.YEARDATE AS post1_date,
    q1.CLOSE AS post1_close,
    q3.YEARDATE AS post3_date,
    q3.CLOSE AS post3_close,
    q5.YEARDATE AS post5_date,
    q5.CLOSE AS post5_close,
    (
      SELECT AVG(v.VOLUME)
      FROM price_rank v
      WHERE v.TICKER = a.TICKER
        AND v.trade_rn BETWEEN a.anchor_rn - 20 AND a.anchor_rn - 1
    ) AS avg_volume_pre20,
    (
      SELECT AVG(v.VOLUME)
      FROM price_rank v
      WHERE v.TICKER = a.TICKER
        AND v.trade_rn BETWEEN a.anchor_rn - 120 AND a.anchor_rn - 21
    ) AS avg_volume_base100
  FROM anchored a
  LEFT JOIN price_rank p5
    ON p5.TICKER = a.TICKER AND p5.trade_rn = a.anchor_rn - 5
  LEFT JOIN price_rank p10
    ON p10.TICKER = a.TICKER AND p10.trade_rn = a.anchor_rn - 10
  LEFT JOIN price_rank p20
    ON p20.TICKER = a.TICKER AND p20.trade_rn = a.anchor_rn - 20
  LEFT JOIN price_rank p40
    ON p40.TICKER = a.TICKER AND p40.trade_rn = a.anchor_rn - 40
  LEFT JOIN price_rank q1
    ON q1.TICKER = a.TICKER AND q1.trade_rn = a.anchor_rn + 1
  LEFT JOIN price_rank q3
    ON q3.TICKER = a.TICKER AND q3.trade_rn = a.anchor_rn + 3
  LEFT JOIN price_rank q5
    ON q5.TICKER = a.TICKER AND q5.trade_rn = a.anchor_rn + 5
),
bench AS (
  SELECT DATE, CLOSE
  FROM `gmailpj-357912.STOCK.INDEX_PRICE`
  WHERE INDEX_CODE = 'N225'
    AND DATE >= DATE_SUB(@start_date, INTERVAL 220 DAY)
    AND CLOSE IS NOT NULL
    AND CLOSE > 0
)
SELECT
  ep.TICKER,
  sc.STOCK_NAME,
  sc.MARKET_CATEGORY,
  sc.INDUSTRY_33_CATEGORY,
  ep.DISCLOSURE_NUMBER,
  ep.DISCLOSED_DATE,
  ep.DISCLOSED_TIME,
  ep.TYPE_OF_DOCUMENT,
  ep.TYPE_OF_CURRENT_PERIOD,
  ep.CURRENT_PERIOD_END_DATE,
  ep.anchor_date,
  ep.anchor_close,
  SAFE_DIVIDE(ep.anchor_close, ep.pre5_close) - 1 AS ret_pre5,
  SAFE_DIVIDE(ep.anchor_close, ep.pre10_close) - 1 AS ret_pre10,
  SAFE_DIVIDE(ep.anchor_close, ep.pre20_close) - 1 AS ret_pre20,
  SAFE_DIVIDE(ep.anchor_close, ep.pre40_close) - 1 AS ret_pre40,
  SAFE_DIVIDE(ep.post1_close, ep.anchor_close) - 1 AS ret_post1,
  SAFE_DIVIDE(ep.post3_close, ep.anchor_close) - 1 AS ret_post3,
  SAFE_DIVIDE(ep.post5_close, ep.anchor_close) - 1 AS ret_post5,
  SAFE_DIVIDE(ep.avg_volume_pre20, ep.avg_volume_base100) AS volume_ratio,
  SAFE_DIVIDE(ba.CLOSE, b5.CLOSE) - 1 AS bench_pre5,
  SAFE_DIVIDE(ba.CLOSE, b10.CLOSE) - 1 AS bench_pre10,
  SAFE_DIVIDE(ba.CLOSE, b20.CLOSE) - 1 AS bench_pre20,
  SAFE_DIVIDE(ba.CLOSE, b40.CLOSE) - 1 AS bench_pre40,
  SAFE_DIVIDE(bq1.CLOSE, ba.CLOSE) - 1 AS bench_post1,
  SAFE_DIVIDE(bq3.CLOSE, ba.CLOSE) - 1 AS bench_post3,
  SAFE_DIVIDE(bq5.CLOSE, ba.CLOSE) - 1 AS bench_post5
FROM event_prices ep
LEFT JOIN `gmailpj-357912.STOCK.STOCK_CODE_LIST` sc
  ON sc.TICKER = ep.TICKER
LEFT JOIN bench ba
  ON ba.DATE = ep.anchor_date
LEFT JOIN bench b5
  ON b5.DATE = ep.pre5_date
LEFT JOIN bench b10
  ON b10.DATE = ep.pre10_date
LEFT JOIN bench b20
  ON b20.DATE = ep.pre20_date
LEFT JOIN bench b40
  ON b40.DATE = ep.pre40_date
LEFT JOIN bench bq1
  ON bq1.DATE = ep.post1_date
LEFT JOIN bench bq3
  ON bq3.DATE = ep.post3_date
LEFT JOIN bench bq5
  ON bq5.DATE = ep.post5_date
ORDER BY ep.TICKER, ep.DISCLOSED_DATE
"""


def read_tickers(path: Path) -> list[str]:
    rows = pd.read_csv(path, sep="\t", header=None, dtype=str, encoding="cp932")
    tickers = rows.iloc[:, 0].str.extract(r"(\d{4})", expand=False).dropna()
    return sorted(tickers.drop_duplicates().tolist())


def get_bq_client() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(str(KEY_FILE))
    return bigquery.Client(project=PROJECT_ID, credentials=creds)


def fetch_event_features(tickers: list[str], start_date: str) -> pd.DataFrame:
    cfg = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("tickers", "STRING", tickers),
            bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        ]
    )
    return get_bq_client().query(EVENT_FEATURE_SQL, job_config=cfg).to_dataframe()


def add_event_scores(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for window in (5, 10, 20, 40):
        out[f"abn_pre{window}"] = out[f"ret_pre{window}"] - out[f"bench_pre{window}"]
    for window in (1, 3, 5):
        out[f"abn_post{window}"] = out[f"ret_post{window}"] - out[f"bench_post{window}"]

    out["strong_runup_event"] = (
        (out["abn_pre5"] >= 0.03)
        | (out["abn_pre10"] >= 0.04)
        | (out["abn_pre20"] >= 0.06)
        | (out["abn_pre40"] >= 0.08)
    )
    out["fade_after_event"] = out["strong_runup_event"] & (out["abn_post5"] <= -0.03)
    return out


def _safe_median(series: pd.Series) -> float:
    value = series.dropna().median()
    return float(value) if pd.notna(value) else np.nan


def _next_period(period: object) -> str | None:
    period_text = str(period)
    return {
        "1Q": "2Q",
        "2Q": "3Q",
        "3Q": "FY",
        "FY": "1Q",
    }.get(period_text)


def _project_next_expected_date(g: pd.DataFrame, as_of: pd.Timestamp) -> pd.Timestamp | pd.NaT:
    ordered = g.dropna(subset=["DISCLOSED_DATE"]).copy()
    if ordered.empty:
        return pd.NaT
    ordered["DISCLOSED_DATE"] = pd.to_datetime(ordered["DISCLOSED_DATE"])
    latest = ordered.sort_values(["DISCLOSED_DATE", "DISCLOSED_TIME"]).iloc[-1]
    target_period = _next_period(latest["TYPE_OF_CURRENT_PERIOD"])
    period_dates = ordered.loc[ordered["TYPE_OF_CURRENT_PERIOD"].eq(target_period), "DISCLOSED_DATE"]
    if period_dates.empty:
        period_dates = ordered["DISCLOSED_DATE"]

    candidates = []
    for value in pd.to_datetime(period_dates.dropna()):
        projected = value
        while projected.date() <= as_of.date():
            projected = projected + pd.DateOffset(years=1)
        candidates.append(projected.normalize())
    if not candidates:
        return pd.NaT
    projected = min(candidates)
    if projected.weekday() >= 5:
        projected = projected - pd.offsets.BDay(1)
    return projected.normalize()


def _entry_date(expected_date: pd.Timestamp | pd.NaT, window_days: str) -> pd.Timestamp | pd.NaT:
    if pd.isna(expected_date) or not window_days:
        return pd.NaT
    return (expected_date - pd.offsets.BDay(int(window_days))).normalize()


def classify(events: pd.DataFrame, as_of: date) -> pd.DataFrame:
    scored = add_event_scores(events)
    rows: list[dict[str, object]] = []
    as_of_ts = pd.Timestamp(as_of)

    for ticker, g in scored.groupby("TICKER", dropna=False):
        valid_pre = g[["abn_pre5", "abn_pre10", "abn_pre20", "abn_pre40"]].notna().any(axis=1)
        event_count = int(valid_pre.sum())
        strong_events = g.loc[valid_pre, "strong_runup_event"]
        runup_rate = float(strong_events.mean()) if event_count else np.nan

        medians = {
            "5": _safe_median(g["abn_pre5"]),
            "10": _safe_median(g["abn_pre10"]),
            "20": _safe_median(g["abn_pre20"]),
            "40": _safe_median(g["abn_pre40"]),
        }
        valid_medians = {k: v for k, v in medians.items() if pd.notna(v)}
        best_window = max(valid_medians, key=valid_medians.get) if valid_medians else ""
        best_median = valid_medians.get(best_window, np.nan)

        strong_g = g[g["strong_runup_event"]]
        fade_rate = float(strong_g["fade_after_event"].mean()) if len(strong_g) else np.nan
        volume_ratio_median = _safe_median(g["volume_ratio"])
        volume_component = 0.0
        if pd.notna(volume_ratio_median):
            volume_component = min(max((volume_ratio_median - 1.0) / 1.0, 0.0), 1.0)

        score = (
            45.0 * (runup_rate if pd.notna(runup_rate) else 0.0)
            + 30.0 * min(max((best_median if pd.notna(best_median) else 0.0) / 0.10, 0.0), 1.0)
            + 15.0 * volume_component
            + 10.0 * (fade_rate if pd.notna(fade_rate) else 0.0)
        )

        if event_count >= 8 and runup_rate >= 0.45 and best_median >= 0.035:
            pattern_class = "strong_pre_runup"
            pattern_rank = 1
        elif event_count >= 6 and runup_rate >= 0.30 and best_median >= 0.020:
            pattern_class = "moderate_pre_runup"
            pattern_rank = 2
        else:
            pattern_class = "no_clear_pattern"
            pattern_rank = 9

        if pattern_class != "no_clear_pattern":
            if best_window == "40":
                timing_class = "early_40d"
            elif best_window == "20":
                timing_class = "standard_20d"
            else:
                timing_class = f"late_{best_window}d"
        else:
            timing_class = ""

        fade_class = "fade_after_earnings" if pd.notna(fade_rate) and fade_rate >= 0.40 else ""
        next_expected_earnings_date = _project_next_expected_date(g, as_of_ts)
        entry_date = _entry_date(next_expected_earnings_date, best_window)

        rows.append(
            {
                "TICKER": ticker,
                "STOCK_NAME": g["STOCK_NAME"].dropna().iloc[0] if g["STOCK_NAME"].notna().any() else "",
                "MARKET_CATEGORY": (
                    g["MARKET_CATEGORY"].dropna().iloc[0] if g["MARKET_CATEGORY"].notna().any() else ""
                ),
                "INDUSTRY_33_CATEGORY": (
                    g["INDUSTRY_33_CATEGORY"].dropna().iloc[0]
                    if g["INDUSTRY_33_CATEGORY"].notna().any()
                    else ""
                ),
                "event_count": event_count,
                "pattern_score": round(score, 2),
                "pattern_rank": pattern_rank,
                "pattern_class": pattern_class,
                "timing_class": timing_class,
                "next_expected_earnings_date": next_expected_earnings_date,
                "entry_date": entry_date,
                "fade_class": fade_class,
                "runup_event_rate": round(runup_rate, 4) if pd.notna(runup_rate) else np.nan,
                "fade_after_runup_rate": round(fade_rate, 4) if pd.notna(fade_rate) else np.nan,
                "best_pre_window_days": int(best_window) if best_window else np.nan,
                "best_pre_abn_median": round(best_median, 4) if pd.notna(best_median) else np.nan,
                "abn_pre5_median": round(medians["5"], 4) if pd.notna(medians["5"]) else np.nan,
                "abn_pre10_median": round(medians["10"], 4) if pd.notna(medians["10"]) else np.nan,
                "abn_pre20_median": round(medians["20"], 4) if pd.notna(medians["20"]) else np.nan,
                "abn_pre40_median": round(medians["40"], 4) if pd.notna(medians["40"]) else np.nan,
                "volume_ratio_median": (
                    round(volume_ratio_median, 4) if pd.notna(volume_ratio_median) else np.nan
                ),
                "latest_disclosed_date": g["DISCLOSED_DATE"].max(),
            }
        )

    result = pd.DataFrame(rows)
    return result.sort_values(
        ["pattern_rank", "pattern_score", "runup_event_rate", "best_pre_abn_median"],
        ascending=[True, False, False, False],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--start-date", default="2016-01-01")
    parser.add_argument(
        "--as-of",
        default=datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat(),
        help="Date used when projecting the next expected earnings date.",
    )
    parser.add_argument("--refresh-cache", action="store_true")
    args = parser.parse_args()

    tickers = read_tickers(args.input)
    print(f"tickers={len(tickers)}")

    args.cache.parent.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    if args.cache.exists() and not args.refresh_cache:
        print(f"cache=hit {args.cache}")
        events = pd.read_csv(args.cache, dtype={"TICKER": str})
    else:
        print(f"cache=miss; fetching event features from BigQuery once -> {args.cache}")
        events = fetch_event_features(tickers, args.start_date)
        events.to_csv(args.cache, index=False, encoding="utf-8-sig")

    classification = classify(events, date.fromisoformat(args.as_of))
    classification.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"events={len(events)} output_rows={len(classification)}")
    print(f"output={args.output}")
    print(classification.head(20).to_string(index=False))


if __name__ == "__main__":
    main()

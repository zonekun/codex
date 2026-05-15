"""Screen repeat conservative-guidance patterns around FY earnings.

The screener looks for fiscal years where a company issued weak initial FY
guidance, the stock sold off after the FY results announcement, and the final
actual result later landed near or above the prior-year result.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from google.cloud import bigquery
from google.oauth2 import service_account

PROJECT_ID = "gmailpj-357912"
KEY_FILE = "keys/gcp-service-account.json"
DEFAULT_OUTPUT_DIR = Path("data/output/fy_conservative_guidance")

log = structlog.get_logger()


SQL = """
WITH universe_base AS (
  SELECT TICKER, STOCK_NAME, MARKET_CATEGORY, EXCHANGE,
         INDUSTRY_33_CATEGORY, INDUSTRY_17_CATEGORY
  FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
  WHERE TICKER IS NOT NULL
    AND MARKET_CATEGORY IS NOT NULL
    AND NOT REGEXP_CONTAINS(
      MARKET_CATEGORY,
      r'(ETF|ETN|REIT|ファンド|PRO|外国|出資証券|その他)'
    )
),
universe_ranked AS (
  SELECT
    *,
    ROW_NUMBER() OVER (PARTITION BY TICKER ORDER BY EXCHANGE) AS TICKER_RN,
    ROW_NUMBER() OVER (ORDER BY TICKER) AS UNIVERSE_RN
  FROM universe_base
),
universe AS (
  SELECT * EXCEPT(TICKER_RN, UNIVERSE_RN)
  FROM universe_ranked
  WHERE TICKER_RN = 1
    AND (@limit_tickers IS NULL OR UNIVERSE_RN <= @limit_tickers)
),
fin_all AS (
  SELECT
    LOCAL_CODE AS TICKER,
    DISCLOSED_DATE,
    DISCLOSED_TIME,
    DISCLOSURE_NUMBER,
    TYPE_OF_DOCUMENT,
    TYPE_OF_CURRENT_PERIOD,
    CURRENT_FISCAL_YEAR_END_DATE,
    NEXT_FISCAL_YEAR_END_DATE,
    OPERATING_PROFIT,
    ORDINARY_PROFIT,
    PROFIT,
    COALESCE(OPERATING_PROFIT, ORDINARY_PROFIT, PROFIT) AS ACTUAL_METRIC,
    CASE
      WHEN OPERATING_PROFIT IS NOT NULL THEN 'operating_profit'
      WHEN ORDINARY_PROFIT IS NOT NULL THEN 'ordinary_profit'
      WHEN PROFIT IS NOT NULL THEN 'profit'
      ELSE NULL
    END AS ACTUAL_METRIC_NAME,
    COALESCE(
      NEXT_YEAR_FORECAST_OPERATING_PROFIT,
      NEXT_YEAR_FORECAST_ORDINARY_PROFIT,
      NEXT_YEAR_FORECAST_PROFIT
    ) AS INITIAL_FORECAST_METRIC,
    CASE
      WHEN NEXT_YEAR_FORECAST_OPERATING_PROFIT IS NOT NULL THEN 'operating_profit'
      WHEN NEXT_YEAR_FORECAST_ORDINARY_PROFIT IS NOT NULL THEN 'ordinary_profit'
      WHEN NEXT_YEAR_FORECAST_PROFIT IS NOT NULL THEN 'profit'
      ELSE NULL
    END AS INITIAL_FORECAST_METRIC_NAME,
    COALESCE(
      FORECAST_OPERATING_PROFIT,
      FORECAST_ORDINARY_PROFIT,
      FORECAST_PROFIT,
      NEXT_YEAR_FORECAST_OPERATING_PROFIT,
      NEXT_YEAR_FORECAST_ORDINARY_PROFIT,
      NEXT_YEAR_FORECAST_PROFIT
    ) AS REVISION_FORECAST_METRIC
  FROM `gmailpj-357912.STOCK.FIN_SUMMARY`
  WHERE LOCAL_CODE IN (SELECT TICKER FROM universe)
    AND TYPE_OF_CURRENT_PERIOD = 'FY'
    AND CURRENT_FISCAL_YEAR_END_DATE IS NOT NULL
    AND (
      TYPE_OF_DOCUMENT LIKE 'FYFinancialStatements_%'
      OR TYPE_OF_DOCUMENT = 'EarnForecastRevision'
    )
),
fy_source AS (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY TICKER, CURRENT_FISCAL_YEAR_END_DATE
      ORDER BY
        DISCLOSED_DATE ASC,
        CASE WHEN TYPE_OF_DOCUMENT LIKE '%Consolidated%' THEN 0 ELSE 1 END,
        DISCLOSURE_NUMBER ASC
    ) AS rn
  FROM fin_all
  WHERE TYPE_OF_DOCUMENT LIKE 'FYFinancialStatements_%'
),
fy_rows AS (
  SELECT * EXCEPT(rn)
  FROM fy_source
  WHERE rn = 1
),
initial_events AS (
  SELECT
    fy.TICKER,
    u.STOCK_NAME,
    u.MARKET_CATEGORY,
    u.EXCHANGE,
    u.INDUSTRY_33_CATEGORY,
    u.INDUSTRY_17_CATEGORY,
    fy.DISCLOSED_DATE AS INITIAL_DISCLOSED_DATE,
    fy.DISCLOSED_TIME AS INITIAL_DISCLOSED_TIME,
    fy.DISCLOSURE_NUMBER AS INITIAL_DISCLOSURE_NUMBER,
    fy.TYPE_OF_DOCUMENT AS INITIAL_TYPE_OF_DOCUMENT,
    fy.CURRENT_FISCAL_YEAR_END_DATE AS PREVIOUS_FISCAL_YEAR_END_DATE,
    fy.NEXT_FISCAL_YEAR_END_DATE AS TARGET_FISCAL_YEAR_END_DATE,
    fy.ACTUAL_METRIC AS PREVIOUS_ACTUAL_METRIC,
    fy.ACTUAL_METRIC_NAME AS PREVIOUS_ACTUAL_METRIC_NAME,
    fy.INITIAL_FORECAST_METRIC,
    fy.INITIAL_FORECAST_METRIC_NAME
  FROM fy_rows fy
  JOIN universe u USING (TICKER)
  WHERE fy.DISCLOSED_DATE BETWEEN @date_from AND @date_to
    AND fy.NEXT_FISCAL_YEAR_END_DATE IS NOT NULL
    AND fy.ACTUAL_METRIC IS NOT NULL
    AND fy.INITIAL_FORECAST_METRIC IS NOT NULL
),
final_actuals AS (
  SELECT
    TICKER,
    CURRENT_FISCAL_YEAR_END_DATE AS TARGET_FISCAL_YEAR_END_DATE,
    DISCLOSED_DATE AS FINAL_DISCLOSED_DATE,
    DISCLOSED_TIME AS FINAL_DISCLOSED_TIME,
    DISCLOSURE_NUMBER AS FINAL_DISCLOSURE_NUMBER,
    TYPE_OF_DOCUMENT AS FINAL_TYPE_OF_DOCUMENT,
    ACTUAL_METRIC AS FINAL_ACTUAL_METRIC,
    ACTUAL_METRIC_NAME AS FINAL_ACTUAL_METRIC_NAME
  FROM fy_rows
),
revision_source AS (
  SELECT
    TICKER,
    CURRENT_FISCAL_YEAR_END_DATE AS TARGET_FISCAL_YEAR_END_DATE,
    DISCLOSED_DATE,
    DISCLOSED_TIME,
    DISCLOSURE_NUMBER,
    REVISION_FORECAST_METRIC
  FROM fin_all
  WHERE TYPE_OF_DOCUMENT = 'EarnForecastRevision'
),
revision_summary AS (
  SELECT
    ie.TICKER,
    ie.TARGET_FISCAL_YEAR_END_DATE,
    COUNTIF(rs.REVISION_FORECAST_METRIC IS NOT NULL) AS REVISION_COUNT,
    MIN(rs.DISCLOSED_DATE) AS FIRST_REVISION_DATE,
    MAX(rs.DISCLOSED_DATE) AS LAST_REVISION_DATE,
    MAX(rs.REVISION_FORECAST_METRIC) AS MAX_REVISION_FORECAST_METRIC,
    ARRAY_AGG(
      STRUCT(
        rs.DISCLOSED_DATE AS disclosed_date,
        rs.DISCLOSURE_NUMBER AS disclosure_number,
        rs.REVISION_FORECAST_METRIC AS forecast_metric
      )
      IGNORE NULLS
      ORDER BY rs.DISCLOSED_DATE
      LIMIT 5
    ) AS REVISION_SAMPLES
  FROM initial_events ie
  LEFT JOIN revision_source rs
    ON rs.TICKER = ie.TICKER
   AND rs.TARGET_FISCAL_YEAR_END_DATE = ie.TARGET_FISCAL_YEAR_END_DATE
   AND rs.DISCLOSED_DATE > ie.INITIAL_DISCLOSED_DATE
  GROUP BY ie.TICKER, ie.TARGET_FISCAL_YEAR_END_DATE
),
tdnet_distinct AS (
  SELECT DISTINCT
    DOC_ID,
    TICKER,
    SUBMISSION_DATE,
    MAIN_CATEGORY,
    DOC_TITLE,
    SUB_CATEGORIES,
    DISCLOSURE_TIME
  FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
  WHERE TICKER IN (SELECT TICKER FROM universe)
    AND SUBMISSION_DATE BETWEEN DATE_SUB(@date_from, INTERVAL 7 DAY)
                            AND DATE_ADD(@date_to, INTERVAL 370 DAY)
    AND (
      MAIN_CATEGORY IN ('決算短信', '業績修正', '業績予想')
      OR REGEXP_CONTAINS(DOC_TITLE, r'(決算短信|業績予想|業績.*修正|通期.*修正)')
    )
),
tdnet_fy AS (
  SELECT
    TICKER,
    SUBMISSION_DATE,
    ARRAY_AGG(
      STRUCT(MAIN_CATEGORY, SUB_CATEGORIES, DOC_TITLE, DISCLOSURE_TIME)
      ORDER BY DOC_TITLE
      LIMIT 5
    ) AS FY_TDNET_EVENTS
  FROM tdnet_distinct
  WHERE MAIN_CATEGORY = '決算短信'
     OR REGEXP_CONTAINS(DOC_TITLE, r'決算短信')
  GROUP BY TICKER, SUBMISSION_DATE
),
tdnet_revision AS (
  SELECT
    TICKER,
    SUBMISSION_DATE,
    ARRAY_AGG(
      STRUCT(MAIN_CATEGORY, SUB_CATEGORIES, DOC_TITLE, DISCLOSURE_TIME)
      ORDER BY DOC_TITLE
      LIMIT 5
    ) AS REVISION_TDNET_EVENTS
  FROM tdnet_distinct
  WHERE MAIN_CATEGORY IN ('業績修正', '業績予想')
     OR REGEXP_CONTAINS(DOC_TITLE, r'(業績予想|業績.*修正|通期.*修正)')
  GROUP BY TICKER, SUBMISSION_DATE
),
base AS (
  SELECT
    ie.*,
    fa.FINAL_DISCLOSED_DATE,
    fa.FINAL_DISCLOSED_TIME,
    fa.FINAL_DISCLOSURE_NUMBER,
    fa.FINAL_TYPE_OF_DOCUMENT,
    fa.FINAL_ACTUAL_METRIC,
    fa.FINAL_ACTUAL_METRIC_NAME,
    rs.REVISION_COUNT,
    rs.FIRST_REVISION_DATE,
    rs.LAST_REVISION_DATE,
    rs.MAX_REVISION_FORECAST_METRIC,
    rs.REVISION_SAMPLES,
    tf.FY_TDNET_EVENTS
  FROM initial_events ie
  LEFT JOIN final_actuals fa
    ON fa.TICKER = ie.TICKER
   AND fa.TARGET_FISCAL_YEAR_END_DATE = ie.TARGET_FISCAL_YEAR_END_DATE
  LEFT JOIN revision_summary rs
    ON rs.TICKER = ie.TICKER
   AND rs.TARGET_FISCAL_YEAR_END_DATE = ie.TARGET_FISCAL_YEAR_END_DATE
  LEFT JOIN tdnet_fy tf
    ON tf.TICKER = ie.TICKER
   AND tf.SUBMISSION_DATE = ie.INITIAL_DISCLOSED_DATE
),
price_window AS (
  SELECT
    b.TICKER,
    b.TARGET_FISCAL_YEAR_END_DATE,
    p.YEARDATE,
    p.CLOSE,
    CASE WHEN p.YEARDATE < b.INITIAL_DISCLOSED_DATE THEN 'PREV' ELSE 'AFTER' END AS SIDE,
    ROW_NUMBER() OVER (
      PARTITION BY b.TICKER, b.TARGET_FISCAL_YEAR_END_DATE,
        CASE WHEN p.YEARDATE < b.INITIAL_DISCLOSED_DATE THEN 'PREV' ELSE 'AFTER' END
      ORDER BY CASE WHEN p.YEARDATE < b.INITIAL_DISCLOSED_DATE
        THEN -1 * UNIX_DATE(p.YEARDATE) ELSE UNIX_DATE(p.YEARDATE) END
    ) AS rn
  FROM base b
  JOIN `gmailpj-357912.STOCK.STOCK_PRICE` p
    ON p.TICKER = b.TICKER
   AND p.YEARDATE BETWEEN DATE_SUB(b.INITIAL_DISCLOSED_DATE, INTERVAL 10 DAY)
                       AND DATE_ADD(b.INITIAL_DISCLOSED_DATE, INTERVAL 10 DAY)
   AND p.YEARDATE != b.INITIAL_DISCLOSED_DATE
),
price_prev AS (
  SELECT TICKER, TARGET_FISCAL_YEAR_END_DATE,
         YEARDATE AS PREV_PRICE_DATE, CLOSE AS PREV_CLOSE
  FROM price_window WHERE SIDE = 'PREV' AND rn = 1
),
price_next AS (
  SELECT TICKER, TARGET_FISCAL_YEAR_END_DATE,
         YEARDATE AS NEXT_PRICE_DATE, CLOSE AS NEXT_CLOSE
  FROM price_window WHERE SIDE = 'AFTER' AND rn = 1
),
price_3d AS (
  SELECT TICKER, TARGET_FISCAL_YEAR_END_DATE,
         YEARDATE AS PRICE_3D_DATE, CLOSE AS CLOSE_3D
  FROM price_window WHERE SIDE = 'AFTER' AND rn = 3
),
tdnet_revision_groups AS (
  SELECT
    b.TICKER,
    b.TARGET_FISCAL_YEAR_END_DATE,
    ARRAY_AGG(
      STRUCT(
        tr.SUBMISSION_DATE AS submission_date,
        tr.REVISION_TDNET_EVENTS AS events
      )
      IGNORE NULLS
      ORDER BY tr.SUBMISSION_DATE
      LIMIT 5
    ) AS REVISION_TDNET_EVENT_GROUPS
  FROM base b
  LEFT JOIN tdnet_revision tr
    ON tr.TICKER = b.TICKER
   AND tr.SUBMISSION_DATE > b.INITIAL_DISCLOSED_DATE
   AND (
     b.FINAL_DISCLOSED_DATE IS NULL
     OR tr.SUBMISSION_DATE <= b.FINAL_DISCLOSED_DATE
   )
  GROUP BY b.TICKER, b.TARGET_FISCAL_YEAR_END_DATE
),
with_price AS (
  SELECT
    b.*,
    pp.PREV_PRICE_DATE,
    pp.PREV_CLOSE,
    pn.NEXT_PRICE_DATE,
    pn.NEXT_CLOSE,
    p3.PRICE_3D_DATE,
    p3.CLOSE_3D,
    trg.REVISION_TDNET_EVENT_GROUPS
  FROM base b
  LEFT JOIN price_prev pp
    ON pp.TICKER = b.TICKER
   AND pp.TARGET_FISCAL_YEAR_END_DATE = b.TARGET_FISCAL_YEAR_END_DATE
  LEFT JOIN price_next pn
    ON pn.TICKER = b.TICKER
   AND pn.TARGET_FISCAL_YEAR_END_DATE = b.TARGET_FISCAL_YEAR_END_DATE
  LEFT JOIN price_3d p3
    ON p3.TICKER = b.TICKER
   AND p3.TARGET_FISCAL_YEAR_END_DATE = b.TARGET_FISCAL_YEAR_END_DATE
  LEFT JOIN tdnet_revision_groups trg
    ON trg.TICKER = b.TICKER
   AND trg.TARGET_FISCAL_YEAR_END_DATE = b.TARGET_FISCAL_YEAR_END_DATE
)
SELECT
  wp.*,
  SAFE_DIVIDE(INITIAL_FORECAST_METRIC, PREVIOUS_ACTUAL_METRIC) - 1 AS INITIAL_GROWTH,
  SAFE_DIVIDE(FINAL_ACTUAL_METRIC, INITIAL_FORECAST_METRIC) - 1 AS ACTUAL_VS_INITIAL,
  SAFE_DIVIDE(FINAL_ACTUAL_METRIC, PREVIOUS_ACTUAL_METRIC) - 1 AS ACTUAL_GROWTH,
  SAFE_DIVIDE(MAX_REVISION_FORECAST_METRIC, INITIAL_FORECAST_METRIC) - 1 AS MAX_REVISION_VS_INITIAL,
  SAFE_DIVIDE(NEXT_CLOSE, PREV_CLOSE) - 1 AS RET_1D,
  SAFE_DIVIDE(CLOSE_3D, PREV_CLOSE) - 1 AS RET_3D
FROM with_price wp
ORDER BY TICKER, TARGET_FISCAL_YEAR_END_DATE
"""


@dataclass(frozen=True)
class Thresholds:
    """Pattern classification thresholds."""

    weak_growth_max: float
    selloff_1d: float
    selloff_3d: float
    min_actual_vs_initial: float
    min_actual_growth: float
    strong_actual_vs_initial: float
    strong_actual_growth: float
    positive_revision_min: float
    min_hit_count: int
    min_hit_rate: float


def configure_logging() -> None:
    """Configure structlog console output."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_bq_client() -> bigquery.Client:
    """Build a BigQuery client with explicit service-account credentials."""
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return bigquery.Client(project=PROJECT_ID, credentials=creds)


def parse_date(value: str) -> date:
    """Parse a YYYY-MM-DD string as a date."""
    return datetime.strptime(value, "%Y-%m-%d").date()


def scalar(value: Any) -> Any:
    """Convert BigQuery scalar values into CSV/JSON friendly values."""
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def pct(value: Any) -> str:
    """Format a ratio as a percent string."""
    if value in (None, ""):
        return ""
    return f"{float(value) * 100:.1f}%"


def money(value: Any) -> str:
    """Format a financial numeric value compactly."""
    if value in (None, ""):
        return ""
    return str(int(value))


def row_to_dict(row: bigquery.table.Row) -> dict[str, Any]:
    """Convert a BigQuery row to a mutable dictionary."""
    return dict(row.items())


def classify_row(row: dict[str, Any], thresholds: Thresholds) -> dict[str, Any]:
    """Classify one ticker-year row.

    Args:
        row: BigQuery result row.
        thresholds: Classification thresholds.

    Returns:
        Row augmented with boolean flags and reason text.
    """
    initial_growth = row.get("INITIAL_GROWTH")
    ret_1d = row.get("RET_1D")
    ret_3d = row.get("RET_3D")
    actual_vs_initial = row.get("ACTUAL_VS_INITIAL")
    actual_growth = row.get("ACTUAL_GROWTH")
    max_revision_vs_initial = row.get("MAX_REVISION_VS_INITIAL")
    previous_actual = row.get("PREVIOUS_ACTUAL_METRIC")
    initial_forecast = row.get("INITIAL_FORECAST_METRIC")
    final_actual = row.get("FINAL_ACTUAL_METRIC")
    actual_metric_name = row.get("PREVIOUS_ACTUAL_METRIC_NAME")
    forecast_metric_name = row.get("INITIAL_FORECAST_METRIC_NAME")
    invalid_baseline = (
        previous_actual is None
        or initial_forecast is None
        or final_actual is None
        or float(previous_actual) <= 0
        or float(initial_forecast) <= 0
        or (actual_metric_name is not None
            and forecast_metric_name is not None
            and actual_metric_name != forecast_metric_name)
    )

    weak_guidance = (
        not invalid_baseline
        and initial_growth is not None
        and float(initial_growth) <= thresholds.weak_growth_max
    )
    selloff = (
        not invalid_baseline
        and (
            (ret_1d is not None and float(ret_1d) <= thresholds.selloff_1d)
            or (ret_3d is not None and float(ret_3d) <= thresholds.selloff_3d)
        )
    )
    positive_revision = (
        not invalid_baseline
        and max_revision_vs_initial is not None
        and float(max_revision_vs_initial) >= thresholds.positive_revision_min
    )
    conservative_landing = (
        not invalid_baseline
        and actual_vs_initial is not None
        and actual_growth is not None
        and float(actual_vs_initial) >= thresholds.min_actual_vs_initial
        and float(actual_growth) >= thresholds.min_actual_growth
    )
    strong_hit = (
        not invalid_baseline
        and actual_vs_initial is not None
        and actual_growth is not None
        and float(actual_vs_initial) >= thresholds.strong_actual_vs_initial
        and float(actual_growth) >= thresholds.strong_actual_growth
    )
    pattern_hit = weak_guidance and selloff and conservative_landing

    reasons: list[str] = []
    if invalid_baseline:
        reasons.append("invalid_baseline_nonpositive_or_missing_metric")
    if weak_guidance:
        reasons.append(f"initial_growth={pct(initial_growth)}")
    if selloff:
        reasons.append(f"ret_1d={pct(ret_1d)} ret_3d={pct(ret_3d)}")
    if conservative_landing:
        reasons.append(
            f"actual_vs_initial={pct(actual_vs_initial)} actual_growth={pct(actual_growth)}"
        )
    if positive_revision:
        reasons.append(f"positive_revision={pct(max_revision_vs_initial)}")

    row["INVALID_BASELINE"] = invalid_baseline
    row["WEAK_GUIDANCE"] = weak_guidance
    row["SELLOFF"] = selloff
    row["POSITIVE_REVISION"] = positive_revision
    row["CONSERVATIVE_LANDING"] = conservative_landing
    row["STRONG_HIT"] = strong_hit
    row["PATTERN_HIT"] = pattern_hit
    row["REASON"] = "; ".join(reasons)
    return row

def fetch_candidate_years(
    client: bigquery.Client,
    date_from: date,
    date_to: date,
    limit_tickers: int | None,
) -> list[dict[str, Any]]:
    """Fetch ticker-year source rows from BigQuery."""
    config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("date_from", "DATE", date_from),
            bigquery.ScalarQueryParameter("date_to", "DATE", date_to),
            bigquery.ScalarQueryParameter("limit_tickers", "INT64", limit_tickers),
        ]
    )
    rows = [row_to_dict(row) for row in client.query(SQL, job_config=config).result()]
    log.info(
        "candidate_years_fetched",
        rows=len(rows),
        date_from=str(date_from),
        date_to=str(date_to),
        limit_tickers=limit_tickers,
    )
    return rows


def summarize_companies(
    rows: list[dict[str, Any]],
    thresholds: Thresholds,
) -> list[dict[str, Any]]:
    """Aggregate ticker-year rows into company scores."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["TICKER"])].append(row)

    def avg(key: str, source: list[dict[str, Any]]) -> float | None:
        """Average of non-None values for key."""
        values = [float(row[key]) for row in source if row.get(key) is not None]
        return sum(values) / len(values) if values else None

    summaries: list[dict[str, Any]] = []
    for ticker, ticker_rows in grouped.items():
        evaluable = [
            row for row in ticker_rows
            if row.get("WEAK_GUIDANCE") and row.get("SELLOFF")
        ]
        hits = [row for row in ticker_rows if row.get("PATTERN_HIT")]
        if not hits:
            continue

        hit_count = len(hits)
        hit_rate = hit_count / len(evaluable) if evaluable else 0.0
        if hit_count < thresholds.min_hit_count and hit_rate < thresholds.min_hit_rate:
            continue

        first = sorted(ticker_rows, key=lambda r: str(r["TARGET_FISCAL_YEAR_END_DATE"]))[-1]
        summaries.append(
            {
                "TICKER": ticker,
                "STOCK_NAME": first.get("STOCK_NAME", ""),
                "MARKET_CATEGORY": first.get("MARKET_CATEGORY", ""),
                "INDUSTRY_33_CATEGORY": first.get("INDUSTRY_33_CATEGORY", ""),
                "YEARS_EVALUATED": len(ticker_rows),
                "WEAK_SELLOFF_YEARS": len(evaluable),
                "HIT_COUNT": hit_count,
                "HIT_RATE": hit_rate,
                "AVG_RET_1D": avg("RET_1D", hits),
                "AVG_RET_3D": avg("RET_3D", hits),
                "AVG_INITIAL_GROWTH": avg("INITIAL_GROWTH", hits),
                "AVG_ACTUAL_VS_INITIAL": avg("ACTUAL_VS_INITIAL", hits),
                "AVG_ACTUAL_GROWTH": avg("ACTUAL_GROWTH", hits),
                "POSITIVE_REVISION_YEARS": sum(
                    1 for row in hits if row.get("POSITIVE_REVISION")
                ),
                "LATEST_HIT_YEAR": max(
                    str(row["TARGET_FISCAL_YEAR_END_DATE"]) for row in hits
                ),
            }
        )

    summaries.sort(
        key=lambda row: (
            int(row["HIT_COUNT"]),
            float(row["HIT_RATE"]),
            float(row["AVG_ACTUAL_VS_INITIAL"] or 0.0),
        ),
        reverse=True,
    )
    return summaries


def json_cell(value: Any) -> str:
    """Serialize complex BigQuery cell values for CSV."""
    if value in (None, ""):
        return ""
    return json.dumps(value, ensure_ascii=False, default=str)


def write_candidate_years(rows: list[dict[str, Any]], path: Path) -> None:
    """Write ticker-year rows to CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "TICKER",
        "STOCK_NAME",
        "MARKET_CATEGORY",
        "INDUSTRY_33_CATEGORY",
        "INITIAL_DISCLOSED_DATE",
        "TARGET_FISCAL_YEAR_END_DATE",
        "FINAL_DISCLOSED_DATE",
        "PREVIOUS_ACTUAL_METRIC",
        "INITIAL_FORECAST_METRIC",
        "FINAL_ACTUAL_METRIC",
        "INITIAL_GROWTH",
        "ACTUAL_VS_INITIAL",
        "ACTUAL_GROWTH",
        "REVISION_COUNT",
        "MAX_REVISION_FORECAST_METRIC",
        "MAX_REVISION_VS_INITIAL",
        "PREV_PRICE_DATE",
        "PREV_CLOSE",
        "NEXT_PRICE_DATE",
        "NEXT_CLOSE",
        "PRICE_3D_DATE",
        "CLOSE_3D",
        "RET_1D",
        "RET_3D",
        "INVALID_BASELINE",
        "WEAK_GUIDANCE",
        "SELLOFF",
        "POSITIVE_REVISION",
        "CONSERVATIVE_LANDING",
        "STRONG_HIT",
        "PATTERN_HIT",
        "REASON",
        "FY_TDNET_EVENTS",
        "REVISION_TDNET_EVENT_GROUPS",
        "REVISION_SAMPLES",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field: (
                        json_cell(row.get(field))
                        if field.endswith("EVENTS")
                        or field.endswith("EVENT_GROUPS")
                        or field == "REVISION_SAMPLES"
                        else scalar(row.get(field))
                    )
                    for field in fields
                }
            )
    log.info("candidate_years_written", path=str(path), rows=len(rows))


def write_company_scores(rows: list[dict[str, Any]], path: Path) -> None:
    """Write company-level scores to CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "TICKER",
        "STOCK_NAME",
        "MARKET_CATEGORY",
        "INDUSTRY_33_CATEGORY",
        "YEARS_EVALUATED",
        "WEAK_SELLOFF_YEARS",
        "HIT_COUNT",
        "HIT_RATE",
        "AVG_RET_1D",
        "AVG_RET_3D",
        "AVG_INITIAL_GROWTH",
        "AVG_ACTUAL_VS_INITIAL",
        "AVG_ACTUAL_GROWTH",
        "POSITIVE_REVISION_YEARS",
        "LATEST_HIT_YEAR",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: scalar(row.get(field)) for field in fields})
    log.info("company_scores_written", path=str(path), rows=len(rows))


def write_html_report(
    candidate_rows: list[dict[str, Any]],
    company_rows: list[dict[str, Any]],
    path: Path,
    date_from: date,
    date_to: date,
) -> None:
    """Write a compact HTML report for browser review."""
    path.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(tz=ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")

    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidate_rows:
        if row.get("PATTERN_HIT"):
            by_ticker[str(row["TICKER"])].append(row)

    parts = [
        "<!doctype html>",
        "<html><head><meta charset=\"utf-8\">",
        "<title>FY Conservative Guidance Screener</title>",
        "<style>",
        "body{font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:24px;color:#1f2933}",
        "table{border-collapse:collapse;width:100%;font-size:13px;margin:12px 0 28px}",
        "th,td{border:1px solid #d9e2ec;padding:6px 8px;vertical-align:top}",
        "th{background:#eef2f7;text-align:left}",
        ".num{text-align:right;font-variant-numeric:tabular-nums}",
        ".muted{color:#627d98}",
        "details{margin:6px 0}",
        "summary{cursor:pointer;font-weight:600}",
        "</style></head><body>",
        "<h1>FY Conservative Guidance Screener</h1>",
        f"<p class=\"muted\">period={date_from} to {date_to} generated_at={generated_at}</p>",
        "<h2>Company Scores</h2>",
        "<table><thead><tr>",
    ]
    headers = [
        "Ticker",
        "Name",
        "Market",
        "Hits",
        "Hit Rate",
        "Avg Selloff 1D",
        "Avg Initial Growth",
        "Avg Actual vs Initial",
        "Latest Hit",
    ]
    parts.extend(f"<th>{html.escape(h)}</th>" for h in headers)
    parts.append("</tr></thead><tbody>")
    for row in company_rows[:100]:
        parts.append("<tr>")
        parts.append(f"<td>{html.escape(str(row['TICKER']))}</td>")
        parts.append(f"<td>{html.escape(str(row['STOCK_NAME']))}</td>")
        parts.append(f"<td>{html.escape(str(row['MARKET_CATEGORY']))}</td>")
        parts.append(f"<td class=\"num\">{row['HIT_COUNT']}</td>")
        parts.append(f"<td class=\"num\">{pct(row['HIT_RATE'])}</td>")
        parts.append(f"<td class=\"num\">{pct(row['AVG_RET_1D'])}</td>")
        parts.append(f"<td class=\"num\">{pct(row['AVG_INITIAL_GROWTH'])}</td>")
        parts.append(f"<td class=\"num\">{pct(row['AVG_ACTUAL_VS_INITIAL'])}</td>")
        parts.append(f"<td>{html.escape(str(row['LATEST_HIT_YEAR']))}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")

    parts.append("<h2>Hit Evidence</h2>")
    for row in company_rows[:100]:
        ticker = str(row["TICKER"])
        ticker_hits = sorted(
            by_ticker.get(ticker, []),
            key=lambda r: str(r["TARGET_FISCAL_YEAR_END_DATE"]),
            reverse=True,
        )
        parts.append(
            f"<details><summary>{html.escape(ticker)} "
            f"{html.escape(str(row['STOCK_NAME']))} "
            f"({row['HIT_COUNT']} hits)</summary>"
        )
        parts.append("<table><thead><tr>")
        for header in [
            "Target FY",
            "Initial Date",
            "Final Date",
            "Initial Growth",
            "Ret 1D",
            "Actual vs Initial",
            "Actual Growth",
            "Reason",
        ]:
            parts.append(f"<th>{html.escape(header)}</th>")
        parts.append("</tr></thead><tbody>")
        for hit in ticker_hits:
            parts.append("<tr>")
            parts.append(f"<td>{html.escape(str(hit['TARGET_FISCAL_YEAR_END_DATE']))}</td>")
            parts.append(f"<td>{html.escape(str(hit['INITIAL_DISCLOSED_DATE']))}</td>")
            parts.append(f"<td>{html.escape(str(hit.get('FINAL_DISCLOSED_DATE') or ''))}</td>")
            parts.append(f"<td class=\"num\">{pct(hit.get('INITIAL_GROWTH'))}</td>")
            parts.append(f"<td class=\"num\">{pct(hit.get('RET_1D'))}</td>")
            parts.append(f"<td class=\"num\">{pct(hit.get('ACTUAL_VS_INITIAL'))}</td>")
            parts.append(f"<td class=\"num\">{pct(hit.get('ACTUAL_GROWTH'))}</td>")
            parts.append(f"<td>{html.escape(str(hit.get('REASON') or ''))}</td>")
            parts.append("</tr>")
        parts.append("</tbody></table></details>")
    parts.append("</body></html>")

    path.write_text("\n".join(parts), encoding="utf-8")
    log.info("html_report_written", path=str(path), companies=len(company_rows))


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Screen repeat conservative FY guidance patterns."
    )
    parser.add_argument("--date-from", default="2018-01-01")
    parser.add_argument("--date-to", default=date.today().isoformat())
    parser.add_argument("--limit-tickers", type=int)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--weak-growth-max", type=float, default=0.05)
    parser.add_argument("--selloff-1d", type=float, default=-0.07)
    parser.add_argument("--selloff-3d", type=float, default=-0.10)
    parser.add_argument("--min-actual-vs-initial", type=float, default=0.10)
    parser.add_argument("--min-actual-growth", type=float, default=-0.05)
    parser.add_argument("--strong-actual-vs-initial", type=float, default=0.20)
    parser.add_argument("--strong-actual-growth", type=float, default=0.00)
    parser.add_argument("--positive-revision-min", type=float, default=0.10)
    parser.add_argument("--min-hit-count", type=int, default=2)
    parser.add_argument("--min-hit-rate", type=float, default=0.30)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the screener."""
    configure_logging()
    args = build_parser().parse_args(argv)
    thresholds = Thresholds(
        weak_growth_max=args.weak_growth_max,
        selloff_1d=args.selloff_1d,
        selloff_3d=args.selloff_3d,
        min_actual_vs_initial=args.min_actual_vs_initial,
        min_actual_growth=args.min_actual_growth,
        strong_actual_vs_initial=args.strong_actual_vs_initial,
        strong_actual_growth=args.strong_actual_growth,
        positive_revision_min=args.positive_revision_min,
        min_hit_count=args.min_hit_count,
        min_hit_rate=args.min_hit_rate,
    )
    date_from = parse_date(args.date_from)
    date_to = parse_date(args.date_to)

    try:
        client = get_bq_client()
        rows = fetch_candidate_years(
            client=client,
            date_from=date_from,
            date_to=date_to,
            limit_tickers=args.limit_tickers,
        )
        classified = [classify_row(row, thresholds) for row in rows]
        company_scores = summarize_companies(classified, thresholds)

        args.output_dir.mkdir(parents=True, exist_ok=True)
        candidate_path = args.output_dir / "candidate_years.csv"
        company_path = args.output_dir / "company_scores.csv"
        report_path = args.output_dir / "report.html"
        write_candidate_years(classified, candidate_path)
        write_company_scores(company_scores, company_path)
        write_html_report(classified, company_scores, report_path, date_from, date_to)
    except Exception:
        log.exception("screener_failed")
        return 1

    log.info(
        "screener_done",
        candidate_years=len(classified),
        companies=len(company_scores),
        pattern_hits=sum(1 for row in classified if row.get("PATTERN_HIT")),
        candidate_path=str(candidate_path),
        company_path=str(company_path),
        report_path=str(report_path),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

# -*- coding: utf-8 -*-
"""Phase 4-7 earnings actual backfill runner.

This script is a dedicated backfill copy of ``earnings_actual_load.py``.
It uses the same fin_summary extraction and row transform, but wraps the load
with backfill-only safeguards:

- dry-run by default; destructive writes require ``--execute``
- mandatory BQ backup on execute unless an existing backup table is supplied
- stage-table load followed by DELETE/INSERT in a BigQuery transaction
- per-chunk verification
- execution log append to the Phase 4 plan Markdown

Example:
  python scripts/earnings_actual_backfill.py --mode pilot
  python scripts/earnings_actual_backfill.py --mode pilot --execute
  python scripts/earnings_actual_backfill.py --mode all --execute
"""

import argparse
import io
import json as _json
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import structlog
from google.cloud import bigquery
from google.oauth2 import service_account

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import LogCapture, send_mail

JST = timezone(timedelta(hours=+9), "JST")

PROJECT_ID = "gmailpj-357912"
DATASET_ID = "STOCK"
TABLE_ID = "EARNINGS_DISCLOSURE_CALENDAR"
TABLE_FQN = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
PLAN_MD = Path("docs/plans/tools-101_earnings_actual_load_20260522_175452.md")

BACKFILL_FROM = date(2017, 1, 1)
BACKFILL_TO = date(2026, 5, 22)
PILOT_FROM = date(2017, 1, 1)
PILOT_TO = date(2017, 1, 31)
SAMPLE_TICKER = "2162"
SAMPLE_DATE = date(2026, 5, 11)

QUARTER_MAP: dict[str, str] = {
    "FY": "\u672c\u6c7a\u7b97",
    "1Q": "1Q",
    "2Q": "\u4e2d\u9593\u6c7a\u7b97",
    "3Q": "3Q",
    "4Q": "\u672c\u6c7a\u7b97",
    "5Q": "\u672c\u6c7a\u7b97",
}
QUARTER_UNKNOWN = "\u4e0d\u660e"

MARKET_CATEGORIES = (
    "\u30d7\u30e9\u30a4\u30e0\uff08\u5185\u56fd\u682a\u5f0f\uff09",
    "\u30b9\u30bf\u30f3\u30c0\u30fc\u30c9\uff08\u5185\u56fd\u682a\u5f0f\uff09",
    "\u30b0\u30ed\u30fc\u30b9\uff08\u5185\u56fd\u682a\u5f0f\uff09",
)
DELISTED_MARKET_SEGMENTS = (
    "\u30d7\u30e9\u30a4\u30e0",
    "\u30b9\u30bf\u30f3\u30c0\u30fc\u30c9",
    "\u30b0\u30ed\u30fc\u30b9",
    "\u6771\u8a3c\u30d7\u30e9\u30a4\u30e0",
    "\u6771\u8a3c\u30b9\u30bf\u30f3\u30c0\u30fc\u30c9",
    "\u6771\u8a3c\u30b0\u30ed\u30fc\u30b9",
    "\u7b2c\u4e00\u90e8",
    "\u7b2c\u4e8c\u90e8",
    "\u30de\u30b6\u30fc\u30ba",
    "JQ\u30b9\u30bf\u30f3\u30c0\u30fc\u30c9",
    "JQ\u30b0\u30ed\u30fc\u30b9",
)

REQUIRED_COLUMNS: tuple[str, ...] = (
    "TICKER",
    "FISCAL_YEAR_END",
    "QUARTER",
    "CATEGORY",
    "RECORD_TYPE",
    "REVISION_SEQ",
    "DISCLOSURE_DATE",
    "DISCLOSURE_TIME",
    "SOURCE",
    "LOADED_AT",
)

log = structlog.get_logger()


@dataclass(frozen=True)
class Chunk:
    """Backfill date chunk."""

    name: str
    date_from: date
    date_to: date


@dataclass(frozen=True)
class ChunkResult:
    """Backfill result for one chunk."""

    chunk: Chunk
    expected_rows: int
    target_rows_after: int
    inserted_rows: int
    duplicate_keys: int
    scheduled_before: int
    scheduled_after: int
    category_counts: dict[str, int]


def detect_runtime() -> str:
    """Return execution runtime label."""
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    return "local"


RUNTIME = detect_runtime()


def get_bq_client() -> bigquery.Client:
    """Build a BigQuery client for local or Cloud Run execution."""
    if RUNTIME == "local":
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from src.core.config import settings

        creds = service_account.Credentials.from_service_account_file(
            settings.google_application_credentials,
        )
        return bigquery.Client(project=PROJECT_ID, credentials=creds)
    return bigquery.Client(project=PROJECT_ID)


def parse_yyyymmdd(value: str) -> date:
    """Parse YYYYMMDD date string."""
    return datetime.strptime(value, "%Y%m%d").date()


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Phase 4-7 fin_summary backfill for EARNINGS_DISCLOSURE_CALENDAR",
    )
    parser.add_argument(
        "--mode",
        choices=("pilot", "all", "chunk"),
        default="pilot",
        help="Backfill scope. Default is the safe 201701 pilot month.",
    )
    parser.add_argument("--from", dest="date_from", help="Chunk start YYYYMMDD; mode=chunk only")
    parser.add_argument("--to", dest="date_to", help="Chunk end YYYYMMDD; mode=chunk only")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually write BQ. Without this flag the script only plans and verifies inputs.",
    )
    parser.add_argument(
        "--backup-table",
        default=None,
        help="Existing backup table name to record in the log. If omitted, --execute creates one.",
    )
    parser.add_argument(
        "--append-log",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Append execution result to the Phase 4 plan MD on --execute.",
    )
    parser.add_argument(
        "--plan-md",
        default=str(PLAN_MD),
        help="Plan MD path for execution log append.",
    )
    return parser.parse_args()


def build_chunks(args: argparse.Namespace) -> list[Chunk]:
    """Build backfill chunks from CLI args."""
    if args.mode == "pilot":
        return [Chunk("pilot_201701", PILOT_FROM, PILOT_TO)]

    if args.mode == "chunk":
        if not args.date_from or not args.date_to:
            raise ValueError("--mode chunk requires --from and --to")
        d_from = parse_yyyymmdd(args.date_from)
        d_to = parse_yyyymmdd(args.date_to)
        validate_backfill_range(d_from, d_to)
        return [Chunk(f"chunk_{args.date_from}_{args.date_to}", d_from, d_to)]

    return [
        Chunk("201701", date(2017, 1, 1), date(2017, 1, 31)),
        Chunk("201702_201712", date(2017, 2, 1), date(2017, 12, 31)),
        Chunk("2018", date(2018, 1, 1), date(2018, 12, 31)),
        Chunk("2019", date(2019, 1, 1), date(2019, 12, 31)),
        Chunk("2020", date(2020, 1, 1), date(2020, 12, 31)),
        Chunk("2021", date(2021, 1, 1), date(2021, 12, 31)),
        Chunk("2022", date(2022, 1, 1), date(2022, 12, 31)),
        Chunk("2023", date(2023, 1, 1), date(2023, 12, 31)),
        Chunk("2024", date(2024, 1, 1), date(2024, 12, 31)),
        Chunk("2025", date(2025, 1, 1), date(2025, 12, 31)),
        Chunk("202601_20260522", date(2026, 1, 1), BACKFILL_TO),
    ]


def validate_backfill_range(d_from: date, d_to: date) -> None:
    """Reject dates outside the approved Phase 4-7 backfill range."""
    if d_from > d_to:
        raise ValueError(f"Invalid range: {d_from} > {d_to}")
    if d_from < BACKFILL_FROM or d_to > BACKFILL_TO:
        raise ValueError(f"Range must stay within {BACKFILL_FROM} - {BACKFILL_TO}")


EXTRACT_SQL = """
WITH valid_tickers AS (
  SELECT TICKER
  FROM `{project}.STOCK.STOCK_CODE_LIST`
  WHERE EXCHANGE = 'TSE'
    AND MARKET_CATEGORY IN UNNEST(@market_categories)
  UNION DISTINCT
  SELECT TICKER
  FROM `{project}.STOCK.DELISTED_STOCKS`
  WHERE MARKET_SEGMENT IN UNNEST(@delisted_market_segments)
),
fin_base AS (
  SELECT
    f.LOCAL_CODE,
    f.DISCLOSED_DATE,
    f.DISCLOSED_TIME,
    f.DISCLOSURE_NUMBER,
    f.TYPE_OF_DOCUMENT,
    f.TYPE_OF_CURRENT_PERIOD,
    f.CURRENT_FISCAL_YEAR_END_DATE
  FROM `{project}.STOCK.fin_summary` f
  INNER JOIN valid_tickers vt
    ON f.LOCAL_CODE = vt.TICKER
  WHERE f.DISCLOSED_DATE BETWEEN @seq_from AND @seq_to
    AND (
      f.TYPE_OF_DOCUMENT LIKE '%FinancialStatements%'
      OR f.TYPE_OF_DOCUMENT IN ('EarnForecastRevision', 'REITEarnForecastRevision')
    )
),
result_docs AS (
  SELECT
    LOCAL_CODE,
    DISCLOSED_DATE,
    DISCLOSED_TIME,
    TYPE_OF_CURRENT_PERIOD,
    CURRENT_FISCAL_YEAR_END_DATE,
    'R' AS CATEGORY,
    1 AS REVISION_SEQ
  FROM fin_base
  WHERE TYPE_OF_DOCUMENT LIKE '%FinancialStatements%'
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY LOCAL_CODE, CURRENT_FISCAL_YEAR_END_DATE, TYPE_OF_CURRENT_PERIOD
    ORDER BY
      DISCLOSED_DATE DESC,
      DISCLOSURE_NUMBER DESC,
      CASE WHEN TYPE_OF_DOCUMENT LIKE '%Consolidated%' THEN 0 ELSE 1 END
  ) = 1
),
forecast_docs AS (
  SELECT
    LOCAL_CODE,
    DISCLOSED_DATE,
    DISCLOSED_TIME,
    TYPE_OF_CURRENT_PERIOD,
    CURRENT_FISCAL_YEAR_END_DATE,
    'F' AS CATEGORY,
    ROW_NUMBER() OVER (
      PARTITION BY LOCAL_CODE, CURRENT_FISCAL_YEAR_END_DATE, TYPE_OF_CURRENT_PERIOD
      ORDER BY DISCLOSURE_NUMBER ASC
    ) AS REVISION_SEQ
  FROM fin_base
  WHERE TYPE_OF_DOCUMENT IN ('EarnForecastRevision', 'REITEarnForecastRevision')
),
all_docs AS (
  SELECT
    LOCAL_CODE AS TICKER,
    DISCLOSED_DATE,
    DISCLOSED_TIME,
    TYPE_OF_CURRENT_PERIOD,
    CURRENT_FISCAL_YEAR_END_DATE,
    CATEGORY,
    REVISION_SEQ
  FROM result_docs
  UNION ALL
  SELECT
    LOCAL_CODE AS TICKER,
    DISCLOSED_DATE,
    DISCLOSED_TIME,
    TYPE_OF_CURRENT_PERIOD,
    CURRENT_FISCAL_YEAR_END_DATE,
    CATEGORY,
    REVISION_SEQ
  FROM forecast_docs
)
SELECT
  TICKER,
  DISCLOSED_DATE,
  DISCLOSED_TIME,
  TYPE_OF_CURRENT_PERIOD,
  CURRENT_FISCAL_YEAR_END_DATE,
  CATEGORY,
  REVISION_SEQ
FROM all_docs
WHERE DISCLOSED_DATE BETWEEN @date_from AND @date_to
ORDER BY DISCLOSED_DATE, TICKER, CATEGORY, REVISION_SEQ
""".format(project=PROJECT_ID)


def date_query_config(d_from: date, d_to: date) -> bigquery.QueryJobConfig:
    """Return date range query parameters."""
    return bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("date_from", "DATE", str(d_from)),
            bigquery.ScalarQueryParameter("date_to", "DATE", str(d_to)),
        ],
    )


def extract_query_config(d_from: date, d_to: date) -> bigquery.QueryJobConfig:
    """Return fin_summary extraction query parameters."""
    return bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("date_from", "DATE", str(d_from)),
            bigquery.ScalarQueryParameter("date_to", "DATE", str(d_to)),
            bigquery.ScalarQueryParameter("seq_from", "DATE", str(BACKFILL_FROM)),
            bigquery.ScalarQueryParameter("seq_to", "DATE", str(BACKFILL_TO)),
            bigquery.ArrayQueryParameter("market_categories", "STRING", list(MARKET_CATEGORIES)),
            bigquery.ArrayQueryParameter("delisted_market_segments", "STRING", list(DELISTED_MARKET_SEGMENTS)),
        ],
    )


def fetch_source(client: bigquery.Client, d_from: date, d_to: date) -> pd.DataFrame:
    """Fetch fin_summary source rows for one chunk."""
    df = client.query(EXTRACT_SQL, job_config=extract_query_config(d_from, d_to)).to_dataframe()
    log.info("fetch_done", row_cnt=len(df), date_from=str(d_from), date_to=str(d_to))
    return df


def transform(df: pd.DataFrame) -> pd.DataFrame:
    """Transform source rows to EARNINGS_DISCLOSURE_CALENDAR schema."""
    if df.empty:
        return pd.DataFrame(columns=list(REQUIRED_COLUMNS))

    records: list[dict] = []
    now_jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    for _, row in df.iterrows():
        category = row.get("CATEGORY")
        if category not in {"R", "F"}:
            continue

        quarter = QUARTER_UNKNOWN
        if pd.notna(row.get("TYPE_OF_CURRENT_PERIOD")):
            quarter = QUARTER_MAP.get(row["TYPE_OF_CURRENT_PERIOD"], QUARTER_UNKNOWN)

        fiscal_year_end = None
        if pd.notna(row.get("CURRENT_FISCAL_YEAR_END_DATE")):
            fiscal_year_end = str(row["CURRENT_FISCAL_YEAR_END_DATE"])

        disclosure_time = None
        if pd.notna(row.get("DISCLOSED_TIME")):
            t = row["DISCLOSED_TIME"]
            disclosure_time = t.strftime("%H:%M:%S") if hasattr(t, "strftime") else str(t)

        records.append({
            "TICKER": str(row["TICKER"]),
            "FISCAL_YEAR_END": fiscal_year_end,
            "QUARTER": quarter,
            "CATEGORY": category,
            "RECORD_TYPE": "A",
            "REVISION_SEQ": int(row["REVISION_SEQ"]),
            "DISCLOSURE_DATE": str(row["DISCLOSED_DATE"]),
            "DISCLOSURE_TIME": disclosure_time,
            "SOURCE": "jquants",
            "LOADED_AT": now_jst,
        })

    out = pd.DataFrame(records, columns=list(REQUIRED_COLUMNS))
    if not out.empty:
        out["REVISION_SEQ"] = out["REVISION_SEQ"].astype(int)
    log.info("transform_done", row_cnt=len(out))
    return out


def schema_fields() -> list[bigquery.SchemaField]:
    """Return table schema used by stage load."""
    return [
        bigquery.SchemaField("TICKER", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("FISCAL_YEAR_END", "DATE"),
        bigquery.SchemaField("QUARTER", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("CATEGORY", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("RECORD_TYPE", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("REVISION_SEQ", "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("DISCLOSURE_DATE", "DATE"),
        bigquery.SchemaField("DISCLOSURE_TIME", "TIME"),
        bigquery.SchemaField("SOURCE", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("LOADED_AT", "DATETIME", mode="REQUIRED"),
    ]


def count_scheduled_rows(client: bigquery.Client) -> int:
    """Count scheduled rows that must remain unchanged."""
    sql = f"SELECT COUNT(*) AS cnt FROM `{TABLE_FQN}` WHERE RECORD_TYPE = 'S'"
    rows = list(client.query(sql).result())
    return int(rows[0]["cnt"])


def validate_schema(client: bigquery.Client) -> None:
    """Validate target schema exactly matches the Phase 4 post-DROP columns."""
    table = client.get_table(TABLE_FQN)
    actual = [field.name for field in table.schema]
    if actual != list(REQUIRED_COLUMNS):
        raise RuntimeError(f"Unexpected {TABLE_FQN} schema: {actual}")


def create_backup(client: bigquery.Client, run_id: str) -> str:
    """Create a full target table backup and return its table name."""
    backup_table = f"{TABLE_ID}_BAK_{run_id}"
    sql = f"""
    CREATE OR REPLACE TABLE `{PROJECT_ID}.{DATASET_ID}.{backup_table}` AS
    SELECT * FROM `{TABLE_FQN}`
    """
    client.query(sql).result()
    log.info("backup_created", backup_table=backup_table)
    return backup_table


def table_ref_from_name(table_name: str) -> str:
    """Return a fully qualified table reference from a table id or FQN."""
    if table_name.count(".") == 2:
        return table_name
    return f"{PROJECT_ID}.{DATASET_ID}.{table_name}"


def validate_backup_table(client: bigquery.Client, backup_table: str) -> None:
    """Verify a user-supplied backup table exists and has the target schema."""
    table = client.get_table(table_ref_from_name(backup_table))
    actual = [field.name for field in table.schema]
    if actual != list(REQUIRED_COLUMNS):
        raise RuntimeError(f"Backup table schema mismatch: {backup_table} -> {actual}")
    log.info("backup_verified", backup_table=backup_table, row_count=table.num_rows)


def restore_from_backup(client: bigquery.Client, backup_table: str) -> None:
    """Restore the target table from a verified backup table."""
    validate_backup_table(client, backup_table)
    sql = f"""
    BEGIN TRANSACTION;

    DELETE FROM `{TABLE_FQN}`
    WHERE TRUE;

    INSERT INTO `{TABLE_FQN}` (
      TICKER,
      FISCAL_YEAR_END,
      QUARTER,
      CATEGORY,
      RECORD_TYPE,
      REVISION_SEQ,
      DISCLOSURE_DATE,
      DISCLOSURE_TIME,
      SOURCE,
      LOADED_AT
    )
    SELECT
      TICKER,
      FISCAL_YEAR_END,
      QUARTER,
      CATEGORY,
      RECORD_TYPE,
      REVISION_SEQ,
      DISCLOSURE_DATE,
      DISCLOSURE_TIME,
      SOURCE,
      LOADED_AT
    FROM `{table_ref_from_name(backup_table)}`;

    COMMIT TRANSACTION;
    """
    client.query(sql).result()
    log.error("target_restored_from_backup", backup_table=backup_table)


def load_stage_table(client: bigquery.Client, df: pd.DataFrame, stage_table: str) -> int:
    """Load transformed rows to a stage table."""
    ndjson_lines = []
    for _, row in df.iterrows():
        rec = {k: (None if pd.isna(v) else v) for k, v in row.to_dict().items()}
        ndjson_lines.append(_json.dumps(rec, ensure_ascii=False))
    ndjson_bytes = ("\n".join(ndjson_lines)).encode("utf-8")

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        schema=schema_fields(),
    )
    job = client.load_table_from_file(
        io.BytesIO(ndjson_bytes),
        f"{PROJECT_ID}.{DATASET_ID}.{stage_table}",
        job_config=job_config,
    )
    job.result()
    log.info("stage_load_done", stage_table=stage_table, row_cnt=len(df))
    return len(df)


def merge_stage_chunk(
    client: bigquery.Client,
    chunk: Chunk,
    stage_table: str,
) -> tuple[int, int]:
    """Replace one target chunk from the stage table inside a BQ transaction."""
    sql = f"""
    BEGIN TRANSACTION;

    DELETE FROM `{TABLE_FQN}`
    WHERE RECORD_TYPE = 'A'
      AND DISCLOSURE_DATE BETWEEN @date_from AND @date_to;

    INSERT INTO `{TABLE_FQN}` (
      TICKER,
      FISCAL_YEAR_END,
      QUARTER,
      CATEGORY,
      RECORD_TYPE,
      REVISION_SEQ,
      DISCLOSURE_DATE,
      DISCLOSURE_TIME,
      SOURCE,
      LOADED_AT
    )
    SELECT
      TICKER,
      FISCAL_YEAR_END,
      QUARTER,
      CATEGORY,
      RECORD_TYPE,
      REVISION_SEQ,
      DISCLOSURE_DATE,
      DISCLOSURE_TIME,
      SOURCE,
      LOADED_AT
    FROM `{PROJECT_ID}.{DATASET_ID}.{stage_table}`;

    COMMIT TRANSACTION;
    """
    job = client.query(sql, job_config=date_query_config(chunk.date_from, chunk.date_to))
    job.result()
    deleted = count_a_rows(client, chunk.date_from, chunk.date_to)
    inserted = count_stage_rows(client, stage_table)
    log.info("merge_done", chunk=chunk.name, target_rows_after=deleted, stage_rows=inserted)
    return deleted, inserted


def drop_stage_table(client: bigquery.Client, stage_table: str) -> None:
    """Drop stage table if it exists."""
    client.delete_table(f"{PROJECT_ID}.{DATASET_ID}.{stage_table}", not_found_ok=True)
    log.info("stage_dropped", stage_table=stage_table)


def count_stage_rows(client: bigquery.Client, stage_table: str) -> int:
    """Count rows in a stage table."""
    sql = f"SELECT COUNT(*) AS cnt FROM `{PROJECT_ID}.{DATASET_ID}.{stage_table}`"
    rows = list(client.query(sql).result())
    return int(rows[0]["cnt"])


def count_a_rows(client: bigquery.Client, d_from: date, d_to: date) -> int:
    """Count actual target rows in a date range."""
    sql = f"""
    SELECT COUNT(*) AS cnt
    FROM `{TABLE_FQN}`
    WHERE RECORD_TYPE = 'A'
      AND DISCLOSURE_DATE BETWEEN @date_from AND @date_to
    """
    rows = list(client.query(sql, job_config=date_query_config(d_from, d_to)).result())
    return int(rows[0]["cnt"])


def duplicate_key_count(client: bigquery.Client, d_from: date, d_to: date) -> int:
    """Count logical primary-key duplicates in a date range."""
    sql = f"""
    SELECT COUNT(*) AS duplicate_key_count
    FROM (
      SELECT
        TICKER,
        FISCAL_YEAR_END,
        QUARTER,
        CATEGORY,
        RECORD_TYPE,
        REVISION_SEQ,
        COUNT(*) AS cnt
      FROM `{TABLE_FQN}`
      WHERE RECORD_TYPE = 'A'
        AND DISCLOSURE_DATE BETWEEN @date_from AND @date_to
      GROUP BY 1,2,3,4,5,6
      HAVING cnt > 1
    )
    """
    rows = list(client.query(sql, job_config=date_query_config(d_from, d_to)).result())
    return int(rows[0]["duplicate_key_count"])


def duplicate_inserted_key_count(client: bigquery.Client, stage_table: str) -> int:
    """Count full-table duplicate logical keys for keys present in the stage table."""
    sql = f"""
    WITH stage_keys AS (
      SELECT DISTINCT
        TICKER,
        FISCAL_YEAR_END,
        QUARTER,
        CATEGORY,
        RECORD_TYPE,
        REVISION_SEQ
      FROM `{PROJECT_ID}.{DATASET_ID}.{stage_table}`
    ),
    target_key_counts AS (
      SELECT
        tgt.TICKER,
        tgt.FISCAL_YEAR_END,
        tgt.QUARTER,
        tgt.CATEGORY,
        tgt.RECORD_TYPE,
        tgt.REVISION_SEQ,
        COUNT(*) AS cnt
      FROM `{TABLE_FQN}` AS tgt
      INNER JOIN stage_keys AS sk
        ON tgt.TICKER = sk.TICKER
       AND COALESCE(tgt.FISCAL_YEAR_END, DATE '0001-01-01') = COALESCE(sk.FISCAL_YEAR_END, DATE '0001-01-01')
       AND tgt.QUARTER = sk.QUARTER
       AND tgt.CATEGORY = sk.CATEGORY
       AND tgt.RECORD_TYPE = sk.RECORD_TYPE
       AND tgt.REVISION_SEQ = sk.REVISION_SEQ
      WHERE tgt.RECORD_TYPE = 'A'
      GROUP BY 1,2,3,4,5,6
      HAVING cnt > 1
    )
    SELECT COUNT(*) AS duplicate_key_count
    FROM target_key_counts
    """
    rows = list(client.query(sql).result())
    return int(rows[0]["duplicate_key_count"])


def category_counts(df: pd.DataFrame) -> dict[str, int]:
    """Return category counts for transformed rows."""
    if df.empty:
        return {}
    counts = df.groupby("CATEGORY").size().to_dict()
    return {str(k): int(v) for k, v in counts.items()}


def validate_transformed_rows(df: pd.DataFrame, chunk: Chunk) -> None:
    """Validate transformed rows before any target-table mutation."""
    if list(df.columns) != list(REQUIRED_COLUMNS):
        raise RuntimeError(f"Unexpected transformed columns: {list(df.columns)}")

    dates = pd.to_datetime(df["DISCLOSURE_DATE"]).dt.date
    if ((dates < chunk.date_from) | (dates > chunk.date_to)).any():
        raise RuntimeError(f"Transformed rows contain dates outside {chunk.name}")

    if (df["RECORD_TYPE"] != "A").any():
        raise RuntimeError(f"Transformed rows contain non-A records in {chunk.name}")

    key_cols = [
        "TICKER",
        "FISCAL_YEAR_END",
        "QUARTER",
        "CATEGORY",
        "RECORD_TYPE",
        "REVISION_SEQ",
    ]
    duplicate_rows = df.duplicated(subset=key_cols, keep=False)
    if duplicate_rows.any():
        sample = df.loc[duplicate_rows, key_cols].head(5).to_dict("records")
        raise RuntimeError(f"Duplicate transformed keys in {chunk.name}: {sample}")

    if chunk.date_from <= SAMPLE_DATE <= chunk.date_to:
        sample_df = df[
            (df["TICKER"] == SAMPLE_TICKER)
            & (df["DISCLOSURE_DATE"] == str(SAMPLE_DATE))
        ]
        if len(sample_df) != 1:
            raise RuntimeError(
                f"Expected one transformed {SAMPLE_TICKER}/{SAMPLE_DATE} row, got {len(sample_df)}",
            )
        row = sample_df.iloc[0]
        if row["CATEGORY"] != "R" or row["QUARTER"] != "3Q" or row["REVISION_SEQ"] != 1:
            raise RuntimeError(f"Unexpected transformed sample row: {row.to_dict()}")


def fetch_sample_rows(client: bigquery.Client) -> list[dict]:
    """Fetch the 2162/2026-05-11 verification sample."""
    sql = f"""
    SELECT
      TICKER,
      DISCLOSURE_DATE,
      DISCLOSURE_TIME,
      QUARTER,
      FISCAL_YEAR_END,
      CATEGORY,
      RECORD_TYPE,
      REVISION_SEQ,
      SOURCE
    FROM `{TABLE_FQN}`
    WHERE RECORD_TYPE = 'A'
      AND TICKER = @ticker
      AND DISCLOSURE_DATE = @sample_date
    ORDER BY CATEGORY, REVISION_SEQ
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("ticker", "STRING", SAMPLE_TICKER),
            bigquery.ScalarQueryParameter("sample_date", "DATE", str(SAMPLE_DATE)),
        ],
    )
    return [dict(row.items()) for row in client.query(sql, job_config=job_config).result()]


def verify_sample_rows(client: bigquery.Client) -> None:
    """Verify the known 2162 sample after a range including it."""
    rows = fetch_sample_rows(client)
    if len(rows) != 1:
        raise RuntimeError(f"Expected one {SAMPLE_TICKER}/{SAMPLE_DATE} row, got {len(rows)}")
    row = rows[0]
    if row["CATEGORY"] != "R" or row["QUARTER"] != "3Q" or row["REVISION_SEQ"] != 1:
        raise RuntimeError(f"Unexpected sample row: {row}")


def run_chunk(
    client: bigquery.Client,
    chunk: Chunk,
    run_id: str,
    execute: bool,
    backup_table: str,
) -> ChunkResult:
    """Run or dry-run one backfill chunk."""
    validate_backfill_range(chunk.date_from, chunk.date_to)
    scheduled_before = count_scheduled_rows(client)
    src = fetch_source(client, chunk.date_from, chunk.date_to)
    out = transform(src)
    counts = category_counts(out)

    if not execute:
        log.info(
            "dry_run_chunk",
            chunk=chunk.name,
            date_from=str(chunk.date_from),
            date_to=str(chunk.date_to),
            expected_rows=len(out),
            category_counts=counts,
            scheduled_rows=scheduled_before,
        )
        return ChunkResult(
            chunk=chunk,
            expected_rows=len(out),
            target_rows_after=0,
            inserted_rows=0,
            duplicate_keys=0,
            scheduled_before=scheduled_before,
            scheduled_after=scheduled_before,
            category_counts=counts,
        )

    if out.empty:
        raise RuntimeError(f"Refusing to execute empty chunk: {chunk}")

    validate_transformed_rows(out, chunk)

    stage_table = f"{TABLE_ID}_BF_STAGE_{run_id}_{chunk.name}".upper()
    merged = False
    try:
        load_stage_table(client, out, stage_table)
        target_rows_after, stage_rows = merge_stage_chunk(client, chunk, stage_table)
        merged = True
        scheduled_after = count_scheduled_rows(client)
        dupes = duplicate_inserted_key_count(client, stage_table)

        if scheduled_before != scheduled_after:
            raise RuntimeError(f"S rows changed: before={scheduled_before}, after={scheduled_after}")
        if stage_rows != len(out) or target_rows_after != len(out):
            raise RuntimeError(
                f"Row count mismatch for {chunk.name}: expected={len(out)}, "
                f"stage={stage_rows}, target={target_rows_after}",
            )
        if dupes:
            raise RuntimeError(f"Duplicate logical keys detected in {chunk.name}: {dupes}")
        if chunk.date_from <= SAMPLE_DATE <= chunk.date_to:
            verify_sample_rows(client)
    except Exception:
        if merged and backup_table:
            restore_from_backup(client, backup_table)
        raise
    finally:
        drop_stage_table(client, stage_table)

    return ChunkResult(
        chunk=chunk,
        expected_rows=len(out),
        target_rows_after=target_rows_after,
        inserted_rows=stage_rows,
        duplicate_keys=dupes,
        scheduled_before=scheduled_before,
        scheduled_after=scheduled_after,
        category_counts=counts,
    )


def append_plan_log(plan_md: Path, run_id: str, backup_table: str, results: list[ChunkResult]) -> None:
    """Append a backfill execution log to the Phase 4 plan Markdown."""
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    lines = [
        "",
        "---",
        "",
        f"## Phase 4-7 実行ログ: {now}",
        "",
        f"- run_id: `{run_id}`",
        f"- backup_table: `{backup_table}`",
        "- status: completed",
        "",
        "| chunk | from | to | expected | inserted | duplicate_keys | category_counts |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for result in results:
        counts = ", ".join(f"{k}={v}" for k, v in sorted(result.category_counts.items()))
        lines.append(
            f"| {result.chunk.name} | {result.chunk.date_from} | {result.chunk.date_to} | "
            f"{result.expected_rows} | {result.inserted_rows} | {result.duplicate_keys} | {counts} |",
        )
    plan_md.write_text(plan_md.read_text(encoding="utf-8") + "\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    """Run Phase 4-7 backfill orchestration."""
    args = parse_args()
    chunks = build_chunks(args)
    run_id = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    backup_table = args.backup_table or ""

    log_cap = LogCapture()
    log_cap.start()
    try:
        client = get_bq_client()
        validate_schema(client)

        log.info(
            "earnings_actual_backfill_start",
            run_id=run_id,
            execute=args.execute,
            mode=args.mode,
            chunks=[chunk.name for chunk in chunks],
        )

        if args.execute and not backup_table:
            backup_table = create_backup(client, run_id)
        elif args.execute:
            validate_backup_table(client, backup_table)

        results: list[ChunkResult] = []
        for chunk in chunks:
            results.append(run_chunk(client, chunk, run_id, args.execute, backup_table))

        if args.execute and args.append_log:
            append_plan_log(Path(args.plan_md), run_id, backup_table, results)

        total_expected = sum(result.expected_rows for result in results)
        total_inserted = sum(result.inserted_rows for result in results)
        log.info(
            "earnings_actual_backfill_done",
            run_id=run_id,
            execute=args.execute,
            total_expected=total_expected,
            total_inserted=total_inserted,
            backup_table=backup_table or None,
            errors=0,
        )

    except Exception:
        log.error("fatal", exc=traceback.format_exc())
        send_mail(
            subject="[earnings_actual_backfill] ERROR",
            body=traceback.format_exc(),
        )
        raise
    finally:
        log_cap.stop()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""fin_summary → EARNINGS_DISCLOSURE_CALENDAR 実績ロード

実行環境: Windows ローカル / Cloud Run Job
ソース  : BQ STOCK.fin_summary
ロード先: BQ STOCK.EARNINGS_DISCLOSURE_CALENDAR (RECORD_TYPE='A')

引数:
  --from YYYYMMDD  開始日
  --to   YYYYMMDD  終了日（省略時は --from と同日）
  --week           過去1週間分をロード（--from/--to より優先）

方式: 対象期間の A レコードを DELETE → 再INSERT（冪等）
"""

import argparse
import io
import json as _json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone, date

import pandas as pd
import structlog
from google.cloud import bigquery
from google.oauth2 import service_account

# ── パスを通す ──────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import send_mail, LogCapture

# ── 定数 ────────────────────────────────────────────
JST = timezone(timedelta(hours=+9), "JST")

PROJECT_ID = "gmailpj-357912"
DATASET_ID = "STOCK"
TABLE_ID = "EARNINGS_DISCLOSURE_CALENDAR"
TABLE_FQN = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

# QUARTER マッピング（fin_summary TYPE_OF_CURRENT_PERIOD → 本テーブル）
QUARTER_MAP: dict[str, str] = {
    "FY": "本決算",
    "1Q": "1Q",
    "2Q": "中間決算",
    "3Q": "3Q",
    "4Q": "本決算",
    "5Q": "本決算",
}

log = structlog.get_logger()


# ============================================================
# 実行環境の自動判別
# ============================================================

def detect_runtime() -> str:
    """実行環境を自動判別する."""
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    return "local"


RUNTIME: str = detect_runtime()


# ============================================================
# BigQuery クライアント
# ============================================================

def get_bq_client() -> bigquery.Client:
    """実行環境に応じた BigQuery クライアントを返す."""
    if RUNTIME == "local":
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from src.core.config import settings
        creds = service_account.Credentials.from_service_account_file(
            settings.google_application_credentials,
        )
        return bigquery.Client(project=PROJECT_ID, credentials=creds)
    else:  # cloudrun: ADC
        return bigquery.Client(project=PROJECT_ID)


# ============================================================
# 引数パース
# ============================================================

def parse_args() -> argparse.Namespace:
    """引数をパースする."""
    parser = argparse.ArgumentParser(description="fin_summary実績 → EARNINGS_DISCLOSURE_CALENDAR ロード")
    parser.add_argument("--from", dest="date_from", default=None,
                        help="開始日 YYYYMMDD")
    parser.add_argument("--to", dest="date_to", default=None,
                        help="終了日 YYYYMMDD（省略時は --from と同日）")
    parser.add_argument("--week", action="store_true",
                        help="過去1週間分をロード（--from/--to より優先）")
    return parser.parse_args()


def resolve_dates(args: argparse.Namespace) -> tuple[date, date]:
    """引数から (date_from, date_to) を解決する."""
    today = datetime.now(JST).date()

    if args.week:
        return today - timedelta(days=7), today

    if args.date_from is None:
        # デフォルト: 今日
        return today, today

    d_from = datetime.strptime(args.date_from, "%Y%m%d").date()
    d_to = (datetime.strptime(args.date_to, "%Y%m%d").date()
            if args.date_to else d_from)
    return d_from, d_to


# ============================================================
# 1. BQ からソースデータ取得
# ============================================================

EXTRACT_SQL = """
WITH valid_tickers AS (
  SELECT TICKER
  FROM `{project}.STOCK.STOCK_CODE_LIST`
  WHERE EXCHANGE = 'TSE'
    AND MARKET_CATEGORY IN (
      'プライム（内国株式）',
      'スタンダード（内国株式）',
      'グロース（内国株式）'
    )
  UNION DISTINCT
  SELECT TICKER
  FROM `{project}.STOCK.DELISTED_STOCKS`
  WHERE MARKET_SEGMENT IN (
    'プライム',
    'スタンダード',
    'グロース',
    '東証プライム',
    '東証スタンダード',
    '東証グロース',
    '第一部',
    '第二部',
    'マザーズ',
    'JQスタンダード',
    'JQグロース'
  )
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
  WHERE f.DISCLOSED_DATE BETWEEN @date_from AND @date_to
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
    PARTITION BY LOCAL_CODE, DISCLOSED_DATE, CURRENT_FISCAL_YEAR_END_DATE
    ORDER BY
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
)
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
ORDER BY DISCLOSED_DATE, TICKER, CATEGORY, REVISION_SEQ
""".format(project=PROJECT_ID)


def fetch_source(client: bigquery.Client, d_from: date, d_to: date) -> pd.DataFrame:
    """BQ からソースデータを取得する."""
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("date_from", "DATE", str(d_from)),
            bigquery.ScalarQueryParameter("date_to", "DATE", str(d_to)),
        ],
    )
    df = client.query(EXTRACT_SQL, job_config=job_config).to_dataframe()
    log.info("fetch_done", row_cnt=len(df), date_from=str(d_from), date_to=str(d_to))
    return df


# ============================================================
# 2. データ変換
# ============================================================

def transform(df: pd.DataFrame) -> pd.DataFrame:
    """ソースデータを EARNINGS_DISCLOSURE_CALENDAR 形式に変換する."""
    if df.empty:
        return pd.DataFrame()

    records: list[dict] = []
    now_jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    for _, row in df.iterrows():
        category = row.get("CATEGORY")
        if category not in {"R", "F"}:
            continue

        # QUARTER マッピング（NOT NULL 制約あり。不明時は "不明"）
        quarter = "不明"
        if pd.notna(row.get("TYPE_OF_CURRENT_PERIOD")):
            quarter = QUARTER_MAP.get(row["TYPE_OF_CURRENT_PERIOD"], "不明")

        # FISCAL_YEAR_END
        fiscal_year_end = None
        if pd.notna(row.get("CURRENT_FISCAL_YEAR_END_DATE")):
            fiscal_year_end = str(row["CURRENT_FISCAL_YEAR_END_DATE"])

        # DISCLOSED_TIME
        disclosure_time = None
        if pd.notna(row.get("DISCLOSED_TIME")):
            t = row["DISCLOSED_TIME"]
            if hasattr(t, "strftime"):
                disclosure_time = t.strftime("%H:%M:%S")
            else:
                disclosure_time = str(t)

        records.append({
            "TICKER": row["TICKER"],
            "FISCAL_YEAR_END": fiscal_year_end,
            "QUARTER": quarter,
            "CATEGORY": category,
            "RECORD_TYPE": "A",
            "REVISION_SEQ": int(row["REVISION_SEQ"]),
            "DISCLOSURE_DATE": str(row["DISCLOSED_DATE"]),
            "DISCLOSURE_TIME": disclosure_time,
            # Phase 4移行中は既存BQスキーマ互換のためNULL固定。DDL DROPは別作業。
            "DISCLOSURE_NUMBER": None,
            "TYPE_OF_DOCUMENT": None,
            "DOC_TITLE": None,
            "SOURCE": "jquants",
            "LOADED_AT": now_jst,
        })

    out = pd.DataFrame(records)

    if out.empty:
        return out

    out["REVISION_SEQ"] = out["REVISION_SEQ"].astype(int)

    log.info("transform_done", row_cnt=len(out))
    return out


# ============================================================
# 3. BQ ロード
# ============================================================

def delete_actual_range(client: bigquery.Client, d_from: date, d_to: date) -> int:
    """対象期間の A レコードを削除する."""
    sql = f"""
    DELETE FROM `{TABLE_FQN}`
    WHERE RECORD_TYPE = 'A'
      AND DISCLOSURE_DATE BETWEEN @date_from AND @date_to
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("date_from", "DATE", str(d_from)),
            bigquery.ScalarQueryParameter("date_to", "DATE", str(d_to)),
        ],
    )
    result = client.query(sql, job_config=job_config).result()
    deleted = result.num_dml_affected_rows or 0
    log.info("delete_done", deleted=deleted, date_from=str(d_from), date_to=str(d_to))
    return deleted


def insert_rows(client: bigquery.Client, df: pd.DataFrame) -> int:
    """変換済みデータを BQ に INSERT する."""
    if df.empty:
        log.info("no_rows_to_insert")
        return 0

    ndjson_lines = []
    for _, row in df.iterrows():
        rec = {k: (None if pd.isna(v) else v) for k, v in row.to_dict().items()}
        ndjson_lines.append(_json.dumps(rec, ensure_ascii=False))
    ndjson_bytes = ("\n".join(ndjson_lines)).encode("utf-8")

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        schema=[
            bigquery.SchemaField("TICKER", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("FISCAL_YEAR_END", "DATE"),
            bigquery.SchemaField("QUARTER", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("CATEGORY", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("RECORD_TYPE", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("REVISION_SEQ", "INTEGER", mode="REQUIRED"),
            bigquery.SchemaField("DISCLOSURE_DATE", "DATE"),
            bigquery.SchemaField("DISCLOSURE_TIME", "TIME"),
            bigquery.SchemaField("DISCLOSURE_NUMBER", "STRING"),
            bigquery.SchemaField("TYPE_OF_DOCUMENT", "STRING"),
            bigquery.SchemaField("DOC_TITLE", "STRING"),
            bigquery.SchemaField("SOURCE", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("LOADED_AT", "DATETIME", mode="REQUIRED"),
        ],
    )

    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
    job = client.load_table_from_file(
        io.BytesIO(ndjson_bytes), table_ref, job_config=job_config,
    )
    job.result()

    log.info("insert_done", row_cnt=len(df))
    return len(df)


# ============================================================
# main
# ============================================================

def main() -> None:
    """メイン処理."""
    args = parse_args()
    d_from, d_to = resolve_dates(args)

    log.info("=== earnings_actual_load START ===",
             date_from=str(d_from), date_to=str(d_to), mode="week" if args.week else "range")

    log_cap = LogCapture()
    log_cap.start()
    try:
        client = get_bq_client()

        # 1. ソース取得
        src = fetch_source(client, d_from, d_to)
        if src.empty:
            log.info("no_source_data")
            return

        # 2. 変換
        out = transform(src)
        if out.empty:
            log.info("no_records_after_transform")
            return

        # 3. DELETE → INSERT
        delete_actual_range(client, d_from, d_to)
        inserted = insert_rows(client, out)

        log.info("=== earnings_actual_load DONE ===", inserted=inserted)

    except Exception:
        log.error("fatal", exc=traceback.format_exc())
        send_mail(
            subject="[earnings_actual_load] ERROR",
            body=traceback.format_exc(),
        )
        raise
    finally:
        log_cap.stop()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""TDNET_DOCUMENTS_ENHANCED → EARNINGS_DISCLOSURE_CALENDAR 実績ロード

実行環境: Windows ローカル / Cloud Run Job
ソース  : BQ STOCK.TDNET_DOCUMENTS_ENHANCED (MAIN_CATEGORY IN ('決算短信','業績予想'))
ロード先: BQ STOCK.EARNINGS_DISCLOSURE_CALENDAR (RECORD_TYPE='A')
補完    : fin_summary と JOIN → DISCLOSED_TIME, QUARTER, FISCAL_YEAR_END, TYPE_OF_DOCUMENT

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

# MAIN_CATEGORY → CATEGORY マッピング
CATEGORY_MAP: dict[str, str] = {
    "決算短信": "R",
    "業績予想": "F",
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
    parser = argparse.ArgumentParser(
        description="TDNET実績 → EARNINGS_DISCLOSURE_CALENDAR ロード",
    )
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
WITH tdnet_docs AS (
  -- TDNET_DOCUMENTS_ENHANCED から決算短信・業績予想を抽出（重複チャンク除去）
  -- 訂正・差替え・お知らせを除外（最初の実績のみ保持）
  SELECT DISTINCT
    TICKER,
    SUBMISSION_DATE,
    MAIN_CATEGORY,
    DOC_TITLE,
    DOC_ID
  FROM `{project}.STOCK.TDNET_DOCUMENTS_ENHANCED`
  WHERE MAIN_CATEGORY IN ('決算短信', '業績予想')
    AND SUBMISSION_DATE BETWEEN @date_from AND @date_to
    AND DOC_TITLE NOT LIKE '%訂正%'
    AND DOC_TITLE NOT LIKE '%差替え%'
    AND DOC_TITLE NOT LIKE '%のお知らせ'
    AND DOC_TITLE NOT LIKE '%に関するお知らせ'
),
fin AS (
  -- fin_summary から開示時刻・四半期・決算期末を取得
  -- 同一銘柄×同一日に複数レコード（連結+単体等）がある場合は最初の1件のみ
  SELECT
    LOCAL_CODE,
    DISCLOSED_DATE,
    DISCLOSED_TIME,
    TYPE_OF_CURRENT_PERIOD,
    TYPE_OF_DOCUMENT,
    CURRENT_FISCAL_YEAR_END_DATE,
    ROW_NUMBER() OVER (
      PARTITION BY LOCAL_CODE, DISCLOSED_DATE
      ORDER BY DISCLOSURE_NUMBER DESC
    ) AS rn
  FROM `{project}.STOCK.fin_summary`
  WHERE DISCLOSED_DATE BETWEEN @date_from AND @date_to
)
SELECT
  t.TICKER,
  t.SUBMISSION_DATE,
  t.MAIN_CATEGORY,
  t.DOC_TITLE,
  t.DOC_ID,
  f.DISCLOSED_TIME,
  f.TYPE_OF_CURRENT_PERIOD,
  f.TYPE_OF_DOCUMENT,
  f.CURRENT_FISCAL_YEAR_END_DATE
FROM tdnet_docs t
LEFT JOIN fin f
  ON t.TICKER = f.LOCAL_CODE
  AND t.SUBMISSION_DATE = f.DISCLOSED_DATE
  AND f.rn = 1
ORDER BY t.SUBMISSION_DATE, t.TICKER
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
        category = CATEGORY_MAP.get(row["MAIN_CATEGORY"])
        if category is None:
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

        # TYPE_OF_DOCUMENT
        type_of_doc = None
        if pd.notna(row.get("TYPE_OF_DOCUMENT")):
            type_of_doc = row["TYPE_OF_DOCUMENT"]

        # DOC_TITLE
        doc_title = None
        if pd.notna(row.get("DOC_TITLE")):
            doc_title = str(row["DOC_TITLE"])

        records.append({
            "TICKER": row["TICKER"],
            "FISCAL_YEAR_END": fiscal_year_end,
            "QUARTER": quarter,
            "CATEGORY": category,
            "RECORD_TYPE": "A",
            "REVISION_SEQ": 1,  # 後で上書き
            "DISCLOSURE_DATE": str(row["SUBMISSION_DATE"]),
            "DISCLOSURE_TIME": disclosure_time,
            "DISCLOSURE_NUMBER": str(row["DOC_ID"]) if pd.notna(row.get("DOC_ID")) else None,
            "TYPE_OF_DOCUMENT": type_of_doc,
            "DOC_TITLE": doc_title,
            "SOURCE": "tdnet",
            "LOADED_AT": now_jst,
        })

    out = pd.DataFrame(records)

    if out.empty:
        return out

    # REVISION_SEQ: 業績予想(F)は同一銘柄×決算期×QUARTERで日付順に連番
    # 決算(R)は常に1
    def assign_seq(group: pd.DataFrame) -> pd.DataFrame:
        group = group.sort_values("DISCLOSURE_DATE")
        group["REVISION_SEQ"] = range(1, len(group) + 1)
        return group

    f_mask = out["CATEGORY"] == "F"
    if f_mask.any():
        f_df = out[f_mask].copy()
        f_df = f_df.groupby(
            ["TICKER", "FISCAL_YEAR_END", "QUARTER"], group_keys=False, dropna=False,
        ).apply(assign_seq, include_groups=False)
        out.loc[f_mask, "REVISION_SEQ"] = f_df["REVISION_SEQ"]

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

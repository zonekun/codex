"""株主名TYPEマッピングCSVをBQ SHAREHOLDER_COMPOSITION_EXTENDにロードする.

Usage:
    PYTHONUTF8=1 uv run python scripts/tob_prediction/load_shareholder_name_types.py --mode dry-run
    PYTHONUTF8=1 uv run python scripts/tob_prediction/load_shareholder_name_types.py --mode sample
    PYTHONUTF8=1 uv run python scripts/tob_prediction/load_shareholder_name_types.py --mode full
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import structlog
from google.cloud import bigquery
from google.oauth2 import service_account

from src.core.config import settings

logger = structlog.get_logger()

INPUT_CSV = Path(r"C:\tmp\tob_prediction\shareholder_name_types.csv")
PROJECT = "gmailpj-357912"
DATASET = "STOCK"
TABLE = "SHAREHOLDER_COMPOSITION_EXTEND"
FULL_TABLE = f"{PROJECT}.{DATASET}.{TABLE}"

SCHEMA = [
    bigquery.SchemaField("NAME", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("NAME_NORMALIZED", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("TYPE", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("CONFIDENCE", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("TICKER", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("UPDATED_AT", "TIMESTAMP", mode="REQUIRED"),
]

_ALL_SPACES_RE = re.compile(r"[\s　 ]+")


def _normalize_fullwidth(name: str) -> str:
    """Fullwidth Latin/digit → ASCII."""
    result = []
    for ch in name:
        cp = ord(ch)
        if 0xFF21 <= cp <= 0xFF3A:
            result.append(chr(cp - 0xFEE0))
        elif 0xFF41 <= cp <= 0xFF5A:
            result.append(chr(cp - 0xFEE0))
        elif 0xFF10 <= cp <= 0xFF19:
            result.append(chr(cp - 0xFEE0))
        else:
            result.append(ch)
    return "".join(result)


def _compute_normalized(name: str) -> str:
    """JOIN用正規化: fullwidth→ASCII + スペース統一 + upper."""
    n = _normalize_fullwidth(name)
    n = _ALL_SPACES_RE.sub(" ", n).strip()
    return n.upper()


def build_client() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(
        settings.google_application_credentials
    )
    return bigquery.Client(project=PROJECT, credentials=creds)


def load_csv() -> pd.DataFrame:
    df = pd.read_csv(INPUT_CSV, encoding="utf-8")
    required = {"name", "type", "confidence"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {missing}")
    logger.info("csv_loaded", rows=len(df), unclassified=int((df["type"] == "UNCLASSIFIED").sum()))
    return df


def prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    """CSVをBQスキーマに合わせたDataFrameに変換する."""
    jst = timezone(timedelta(hours=9))
    now = datetime.now(tz=jst)

    out = pd.DataFrame()
    out["NAME"] = df["name"]
    out["NAME_NORMALIZED"] = df["name"].apply(_compute_normalized)
    out["TYPE"] = df["type"]
    out["CONFIDENCE"] = df["confidence"].fillna("RULE")
    out["TICKER"] = None
    out["UPDATED_AT"] = now
    return out


def create_table_if_not_exists(client: bigquery.Client) -> None:
    table_ref = bigquery.Table(FULL_TABLE, schema=SCHEMA)
    table_ref.description = (
        "株主名→TYPE マッピングテーブル。TOP10_NAMES_JSON内のユニーク株主名を6種に分類。"
        "classify_shareholder_names.py + Sonnet判定結果。"
    )
    try:
        client.create_table(table_ref)
        logger.info("table_created", table=FULL_TABLE)
    except Exception as e:
        if "Already Exists" in str(e):
            logger.info("table_already_exists", table=FULL_TABLE)
        else:
            raise


def insert_rows(client: bigquery.Client, df: pd.DataFrame) -> None:
    job_config = bigquery.LoadJobConfig(
        schema=SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    job = client.load_table_from_dataframe(df, FULL_TABLE, job_config=job_config)
    job.result()
    logger.info("load_complete", rows=len(df), table=FULL_TABLE)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["dry-run", "sample", "full"],
        default="dry-run",
        help="dry-run: CSVのみ確認 / sample: 10件のみINSERT / full: 全件TRUNCATE+INSERT",
    )
    args = parser.parse_args()

    df_csv = load_csv()
    df_bq = prepare_df(df_csv)

    logger.info(
        "type_distribution",
        **df_bq["TYPE"].value_counts().to_dict(),
    )

    if args.mode == "dry-run":
        logger.info("dry_run_sample", data=df_bq.head(5).to_dict(orient="records"))
        print("\n--- dry-run: 先頭5件 ---")
        print(df_bq.head(5).to_string(index=False))
        print(f"\n合計 {len(df_bq)} 行。問題なければ --mode sample で10件確認。")
        return

    client = build_client()

    if args.mode == "sample":
        create_table_if_not_exists(client)
        sample = df_bq.head(10)
        job_config = bigquery.LoadJobConfig(
            schema=SCHEMA,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )
        job = client.load_table_from_dataframe(sample, FULL_TABLE, job_config=job_config)
        job.result()
        logger.info("sample_insert_done", rows=10)
        print("10件INSERTしました。BQコンソールで確認後 --mode full で全件投入してください。")
        return

    # full
    create_table_if_not_exists(client)
    insert_rows(client, df_bq)
    print(f"\n✓ {len(df_bq):,}行を {FULL_TABLE} に投入完了。")


if __name__ == "__main__":
    main()

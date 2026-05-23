"""TOB公表IRリリース日テーブルを BQ に投入.

Codex 収集済み CSV (tob_ir_release_dates_20260519_200803.csv, 616行) の
resolved 分 475 行を `STOCK.DELISTED_STOCKS_TOB_ENHANCE` に TRUNCATE + ロードする。

データカタログ: docs/data_catalog/bq_delisted_stocks_tob_enhance.md

Usage:
    PYTHONUTF8=1 python scripts/load_tob_ir_release_dates.py
    PYTHONUTF8=1 python scripts/load_tob_ir_release_dates.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import timezone, timedelta
from pathlib import Path

import pandas as pd
import structlog
from google.cloud import bigquery
from google.oauth2 import service_account

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

CREDENTIALS_PATH = PROJECT_ROOT / "keys" / "gcp-service-account.json"
BQ_PROJECT = "gmailpj-357912"
BQ_DATASET = "STOCK"
BQ_TABLE = f"{BQ_PROJECT}.{BQ_DATASET}.DELISTED_STOCKS_TOB_ENHANCE"

# Codex 詳細 CSV（full schema, 616行）
SOURCE_CSV = PROJECT_ROOT / "data" / "csv" / "tob_ir_release_dates" / "tob_ir_release_dates_20260519_200803.csv"

JST = timezone(timedelta(hours=+9), "JST")

BQ_SCHEMA = [
    bigquery.SchemaField("TICKER", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("IR_FIRST_RELEASE_DATE", "DATE", mode="NULLABLE"),
    bigquery.SchemaField("IR_RELEASE_KIND", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("SOURCE", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("DOC_ID_OR_URL", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("DOC_TITLE", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("EVIDENCE_TEXT", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("CONFIDENCE", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("NOTES", "STRING", mode="NULLABLE"),
]

log = structlog.get_logger()


def _get_bq() -> bigquery.Client:
    """BQ クライアントを生成."""
    if not CREDENTIALS_PATH.exists():
        log.error("credentials_not_found", path=str(CREDENTIALS_PATH))
        sys.exit(1)
    creds = service_account.Credentials.from_service_account_file(
        str(CREDENTIALS_PATH),
        scopes=["https://www.googleapis.com/auth/bigquery"],
    )
    return bigquery.Client(project=BQ_PROJECT, credentials=creds)


def load_csv() -> pd.DataFrame:
    """CSV 読み込み → BQ スキーマにマッピング → UNRESOLVED 除外."""
    if not SOURCE_CSV.exists():
        log.error("source_csv_not_found", path=str(SOURCE_CSV))
        sys.exit(1)

    raw = pd.read_csv(SOURCE_CSV, encoding="utf-8", dtype={"ticker": str})
    log.info("csv_loaded", rows=len(raw), path=str(SOURCE_CSV))

    # UNRESOLVED 除外
    resolved = raw[raw["source"] != "UNRESOLVED"].copy()
    log.info("after_filter", total=len(raw), resolved=len(resolved), unresolved=len(raw) - len(resolved))

    # カラムマッピング（CSV lowercase → BQ UPPERCASE）
    resolved = resolved.rename(columns={
        "ticker": "TICKER",
        "ir_release_date": "IR_FIRST_RELEASE_DATE",
        "ir_release_kind": "IR_RELEASE_KIND",
        "source": "SOURCE",
        "doc_id_or_url": "DOC_ID_OR_URL",
        "doc_title": "DOC_TITLE",
        "evidence_text": "EVIDENCE_TEXT",
        "confidence": "CONFIDENCE",
        "notes": "NOTES",
    })

    # BQ スキーマ列のみ残す
    bq_cols = [f.name for f in BQ_SCHEMA]
    df = resolved[bq_cols].copy()

    # DATE 型変換
    df["IR_FIRST_RELEASE_DATE"] = pd.to_datetime(
        df["IR_FIRST_RELEASE_DATE"], errors="coerce"
    ).dt.date

    # DOC_ID_OR_URL: float として読まれた TDnet 文書 ID → 整数文字列
    def _to_id_str(v: object) -> str | None:
        if pd.isna(v):
            return None
        try:
            return str(int(float(str(v))))
        except (ValueError, OverflowError):
            return str(v)

    df["DOC_ID_OR_URL"] = df["DOC_ID_OR_URL"].apply(_to_id_str)

    # TICKER を文字列型で確定
    df["TICKER"] = df["TICKER"].astype(str)

    return df


def ensure_table(bq: bigquery.Client) -> None:
    """テーブルが存在しない場合は CREATE、存在する場合はスキーマ確認のみ."""
    table_ref = bigquery.Table(BQ_TABLE, schema=BQ_SCHEMA)
    try:
        bq.get_table(BQ_TABLE)
        log.info("table_exists", table=BQ_TABLE)
    except Exception:
        bq.create_table(table_ref)
        log.info("table_created", table=BQ_TABLE)


def upload(bq: bigquery.Client, df: pd.DataFrame) -> None:
    """DataFrame を BQ テーブルに WRITE_TRUNCATE でロード."""
    job_cfg = bigquery.LoadJobConfig(
        schema=BQ_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    job = bq.load_table_from_dataframe(df, BQ_TABLE, job_config=job_cfg)
    job.result()  # 完了待ち
    table = bq.get_table(BQ_TABLE)
    log.info("upload_done", rows=table.num_rows, table=BQ_TABLE)


def main() -> None:
    """エントリポイント."""
    parser = argparse.ArgumentParser(description="TOB IR リリース日 BQ 投入")
    parser.add_argument("--dry-run", action="store_true", help="BQ 書き込みをスキップして内容確認のみ")
    args = parser.parse_args()

    errors = 0

    df = load_csv()

    # サンプル表示
    log.info("sample_rows", n=3)
    print(df.head(3).to_string())
    print()
    print(f"投入予定: {len(df)} 行 → {BQ_TABLE}")
    print(f"IR_RELEASE_KIND: {df['IR_RELEASE_KIND'].value_counts().to_dict()}")
    print(f"CONFIDENCE: {df['CONFIDENCE'].value_counts().to_dict()}")

    if args.dry_run:
        log.info("dry_run_complete", rows=len(df))
        sys.exit(0)

    bq = _get_bq()
    ensure_table(bq)
    upload(bq, df)

    log.info("done", errors=errors)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()

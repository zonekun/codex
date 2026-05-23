"""SHAREHOLDER_COMPOSITIONにオーナー色派生カラムを追加・更新する.

Step 5: ALTER TABLE → 有名投資家リスト生成 → BQ DML UPDATE

Usage:
    PYTHONUTF8=1 uv run python scripts/tob_prediction/compute_owner_features.py --mode dry-run
    PYTHONUTF8=1 uv run python scripts/tob_prediction/compute_owner_features.py --mode sample
    PYTHONUTF8=1 uv run python scripts/tob_prediction/compute_owner_features.py --mode full
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import structlog
from google.cloud import bigquery
from google.oauth2 import service_account

from src.core.config import settings

logger = structlog.get_logger()

PROJECT = "gmailpj-357912"
SC_TABLE = f"{PROJECT}.STOCK.SHAREHOLDER_COMPOSITION"
EXTEND_TABLE = f"{PROJECT}.STOCK.SHAREHOLDER_COMPOSITION_EXTEND"

FAMOUS_THRESHOLD = 20  # >=20銘柄TOP10に出現するINDIVIDUALを「有名個人投資家」とする (EDA: 19名)
FAMOUS_CSV = Path(r"C:\tmp\tob_prediction\famous_investors.csv")

NEW_COLUMNS = [
    bigquery.SchemaField("REAL_TOP_NAME", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("REAL_TOP_RATIO", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("REAL_TOP_TYPE", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("OWNER_COUNT_IN_TOP10", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("OWNER_RATIO_IN_TOP10", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("HAS_FAMOUS_INVESTOR", "BOOL", mode="NULLABLE"),
    bigquery.SchemaField("HAS_BUSINESS_PARTNER_INVESTOR", "BOOL", mode="NULLABLE"),
    bigquery.SchemaField("BUSINESS_PARTNER_RATIO_IN_TOP10", "FLOAT64", mode="NULLABLE"),
]


def build_client() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(
        settings.google_application_credentials
    )
    return bigquery.Client(project=PROJECT, credentials=creds)


def add_columns_if_missing(client: bigquery.Client) -> None:
    """新カラムが未存在の場合のみ ALTER TABLE で追加する."""
    table = client.get_table(SC_TABLE)
    existing = {f.name for f in table.schema}
    new_fields = [f for f in NEW_COLUMNS if f.name not in existing]
    if not new_fields:
        logger.info("all_columns_already_exist")
        return

    # BQ DDL で追加
    for field in new_fields:
        dtype = field.field_type
        ddl = f"""
        ALTER TABLE `{SC_TABLE}`
        ADD COLUMN IF NOT EXISTS {field.name} {dtype}
        """
        client.query(ddl).result()
        logger.info("column_added", name=field.name, type=dtype)

    logger.info("alter_table_done", added=len(new_fields))


def build_famous_investors(client: bigquery.Client) -> list[str]:
    """出現銘柄数>=FAMOUS_THRESHOLDのINDIVIDUAL名リストを返す。CSV出力も行う."""
    sql = f"""
    WITH expanded AS (
      SELECT SC.TICKER, JSON_VALUE(entry, '$.name') AS entry_name
      FROM `{SC_TABLE}` SC,
      UNNEST(JSON_QUERY_ARRAY(SC.TOP10_NAMES_JSON)) AS entry
      WHERE SC.TOP10_NAMES_JSON IS NOT NULL
    ),
    individual_counts AS (
      SELECT E.entry_name AS name, COUNT(DISTINCT E.TICKER) AS ticker_count
      FROM expanded E
      INNER JOIN `{EXTEND_TABLE}` SCE
        ON SCE.NAME = E.entry_name AND SCE.TYPE = 'INDIVIDUAL'
      GROUP BY E.entry_name
    )
    SELECT name, ticker_count
    FROM individual_counts
    WHERE ticker_count >= {FAMOUS_THRESHOLD}
    ORDER BY ticker_count DESC
    """
    df = client.query(sql).to_dataframe()
    FAMOUS_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(FAMOUS_CSV, index=False, encoding="utf-8")
    logger.info("famous_investors_saved", count=len(df), path=str(FAMOUS_CSV))
    print(f"\n=== 有名個人投資家 (>={FAMOUS_THRESHOLD}社, {len(df)}名) ===")
    print(df.to_string(index=False))
    return df["name"].tolist()


def _eda_listed_corp(client: bigquery.Client) -> None:
    """LISTED_CORP TYPE の件数と TOP10 出現分布を表示（変更なし）."""
    sql = f"""
    WITH expanded AS (
      SELECT SC.TICKER, SC.FISCAL_YEAR_END,
        JSON_VALUE(entry, '$.name') AS entry_name
      FROM `{SC_TABLE}` SC,
      UNNEST(JSON_QUERY_ARRAY(SC.TOP10_NAMES_JSON)) AS entry
      WHERE SC.TOP10_NAMES_JSON IS NOT NULL
    )
    SELECT
      COALESCE(SCE.TYPE, 'UNKNOWN') AS entry_type,
      COUNT(DISTINCT CONCAT(E.TICKER, CAST(E.FISCAL_YEAR_END AS STRING))) AS n_records,
      COUNT(DISTINCT E.TICKER) AS n_tickers
    FROM expanded E
    LEFT JOIN `{EXTEND_TABLE}` SCE ON SCE.NAME = E.entry_name
    WHERE COALESCE(SCE.TYPE, 'UNKNOWN') = 'LISTED_CORP'
    GROUP BY entry_type
    """
    df = client.query(sql).to_dataframe()
    print("\n=== LISTED_CORP 出現件数 (Plan B 完了確認) ===")
    print(df.to_string(index=False) if not df.empty else "LISTED_CORP=0件 → Plan B 未完了")


def _check_plan_b_complete(client: bigquery.Client) -> None:
    """Plan B 完了確認: LISTED_CORP が 50 件以上存在することを検証."""
    sql = f"""
    SELECT COUNT(*) AS n
    FROM `{EXTEND_TABLE}`
    WHERE TYPE = 'LISTED_CORP'
    """
    df = client.query(sql).to_dataframe()
    n = int(df["n"].iloc[0])
    if n < 50:
        raise RuntimeError(
            f"Plan B 未完了: LISTED_CORP={n}件 (基準: 50件以上)。"
            "Plan B を完了してから再実行してください。"
        )
    logger.info("plan_b_gate_passed", listed_corp_count=n)


def compute_and_update(client: bigquery.Client, sample_tickers: list[str] | None = None) -> None:
    """BQ DML UPDATE でオーナー色カラムを計算・書き込む.

    Args:
        sample_tickers: Noneなら全件。リストなら指定TICKERのみ。
    """
    _check_plan_b_complete(client)
    ticker_filter = ""
    if sample_tickers:
        tickers_str = ", ".join(f"'{t}'" for t in sample_tickers)
        ticker_filter = f"AND SC.TICKER IN ({tickers_str})"

    # 有名投資家リストをBQ一時テーブルで渡すためにインライン化
    famous_sql_subq = f"""
    WITH expanded_famous AS (
      SELECT SC.TICKER, JSON_VALUE(entry, '$.name') AS entry_name
      FROM `{SC_TABLE}` SC,
      UNNEST(JSON_QUERY_ARRAY(SC.TOP10_NAMES_JSON)) AS entry
      WHERE SC.TOP10_NAMES_JSON IS NOT NULL
    ),
    individual_counts AS (
      SELECT E.entry_name AS name
      FROM expanded_famous E
      INNER JOIN `{EXTEND_TABLE}` SCE
        ON SCE.NAME = E.entry_name AND SCE.TYPE = 'INDIVIDUAL'
      GROUP BY E.entry_name
      HAVING COUNT(DISTINCT E.TICKER) >= {FAMOUS_THRESHOLD}
    )
    SELECT name FROM individual_counts
    """

    update_sql = f"""
    UPDATE `{SC_TABLE}` SC
    SET
      SC.REAL_TOP_NAME                   = derived.REAL_TOP_NAME,
      SC.REAL_TOP_RATIO                  = derived.REAL_TOP_RATIO,
      SC.REAL_TOP_TYPE                   = derived.REAL_TOP_TYPE,
      SC.OWNER_COUNT_IN_TOP10            = derived.OWNER_COUNT_IN_TOP10,
      SC.OWNER_RATIO_IN_TOP10            = derived.OWNER_RATIO_IN_TOP10,
      SC.HAS_FAMOUS_INVESTOR             = derived.HAS_FAMOUS_INVESTOR,
      SC.HAS_BUSINESS_PARTNER_INVESTOR   = derived.HAS_BUSINESS_PARTNER_INVESTOR,
      SC.BUSINESS_PARTNER_RATIO_IN_TOP10 = derived.BUSINESS_PARTNER_RATIO_IN_TOP10
    FROM (
      WITH entries AS (
        SELECT
          SC2.TICKER,
          SC2.FISCAL_YEAR_END,
          JSON_VALUE(entry, '$.name')            AS entry_name,
          SAFE_CAST(JSON_VALUE(entry, '$.ratio') AS FLOAT64) AS entry_ratio,
          OFFSET                                  AS entry_rank
        FROM `{SC_TABLE}` SC2,
        UNNEST(JSON_QUERY_ARRAY(SC2.TOP10_NAMES_JSON)) AS entry WITH OFFSET
        WHERE SC2.TOP10_NAMES_JSON IS NOT NULL
        {ticker_filter.replace('SC.TICKER', 'SC2.TICKER')}
      ),
      entries_typed AS (
        SELECT
          E.TICKER,
          E.FISCAL_YEAR_END,
          E.entry_name,
          E.entry_ratio,
          E.entry_rank,
          COALESCE(SCE.TYPE, 'UNKNOWN') AS entry_type
        FROM entries E
        LEFT JOIN `{EXTEND_TABLE}` SCE ON SCE.NAME = E.entry_name
      ),
      famous_investors AS (
        {famous_sql_subq}
      ),
      real_top AS (
        SELECT TICKER, FISCAL_YEAR_END, entry_name, entry_ratio, entry_type,
          ROW_NUMBER() OVER (
            PARTITION BY TICKER, FISCAL_YEAR_END
            ORDER BY entry_rank
          ) AS rn
        FROM entries_typed
        WHERE entry_type NOT IN ('TRUST_BANK', 'FOREIGN_CUSTODIAN', 'UNKNOWN')
      ),
      owner_agg AS (
        SELECT
          TICKER,
          FISCAL_YEAR_END,
          COUNTIF(entry_type IN ('INDIVIDUAL', 'ASSET_MGMT'))       AS OWNER_COUNT_IN_TOP10,
          IFNULL(SUM(IF(entry_type IN ('INDIVIDUAL', 'ASSET_MGMT'), entry_ratio, 0)), 0) AS OWNER_RATIO_IN_TOP10,
          LOGICAL_OR(entry_name IN (SELECT name FROM famous_investors)) AS HAS_FAMOUS_INVESTOR,
          LOGICAL_OR(entry_type = 'LISTED_CORP')                        AS HAS_BUSINESS_PARTNER_INVESTOR,
          IFNULL(SUM(IF(entry_type = 'LISTED_CORP', entry_ratio, 0)), 0.0) AS BUSINESS_PARTNER_RATIO_IN_TOP10
        FROM entries_typed
        GROUP BY TICKER, FISCAL_YEAR_END
      )
      SELECT
        OA.TICKER,
        OA.FISCAL_YEAR_END,
        RT.entry_name    AS REAL_TOP_NAME,
        RT.entry_ratio   AS REAL_TOP_RATIO,
        RT.entry_type    AS REAL_TOP_TYPE,
        OA.OWNER_COUNT_IN_TOP10,
        OA.OWNER_RATIO_IN_TOP10,
        OA.HAS_FAMOUS_INVESTOR,
        OA.HAS_BUSINESS_PARTNER_INVESTOR,
        OA.BUSINESS_PARTNER_RATIO_IN_TOP10
      FROM owner_agg OA
      LEFT JOIN real_top RT
        ON RT.TICKER = OA.TICKER AND RT.FISCAL_YEAR_END = OA.FISCAL_YEAR_END AND RT.rn = 1
    ) derived
    WHERE SC.TICKER = derived.TICKER
      AND SC.FISCAL_YEAR_END = derived.FISCAL_YEAR_END
      {ticker_filter}
    """

    logger.info("update_start", sample=sample_tickers is not None)
    job = client.query(update_sql)
    result = job.result()
    affected = result.num_dml_affected_rows
    logger.info("update_done", affected_rows=affected)
    print(f"UPDATE完了: {affected}行更新")


def verify_results(client: bigquery.Client, tickers: list[str]) -> None:
    """指定TICKERのオーナー色カラム検証."""
    tickers_str = ", ".join(f"'{t}'" for t in tickers)
    sql = f"""
    SELECT TICKER, FISCAL_YEAR_END, REAL_TOP_NAME, REAL_TOP_TYPE,
           OWNER_COUNT_IN_TOP10, OWNER_RATIO_IN_TOP10, HAS_FAMOUS_INVESTOR,
           HAS_BUSINESS_PARTNER_INVESTOR, BUSINESS_PARTNER_RATIO_IN_TOP10
    FROM `{SC_TABLE}`
    WHERE TICKER IN ({tickers_str})
    ORDER BY TICKER, FISCAL_YEAR_END DESC
    LIMIT 30
    """
    df = client.query(sql).to_dataframe()
    print(df.to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["dry-run", "sample", "full"], default="dry-run")
    args = parser.parse_args()

    client = build_client()

    if args.mode == "dry-run":
        print("=== dry-run: 有名投資家EDA + LISTED_CORP件数確認 (BQ変更なし) ===")
        build_famous_investors(client)
        _eda_listed_corp(client)
        print("\nALTER TABLE と UPDATE は --mode sample/full で実行されます。")
        return

    # ALTER TABLE（common for sample & full）
    add_columns_if_missing(client)

    # 有名投資家リスト生成
    build_famous_investors(client)

    if args.mode == "sample":
        # ユニクロ・久光・マンダム・トヨタで10件確認
        sample_tickers = ["4686", "1429", "9983", "4530"]  # ジャストシステム・日本アクア・ユニクロ・久光
        print(f"\n--- sample: {sample_tickers} の4銘柄で確認 ---")
        compute_and_update(client, sample_tickers=sample_tickers)
        print("\n--- 結果確認 ---")
        verify_results(client, sample_tickers)
        print("\n問題なければ --mode full で全件実行してください。")
        return

    # full
    compute_and_update(client, sample_tickers=None)
    print("\n--- 検証: ユニクロ(9983)・久光(4530)・マンダム(4917) ---")
    verify_results(client, ["9983", "4530", "4917"])


if __name__ == "__main__":
    main()

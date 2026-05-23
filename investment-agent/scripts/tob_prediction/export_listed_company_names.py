"""BQ STOCK_CODE_LIST から上場銘柄名セットを data/master/listed_company_names.csv に出力する.

Usage:
    PYTHONUTF8=1 uv run python scripts/tob_prediction/export_listed_company_names.py --dry-run
    PYTHONUTF8=1 uv run python scripts/tob_prediction/export_listed_company_names.py
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd
import structlog
from google.cloud import bigquery
from google.oauth2 import service_account

from src.core.config import settings

logger = structlog.get_logger()

OUTPUT_CSV = Path("data/master/listed_company_names.csv")
PROJECT = "gmailpj-357912"
SQL = """
SELECT DISTINCT name
FROM (
  SELECT STOCK_NAME AS name
  FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
  WHERE STOCK_NAME IS NOT NULL
  UNION DISTINCT
  SELECT COMPANY_NAME AS name
  FROM `gmailpj-357912.STOCK.DELISTED_STOCKS`
  WHERE COMPANY_NAME IS NOT NULL
)
ORDER BY name
"""

_ALL_SPACES_RE = re.compile(r"[\s　\xa0]+")
_CORP_PREFIXES = (
    "株式会社", "㈱", "（株）", "(株)", "有限会社", "合同会社",
    "合資会社", "合名会社",
)
_CORP_SUFFIXES_END = ("株式会社", "㈱", "（株）", "(株)")


def _normalize_fullwidth(name: str) -> str:
    """Convert fullwidth Latin letters/digits to ASCII equivalents."""
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


def _normalize_for_listed_check(name: str) -> str:
    """法人格プレフィックス/サフィックス除去 + fullwidth→ASCII + スペース除去 + upper.

    引数は原文でも fullwidth 変換済みでも可（内部で再変換するため結果は同一）。
    """
    # TODO: classify_shareholder_names.py に同一実装あり。変更時は両ファイルを同期すること。
    n = _normalize_fullwidth(name).strip()
    n = _ALL_SPACES_RE.sub("", n)
    for prefix in _CORP_PREFIXES:
        if n.startswith(prefix):
            n = n[len(prefix):]
            break
    for suffix in _CORP_SUFFIXES_END:
        if n.endswith(suffix):
            n = n[:-len(suffix)]
            break
    return n.upper()


def build_client() -> bigquery.Client:
    """BQ クライアントを構築する."""
    creds = service_account.Credentials.from_service_account_file(
        settings.google_application_credentials
    )
    return bigquery.Client(project=PROJECT, credentials=creds)


def main() -> None:
    """BQ から上場銘柄名を取得して CSV に出力する."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="CSV出力せず先頭20件を表示して終了",
    )
    args = parser.parse_args()

    client = build_client()
    df = client.query(SQL).to_dataframe()
    logger.info("bq_fetched", count=len(df))

    df["name_normalized"] = df["name"].apply(_normalize_for_listed_check)
    df = df.rename(columns={"name": "stock_name"})

    if args.dry_run:
        print(df.head(20).to_string(index=False))
        return

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8")  # internal use only (utf-8); not for external sharing
    logger.info("listed_names_exported", count=len(df), path=str(OUTPUT_CSV))


if __name__ == "__main__":
    main()

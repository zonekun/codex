"""創業家資産管理会社候補CSV生成スクリプト.

SHAREHOLDER_COMPOSITION の PRIVATE_CORP かつ1社のみ出現する株主を
アクティビスト・上場事業法人を除外した上でCSV出力する。

Usage:
    PYTHONUTF8=1 uv run python scripts/tob_prediction/generate_family_holding_candidates.py --mode dry-run
    PYTHONUTF8=1 uv run python scripts/tob_prediction/generate_family_holding_candidates.py --mode full
"""

from __future__ import annotations

import argparse
import csv
import re
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
SCL_TABLE = f"{PROJECT}.STOCK.STOCK_CODE_LIST"

OUTPUT_CSV = Path(r"C:\tmp\tob_prediction\family_holding_candidates.csv")
EXCLUDED_ACTIVISTS_CSV = Path(r"C:\tmp\tob_prediction\excluded_activists.csv")
EXCLUDED_LISTED_CSV = Path(r"C:\tmp\tob_prediction\excluded_listed_corps.csv")
ACTIVISTS_CSV = Path("data/master/activists.csv")
ACTIVIST_ALIASES_CSV = Path("data/master/activist_aliases.csv")

# ---------------------------------------------------------------------------
# 正規化ヘルパー（classify_shareholder_names.py と統一）
# ---------------------------------------------------------------------------

# 法人格プレフィックス（上場事業法人照合時に除去）
# classify_shareholder_names.py の CORP_SUFFIXES_JP から先頭付与パターンを抽出
_LEGAL_PREFIXES = [
    "株式会社", "有限会社", "合同会社", "合資会社", "合名会社",
    "㈱", "㈲",
    "（株）", "(株)", "(株）", "（株)",  # 混在ブラケット
    "（有）", "(有)",
    "（合）", "(合)",
]

_ALL_SPACES_RE = re.compile(r"[\s　\xa0]+")


def _normalize_fullwidth(name: str) -> str:
    """全角英数字→ASCII変換（classify_shareholder_names.py と同実装）."""
    result = []
    for ch in name:
        cp = ord(ch)
        if 0xFF21 <= cp <= 0xFF3A:   # 全角大文字 A-Z
            result.append(chr(cp - 0xFEE0))
        elif 0xFF41 <= cp <= 0xFF5A: # 全角小文字 a-z
            result.append(chr(cp - 0xFEE0))
        elif 0xFF10 <= cp <= 0xFF19: # 全角数字 0-9
            result.append(chr(cp - 0xFEE0))
        else:
            result.append(ch)
    return "".join(result)


def _normalize_for_activist(name: str) -> str:
    """法人格プレフィックス除去 + 全角→ASCII + 全スペース除去 + 大文字統一（アクティビスト照合用）.

    表記揺れ対応:
    - 株式会社/㈱/(株)/(株）などプレフィックス違い
    - スペースあり/なし/全角スペース混在
    - 全角英数字 (UH　Partners２ → UHPartners2)
    """
    name = _normalize_fullwidth(name)
    name = _ALL_SPACES_RE.sub("", name)   # スペース完全除去（単一化では不十分）
    # 法人格プレフィックス除去（除去後もスペースなしで比較）
    for prefix in _LEGAL_PREFIXES:
        norm_prefix = _ALL_SPACES_RE.sub("", prefix)
        if name.upper().startswith(norm_prefix.upper()):
            name = name[len(norm_prefix):]
            break
    return name.upper()


def _strip_legal_prefix(name: str) -> str:
    """法人格プレフィックスを除去した短縮名を返す（上場事業法人照合用）."""
    for prefix in _LEGAL_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):].strip()
    return name.strip()


# ---------------------------------------------------------------------------
# データロード
# ---------------------------------------------------------------------------

def build_client() -> bigquery.Client:
    """BQクライアントを構築する."""
    creds = service_account.Credentials.from_service_account_file(
        settings.google_application_credentials
    )
    return bigquery.Client(project=PROJECT, credentials=creds)


def load_activist_names() -> set[str]:
    """activists.csv + activist_aliases.csv の全名前を正規化してセットで返す（A-1対処）."""
    names: set[str] = set()
    with open(ACTIVISTS_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            names.add(_normalize_for_activist(row["NAME"]))
    with open(ACTIVIST_ALIASES_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            names.add(_normalize_for_activist(row["ALIAS"]))
    return names


def load_listed_company_names(client: bigquery.Client) -> set[str]:
    """STOCK_CODE_LIST の STOCK_NAME とプレフィックス除去版を返す（A-2対処）."""
    sql = f"SELECT DISTINCT STOCK_NAME FROM `{SCL_TABLE}` WHERE STOCK_NAME IS NOT NULL"
    df = client.query(sql).to_dataframe()
    names: set[str] = set()
    for raw in df["STOCK_NAME"].dropna():
        raw = raw.strip()
        names.add(raw)
        stripped = _strip_legal_prefix(raw)
        if stripped:
            names.add(stripped)
    return names


def fetch_candidates(client: bigquery.Client) -> pd.DataFrame:
    """BQからPRIVATE_CORP 1社のみ出現候補を取得する."""
    sql = f"""
    WITH private_entries AS (
      SELECT
        JSON_VALUE(entry, '$.name') AS shareholder_name,
        SC.TICKER,
        MAX(SAFE_CAST(JSON_VALUE(entry, '$.ratio') AS FLOAT64)) AS max_ratio
      FROM `{SC_TABLE}` SC,
      UNNEST(JSON_QUERY_ARRAY(SC.TOP10_NAMES_JSON)) AS entry
      INNER JOIN `{EXTEND_TABLE}` SCE
        ON SCE.NAME = JSON_VALUE(entry, '$.name') AND SCE.TYPE = 'PRIVATE_CORP'
      WHERE SC.TOP10_NAMES_JSON IS NOT NULL
      GROUP BY 1, 2
    ),
    single_ticker AS (
      SELECT shareholder_name
      FROM private_entries
      GROUP BY shareholder_name
      HAVING COUNT(DISTINCT TICKER) = 1
    )
    SELECT
      pe.shareholder_name,
      pe.TICKER,
      scl.STOCK_NAME AS issuer_name,
      pe.max_ratio
    FROM single_ticker st
    JOIN private_entries pe ON pe.shareholder_name = st.shareholder_name
    LEFT JOIN (
      SELECT TICKER, ANY_VALUE(STOCK_NAME) AS STOCK_NAME
      FROM `{SCL_TABLE}`
      GROUP BY TICKER
    ) scl ON scl.TICKER = pe.TICKER
    ORDER BY pe.max_ratio DESC
    """
    logger.info("bq_query_start")
    df = client.query(sql).to_dataframe()
    logger.info("bq_query_done", total_rows=len(df))
    return df


# ---------------------------------------------------------------------------
# フィルタリング
# ---------------------------------------------------------------------------

def _classify_kubun(name: str) -> str:
    """区分を返す（有限/合同 or 株式会社5%+）."""
    if name.startswith(("有限会社", "合同会社")):
        return "有限/合同"
    return "株式会社5%+"


def apply_filters(
    df: pd.DataFrame,
    activist_names: set[str],
    listed_names: set[str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """フィルタを適用し (candidates, excluded_activists, excluded_listed) を返す."""
    df = df.copy()
    df["区分"] = df["shareholder_name"].apply(_classify_kubun)

    # 比率フィルタ: ①有限/合同 ≥1% ②株式会社 ≥5%
    mask_yg = (df["区分"] == "有限/合同") & (df["max_ratio"] >= 0.01)
    mask_kk = (df["区分"] == "株式会社5%+") & (df["max_ratio"] >= 0.05)
    df = df[mask_yg | mask_kk].copy()
    logger.info("after_ratio_filter", count=len(df))

    # アクティビスト除外 (A-1: 全角正規化後に照合)
    normalized = df["shareholder_name"].apply(_normalize_for_activist)
    mask_activist = normalized.isin(activist_names)
    excluded_activists = df[mask_activist].copy()
    df = df[~mask_activist].copy()
    logger.info("activist_excluded", count=len(excluded_activists))

    # 上場事業法人除外 (A-2: プレフィックス除去後に照合)
    stripped = df["shareholder_name"].apply(_strip_legal_prefix)
    mask_listed = stripped.isin(listed_names) | df["shareholder_name"].isin(listed_names)
    excluded_listed = df[mask_listed].copy()
    df = df[~mask_listed].copy()
    logger.info("listed_corp_excluded", count=len(excluded_listed))

    if len(df) == 0:
        logger.warning("no_candidates_after_filter")

    return df, excluded_activists, excluded_listed


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    """エントリポイント."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["dry-run", "full"], default="dry-run")
    args = parser.parse_args()

    client = build_client()

    logger.info("loading_activist_names")
    activist_names = load_activist_names()
    logger.info("activist_names_loaded", count=len(activist_names))

    logger.info("loading_listed_company_names")
    listed_names = load_listed_company_names(client)
    logger.info("listed_names_loaded", count=len(listed_names))

    df_raw = fetch_candidates(client)
    df, exc_activist, exc_listed = apply_filters(df_raw, activist_names, listed_names)

    # 集計表示
    counts = df["区分"].value_counts()
    print("\n=== 候補件数 ===")
    for k, v in counts.items():
        print(f"  {k}: {v}件")
    print(f"  合計: {len(df)}件")
    print(f"\n除外: アクティビスト {len(exc_activist)}件 / 上場事業法人 {len(exc_listed)}件")

    if args.mode == "dry-run":
        print("\n--mode full で CSV を出力します。")
        return

    # CSV出力
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    out = df.rename(columns={
        "TICKER": "発行体TICKER",
        "issuer_name": "発行体名",
        "shareholder_name": "株主名",
        "max_ratio": "最大保有比率",
    })[["発行体TICKER", "発行体名", "株主名", "最大保有比率", "区分"]].copy()
    out["判定"] = ""
    out["最大保有比率"] = out["最大保有比率"].apply(
        lambda x: f"{x * 100:.1f}%" if pd.notna(x) else ""
    )
    out.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    logger.info("output_saved", path=str(OUTPUT_CSV), rows=len(out))

    if len(exc_activist) > 0:
        exc_activist.to_csv(EXCLUDED_ACTIVISTS_CSV, index=False, encoding="utf-8-sig")
        logger.info("excluded_activists_saved", path=str(EXCLUDED_ACTIVISTS_CSV))

    if len(exc_listed) > 0:
        exc_listed.to_csv(EXCLUDED_LISTED_CSV, index=False, encoding="utf-8-sig")
        logger.info("excluded_listed_corps_saved", path=str(EXCLUDED_LISTED_CSV))

    print(f"\n出力: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()

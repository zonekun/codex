"""Nikkei 225 構成銘柄リストを Wikipedia 日本語版から取得し CSV 保存する.

Wikipedia「日経平均株価」ページには業種別に構成銘柄テーブルが 34 個に分かれて掲載されており、
合計すると 225 行になる（業種見出し配下の各テーブル行数の合計）。

本スクリプトはそれらのテーブルを結合し、ticker と 銘柄名を抽出して
`data/master/nikkei225_constituents.csv` に保存する。

業種情報は既存の BQ `STOCK.STOCK_CODE_LIST` から JOIN で取得する方針のため
本スクリプトでは付与しない（Wikipedia のセクション見出しに依存せず、既存マスタと整合を優先）。

実行:
    PYTHONUTF8=1 python scripts/download_nikkei225_constituents.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import structlog

logger = structlog.get_logger()

WIKI_URL = "https://ja.wikipedia.org/wiki/%E6%97%A5%E7%B5%8C%E5%B9%B3%E5%9D%87%E6%A0%AA%E4%BE%A1"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
EXPECTED_COUNT = 225
REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO_ROOT / "data" / "master" / "nikkei225_constituents.csv"


def fetch_wikipedia_html() -> str:
    """Wikipedia 日本語版「日経平均株価」ページ HTML を取得."""
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(WIKI_URL, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text


def extract_constituent_tables(html: str) -> pd.DataFrame:
    """業種別構成銘柄テーブルを結合して単一 DataFrame を返す.

    Wikipedia 日本語版のページ内には「証券コード/銘柄/備考」の列を持つテーブルが
    複数配置されている。それらを縦結合すると 225 行になる。
    """
    tables = pd.read_html(StringIO(html))
    logger.info("wikipedia_tables_found", count=len(tables))

    matched: list[pd.DataFrame] = []
    for tbl in tables:
        cols = list(tbl.columns)
        if cols[:2] == ["証券コード", "銘柄"]:
            matched.append(tbl)

    if not matched:
        raise RuntimeError("証券コード列を持つテーブルが見つからない")

    merged = pd.concat(matched, ignore_index=True)
    logger.info("constituent_rows_merged", rows=len(merged))
    return merged


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """ticker を 4 桁文字列に正規化し列名を統一する."""
    out = pd.DataFrame()
    out["TICKER"] = df["証券コード"].astype(str).str.extract(r"(\d+)")[0].str.zfill(4)
    out["CODE5"] = out["TICKER"] + "0"
    out["NAME"] = df["銘柄"].astype(str).str.strip()
    out = out.dropna(subset=["TICKER", "NAME"]).copy()
    out = out[out["TICKER"].str.len() == 4]
    out = out.drop_duplicates(subset=["TICKER"]).reset_index(drop=True)
    return out


def main() -> int:
    jst = ZoneInfo("Asia/Tokyo")
    now_jst = datetime.now(tz=jst)
    logger.info("fetch_start", url=WIKI_URL, at=now_jst.isoformat())

    html = fetch_wikipedia_html()
    raw = extract_constituent_tables(html)
    df = normalize(raw)

    if len(df) != EXPECTED_COUNT:
        logger.warning(
            "constituent_count_mismatch",
            expected=EXPECTED_COUNT,
            actual=len(df),
        )
    else:
        logger.info("constituent_count_ok", count=len(df))

    df["SOURCE"] = "wikipedia_ja"
    df["FETCHED_AT_JST"] = now_jst.strftime("%Y-%m-%d %H:%M:%S")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8")
    logger.info("saved", path=str(OUTPUT_PATH), rows=len(df))

    print(f"Saved {len(df)} rows -> {OUTPUT_PATH}")
    print(df.head(10).to_string(index=False))
    return 0 if len(df) == EXPECTED_COUNT else 1


if __name__ == "__main__":
    sys.exit(main())

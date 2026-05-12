"""テーマ横断スクリーニング: TDNET CHUNK_TEXT キーワード共起 + ベクトル類似検索.

093_earnings_deep_analysis.md §テーマ横断スクリーニング の Python 実装。
BQ 結果をローカルで加工し最終テーブルのみ出力する（トークン削減用）。

Usage:
  PYTHONUTF8=1 python scripts/093_deep_analysis_screener.py ^
    --start 2026-04-28 --end 2026-05-02 ^
    --keywords "半導体|ウエハ|ウェーハ|シリコンウエハ" ^
    --vector-query-file config/themes/093_deep_analysis_semiconductor_ai.txt ^
    --cosine 0.40

  # 偽陽性裏どり（CHUNK_TEXT確認）
  PYTHONUTF8=1 python scripts/093_deep_analysis_screener.py ^
    --chunks 5332,9202 --start 2026-04-28 --end 2026-05-02 ^
    --keywords "半導体|ウエハ|ウェーハ|シリコンウエハ"
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import structlog
from dotenv import load_dotenv
from google.cloud import bigquery
from google.oauth2 import service_account

PROJECT_ROOT = Path(r"C:\gdrive\claude\investment-agent")
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

PROJECT_ID = "gmailpj-357912"
DATASET = "STOCK"
TABLE_TDNET = f"{PROJECT_ID}.{DATASET}.TDNET_DOCUMENTS_ENHANCED"
TABLE_STOCK = f"{PROJECT_ID}.{DATASET}.STOCK_CODE_LIST"
TABLE_YF = f"{PROJECT_ID}.{DATASET}.YF_STOCK_INFO"
EMBED_MODEL = f"{PROJECT_ID}.{DATASET}.embed_model"

POSITIVE_PATTERN = r"増収|増益|好調|堅調|伸長|拡大|過去最高|上方修正|大幅増|順調|上振れ|上回"
DOC_CATEGORIES = ("決算短信", "決算説明資料")

JST = timezone(timedelta(hours=+9), "JST")
log = structlog.get_logger()

_bq: bigquery.Client | None = None


def _get_bq_client() -> bigquery.Client:
    """BQ クライアントを singleton で取得."""
    global _bq
    if _bq is None:
        from src.core.config import Settings

        settings = Settings()
        creds = service_account.Credentials.from_service_account_file(
            settings.google_application_credentials,
        )
        _bq = bigquery.Client(project=PROJECT_ID, credentials=creds)
    return _bq


def search_keywords(start: str, end: str, keywords: str) -> pd.DataFrame:
    """Step 1: キーワード共起検索（テーマワード × 好調表現）."""
    sql = f"""
    SELECT t.TICKER,
           STRING_AGG(DISTINCT t.MAIN_CATEGORY, '+') AS doc_types_kw
    FROM `{TABLE_TDNET}` t
    WHERE t.SUBMISSION_DATE BETWEEN @start_date AND @end_date
      AND t.MAIN_CATEGORY IN UNNEST(@categories)
      AND REGEXP_CONTAINS(t.CHUNK_TEXT, @theme_keywords)
      AND REGEXP_CONTAINS(t.CHUNK_TEXT, @positive_pattern)
    GROUP BY t.TICKER
    """
    config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("start_date", "DATE", start),
        bigquery.ScalarQueryParameter("end_date", "DATE", end),
        bigquery.ArrayQueryParameter("categories", "STRING", list(DOC_CATEGORIES)),
        bigquery.ScalarQueryParameter("theme_keywords", "STRING", keywords),
        bigquery.ScalarQueryParameter("positive_pattern", "STRING", POSITIVE_PATTERN),
    ])
    log.info("keyword_search", start=start, end=end, keywords=keywords[:50])
    df = _get_bq_client().query(sql, job_config=config).to_dataframe()
    log.info("keyword_hits", count=len(df))
    return df


def search_vector(
    start: str, end: str, query_text: str, threshold: float
) -> pd.DataFrame:
    """Step 2: ベクトル類似検索（ML.GENERATE_EMBEDDING + ML.DISTANCE）."""
    sql = f"""
    WITH query_emb AS (
      SELECT ml_generate_embedding_result AS emb
      FROM ML.GENERATE_EMBEDDING(
        MODEL `{EMBED_MODEL}`,
        (SELECT @query_text AS content),
        STRUCT(TRUE AS flatten_json_output)
      )
    )
    SELECT t.TICKER,
           ROUND(MIN(ML.DISTANCE(t.EMBEDDING, q.emb, 'COSINE')), 4) AS min_cosine_dist,
           STRING_AGG(DISTINCT t.MAIN_CATEGORY, '+') AS doc_types_vec
    FROM `{TABLE_TDNET}` t
    CROSS JOIN query_emb q
    WHERE t.SUBMISSION_DATE BETWEEN @start_date AND @end_date
      AND t.MAIN_CATEGORY IN UNNEST(@categories)
      AND t.EMBEDDING IS NOT NULL
      AND ML.DISTANCE(t.EMBEDDING, q.emb, 'COSINE') < @threshold
    GROUP BY t.TICKER
    """
    config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("start_date", "DATE", start),
        bigquery.ScalarQueryParameter("end_date", "DATE", end),
        bigquery.ArrayQueryParameter("categories", "STRING", list(DOC_CATEGORIES)),
        bigquery.ScalarQueryParameter("query_text", "STRING", query_text),
        bigquery.ScalarQueryParameter("threshold", "FLOAT64", threshold),
    ])
    log.info("vector_search", start=start, end=end, threshold=threshold)
    df = _get_bq_client().query(sql, job_config=config).to_dataframe()
    log.info("vector_hits", count=len(df))
    return df


def fetch_stock_info(tickers: list[str]) -> pd.DataFrame:
    """銘柄名 + 時価総額（億円）を取得."""
    if not tickers:
        return pd.DataFrame(columns=["TICKER", "STOCK_NAME", "MARKET_CAP_OKU"])
    sql = f"""
    SELECT s.TICKER, s.STOCK_NAME,
           ROUND(yi.MARKET_CAP / 1e8, 0) AS MARKET_CAP_OKU
    FROM `{TABLE_STOCK}` s
    LEFT JOIN (
      SELECT yi.TICKER, yi.MARKET_CAP
      FROM `{TABLE_YF}` yi
      INNER JOIN (
        SELECT TICKER, MAX(LOADED_DATE) AS max_date
        FROM `{TABLE_YF}`
        WHERE TICKER IN UNNEST(@tickers)
        GROUP BY TICKER
      ) latest ON yi.TICKER = latest.TICKER AND yi.LOADED_DATE = latest.max_date
    ) yi ON s.TICKER = yi.TICKER
    WHERE s.TICKER IN UNNEST(@tickers) AND s.EXCHANGE = 'TSE'
    """
    config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("tickers", "STRING", tickers),
    ])
    return _get_bq_client().query(sql, job_config=config).to_dataframe()


def fetch_chunks(
    tickers: list[str], start: str, end: str, keywords: str
) -> pd.DataFrame:
    """偽陽性裏どり用: テーマキーワード含有チャンクを取得."""
    sql = f"""
    SELECT t.TICKER, t.DOC_TITLE, t.MAIN_CATEGORY,
           SUBSTR(t.CHUNK_TEXT, 1, 500) AS chunk_preview
    FROM `{TABLE_TDNET}` t
    WHERE t.TICKER IN UNNEST(@tickers)
      AND t.SUBMISSION_DATE BETWEEN @start_date AND @end_date
      AND t.MAIN_CATEGORY IN UNNEST(@categories)
      AND REGEXP_CONTAINS(t.CHUNK_TEXT, @theme_keywords)
    ORDER BY t.TICKER, t.MAIN_CATEGORY
    """
    config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("tickers", "STRING", tickers),
        bigquery.ScalarQueryParameter("start_date", "DATE", start),
        bigquery.ScalarQueryParameter("end_date", "DATE", end),
        bigquery.ArrayQueryParameter("categories", "STRING", list(DOC_CATEGORIES)),
        bigquery.ScalarQueryParameter("theme_keywords", "STRING", keywords),
    ])
    return _get_bq_client().query(sql, job_config=config).to_dataframe()


def merge_results(
    df_kw: pd.DataFrame, df_vec: pd.DataFrame, df_info: pd.DataFrame
) -> pd.DataFrame:
    """キーワード + ベクトル結果をマージして HIT_TYPE を分類."""
    kw_tickers = set(df_kw["TICKER"])
    vec_tickers = set(df_vec["TICKER"]) if not df_vec.empty else set()

    rows: list[dict] = []
    for ticker in sorted(kw_tickers | vec_tickers):
        in_kw = ticker in kw_tickers
        in_vec = ticker in vec_tickers

        if in_kw and in_vec:
            hit_type = "BOTH"
        elif in_kw:
            hit_type = "KEYWORD_ONLY"
        else:
            hit_type = "VECTOR_ONLY"

        dist = (
            df_vec.loc[df_vec["TICKER"] == ticker, "min_cosine_dist"].values[0]
            if in_vec
            else None
        )

        doc_parts: set[str] = set()
        if in_kw:
            doc_parts.update(
                df_kw.loc[df_kw["TICKER"] == ticker, "doc_types_kw"].values[0].split("+")
            )
        if in_vec:
            doc_parts.update(
                df_vec.loc[df_vec["TICKER"] == ticker, "doc_types_vec"].values[0].split("+")
            )

        rows.append({
            "TICKER": ticker,
            "HIT_TYPE": hit_type,
            "MIN_COSINE_DIST": dist,
            "DOC_TYPES": "+".join(sorted(doc_parts)),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df = df.merge(df_info, on="TICKER", how="left")

    type_order = {"BOTH": 0, "VECTOR_ONLY": 1, "KEYWORD_ONLY": 2}
    df["_sort"] = df["HIT_TYPE"].map(type_order)
    df = df.sort_values(
        ["_sort", "MIN_COSINE_DIST"], ascending=[True, True], na_position="last"
    )
    return df.drop(columns=["_sort"]).reset_index(drop=True)


def main() -> None:
    """メイン処理."""
    parser = argparse.ArgumentParser(description="テーマ横断スクリーニング (093)")
    parser.add_argument("--start", required=True, help="開始日 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="終了日 YYYY-MM-DD")
    parser.add_argument("--keywords", required=True, help="テーマキーワード正規表現")
    parser.add_argument("--vector-query", help="ベクトル検索テキスト（インライン）")
    parser.add_argument("--vector-query-file", help="ベクトル検索テキストファイルパス")
    parser.add_argument("--cosine", type=float, default=0.40, help="コサイン距離閾値")
    parser.add_argument("--chunks", help="裏どり対象 TICKER（カンマ区切り）")
    parser.add_argument("--csv", help="CSV 出力先（省略時: C:\\tmp\\theme_screen_*.csv）")
    args = parser.parse_args()

    # --- chunks モード（裏どり用） ---
    if args.chunks:
        tickers = [t.strip() for t in args.chunks.split(",")]
        log.info("fetch_chunks", tickers=tickers)
        df = fetch_chunks(tickers, args.start, args.end, args.keywords)
        if df.empty:
            print("該当チャンクなし")
            return
        for _, row in df.iterrows():
            print(f"\n--- {row['TICKER']} [{row['MAIN_CATEGORY']}] {row['DOC_TITLE']} ---")
            print(row["chunk_preview"])
        print(f"\n合計: {len(df)} チャンク")
        return

    # --- スクリーニングモード ---
    vector_query: str | None = None
    if args.vector_query_file:
        vector_query = Path(args.vector_query_file).read_text(encoding="utf-8").strip()
    elif args.vector_query:
        vector_query = args.vector_query

    df_kw = search_keywords(args.start, args.end, args.keywords)

    if vector_query:
        df_vec = search_vector(args.start, args.end, vector_query, args.cosine)
    else:
        df_vec = pd.DataFrame(columns=["TICKER", "min_cosine_dist", "doc_types_vec"])

    all_tickers = list(set(df_kw["TICKER"].tolist() + df_vec["TICKER"].tolist()))
    if not all_tickers:
        print("ヒットなし")
        return

    df_info = fetch_stock_info(all_tickers)
    df = merge_results(df_kw, df_vec, df_info)

    # --- サマリ出力 ---
    counts = df["HIT_TYPE"].value_counts().to_dict()
    print("=== テーマスクリーニング結果 ===")
    print(f"期間: {args.start} ~ {args.end}")
    print(f"キーワード: {args.keywords}")
    if vector_query:
        print(f"ベクトル閾値: {args.cosine}")
    print(f"ヒット: {', '.join(f'{k}={v}' for k, v in counts.items())}, 合計={len(df)}")
    print()

    # --- テーブル出力 ---
    display_cols = [
        "TICKER", "STOCK_NAME", "MARKET_CAP_OKU", "HIT_TYPE", "MIN_COSINE_DIST", "DOC_TYPES",
    ]
    print(df[display_cols].to_string(index=False, na_rep="N/A"))

    # --- CSV 保存 ---
    if args.csv:
        out_path = Path(args.csv)
    else:
        ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        out_path = Path(f"C:/tmp/theme_screen_{ts}.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df[display_cols].to_csv(out_path, index=False, encoding="utf-8")
    print(f"\nCSV: {out_path}")


if __name__ == "__main__":
    main()

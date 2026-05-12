"""セグメント変態検知: テーマ出現頻度の時系列変化でセグメント転換を早期発見.

093_earnings_deep_analysis.md §セグメント変態検知 の Python 実装。
決算発表予定銘柄に対し、テーマキーワードの出現推移を期間別に集計し
変態シグナル（漸増/新出/好調転化/急増）を検出する。

Usage:
  PYTHONUTF8=1 python scripts/093_deep_analysis_transform_scanner.py ^
    --disclosure-dates 2026-05-07,2026-05-08 ^
    --themes semiconductor_ai,photonic_fusion ^
    --since 2020-04-01

  # 裏どり（シグナルあり銘柄のCHUNK_TEXT取得）
  PYTHONUTF8=1 python scripts/093_deep_analysis_transform_scanner.py ^
    --verify 6223,7972,9143 ^
    --themes semiconductor_ai ^
    --since 2020-04-01
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
TABLE_CAL = f"{PROJECT_ID}.{DATASET}.EARNINGS_DISCLOSURE_CALENDAR"

DOC_CATEGORIES = ("決算短信", "決算説明資料")
POSITIVE_PATTERN = r"増収|増益|拡大|好調|過去最高|伸長|順調|上振れ|上回"

# テーマ定義: name -> keyword regex
THEMES: dict[str, str] = {
    "semiconductor_ai": r"半導体|ウエハ|ウェーハ|シリコンウエハ|エッチング|CVD|フォトレジスト|ダイシング|ボンディング|ABF|テスター|プローバ|露光|静電チャック|ファインセラミック|CMP",
    "photonic_fusion": r"光電融合|シリコンフォトニクス|光導波路|光チップレット|光I/O|コパッケージ|CPO|光配線|光トランシーバ|光電変換",
    "dc_power_infra": r"受変電設備|特高受電|特別高圧|変圧器|配電盤|制御盤|スイッチギヤ|ガス絶縁開閉装置|遮断器|保護継電器|電力ケーブル|高圧ケーブル|CVケーブル|バスダクト|キュービクル|無停電電源|UPS|直流給電",
    "aerospace_defense": r"航空宇宙|人工衛星|衛星通信|衛星搭載|宇宙機器|防衛装備|レーダ|ロケット|JAXA|高信頼性部品|耐放射線|MIL規格|慣性航法|ジャイロ|ミサイル|戦闘機|イージス|哨戒機",
}

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


def get_disclosure_tickers(dates: list[str]) -> pd.DataFrame:
    """指定日に決算短信を提出した／提出予定の銘柄を取得.

    TDNET実績テーブルを先に参照し、0件ならカレンダーにフォールバック（将来日対応）。
    """
    sql_tdnet = f"""
    SELECT DISTINCT TICKER, SUBMISSION_DATE AS DISCLOSURE_DATE
    FROM `{TABLE_TDNET}`
    WHERE SUBMISSION_DATE IN UNNEST(@dates)
      AND MAIN_CATEGORY = '決算短信'
    ORDER BY SUBMISSION_DATE, TICKER
    """
    config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("dates", "DATE", dates),
    ])
    df = _get_bq_client().query(sql_tdnet, job_config=config).to_dataframe()
    if not df.empty:
        log.info("disclosure_tickers", source="TDNET", dates=dates, count=len(df))
        return df

    sql_cal = f"""
    SELECT DISTINCT TICKER, DISCLOSURE_DATE
    FROM `{TABLE_CAL}`
    WHERE DISCLOSURE_DATE IN UNNEST(@dates)
    ORDER BY DISCLOSURE_DATE, TICKER
    """
    df = _get_bq_client().query(sql_cal, job_config=config).to_dataframe()
    log.info("disclosure_tickers", source="CALENDAR", dates=dates, count=len(df))
    return df


def _fetch_calendar_dates(tickers: list[str]) -> dict[str, str]:
    """カレンダーから直近の決算発表予定日を取得."""
    if not tickers:
        return {}
    sql = f"""
    SELECT TICKER, CAST(MIN(DISCLOSURE_DATE) AS STRING) AS DISCLOSURE_DATE
    FROM `{TABLE_CAL}`
    WHERE TICKER IN UNNEST(@tickers)
      AND DISCLOSURE_DATE >= CURRENT_DATE() - 7
    GROUP BY TICKER
    """
    config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("tickers", "STRING", tickers),
    ])
    df = _get_bq_client().query(sql, job_config=config).to_dataframe()
    if df.empty:
        return {}
    return dict(zip(df["TICKER"], df["DISCLOSURE_DATE"]))


def scan_theme_evolution(
    tickers: list[str], theme_name: str, keywords: str, since: str
) -> pd.DataFrame:
    """テーマ出現頻度を期間別に集計."""
    sql = f"""
    WITH yearly AS (
      SELECT TICKER,
             CASE
               WHEN SUBMISSION_DATE < DATE_SUB(CURRENT_DATE(), INTERVAL 24 MONTH) THEN 'prior'
               WHEN SUBMISSION_DATE < DATE_SUB(CURRENT_DATE(), INTERVAL 12 MONTH) THEN 'mid'
               ELSE 'recent'
             END AS period,
             COUNT(*) AS theme_chunks,
             COUNTIF(REGEXP_CONTAINS(CHUNK_TEXT, @positive_pattern)) AS positive_chunks
      FROM `{TABLE_TDNET}`
      WHERE TICKER IN UNNEST(@tickers)
        AND MAIN_CATEGORY IN UNNEST(@categories)
        AND SUBMISSION_DATE >= @since_date
        AND REGEXP_CONTAINS(CHUNK_TEXT, @theme_kw)
      GROUP BY TICKER, period
    )
    SELECT * FROM yearly ORDER BY TICKER, period
    """
    config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("tickers", "STRING", tickers),
        bigquery.ArrayQueryParameter("categories", "STRING", list(DOC_CATEGORIES)),
        bigquery.ScalarQueryParameter("since_date", "DATE", since),
        bigquery.ScalarQueryParameter("theme_kw", "STRING", keywords),
        bigquery.ScalarQueryParameter("positive_pattern", "STRING", POSITIVE_PATTERN),
    ])
    log.info("scan_evolution", theme=theme_name, tickers_count=len(tickers))
    df = _get_bq_client().query(sql, job_config=config).to_dataframe()
    if not df.empty:
        df["theme"] = theme_name
    log.info("scan_hits", theme=theme_name, tickers_hit=df["TICKER"].nunique() if not df.empty else 0)
    return df


def classify_signals(df: pd.DataFrame) -> pd.DataFrame:
    """期間別集計からシグナルを判定."""
    if df.empty:
        return pd.DataFrame()

    pivot_tc = df.pivot_table(
        index=["TICKER", "theme"], columns="period", values="theme_chunks", fill_value=0
    ).reset_index()
    pivot_pc = df.pivot_table(
        index=["TICKER", "theme"], columns="period", values="positive_chunks", fill_value=0
    ).reset_index()

    for col in ["prior", "mid", "recent"]:
        if col not in pivot_tc.columns:
            pivot_tc[col] = 0
        if col not in pivot_pc.columns:
            pivot_pc[col] = 0

    pivot_tc = pivot_tc.rename(columns={"prior": "tc_prior", "mid": "tc_mid", "recent": "tc_recent"})
    pivot_pc = pivot_pc.rename(columns={"prior": "pc_prior", "mid": "pc_mid", "recent": "pc_recent"})

    scored = pivot_tc.merge(pivot_pc, on=["TICKER", "theme"])

    def _classify(row: pd.Series) -> str:
        tc_p, tc_m, tc_r = row["tc_prior"], row["tc_mid"], row["tc_recent"]
        pc_r = row["pc_recent"]
        signals = []
        if tc_r > tc_m > tc_p and tc_p > 0:
            signals.append("漸増")
        if tc_p == 0 and (tc_m > 0 or tc_r > 0):
            signals.append("新出")
        if tc_r >= 3 and pc_r / tc_r >= 0.5:
            signals.append("好調転化")
        if tc_r >= 5 and tc_m > 0 and tc_r / tc_m >= 2:
            signals.append("急増")
        if not signals and tc_p >= 20 and tc_r >= 5 and pc_r >= 3:
            signals.append("本業好調")
        return ",".join(signals) if signals else ""

    scored["signal"] = scored.apply(_classify, axis=1)
    return scored[scored["signal"] != ""].copy()


def fetch_stock_info(tickers: list[str]) -> pd.DataFrame:
    """銘柄名 + 時価総額."""
    if not tickers:
        return pd.DataFrame(columns=["TICKER", "STOCK_NAME", "mcap_oku"])
    sql = f"""
    SELECT s.TICKER, s.STOCK_NAME, ROUND(yi.MARKET_CAP / 1e8, 0) AS mcap_oku
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


def fetch_verify_chunks(
    tickers: list[str], keywords: str, since: str
) -> pd.DataFrame:
    """裏どり用: テーマキーワード含有チャンクを全取得."""
    sql = f"""
    SELECT t.TICKER, s.STOCK_NAME, t.SUBMISSION_DATE, t.MAIN_CATEGORY, t.DOC_TITLE,
           SUBSTR(t.CHUNK_TEXT, 1, 500) AS chunk_preview,
           CASE WHEN REGEXP_CONTAINS(t.CHUNK_TEXT, @positive_pattern) THEN 'Y' ELSE 'N' END AS positive
    FROM `{TABLE_TDNET}` t
    LEFT JOIN `{TABLE_STOCK}` s ON t.TICKER = s.TICKER AND s.EXCHANGE = 'TSE'
    WHERE t.TICKER IN UNNEST(@tickers)
      AND t.MAIN_CATEGORY IN UNNEST(@categories)
      AND t.SUBMISSION_DATE >= @since_date
      AND REGEXP_CONTAINS(t.CHUNK_TEXT, @theme_kw)
    ORDER BY t.TICKER, t.SUBMISSION_DATE, t.MAIN_CATEGORY
    """
    config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("tickers", "STRING", tickers),
        bigquery.ArrayQueryParameter("categories", "STRING", list(DOC_CATEGORIES)),
        bigquery.ScalarQueryParameter("since_date", "DATE", since),
        bigquery.ScalarQueryParameter("theme_kw", "STRING", keywords),
        bigquery.ScalarQueryParameter("positive_pattern", "STRING", POSITIVE_PATTERN),
    ])
    return _get_bq_client().query(sql, job_config=config).to_dataframe()


def main() -> None:
    """メイン処理."""
    parser = argparse.ArgumentParser(description="セグメント変態検知 (093 パターン3)")
    parser.add_argument("--disclosure-dates", help="決算発表予定日 (カンマ区切り YYYY-MM-DD)")
    parser.add_argument("--tickers", help="直接指定する銘柄コード (カンマ区切り)")
    parser.add_argument("--themes", required=True, help="テーマ名 (カンマ区切り: semiconductor_ai,photonic_fusion)")
    parser.add_argument("--since", default="2020-04-01", help="集計開始日 (default: 2020-04-01)")
    parser.add_argument("--verify", help="裏どり対象TICKER (カンマ区切り)")
    parser.add_argument("--csv", help="CSV出力先 (省略時: C:\\tmp\\theme_transform_*.csv)")
    args = parser.parse_args()

    theme_names = [t.strip() for t in args.themes.split(",")]
    theme_keywords = {name: THEMES[name] for name in theme_names if name in THEMES}

    if not theme_keywords:
        print(f"未定義テーマ: {theme_names}. 利用可能: {list(THEMES.keys())}")
        sys.exit(1)

    # --- 裏どりモード ---
    if args.verify:
        tickers = [t.strip() for t in args.verify.split(",")]
        combined_kw = "|".join(theme_keywords.values())
        log.info("verify_mode", tickers=tickers)
        df = fetch_verify_chunks(tickers, combined_kw, args.since)
        out = Path(args.csv) if args.csv else Path(f"C:/tmp/theme_transform_verify_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}.csv")
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False, encoding="utf-8")
        print(f"{len(df)} chunks, {df['TICKER'].nunique()} tickers")
        print(f"CSV: {out}")
        return

    # --- スキャンモード ---
    if args.disclosure_dates:
        dates = [d.strip() for d in args.disclosure_dates.split(",")]
        cal_df = get_disclosure_tickers(dates)
        tickers = cal_df["TICKER"].tolist()
        cal_map = dict(zip(cal_df["TICKER"], cal_df["DISCLOSURE_DATE"].astype(str)))
    elif args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",")]
        cal_map = _fetch_calendar_dates(tickers)
    else:
        print("--disclosure-dates または --tickers を指定してください")
        sys.exit(1)

    if not tickers:
        print("対象銘柄なし")
        return

    all_results: list[pd.DataFrame] = []
    for name, kw in theme_keywords.items():
        df_raw = scan_theme_evolution(tickers, name, kw, args.since)
        if not df_raw.empty:
            all_results.append(df_raw)

    if not all_results:
        print("テーマヒットなし")
        return

    df_all = pd.concat(all_results, ignore_index=True)
    scored = classify_signals(df_all)

    if scored.empty:
        print("変態シグナル該当なし")
        return

    # 銘柄情報付加
    ticker_list = scored["TICKER"].tolist()
    df_info = fetch_stock_info(ticker_list)
    scored = scored.merge(df_info, on="TICKER", how="left")

    if cal_map:
        scored["DISCLOSURE_DATE"] = scored["TICKER"].map(cal_map).fillna("")

    # 出力
    out_cols = ["DISCLOSURE_DATE", "TICKER", "STOCK_NAME", "theme", "signal",
                "tc_prior", "pc_prior", "tc_mid", "pc_mid", "tc_recent", "pc_recent", "mcap_oku"]
    out_cols = [c for c in out_cols if c in scored.columns]
    sort_cols = [c for c in ["theme", "DISCLOSURE_DATE", "TICKER"] if c in scored.columns]
    scored = scored.sort_values(sort_cols)

    print(f"=== 変態シグナル検出結果 ===")
    print(f"対象: {len(tickers)} 社, テーマ: {list(theme_keywords.keys())}")
    print(f"シグナルあり: {len(scored)} 社")
    print()
    print(scored[out_cols].to_string(index=False))

    # CSV
    if args.csv:
        out_path = Path(args.csv)
    else:
        ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        out_path = Path(f"C:/tmp/theme_transform_{ts}.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    scored[out_cols].to_csv(out_path, index=False, encoding="utf-8")
    print(f"\nCSV: {out_path}")


if __name__ == "__main__":
    main()

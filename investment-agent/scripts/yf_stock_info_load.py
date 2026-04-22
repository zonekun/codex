"""yfinance Ticker.info から銘柄属性・バリュエーション情報を取得して BQ に追加.

対応環境:
    - ローカルPC
    - Google Colab（個人ユース）
    - Google Colab Enterprise / Cloud Run Job

使い方:
    PYTHONUTF8=1 python scripts/yf_stock_info_load.py           # 全銘柄取得
    PYTHONUTF8=1 python scripts/yf_stock_info_load.py --ticker 7203 9984  # 指定銘柄のみ
    PYTHONUTF8=1 python scripts/yf_stock_info_load.py --dry-run # BQ 書き込みなしで動作確認
"""

import argparse
import os
import random
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta

import pandas as pd
import yfinance as yf
from curl_cffi import requests as curl_requests
from google.cloud import bigquery

# notify.py（同ディレクトリ）から共通メール・ログ機能をインポート
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import send_mail, LogCapture

# ==========================================
# 設定
# ==========================================

PROJECT = "gmailpj-357912"
DATASET = "STOCK"
TABLE = "YF_STOCK_INFO"
BQ_TABLE_ID = f"{PROJECT}.{DATASET}.{TABLE}"
KEY_FILE = "keys/gcp-service-account.json"

BATCH_SIZE = 50          # 1バッチあたりの銘柄数
SLEEP_MIN = 0.3          # 銘柄間スリープ最小（秒）
SLEEP_MAX = 0.8          # 銘柄間スリープ最大（秒）
BULK_INTERVAL = 200      # この件数ごとに長めのスリープ
BULK_SLEEP_MIN = 3.0     # バルクスリープ最小（秒）
BULK_SLEEP_MAX = 6.0     # バルクスリープ最大（秒）
REQUEST_TIMEOUT = 15     # yfinance リクエストタイムアウト（秒）

JST = timezone(timedelta(hours=+9), "JST")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# yfinance info キー → BQ カラム名 のマッピング
# NO2 (TICKER) は symbol から加工するため含めない
YF_KEY_TO_BQ: list[tuple[str, str]] = [
    ("symbol", "SYMBOL"),
    # TICKER は symbol から .T を除去して生成
    ("longName", "LONG_NAME"),
    ("city", "CITY"),
    ("zip", "ZIP"),
    ("sector", "SECTOR"),
    ("website", "WEBSITE"),
    ("fullTimeEmployees", "FULL_TIME_EMPLOYEES"),
    ("industryKey", "INDUSTRY_KEY"),
    ("industryDisp", "INDUSTRY_DISP"),
    ("sectorKey", "SECTOR_KEY"),
    ("sectorDisp", "SECTOR_DISP"),
    ("irWebsite", "IR_WEBSITE"),
    ("currentPrice", "CURRENT_PRICE"),
    ("marketCap", "MARKET_CAP"),
    ("enterpriseValue", "ENTERPRISE_VALUE"),
    ("sharesOutstanding", "SHARES_OUTSTANDING"),
    ("beta", "BETA"),
    ("floatShares", "FLOAT_SHARES"),
    ("impliedSharesOutstanding", "IMPLIED_SHARES_OUTSTANDING"),
    ("heldPercentInstitutions", "HELD_PERCENT_INSTITUTIONS"),
    ("trailingPE", "TRAILING_PE"),
    ("forwardPE", "FORWARD_PE"),
    ("priceToBook", "PRICE_TO_BOOK"),
    ("priceToSalesTrailing12Months", "PRICE_TO_SALES_TRAILING_12M"),
    ("enterpriseToEbitda", "ENTERPRISE_TO_EBITDA"),
    ("trailingPegRatio", "TRAILING_PEG_RATIO"),
    ("enterpriseToRevenue", "ENTERPRISE_TO_REVENUE"),
    ("ebitda", "EBITDA"),
    ("totalDebt", "TOTAL_DEBT"),
    ("grossProfits", "GROSS_PROFITS"),
    ("operatingCashflow", "OPERATING_CASHFLOW"),
    ("earningsGrowth", "EARNINGS_GROWTH"),
    ("grossMargins", "GROSS_MARGINS"),
    ("ebitdaMargins", "EBITDA_MARGINS"),
    ("operatingMargins", "OPERATING_MARGINS"),
    ("profitMargins", "PROFIT_MARGINS"),
    ("forwardEps", "FORWARD_EPS"),
    ("epsForward", "EPS_FORWARD"),
    ("totalRevenue", "TOTAL_REVENUE"),
    ("revenueGrowth", "REVENUE_GROWTH"),
    ("returnOnAssets", "RETURN_ON_ASSETS"),
    ("returnOnEquity", "RETURN_ON_EQUITY"),
    ("debtToEquity", "DEBT_TO_EQUITY"),
    ("totalCash", "TOTAL_CASH"),
    ("freeCashflow", "FREE_CASHFLOW"),
    ("bookValue", "BOOK_VALUE"),
    ("trailingEps", "TRAILING_EPS"),
    ("dividendYield", "DIVIDEND_YIELD"),
    ("dividendRate", "DIVIDEND_RATE"),
    ("payoutRatio", "PAYOUT_RATIO"),
    ("exDividendDate", "EX_DIVIDEND_DATE"),
    ("lastDividendValue", "LAST_DIVIDEND_VALUE"),
    ("lastDividendDate", "LAST_DIVIDEND_DATE"),
    ("targetMeanPrice", "TARGET_MEAN_PRICE"),
    ("recommendationKey", "RECOMMENDATION_KEY"),
    ("overallRisk", "OVERALL_RISK"),
    ("recommendationMean", "RECOMMENDATION_MEAN"),
    ("numberOfAnalystOpinions", "NUMBER_OF_ANALYST_OPINIONS"),
    ("averageAnalystRating", "AVERAGE_ANALYST_RATING"),
    ("52WeekChange", "WEEK_52_CHANGE"),
    ("lastFiscalYearEnd", "LAST_FISCAL_YEAR_END"),
    ("nextFiscalYearEnd", "NEXT_FISCAL_YEAR_END"),
    ("mostRecentQuarter", "MOST_RECENT_QUARTER"),
    ("earningsTimestamp", "EARNINGS_TIMESTAMP"),
    ("earningsTimestampStart", "EARNINGS_TIMESTAMP_START"),
    ("earningsTimestampEnd", "EARNINGS_TIMESTAMP_END"),
    ("exchange", "EXCHANGE"),
    ("quoteType", "QUOTE_TYPE"),
]

# UNIXタイムスタンプ → DATE に変換するカラム
UNIX_TO_DATE_COLS = {
    "EX_DIVIDEND_DATE", "LAST_DIVIDEND_DATE",
    "LAST_FISCAL_YEAR_END", "NEXT_FISCAL_YEAR_END", "MOST_RECENT_QUARTER",
}

# UNIXタイムスタンプ → DATETIME(JST) に変換するカラム
UNIX_TO_DATETIME_COLS = {
    "EARNINGS_TIMESTAMP", "EARNINGS_TIMESTAMP_START", "EARNINGS_TIMESTAMP_END",
}

# BQ スキーマ定義
SCHEMA = [
    bigquery.SchemaField("SYMBOL", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("TICKER", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("LONG_NAME", "STRING"),
    bigquery.SchemaField("CITY", "STRING"),
    bigquery.SchemaField("ZIP", "STRING"),
    bigquery.SchemaField("SECTOR", "STRING"),
    bigquery.SchemaField("WEBSITE", "STRING"),
    bigquery.SchemaField("FULL_TIME_EMPLOYEES", "INTEGER"),
    bigquery.SchemaField("INDUSTRY_KEY", "STRING"),
    bigquery.SchemaField("INDUSTRY_DISP", "STRING"),
    bigquery.SchemaField("SECTOR_KEY", "STRING"),
    bigquery.SchemaField("SECTOR_DISP", "STRING"),
    bigquery.SchemaField("IR_WEBSITE", "STRING"),
    bigquery.SchemaField("CURRENT_PRICE", "FLOAT64"),
    bigquery.SchemaField("MARKET_CAP", "INTEGER"),
    bigquery.SchemaField("ENTERPRISE_VALUE", "INTEGER"),
    bigquery.SchemaField("SHARES_OUTSTANDING", "INTEGER"),
    bigquery.SchemaField("BETA", "FLOAT64"),
    bigquery.SchemaField("FLOAT_SHARES", "INTEGER"),
    bigquery.SchemaField("IMPLIED_SHARES_OUTSTANDING", "INTEGER"),
    bigquery.SchemaField("HELD_PERCENT_INSTITUTIONS", "FLOAT64"),
    bigquery.SchemaField("TRAILING_PE", "FLOAT64"),
    bigquery.SchemaField("FORWARD_PE", "FLOAT64"),
    bigquery.SchemaField("PRICE_TO_BOOK", "FLOAT64"),
    bigquery.SchemaField("PRICE_TO_SALES_TRAILING_12M", "FLOAT64"),
    bigquery.SchemaField("ENTERPRISE_TO_EBITDA", "FLOAT64"),
    bigquery.SchemaField("TRAILING_PEG_RATIO", "FLOAT64"),
    bigquery.SchemaField("ENTERPRISE_TO_REVENUE", "FLOAT64"),
    bigquery.SchemaField("EBITDA", "INTEGER"),
    bigquery.SchemaField("TOTAL_DEBT", "INTEGER"),
    bigquery.SchemaField("GROSS_PROFITS", "INTEGER"),
    bigquery.SchemaField("OPERATING_CASHFLOW", "INTEGER"),
    bigquery.SchemaField("EARNINGS_GROWTH", "FLOAT64"),
    bigquery.SchemaField("GROSS_MARGINS", "FLOAT64"),
    bigquery.SchemaField("EBITDA_MARGINS", "FLOAT64"),
    bigquery.SchemaField("OPERATING_MARGINS", "FLOAT64"),
    bigquery.SchemaField("PROFIT_MARGINS", "FLOAT64"),
    bigquery.SchemaField("FORWARD_EPS", "FLOAT64"),
    bigquery.SchemaField("EPS_FORWARD", "FLOAT64"),
    bigquery.SchemaField("TOTAL_REVENUE", "INTEGER"),
    bigquery.SchemaField("REVENUE_GROWTH", "FLOAT64"),
    bigquery.SchemaField("RETURN_ON_ASSETS", "FLOAT64"),
    bigquery.SchemaField("RETURN_ON_EQUITY", "FLOAT64"),
    bigquery.SchemaField("DEBT_TO_EQUITY", "FLOAT64"),
    bigquery.SchemaField("TOTAL_CASH", "INTEGER"),
    bigquery.SchemaField("FREE_CASHFLOW", "INTEGER"),
    bigquery.SchemaField("BOOK_VALUE", "FLOAT64"),
    bigquery.SchemaField("TRAILING_EPS", "FLOAT64"),
    bigquery.SchemaField("DIVIDEND_YIELD", "FLOAT64"),
    bigquery.SchemaField("DIVIDEND_RATE", "FLOAT64"),
    bigquery.SchemaField("PAYOUT_RATIO", "FLOAT64"),
    bigquery.SchemaField("EX_DIVIDEND_DATE", "DATE"),
    bigquery.SchemaField("LAST_DIVIDEND_VALUE", "FLOAT64"),
    bigquery.SchemaField("LAST_DIVIDEND_DATE", "DATE"),
    bigquery.SchemaField("TARGET_MEAN_PRICE", "FLOAT64"),
    bigquery.SchemaField("RECOMMENDATION_KEY", "STRING"),
    bigquery.SchemaField("OVERALL_RISK", "INTEGER"),
    bigquery.SchemaField("RECOMMENDATION_MEAN", "FLOAT64"),
    bigquery.SchemaField("NUMBER_OF_ANALYST_OPINIONS", "INTEGER"),
    bigquery.SchemaField("AVERAGE_ANALYST_RATING", "STRING"),
    bigquery.SchemaField("WEEK_52_CHANGE", "FLOAT64"),
    bigquery.SchemaField("LAST_FISCAL_YEAR_END", "DATE"),
    bigquery.SchemaField("NEXT_FISCAL_YEAR_END", "DATE"),
    bigquery.SchemaField("MOST_RECENT_QUARTER", "DATE"),
    bigquery.SchemaField("EARNINGS_TIMESTAMP", "DATETIME"),
    bigquery.SchemaField("EARNINGS_TIMESTAMP_START", "DATETIME"),
    bigquery.SchemaField("EARNINGS_TIMESTAMP_END", "DATETIME"),
    bigquery.SchemaField("EXCHANGE", "STRING"),
    bigquery.SchemaField("QUOTE_TYPE", "STRING"),
    bigquery.SchemaField("LOADED_DATE", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("LOADED_AT", "DATETIME", mode="REQUIRED"),
]


# ==========================================
# 0. 実行環境判別
# ==========================================

def detect_runtime() -> str:
    """実行環境を自動判別する.

    Returns:
        "colab_personal" | "colab_enterprise" | "cloudrun" | "local"
    """
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    try:
        import google.colab  # noqa: F401
        if os.environ.get("GOOGLE_CLOUD_PROJECT"):
            return "colab_enterprise"
        return "colab_personal"
    except ImportError:
        return "local"


RUNTIME: str = detect_runtime()


# ==========================================
# 1. 認証・BQ クライアント
# ==========================================

def setup_environment() -> None:
    """実行環境に応じた GCP 認証を行う."""
    print(f"実行環境: {RUNTIME}")
    if RUNTIME == "colab_personal":
        from google.colab import auth
        auth.authenticate_user()


def get_bq_client() -> bigquery.Client:
    """実行環境に応じた BigQuery クライアントを返す."""
    if RUNTIME == "local":
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(KEY_FILE)
        return bigquery.Client(project=PROJECT, credentials=creds)
    else:
        return bigquery.Client(project=PROJECT)


# ==========================================
# 2. 銘柄コードの取得
# ==========================================

def fetch_all_tickers(client: bigquery.Client) -> list[str]:
    """BQ STOCK_CODE_LIST から全上場銘柄コードを取得する."""
    sql = f"""
        SELECT DISTINCT TICKER
        FROM `{PROJECT}.{DATASET}.STOCK_CODE_LIST`
        WHERE EXCHANGE IN ('TSE', 'NSE', 'SSE', 'FSE')
        ORDER BY TICKER
    """
    df = client.query(sql).to_dataframe()
    return df["TICKER"].astype(str).tolist()


# ==========================================
# 3. yfinance から銘柄情報を取得
# ==========================================

def _convert_unix_to_date(val: int | float | None) -> "datetime.date | None":
    """UNIXタイムスタンプ → date に変換する."""
    if val is None or pd.isna(val):
        return None
    try:
        return datetime.fromtimestamp(int(val), tz=JST).date()
    except (ValueError, OSError, OverflowError):
        return None


def _convert_unix_to_datetime_jst(val: int | float | None) -> "datetime | None":
    """UNIXタイムスタンプ → datetime(JST, tzinfo除去) に変換する."""
    if val is None or pd.isna(val):
        return None
    try:
        return datetime.fromtimestamp(int(val), tz=JST).replace(tzinfo=None)
    except (ValueError, OSError, OverflowError):
        return None


def fetch_stock_info(
    ticker_4: str,
    session: curl_requests.Session,
) -> dict | None:
    """4桁コードの銘柄から Ticker.info を取得し、BQカラム名のdictを返す.

    Args:
        ticker_4: 4桁銘柄コード
        session: curl_cffi セッション

    Returns:
        BQカラム名をキーとしたdict。取得失敗時は None
    """
    yf_ticker = ticker_4 + ".T"
    t = yf.Ticker(yf_ticker, session=session)
    info = t.info

    if not info or info.get("quoteType") is None:
        return None

    row: dict = {}

    # マッピングに基づいて値を取得
    for yf_key, bq_col in YF_KEY_TO_BQ:
        row[bq_col] = info.get(yf_key)

    # NO2: TICKER — symbol から .T を除去して4桁コード抽出
    symbol = info.get("symbol", yf_ticker)
    row["TICKER"] = symbol.split(".")[0]

    # UNIXタイムスタンプ → DATE 変換
    for col in UNIX_TO_DATE_COLS:
        row[col] = _convert_unix_to_date(row.get(col))

    # UNIXタイムスタンプ → DATETIME(JST) 変換
    for col in UNIX_TO_DATETIME_COLS:
        row[col] = _convert_unix_to_datetime_jst(row.get(col))

    return row


# ==========================================
# 4. BQ へのロード
# ==========================================

def load_to_bq(client: bigquery.Client, df: pd.DataFrame) -> int:
    """DataFrame を YF_STOCK_INFO テーブルに WRITE_APPEND でロードする.

    Returns:
        ロードした行数
    """
    job_config = bigquery.LoadJobConfig(
        schema=SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    print(f"BQ {BQ_TABLE_ID} に WRITE_APPEND でロード中 ({len(df)} 件)...")
    job = client.load_table_from_dataframe(df, BQ_TABLE_ID, job_config=job_config)
    job.result()
    print(f"BQ ロード完了: {job.output_rows} 行を追加")
    return job.output_rows


# ==========================================
# 5. メイン処理
# ==========================================

def main() -> None:
    """エントリポイント."""
    parser = argparse.ArgumentParser(
        description="yfinance Ticker.info → BQ YF_STOCK_INFO ロード",
    )
    parser.add_argument("--ticker", nargs="+", help="4桁銘柄コード（省略時は全銘柄）")
    parser.add_argument("--dry-run", action="store_true", help="BQ 書き込みなしで動作確認")
    args = parser.parse_args()

    start_time = datetime.now(JST)
    log_cap = LogCapture()
    log_cap.start()

    try:
        setup_environment()
        client = get_bq_client()

        # 銘柄リスト取得
        if args.ticker:
            tickers = [str(t).zfill(4) for t in args.ticker]
            label = f"指定銘柄 {len(tickers)} 件"
        else:
            print("BQ STOCK_CODE_LIST から全銘柄を取得中...")
            tickers = fetch_all_tickers(client)
            label = f"全銘柄 {len(tickers)} 件"

        print(f"対象: {label}")

        # curl_cffi セッション（TLSフィンガープリント偽装）
        session = curl_requests.Session(impersonate="chrome124")
        session.headers.update({"User-Agent": _UA})
        session.timeout = REQUEST_TIMEOUT

        # LOADED_DATE / LOADED_AT（全行共通）
        now_jst = datetime.now(JST)
        loaded_date = now_jst.date()
        loaded_at = now_jst.replace(tzinfo=None)  # DATETIME型（タイムゾーンなし・JST）

        all_rows: list[dict] = []
        ok_count = 0
        skip_count = 0
        err_count = 0

        for i, ticker in enumerate(tickers, 1):
            try:
                row = fetch_stock_info(ticker, session)
                if row is None:
                    skip_count += 1
                else:
                    row["LOADED_DATE"] = loaded_date
                    row["LOADED_AT"] = loaded_at
                    all_rows.append(row)
                    ok_count += 1
            except Exception as e:
                err_count += 1
                print(f"  [{i}/{len(tickers)}] {ticker}: エラー {e}")

            if i % 100 == 0:
                print(
                    f"  進捗: {i}/{len(tickers)}"
                    f"（取得={ok_count}, スキップ={skip_count}, エラー={err_count}）"
                )

            # 銘柄間スリープ
            time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

            # BULK_INTERVAL 件ごとに長めのスリープ
            if i % BULK_INTERVAL == 0 and i < len(tickers):
                bulk_sleep = random.uniform(BULK_SLEEP_MIN, BULK_SLEEP_MAX)
                print(f"  [{i}/{len(tickers)}] {BULK_INTERVAL}件完了 → {bulk_sleep:.1f}s 休憩中...")
                time.sleep(bulk_sleep)

        if not all_rows:
            print("取得データなし。終了。")
            log_cap.stop()
            return

        # DataFrame 構築
        result_df = pd.DataFrame(all_rows)

        # Infinity / -Infinity をNaNに置換（yfinanceが一部銘柄で返す）
        import numpy as np
        result_df = result_df.replace([np.inf, -np.inf, "Infinity", "-Infinity"], np.nan)

        # 整数型カラムの型変換（NaN混在対策で Int64）
        int_cols = [
            "FULL_TIME_EMPLOYEES", "MARKET_CAP", "ENTERPRISE_VALUE",
            "SHARES_OUTSTANDING", "FLOAT_SHARES", "IMPLIED_SHARES_OUTSTANDING",
            "EBITDA", "TOTAL_DEBT", "GROSS_PROFITS", "OPERATING_CASHFLOW",
            "TOTAL_REVENUE", "TOTAL_CASH", "FREE_CASHFLOW",
            "OVERALL_RISK", "NUMBER_OF_ANALYST_OPINIONS",
        ]
        for col in int_cols:
            if col in result_df.columns:
                result_df[col] = pd.to_numeric(result_df[col], errors="coerce").astype("Int64")

        # DATE カラムの型変換
        for col in UNIX_TO_DATE_COLS:
            if col in result_df.columns:
                result_df[col] = pd.to_datetime(result_df[col], errors="coerce").dt.date

        # DATETIME カラム — すでに変換済みだが念のため
        for col in UNIX_TO_DATETIME_COLS:
            if col in result_df.columns:
                result_df[col] = pd.to_datetime(result_df[col], errors="coerce")

        print(f"\n=== 集計 ===")
        print(f"  取得銘柄数  : {ok_count}")
        print(f"  スキップ    : {skip_count}")
        print(f"  エラー      : {err_count}")
        print(f"  総レコード数: {len(result_df)}")
        print(result_df[["TICKER", "LONG_NAME", "CURRENT_PRICE", "MARKET_CAP"]].head(10).to_string(index=False))

        if args.dry_run:
            print("\n[dry-run] BQ 書き込みをスキップ")
            log_cap.stop()
            return

        bq_rows = load_to_bq(client, result_df)

        log_cap.stop()

        elapsed = datetime.now(JST) - start_time
        summary = (
            f"[YF_STOCK_INFO] 完了\n"
            f"対象: {label}\n"
            f"取得: {ok_count} / スキップ: {skip_count} / エラー: {err_count}\n"
            f"BQ追加: {bq_rows} 行\n"
            f"所要時間: {elapsed}"
        )
        print(f"\n{summary}")

    except Exception as e:
        log_text = log_cap.stop()
        send_mail(
            "[YF_STOCK_INFO] エラー",
            f"エラーが発生しました: {e}\n\n{traceback.format_exc()}",
            attachment_text=log_text,
            attachment_name="yf_stock_info_log.txt",
        )
        raise


if __name__ == "__main__":
    main()

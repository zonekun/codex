"""権利付き最終日・権利落ち日・配当履歴を yfinance から取得して BQ に格納.

対応環境:
    - ローカルPC
    - Google Colab（個人ユース）
    - Google Colab Enterprise / Cloud Run Job

使い方:
    PYTHONUTF8=1 python scripts/dividend_date_load.py           # 全銘柄取得（BQ STOCK_CODE_LIST から）
    PYTHONUTF8=1 python scripts/dividend_date_load.py --ticker 7203 9984  # 指定銘柄のみ
    PYTHONUTF8=1 python scripts/dividend_date_load.py --dry-run # BQ 書き込みなしで動作確認
"""

import argparse
import os
import random
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP

import jpholiday
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

PROJECT   = "gmailpj-357912"
DATASET   = "STOCK"
TABLE     = "DIVIDEND_DATE"
KEY_FILE  = "keys/gcp-service-account.json"
SLEEP_MIN      = 0.1    # 銘柄間スリープ最小（秒）
SLEEP_MAX      = 0.3    # 銘柄間スリープ最大（秒）
BULK_INTERVAL  = 200    # この件数ごとに長めのスリープを挟む
BULK_SLEEP_MIN = 2.0    # バルクスリープ最小（秒）
BULK_SLEEP_MAX = 5.0    # バルクスリープ最大（秒）
REQUEST_TIMEOUT = 15    # yfinance リクエストタイムアウト（秒）
JST            = timezone(timedelta(hours=+9), "JST")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


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
    # colab_enterprise / cloudrun は ADC が自動的に有効


def get_bq_client() -> bigquery.Client:
    """実行環境に応じた BigQuery クライアントを返す."""
    if RUNTIME == "local":
        # ローカルPC: サービスアカウントキーで明示認証
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(KEY_FILE)
        return bigquery.Client(project=PROJECT, credentials=creds)
    else:
        # Cloud Run / Colab: ADC を使用（サービスアカウントが自動適用）
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
# 3. 前営業日の算出（権利落ち日 → 権利付き最終日）
# ==========================================

def prev_business_day(d: "datetime.date") -> "datetime.date":
    """指定日の前営業日を返す（土日・日本祝日を除く）."""
    from datetime import timedelta as td
    prev = d - td(days=1)
    while prev.weekday() >= 5 or jpholiday.is_holiday(prev):
        prev -= td(days=1)
    return prev


# ==========================================
# 4. yfinance からデータ取得
# ==========================================

def fetch_dividend_dates(ticker_4: str, session: curl_requests.Session) -> pd.DataFrame:
    """4桁コードの銘柄から権利落ち日・配当を取得する.

    Args:
        ticker_4: 4桁銘柄コード
        session:  curl_cffi セッション（Chrome TLS フィンガープリント偽装）

    Returns:
        DataFrame with columns: TICKER, CUM_DATE, EX_DATE, DIVIDEND
        データなし（無配等）の場合は空 DataFrame を返す
    """
    yf_ticker = ticker_4 + ".T"
    t = yf.Ticker(yf_ticker, session=session)
    divs = t.dividends  # DatetimeIndex（権利落ち日）, Series（配当額）

    if divs.empty:
        return pd.DataFrame()

    rows = []
    for ex_ts, amount in divs.items():
        ex_date  = ex_ts.date()
        cum_date = prev_business_day(ex_date)
        dividend = float(
            Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )
        rows.append({
            "TICKER":   ticker_4,
            "CUM_DATE": cum_date,
            "EX_DATE":  ex_date,
            "DIVIDEND": dividend,
        })

    return pd.DataFrame(rows)


# ==========================================
# 5. BQ へのロード
# ==========================================

SCHEMA = [
    bigquery.SchemaField("TICKER",    "STRING",   mode="REQUIRED"),
    bigquery.SchemaField("CUM_DATE",  "DATE",     mode="REQUIRED"),
    bigquery.SchemaField("EX_DATE",   "DATE",     mode="REQUIRED"),
    bigquery.SchemaField("DIVIDEND",  "NUMERIC"),
    bigquery.SchemaField("LOADED_AT", "DATETIME"),
]


def load_to_bq(client: bigquery.Client, df: pd.DataFrame) -> None:
    """DataFrame を DIVIDEND_DATE テーブルに MERGE (upsert) でロードする.

    キー: (TICKER, EX_DATE)
    - MATCHED     → CUM_DATE / DIVIDEND / LOADED_AT を更新
    - NOT MATCHED → 新規 INSERT
    - 削除は行わない（yfinance が過去データ提供を停止しても既存行を保持）

    処理フロー:
        1. 一時テーブル（_tmp_dividend_date_<timestamp>）に WRITE_TRUNCATE でロード
        2. MERGE SQL で本テーブルに upsert
        3. 一時テーブルを削除
    """
    now_jst = datetime.now(JST).replace(tzinfo=None)  # DATETIME 型（タイムゾーンなし・JST）
    df = df.copy()
    df["LOADED_AT"] = now_jst

    # pyarrow 型変換
    df["CUM_DATE"] = pd.to_datetime(df["CUM_DATE"]).dt.date
    df["EX_DATE"]  = pd.to_datetime(df["EX_DATE"]).dt.date
    # NUMERIC 型: float → Decimal（pyarrow は float を NUMERIC に直接変換不可）
    df["DIVIDEND"] = df["DIVIDEND"].apply(
        lambda x: Decimal(str(x)) if pd.notna(x) else None
    )

    # 一時テーブル名（タイムスタンプ付きで衝突を回避）
    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    tmp_table = f"{PROJECT}.{DATASET}._tmp_{TABLE.lower()}_{ts}"
    main_table = f"{PROJECT}.{DATASET}.{TABLE}"

    # --- Step 1: 一時テーブルに全件ロード ---
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        schema=SCHEMA,
    )
    print(f"一時テーブル {tmp_table} にロード中 ({len(df)} 件)...")
    job = client.load_table_from_dataframe(df, tmp_table, job_config=job_config)
    job.result()

    # --- Step 2: MERGE で本テーブルに upsert ---
    merge_sql = f"""
    MERGE `{main_table}` T
    USING `{tmp_table}` S
    ON T.TICKER = S.TICKER AND T.EX_DATE = S.EX_DATE
    WHEN MATCHED THEN
      UPDATE SET
        T.CUM_DATE   = S.CUM_DATE,
        T.DIVIDEND   = S.DIVIDEND,
        T.LOADED_AT  = S.LOADED_AT
    WHEN NOT MATCHED THEN
      INSERT (TICKER, CUM_DATE, EX_DATE, DIVIDEND, LOADED_AT)
      VALUES (S.TICKER, S.CUM_DATE, S.EX_DATE, S.DIVIDEND, S.LOADED_AT)
    """
    print("MERGE (upsert) 実行中...")
    merge_job = client.query(merge_sql)
    merge_result = merge_job.result()
    print(f"MERGE 完了: {merge_result.num_dml_affected_rows} 行に影響")

    # --- Step 3: 一時テーブル削除 ---
    client.delete_table(tmp_table, not_found_ok=True)
    print(f"一時テーブル {tmp_table} 削除完了")
    print(f"BQ upsert 完了: {len(df)} 件処理")


# ==========================================
# 6. メイン処理
# ==========================================

def main() -> None:
    parser = argparse.ArgumentParser(description="権利付き最終日・権利落ち日を BQ に格納")
    parser.add_argument("--ticker", nargs="+", help="4桁銘柄コード（省略時は全銘柄）")
    parser.add_argument("--dry-run", action="store_true", help="BQ 書き込みなしで動作確認")
    args = parser.parse_args()

    start_time = datetime.now(JST)
    log_cap    = LogCapture()
    log_cap.start()

    try:
        setup_environment()
        client = get_bq_client()

        if args.ticker:
            tickers = [str(t).zfill(4) for t in args.ticker]
            label   = f"指定銘柄 {len(tickers)} 件"
        else:
            print("BQ STOCK_CODE_LIST から全銘柄を取得中...")
            tickers = fetch_all_tickers(client)
            label   = f"全銘柄 {len(tickers)} 件"

        session = curl_requests.Session(impersonate="chrome124")
        session.headers.update({"User-Agent": _UA})
        session.timeout = REQUEST_TIMEOUT

        all_rows:   list[pd.DataFrame] = []
        ok_count    = 0
        skip_count  = 0
        err_count   = 0

        for i, ticker in enumerate(tickers, 1):
            try:
                df = fetch_dividend_dates(ticker, session)
                if df.empty:
                    skip_count += 1
                else:
                    all_rows.append(df)
                    ok_count += 1
                    if len(tickers) <= 10:
                        print(f"  [{i}/{len(tickers)}] {ticker}: {len(df)} 件")
            except Exception as e:
                err_count += 1
                print(f"  [{i}/{len(tickers)}] {ticker}: エラー {e}")

            if i % 100 == 0:
                print(
                    f"  進捗: {i}/{len(tickers)}"
                    f"（取得あり={ok_count}, 無配スキップ={skip_count}, エラー={err_count}）"
                )

            # 銘柄間: ランダムスリープ
            time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

            # BULK_INTERVAL 件ごと: 長めのスリープ（ban 対策）
            if i % BULK_INTERVAL == 0 and i < len(tickers):
                bulk_sleep = random.uniform(BULK_SLEEP_MIN, BULK_SLEEP_MAX)
                print(f"  [{i}/{len(tickers)}] {BULK_INTERVAL}件完了 → {bulk_sleep:.1f}s 休憩中...")
                time.sleep(bulk_sleep)

        if not all_rows:
            print("取得データなし。終了。")
            log_cap.stop()
            return

        result_df = pd.concat(all_rows, ignore_index=True)
        result_df = result_df.sort_values(["TICKER", "EX_DATE"]).reset_index(drop=True)

        print(f"\n=== 集計 ===")
        print(f"  取得銘柄数  : {ok_count}")
        print(f"  無配スキップ: {skip_count}")
        print(f"  エラー      : {err_count}")
        print(f"  総レコード数: {len(result_df)}")
        print(result_df.head(10).to_string(index=False))

        if args.dry_run:
            print("\n[dry-run] BQ 書き込みをスキップ")
            log_cap.stop()
            return

        print(f"\nBQ {PROJECT}.{DATASET}.{TABLE} に WRITE_TRUNCATE でロード中...")
        load_to_bq(client, result_df)

        log_cap.stop()

    except Exception as e:
        log_text = log_cap.stop()
        send_mail(
            "[DIVIDEND_DATE] エラー",
            f"エラーが発生しました: {e}\n\n{traceback.format_exc()}",
            attachment_text=log_text,
            attachment_name="dividend_date_log.txt",
        )
        raise


if __name__ == "__main__":
    main()

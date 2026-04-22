"""yfinance 前場スナップショット → BigQuery STOCK.STOCK_PRICE_YF_AM ロード.

前場終了後（11:45 JST）に起動し、当日の前場 OHLCV を取得して BQ に格納する。
後場開始（12:30）前に完了させるため、4並列タスクで ticker を分割して処理する。

対応環境:
    - ローカルPC
    - Cloud Run Job（4並列タスク）

使い方:
    PYTHONUTF8=1 python scripts/stock_price_yf_am_load.py              # 当日分
    PYTHONUTF8=1 python scripts/stock_price_yf_am_load.py --dry-run    # BQ 書き込みなし
"""

import os
import random
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta

import jpholiday
import pandas as pd
import yfinance as yf
from google.cloud import bigquery

# notify.py（同ディレクトリ）から共通メール・ログ機能をインポート
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import send_mail, LogCapture

# ==========================================
# 0. 実行環境判別
# ==========================================

def detect_runtime() -> str:
    """実行環境を自動判別する.

    Returns:
        "cloudrun" | "colab_personal" | "colab_enterprise" | "local"
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
# 1. 設定
# ==========================================

PROJECT  = "gmailpj-357912"
DATASET  = "STOCK"
TABLE    = "STOCK_PRICE_YF_AM"
BQ_TABLE_ID = f"{PROJECT}.{DATASET}.{TABLE}"
KEY_FILE = "keys/gcp-service-account.json"

# yfinance バッチ設定
BATCH_SIZE = 50
SLEEP_MIN  = 1
SLEEP_MAX  = 3

JST = timezone(timedelta(hours=+9), "JST")

# jpholiday が対応しない特別休日（大晦日・年始休暇）
import datetime as _dt
SPECIAL_DATES = {
    _dt.date(2025, 12, 31),  # 大晦日
    _dt.date(2026,  1,  2),  # 年始休暇
    _dt.date(2026,  1,  3),  # 年始休暇
}

EXCHANGE_SUFFIX_MAP = {"TSE": ".T", "NSE": ".N", "SSE": ".S", "FSE": ".F"}

# Cloud Run タスク分割（4並列）
TASK_INDEX = int(os.environ.get("CLOUD_RUN_TASK_INDEX", 0))
TASK_COUNT = int(os.environ.get("CLOUD_RUN_TASK_COUNT", 1))

# ==========================================
# 2. 認証・BQ クライアント
# ==========================================

def get_bq_client() -> bigquery.Client:
    """実行環境に応じた BigQuery クライアントを返す."""
    if RUNTIME == "local":
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(KEY_FILE)
        return bigquery.Client(project=PROJECT, credentials=creds)
    else:
        return bigquery.Client(project=PROJECT)


# ==========================================
# 3. 取引日判定
# ==========================================

def is_trading_day(d: _dt.date) -> bool:
    """日本の取引日かどうかを判定する."""
    if d.weekday() >= 5:
        return False
    if jpholiday.is_holiday(d) or d in SPECIAL_DATES:
        return False
    return True


# ==========================================
# 4. 銘柄一覧取得
# ==========================================

def get_tickers(client: bigquery.Client) -> list[str]:
    """BigQuery STOCK_CODE_LIST から yfinance 用ティッカーリストを取得する.

    TICKER は 4桁数字 + 末尾アルファベット（174A 等）を含む。
    """
    query = f"""
        SELECT TICKER, EXCHANGE
        FROM `{PROJECT}.{DATASET}.STOCK_CODE_LIST`
        WHERE EXCHANGE IN ('TSE', 'NSE', 'SSE', 'FSE')
        ORDER BY TICKER
    """
    rows = client.query(query).result()
    tickers = [
        f"{row.TICKER}{EXCHANGE_SUFFIX_MAP.get(row.EXCHANGE, '.T')}"
        for row in rows
    ]
    print(f"全銘柄数: {len(tickers)}")

    # Cloud Run タスク分割
    if TASK_COUNT > 1:
        tickers = tickers[TASK_INDEX::TASK_COUNT]
        print(
            f"タスク分割: TASK_INDEX={TASK_INDEX} / TASK_COUNT={TASK_COUNT}"
            f" → {len(tickers)} 銘柄を担当"
        )

    return tickers


# ==========================================
# 5. 株価取得（バッチ処理）
# ==========================================

def fetch_am_data(tickers: list[str], date_str_hyphen: str) -> pd.DataFrame:
    """yfinance でバッチ取得し、当日の前場データを返す."""
    target_start = date_str_hyphen
    target_end = (
        datetime.strptime(date_str_hyphen, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    print(f"取得対象日: {target_start}")

    all_frames: list[pd.DataFrame] = []
    total_batches = (len(tickers) - 1) // BATCH_SIZE + 1

    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i : i + BATCH_SIZE]
        batch_no = i // BATCH_SIZE + 1
        print(f"Processing batch {batch_no}/{total_batches} ({len(batch)} tickers) ...")
        try:
            df_batch = yf.download(
                batch,
                start=target_start,
                end=target_end,
                group_by="ticker",
                auto_adjust=True,
                actions=False,
                threads=True,
                progress=False,
            )
            if not df_batch.empty and isinstance(df_batch.columns, pd.MultiIndex):
                df_stacked = df_batch.stack(level=0, future_stack=True)
                df_stacked.index.names = ["Date", "Ticker"]
                all_frames.append(df_stacked)
        except Exception as e:
            print(f"Error in batch {batch_no}: {e}")
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    if not all_frames:
        print("データが取得できませんでした。")
        return pd.DataFrame()

    master_df = pd.concat(all_frames)
    master_df.columns = [c.capitalize() for c in master_df.columns]
    master_df = master_df.reset_index()

    # 価格を小数点以下2桁に丸め
    for col in ["Open", "High", "Low", "Close"]:
        if col in master_df.columns:
            master_df[col] = pd.to_numeric(master_df[col], errors="coerce").round(2)

    # 出来高を整数に
    if "Volume" in master_df.columns:
        master_df["Volume"] = master_df["Volume"].fillna(0).astype(int)

    return master_df


# ==========================================
# 6. BQ 保存
# ==========================================

BQ_SCHEMA = [
    bigquery.SchemaField("DATE",     "DATE",     mode="REQUIRED"),
    bigquery.SchemaField("TICKER",   "STRING",   mode="REQUIRED"),
    bigquery.SchemaField("OPEN",     "FLOAT64",  mode="NULLABLE"),
    bigquery.SchemaField("HIGH",     "FLOAT64",  mode="NULLABLE"),
    bigquery.SchemaField("LOW",      "FLOAT64",  mode="NULLABLE"),
    bigquery.SchemaField("CLOSE",    "FLOAT64",  mode="NULLABLE"),
    bigquery.SchemaField("VOLUME",   "INTEGER",  mode="NULLABLE"),
    bigquery.SchemaField("TURNOVER", "FLOAT64",  mode="NULLABLE"),
    bigquery.SchemaField("LOADED_AT", "DATETIME", mode="NULLABLE"),
]


def create_table_if_not_exists(client: bigquery.Client) -> None:
    """STOCK_PRICE_YF_AM テーブルが存在しなければ作成する."""
    table_ref = BQ_TABLE_ID
    bq_table = bigquery.Table(table_ref, schema=BQ_SCHEMA)
    bq_table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="DATE",
    )
    bq_table.clustering_fields = ["TICKER"]
    try:
        client.create_table(bq_table)
        print(f"テーブル作成: {table_ref}")
    except Exception as e:
        if "Already Exists" in str(e):
            print(f"テーブル既存: {table_ref}")
        else:
            raise


def save_to_bq(client: bigquery.Client, df: pd.DataFrame, dry_run: bool = False) -> int:
    """BigQuery STOCK_PRICE_YF_AM テーブルに APPEND する."""
    df_bq = df.copy()
    df_bq = df_bq.rename(columns={
        "Open": "OPEN", "High": "HIGH", "Low": "LOW",
        "Close": "CLOSE", "Volume": "VOLUME",
    })
    df_bq["DATE"] = df_bq["Date"].dt.date
    # .T / .N / .S / .F サフィックスを除去してTICKERを生成
    df_bq["TICKER"] = df_bq["Ticker"].astype(str).str.replace(
        r"\.[TNSF]$", "", regex=True,
    )

    # TURNOVER = CLOSE * VOLUME（概算売買代金）
    df_bq["TURNOVER"] = (
        pd.to_numeric(df_bq["CLOSE"], errors="coerce")
        * pd.to_numeric(df_bq["VOLUME"], errors="coerce")
    )

    df_bq["LOADED_AT"] = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    # VOLUME を整数に
    df_bq["VOLUME"] = pd.to_numeric(df_bq["VOLUME"], errors="coerce").astype("Int64")

    df_bq = df_bq[["DATE", "TICKER", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "TURNOVER", "LOADED_AT"]]
    df_bq = df_bq.dropna(subset=["TICKER"])

    if df_bq.empty:
        print("BigQueryにインサートする有効なデータがありませんでした。")
        return 0

    if dry_run:
        print(f"[dry-run] {len(df_bq)} 行 INSERT スキップ")
        print(df_bq.head(10).to_string())
        return 0

    # LOADED_AT を datetime に変換
    df_bq["LOADED_AT"] = pd.to_datetime(df_bq["LOADED_AT"])

    job_config = bigquery.LoadJobConfig(
        schema=BQ_SCHEMA,
        write_disposition="WRITE_APPEND",
    )
    print(f"BigQueryへデータを送信中... ({len(df_bq)} 行)")
    job = client.load_table_from_dataframe(df_bq, BQ_TABLE_ID, job_config=job_config)
    job.result()
    print(f"BigQueryロード完了: {job.output_rows} 行を追加しました。")
    return job.output_rows


# ==========================================
# 7. メイン
# ==========================================

def main() -> None:
    """エントリポイント."""
    import argparse

    parser = argparse.ArgumentParser(description="yfinance 前場スナップショット → BQ ロード")
    parser.add_argument("--dry-run", action="store_true", help="BQ 書き込みを行わない")
    args = parser.parse_args()

    start_time = datetime.now(JST)
    log_cap = LogCapture()
    log_cap.start()

    try:
        print(f"実行環境: {RUNTIME}")
        print(f"タスク: {TASK_INDEX + 1}/{TASK_COUNT}")
        print(f"開始時刻: {start_time.strftime('%Y-%m-%d %H:%M:%S')} JST")

        # 取引日判定
        today = datetime.now(JST).date()
        if not is_trading_day(today):
            reason = "土日" if today.weekday() >= 5 else "祝日/特別休日"
            print(f"【スキップ】対象日({today})は{reason}のため、処理を終了します。")
            log_cap.stop()
            return

        date_str_hyphen = today.strftime("%Y-%m-%d")

        client = get_bq_client()
        create_table_if_not_exists(client)

        tickers = get_tickers(client)
        if not tickers:
            print("銘柄リストが空です。")
            log_cap.stop()
            return

        df = fetch_am_data(tickers, date_str_hyphen)
        bq_rows = 0
        if not df.empty:
            bq_rows = save_to_bq(client, df, dry_run=args.dry_run)

        elapsed = (datetime.now(JST) - start_time).total_seconds()
        print(f"完了: {bq_rows} 行ロード / 所要 {elapsed:.0f} 秒")

        log_text = log_cap.stop()

    except Exception as e:
        log_text = log_cap.stop()
        send_mail(
            f"[STOCK_PRICE_YF_AM] エラー (task {TASK_INDEX + 1}/{TASK_COUNT})",
            f"エラーが発生しました: {e}\n\n{traceback.format_exc()}",
            attachment_text=log_text,
        )
        raise


if __name__ == "__main__":
    main()

"""yfinance 株価取得 → GCS CSV + BigQuery STOCK.STOCK_PRICE + Dropbox ロード.

対応環境:
    - Google Colab（個人ユース）
    - Google Colab Enterprise
    - Cloud Run Job
"""

import os
import sys
import random
import time
import traceback
from datetime import datetime, timezone, timedelta

import jpholiday
import pandas as pd
import yfinance as yf
import dropbox
from dropbox.files import WriteMode
from google.cloud import bigquery, storage

# notify.py（同ディレクトリ）から共通メール・ログ機能をインポート
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import send_mail, LogCapture

# ==========================================
# 0. 実行環境判別
# ==========================================

def detect_runtime() -> str:
    """実行環境を自動判別する.

    Returns:
        "colab_personal" | "colab_enterprise" | "cloudrun"
    """
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    try:
        import google.colab  # noqa: F401
        if os.environ.get("GOOGLE_CLOUD_PROJECT"):
            return "colab_enterprise"
        return "colab_personal"
    except ImportError:
        return "cloudrun"


RUNTIME: str = detect_runtime()

# ==========================================
# 1. 設定エリア
# ==========================================

# ------------------------------------------
# A. 土日祝日のスキップ機能 (1=ON, 0=OFF)
#    1: 土日・祝日は処理しない（デフォルト）
#    0: 土日・祝日でも処理する
# ------------------------------------------
ENABLE_HOLIDAY_SKIP = 1

# ------------------------------------------
# B. 任意の日付指定 (YYYYMMDD)
#    空文字: システム日付を使用（デフォルト）
#    例: '20260301'
# ------------------------------------------
MANUAL_TARGET_DATE = ''

# ------------------------------------------
# C. DB(BigQuery)登録
#    0: 登録する（デフォルト）
#    1: スキップ
# ------------------------------------------
SKIP_DB_REGISTER = 0

# --- GCS設定 ---
BUCKET_NAME = "stock_data_1930932"
GCS_FOLDER  = "stock_price/history"

# --- BigQuery設定 ---
BQ_PROJECT_ID = "gmailpj-357912"
BQ_TABLE_ID   = "gmailpj-357912.STOCK.STOCK_PRICE"

# --- Dropbox設定 ---
DBX_APP_KEY       = 't8feblcw74hoeky'
DBX_APP_SECRET    = 'fcjgc37d034pw1n'
DBX_REFRESH_TOKEN = 'XwOxZlA8jPUAAAAAAAAAAZxnT4qRFtWLcShpKy3cNjTf3euIMqEZxCNieAQiLSDw'
DBX_FOLDER        = '/stock/script/kdb'

# --- yfinance バッチ設定 ---
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

# ==========================================
# 2. 認証・クライアント（遅延初期化）
# ==========================================

def setup_environment() -> None:
    """実行環境に応じた GCP 認証を行う."""
    print(f"実行環境: {RUNTIME}")
    if RUNTIME == "colab_personal":
        from google.colab import auth
        auth.authenticate_user()
    # colab_enterprise / cloudrun は ADC が自動的に有効


_storage_client: storage.Client | None = None
_bq_client: bigquery.Client | None = None


def _get_storage_client() -> storage.Client:
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client(project=BQ_PROJECT_ID)
    return _storage_client


def _get_bq_client() -> bigquery.Client:
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=BQ_PROJECT_ID)
    return _bq_client


# ==========================================
# 3. 日付決定・休日スキップ
# ==========================================

def resolve_target_date() -> tuple[str, str] | None:
    """処理対象日を決定する.

    Returns:
        (date_str, date_str_hyphen) または None（スキップ対象日）
    """
    if MANUAL_TARGET_DATE:
        today_date = datetime.strptime(MANUAL_TARGET_DATE, '%Y%m%d').date()
        print(f"★指定日付モードで実行します: {today_date}")
    else:
        today_date = datetime.now(JST).date()
        print(f"システム日付モードで実行します: {today_date}")

    is_weekend = today_date.weekday() >= 5
    is_holiday = jpholiday.is_holiday(today_date) or (today_date in SPECIAL_DATES)
    date_str   = today_date.strftime('%Y%m%d')

    if ENABLE_HOLIDAY_SKIP == 1 and (is_weekend or is_holiday):
        reason = "土日" if is_weekend else "祝日"
        print(f"【スキップON】対象日({date_str})は{reason}のため、処理を終了します。")
        return None
    elif is_weekend or is_holiday:
        reason = "土日" if is_weekend else "祝日"
        print(f"【スキップOFF】対象日({date_str})は{reason}ですが、処理を強制続行します。")

    return date_str, today_date.strftime('%Y-%m-%d')


# ==========================================
# 4. 銘柄一覧取得
# ==========================================

EXCHANGE_SUFFIX_MAP = {'TSE': '.T', 'NSE': '.N', 'SSE': '.S', 'FSE': '.F'}


def get_tickers() -> list[str]:
    """BigQuery STOCK_CODE_LIST から yfinance 用ティッカーリストを取得する."""
    print("BigQuery (STOCK.STOCK_CODE_LIST) から銘柄一覧を取得します...")
    query = f"""
        SELECT TICKER, EXCHANGE
        FROM `{BQ_PROJECT_ID}.STOCK.STOCK_CODE_LIST`
        WHERE EXCHANGE IN ('TSE', 'NSE', 'SSE', 'FSE')
    """
    results = _get_bq_client().query(query).result()
    tickers = [
        f"{row.TICKER}{EXCHANGE_SUFFIX_MAP.get(row.EXCHANGE, '.T')}"
        for row in results
    ]
    print(f"取得対象銘柄数: {len(tickers)} 銘柄")
    return tickers


# ==========================================
# 5. 株価取得（バッチ処理）
# ==========================================

def fetch_stock_data(tickers: list[str], date_str: str, date_str_hyphen: str) -> pd.DataFrame:
    """yfinance でバッチ取得し、対象日のデータを返す."""
    target_start = date_str_hyphen
    target_end   = (datetime.strptime(date_str_hyphen, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    print(f"取得対象日: {target_start}")

    all_frames: list[pd.DataFrame] = []
    total_batches = (len(tickers) - 1) // BATCH_SIZE + 1

    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i: i + BATCH_SIZE]
        print(f"Processing batch {i // BATCH_SIZE + 1}/{total_batches} ...")
        try:
            df_batch = yf.download(
                batch,
                start=target_start,
                end=target_end,
                group_by='ticker',
                auto_adjust=True,
                actions=False,
                threads=True,
                progress=False,
            )
            if not df_batch.empty and isinstance(df_batch.columns, pd.MultiIndex):
                df_stacked = df_batch.stack(level=0, future_stack=True)
                df_stacked.index.names = ['Date', 'Ticker']
                all_frames.append(df_stacked)
        except Exception as e:
            print(f"Error in batch {i}: {e}")
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    if not all_frames:
        print("データが取得できませんでした。")
        return pd.DataFrame()

    master_df = pd.concat(all_frames)
    master_df.columns = [c.capitalize() for c in master_df.columns]
    master_df = master_df.reset_index()
    master_df['Date_str'] = master_df['Date'].dt.strftime('%Y%m%d')

    target_data = master_df[master_df['Date_str'] == date_str].copy()
    if target_data.empty:
        print("指定日のデータが取得できませんでした。")
        return pd.DataFrame()

    # 株価を小数点以下2桁に丸め（.9999...対策）
    for col in ['Open', 'High', 'Low', 'Close']:
        if col in target_data.columns:
            target_data[col] = pd.to_numeric(target_data[col], errors='coerce').round(2)
    # 出来高を整数に（.0対策）
    if 'Volume' in target_data.columns:
        target_data['Volume'] = target_data['Volume'].fillna(0).astype(int)

    return target_data


# ==========================================
# 6. 保存処理
# ==========================================

def save_to_gcs(target_data: pd.DataFrame, date_str: str) -> None:
    """CSV を GCS にアップロードする."""
    csv_data = target_data.drop(columns=['Date', 'Date_str'])
    cols = [c for c in ['Ticker', 'Open', 'High', 'Low', 'Close', 'Volume'] if c in csv_data.columns]
    csv_string = csv_data[cols].to_csv(index=False, lineterminator='\r\n')

    blob_name = f"{GCS_FOLDER}/{date_str}.csv"
    print(f"GCSへアップロード中: gs://{BUCKET_NAME}/{blob_name}")
    _get_storage_client().bucket(BUCKET_NAME).blob(blob_name).upload_from_string(
        csv_string, content_type='text/csv'
    )
    print("GCSアップロード完了。")


def save_to_bq(target_data: pd.DataFrame) -> int:
    """BigQuery STOCK_PRICE テーブルに APPEND する. 追加行数を返す."""
    df_bq = target_data.copy()
    df_bq = df_bq.rename(columns={
        'Ticker': 'TICKER', 'Open': 'OPEN', 'High': 'HIGH',
        'Low': 'LOW', 'Close': 'CLOSE', 'Volume': 'VOLUME',
    })
    df_bq['YEARDATE'] = df_bq['Date'].dt.date
    df_bq['TICKER']   = df_bq['TICKER'].astype(str).str.split('.').str[0]
    df_bq = df_bq[df_bq['TICKER'].str.len() == 4]

    for col in ['OPEN', 'HIGH', 'LOW', 'CLOSE', 'VOLUME']:
        if col not in df_bq.columns:
            df_bq[col] = None
        df_bq[col] = pd.to_numeric(df_bq[col], errors='coerce').round().astype('Int64')

    df_bq = df_bq[['YEARDATE', 'TICKER', 'OPEN', 'HIGH', 'LOW', 'CLOSE', 'VOLUME']]

    if df_bq.empty:
        print("BigQueryにインサートする有効なデータがありませんでした。")
        return 0

    job_config = bigquery.LoadJobConfig(
        schema=[
            bigquery.SchemaField("YEARDATE", "DATE",    mode="REQUIRED"),
            bigquery.SchemaField("TICKER",   "STRING",  mode="REQUIRED"),
            bigquery.SchemaField("OPEN",     "INTEGER", mode="NULLABLE"),
            bigquery.SchemaField("HIGH",     "INTEGER", mode="NULLABLE"),
            bigquery.SchemaField("LOW",      "INTEGER", mode="NULLABLE"),
            bigquery.SchemaField("CLOSE",    "INTEGER", mode="NULLABLE"),
            bigquery.SchemaField("VOLUME",   "INTEGER", mode="NULLABLE"),
        ],
        write_disposition="WRITE_APPEND",
    )
    print(f"BigQueryへデータを送信中... ({len(df_bq)}行)")
    job = _get_bq_client().load_table_from_dataframe(df_bq, BQ_TABLE_ID, job_config=job_config)
    job.result()
    print(f"BigQueryロード完了: {job.output_rows} 行を追加しました。")
    return job.output_rows


_DROPBOX_ERROR: str = ""


def save_to_dropbox(target_data: pd.DataFrame, date_str_hyphen: str) -> None:
    """Dropbox に Shift-JIS CSV をアップロードする.

    Dropbox容量不足の場合は処理を継続し、グローバル変数にエラーを記録する。
    """
    global _DROPBOX_ERROR
    if not DBX_REFRESH_TOKEN:
        print("DropboxのREFRESH_TOKENが設定されていないため、スキップします。")
        return

    df_dbx = target_data.copy()

    def get_market_name(t: str) -> str:
        if t.endswith('.N'): return '名証'
        if t.endswith('.S'): return '札証'
        if t.endswith('.F'): return '福証'
        return '東証'

    df_dbx['Dummy_Market'] = df_dbx['Ticker'].apply(get_market_name)
    df_dbx['Ticker']       = df_dbx['Ticker'].str.replace('.', '-', regex=False)
    df_dbx['Dummy_Name']   = 'ダミー'
    df_dbx['Trading_Value'] = 0
    df_dbx['Date_YMD']     = df_dbx['Date'].dt.strftime('%Y%m%d')

    cols = ['Ticker', 'Dummy_Name', 'Dummy_Market',
            'Open', 'High', 'Low', 'Close', 'Volume', 'Trading_Value', 'Date_YMD']
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        if col not in df_dbx.columns:
            df_dbx[col] = 0

    csv_bytes = df_dbx[cols].to_csv(
        index=False, header=False, lineterminator='\r\n'
    ).encode('shift_jis', errors='ignore')

    upload_path = f"{DBX_FOLDER}/stocks_{date_str_hyphen}.csv"
    print(f"Dropboxへアップロード中: {upload_path} (Shift-JIS)")
    dbx_client = dropbox.Dropbox(
        app_key=DBX_APP_KEY,
        app_secret=DBX_APP_SECRET,
        oauth2_refresh_token=DBX_REFRESH_TOKEN,
    )
    try:
        dbx_client.files_upload(csv_bytes, upload_path, mode=WriteMode('overwrite'))
        print("Dropboxへのアップロードが完了しました。")
    except Exception as e:
        if "insufficient_space" in str(e):
            _DROPBOX_ERROR = f"Dropbox 容量不足のためアップロードをスキップ: {upload_path}"
            print(f"[警告] {_DROPBOX_ERROR}")
        else:
            raise


# ==========================================
# 7. メイン
# ==========================================

def main() -> None:
    """エントリポイント."""
    start_time = datetime.now(JST)
    log_cap    = LogCapture()
    log_cap.start()

    try:
        setup_environment()

        result = resolve_target_date()
        if result is None:
            log_cap.stop()
            return  # 休日スキップ
        date_str, date_str_hyphen = result

        tickers     = get_tickers()
        target_data = fetch_stock_data(tickers, date_str, date_str_hyphen)

        bq_rows = 0
        if target_data.empty:
            raise RuntimeError(
                f"株価データが取得できませんでした: target_date={date_str_hyphen}, "
                f"tickers={len(tickers)}. GCS/BigQuery/Dropboxへの保存は実行していません。"
            )

        save_to_gcs(target_data, date_str)
        if SKIP_DB_REGISTER == 0:
            bq_rows = save_to_bq(target_data)
        else:
            print("\n【スキップ】設定により、BigQueryへの登録処理をスキップします。")
        save_to_dropbox(target_data, date_str_hyphen)

        log_text = log_cap.stop()

        # Dropbox容量不足は後続処理完了後に異常終了として通知
        if _DROPBOX_ERROR:
            send_mail(
                "[STOCK_PRICE] 【異常終了】DROPBOX容量不足",
                f"Dropboxへのアップロードが容量不足で失敗しました。\n"
                f"BQ・GCSへのロードは正常完了しています。\n\n"
                f"スキップされたファイル: {_DROPBOX_ERROR}",
                attachment_text=log_text,
            )
            sys.exit(1)

    except Exception as e:
        log_text = log_cap.stop()
        send_mail(
            "[STOCK_PRICE] エラー",
            f"エラーが発生しました: {e}\n\n{traceback.format_exc()}",
            attachment_text=log_text,
        )
        raise


if __name__ == "__main__":
    main()

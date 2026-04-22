"""品貸料・貸借残高データ取得 → BigQuery ロード.

taisyaku.jp から shina.csv（品貸料）と zandaka.csv（貸借残高）をダウンロードし、
クレンジング後に BigQuery の SHINA_RATES・MARGIN_BALANCE テーブルに追記する。

対応環境:
    - ローカルPC（主環境）
    - Google Colab（個人ユース）
    - Google Colab Enterprise
    - Cloud Run Job
"""

import csv
import io
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

import jpholiday
import pandas as pd
import requests
from google.cloud import bigquery
from google.oauth2 import service_account

# ==========================================
# 0. 実行環境判別（インポートより先に確定）
# ==========================================

def detect_runtime() -> str:
    """実行環境を自動判別する.

    Returns:
        "local" | "colab_personal" | "colab_enterprise" | "cloudrun"
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
JST = timezone(timedelta(hours=+9), "JST")

# jpholiday が対応しない特別休日（大晦日・年始休暇）
import datetime as _dt
SPECIAL_DATES = {
    _dt.date(2025, 12, 31),  # 大晦日
    _dt.date(2026,  1,  2),  # 年始休暇
    _dt.date(2026,  1,  3),  # 年始休暇
}

# ==========================================
# 環境別ライブラリ読み込み
# ==========================================

if RUNTIME == "local":
    # ローカル: Slib（既存の共通ライブラリ）を使用
    _SLIB_PATH = r"C:\Users\zonekun\Dropbox\stock\py"
    if _SLIB_PATH not in sys.path:
        sys.path.insert(0, _SLIB_PATH)
    import Slib
else:
    # サーバ: notify.py の共通ライブラリを使用
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from notify import send_mail, LogCapture
    import dropbox
    from dropbox.files import WriteMode

# ==========================================
# 1. 設定エリア
# ==========================================

# ------------------------------------------
# A. 休日スキップ
#    1: 土日・祝日は処理しない（デフォルト）
#    0: 土日・祝日でも処理する
# ------------------------------------------
ENABLE_HOLIDAY_SKIP = 1

SHINA_URL   = "https://www.taisyaku.jp/data/shina.csv"
ZANDAKA_URL = "https://www.taisyaku.jp/data/zandaka.csv"

PROJECT_ID = "gmailpj-357912"
DATASET_ID = "STOCK"

# BQ 認証キー（ローカル・Colab個人のみ使用）
KEY_PATH = os.environ.get(
    "GOOGLE_APPLICATION_CREDENTIALS",
    r"C:\Users\zonekun\Dropbox\AWS\google_bq_key.json",
)

# ダウンロード作業フォルダ
if RUNTIME == "local":
    DOWNLOAD_FOLDER = r"C:\Users\zonekun\Downloads"
else:
    DOWNLOAD_FOLDER = "/tmp"

# アーカイブ保管先（ローカルのみ。サーバは Dropbox API でアップロード）
MOVE_FOLDER     = r"C:\Users\zonekun\Dropbox\stock\script\shina"
DBX_FOLDER_PATH = "/stock/script/shina"  # Dropbox 上のパス（サーバ環境用）

# Dropbox 認証情報（サーバ環境用）
DBX_APP_KEY       = "t8feblcw74hoeky"
DBX_APP_SECRET    = "fcjgc37d034pw1n"
DBX_REFRESH_TOKEN = "XwOxZlA8jPUAAAAAAAAAAZxnT4qRFtWLcShpKy3cNjTf3euIMqEZxCNieAQiLSDw"

# カラムマッピング
SHINA_COL_MAP = {
    "貸借申込日": "YEARDATE", "決済日": "SETTLEMENT_DATE", "コード": "TICKER",
    "銘柄名": "NAME", "取引所区分": "MARKET_TYPE", "決算事由": "SETTLEMENT_REASON",
    "決算等": "SETTLEMENT_EVENT_DATE", "貸借値段（円）": "LOAN_PRICE",
    "貸株超過株数": "EXCESS_STOCK_VOLUME", "最高料率（円）": "MAX_RATE",
    "当日品貸料率（円）": "DAILY_RATE", "当日品貸日数": "DAILY_DAYS",
    "前日品貸料率（円）": "PREV_DAILY_RATE", "備考": "REMARKS",
    "制限": "RESTRICTIONS", "応札倍率ランク": "BID_RATIO_RANK",
}

ZANDAKA_COL_MAP = {
    "申込日": "YEARDATE", "銘柄コード": "TICKER", "銘柄名": "NAME",
    "取引所区分名": "MARKET_NAME", "融資新規株数": "FIN_NEW_STOCKS",
    "融資返済株数": "FIN_REPAY_STOCKS", "融資残高株数": "FIN_BAL_STOCKS",
    "貸株新規株数": "LEND_NEW_STOCKS", "貸株返済株数": "LEND_REPAY_STOCKS",
    "貸株残高株数": "LEND_BAL_STOCKS", "差引残高株数": "NET_BAL_STOCKS",
    "融資新規金額": "FIN_NEW_AMT", "融資返済金額": "FIN_REPAY_AMT",
    "融資残高金額": "FIN_BAL_AMT", "貸株新規金額": "LEND_NEW_AMT",
    "貸株返済金額": "LEND_REPAY_AMT", "貸株残高金額": "LEND_BAL_AMT",
    "差引残高金額": "NET_BAL_AMT", "制度信用・買残高株数": "SYS_CREDIT_BUY",
    "制度信用・売残高株数": "SYS_CREDIT_SELL", "融資権利落額": "FIN_RIGHTS_DROP",
    "貸株権利落額": "LEND_RIGHTS_DROP", "合計・更新差金融資値上り": "DIFF_FIN_UP",
    "合計・更新差金融資値下り": "DIFF_FIN_DOWN", "合計・更新差金貸株値下り": "DIFF_LEND_DOWN",
    "合計・更新差金貸株値上り": "DIFF_LEND_UP", "総合回転日数": "TOTAL_DAYS",
    "融資・新規回転日数": "FIN_NEW_DAYS", "融資・返済回転日数": "FIN_REPAY_DAYS",
    "融資・残高回転日数": "FIN_BAL_DAYS", "貸株・新規回転日数": "LEND_NEW_DAYS",
    "貸株・返済回転日数": "LEND_REPAY_DAYS", "貸株・残高回転日数": "LEND_BAL_DAYS",
}


# ==========================================
# 2. 共通ユーティリティ
# ==========================================

def _send_mail(subject: str, body: str) -> None:
    """環境別メール送信ラッパー."""
    if RUNTIME == "local":
        Slib.SendMail(subject, body)
    else:
        send_mail(subject, body)


def setup_environment() -> None:
    """実行環境に応じた認証を行う."""
    print(f"実行環境: {RUNTIME}")
    if RUNTIME == "colab_personal":
        from google.colab import auth
        auth.authenticate_user()


def get_bq_client() -> bigquery.Client:
    """実行環境に応じた BigQuery クライアントを返す."""
    if RUNTIME in ("cloudrun", "colab_enterprise"):
        return bigquery.Client(project=PROJECT_ID)
    credentials = service_account.Credentials.from_service_account_file(KEY_PATH)
    return bigquery.Client(credentials=credentials, project=PROJECT_ID)


_DROPBOX_ERRORS: list[str] = []


def upload_to_dropbox(local_path: str, filename: str) -> None:
    """Dropbox API でファイルをアップロードする（サーバ環境用）.

    Dropbox容量不足の場合は処理を継続し、グローバルリストにエラーを記録する。
    """
    from dropbox.exceptions import ApiError
    dbx = dropbox.Dropbox(
        app_key=DBX_APP_KEY,
        app_secret=DBX_APP_SECRET,
        oauth2_refresh_token=DBX_REFRESH_TOKEN,
    )
    dbx_path = f"{DBX_FOLDER_PATH}/{filename}"
    try:
        with open(local_path, "rb") as f:
            dbx.files_upload(f.read(), dbx_path, mode=WriteMode("overwrite"))
        print(f"Dropbox アップロード完了: {dbx_path}")
    except ApiError as e:
        err_str = str(e)
        if "insufficient_space" in err_str:
            msg = f"Dropbox 容量不足のためアップロードをスキップ: {dbx_path}"
            print(f"[警告] {msg}")
            _DROPBOX_ERRORS.append(msg)
        else:
            raise


def is_holiday_today() -> tuple[bool, str]:
    """今日が土日・祝日かどうかを判定する.

    Returns:
        (スキップすべきか, 理由文字列)
    """
    today = datetime.now(JST).date()
    if today.weekday() >= 5:
        return True, "土日"
    if jpholiday.is_holiday(today) or (today in SPECIAL_DATES):
        return True, "祝日"
    return False, ""


# ==========================================
# 3. ダウンロード処理
# ==========================================

def delete_all_files_in_folder(folder_path: str) -> None:
    """ダウンロードフォルダ内のすべてのファイルを削除する（ローカルのみ）."""
    if RUNTIME != "local":
        return
    try:
        for filename in os.listdir(folder_path):
            file_path = os.path.join(folder_path, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
                print(f"削除しました: {file_path}")
    except Exception as e:
        print(f"ファイル削除中にエラーが発生しました: {e}")


def download_shina() -> str:
    """shina.csv をダウンロードし、先頭3行を削除して保存する.

    Returns:
        保存したファイルパス
    """
    current_date = datetime.now(JST).strftime("%Y_%m_%d")
    filename     = f"shina{current_date}.csv"
    temp_path    = os.path.join(DOWNLOAD_FOLDER, "shina_temp.csv")
    save_path    = os.path.join(
        MOVE_FOLDER if RUNTIME == "local" else DOWNLOAD_FOLDER,
        filename,
    )

    response = requests.get(SHINA_URL, stream=True, timeout=60)
    response.raise_for_status()

    with open(temp_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    # 先頭3行を削除
    with open(temp_path, mode="r", encoding="shift_jis") as infile, \
         open(save_path, mode="w", encoding="shift_jis", newline="") as outfile:
        reader = csv.reader(infile)
        writer = csv.writer(outfile)
        for _ in range(3):
            next(reader, None)
        for row in reader:
            writer.writerow(row)

    os.remove(temp_path)
    print(f"shina ダウンロード完了: {save_path}")

    # サーバ環境: Dropbox API でアップロード
    if RUNTIME != "local":
        upload_to_dropbox(save_path, filename)

    return save_path


def download_zandaka() -> str:
    """zandaka.csv をダウンロードし、不要列を削除して保存する.

    Returns:
        保存したファイルパス
    """
    current_date = datetime.now(JST).strftime("%Y_%m_%d")
    filename     = f"zandaka{current_date}.csv"
    temp_path    = os.path.join(DOWNLOAD_FOLDER, "zandaka_temp.csv")
    save_path    = os.path.join(
        MOVE_FOLDER if RUNTIME == "local" else DOWNLOAD_FOLDER,
        filename,
    )

    response = requests.get(ZANDAKA_URL, stream=True, timeout=60)
    response.raise_for_status()

    with open(temp_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    # 2列目、6列目、7列目を削除
    with open(temp_path, mode="r", encoding="shift_jis") as infile, \
         open(save_path, mode="w", encoding="shift_jis", newline="") as outfile:
        reader = csv.reader(infile)
        writer = csv.writer(outfile)
        for row in reader:
            writer.writerow(row[:1] + row[2:5] + row[7:])

    os.remove(temp_path)
    print(f"zandaka ダウンロード完了: {save_path}")

    # サーバ環境: Dropbox API でアップロード
    if RUNTIME != "local":
        upload_to_dropbox(save_path, filename)

    return save_path


# ==========================================
# 4. BigQuery ロード処理
# ==========================================

def clean_and_load(bq_client: bigquery.Client, file_path: str, table_name: str, col_map: dict) -> None:
    """CSVを読み込み、クレンジングしてBigQueryにロードする."""
    print(f"\n--- BQ処理中: {os.path.basename(file_path)} ---")

    df = pd.read_csv(file_path, encoding="cp932", index_col=False)

    df = df.rename(columns=col_map)
    valid_cols = [c for c in col_map.values() if c in df.columns]
    df = df[valid_cols]

    # 日付変換
    for col in df.columns:
        if "DATE" in col or col == "YEARDATE":
            df[col] = pd.to_datetime(df[col].astype(str), errors="coerce").dt.date

    # 数値クレンジング
    for col in df.select_dtypes(include=["object"]).columns:
        if any(kw in col for kw in ["STOCKS", "AMT", "RATE", "PRICE", "DAYS", "BAL", "VOLUME", "UP", "DOWN", "DIFF"]):
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # リラン対策：既存データの削除
    table_id = f"{PROJECT_ID}.{DATASET_ID}.{table_name}"
    target_dates = df["YEARDATE"].dropna().unique()
    if len(target_dates) > 0:
        date_list_str = ", ".join([f"'{d}'" for d in target_dates])
        delete_query  = f"DELETE FROM `{table_id}` WHERE YEARDATE IN ({date_list_str})"
        print(f"既存レコードを確認中... 対象日: {date_list_str}")
        query_job = bq_client.query(delete_query)
        query_job.result()
        print(f"削除完了: {query_job.num_dml_affected_rows} 行を削除しました。")

    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    job = bq_client.load_table_from_dataframe(df, table_id, job_config=job_config)
    job.result()
    print(f"追加完了: {len(df)}件を {table_name} にインサートしました。")


# ==========================================
# 5. メイン
# ==========================================

def main() -> None:
    """エントリポイント."""
    start_time = datetime.now(JST)

    # サーバ環境のみ LogCapture を使用
    log_cap = LogCapture() if RUNTIME != "local" else None
    if log_cap:
        log_cap.start()

    try:
        setup_environment()

        # 休日スキップ判定
        is_skip, reason = is_holiday_today()
        if is_skip:
            if ENABLE_HOLIDAY_SKIP == 1:
                print(f"【スキップON】本日は{reason}のため、処理を終了します。")
                return
            else:
                print(f"【スキップOFF】本日は{reason}ですが、処理を強制続行します。")

        # --------------------------------------------------
        # Phase 1: ダウンロード
        # --------------------------------------------------
        print("=" * 50)
        print("Phase 1: ダウンロード開始")
        print("=" * 50)

        delete_all_files_in_folder(DOWNLOAD_FOLDER)

        shina_path    = None
        zandaka_path  = None
        download_errors = []

        try:
            shina_path = download_shina()
        except Exception as e:
            msg = f"shina ダウンロード失敗: {e}\n{traceback.format_exc()}"
            print(msg)
            download_errors.append(msg)

        time.sleep(10)

        try:
            zandaka_path = download_zandaka()
        except Exception as e:
            msg = f"zandaka ダウンロード失敗: {e}\n{traceback.format_exc()}"
            print(msg)
            download_errors.append(msg)

        if shina_path is None and zandaka_path is None:
            end_time = datetime.now(JST)
            _send_mail(
                "【異常終了】品貸・残高 ダウンロード失敗",
                f"shina, zandaka の両方のダウンロードに失敗しました。\n\n"
                f"エラー詳細:\n" + "\n".join(download_errors) + "\n\n"
                f"開始: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"終了: {end_time.strftime('%Y-%m-%d %H:%M:%S')}",
            )
            sys.exit(1)

        if download_errors:
            print(f"[警告] 一部ダウンロード失敗: {download_errors}")

        # --------------------------------------------------
        # Phase 2: BigQuery ロード
        # --------------------------------------------------
        print("\n" + "=" * 50)
        print("Phase 2: BigQuery ロード開始")
        print("=" * 50)

        bq_client = get_bq_client()
        bq_errors = []

        if shina_path:
            try:
                clean_and_load(bq_client, shina_path, "SHINA_RATES", SHINA_COL_MAP)
            except Exception as e:
                msg = f"SHINA_RATES BQロード失敗: {e}\n{traceback.format_exc()}"
                print(msg)
                bq_errors.append(msg)

        if zandaka_path:
            try:
                clean_and_load(bq_client, zandaka_path, "MARGIN_BALANCE", ZANDAKA_COL_MAP)
            except Exception as e:
                msg = f"MARGIN_BALANCE BQロード失敗: {e}\n{traceback.format_exc()}"
                print(msg)
                bq_errors.append(msg)

        # --------------------------------------------------
        # 終了メール
        # --------------------------------------------------
        end_time  = datetime.now(JST)
        time_info = (
            f"開始: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"終了: {end_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        if log_cap:
            log_cap.stop()

        if download_errors and bq_errors:
            _send_mail(
                "【異常終了】品貸・残高 ダウンロード・BQ両方でエラー",
                f"ダウンロードとBigQueryロードの両方でエラーが発生しました。\n\n"
                f"ダウンロードエラー:\n" + "\n".join(download_errors) + "\n\n"
                f"BQエラー:\n" + "\n".join(bq_errors) + "\n\n" + time_info,
            )
        elif download_errors:
            _send_mail(
                "【一部エラー】品貸・残高 ダウンロード一部失敗",
                f"ダウンロードで一部エラーが発生しましたが、成功分のBQロードは完了しました。\n\n"
                f"ダウンロードエラー:\n" + "\n".join(download_errors) + "\n\n" + time_info,
            )
        elif bq_errors:
            _send_mail(
                "【異常終了】品貸・残高 BQロード失敗",
                f"ダウンロードは正常ですが、BigQueryへのロードでエラーが発生しました。\n\n"
                f"BQエラー:\n" + "\n".join(bq_errors) + "\n\n" + time_info,
            )
        # 正常終了: メール送信なし

        # Dropbox容量不足は後続処理完了後に異常終了として通知
        if _DROPBOX_ERRORS:
            _send_mail(
                "【異常終了】DROPBOX容量不足 品貸・残高",
                f"Dropboxへのアップロードが容量不足で失敗しました。\n"
                f"BQ・GCSへのロードは正常完了しています。\n\n"
                f"スキップされたファイル:\n" + "\n".join(_DROPBOX_ERRORS) + "\n\n" + time_info,
            )
            sys.exit(1)

        print("\nすべての処理が終了しました。")

    except Exception as e:
        log_text = log_cap.stop() if log_cap else None
        if RUNTIME != "local":
            send_mail(
                "[SHINA] エラー",
                f"エラーが発生しました: {e}\n\n{traceback.format_exc()}",
                attachment_text=log_text,
            )
        else:
            _send_mail("[SHINA] エラー", f"エラーが発生しました: {e}\n\n{traceback.format_exc()}")
        raise


if __name__ == "__main__":
    main()

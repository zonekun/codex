"""BQ STOCK.CONSENSUS 最新レコードを Shift-JIS CSV で Dropbox にアップロードする.

用途: コンセンサステーブルの最新スナップショット（最新DATAAT）をCSV保管。
実行: PYTHONUTF8=1 C:\\venvs\\investment-agent\\Scripts\\python.exe scripts/export_consensus_csv.py
"""

import os
import sys

import dropbox
import pandas as pd
import structlog
from dropbox.files import WriteMode
from google.cloud import bigquery
from google.oauth2 import service_account

# ==========================================
# 定数
# ==========================================
_BASE = os.path.join(os.path.dirname(__file__), "..")
KEY_FILE = os.path.join(_BASE, "keys", "gcp-service-account.json")
BQ_TABLE = "gmailpj-357912.STOCK.CONSENSUS"

# Dropbox
DBX_APP_KEY = "t8feblcw74hoeky"
DBX_APP_SECRET = "fcjgc37d034pw1n"
DBX_REFRESH_TOKEN = "XwOxZlA8jPUAAAAAAAAAAZxnT4qRFtWLcShpKy3cNjTf3euIMqEZxCNieAQiLSDw"
DBX_UPLOAD_PATH = "/stock/temp/CONSENSUS_latest.csv"

log = structlog.get_logger()


def fetch_latest_consensus() -> pd.DataFrame:
    """BQ から最新 DATAAT の全件を取得し、ピボット形式に変換する.

    出力カラム: code, 1Q, 2Q, 3Q, 4Q, NEXT
    - 1Q〜3Q: QUARTER='1Q'〜'3Q', TARGET='CURRENT'
    - 4Q: QUARTER='FY', TARGET='CURRENT'
    - NEXT: QUARTER='FY', TARGET='NEXT'
    """
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    client = bigquery.Client(credentials=creds, project="gmailpj-357912")
    sql = f"""
        SELECT *
        FROM `{BQ_TABLE}`
        WHERE DATAAT = (SELECT MAX(DATAAT) FROM `{BQ_TABLE}`)
    """
    raw = client.query(sql).to_dataframe()
    log.info("BQ取得完了", rows=len(raw))

    # ピボット用ラベル: FY/CURRENT→4Q, FY/NEXT→NEXT, それ以外はQUARTERそのまま
    raw["col"] = raw.apply(
        lambda r: "NEXT" if r["QUARTER"] == "FY" and r["TARGET"] == "NEXT"
        else "4Q" if r["QUARTER"] == "FY"
        else r["QUARTER"],
        axis=1,
    )
    pivot = raw.pivot_table(index="TICKER", columns="col", values="PROFIT", aggfunc="first")
    pivot = pivot.reindex(columns=["1Q", "2Q", "3Q", "4Q", "NEXT"])
    pivot = pivot.reset_index().rename(columns={"TICKER": "code"})
    pivot = pivot.sort_values("code").reset_index(drop=True)
    return pivot


def upload_to_dropbox(csv_bytes: bytes, upload_path: str) -> None:
    """Dropbox にファイルをアップロードする."""
    dbx = dropbox.Dropbox(
        app_key=DBX_APP_KEY,
        app_secret=DBX_APP_SECRET,
        oauth2_refresh_token=DBX_REFRESH_TOKEN,
    )
    dbx.files_upload(csv_bytes, upload_path, mode=WriteMode("overwrite"))
    log.info("Dropboxアップロード完了", path=upload_path, size_bytes=len(csv_bytes))


def main() -> None:
    """メイン処理."""
    df = fetch_latest_consensus()
    if df.empty:
        log.warning("CONSENSUSテーブルにデータなし")
        sys.exit(1)

    csv_bytes = df.to_csv(index=False, lineterminator="\r\n").encode(
        "shift_jis", errors="replace"
    )
    upload_to_dropbox(csv_bytes, DBX_UPLOAD_PATH)


if __name__ == "__main__":
    main()

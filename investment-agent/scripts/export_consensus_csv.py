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
BQ_TABLE = "gmailpj-357912.STOCK.V_CONSENSUS_MERGED"

# Dropbox
DBX_APP_KEY = "t8feblcw74hoeky"
DBX_APP_SECRET = "fcjgc37d034pw1n"
DBX_REFRESH_TOKEN = "XwOxZlA8jPUAAAAAAAAAAZxnT4qRFtWLcShpKy3cNjTf3euIMqEZxCNieAQiLSDw"
DBX_UPLOAD_PATH = "/stock/temp/CONSENSUS_latest.csv"

log = structlog.get_logger()


def fetch_latest_consensus() -> pd.DataFrame:
    """BQ VIEW から全件取得し、C案PERIOD_REL判定でピボット形式に変換する.

    出力カラム: code, 1Q, 2Q, 3Q, 4Q, NEXT
    - 1Q〜3Q: ORD_PROFIT（四半期）
    - 4Q: 当期FYのORD_PROFIT（fin_summary最新FY直後）
    - NEXT: 来期FYのORD_PROFIT
    """
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    client = bigquery.Client(credentials=creds, project="gmailpj-357912")
    sql = f"""
        SELECT TICKER, FY, QUARTER, ORD_PROFIT
        FROM `{BQ_TABLE}`
    """
    raw = client.query(sql).to_dataframe()
    log.info("BQ取得完了", rows=len(raw))

    fy_sql = "SELECT TICKER, CURRENT_FY FROM `gmailpj-357912.STOCK.V_LATEST_DISCLOSURE`"
    fy_df = client.query(fy_sql).to_dataframe()
    current_fy_map: dict[str, str] = dict(zip(fy_df["TICKER"], fy_df["CURRENT_FY"]))
    log.info("FYルックアップ完了", tickers=len(current_fy_map))

    result_rows: list[dict[str, object]] = []
    for ticker, group in raw.groupby("TICKER"):
        data: dict[str, object] = {"code": ticker}
        current_fy = current_fy_map.get(str(ticker))
        future_fys: list[tuple[str, int]] = []

        for _, row in group.iterrows():
            profit = row["ORD_PROFIT"]
            if pd.isna(profit):
                continue
            quarter = row["QUARTER"]
            if quarter in ("1Q", "2Q", "3Q"):
                data[quarter] = int(profit)
            elif quarter == "FY" and current_fy and row["FY"] >= current_fy:
                future_fys.append((row["FY"], int(profit)))

        future_fys.sort()
        if len(future_fys) >= 1:
            data["4Q"] = future_fys[0][1]
        if len(future_fys) >= 2:
            data["NEXT"] = future_fys[1][1]
        result_rows.append(data)

    pivot = pd.DataFrame(result_rows)
    pivot = pivot.reindex(columns=["code", "1Q", "2Q", "3Q", "4Q", "NEXT"])
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

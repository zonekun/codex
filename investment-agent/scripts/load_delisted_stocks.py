"""
上場廃止銘柄CSVをBigQuery STOCK.DELISTED_STOCKS にロードするスクリプト。

使い方:
    PYTHONUTF8=1 uv run python scripts/load_delisted_stocks.py

    # テスト実行（BQ書き込みなし）
    PYTHONUTF8=1 uv run python scripts/load_delisted_stocks.py --dry-run

    # 別ファイルを指定
    PYTHONUTF8=1 uv run python scripts/load_delisted_stocks.py --csv path/to/file.csv

注意:
    - WRITE_TRUNCATE モード（既存データを全削除して上書き）
    - IS_TOB_MBO カラムは NULL でロード（後で別途更新）
"""

import argparse
import sys
from pathlib import Path

import urllib3
import requests as _req
from requests.adapters import HTTPAdapter as _HA

urllib3.disable_warnings()

class _NoVerify(_HA):
    def send(self, req, **kw):
        kw["verify"] = False
        return super().send(req, **kw)

_orig = _req.Session.__init__
def _p(self, *a, **kw):
    _orig(self, *a, **kw)
    self.mount("https://", _NoVerify())
    self.verify = False
_req.Session.__init__ = _p

import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account

# ---- 設定 ----
PROJECT_ID = "gmailpj-357912"
DATASET_ID = "STOCK"
TABLE_ID = "DELISTED_STOCKS"
DEFAULT_CSV = r"C:\Users\zonekun\Dropbox\stock\py\jpx_delisted_stocks_2017_to_now.csv"
KEY_PATH = "keys/gcp-service-account.json"

COLUMN_MAP = {
    "上場廃止日": "DELISTING_DATE",
    "銘柄名": "COMPANY_NAME",
    "コード": "TICKER",
    "市場区分": "MARKET_SEGMENT",
    "上場廃止理由": "DELISTING_REASON",
    "取得元年度": "FISCAL_YEAR",
}

BQ_SCHEMA = [
    bigquery.SchemaField("DELISTING_DATE", "DATE"),
    bigquery.SchemaField("COMPANY_NAME", "STRING"),
    bigquery.SchemaField("TICKER", "STRING"),
    bigquery.SchemaField("MARKET_SEGMENT", "STRING"),
    bigquery.SchemaField("DELISTING_REASON", "STRING"),
    bigquery.SchemaField("FISCAL_YEAR", "INT64"),
    bigquery.SchemaField("IS_TOB_MBO", "BOOL"),
]


def load_csv(csv_path: str) -> pd.DataFrame:
    """CSVを読み込んでBQ用DataFrameに変換する。"""
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    print(f"CSV読み込み完了: {len(df)}行")
    print(f"カラム: {df.columns.tolist()}")

    # カラム名をBQ仕様にリネーム
    df = df.rename(columns=COLUMN_MAP)

    # DELISTING_DATE: 日付型に変換
    df["DELISTING_DATE"] = pd.to_datetime(df["DELISTING_DATE"], format="%Y/%m/%d").dt.date

    # TICKER: 文字列（4桁コード）に統一
    df["TICKER"] = df["TICKER"].astype(str).str.zfill(4)

    # FISCAL_YEAR: int（"2026(最新)" など括弧付きの値を数字部分のみ抽出）
    df["FISCAL_YEAR"] = df["FISCAL_YEAR"].astype(str).str.extract(r"(\d{4})")[0].astype("Int64")

    # IS_TOB_MBO: NULL（後で別途更新）
    df["IS_TOB_MBO"] = None
    df["IS_TOB_MBO"] = df["IS_TOB_MBO"].astype("boolean")

    # カラム順をBQスキーマに合わせる
    col_order = [f.name for f in BQ_SCHEMA]
    df = df[col_order]

    print("\n先頭3行:")
    print(df.head(3).to_string())
    print(f"\nデータ型:\n{df.dtypes}")
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=DEFAULT_CSV, help="CSVファイルパス")
    parser.add_argument("--dry-run", action="store_true", help="BQ書き込みなしで確認のみ")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"ERROR: ファイルが見つかりません: {csv_path}", file=sys.stderr)
        sys.exit(1)

    df = load_csv(str(csv_path))

    if args.dry_run:
        print("\n[DRY RUN] BQ書き込みをスキップします。")
        return

    # BigQuery クライアント
    credentials = service_account.Credentials.from_service_account_file(KEY_PATH)
    client = bigquery.Client(project=PROJECT_ID, credentials=credentials)

    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

    job_config = bigquery.LoadJobConfig(
        schema=BQ_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )

    print(f"\nBQロード開始: {table_ref} (WRITE_TRUNCATE)")
    job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
    job.result()
    print(f"ロード完了: {job.output_rows}行")


if __name__ == "__main__":
    main()

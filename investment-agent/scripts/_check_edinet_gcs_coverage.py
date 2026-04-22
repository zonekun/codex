"""Phase 0: GCS EDINET 有報年 カバレッジ調査。

gs://stock_data_1930932/edinet/{ticker}/ を全ticker走査、
2013-2026 の 有報年 (docTypeCode=120 / ファイル名に「有報年」を含む) を在庫表化。

出力:
  data/logs/edinet_gcs_coverage_YYYYMMDD.csv
    columns: TICKER, YEAR_2013, YEAR_2014, ..., YEAR_2026 (1=有り, 0=無し)
  サマリー: ticker_count, year別カバー数、不足件数合計
"""
from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import pandas as pd
from google.cloud import storage
from google.cloud import bigquery
from google.oauth2 import service_account

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

KEY_FILE = "keys/gcp-service-account.json"
PROJECT = "gmailpj-357912"
BUCKET = "stock_data_1930932"
JST = timezone(timedelta(hours=9))


def get_clients():
    creds = service_account.Credentials.from_service_account_file(
        KEY_FILE, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return (
        storage.Client(project=PROJECT, credentials=creds),
        bigquery.Client(project=PROJECT, credentials=creds),
    )


def list_active_tickers(bq: bigquery.Client) -> list[str]:
    """現在上場 + 廃止銘柄の全tickerを返す。"""
    sql = f"""
    SELECT DISTINCT TICKER FROM (
      SELECT TICKER FROM `{PROJECT}.STOCK.STOCK_CODE_LIST` WHERE EXCHANGE = 'TSE'
      UNION ALL
      SELECT TICKER FROM `{PROJECT}.STOCK.DELISTED_STOCKS`
    )
    ORDER BY TICKER
    """
    df = bq.query(sql).to_dataframe()
    return df["TICKER"].tolist()


def scan_ticker_coverage(storage_client: storage.Client, ticker: str) -> dict[int, list[str]]:
    """指定tickerのGCS edinet 配下をスキャン、年→docID リストを返す。"""
    bucket = storage_client.bucket(BUCKET)
    prefix = f"edinet/{ticker}/"
    year_to_docids: dict[int, list[str]] = defaultdict(list)
    for blob in storage_client.list_blobs(BUCKET, prefix=prefix):
        name = os.path.basename(blob.name)
        # パターン: {ticker}_有報年_{提出日YYYYMMDD}_{書類種別}_{docID}_...
        if "_有報年_" not in name:
            continue
        m = re.match(r"^\d+_有報年_(\d{8})_[^_]+_(S\d+[A-Z]*)_", name)
        if not m:
            continue
        submit_date = m.group(1)
        doc_id = m.group(2)
        submit_year = int(submit_date[:4])
        # 有報は通常 3月期決算なら6月提出、12月期決算なら3月提出など
        # FISCAL_YEAR_END を厳密に出すには XBRL パースが必要だが、近似として「提出年」で集計
        year_to_docids[submit_year].append(doc_id)
    return year_to_docids


def main():
    storage_client, bq = get_clients()
    print("[info] loading active tickers from BQ...")
    tickers = list_active_tickers(bq)
    print(f"[info] ticker count: {len(tickers)}")

    target_years = list(range(2013, 2027))  # 2013-2026
    coverage: dict[str, dict[int, int]] = {}

    for i, ticker in enumerate(tickers, 1):
        if i % 100 == 0:
            print(f"  [{i}/{len(tickers)}] {ticker}")
        try:
            year_to_docids = scan_ticker_coverage(storage_client, ticker)
        except Exception as e:
            print(f"  [{ticker}] err: {e}")
            year_to_docids = {}
        coverage[ticker] = {y: len(year_to_docids.get(y, [])) for y in target_years}

    # CSV 出力
    os.makedirs("data/logs", exist_ok=True)
    rows = []
    for ticker, ymap in coverage.items():
        row = {"TICKER": ticker}
        for y in target_years:
            row[f"Y{y}"] = ymap[y]
        rows.append(row)
    df = pd.DataFrame(rows)
    out_path = f"data/logs/edinet_gcs_coverage_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}.csv"
    df.to_csv(out_path, index=False, encoding="utf-8")
    print(f"\n[info] saved: {out_path}")

    # サマリー
    total_records = len(tickers) * len(target_years)
    covered = sum((df[[f"Y{y}" for y in target_years]] > 0).sum().sum() for _ in [0])
    print(f"\n=== カバレッジサマリー ===")
    print(f"ticker数: {len(tickers)}")
    print(f"対象年数: {len(target_years)} ({target_years[0]}-{target_years[-1]})")
    print(f"理論上の総レコード: {total_records}")
    print(f"GCS存在: {covered}  ({covered*100/total_records:.1f}%)")
    print(f"不足: {total_records - covered}  ({(total_records-covered)*100/total_records:.1f}%)")
    print()
    print("=== 年別カバー数 ===")
    for y in target_years:
        n = (df[f"Y{y}"] > 0).sum()
        print(f"  Y{y}: {n}/{len(tickers)}  ({n*100/len(tickers):.1f}%)")


if __name__ == "__main__":
    main()

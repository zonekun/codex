"""
4社の抽出デバッグ: extract_monthly_data.py の extract_from_tdnet_text を直接テスト
実行: PYTHONUTF8=1 uv run python scripts/debug_extract.py
"""
import sys
sys.path.insert(0, "scripts")

import json
import re
from google.oauth2 import service_account
from google.cloud import storage, bigquery

KEY_FILE = r"G:\マイドライブ\claude\investment-agent\keys\gcp-service-account.json"
BUCKET = "stock_data_1930932"

creds = service_account.Credentials.from_service_account_file(KEY_FILE)
gcs = storage.Client(credentials=creds, project="gmailpj-357912")
bq = bigquery.Client(credentials=creds, project="gmailpj-357912")
bucket = gcs.bucket(BUCKET)

# extract_monthly_data.py から抽出関数をインポート
from extract_monthly_data import extract_from_tdnet_text

TICKERS = ["2305", "4177", "7185", "7674"]

for ticker in TICKERS:
    print(f"\n{'='*60}")
    print(f"[{ticker}]")

    # アダプター読み込み
    try:
        blob = bucket.blob(f"monthlydata/{ticker}/extract_adapter.json")
        adapter = json.loads(blob.download_as_text())
        print(f"  アダプター: source={adapter.get('source')}, fields={len(adapter.get('fields', []))}件")
    except Exception as e:
        print(f"  アダプター読み込み失敗: {e}")
        continue

    # BQ から最新文書取得
    sql = f"""
    SELECT TICKER, SUBMISSION_DATE, DOC_TITLE, FILE_NAME,
      STRING_AGG(CHUNK_TEXT, ' ') AS full_text
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE TICKER = @ticker
      AND (MAIN_CATEGORY = '月次開示' OR EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) AS sc WHERE sc = '月次開示'))
      AND SUBMISSION_DATE >= '2025-01-01'
    GROUP BY TICKER, SUBMISSION_DATE, DOC_TITLE, FILE_NAME
    ORDER BY SUBMISSION_DATE DESC
    LIMIT 2
    """
    job_cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("ticker", "STRING", ticker)]
    )
    df = bq.query(sql, job_config=job_cfg).to_dataframe()
    print(f"  BQ文書数: {len(df)}件")

    for _, row in df.iterrows():
        doc_title = row["DOC_TITLE"]
        submission_date = str(row["SUBMISSION_DATE"])
        full_text = row["full_text"]
        print(f"\n  文書: {doc_title}")
        print(f"  提出日: {submission_date}")
        print(f"  テキスト長: {len(full_text)}文字")

        result = extract_from_tdnet_text(full_text, adapter, doc_title, submission_date)
        print(f"  抽出結果: {result}")

        if result is None:
            # _parse_year_month デバッグ
            from extract_monthly_data import _parse_year_month
            ym = _parse_year_month(adapter, doc_title, submission_date)
            print(f"  year_month解析: {ym}")

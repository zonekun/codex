"""24社が前回0件・今回ありの理由調査.

各社について:
- yesterday extract-monthly-data-66dpm の処理ログ (skip/success/error)
- monthly_records.json の updated_at（今日初か）
- monthly/docs/{ticker}/ の PDF 数とtime_created
"""
from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path
from google.cloud import storage
from google.cloud.logging_v2 import Client as LoggingClient
from google.oauth2 import service_account

creds = service_account.Credentials.from_service_account_file('keys/gcp-service-account.json')
gcs = storage.Client(project='gmailpj-357912', credentials=creds)
bucket = gcs.bucket('stock_data_1930932')
log_client = LoggingClient(project='gmailpj-357912', credentials=creds)

TICKERS = ['8173','7601','5891','3083','8218','7603','9262','7513','8040','8167','2726','9854','2730','1840','9831','9835','3543','3395','9031','7682','3544','3931','8244','9044']

# 1. monthly_records.json updated_at と record count
print('=== monthly_records.json status ===')
for t in TICKERS:
    b = bucket.blob(f'monthly/record/{t}/monthly_records.json')
    if b.exists():
        try:
            d = json.loads(b.download_as_text())
            print(f'  {t}: updated={d.get("updated_at","")[:19]}, count={d.get("record_count",len(d.get("records",[])))}')
        except Exception as e:
            print(f'  {t}: parse err {e}')
    else:
        print(f'  {t}: NOT EXIST')

# 2. yesterday extract-monthly-data-66dpm logs
print('\n=== Cloud Run logs (extract-monthly-data-66dpm yesterday) ===')
filter_str = (
    'resource.type=cloud_run_job AND '
    'labels."run.googleapis.com/execution_name"="extract-monthly-data-66dpm"'
)
logs_by_ticker = defaultdict(list)
for entry in log_client.list_entries(filter_=filter_str, page_size=1000, max_results=20000):
    payload = entry.payload if isinstance(entry.payload, str) else str(entry.payload)
    for t in TICKERS:
        if f' {t} ' in payload or f'[{t}]' in payload:
            logs_by_ticker[t].append((entry.timestamp.isoformat(), payload[:300]))

for t in TICKERS:
    entries = sorted(logs_by_ticker.get(t, []))
    print(f'\n--- {t} ({len(entries)} 件) ---')
    for ts, msg in entries[:8]:  # 先頭8件
        print(f'{ts[:19]} | {msg.strip()[:200]}')

# 3. GCS PDF time_created (まとめ)
print('\n=== GCS PDFs summary ===')
for t in TICKERS:
    blobs = list(bucket.list_blobs(prefix=f'monthly/docs/{t}/'))
    if not blobs:
        print(f'  {t}: PDF 0件')
        continue
    latest = max(b.time_created for b in blobs)
    earliest = min(b.time_created for b in blobs)
    print(f'  {t}: PDF {len(blobs)}件、最新 {latest.strftime("%Y-%m-%d %H:%M")} UTC')

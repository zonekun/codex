"""24社が yesterday extract-monthly-data-66dpm で処理されたか深堀り.

具体的に:
1. 66dpm の args 確認 (`--all` だったか tickers 制限だったか)
2. 各 ticker の Cloud Run logs を厳密 filter で検索 (textPayload=~"\\[\\d+/\\d+\\] {ticker}")
3. 「抽出レコードなし → スキップ」「成功」「対象外」のどれか分類
4. 過去 monthly_records.json の updated_at で「今日初書き込み」確認
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

print('=== 1. 66dpm execution args ===')
import subprocess
# gcloud で execution describe
r = subprocess.run(
    ['cmd', '/c', 'gcloud', 'run', 'jobs', 'executions', 'describe', 'extract-monthly-data-66dpm',
     '--region', 'us-west1', '--project', 'gmailpj-357912', '--format=json'],
    capture_output=True, text=True, encoding='utf-8'
)
if r.returncode == 0:
    data = json.loads(r.stdout)
    args = data.get('spec', {}).get('template', {}).get('spec', {}).get('containers', [{}])[0].get('args', [])
    print(f'  args ({len(args)}): {args[:5]} ... {args[-5:]}')
    print(f'  --tickers in args: {"--tickers" in args}')
    print(f'  --all in args: {"--all" in args}')
else:
    print(f'  err: {r.stderr[:500]}')

print('\n=== 2. 66dpm 完了サマリ ===')
filter_str = (
    'resource.type=cloud_run_job AND '
    'labels."run.googleapis.com/execution_name"="extract-monthly-data-66dpm" AND '
    '(textPayload=~"完了" OR textPayload=~"成功=" OR textPayload=~"スキップ=")'
)
for entry in log_client.list_entries(filter_=filter_str, page_size=20, max_results=20):
    payload = entry.payload if isinstance(entry.payload, str) else str(entry.payload)
    if '完了' in payload or '成功=' in payload or 'スキップ=' in payload:
        print(f'  {entry.timestamp.isoformat()[:19]} | {payload[:200]}')

print('\n=== 3. 各 ticker logs (厳密 filter) ===')
# 「[N/408] {ticker}」の文字列パターンを各 ticker で検索
ticker_results = {}
for t in TICKERS:
    filter_str = (
        f'resource.type=cloud_run_job AND '
        f'labels."run.googleapis.com/execution_name"="extract-monthly-data-66dpm" AND '
        f'(textPayload=~"\\\\] {t} " OR textPayload=~"\\\\[{t}\\\\]")'
    )
    found = []
    try:
        for entry in log_client.list_entries(filter_=filter_str, page_size=20, max_results=20):
            payload = entry.payload if isinstance(entry.payload, str) else str(entry.payload)
            found.append((entry.timestamp.isoformat(), payload[:250]))
    except Exception as e:
        found.append(('ERR', str(e)))
    ticker_results[t] = found

print(f'\n--- ticker 別 66dpm ログ件数 ---')
for t in TICKERS:
    n = len(ticker_results[t])
    print(f'  {t}: {n} 件')

# 詳細表示（最初4社）
for t in TICKERS[:4]:
    print(f'\n--- {t} 詳細 ---')
    for ts, msg in ticker_results[t][:5]:
        print(f'  {ts[:19]} | {msg.strip()[:200]}')

# 4. monthly_records.json updated_at で「今日初」確認
print('\n=== 4. records updated_at vs 過去 ===')
for t in TICKERS:
    b = bucket.blob(f'monthly/record/{t}/monthly_records.json')
    if b.exists():
        d = json.loads(b.download_as_text())
        # records sample
        sample = (d.get('records') or [])[:1]
        sample_sub = sample[0].get('submission_date', '?') if sample else '-'
        print(f'  {t}: updated={d.get("updated_at","")[:19]}, count={len(d.get("records",[]))}, sample sub={sample_sub}')

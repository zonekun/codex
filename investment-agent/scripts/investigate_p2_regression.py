"""劣化 10社の個別調査.

各 ticker:
- 前回CSV / 今回CSV のレコード比較
- 現在 extract_adapter (source / fields / overwrite_past_months)
- monthly_records.json 現状
- extract-monthly-data-m2j27 のログ
- 仮説と原因分類

出力: data/logs/regression_10_investigation.md
"""
from __future__ import annotations
import json
import csv
from collections import defaultdict
from pathlib import Path
from google.cloud import storage
from google.cloud.logging_v2 import Client as LoggingClient
from google.oauth2 import service_account

creds = service_account.Credentials.from_service_account_file('keys/gcp-service-account.json')
gcs = storage.Client(project='gmailpj-357912', credentials=creds)
bucket = gcs.bucket('stock_data_1930932')
log_client = LoggingClient(project='gmailpj-357912', credentials=creds)

TICKERS = ['3349', '1420', '3561', '6752', '7127', '7685', '3175', '2782', '2337', '7918']

# CSV読み込み
def read_csv(path):
    rows = defaultdict(list)
    with open(path, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            rows[row['ticker']].append(row)
    return rows

before = read_csv(r'C:\tmp\buffett_compare_20260417_221647.csv')
after = read_csv(r'C:\tmp\buffett_compare_20260418_173658.csv')

# Cloud Run logs 一括取得
print('Cloud Run logs 取得中...')
filter_str = (
    'resource.type=cloud_run_job AND '
    'labels."run.googleapis.com/execution_name"="extract-monthly-data-m2j27"'
)
logs_by_ticker = defaultdict(list)
ticker_set = set(TICKERS)
for entry in log_client.list_entries(filter_=filter_str, page_size=1000, max_results=20000):
    payload = entry.payload if isinstance(entry.payload, str) else str(entry.payload)
    for t in ticker_set:
        if f' {t} ' in payload or f'[{t}]' in payload or payload.startswith(f'  → ') and t in payload:
            logs_by_ticker[t].append((entry.timestamp.isoformat(), payload[:300]))

print(f'logs取得完了: {sum(len(v) for v in logs_by_ticker.values())} entries')

lines = ['# 劣化 10社 個別調査 (2026-04-18)', '']

for t in TICKERS:
    lines.append(f'\n---\n## {t}')

    # adapter
    try:
        ap = json.loads(bucket.blob(f'monthly/meta/{t}/extract_adapter.json').download_as_text())
    except Exception as e:
        lines.append(f'  adapter取得失敗: {e}')
        continue

    lines.append(f'- **source**: `{ap.get("source")}` / **format**: `{ap.get("format")}` / **method**: `{ap.get("extraction_method")}`')
    lines.append(f'- **overwrite_past_months**: `{ap.get("overwrite_past_months")}`')
    lines.append(f'- **manual_override**: `{ap.get("manual_override")}`')
    lines.append(f'- **fields**: {len(ap.get("fields", []))}')
    for f in ap.get('fields', []):
        bc_ignore = f.get('bc_ignore', False)
        scale = f.get('unit_scale')
        offset = f.get('yoy_offset')
        extras = []
        if bc_ignore: extras.append('bc_ignore=true')
        if scale: extras.append(f'unit_scale={scale}')
        if offset: extras.append(f'yoy_offset={offset}')
        lines.append(f'  - `{f.get("key")}` ({f.get("value_type")}, {",".join(extras) or "-"})')

    # records 現状
    try:
        rec = json.loads(bucket.blob(f'monthly/record/{t}/monthly_records.json').download_as_text())
        lines.append(f'\n### records 現状 ({len(rec.get("records", []))}件)')
        for r in (rec.get('records') or [])[:5]:
            ym = r.get('year_month', '')
            sub = r.get('submission_date', '')
            keys = list((r.get('fields') or {}).keys())
            lines.append(f'  - {ym} (sub={sub}) keys={keys[:3]}{"..." if len(keys)>3 else ""}')
    except Exception as e:
        lines.append(f'\n### records: 取得失敗 / 不在 ({e})')

    # 前回CSV
    bf = before.get(t, [])
    af = after.get(t, [])
    lines.append(f'\n### CSV: 前回 {len(bf)}行 / 今回 {len(af)}行')
    if bf:
        lines.append('**前回 (max3)**:')
        for row in bf[:3]:
            lines.append(f'  - {row["year_month"]} `{row["our_field"][:30]}` our={row["our_value"]} bc={row["bc_value"]} match={row["match"]}')
    if af:
        lines.append('**今回 (max3)**:')
        for row in af[:3]:
            lines.append(f'  - {row["year_month"]} `{row["our_field"][:30]}` our={row["our_value"]} bc={row["bc_value"]} match={row["match"]}')

    # logs
    entries = sorted(logs_by_ticker.get(t, []))
    lines.append(f'\n### Cloud Run logs ({len(entries)}件)')
    lines.append('```')
    for ts, msg in entries[-15:]:
        lines.append(f'{ts[:19]} | {msg.strip()[:250]}')
    lines.append('```')

Path('data/logs/regression_10_investigation.md').write_text('\n'.join(lines), encoding='utf-8')
print(f'書き出し: data/logs/regression_10_investigation.md ({len(lines)} 行)')

"""58社候補の深堀り調査用データを事前収集。

各社について:
- extract_adapter.json (GCS)
- monthly_records.json (GCS)
- BC compare CSVのその社のレコード
- STOCK_NAME

集約結果を data/logs/non_yearmonth_data.json に出す。
"""
from __future__ import annotations
import json
import csv
from pathlib import Path
from collections import defaultdict
from google.cloud import storage, bigquery
from google.oauth2 import service_account

creds = service_account.Credentials.from_service_account_file('keys/gcp-service-account.json')
gcs = storage.Client(project='gmailpj-357912', credentials=creds)
bq = bigquery.Client(project='gmailpj-357912', credentials=creds)
bucket = gcs.bucket('stock_data_1930932')

# 1. fixed済 38社を特定
fixed = set()
diff_file = Path('data/logs/desc_rewrite_diff.md')
if diff_file.exists():
    for line in diff_file.read_text(encoding='utf-8').splitlines():
        if line.startswith('## '):
            t = line[3:].split(' ')[0].strip()
            fixed.add(t)
fixed.update(['4015', '5580', '5589'])
print(f'既fix: {len(fixed)}')

# 2. 96社のGemini adapter一覧
gemini_tickers = []
for blob in bucket.list_blobs(prefix='monthly/meta/'):
    if not blob.name.endswith('/extract_adapter.json'):
        continue
    try:
        d = json.loads(blob.download_as_text())
    except Exception:
        continue
    if d.get('extraction_method') == 'gemini':
        t = blob.name.split('/')[2]
        gemini_tickers.append((t, d))

candidates = [(t, d) for t, d in gemini_tickers if t not in fixed]
print(f'候補社数: {len(candidates)}')

# 3. BC compare CSV
bc_ng = defaultdict(list)  # ticker -> [{ym, our_field, our_value, bc_field, bc_value, diff, match}]
compare_csv = r'C:\tmp\buffett_compare_20260417_221647.csv'
with open(compare_csv, encoding='utf-8-sig') as f:
    for row in csv.DictReader(f):
        if row['match'] in ('NG', 'BC_NODATA'):
            bc_ng[row['ticker']].append(row)

# 4. STOCK_NAME一括取得
sql = f"""SELECT TICKER, STOCK_NAME FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
WHERE TICKER IN ({','.join(repr(t) for t, _ in candidates)})"""
name_map = {r.TICKER: r.STOCK_NAME for r in bq.query(sql)}

# 5. 過去調査結果の抽出 (docs/plans/*_investigation*.md)
past_investigations = defaultdict(list)
for plan_md in Path('docs/plans').glob('*investigation*.md'):
    try:
        txt = plan_md.read_text(encoding='utf-8')
    except Exception:
        continue
    # ticker 4桁数字 or 英数字 を章タイトルから拾う
    import re
    for line in txt.split('\n'):
        m = re.match(r'^###\s+(\d{4}[A-Z]?|\d{3}[A-Z])\s+', line)
        if m:
            past_investigations[m.group(1)].append(plan_md.name)

# 6. 集約データ作成
report = []
for t, d in candidates:
    try:
        rec = json.loads(bucket.blob(f'monthly/record/{t}/monthly_records.json').download_as_text())
    except Exception:
        rec = {'records': [], 'updated_at': ''}

    has_2026 = any(r.get('year_month', '').startswith('2026') for r in rec.get('records', []))

    report.append({
        'ticker': t,
        'stock_name': name_map.get(t, ''),
        'adapter': {
            'source': d.get('source'),
            'format': d.get('format'),
            'sample_doc_title': d.get('sample_doc_title'),
            'sample_submission_date': d.get('sample_submission_date'),
            'created_at': d.get('created_at'),
            'manual_override': d.get('manual_override'),
            'fields': [
                {
                    'key': f.get('key'),
                    'description': f.get('description', ''),
                    'value_type': f.get('value_type'),
                    'row_label_regex': f.get('row_label_regex'),
                    'unit_scale': f.get('unit_scale'),
                    'group': f.get('group'),
                }
                for f in d.get('fields', [])
            ],
        },
        'records_count': len(rec.get('records', [])),
        'records_has_2026': has_2026,
        'records_latest_ym': max((r.get('year_month', '') for r in rec.get('records', [])), default=''),
        'records_updated_at': rec.get('updated_at', ''),
        'bc_ng_count': len(bc_ng.get(t, [])),
        'bc_ng_entries': bc_ng.get(t, [])[:20],
        'past_investigation_files': past_investigations.get(t, []),
    })

Path('data/logs/non_yearmonth_data.json').write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
)
print(f'書き出し: data/logs/non_yearmonth_data.json')
print(f'候補: {len(report)} / うちBC NGあり: {sum(1 for r in report if r["bc_ng_count"] > 0)}')
print(f'うち過去調査済: {sum(1 for r in report if r["past_investigation_files"])}')

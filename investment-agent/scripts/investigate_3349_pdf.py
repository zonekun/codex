"""3349 コスモス薬品 PDF実物詳細調査.

「いつもの方法」:
- adapter / records / BC値 突合
- 直近月次PDFを Dropbox `/stock/temp/3349_investigation/` にUP
- pdfplumber でテキスト抜粋
- BC正解値が PDF のどこから来てるか特定（BC逆引き）
"""
from __future__ import annotations
import io
import json
import csv
from collections import defaultdict
from pathlib import Path
from google.cloud import storage
from google.oauth2 import service_account
import dropbox
from dropbox.files import WriteMode
import pdfplumber

creds = service_account.Credentials.from_service_account_file('keys/gcp-service-account.json')
gcs = storage.Client(project='gmailpj-357912', credentials=creds)
bucket = gcs.bucket('stock_data_1930932')

DBX_APP_KEY = 't8feblcw74hoeky'
DBX_APP_SECRET = 'fcjgc37d034pw1n'
DBX_REFRESH_TOKEN = 'XwOxZlA8jPUAAAAAAAAAAZxnT4qRFtWLcShpKy3cNjTf3euIMqEZxCNieAQiLSDw'
DBX_FOLDER = '/stock/temp/3349_investigation'

dbx = dropbox.Dropbox(
    app_key=DBX_APP_KEY, app_secret=DBX_APP_SECRET, oauth2_refresh_token=DBX_REFRESH_TOKEN,
)
try:
    dbx.files_delete_v2(DBX_FOLDER)
except Exception:
    pass

lines = ['# 3349 コスモス薬品 PDF実物詳細調査', f'実施: 2026-04-18', '']

# adapter
ap = json.loads(bucket.blob('monthly/meta/3349/extract_adapter.json').download_as_text())
lines.append('## adapter')
lines.append(f'- source: `{ap.get("source")}`, method: `{ap.get("extraction_method")}`')
lines.append(f'- fields: {len(ap.get("fields", []))}')
lines.append(f'- overwrite_past_months: `{ap.get("overwrite_past_months")}`')
for f in ap.get('fields', []):
    lines.append(f'\n  **`{f.get("key")}`** ({f.get("value_type")}):')
    lines.append(f'  - description: {(f.get("description") or "")[:300]}')

# records
rec = json.loads(bucket.blob('monthly/record/3349/monthly_records.json').download_as_text())
lines.append(f'\n## records 現状 ({len(rec.get("records", []))}件)')
for r in rec.get('records', []):
    lines.append(f'- {r.get("year_month")} (sub={r.get("submission_date")}) fields={r.get("fields")}')

# BC values
bc_data = defaultdict(dict)
with open('data/csv/bc_monthly_kpi.csv', encoding='utf-8-sig') as f:
    for row in csv.DictReader(f):
        if row['ticker'] == '3349':
            try:
                bc_data[row['year_month']][row['field']] = float(row['value'])
            except (ValueError, TypeError):
                pass
lines.append(f'\n## BC側 値（直近6ヶ月）')
for ym in sorted(bc_data.keys(), reverse=True)[:6]:
    lines.append(f'\n**{ym}:**')
    for k, v in bc_data[ym].items():
        lines.append(f'  - `{k}`: {v}')

# 直近 monthly PDFs
print('Listing 3349 PDFs...')
all_pdfs = sorted([b for b in bucket.list_blobs(prefix='monthly/docs/3349/') if b.name.endswith('.pdf')], key=lambda b: b.name, reverse=True)
recent_pdfs = all_pdfs[:5]
lines.append(f'\n## 直近5件の月次PDF')
for b in recent_pdfs:
    lines.append(f'- {b.name.rsplit(chr(47), 1)[-1]} ({b.size:,} bytes)')

# Dropbox upload + text 抽出
for i, b in enumerate(recent_pdfs):
    fname = b.name.rsplit('/', 1)[-1]
    data = b.download_as_bytes()
    try:
        dbx.files_upload(data, f'{DBX_FOLDER}/{fname}', mode=WriteMode.overwrite, mute=True)
        print(f'UP: {fname}')
    except Exception as e:
        print(f'UP err: {e}')

    if i < 2:  # 上位2 PDF のテキスト抽出
        lines.append(f'\n### PDF text: {fname}')
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for j, page in enumerate(pdf.pages[:2]):
                    text = page.extract_text() or ''
                    lines.append(f'\n#### Page {j+1}')
                    lines.append('```')
                    lines.append(text[:3500])
                    lines.append('```')
        except Exception as e:
            lines.append(f'pdfplumber err: {e}')

# adapter 同様 Dropbox に
for fname in ['extract_adapter.json', 'monthly_records.json']:
    if fname.startswith('extract'):
        data = json.dumps(ap, ensure_ascii=False, indent=2).encode('utf-8')
    else:
        data = json.dumps(rec, ensure_ascii=False, indent=2).encode('utf-8')
    try:
        dbx.files_upload(data, f'{DBX_FOLDER}/{fname}', mode=WriteMode.overwrite, mute=True)
    except Exception as e:
        print(f'UP {fname} err: {e}')

Path('data/logs/3349_investigation.md').write_text('\n'.join(lines), encoding='utf-8')
print(f'\n書き出し: data/logs/3349_investigation.md')
print(f'Dropbox: {DBX_FOLDER}/')

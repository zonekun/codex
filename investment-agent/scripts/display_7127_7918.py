"""7127/7918 抽出 records 表示 + Dropbox UP."""
from __future__ import annotations
import json
from pathlib import Path
from google.cloud import storage
from google.oauth2 import service_account
import dropbox
from dropbox.files import WriteMode

creds = service_account.Credentials.from_service_account_file('keys/gcp-service-account.json')
gcs = storage.Client(project='gmailpj-357912', credentials=creds)
bucket = gcs.bucket('stock_data_1930932')

DBX_APP_KEY = 't8feblcw74hoeky'
DBX_APP_SECRET = 'fcjgc37d034pw1n'
DBX_REFRESH_TOKEN = 'XwOxZlA8jPUAAAAAAAAAAZxnT4qRFtWLcShpKy3cNjTf3euIMqEZxCNieAQiLSDw'
DBX_FOLDER = '/stock/temp/7127_7918_extract_report'

dbx = dropbox.Dropbox(
    app_key=DBX_APP_KEY, app_secret=DBX_APP_SECRET, oauth2_refresh_token=DBX_REFRESH_TOKEN,
)
try:
    dbx.files_delete_v2(DBX_FOLDER)
except Exception:
    pass

lines = ['# 7127/7918 再extract 結果レポート', '', f'実施: 2026-04-18 19:37 (BG bl89uclza)', '']
lines.append('## TDnet経由 extract_from_text_gemini プロンプト改修後の結果')
lines.append('')
lines.append('| ticker | 前回 (Round 2) | 今回 | 評価 |')
lines.append('|--------|----------------|------|------|')
lines.append('| 7127 | 83.3% | 5/6 = 83.3% | 横ばい |')
lines.append('| 7918 | 88.9% | 12/18 = 67% | **劣化** |')
lines.append('')
lines.append('全体: 24 fields, 17 OK / 7 NG, 一致率 70.8%')
lines.append('')

for t in ['7127', '7918']:
    print(f'\n========== {t} records ==========')
    lines.append(f'\n## {t} records')
    blob = bucket.blob(f'monthly/record/{t}/monthly_records.json')
    if not blob.exists():
        print('  records なし')
        continue
    data = json.loads(blob.download_as_text())
    records = data.get('records', [])
    print(f'  count: {len(records)}, updated_at: {data.get("updated_at", "")[:19]}')
    lines.append(f'\n- count: {len(records)}')
    lines.append(f'- updated_at: {data.get("updated_at", "")[:19]}')
    lines.append(f'\n```json')
    # adapter
    ap_blob = bucket.blob(f'monthly/meta/{t}/extract_adapter.json')
    if ap_blob.exists():
        ap = json.loads(ap_blob.download_as_text())
        # adapter は別UP
        dbx.files_upload(
            json.dumps(ap, ensure_ascii=False, indent=2).encode('utf-8'),
            f'{DBX_FOLDER}/{t}_extract_adapter.json',
            mode=WriteMode.overwrite, mute=True,
        )
        print(f'  adapter source: {ap.get("source")} method: {ap.get("extraction_method")}')
        lines.append(f'  // adapter: source={ap.get("source")} method={ap.get("extraction_method")}')

    # records 全件 表示
    for r in records:
        ym = r.get('year_month', '?')
        sub = r.get('submission_date', '?')
        flds = r.get('fields', {})
        flds_str = json.dumps(flds, ensure_ascii=False)
        line = f'  {ym} sub={sub} fields={flds_str}'
        print(line)
        lines.append(line)
    lines.append('```')

    # records.json 全体を Dropbox UP
    dbx.files_upload(
        json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'),
        f'{DBX_FOLDER}/{t}_monthly_records.json',
        mode=WriteMode.overwrite, mute=True,
    )

# レポート MD UP
report_md = '\n'.join(lines)
Path('data/logs/7127_7918_report.md').write_text(report_md, encoding='utf-8')
dbx.files_upload(
    report_md.encode('utf-8'),
    f'{DBX_FOLDER}/REPORT.md',
    mode=WriteMode.overwrite, mute=True,
)

# BC compare CSV (関連2社のみ抜粋) UP
import csv
src_csv = Path('C:/tmp/buffett_compare_20260418_193948.csv')
if src_csv.exists():
    with open(src_csv, encoding='utf-8-sig') as f:
        rows = [r for r in csv.DictReader(f) if r.get('ticker') in ('7127', '7918')]
    if rows:
        out = Path('C:/tmp/7127_7918_bc_compare.csv')
        with open(out, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)
        dbx.files_upload(out.read_bytes(), f'{DBX_FOLDER}/bc_compare_7127_7918.csv', mode=WriteMode.overwrite, mute=True)

print(f'\nDropbox: {DBX_FOLDER}/')
print(f'  REPORT.md / 7127_extract_adapter.json / 7127_monthly_records.json')
print(f'  / 7918_extract_adapter.json / 7918_monthly_records.json / bc_compare_7127_7918.csv')

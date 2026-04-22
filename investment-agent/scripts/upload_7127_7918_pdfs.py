"""7127/7918 今回extract対象 月次開示PDFを Dropbox UP."""
from __future__ import annotations
import json
from google.cloud import storage
from google.oauth2 import service_account
import dropbox
from dropbox.files import WriteMode

creds = service_account.Credentials.from_service_account_file('keys/gcp-service-account.json')
gcs = storage.Client(project='gmailpj-357912', credentials=creds)
bucket = gcs.bucket('stock_data_1930932')

DBX_FOLDER = '/stock/temp/7127_7918_extract_report'
dbx = dropbox.Dropbox(
    app_key='t8feblcw74hoeky',
    app_secret='fcjgc37d034pw1n',
    oauth2_refresh_token='XwOxZlA8jPUAAAAAAAAAAZxnT4qRFtWLcShpKy3cNjTf3euIMqEZxCNieAQiLSDw',
)

for t in ['7127', '7918']:
    rec = json.loads(bucket.blob(f'monthly/record/{t}/monthly_records.json').download_as_text())
    sub_dates = sorted({r.get('submission_date', '') for r in rec.get('records', []) if r.get('submission_date')})
    sub_yyyymmdd = {sd.replace('-', '') for sd in sub_dates}
    print(f'\n=== {t}: extract対象 sub={len(sub_dates)}件 ===')

    blobs = list(bucket.list_blobs(prefix=f'tdnet/{t}/'))
    monthly_blobs = [b for b in blobs if '_月次開示_' in b.name]
    matched = [b for b in monthly_blobs if any(b.name.split('/')[-1].startswith(ymd) for ymd in sub_yyyymmdd)]
    print(f'  全月次PDF: {len(monthly_blobs)} / extract対象一致: {len(matched)}')

    for i, b in enumerate(matched, 1):
        fname = b.name.split('/')[-1]
        try:
            data = b.download_as_bytes()
            dbx.files_upload(data, f'{DBX_FOLDER}/pdfs_{t}/{fname}', mode=WriteMode.overwrite, mute=True)
            print(f'  [{i}/{len(matched)}] {fname}')
        except Exception as e:
            print(f'  ERR {fname}: {e}')

print(f'\nDropbox: {DBX_FOLDER}/pdfs_7127/ , pdfs_7918/')

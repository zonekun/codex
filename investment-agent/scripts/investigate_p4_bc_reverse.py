"""P4 軽微 diff 7社の BC逆引き調査.

各社について:
1. 最新の月次PDF を GCS から取得 → Dropbox `/stock/temp/p4_bc_reverse/{ticker}/` にアップ
2. BC正解値（直近月）を `bc_monthly_kpi.csv` から取り出し
3. 我々の monthly_records 値も並べる
4. md にまとめて、ユーザーが PDF と照合する材料を提供

対象:
- 3329 東和フードサービス (diff 0.6-5.0)
- 7606 ユナイテッドアローズ (diff 2.5-3.8)
- 2654 アスモ (diff 5-8)
- 2750 石光商事 (diff 1-24)
- 8203 ＭｒＭａｘHD (diff 1.3-2.8)
- 8255 アクシアル (diff 0.6-2.2)
- 428A サイプレスHD (diff 1-3.4)
"""
from __future__ import annotations
import json
import csv
from collections import defaultdict
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
DBX_FOLDER = '/stock/temp/p4_bc_reverse'

dbx = dropbox.Dropbox(
    app_key=DBX_APP_KEY, app_secret=DBX_APP_SECRET, oauth2_refresh_token=DBX_REFRESH_TOKEN,
)

P4_TICKERS = ['3329', '7606', '2654', '2750', '8203', '8255', '428A']

# BC データ
bc_data = defaultdict(lambda: defaultdict(dict))
with open('data/csv/bc_monthly_kpi.csv', encoding='utf-8-sig') as f:
    for row in csv.DictReader(f):
        try:
            bc_data[row['ticker']][row['year_month']][row['field']] = float(row['value'])
        except (ValueError, TypeError):
            pass

# 古いフォルダ削除
for t in P4_TICKERS:
    try:
        dbx.files_delete_v2(f'{DBX_FOLDER}/{t}')
    except Exception:
        pass

lines = ['# P4 BC逆引き調査', f'対象: {len(P4_TICKERS)}社', '']

for ticker in P4_TICKERS:
    print(f'=== {ticker} ===')
    lines.append(f'\n---\n## {ticker}')

    # adapter
    try:
        d = json.loads(bucket.blob(f'monthly/meta/{ticker}/extract_adapter.json').download_as_text())
    except Exception as e:
        lines.append(f'  adapter取得失敗: {e}')
        continue

    lines.append(f'- source: `{d.get("source")}`')
    lines.append(f'- sample_doc_title: `{d.get("sample_doc_title")}`')

    # records
    try:
        rec = json.loads(bucket.blob(f'monthly/record/{ticker}/monthly_records.json').download_as_text())
    except Exception:
        rec = {'records': []}

    # 最新の3 records
    recent = sorted(rec.get('records', []), key=lambda r: r.get('year_month', ''), reverse=True)[:3]

    lines.append(f'\n### Our values (直近3ヶ月)')
    for r in recent:
        ym = r.get('year_month', '')
        sub = r.get('submission_date', '')
        title = r.get('doc_title', '')[:60]
        lines.append(f'- **{ym}** (sub={sub}, title=`{title}`):')
        for k, v in (r.get('fields') or {}).items():
            bc_v = bc_data[ticker].get(ym, {}).get(k, '?')
            mark = '✅' if abs(float(v) - float(bc_v)) <= 0.5 else '❌' if isinstance(bc_v, (int, float)) else '⚠️'
            lines.append(f'  - {mark} `{k}`: our={v} vs BC={bc_v}')

    lines.append(f'\n### BC values (直近3ヶ月、参考)')
    for ym_key in sorted(bc_data[ticker].keys(), reverse=True)[:3]:
        lines.append(f'- **{ym_key}**:')
        for k, v in bc_data[ticker][ym_key].items():
            lines.append(f'  - `{k}`: {v}')

    # PDFアップロード（最新3〜5本）
    pdf_uploaded = 0
    if d.get('source') == 'tdnet':
        # records 内の doc_title を逆引き
        used_titles = set(r.get('doc_title', '') for r in recent if r.get('doc_title'))
        for blob in bucket.list_blobs(prefix=f'tdnet/{ticker}/'):
            if not blob.name.endswith('.pdf'):
                continue
            stem = blob.name.rsplit('/', 1)[-1].replace('.pdf', '')
            for ut in used_titles:
                if ut and (ut in stem or stem in ut):
                    try:
                        data = blob.download_as_bytes()
                        dbx_path = f'{DBX_FOLDER}/{ticker}/{blob.name.rsplit("/", 1)[-1]}'
                        dbx.files_upload(data, dbx_path, mode=WriteMode.overwrite, mute=True)
                        pdf_uploaded += 1
                        break
                    except Exception as e:
                        print(f'  upload err: {e}')
            if pdf_uploaded >= 5:
                break
    elif d.get('source') == 'pdf':
        # 非TDnet: monthly/docs/{ticker}/ の直近 PDF を探す
        all_pdfs = sorted(
            [b for b in bucket.list_blobs(prefix=f'monthly/docs/{ticker}/') if b.name.endswith('.pdf')],
            key=lambda b: b.name, reverse=True
        )
        for b in all_pdfs[:5]:
            try:
                data = b.download_as_bytes()
                dbx_path = f'{DBX_FOLDER}/{ticker}/{b.name.rsplit("/", 1)[-1]}'
                dbx.files_upload(data, dbx_path, mode=WriteMode.overwrite, mute=True)
                pdf_uploaded += 1
            except Exception as e:
                print(f'  upload err: {e}')

    lines.append(f'\n### Dropbox PDF (`{DBX_FOLDER}/{ticker}/`): {pdf_uploaded}件')

    # extract_adapter も Dropbox に
    try:
        adp_data = json.dumps(d, ensure_ascii=False, indent=2).encode('utf-8')
        dbx.files_upload(adp_data, f'{DBX_FOLDER}/{ticker}/extract_adapter.json', mode=WriteMode.overwrite, mute=True)
    except Exception as e:
        lines.append(f'  adapter upload err: {e}')

out = Path('data/logs/p4_bc_reverse_investigation.md')
out.write_text('\n'.join(lines), encoding='utf-8')
print(f'\n書き出し: {out}')
print(f'Dropbox: {DBX_FOLDER}/')

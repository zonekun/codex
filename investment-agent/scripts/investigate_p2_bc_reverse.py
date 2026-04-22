"""P2 6社 BC逆引き深堀り調査.

対象: 6627, 6301, 8739, 4666, 3358, 3663
- adapter現状 / records / BC値
- 最新の月次PDF を pdfplumber で開いて該当行を特定
- BC値が PDF のどこから抽出できるかを逆算
- 修正提案を md にまとめる
"""
from __future__ import annotations
import io
import json
import csv
import re
from collections import defaultdict
from pathlib import Path
from google.cloud import storage, bigquery
from google.oauth2 import service_account

creds = service_account.Credentials.from_service_account_file('keys/gcp-service-account.json')
gcs = storage.Client(project='gmailpj-357912', credentials=creds)
bucket = gcs.bucket('stock_data_1930932')
bq = bigquery.Client(project='gmailpj-357912', credentials=creds)

P2_TICKERS = ['6627', '6301', '8739', '4666', '3358', '3663']

# BC データ
bc_data = defaultdict(lambda: defaultdict(dict))
with open('data/csv/bc_monthly_kpi.csv', encoding='utf-8-sig') as f:
    for row in csv.DictReader(f):
        try:
            bc_data[row['ticker']][row['year_month']][row['field']] = float(row['value'])
        except (ValueError, TypeError):
            pass


def get_pdf_text(blob_name: str, max_pages: int = 3) -> str:
    """GCS PDFをpdfplumberで開いてテキスト返す。"""
    try:
        import pdfplumber
        b = bucket.blob(blob_name)
        with pdfplumber.open(io.BytesIO(b.download_as_bytes())) as pdf:
            text = '\n'.join((page.extract_text() or '') for page in pdf.pages[:max_pages])
        return text
    except Exception as e:
        return f'[ERROR pdfplumber: {e}]'


def get_latest_pdf_blob(ticker: str, source: str) -> str | None:
    """最新の月次PDFのGCSパスを返す。"""
    if source == 'tdnet':
        prefix = f'tdnet/{ticker}/'
        blobs = [b for b in bucket.list_blobs(prefix=prefix)
                 if b.name.endswith('.pdf') and ('月次' in b.name or 'monthly' in b.name.lower() or 'Monthly' in b.name)]
    else:
        prefix = f'monthly/docs/{ticker}/'
        blobs = [b for b in bucket.list_blobs(prefix=prefix) if b.name.endswith('.pdf')]
    if not blobs:
        return None
    blobs.sort(key=lambda b: b.name, reverse=True)
    return blobs[0].name


lines = ['# P2 6社 BC逆引き深堀り調査 (2026-04-18)', '', 'PDF実物の該当セクション × adapter/records/BC値 突合']

for ticker in P2_TICKERS:
    print(f'=== {ticker} ===')
    lines.append(f'\n---\n## {ticker}')

    # adapter
    try:
        ap = json.loads(bucket.blob(f'monthly/meta/{ticker}/extract_adapter.json').download_as_text())
    except Exception as e:
        lines.append(f'adapter取得失敗: {e}')
        continue
    lines.append(f'- source: `{ap.get("source")}`, sample_doc_title: `{ap.get("sample_doc_title")}`')

    # records (latest 3)
    try:
        rec = json.loads(bucket.blob(f'monthly/record/{ticker}/monthly_records.json').download_as_text())
    except Exception:
        rec = {'records': []}
    recent = sorted(rec.get('records', []), key=lambda r: r.get('year_month', ''), reverse=True)[:3]
    lines.append(f'\n### Adapter fields')
    for f in ap.get('fields', []):
        lines.append(f'- `{f.get("key")}` (vt={f.get("value_type")}, scale={f.get("unit_scale")}, group={f.get("group")})')
        if f.get('row_label_regex'):
            lines.append(f'  - regex: `{f.get("row_label_regex")[:120]}`')
        if f.get('description'):
            lines.append(f'  - desc: `{f.get("description")[:200]}`')

    lines.append(f'\n### Our records vs BC (直近3ヶ月)')
    lines.append('| ym | field | our | BC |')
    lines.append('|----|----|----|----|')
    for r in recent:
        ym = r.get('year_month', '')
        for k, v in (r.get('fields') or {}).items():
            bc_v = bc_data[ticker].get(ym, {}).get(k, '?')
            lines.append(f'| {ym} | {k[:30]} | {v} | {bc_v} |')

    # BC values（adapter にない field 含めて全部）
    lines.append(f'\n### BC全フィールド（直近3ヶ月、参考）')
    bc_yms = sorted(bc_data[ticker].keys(), reverse=True)[:3]
    for ym in bc_yms:
        lines.append(f'\n**{ym}**:')
        for k, v in bc_data[ticker][ym].items():
            lines.append(f'  - `{k}`: {v}')

    # 最新PDF text
    pdf_path = get_latest_pdf_blob(ticker, ap.get('source'))
    lines.append(f'\n### 最新PDF抜粋')
    if pdf_path:
        lines.append(f'`{pdf_path}`')
        text = get_pdf_text(pdf_path, max_pages=4)
        # 該当しそうな部分 (adapter の row_label_regex に出てくる単語) でハイライト
        keywords = set()
        for f in ap.get('fields', []):
            for re_str in [f.get('row_label_regex') or '', f.get('description') or '', f.get('key') or '']:
                # 漢字単語抽出
                for m in re.findall(r'[一-龥]+', re_str):
                    if 2 <= len(m) <= 5:
                        keywords.add(m)
        # PDFの 80行を表示
        plines = text.split('\n')
        lines.append('```')
        for line in plines[:120]:
            mark = ''
            for kw in keywords:
                if kw in line:
                    mark = ' ←★'
                    break
            lines.append(f'{line}{mark}'[:200])
        lines.append('```')
    else:
        lines.append(f'PDF not found in tdnet/{ticker}/ or monthly/docs/{ticker}/')

Path('data/logs/p2_bc_reverse_investigation.md').write_text('\n'.join(lines), encoding='utf-8')
print(f'\n書き出し: data/logs/p2_bc_reverse_investigation.md ({len(lines)} 行)')

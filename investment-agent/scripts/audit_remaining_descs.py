"""真に月指定を含む description を精密に抽出."""
import json
import re
from pathlib import Path
from google.cloud import storage
from google.oauth2 import service_account

creds = service_account.Credentials.from_service_account_file('keys/gcp-service-account.json')
gcs = storage.Client(project='gmailpj-357912', credentials=creds)
bucket = gcs.bucket('stock_data_1930932')

rewrote = set()
with open('data/logs/desc_rewrite_diff.md', encoding='utf-8') as f:
    for line in f:
        if line.startswith('## '):
            t = line[3:].split(' ')[0].strip()
            rewrote.add(t)

# 真に月指定を含むパターン（FY表記 2026年X月期 は除外）
MONTH_CONSTRAINT_PATTERNS = [
    r'「[\d０-９]{1,2}月」列',               # 「1月」列
    r'「[\d０-９]{1,2}月度」列',             # 「2月度」列
    r'「[\d０-９]{1,2}月[度]?」行',          # 「1月」行
    r'「[\d０-９]{4}年[\d０-９]{1,2}月[度]?」',  # 「2026年2月度」
    r'最新の?月',                              # 最新月 / 最新の月
    r'直近[の]?月',                            # 直近月 / 直近の月
    r'(当月|対象月|最終月|末月|該当月|報告月)',
    r'[\d０-９]{4}年[\d０-９]{1,2}月の数値',    # 2026年1月の数値
    r'[\d０-９]{1,2}月度の[^、。]{2,10}(数値|実績)',  # 11月度の取引台数実績
    r'[\d０-９]{1,2}月の「[\d０-９,]+」',       # 1月の「133,175」
    r'最新の?[^、。]*?（[\d０-９]{4}年[\d０-９]{1,2}月）',  # 最新の月（2025年4月）
]
MONTH_RE = re.compile('|'.join(MONTH_CONSTRAINT_PATTERNS))


def has_constraint(desc: str) -> bool:
    return bool(MONTH_RE.search(desc or ''))


remaining = []
for blob in bucket.list_blobs(prefix='monthly/meta/'):
    if not blob.name.endswith('/extract_adapter.json'):
        continue
    try:
        d = json.loads(blob.download_as_text())
    except Exception:
        continue
    if d.get('extraction_method') != 'gemini':
        continue
    t = blob.name.split('/')[2]
    if t in rewrote:
        continue
    fs = d.get('fields', [])
    bad = []
    for f in fs:
        desc = f.get('description', '') or ''
        if has_constraint(desc):
            m = MONTH_RE.search(desc)
            hits = [x for x in m.groups() if x]
            bad.append({
                'key': f.get('key') or '',
                'desc': desc,
                'hit': m.group(0) if m else '',
            })
    if bad:
        remaining.append({'ticker': t, 'fields': bad})

lines = [f'# 真に月指定あり tickers ({len(remaining)}社)', '', 'マッチしたキーワード付で表示']
for r in remaining:
    lines.append(f'\n---\n## {r["ticker"]} ({len(r["fields"])}フィールド)')
    for f in r['fields']:
        lines.append(f'\n### `{f["key"]}` | 検出: `{f["hit"]}`')
        lines.append('```')
        lines.append(f['desc'])
        lines.append('```')

Path('data/logs/desc_remaining_audit.md').write_text('\n'.join(lines), encoding='utf-8')
print(f'{len(remaining)} tickers / total fields: {sum(len(r["fields"]) for r in remaining)}')

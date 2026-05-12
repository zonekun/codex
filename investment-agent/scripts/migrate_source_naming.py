"""extract_adapter.json の source 値を厳格化リネーム.

Old → New:
  tdnet      → tdnet (不変)
  pdf        → non-tdnet(pdf)
  html_table → non-tdnet(html_table)
  download   → 旧式（無変更、レガシー）

対象:
- GCS monthly/meta/{ticker}/extract_adapter.json (全 ~493社)
- ローカル meta/monthly/*_extract_adapter.json

並行で extract_monthly_data.py と build_monthly_extractor.py のソース判定も新表記に対応
（旧表記も後方互換でacceptする）
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from google.cloud import storage
from google.oauth2 import service_account

JST = timezone(timedelta(hours=9))
now = datetime.now(JST).strftime('%Y-%m-%dT%H:%M:%S+09:00')

creds = service_account.Credentials.from_service_account_file('keys/gcp-service-account.json')
gcs = storage.Client(project='gmailpj-357912', credentials=creds)
bucket = gcs.bucket('stock_data_1930932')

SOURCE_MAP = {
    'pdf': 'non-tdnet(pdf)',
    'html_table': 'non-tdnet(html_table)',
}

renamed_log = []
unchanged = 0
adapters_dir = Path('meta/monthly')

# GCS全extract_adapter.json をrename
for blob in bucket.list_blobs(prefix='monthly/meta/'):
    if not blob.name.endswith('/extract_adapter.json'):
        continue
    try:
        d = json.loads(blob.download_as_text())
    except Exception:
        continue
    ticker = blob.name.split('/')[2]
    old_source = d.get('source', '')
    new_source = SOURCE_MAP.get(old_source, old_source)

    if new_source != old_source:
        d['source'] = new_source
        d['_source_renamed_at'] = now
        d['_source_renamed_from'] = old_source
        d['manual_override'] = True
        data_str = json.dumps(d, ensure_ascii=False, indent=2)
        blob.upload_from_string(data_str, content_type='application/json')
        # ローカルも更新
        local = adapters_dir / f'{ticker}_extract_adapter.json'
        if local.exists():
            local.write_text(data_str, encoding='utf-8')
        renamed_log.append({'ticker': ticker, 'old': old_source, 'new': new_source})
    else:
        unchanged += 1

# 集計
from collections import Counter
old_dist = Counter(r['old'] for r in renamed_log)
new_dist = Counter(r['new'] for r in renamed_log)

out = Path('data/logs/source_rename_log.md')
lines = ['# extract_adapter.json source 厳格化リネーム結果', f'実施: {now}', '']
lines.append(f'**変更**: {len(renamed_log)} 社 / **不変**: {unchanged} 社')
lines.append('')
lines.append('## old → new 件数')
for old, n in old_dist.most_common():
    lines.append(f'- {old} → {SOURCE_MAP.get(old, old)}: {n} 社')
lines.append('')
lines.append('## 変更社一覧')
for r in renamed_log:
    lines.append(f'- {r["ticker"]}: `{r["old"]}` → `{r["new"]}`')
out.write_text('\n'.join(lines), encoding='utf-8')
print(f'リネーム完了: {len(renamed_log)} 社 / 不変 {unchanged} 社')
print(f'ログ: {out}')

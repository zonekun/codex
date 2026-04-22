"""4343 イオンファンタジー / 8252 丸井グループ の DL失敗詳細調査.

GCS log + Cloud Run logs から原因特定:
- adapter.json (DL設定) 内容
- リクエストURL / 応答ステータス
- 例外スタックトレース
- IRページ取得試行（curl_cffi で再現）
"""
from __future__ import annotations
import json
import io
from pathlib import Path
from google.cloud import storage, logging_v2
from google.oauth2 import service_account

creds = service_account.Credentials.from_service_account_file('keys/gcp-service-account.json')
gcs = storage.Client(project='gmailpj-357912', credentials=creds)
bucket = gcs.bucket('stock_data_1930932')

lines = ['# 4343 / 8252 DL失敗詳細調査 (2026-04-18)', '']

for ticker in ['4343', '8252']:
    lines.append(f'\n---\n## {ticker}')

    # adapter.json
    ap = bucket.blob(f'monthly/meta/{ticker}/adapter.json')
    if ap.exists():
        d = json.loads(ap.download_as_text())
        lines.append('### adapter.json')
        lines.append('```json')
        lines.append(json.dumps(d, ensure_ascii=False, indent=2))
        lines.append('```')

    # Cloud Run log: 当該 ticker のエラー周辺
    print(f'Fetching Cloud Run log for {ticker}...')
    import subprocess
    result = subprocess.run(
        ['gcloud', 'logging', 'read',
         f'resource.type=cloud_run_job AND labels."run.googleapis.com/execution_name"=download-monthly-9f4xb AND textPayload=~"{ticker}"',
         '--project', 'gmailpj-357912', '--limit', '50',
         '--format', 'value(timestamp,textPayload)'],
        capture_output=True, text=True, encoding='utf-8'
    )
    lines.append(f'\n### Cloud Run logs ({ticker})')
    lines.append('```')
    lines.append(result.stdout[:5000] if result.stdout else '(空)')
    if result.stderr:
        lines.append(f'STDERR: {result.stderr[:500]}')
    lines.append('```')

    # IRページに直接アクセス (curl_cffi)
    if ap.exists():
        ir_url = d.get('url') or d.get('ir_page_url')
        if ir_url:
            lines.append(f'\n### IRページ再現テスト: {ir_url}')
            try:
                from curl_cffi import requests as creq
                r = creq.get(ir_url, impersonate='chrome124', timeout=30, verify=False)
                lines.append(f'- HTTP {r.status_code}, body {len(r.text)} chars')
                if r.status_code == 200:
                    # link 数えてみる
                    import re
                    pdf_links = re.findall(r'href="([^"]+\.pdf[^"]*)"', r.text, re.IGNORECASE)
                    lines.append(f'- PDF links: {len(pdf_links)}')
                    for link in pdf_links[:10]:
                        lines.append(f'  - {link[:120]}')
                else:
                    lines.append(f'- body preview: {r.text[:300]}')
            except Exception as e:
                lines.append(f'- ERROR: {e}')

Path('data/logs/dl_failures_4343_8252.md').write_text('\n'.join(lines), encoding='utf-8')
print(f'\n書き出し: data/logs/dl_failures_4343_8252.md')

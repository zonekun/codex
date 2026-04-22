"""失敗3社+取得なし43社=46社 の DL ログ詳細調査.

Cloud Run logs から各 ticker の処理ログ（前後）を抽出。
原因カテゴリ:
- HTTP error (404, 403, timeout)
- リンク未検出（regex match なし、表示はされるが pattern 不一致）
- iframe / JS 動的ロード
- adapter.json の URL 古い

出力: data/logs/dl_46_investigation.md
"""
from __future__ import annotations
import json
import os
from collections import defaultdict
from pathlib import Path
from google.cloud import storage
from google.cloud.logging_v2 import Client as LoggingClient
from google.oauth2 import service_account

creds = service_account.Credentials.from_service_account_file('keys/gcp-service-account.json')
gcs = storage.Client(project='gmailpj-357912', credentials=creds)
bucket = gcs.bucket('stock_data_1930932')
log_client = LoggingClient(project='gmailpj-357912', credentials=creds)

FAILED = ['4343', '6146', '8252']
ZERO_DL = ['1878','1928','2659','2726','2910','2998','3028','3048','3080','3196','3397','3548','3678','4680','4839','6580','7378','7512','7550','7561','7611','7678','8160','8179','8214','8237','8255','8278','8282','8515','8725','9009','9202','9206','9327','9412','9517','9605','9616','9830','9843','9876','9994']

# 当該 execution の全ログを1回だけ取得
print('Cloud Run logs 一括取得中（時間かかります）...')
filter_str = (
    'resource.type=cloud_run_job AND '
    'labels."run.googleapis.com/execution_name"="download-monthly-9f4xb"'
)
logs_by_ticker = defaultdict(list)
for entry in log_client.list_entries(filter_=filter_str, page_size=1000, max_results=20000):
    payload = entry.payload if isinstance(entry.payload, str) else str(entry.payload)
    # ticker 抽出
    for t in FAILED + ZERO_DL:
        if t in payload:
            logs_by_ticker[t].append((entry.timestamp.isoformat(), payload[:300]))
            break

print(f'logs取得完了: {sum(len(v) for v in logs_by_ticker.values())} entries')

# adapter.json も取得
adapter_data = {}
for t in FAILED + ZERO_DL:
    b = bucket.blob(f'monthly/meta/{t}/adapter.json')
    if b.exists():
        try:
            adapter_data[t] = json.loads(b.download_as_text())
        except Exception:
            pass

# md 出力
lines = ['# DL 46社 失敗詳細調査 (download-monthly-9f4xb)', '']
lines.append(f'失敗 {len(FAILED)}社 + 取得なし {len(ZERO_DL)}社 = {len(FAILED)+len(ZERO_DL)}社')
lines.append('')

for ticker in FAILED + ZERO_DL:
    section = '失敗' if ticker in FAILED else '取得なし'
    lines.append(f'\n---\n## {ticker} [{section}]')

    if ticker in adapter_data:
        d = adapter_data[ticker]
        lines.append(f'- **type**: `{d.get("type")}` / **status**: `{d.get("status")}`')
        lines.append(f'- **url**: `{d.get("url") or d.get("ir_page_url")}`')
        lines.append(f'- **link_text_pattern**: `{d.get("link_text_pattern")}`')
        lines.append(f'- **link_href_pattern**: `{d.get("link_href_pattern")}`')
        lines.append(f'- **note**: `{d.get("note", "")[:200]}`')

    entries = sorted(logs_by_ticker.get(ticker, []))
    lines.append(f'- **logs ({len(entries)}件)**:')
    lines.append('```')
    for ts, msg in entries[-15:]:  # 最後15件
        lines.append(f'{ts[:19]} | {msg.strip()[:250]}')
    lines.append('```')

Path('data/logs/dl_46_investigation.md').write_text('\n'.join(lines), encoding='utf-8')
print(f'書き出し: data/logs/dl_46_investigation.md ({len(lines)} 行)')

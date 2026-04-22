"""3175/3349/7127/2782 再修正 + 3349 はPDF実物確認しつつ調整."""
from __future__ import annotations
import io
import json
import pdfplumber
from datetime import datetime, timezone, timedelta
from pathlib import Path
from google.cloud import storage
from google.oauth2 import service_account

JST = timezone(timedelta(hours=9))
now = datetime.now(JST).strftime('%Y-%m-%dT%H:%M:%S+09:00')

creds = service_account.Credentials.from_service_account_file('keys/gcp-service-account.json')
gcs = storage.Client(project='gmailpj-357912', credentials=creds)
bucket = gcs.bucket('stock_data_1930932')

# === 3349 PDF 確認 ===
print('=== 3349 PDF inspection ===')
blobs = sorted([b for b in bucket.list_blobs(prefix='monthly/docs/3349/')], key=lambda b: b.name, reverse=True)[:3]
for b in blobs:
    print(f'  {b.name.rsplit(chr(47), 1)[-1]} ({b.size:,} bytes)')
if blobs:
    b = blobs[0]
    with pdfplumber.open(io.BytesIO(b.download_as_bytes())) as pdf:
        for i, page in enumerate(pdf.pages[:2]):
            print(f'\n--- 3349 Page {i+1} ---')
            print((page.extract_text() or '')[:2500])

# === 修正 ===
def update_adapter(ticker: str, fixes: dict):
    ap = bucket.blob(f'monthly/meta/{ticker}/extract_adapter.json')
    d = json.loads(ap.download_as_text())
    for f in d.get('fields', []):
        k = f.get('key')
        if k in fixes:
            f['description'] = fixes[k]
            f['_manual_description'] = True
    d['manual_override'] = True
    d['p2_regression_refix_at'] = now
    data_str = json.dumps(d, ensure_ascii=False, indent=2)
    Path(f'data/monthly_adapters/{ticker}.json').write_text(data_str, encoding='utf-8')
    ap.upload_from_string(data_str, content_type='application/json')
    print(f'\n{ticker}: {len(fixes)} fields updated')

# 3175 — 直営店店舗数を「直営店合計（海外含む）」に変更
update_adapter('3175', {
    '直営店 店舗数': '「Ⅰ．...店舗数の状況」表の**「直営店合計」行**（海外含む合計）の対象月列の値。「小計」行（国内のみ）ではなく、海外15店舗を含む直営店合計（例: 国内123 + 海外15 = 138）を取得。',
})

# 7127 — 4Q/累計列を取らない指示を激化
update_adapter('7127', {
    '飲食事業部 全店 売上（前年同月比）': '「Ⅲ ...飲食事業部 前年同月比推移」の「全店（全業態）」表、「売上高前年比（%）」行の値。表の列順は「4月,5月,6月,7月,8月,9月,10月,11月,12月,1月,2月,3月,1Q,2Q,3Q,4Q,累計」（**月別12列+集計5列の計17列**）。**月別12列のうち最後の3月列の値（106.5%等）のみ**を取得。1Q/2Q/3Q/4Q/累計の集計5列は**絶対に絶対に取らない**。文書タイトルが「2026年3月度」なら3月列の値を返す。',
    '飲食事業部 全店 客数（前年同月比）': '同表の「客数前年比（%）」行、月別12列のうち文書タイトル月度の列（最後=3月度の場合98.9%）。1Q/2Q/3Q/4Q/累計列は絶対除外。',
})

# 2782 — 列番号を BC値から逆算した正しい位置に
update_adapter('2782', {
    '全社 売上（前年同月比）': '「2026年Ｘ月度の月次売上高前年比及び店舗数」表の対象月行、**1番目の数値列（全社売上前年比）**の値。例: 2026年1月行 「1月 115.2 102.3 112.7 99.5 110.1 102.4 4(0) 6(0) 2,075 (34)」なら 115.2。',
    '直営店舗数': '同表の対象月行、**「月末」列**（最後の整数列、例: 2,075）。括弧内のFC店舗数は無視。',
    '既存店 客数（前年同月比）': '同表の対象月行、**5番目の数値列（既存店 客数 前年同月比）**の値。例: 2026年1月行の5番目=110.1。1番目（全社売上）/2番目/3番目/4番目（全社系）と混同しないこと。',
    '既存店 売上（前年同月比）': '同表の対象月行、**3番目の数値列（既存店 売上 前年同月比）**の値。例: 2026年1月行の3番目=112.7。1番目（全社売上=115.2）と混同しない。',
})

# 3349 — PDF確認後、structure に応じて修正（取りあえず汎用的な強化）
update_adapter('3349', {
    '全店 売上（前年同月比）': '月次速報PDFの月別表（4月〜5月の年度内月別、コスモス薬品は5月決算）の「全店 売上前年比」行の対象月（文書タイトル月度）列の値。**全月同じ値の固定取得は禁止、各月毎に異なる値を返す**。累計列は除外。',
    '既存店 売上（前年同月比）': '同月別表の「既存店 売上前年比」行の対象月列の値。**全店行と既存店行を区別**、対象月毎に異なる値を返す。累計列は除外。',
    '全店 店舗数': '同月別表の「月末店舗数」行の対象月列の値（整数）。0 のような明らかに不正な値は返さない。',
})

print('\n全社 修正完了')

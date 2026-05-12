"""24社の正確な状態リストファイル作成.

各社について:
- source / extraction_method (regex 判定: row_label_regex 有無)
- fields数
- monthly_records.json の record_count, updated_at, ym range
- BC突合結果 (latest run より): OK/NG/BC未取得/不一致パターン
- adapter内 yoy_offset / unit_scale / overwrite_past_months
"""
from __future__ import annotations
import csv
import json
from pathlib import Path
from google.cloud import storage
from google.oauth2 import service_account

creds = service_account.Credentials.from_service_account_file('keys/gcp-service-account.json')
gcs = storage.Client(project='gmailpj-357912', credentials=creds)
bucket = gcs.bucket('stock_data_1930932')

TICKERS = ['8173','7601','5891','3083','8218','7603','9262','7513','8040','8167','2726','9854','2730','1840','9831','9835','3543','3395','9031','7682','3544','3931','8244','9044']

# 直近 BC突合結果 ロード
COMPARE_CSV = Path('C:/tmp/buffett_compare_20260418_193348.csv')
compare_data = {}  # {ticker: {'ok': N, 'ng': N, 'no_bc': N, 'fields_detail': [...]}}
if COMPARE_CSV.exists():
    with open(COMPARE_CSV, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            t = row.get('ticker', '')
            if t not in TICKERS:
                continue
            cd = compare_data.setdefault(t, {'ok': 0, 'ng': 0, 'no_bc': 0, 'samples': []})
            match = row.get('match', '')
            bc_val = row.get('bc_value', '')
            if match == 'OK':
                cd['ok'] += 1
            elif match == 'NG':
                cd['ng'] += 1
                if len(cd['samples']) < 3:
                    cd['samples'].append({
                        'ym': row.get('year_month', ''),
                        'field': row.get('our_field', ''),
                        'our': row.get('our_value', ''),
                        'bc': bc_val,
                        'yoy': row.get('yoy_offset', ''),
                    })
            elif not bc_val or bc_val.strip() == '':
                cd['no_bc'] += 1

rows = []
for t in TICKERS:
    ap_path = Path(f'meta/monthly/{t}_extract_adapter.json')
    if not ap_path.exists():
        rows.append({
            'ticker': t, 'source': 'NO_ADAPTER', 'method': '-', 'fields': 0,
            'bc_ignore': 0, 'yoy_offset': 0, 'unit_scale': 1, 'owpm': False,
            'rec_count': 0, 'rec_ym_min': '', 'rec_ym_max': '', 'rec_updated': '',
            'cmp_ok': 0, 'cmp_ng': 0, 'cmp_no_bc': 0, 'cmp_rate': '0.0%',
            'ng_sample1': '', 'ng_sample2': '',
        })
        continue
    ap = json.loads(ap_path.read_text(encoding='utf-8'))

    # extraction method 判定
    em = ap.get('extraction_method', '')
    if not em:
        # row_label_regex 有無で regex 判定
        has_regex = any(f.get('row_label_regex') for f in ap.get('fields', []))
        em = 'regex' if has_regex else 'unknown'

    # records 状態
    rec_blob = bucket.blob(f'monthly/record/{t}/monthly_records.json')
    rec_count = 0
    rec_updated = ''
    rec_ym_min = ''
    rec_ym_max = ''
    if rec_blob.exists():
        d = json.loads(rec_blob.download_as_text())
        recs = d.get('records', [])
        rec_count = len(recs)
        rec_updated = (d.get('updated_at') or '')[:19]
        yms = sorted([r.get('year_month', '') for r in recs if r.get('year_month')])
        if yms:
            rec_ym_min = yms[0]
            rec_ym_max = yms[-1]

    # adapter 統計
    field_count = len(ap.get('fields', []))
    yoy_offset = ap.get('yoy_offset', 0)
    unit_scale = ap.get('unit_scale', 1)
    owpm = ap.get('overwrite_past_months', False)
    bc_ignore_count = sum(1 for f in ap.get('fields', []) if f.get('bc_ignore'))

    # BC突合結果
    cmp = compare_data.get(t, {'ok': 0, 'ng': 0, 'no_bc': 0, 'samples': []})
    total_cmp = cmp['ok'] + cmp['ng'] + cmp['no_bc']
    rate = (cmp['ok'] / total_cmp * 100) if total_cmp else 0

    rows.append({
        'ticker': t,
        'source': ap.get('source', ''),
        'method': em,
        'fields': field_count,
        'bc_ignore': bc_ignore_count,
        'yoy_offset': yoy_offset,
        'unit_scale': unit_scale,
        'owpm': owpm,
        'rec_count': rec_count,
        'rec_ym_min': rec_ym_min,
        'rec_ym_max': rec_ym_max,
        'rec_updated': rec_updated,
        'cmp_ok': cmp['ok'],
        'cmp_ng': cmp['ng'],
        'cmp_no_bc': cmp['no_bc'],
        'cmp_rate': f'{rate:.1f}%',
        'ng_sample1': str(cmp['samples'][0]) if cmp['samples'] else '',
        'ng_sample2': str(cmp['samples'][1]) if len(cmp['samples']) > 1 else '',
    })

out = Path('data/logs/24_list.csv')
out.parent.mkdir(parents=True, exist_ok=True)
with open(out, 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

# MD 形式も出力
md_lines = ['# 24社 NG リスト (BC突合 14.8%)', '', f'実施: 2026-04-18', '']
md_lines.append('| ticker | source | method | fields | bc_ignore | yoy | unit | owpm | rec | ym range | OK | NG | NoBC | rate |')
md_lines.append('|--------|--------|--------|--------|-----------|-----|------|------|-----|----------|----|----|------|------|')
for r in rows:
    md_lines.append(
        f"| {r['ticker']} | {r['source']} | **{r['method']}** | {r['fields']} | {r['bc_ignore']} | "
        f"{r['yoy_offset']} | {r['unit_scale']} | {r['owpm']} | {r['rec_count']} | "
        f"{r['rec_ym_min']}〜{r['rec_ym_max']} | {r['cmp_ok']} | {r['cmp_ng']} | {r['cmp_no_bc']} | {r['cmp_rate']} |"
    )

# method 別集計
methods = {}
for r in rows:
    m = r['method']
    methods[m] = methods.get(m, 0) + 1
md_lines.append('')
md_lines.append('## method 別集計')
for m, n in methods.items():
    md_lines.append(f'- **{m}**: {n}社')

# NG sample 詳細
md_lines.append('')
md_lines.append('## NG sample (各社 上位2件)')
for r in rows:
    if r.get('ng_sample1'):
        md_lines.append(f'\n### {r["ticker"]} (NG={r["cmp_ng"]})')
        md_lines.append(f'- {r["ng_sample1"]}')
        if r.get('ng_sample2'):
            md_lines.append(f'- {r["ng_sample2"]}')

Path('data/logs/24_list.md').write_text('\n'.join(md_lines), encoding='utf-8')
print(f'CSV: {out}')
print(f'MD: data/logs/24_list.md')

# サマリ表示
print('\n=== method 別集計 ===')
for m, n in sorted(methods.items()):
    print(f'  {m}: {n}社')
print('\n=== 各社 method/一致率 ===')
for r in rows:
    print(f'  {r["ticker"]} [{r["method"]:8s}] rec={r["rec_count"]} OK={r["cmp_ok"]} NG={r["cmp_ng"]} NoBC={r["cmp_no_bc"]} rate={r["cmp_rate"]}')

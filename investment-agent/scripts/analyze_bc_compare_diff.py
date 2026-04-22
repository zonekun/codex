"""前回 (2026-04-17) と 今回 (2026-04-18) の BC compare CSV を ticker レベルで比較.

各 ticker について:
- before: OK件数 / NG件数 / BC_NODATA件数 / 総件数
- after: 同上
- 一致率変化: 改善/劣化/横ばい/対象なし

出力: data/logs/bc_compare_diff_analysis.md
"""
from __future__ import annotations
import csv
from collections import defaultdict
from pathlib import Path

BEFORE = r'C:\tmp\buffett_compare_20260417_221647.csv'
AFTER = r'C:\tmp\buffett_compare_20260418_173658.csv'

def read_csv(path):
    """ticker -> {OK: n, NG: n, BC_NODATA: n}"""
    data = defaultdict(lambda: {'OK': 0, 'NG': 0, 'BC_NODATA': 0})
    with open(path, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            t = row.get('ticker', '')
            m = row.get('match', '')
            data[t][m] = data[t].get(m, 0) + 1
    return data

before = read_csv(BEFORE)
after = read_csv(AFTER)

# touched_tickers
touched = set()
with open('data/logs/touched_tickers.txt', encoding='utf-8') as f:
    touched = set(f.read().strip().split(','))

# 個別修正済 (high signal):
high_signal = {
    'description_rewrite_43': set(),  # 後述
    'p1_5': {'138A', '3174', '7685', '3391', '9007'},
    'p2_6': {'6627', '6301', '8739', '4666', '3358', '3663'},
    'p3_5': {'3046', '3038', '8715', '2337', '9413'},
    'p5_4': {'6040', '7685', '7610', '7059'},
    'field_rename_5': {'2154', '3066', '3547', '6561', '7823'},
}

# desc_rewrite_diff.md から description rewrite 35社特定
import re
with open('data/logs/desc_rewrite_diff.md', encoding='utf-8') as f:
    for line in f:
        if line.startswith('## '):
            t = line[3:].split(' ')[0].strip()
            high_signal['description_rewrite_43'].add(t)
high_signal['description_rewrite_43'].update(['4015', '5580', '5589'])  # 手動3
high_signal['description_rewrite_43'].update(['3046', '3038', '8715', '2337', '9413'])  # P3 5 (overlap with p3)

# 全 ticker (before ∪ after)
all_tickers = set(before.keys()) | set(after.keys())

def rate(d):
    total = d['OK'] + d['NG'] + d['BC_NODATA']
    return (d['OK'] / total * 100) if total > 0 else None

categorized = {'改善': [], '劣化': [], '横ばい': [], '新規': [], '消失': [], 'なし→0件': []}
for t in sorted(all_tickers):
    b = before.get(t, {'OK': 0, 'NG': 0, 'BC_NODATA': 0})
    a = after.get(t, {'OK': 0, 'NG': 0, 'BC_NODATA': 0})
    b_total = b['OK'] + b['NG'] + b['BC_NODATA']
    a_total = a['OK'] + a['NG'] + a['BC_NODATA']
    b_rate = rate(b)
    a_rate = rate(a)

    if b_total == 0 and a_total > 0:
        cat = '新規'
    elif b_total > 0 and a_total == 0:
        cat = '消失'
    elif b_total == 0 and a_total == 0:
        continue
    else:
        diff = a_rate - b_rate
        if diff > 5:
            cat = '改善'
        elif diff < -5:
            cat = '劣化'
        else:
            cat = '横ばい'
    categorized[cat].append({
        'ticker': t,
        'before': b,
        'after': a,
        'b_rate': b_rate,
        'a_rate': a_rate,
        'in_touched': t in touched,
        'tags': [k for k, v in high_signal.items() if t in v],
    })

# md 出力
lines = ['# BC compare 前回 vs 今回 ticker別比較', '']
lines.append(f'**前回**: {BEFORE}')
lines.append(f'**今回**: {AFTER}')
lines.append('')
for cat in ['改善', '劣化', '横ばい', '新規', '消失']:
    items = categorized[cat]
    lines.append(f'\n## {cat} ({len(items)}社)')
    lines.append('| ticker | 前回 OK/総 | 今回 OK/総 | 前→後 一致率 | tags |')
    lines.append('|----|----|----|----|----|')
    for x in items:
        b, a = x['before'], x['after']
        b_str = f'{b["OK"]}/{b["OK"]+b["NG"]+b["BC_NODATA"]}'
        a_str = f'{a["OK"]}/{a["OK"]+a["NG"]+a["BC_NODATA"]}'
        b_r = f'{x["b_rate"]:.1f}%' if x['b_rate'] is not None else '-'
        a_r = f'{x["a_rate"]:.1f}%' if x['a_rate'] is not None else '-'
        tags = ','.join(x['tags']) if x['tags'] else ('touched' if x['in_touched'] else '')
        lines.append(f'| {x["ticker"]} | {b_str} | {a_str} | {b_r} → {a_r} | {tags} |')

# 個別修正効果サマリ
lines.append('\n---\n## 個別修正カテゴリ別 効果')
for cat_key, members in high_signal.items():
    if not members:
        continue
    lines.append(f'\n### {cat_key} ({len(members)}社)')
    lines.append('| ticker | 前回 一致率 | 今回 一致率 | 判定 |')
    lines.append('|----|----|----|----|')
    for t in sorted(members):
        b = before.get(t, {})
        a = after.get(t, {})
        b_r = rate(b) if b else None
        a_r = rate(a) if a else None
        b_s = f'{b_r:.1f}% ({b["OK"]}/{b["OK"]+b["NG"]+b["BC_NODATA"]})' if b_r is not None else '対象なし'
        a_s = f'{a_r:.1f}% ({a["OK"]}/{a["OK"]+a["NG"]+a["BC_NODATA"]})' if a_r is not None else '対象なし'
        if b_r is None and a_r is None:
            j = '-'
        elif b_r is None:
            j = '新規'
        elif a_r is None:
            j = '消失'
        else:
            d = a_r - b_r
            j = '✅改善' if d > 5 else '❌劣化' if d < -5 else '➖横ばい'
        lines.append(f'| {t} | {b_s} | {a_s} | {j} |')

# 全体集計
b_ok = sum(b['OK'] for b in before.values())
b_total = sum(b['OK']+b['NG']+b['BC_NODATA'] for b in before.values())
a_ok = sum(a['OK'] for a in after.values())
a_total = sum(a['OK']+a['NG']+a['BC_NODATA'] for a in after.values())
lines.insert(4, f'**前回全体**: OK={b_ok} / 総={b_total} ({b_ok/b_total*100:.1f}%)  /  **今回全体**: OK={a_ok} / 総={a_total} ({a_ok/a_total*100:.1f}%)')

Path('data/logs/bc_compare_diff_analysis.md').write_text('\n'.join(lines), encoding='utf-8')
print(f'書き出し: data/logs/bc_compare_diff_analysis.md')
print(f'改善 {len(categorized["改善"])} / 劣化 {len(categorized["劣化"])} / 横ばい {len(categorized["横ばい"])} / 新規 {len(categorized["新規"])} / 消失 {len(categorized["消失"])}')

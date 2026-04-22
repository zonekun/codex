"""58社の調査用データを1つの大きな md に出力（分析は手書き）."""
import json
from pathlib import Path

data = json.loads(open('data/logs/non_yearmonth_data.json', encoding='utf-8').read())
# BC NG件数降順 > past調査あり > ticker
data.sort(key=lambda x: (-x['bc_ng_count'], -len(x['past_investigation_files']), x['ticker']))

lines = ['# 58社 非月関連 深堀り調査データ（自動収集）', '',
         '**作成**: 2026-04-18 00:10 JST', '',
         f'**総計**: {len(data)}社 / BC NG有り: {sum(1 for r in data if r["bc_ng_count"] > 0)}社 / 過去調査済: {sum(1 for r in data if r["past_investigation_files"])}社', '']

for r in data:
    name = r['stock_name'] or r['ticker']
    ad = r['adapter']
    lines.append(f'---')
    lines.append(f'## {r["ticker"]} {name}')
    lines.append(f'- **BC NG**: {r["bc_ng_count"]}件 / **records**: {r["records_count"]}件 / **latest_ym**: `{r["records_latest_ym"]}` / **has_2026**: {r["records_has_2026"]}')
    lines.append(f'- **sample_doc_title**: `{ad["sample_doc_title"]}`')
    lines.append(f'- **sample_submission_date**: `{ad["sample_submission_date"]}` / **created_at**: `{ad["created_at"]}`')
    lines.append(f'- **manual_override**: `{ad["manual_override"]}` / **source**: `{ad["source"]}` / **format**: `{ad["format"]}`')
    lines.append(f'- **past_investigations**: `{", ".join(r["past_investigation_files"]) or "-"}`')
    lines.append('')
    lines.append('### Fields')
    for f in ad['fields']:
        lines.append(f'\n**`{f["key"]}`** (value_type={f["value_type"]}, unit_scale={f["unit_scale"]}, group={f["group"]})')
        lines.append(f'- row_label_regex: `{f["row_label_regex"]}`')
        desc = (f["description"] or "").replace("\n", " ")[:500]
        lines.append(f'- description: `{desc}`')

    if r['bc_ng_count'] > 0:
        lines.append('\n### BC NG entries (max 20)')
        lines.append('| ym | our_field | our_value | bc_field | bc_value | diff | match |')
        lines.append('|----|----|----|----|----|----|----|')
        for e in r['bc_ng_entries']:
            lines.append(f'| {e.get("year_month","")} | {e.get("our_field","")[:30]} | {e.get("our_value","")} | {e.get("bc_field","")[:30]} | {e.get("bc_value","")} | {e.get("diff","")} | {e.get("match","")} |')
    lines.append('')

Path('data/logs/non_yearmonth_investigation_all.md').write_text('\n'.join(lines), encoding='utf-8')
print(f'書き出し: data/logs/non_yearmonth_investigation_all.md ({sum(len(l) for l in lines):,} chars)')

"""
不正 year_month レコードを 1社ずつ個別に深堀り調査。
パターン分類はせず、各社の個性を見る。

出力: data/logs/bad_yearmonth_deep.md
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from google.cloud import storage
from google.oauth2 import service_account

creds = service_account.Credentials.from_service_account_file('keys/gcp-service-account.json')
gcs = storage.Client(project='gmailpj-357912', credentials=creds)
bucket = gcs.bucket('stock_data_1930932')


def simulate_parse_year_month(adapter: dict, doc_title: str, submission_date: str):
    """extract_monthly_data.py の _parse_year_month を模倣（trace付き）."""
    trace = []
    year_re = adapter.get("year_from_title_regex", r"(?P<year>\d{4})年")

    # 月度優先
    month_m = re.search(r"(?P<month>\d{1,2})月度", doc_title)
    if month_m:
        trace.append(f"  月度 regex 優先ヒット → month={month_m.group('month')}")
    else:
        month_re_raw = adapter.get("month_from_title_regex", r"(?P<month>\d{1,2})月[度期]")
        month_re = re.sub(r'\(\?<([a-zA-Z_][a-zA-Z0-9_]*)>', r'(?P<\1>', month_re_raw)
        try:
            month_m = re.search(month_re, doc_title)
        except re.error:
            month_m = re.search(r"(?P<month>\d{1,2})月[度期]", doc_title)
        if month_m:
            v = month_m.groupdict().get('month') or month_m.group(1)
            trace.append(f"  adapter.month_re '{month_re_raw}' → '{month_m.group(0)}' → month={v}")
        else:
            trace.append(f"  adapter.month_re '{month_re_raw}' no match")

    year_re_norm = re.sub(r'\(\?<([a-zA-Z_][a-zA-Z0-9_]*)>', r'(?P<\1>', year_re)
    try:
        year_m = re.search(year_re_norm, doc_title)
    except re.error:
        year_m = re.search(r"(?P<year>\d{4})年", doc_title)

    if year_m:
        v = year_m.groupdict().get('year') or year_m.group(1)
        trace.append(f"  adapter.year_re '{year_re}' → '{year_m.group(0)}' → year={v}")
    else:
        trace.append(f"  adapter.year_re '{year_re}' no match")

    result = None
    if year_m and month_m:
        try:
            yd = year_m.groupdict()
            md = month_m.groupdict()
            year = int(yd.get("year") or year_m.group(1))
            month = int(md.get("month") or month_m.group(1))
            _is_default_re = not adapter.get("year_from_title_regex")
            fy_match = re.search(r"\d{4}年\d{1,2}月期", doc_title)
            if _is_default_re and fy_match:
                trace.append(f"  FY期タイトル + default regex → fall-through to submission date heuristic")
            else:
                result = (year, month)
        except Exception:
            pass

    return result, trace


def load_bad_records():
    bad = defaultdict(list)
    for blob in bucket.list_blobs(prefix='monthly/record/'):
        if not blob.name.endswith('/monthly_records.json'):
            continue
        t = blob.name.split('/')[2]
        if t == '3070':
            continue
        try:
            d = json.loads(blob.download_as_text())
        except Exception:
            continue
        for r in d.get('records', []):
            ym = str(r.get('year_month', ''))
            m = re.match(r'^(\d{4})-(\d{2})$', ym)
            is_bad = False
            if not m:
                is_bad = True
            else:
                y, mo = int(m.group(1)), int(m.group(2))
                if y > 2026 or (y == 2026 and mo > 4) or mo < 1 or mo > 12 or y < 2015:
                    is_bad = True
            if is_bad:
                bad[t].append(r)
    return bad


def main():
    out = Path('data/logs/bad_yearmonth_deep.md')
    out.parent.mkdir(parents=True, exist_ok=True)

    bad = load_bad_records()
    print(f"対象ticker: {len(bad)}")

    lines = ["# year_month不正 1社ずつ個別調査（3070除く）", ""]

    for t in sorted(bad.keys()):
        recs = bad[t]
        # adapter 取得
        try:
            adapter = json.loads(
                bucket.blob(f'monthly/meta/{t}/extract_adapter.json').download_as_text()
            )
        except Exception as e:
            lines.append(f"\n---\n## {t}\n- adapter取得失敗: {e}\n")
            continue

        lines.append(f"\n---\n## {t} (不正{len(recs)}件)")
        lines.append(f"**adapter設定**:")
        lines.append(f"- source / extraction_method / format: `{adapter.get('source')}` / `{adapter.get('extraction_method')}` / `{adapter.get('format')}`")
        lines.append(f"- doc_title_pattern: `{adapter.get('doc_title_pattern')}`")
        lines.append(f"- year_from_title_regex: `{adapter.get('year_from_title_regex')}`")
        lines.append(f"- month_from_title_regex: `{adapter.get('month_from_title_regex')}`")
        lines.append(f"- overwrite_past_months: `{adapter.get('overwrite_past_months')}`")
        lines.append(f"- manual_override: `{adapter.get('manual_override')}`")
        lines.append("")
        lines.append(f"### 不正レコード全件 ({len(recs)}件)")
        for i, r in enumerate(recs, 1):
            title = r.get('doc_title', '') or '(空)'
            sub = r.get('submission_date', '') or '(空)'
            ym = r.get('year_month', '')
            y = r.get('year', '')
            mo = r.get('month', '')
            src = r.get('source', '')
            lines.append(f"\n#### 不正レコード {i}")
            lines.append(f"- **記録ym**: `{ym}` (y={y}, m={mo}) / **record source**: `{src}`")
            lines.append(f"- **doc_title**: `{title}`")
            lines.append(f"- **submission_date**: `{sub}`")

            result, trace = simulate_parse_year_month(adapter, title, sub)
            lines.append(f"- **_parse_year_month シミュレーション**:")
            for line in trace:
                lines.append(f"  {line}")
            lines.append(f"- **シミュレーション結果**: `{result}`")

        lines.append("")

    out.write_text('\n'.join(lines), encoding='utf-8')
    print(f"書き出し: {out}")


if __name__ == '__main__':
    main()

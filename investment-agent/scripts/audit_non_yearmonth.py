"""
Gemini adapter のうち description 月指定以外の潜在問題を調査。

見る観点:
- サンプル固有の company名が description に埋まっていないか
- サンプル PDF の section 名が hardcoded（「2026年X月期」等）されて汎用性を失わないか
- 店舗名・商品名など具体ハードコードで、リブランドや新店舗に対応できない
- units / scale が不明確
- 行・列ラベル regex が具体的すぎる
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from google.cloud import storage
from google.oauth2 import service_account

creds = service_account.Credentials.from_service_account_file('keys/gcp-service-account.json')
gcs = storage.Client(project='gmailpj-357912', credentials=creds)
bucket = gcs.bucket('stock_data_1930932')


def check_issues(desc: str) -> list[str]:
    """description から潜在問題を検出してフラグ一覧返す."""
    issues = []
    if not desc:
        return ['desc空']

    # 1. 具体的な会計期年 (YYYY年X月期) がハードコード
    fy_matches = re.findall(r'(20\d{2})年\d{1,2}月期', desc)
    if fy_matches:
        years = sorted(set(fy_matches))
        # 2026年以前の固定 fy 年 → 来年になると古い表記になる
        if any(int(y) <= 2026 for y in years):
            issues.append(f'固定FY年（{",".join(years)}）: 将来無効化リスク')

    # 2. 具体的な店舗数・商品数・数値がサンプル残り
    sample_numbers = re.findall(r'「([\d,]+)」', desc)
    sample_numbers = [n for n in sample_numbers if len(n.replace(',', '')) >= 3]
    if sample_numbers:
        issues.append(f'サンプル数値残り: {sample_numbers[:3]}')

    # 3. 具体的な company 名（個別性高いもの）
    if re.search(r'株式会社|\(株\)|㈱', desc):
        # 会社名っぽい文字列を抽出
        co_m = re.findall(r'[\w・]+[（(]?株[）)]?', desc)
        if co_m:
            issues.append(f'会社名埋め込み: {co_m[:2]}')

    # 4. 「サンプル」「例」「具体的」「下記」等の曖昧な指示語
    if re.search(r'(サンプル|例えば|具体的には|下記|上記)', desc):
        issues.append('曖昧指示語残り')

    # 5. unit の明示がない（売上系で「円」「百万円」「千円」の記載なし）
    has_unit_keyword = re.search(r'(売上|販売|金額|売り)', desc)
    has_unit = re.search(r'(円|千円|百万円|億円|千|百万|億|%|パーセント|％|店|店舗|人|台|件)', desc)
    if has_unit_keyword and not has_unit:
        issues.append('単位未指定')

    # 6. Page 番号固定（PDFレイアウト変更で破綻）
    if re.search(r'Page\s*\d|ページ\s*\d', desc):
        issues.append('Page番号固定')

    # 7. テキスト長すぎ（100文字超）
    if len(desc) > 200:
        issues.append(f'長文({len(desc)}文字)')

    return issues


def main():
    # 既fix済み38社
    fixed = set()
    diff_file = Path('data/logs/desc_rewrite_diff.md')
    if diff_file.exists():
        for line in diff_file.read_text(encoding='utf-8').splitlines():
            if line.startswith('## '):
                t = line[3:].split(' ')[0].strip()
                fixed.add(t)
    # 手動fix 3社追加
    fixed.update(['4015', '5580', '5589'])

    all_issues = []
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
        ticker_issues = []
        for f in d.get('fields', []):
            key = f.get('key', '')
            desc = f.get('description', '') or ''
            if not desc:
                continue
            field_flags = check_issues(desc)
            if field_flags:
                ticker_issues.append({
                    'key': key,
                    'desc': desc,
                    'flags': field_flags,
                })
        if ticker_issues:
            all_issues.append({
                'ticker': t,
                'fixed_in_desc_rewrite': t in fixed,
                'sample_doc_title': d.get('sample_doc_title', ''),
                'issues': ticker_issues,
            })

    # レポート出力
    lines = [f'# Gemini adapter 非月関連の潜在問題 audit', '']
    lines.append(f'対象 tickers: {len(all_issues)}')
    total_flags = sum(len(f['flags']) for r in all_issues for f in r['issues'])
    lines.append(f'合計 flags: {total_flags}')
    lines.append('')

    # 主要 issue カテゴリ集計
    from collections import Counter
    flag_counter = Counter()
    for r in all_issues:
        for f in r['issues']:
            for flag in f['flags']:
                # flag 先頭の cat を抽出
                cat = re.split(r'[（:：]', flag, 1)[0]
                flag_counter[cat] += 1
    lines.append('## 問題カテゴリ分布')
    for cat, n in flag_counter.most_common():
        lines.append(f'- {cat}: {n}件')
    lines.append('')

    # fixed済 と 未fix の分割
    lines.append('## 既fix（今回 description rewrite 対象）: 非月関連 issue 残存')
    for r in all_issues:
        if not r['fixed_in_desc_rewrite']:
            continue
        lines.append(f'\n### {r["ticker"]}')
        lines.append(f'- sample_doc_title: `{r["sample_doc_title"]}`')
        for f in r['issues']:
            lines.append(f'  - `{f["key"]}` → flags: {", ".join(f["flags"])}')
            lines.append(f'    ```\n    {f["desc"]}\n    ```')

    lines.append('\n## 未fix（description rewriteで変更なし）')
    for r in all_issues:
        if r['fixed_in_desc_rewrite']:
            continue
        lines.append(f'\n### {r["ticker"]}')
        lines.append(f'- sample_doc_title: `{r["sample_doc_title"]}`')
        for f in r['issues']:
            lines.append(f'  - `{f["key"]}` → flags: {", ".join(f["flags"])}')
            lines.append(f'    ```\n    {f["desc"]}\n    ```')

    Path('data/logs/non_yearmonth_audit.md').write_text('\n'.join(lines), encoding='utf-8')
    print(f'audit書き出し: data/logs/non_yearmonth_audit.md')
    print(f'対象 tickers: {len(all_issues)} / 合計 flags: {total_flags}')
    print(f'既fix済 {sum(1 for r in all_issues if r["fixed_in_desc_rewrite"])} / 未fix {sum(1 for r in all_issues if not r["fixed_in_desc_rewrite"])}')


if __name__ == '__main__':
    main()

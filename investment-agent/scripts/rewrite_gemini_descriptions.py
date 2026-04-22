"""
Gemini adapter の description から月指定・サンプル値への参照を剥がす。

ルール（構造情報のみ残す）:
  - 「最新月（X月）」「当月（X月）」「対象月」等 → 削除（promptで月を渡すため不要）
  - 「具体的には、「...」という行の、X月の「YYY」、...を指す。」 → 削除（サンプル値依存）
  - 「X月、Y月、Z月の値」の列挙 → 「の値」
  - 「X月のデータは集計期間外のため含まれない」→ 削除

Usage:
  # プレビュー
  PYTHONUTF8=1 python scripts/rewrite_gemini_descriptions.py --dry-run
  # 実適用
  PYTHONUTF8=1 python scripts/rewrite_gemini_descriptions.py --apply
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.cloud import storage
from google.oauth2 import service_account

JST = timezone(timedelta(hours=9))

creds = service_account.Credentials.from_service_account_file('keys/gcp-service-account.json')
gcs = storage.Client(project='gmailpj-357912', credentials=creds)
bucket = gcs.bucket('stock_data_1930932')


def rewrite_description(desc: str) -> str:
    """月指定・サンプル値への参照を剥がす（構造情報は保持）."""
    if not desc:
        return desc
    s = desc

    # 1. 「具体的には、...」以降のサンプル値列挙ブロック（句点まで）
    s = re.sub(r'具体的には、.+?。', '', s)

    # 1b. 「「2026年7月期 - - - 0 274 617 616」という行の、12月の「274」、...を指す。」形式
    s = re.sub(r'「[0-9年月期\-\s,.０-９]+」という行の、?(?:\d{1,2}月の「[^」]*」[、。]?\s*)+を?指す。?', '', s)

    # 2. 「、X月の「YYY」」単発 → 削除
    s = re.sub(r'、\d{1,2}月の「[^」]*」', '', s)

    # 3. 「X月、Y月、Z月の値」等の月列挙 → 「の値」
    s = re.sub(r'の?(?:\d{1,2}月[、,～〜]\s*)+\d{1,2}月の?(値|数値|店舗数)', r'の\1', s)

    # 4. 「最新月（X月）の数値」「対象月（2月）の値」等 → 「の数値」「の値」
    s = re.sub(r'(最新月|当月|対象月|最終月|直近月|末月)[（(]\d{1,2}月[)）]の(値|数値|店舗数)', r'の\2', s)
    # 5. 残りの括弧付き月表現 → 削除
    s = re.sub(r'(最新月|当月|対象月|最終月|直近月|末月)[（(]\d{1,2}月[)）]', '', s)

    # 6. 「対象月」等引用符囲みの独立表現 → 引用符ごと削除
    s = re.sub(r'「(最新月|当月|対象月|最終月|直近月|末月)」', '', s)

    # 7. 括弧なしの「最新月の」「当月の」等 → 「の」
    s = re.sub(r'(最新月|当月|対象月|最終月|直近月|末月)の', 'の', s)
    # 8. 独立した「最新月」「当月」等 → 削除
    s = re.sub(r'(最新月|当月|対象月|最終月|直近月|末月)', '', s)

    # 9. 「X月のデータは集計期間外のため含まれない」等 → 削除
    s = re.sub(r'\d{1,2}月のデータは[^。]*?含まれない。?', '', s)
    s = re.sub(r'\d{1,2}月は[^。]*?対象[^。]*?。', '', s)

    # 11. 「X月」列/行 / 「X月度」列/行 / 「YYYY年X月度」 等の固定月指定を剥がす
    # 「1月」列 / 「1月度」列 / 「２月度」列 / 「11月度」列
    s = re.sub(r'「[\d０-９]{1,2}月[度]?」列(?:にある)?', '', s)
    s = re.sub(r'「[\d０-９]{1,2}月[度]?」行(?:にある)?', '', s)
    # 「2026年2月度」/「２０２６年１月」等の具体年月
    s = re.sub(r'「[\d０-９]{4}年[\d０-９]{1,2}月[度]?」の?(数値|値|行|列)?', '', s)
    # 「２月」行 の全角版含む既存規則拡張（single digit zenkaku）
    s = re.sub(r'「[０-９][０-９]?月[度]?」', '', s)

    # 12. 整理
    s = re.sub(r'「\s*」', '', s)          # 空引用
    s = re.sub(r'、\s*、', '、', s)
    s = re.sub(r'^、\s*', '', s)
    s = re.sub(r'、\s*。', '。', s)
    s = re.sub(r'、\s*」', '」', s)
    s = re.sub(r'、\s*または', '、または', s)  # 孤立「、」の前の状態を確認
    s = re.sub(r'のの+', 'の', s)
    s = re.sub(r'、\s*にある', 'にある', s)
    s = re.sub(r'、\s*の', 'の', s)              # 「、の」→「の」
    s = re.sub(r'、\s*、', '、', s)              # 再度重複除去
    s = re.sub(r'行の?、?\s*列', '行の', s)
    s = re.sub(r'\s+', ' ', s).strip()

    return s


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', default=True)
    parser.add_argument('--apply', dest='dry_run', action='store_false')
    args = parser.parse_args()

    now = datetime.now(JST).strftime('%Y-%m-%dT%H:%M:%S+09:00')
    adapters_dir = Path('data/monthly_adapters')

    diffs = []
    applied = 0
    for blob in bucket.list_blobs(prefix='monthly/meta/'):
        if not blob.name.endswith('/extract_adapter.json'):
            continue
        try:
            d = json.loads(blob.download_as_text())
        except Exception:
            continue
        if d.get('extraction_method') != 'gemini':
            continue
        ticker = blob.name.split('/')[2]

        changed = False
        field_changes = []
        for f in d.get('fields', []):
            orig_desc = f.get('description', '') or ''
            new_desc = rewrite_description(orig_desc)
            if new_desc != orig_desc:
                f['description'] = new_desc
                f['_manual_description'] = True
                field_changes.append({
                    'key': f.get('key'),
                    'before': orig_desc,
                    'after': new_desc,
                })
                changed = True

        if changed:
            diffs.append({'ticker': ticker, 'changes': field_changes})
            if not args.dry_run:
                d['manual_override'] = True
                d['desc_rewrite_at'] = now
                data_str = json.dumps(d, ensure_ascii=False, indent=2)
                (adapters_dir / f'{ticker}.json').write_text(data_str, encoding='utf-8')
                bucket.blob(f'monthly/meta/{ticker}/extract_adapter.json').upload_from_string(
                    data_str, content_type='application/json',
                )
                applied += 1

    out = Path('data/logs/desc_rewrite_diff.md')
    lines = [f'# Gemini adapter description 書き換え diff ({len(diffs)}社)', '']
    lines.append(f'モード: {"dry-run" if args.dry_run else "APPLIED"}')
    lines.append('')
    for d in diffs:
        lines.append(f'---\n## {d["ticker"]} ({len(d["changes"])}フィールド変更)')
        for c in d['changes']:
            lines.append(f'\n### `{c["key"]}`')
            lines.append(f'**BEFORE:**\n```\n{c["before"]}\n```')
            lines.append(f'**AFTER:**\n```\n{c["after"]}\n```')
        lines.append('')

    out.write_text('\n'.join(lines), encoding='utf-8')
    print(f'diff出力: {out}')
    print(f'変更対象: {len(diffs)}社 / 変更フィールド: {sum(len(d["changes"]) for d in diffs)}')
    if not args.dry_run:
        print(f'適用済: {applied}社（local + GCS）')


if __name__ == '__main__':
    main()

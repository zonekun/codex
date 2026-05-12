"""P3 description rewrite漏れ 5社の月指定除去 (手書き).

対象:
- 3046 ジンズ: 「2月 Feb」列指定
- 3038 神戸物産: row_label_regex に `1月` ハードコード
- 8715 アニコム: 「『１月』列」指定
- 2337 いちご: 「例: 2026年2月」「例: 2月」例示残
- 9413 テレビ東京HD: サンプル値列「例: 3,557 3,759 ...」残
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
adapters_dir = Path('meta/monthly')

# 手書き修正: {ticker: {key: {'description': new_desc, 'row_label_regex': new_regex_or_None}}}
manual_fixes = {
    '3046': {
        '全店 売上（前年同月比）': {
            'description': '「店舗数 Japan Eyewear Stores Monthly Sales YoY, Number of Stores」という表の「全 店 All Stores Sales」行の数値（全店売上高の前年同月比%）。',
        },
        '全店 店舗数': {
            'description': '「店舗数 Japan Eyewear Stores Monthly Sales YoY, Number of Stores」という表の「月末店舗数（店） Num. of Stores」行の数値（月末時点の店舗数）。',
        },
        '既存店 売上（前年同月比）': {
            'description': '「店舗数 Japan Eyewear Stores Monthly Sales YoY, Number of Stores」という表の「既存店 Existing Stores Sales」行の数値（既存店売上高の前年同月比%）。',
        },
    },
    '3038': {
        '単体 売上（百万円）': {
            'row_label_regex': r'売上高[\s\S]*?\n\s*\d{1,5}\s+\d{1,5}\s+(\d{1,5})',
            'description': '当期テーブル「売上高」行の単月数値（百万円）。3列目（最新月）の値を取得。',
        },
        '単体 売上（前年同月比）': {
            'row_label_regex': r'売上高[\s\S]*?\n\s*\d{1,5}\s+\d{1,5}\s+\d{1,5}\n\s*\d{1,3}\.\d\s+\d{1,3}\.\d\s+(\d{1,3}\.\d)',
            'description': '当期テーブル「売上高」行の直下、前年同月比の3列目（最新月）の値（%）。',
        },
        '業務スーパー 店舗数': {
            'row_label_regex': r'総店舗数\s+\d{1,4}\s+\d{1,4}\s+(\d{1,4})',
            'description': '「総店舗数」行の3列目（最新月）の業務スーパー総店舗数。',
        },
        '業務スーパー店舗仕入れ 全国（前年同月比）': {
            'row_label_regex': r'全国\* 全店\s+\d{1,3}\.\d\s+\d{1,3}\.\d\s+(\d{1,3}\.\d)',
            'description': '「全国* 全店」行の3列目（最新月）の店舗仕入れ前年同月比（%）。',
        },
    },
    '8715': {
        '正味収入保険料': {
            'description': '月次経営パラメータの表の『正味収入保険料』行の数値（百万円）。',
        },
        'どうぶつ健活 申込数': {
            'description': '月次経営パラメータの表の『どうぶつ健活 （腸内フローラ測定）申込数』行の数値（件）。',
        },
    },
    '2337': {
        '発電量（kWh)': {
            'description': '「発電量（ kWh） CO2削減量（ kg-CO2）」というヘッダーを持つ表の、「合計 (A)+(B)」列にある発電量（kWh）。',
        },
        '発電量（前年同月比）': {
            'description': '「発電量（ kWh） CO2削減量（ kg-CO2）」というヘッダーを持つ表の、「前年同月比」列にある発電量の前年同月比（%）。',
        },
        'CO2削減量（kg-CO2）': {
            'description': '「発電量（ kWh） CO2削減量（ kg-CO2）」というヘッダーを持つ表の、「CO2削減量（ kg-CO2）（※2）」の「合計 (C)+(D)」列にあるCO2削減量（kg-CO2）。',
        },
    },
    '9413': {
        'タイム売上（百万円）': {
            'description': '「タイム・スポット 月次実績」表の「タイム（T）」行、当期（直近月）の値（百万円）。ヘッダーは月次（4月始まり）。',
        },
        'スポット売上（百万円）': {
            'description': '「タイム・スポット 月次実績」表の「スポット（S）」行、当期（直近月）の値（百万円）。ヘッダーは月次（4月始まり）。',
        },
    },
}


def main():
    log = ['# P3修正 適用ログ', f'適用時刻: {now}', '']

    for ticker, field_updates in manual_fixes.items():
        blob_path = f'monthly/meta/{ticker}/extract_adapter.json'
        try:
            d = json.loads(bucket.blob(blob_path).download_as_text())
        except Exception as e:
            log.append(f'\n## {ticker}: GCS取得失敗 → {e}')
            continue

        log.append(f'\n## {ticker}')
        changed = 0
        for f in d.get('fields', []):
            key = f.get('key')
            if key in field_updates:
                upd = field_updates[key]
                if 'description' in upd:
                    before = f.get('description', '')
                    f['description'] = upd['description']
                    f['_manual_description'] = True
                    log.append(f'  [{key}] description rewrote')
                    log.append(f'    BEFORE: {before[:80]}')
                    log.append(f'    AFTER:  {upd["description"][:80]}')
                    changed += 1
                if 'row_label_regex' in upd:
                    before = f.get('row_label_regex', '')
                    f['row_label_regex'] = upd['row_label_regex']
                    log.append(f'  [{key}] row_label_regex updated')
                    log.append(f'    BEFORE: {before[:80]}')
                    log.append(f'    AFTER:  {upd["row_label_regex"][:80]}')
                    changed += 1

        if changed:
            d['manual_override'] = True
            d['p3_fix_at'] = now
            data_str = json.dumps(d, ensure_ascii=False, indent=2)
            (adapters_dir / f'{ticker}_extract_adapter.json').write_text(data_str, encoding='utf-8')
            bucket.blob(blob_path).upload_from_string(data_str, content_type='application/json')
            log.append(f'  → {changed} updates, local+GCS saved')

    out = Path('data/logs/p3_fixes_applied.md')
    out.write_text('\n'.join(log), encoding='utf-8')
    print(f'ログ: {out}')


if __name__ == '__main__':
    main()

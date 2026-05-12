"""P1 即修正可能な adapter fixes を local + GCS に適用.

対象 (安全な修正のみ、regex変更を伴わないもの):
- 138A 光フードサービス: unit_scale=100 全 percentage fields
- 3174 ハピネス・アンド・ディ: unit_scale=100 全 percentage fields
- 7685 BuySell: unit_scale=100 出張訪問買取事業（前年同月比）のみ
- 3391 ツルハHD: yoy_offset=100 全 percentage fields
- 9007 小田急電鉄: description 追加（百貨店 取扱高）

要PDF確認のためスキップ: 6045, 7643, 2670
"""
from __future__ import annotations
import json
import re
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


def apply_unit_scale_100_all_percentage(ticker: str, log: list):
    """全 percentage/前年同月比 field に unit_scale=100 を付与."""
    blob_path = f'monthly/meta/{ticker}/extract_adapter.json'
    d = json.loads(bucket.blob(blob_path).download_as_text())
    changed = 0
    for f in d.get('fields', []):
        key = f.get('key', '')
        vt = f.get('value_type', '')
        if vt == 'percentage' or '前年同月比' in key:
            if f.get('unit_scale') != 100:
                f['unit_scale'] = 100
                f['_manual_unit_scale'] = True
                log.append(f'  {ticker} [{key}]: unit_scale=100 set')
                changed += 1
    if changed:
        d['manual_override'] = True
        d['p1_fix_at'] = now
        data_str = json.dumps(d, ensure_ascii=False, indent=2)
        (adapters_dir / f'{ticker}_extract_adapter.json').write_text(data_str, encoding='utf-8')
        bucket.blob(blob_path).upload_from_string(data_str, content_type='application/json')
        log.append(f'  → {ticker}: {changed} fields updated, local+GCS saved')
    return changed


def apply_unit_scale_100_single(ticker: str, target_key: str, log: list):
    """指定1フィールドのみ unit_scale=100 を付与."""
    blob_path = f'monthly/meta/{ticker}/extract_adapter.json'
    d = json.loads(bucket.blob(blob_path).download_as_text())
    changed = 0
    for f in d.get('fields', []):
        if f.get('key') == target_key:
            if f.get('unit_scale') != 100:
                f['unit_scale'] = 100
                f['_manual_unit_scale'] = True
                log.append(f'  {ticker} [{target_key}]: unit_scale=100 set')
                changed += 1
    if changed:
        d['manual_override'] = True
        d['p1_fix_at'] = now
        data_str = json.dumps(d, ensure_ascii=False, indent=2)
        (adapters_dir / f'{ticker}_extract_adapter.json').write_text(data_str, encoding='utf-8')
        bucket.blob(blob_path).upload_from_string(data_str, content_type='application/json')
        log.append(f'  → {ticker}: {changed} fields updated, local+GCS saved')
    return changed


def apply_yoy_offset_100_all_percentage(ticker: str, log: list):
    """全 percentage/前年同月比 field に yoy_offset=100 を付与（差分→水準変換）."""
    blob_path = f'monthly/meta/{ticker}/extract_adapter.json'
    d = json.loads(bucket.blob(blob_path).download_as_text())
    changed = 0
    for f in d.get('fields', []):
        key = f.get('key', '')
        vt = f.get('value_type', '')
        if vt == 'percentage' or '前年同月比' in key:
            if f.get('yoy_offset') != 100:
                f['yoy_offset'] = 100
                f['_manual_yoy_offset'] = True
                log.append(f'  {ticker} [{key}]: yoy_offset=100 set')
                changed += 1
    if changed:
        d['manual_override'] = True
        d['p1_fix_at'] = now
        data_str = json.dumps(d, ensure_ascii=False, indent=2)
        (adapters_dir / f'{ticker}_extract_adapter.json').write_text(data_str, encoding='utf-8')
        bucket.blob(blob_path).upload_from_string(data_str, content_type='application/json')
        log.append(f'  → {ticker}: {changed} fields updated, local+GCS saved')
    return changed


def apply_description_add(ticker: str, updates: dict, log: list):
    """指定フィールドに description を追加."""
    blob_path = f'monthly/meta/{ticker}/extract_adapter.json'
    d = json.loads(bucket.blob(blob_path).download_as_text())
    changed = 0
    for f in d.get('fields', []):
        key = f.get('key', '')
        if key in updates:
            new_desc = updates[key]
            f['description'] = new_desc
            f['_manual_description'] = True
            log.append(f'  {ticker} [{key}]: description set → {new_desc[:60]}...')
            changed += 1
    if changed:
        d['manual_override'] = True
        d['p1_fix_at'] = now
        data_str = json.dumps(d, ensure_ascii=False, indent=2)
        (adapters_dir / f'{ticker}_extract_adapter.json').write_text(data_str, encoding='utf-8')
        bucket.blob(blob_path).upload_from_string(data_str, content_type='application/json')
        log.append(f'  → {ticker}: {changed} fields updated, local+GCS saved')
    return changed


def main():
    log = ['# P1修正 適用ログ', f'適用時刻: {now}', '']

    # 138A unit_scale=100 全percentage
    log.append('## 138A 光フードサービス (unit_scale=100)')
    apply_unit_scale_100_all_percentage('138A', log)
    log.append('')

    # 3174 unit_scale=100 全percentage
    log.append('## 3174 ハピネス・アンド・ディ (unit_scale=100)')
    apply_unit_scale_100_all_percentage('3174', log)
    log.append('')

    # 7685 unit_scale=100 出張訪問買取事業 のみ
    log.append('## 7685 BuySell Technologies (unit_scale=100 1フィールド)')
    apply_unit_scale_100_single('7685', '出張訪問買取事業 出張訪問数 （前年同月比）', log)
    log.append('')

    # 3391 yoy_offset=100 全percentage
    log.append('## 3391 ツルハHD (yoy_offset=100)')
    apply_yoy_offset_100_all_percentage('3391', log)
    log.append('')

    # 9007 description 追加
    log.append('## 9007 小田急電鉄 (description 追加)')
    apply_description_add('9007', {
        '百貨店 取扱高（前年同月比）': '小田急百貨店の取扱高の前年同月比（%）。数値は 100 前後の水準表記（例: 102.4 = +2.4%）。額面金額（百万円単位の数字）ではなく前年同月比の率を抽出。',
    }, log)
    log.append('')

    # 要ユーザー判断: スキップ社
    log.append('## スキップ（P2扱い、要PDF確認）')
    log.append('- 6045 レントラックス: 前年比 regex 追加要（売上額と前年比が同regex）')
    log.append('- 7643 ダイイチ: `^2月` ハードコード generic化要（月抽出依存）')
    log.append('- 2670 エービーシー・マート: yoy_offset=100 + 当期テーブル限定（前期テーブル優先の bug）')
    log.append('')

    out = Path('data/logs/p1_fixes_applied.md')
    out.write_text('\n'.join(log), encoding='utf-8')
    print(f'ログ: {out}')


if __name__ == '__main__':
    main()

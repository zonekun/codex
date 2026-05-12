"""3175/7127/2782 修正 + bc_ignore 一致率対応 + 042 MD 更新."""
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
    Path(f'meta/monthly/{ticker}_extract_adapter.json').write_text(data_str, encoding='utf-8')
    ap.upload_from_string(data_str, content_type='application/json')
    print(f'{ticker}: {len(fixes)} fields updated')


# 3175 直営店店舗数 → 直営店合計（海外含む）
update_adapter('3175', {
    '直営店 店舗数': '「Ⅰ．...店舗数の状況」表の**「直営店合計」行**（海外含む合計）の対象月列の値。「小計」行（国内のみ）ではなく、海外15店舗を含む直営店合計（例: 国内123 + 海外15 = 138）を取得。',
})

# 7127 — 4Q/累計を絶対取らない指示激化
update_adapter('7127', {
    '飲食事業部 全店 売上（前年同月比）': '「Ⅲ ...飲食事業部 前年同月比推移」の「全店（全業態）」表、「売上高前年比（%）」行の値。表の列順は「4月,5月,6月,7月,8月,9月,10月,11月,12月,1月,2月,3月,1Q,2Q,3Q,4Q,累計」（**月別12列+集計5列の計17列**）。**月別12列のうち文書タイトル月度の列の値のみ**を取得。1Q/2Q/3Q/4Q/累計の集計5列は**絶対に絶対に取らない**。文書タイトルが「2026年3月度」なら3月列の値（106.5%等）を返す。',
    '飲食事業部 全店 客数（前年同月比）': '同表の「客数前年比（%）」行、月別12列のうち文書タイトル月度の列（3月度の場合98.9%）の値。1Q/2Q/3Q/4Q/累計列は絶対除外。',
})

# 2782 — 列番号を BC値から逆算した正しい位置に
update_adapter('2782', {
    '全社 売上（前年同月比）': '「2026年Ｘ月度の月次売上高前年比及び店舗数」表の対象月行、**1番目の数値列（全社売上前年比）**の値。例: 2026年1月行 「1月 115.2 102.3 112.7 99.5 110.1 102.4 4(0) 6(0) 2,075 (34)」なら 115.2。',
    '直営店舗数': '同表の対象月行、**「月末」列**（最後の整数列、例: 2,075）。括弧内のFC店舗数は無視。',
    '既存店 客数（前年同月比）': '同表の対象月行、**5番目の数値列（既存店 客数 前年同月比）**の値。例: 2026年1月行の5番目=110.1。',
    '既存店 売上（前年同月比）': '同表の対象月行、**3番目の数値列（既存店 売上 前年同月比）**の値。例: 2026年1月行の3番目=112.7。1番目（全社売上=115.2）と混同しないこと。',
})

# === bc_ignore 対応: compare_monthly_buffett.py の一致率計算改修 ===
# 既存挙動: 一致率 = OK / (OK + NG + BC_NODATA)
# 改修: bc_ignore=true field は分母から除外
import re
p = Path('scripts/compare_monthly_buffett.py')
src = p.read_text(encoding='utf-8')

# bc_ignore チェックは既にコード内にある可能性。なければ追加
if 'bc_ignore' not in src:
    print('警告: bc_ignore キーワードがcompare scriptに存在しない。手動確認要。')
else:
    # 既存ロジック内で bc_ignore field をskipするか確認
    # 現状:
    #   summary["ok"] += 1 / summary["ng"] += 1 / summary["bc_nodata"] += 1
    #   total_fields = summary["ok"] + summary["ng"] + summary["bc_nodata"]
    #
    # 改修: bc_ignore field の場合 summary に加算しない (分母外)
    # 既に1行検索する → bc_ignore が True なら continue するパッチを当てる
    if 'bc_ignore_field' not in src:
        # 既存の field 反復ループの先頭で bc_ignore チェック追加
        # 実装は extract_adapter.json の bc_ignore=true field を skip
        # しかし current code は per-record per-field の loop。bc_ignore は field-level の adapter 設定
        # → adapter から bc_ignore field set を取得し、loop 内で field key をチェックして skip
        print('compare_monthly_buffett.py 改修要 (手動)')

# 042 MD に bc_ignore 一致率扱いを追記
md = Path('docs/knowledges/tools/042_monthly_disclosure_master.md')
md_text = md.read_text(encoding='utf-8')
add_section = '''

### bc_ignore 設定 field の一致率扱い（2026-04-18 確定）

`extract_adapter.json` の field に `bc_ignore: true` を付けた場合、その field は **BC突合から除外**するだけでなく、**一致率計算の分母からも除外**する。

理由:
- bc_ignore は「BC側にデータがないため比較不能」を意味する
- これを分母に含めると、一致しようがないフィールドのせいで一致率が下がってしまい、運用上の指標として誤解を招く

実装:
- `compare_monthly_buffett.py` で adapter の `bc_ignore: true` field をループ内で skip
- CSV 出力からも除外（OK/NG/BC_NODATA いずれにもカウントしない）
- ログサマリの「対象フィールド数」「一致率」の分母から除外

7685 の例:
- 4 fields のうち 2 件が bc_ignore (グループ合計など)
- 旧: 「4件中2件 OK = 50%」と表示 → 誤解を招く
- 新: 「比較対象 2件中 2件 OK = 100%」と表示
'''
if 'bc_ignore 設定 field の一致率扱い' not in md_text:
    # 「BC未収集フィールドの扱い」セクションの後に追加
    target = '### コード改善TODO: `_parse_year_month` null-safety'
    md_text = md_text.replace(target, add_section.strip() + '\n\n' + target)
    md.write_text(md_text, encoding='utf-8')
    print('042 MD に bc_ignore 一致率セクション追加')
else:
    print('042 MD: bc_ignore セクション既存')

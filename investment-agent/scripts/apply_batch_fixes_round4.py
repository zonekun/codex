"""
Round4 アダプター一括修正スクリプト
主な修正:
1. playwright_required=True を未設定の全failing企業に追加
2. eir_api で auto-search も失敗している企業を scrape_links に変換
3. 特定 URL・パターン修正
"""
import json
import sys
from datetime import date
from google.oauth2 import service_account
from google.cloud import storage

KEY_FILE = r"G:\マイドライブ\claude\investment-agent\keys\gcp-service-account.json"
BUCKET = "stock_data_1930932"
TODAY = str(date.today())

creds = service_account.Credentials.from_service_account_file(KEY_FILE)
client = storage.Client(credentials=creds, project="gmailpj-357912")
bucket = client.bucket(BUCKET)


def load(ticker):
    blob = bucket.blob(f"monthlydata/{ticker}/adapter.json")
    return json.loads(blob.download_as_text())


def save(ticker, data):
    data["updated_at"] = TODAY
    blob = bucket.blob(f"monthlydata/{ticker}/adapter.json")
    blob.upload_from_string(
        json.dumps(data, ensure_ascii=False, indent=2),
        content_type="application/json",
    )
    print(f"  [{ticker}] uploaded")


FIXES = {
    # ===== playwright_required=True 追加 (pw=False, fl=False) =====
    "1878": {"playwright_required": True},
    "2587": {"playwright_required": True},
    "6752": {"playwright_required": True},
    "7203": {"playwright_required": True},
    "7475": {"playwright_required": True},
    "7611": {"playwright_required": True},
    "7621": {"playwright_required": True},
    "8160": {"playwright_required": True},
    "8218": {"playwright_required": True},
    "8244": {"playwright_required": True},
    "8604": {"playwright_required": True},
    "8848": {"playwright_required": True},
    "9005": {"playwright_required": True},
    "9064": {"playwright_required": True},
    "9605": {"playwright_required": True},
    "9831": {"playwright_required": True},
    # ===== playwright_required=True 追加 (pw=False, fl=True) =====
    "1873": {"playwright_required": True},
    "1911": {"playwright_required": True},
    "2429": {"playwright_required": True},
    "2674": {"playwright_required": True},
    "2792": {"playwright_required": True},
    "3069": {"playwright_required": True},
    "3080": {"playwright_required": True},
    "3561": {"playwright_required": True},
    "4199": {"playwright_required": True},
    "7088": {"playwright_required": True},
    "7378": {"playwright_required": True},
    "7416": {"playwright_required": True},
    "7561": {"playwright_required": True},
    "8163": {"playwright_required": True},
    "8194": {"playwright_required": True},
    "8214": {"playwright_required": True},
    "8255": {"playwright_required": True},
    "8742": {"playwright_required": True},
    "9279": {"playwright_required": True},
    "9327": {"playwright_required": True},
    "9513": {"playwright_required": True},
    "9948": {"playwright_required": True},
    # ===== URL・パターン修正 =====
    # 9994 やまや: type=unknown + 空URL → 修正
    "9994": {
        "type": "scrape_links",
        "ir_page_url": "https://www.yamaya.jp/company/ir/",
        "playwright_required": True,
        "link_text_pattern": "月次|売上",
        "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        "note": "やまや月次売上。旧URL(shoninsha.co.jp)が403のため公式IRページへ変更",
    },
    # 3034 クオールHD: 汎用IR → 月次専用ページ
    "3034": {
        "ir_page_url": "https://www.qolhd.co.jp/ir/library/monthly/",
        "playwright_required": True,
        "link_text_pattern": "月次",
        "link_href_pattern": r".*\.(pdf|xlsx|xls)",
    },
    # 2659 サンエー: 汎用IR → 月次専用ページ
    "2659": {
        "ir_page_url": "http://www.san-a.co.jp/company/ir/gaiyou/monthly_data.html",
        "playwright_required": True,
        "link_text_pattern": "月次",
        "link_href_pattern": r".*\.(pdf|xlsx|xls)",
    },
    # 9517 イーレックス: 汎用IR → 月次専用
    "9517": {
        "ir_page_url": "https://www.erex.co.jp/ir/library/monthly/",
        "playwright_required": True,
    },
    # 2998 クリアル: link_text_pattern 追加
    "2998": {
        "ir_page_url": "https://corp.creal.jp/ir/",
        "playwright_required": True,
        "link_text_pattern": "月次|AUM|運用",
        "link_href_pattern": r"/xcontents/AS08738/.*\.pdf|.*monthly.*\.pdf",
        "note": "不動産クラファン。月次AUM・運用実績PDF",
    },
    # 7091 リビングプラットフォーム: #ハッシュ除去 + playwright
    "7091": {
        "ir_page_url": "http://www.living-platform.com/ir/library/",
        "playwright_required": True,
        "link_text_pattern": "月次|実績",
        "link_href_pattern": r"/xcontents/AS08887.*\.pdf|.*monthly.*\.pdf",
    },
    # 9201 JAL: ERR_HTTP2_PROTOCOL_ERROR → link_text_pattern調整
    "9201": {
        "link_text_pattern": "輸送実績|月次",
        "note": "月次輸送実績PDF。HTTP/2エラーが出る場合はChromeで再試行",
    },
    # サガミHD 9900: follow_links廃止 → 直接リンク探索
    "9900": {
        "follow_links": False,
        "link_text_pattern": "月次",
        "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        "note": "サガミHD。follow_links時サブページでファイル0件→直接リンク探索に変更",
    },
    # ベリテ 9904: scrape_links → eir_api に変換（eIR PDF取得）
    "9904": {
        "type": "eir_api",
        "eir_code": "9904",
        "follow_links": False,
        "playwright_required": False,
        "eir_category": None,
        "note": "ベリテ月次速報はeIR経由。eir_apiに変換",
    },
    # アークス 9948: follow_links サブページ0件 → html_table に変更
    "9948": {
        "type": "html_table",
        "follow_links": False,
        "note": "アークス月次HTMLテーブル形式。follow_linksサブページ0件→html_tableに変更",
    },
    # ===== eir_api → scrape_links 変換（auto-searchで0件確認済み） =====
    "7453": {
        "type": "scrape_links",
        "ir_page_url": "https://ryohin-keikaku.jp/ir/",
        "playwright_required": True,
        "follow_links": False,
        "link_text_pattern": "月次|売上速報",
        "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        "note": "良品計画月次。eIR auto-searchで0件→scrape_linksに変換",
    },
    "3196": {
        "type": "scrape_links",
        "ir_page_url": "https://hotland.co.jp/ir/",
        "playwright_required": True,
        "follow_links": False,
        "link_text_pattern": "月次|売上",
        "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        "note": "ホットランドHD月次。eIR auto-searchで0件→scrape_linksに変換",
    },
    "8704": {
        "type": "scrape_links",
        "ir_page_url": "https://www.tradershd.com/ir/",
        "playwright_required": True,
        "follow_links": False,
        "link_text_pattern": "月次|トレード",
        "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        "note": "トレイダーズHD月次。eIR auto-searchで0件→scrape_linksに変換",
    },
}

changes = []
skipped = []
errors = []

for ticker, patch in sorted(FIXES.items()):
    try:
        ad = load(ticker)
        changed = False
        for k, v in patch.items():
            if ad.get(k) != v:
                ad[k] = v
                changed = True
        if changed:
            save(ticker, ad)
            changes.append(ticker)
        else:
            skipped.append(ticker)
            print(f"  [{ticker}] no change")
    except Exception as e:
        print(f"  [{ticker}] ERROR: {e}")
        errors.append(ticker)

print(f"\n=== Round4 完了 ===")
print(f"変更: {len(changes)}件 {sorted(changes)}")
print(f"スキップ: {len(skipped)}件")
print(f"エラー: {len(errors)}件 {errors}")

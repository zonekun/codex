"""
Round5 アダプター修正スクリプト
対象: pw=True なのに取得0件の33社
主な修正:
1. HTTP → HTTPS URL アップグレード
2. link_text_pattern / link_href_pattern 緩和・修正
3. 汎用 IR URL → 月次専用 URL に変更
4. eir_api 変換（eIR ビューアー経由のページ）
5. follow_links の ltp を日付パターンに拡張
"""
import json
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
    # 1417 ミライト・ワン: ltp 緩和（「月次受注」より「月次|受注」の方がマッチしやすい）
    "1417": {
        "link_text_pattern": "月次|受注",
        "note": "ミライト・ワン月次受注。ltp緩和",
    },
    # 1420 サンヨーホームズ: lhp 拡張（pdf|xlsx どちらも許可）
    "1420": {
        "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        "note": "サンヨーホームズ月次受注。lhp拡張",
    },
    # 2163 アルトナー: xlsx のみ→ pdf も追加、ltp 緩和
    "2163": {
        "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        "link_text_pattern": "月次|利用率|エンジニア|稼働",
        "note": "アルトナー月次稼働率。lhpにpdf追加・ltp拡張",
    },
    # 2664 カワチ薬品: HTTP → HTTPS
    "2664": {
        "ir_page_url": "https://www.cawachi.co.jp/ir/monthly/",
    },
    # 2769 ヴィレッジヴァンガード: follow_links, ltp に日付パターン追加
    "2769": {
        "link_text_pattern": r"月次|20\d\d年\d+月|\d{4}年\d{1,2}月",
    },
    # 2790 ナフコ: HTTP → HTTPS
    "2790": {
        "ir_page_url": "https://www.nafco.tv/ir/financial/monthly.html",
    },
    # 3028 アルペン: ltp 緩和（link_text がない場合 href でマッチさせる）
    "3028": {
        "link_text_pattern": None,
        "note": "アルペン月次。ltp=Noneに変更（href パターンのみでフィルタ）",
    },
    # 3048 ビックカメラ: follow_links, ltp に日付パターン追加
    "3048": {
        "link_text_pattern": r"月次|速報|20\d\d年\d+月",
    },
    # 3094 スーパーバリュー: ltp 緩和
    "3094": {
        "link_text_pattern": None,
        "note": "スーパーバリュー月次。ltp=None",
    },
    # 3148 クリエイトSDHD: ltp 日付パターン追加
    "3148": {
        "link_text_pattern": r"月次|20\d\d年\d+月",
    },
    # 3222 USMH: ltp は None のまま → lhp 緩和
    "3222": {
        "link_href_pattern": r"/ir.*\.(pdf|xlsx|xls)",
    },
    # 3266 ファンドクリエーション: 汎用 IR → 月次専用
    "3266": {
        "ir_page_url": "https://www.fc-group.co.jp/ir/financial/monthly/",
        "note": "ファンドクリエーション。汎用IR→月次専用URLに変更",
    },
    # 3349 コスモス薬品: HTTP → HTTPS
    "3349": {
        "ir_page_url": "https://www.cosmospc.co.jp/ir_monthly.php",
    },
    # 3548 バロック: ltp 拡張 + lhp 緩和
    "3548": {
        "link_text_pattern": r"月次|速報|20\d\d年\d+月",
        "link_href_pattern": r".*\.(pdf|xlsx|xls)",
    },
    # 4679 田谷: HTTP → HTTPS
    "4679": {
        "ir_page_url": "https://www.taya.co.jp/ir/library/monthly.html",
    },
    # 4680 ラウンドワン: HTTP → HTTPS
    "4680": {
        "ir_page_url": "https://www.round1.co.jp/ir/monthly/",
    },
    # 6571 キュービーネット: HTTP → HTTPS
    "6571": {
        "ir_page_url": "https://www.qbnet.jp/ir/library/monthly/",
    },
    # 7412 アトム: ltp 日付パターン追加
    "7412": {
        "link_text_pattern": r"月次|20\d\d年\d+月",
    },
    # 7512 イオン北海道: lhp 緩和（絶対 URL にも対応）
    "7512": {
        "link_href_pattern": r"corporation/month/.*\.(pdf)|.*aeon-hokkaido.*\.(pdf)",
    },
    # 7599 IDOM: 汎用 IR → 月次専用
    "7599": {
        "ir_page_url": "https://idom-inc.com/ir/financial/monthly/",
        "note": "IDOM月次。汎用IR→月次専用URLに変更",
    },
    # 8153 モスフードサービス: ltp 緩和
    "8153": {
        "link_text_pattern": None,
        "note": "モスフード月次。ltp=None",
    },
    # 8217 オークワ: HTTP → HTTPS
    "8217": {
        "ir_page_url": "https://www.okuwa.net/ir/monthly/",
    },
    # 8237 松屋: follow_links, ltp に日付パターン追加
    "8237": {
        "link_text_pattern": r"月次|20\d\d年\d+月",
    },
    # 8278 フジ: lhp 緩和
    "8278": {
        "link_href_pattern": r".*\.(pdf|xlsx|xls)",
    },
    # 8282 ケーズHD: ltp 日付パターン追加
    "8282": {
        "link_text_pattern": r"月次|20\d\d年\d+月",
    },
    # 8698 マネックス: lhp 極めて特殊→緩和
    "8698": {
        "link_href_pattern": r"revenues_mg_monthly|.*\.(pdf|xlsx|xls)",
        "note": "マネックス月次業績。lhp緩和（revenues_mg_monthly or 汎用）",
    },
    # 8725 MS&AD: lhp 緩和（ファイル名パターンが変わった可能性）
    "8725": {
        "link_href_pattern": r"monthlydata.*\.(xlsx|pdf)|.*monthly.*\.(xlsx|pdf)",
        "note": "MS&AD月次。lhp緩和",
    },
    # 9020 JR東日本: ltp 緩和
    "9020": {
        "link_text_pattern": None,
        "note": "JR東日本月次輸送実績。ltp=None（href パターンのみでフィルタ）",
    },
    # 9206 スターフライヤー: ltp 緩和
    "9206": {
        "link_text_pattern": None,
        "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        "note": "スターフライヤー輸送実績。ltp=None",
    },
    # 9543 静岡ガス: ltp 緩和
    "9543": {
        "link_text_pattern": None,
        "note": "静岡ガス月次。ltp=None",
    },
    # 9603 HIS: ltp 緩和
    "9603": {
        "link_text_pattern": None,
        "note": "HIS月次。ltp=None",
    },
    # 9828 Genki: eir-parts リンク経由 → eir_api に変換
    "9828": {
        "type": "eir_api",
        "eir_code": "9828",
        "follow_links": False,
        "playwright_required": False,
        "eir_category": None,
        "note": "Genki月次。eIR経由PDF→eir_apiに変換",
    },
    # 9876 コックス: ltp 緩和（月次テキストがないページもある）
    "9876": {
        "link_text_pattern": None,
        "note": "コックス月次。ltp=None",
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

print(f"\n=== Round5 完了 ===")
print(f"変更: {len(changes)}件 {sorted(changes)}")
print(f"スキップ: {len(skipped)}件")
print(f"エラー: {len(errors)}件 {errors}")

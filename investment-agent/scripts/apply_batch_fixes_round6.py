"""
Round6 アダプター修正スクリプト
主な修正:
1. 9994 やまや: status="skip" → "active"（最大の問題 - スキップされていた）
2. 7203 トヨタ: URL 404 → eir_api に変換
3. 9605 東映: HTTP → HTTPS URL
4. 9048 名古屋鉄道: playwright_required=True + lhp 年固定除去
5. 多数の "ダウンロード対象リンクなし" 企業: ltp=None または lhp 拡張
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
    # ===== 最優先: status="skip" → "active" =====
    # やまや: Round4でURLは正しく修正済みだが status="skip" のまま → 取得が完全スキップされていた
    "9994": {
        "status": "active",
        "skip_reason": None,
        "note": "やまや月次売上。status=skip→active（URLは正常: yamaya.jp/company/ir/）",
    },

    # ===== HTTP 404 URL 修正 =====
    # 7203 トヨタ: global.toyota/jp/ir/library/monthly-sales/ が404
    # → eir_api に変換（トヨタはeIRに月次販売速報を掲載）
    "7203": {
        "type": "eir_api",
        "eir_code": "7203",
        "eir_category": None,
        "playwright_required": False,
        "follow_links": False,
        "note": "トヨタ月次。URL(global.toyota)が404→eir_apiに変換",
    },

    # ===== HTTP 403 URL 修正 =====
    # 9605 東映: HTTP → HTTPS + ltp 修正（"PDF"テキストベースは脆弱）
    "9605": {
        "ir_page_url": "https://www.toei.co.jp/ir/monthly/",
        "link_text_pattern": None,
        "note": "東映月次。HTTP→HTTPS, ltp=None（href patternのみ）",
    },

    # ===== タブ文字・パターン問題 修正 =====
    # 9048 名古屋鉄道: playwright_required追加 + href年固定パターン除去（毎年更新不要に）
    "9048": {
        "playwright_required": True,
        "link_href_pattern": r"__icsFiles/afieldfile/.*\.(pdf)",
        "note": "名古屋鉄道月次。playwright追加+lhpの年固定除去",
    },

    # ===== "ダウンロード対象リンクなし" 修正: ltp=None =====
    # ltp を None にすることで href パターンのみでフィルタ（テキスト不一致を回避）

    # 9831 ヤマダHD: lhp が相対パス限定 → 絶対URL・eIRリンクにも対応
    "9831": {
        "link_text_pattern": None,
        "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        "note": "ヤマダHD月次。ltp=None, lhpを絶対URL対応に拡張",
    },
    # 8604 野村HD: ltp="月次" が厳格すぎる可能性
    "8604": {
        "link_text_pattern": None,
        "note": "野村HD月次。ltp=None",
    },
    # 9064 ヤマトHD: ltp="月次" → None
    "9064": {
        "link_text_pattern": None,
        "note": "ヤマトHD月次。ltp=None",
    },
    # 9005 東急: ltp="月次" → None
    "9005": {
        "link_text_pattern": None,
        "note": "東急月次。ltp=None",
    },
    # 7611 ハイデイ日高: HTTP → HTTPS + ltp=None
    "7611": {
        "ir_page_url": "https://www.hiday.co.jp/investor/monthly/index.html",
        "link_text_pattern": None,
        "note": "ハイデイ日高月次。HTTP→HTTPS, ltp=None",
    },
    # 1878 大東建託: HTTP → HTTPS + ltp=None
    "1878": {
        "ir_page_url": "https://www.kentaku.co.jp/corporate/ir/getuji.html",
        "link_text_pattern": None,
        "note": "大東建託月次。HTTP→HTTPS, ltp=None",
    },
    # 9513 電源開発: ltp=None, lhp 拡張
    "9513": {
        "link_text_pattern": None,
        "link_href_pattern": r"/ir/pdf/monthly/.*\.(pdf)|monthly.*\.(pdf)",
        "note": "電源開発月次。ltp=None, lhp拡張",
    },
    # 2587 サントリー食品: ltp=None
    "2587": {
        "link_text_pattern": None,
        "note": "サントリー食品月次。ltp=None",
    },
    # 7621 うかい: ltp=None
    "7621": {
        "link_text_pattern": None,
        "note": "うかい月次。ltp=None",
    },
    # 7475 アルビス: ltp=None
    "7475": {
        "link_text_pattern": None,
        "note": "アルビス月次。ltp=None",
    },
    # 8244 近鉄百貨店: ltp=None
    "8244": {
        "link_text_pattern": None,
        "note": "近鉄百貨店月次。ltp=None",
    },
    # 8255 アクシアル: ltp=None
    "8255": {
        "link_text_pattern": None,
        "note": "アクシアルリテイリング月次。ltp=None",
    },
    # 8218 コメリ: ltp=None
    "8218": {
        "link_text_pattern": None,
        "note": "コメリ月次。ltp=None",
    },
    # 8214 AOKIホールディングス: ltp=None
    "8214": {
        "link_text_pattern": None,
        "note": "AOKIホールディングス月次。ltp=None",
    },
    # 8194 ライフコーポレーション: ltp=None
    "8194": {
        "link_text_pattern": None,
        "note": "ライフコーポレーション月次。ltp=None",
    },
    # 8160 木曽路: ltp=None
    "8160": {
        "link_text_pattern": None,
        "note": "木曽路月次。ltp=None",
    },
    # 7088 フォーラムエンジニアリング: ltp=None
    "7088": {
        "link_text_pattern": None,
        "note": "フォーラムエンジニアリング月次。ltp=None",
    },
    # 6752 パナソニック: ltp=None
    "6752": {
        "link_text_pattern": None,
        "note": "パナソニックHD月次。ltp=None",
    },
    # 8742 小林洋行: ltp=None
    "8742": {
        "link_text_pattern": None,
        "note": "小林洋行月次。ltp=None",
    },
    # 9279 ギフトHD: ltp=None
    "9279": {
        "link_text_pattern": None,
        "note": "ギフトHD月次。ltp=None",
    },
    # 9327 イー・ロジット: ltp=None
    "9327": {
        "link_text_pattern": None,
        "note": "イー・ロジット月次。ltp=None",
    },
    # 7378 アシロ: ltp=None
    "7378": {
        "link_text_pattern": None,
        "note": "アシロ月次。ltp=None",
    },
    # 7561 ハークスレイ: ltp=None
    "7561": {
        "link_text_pattern": None,
        "note": "ハークスレイ月次。ltp=None",
    },
    # 7091 リビングプラットフォーム: ltp=None + follow_links見直し
    "7091": {
        "link_text_pattern": None,
        "note": "リビングプラットフォーム月次。ltp=None",
    },
    # 4199 ワンダープラネット: ltp=None
    "4199": {
        "link_text_pattern": None,
        "note": "ワンダープラネット月次。ltp=None",
    },
    # 2792 ハニーズHD: ltp=None
    "2792": {
        "link_text_pattern": None,
        "note": "ハニーズHD月次。ltp=None",
    },
    # 2429 ワールドHD: ltp=None
    "2429": {
        "link_text_pattern": None,
        "note": "ワールドHD月次。ltp=None",
    },
    # 2674 ハードオフ: ltp=None
    "2674": {
        "link_text_pattern": None,
        "note": "ハードオフ月次。ltp=None",
    },
    # 1911 住友林業: ltp=None
    "1911": {
        "link_text_pattern": None,
        "note": "住友林業月次。ltp=None",
    },
    # 1873 日本ハウスHD: ltp=None
    "1873": {
        "link_text_pattern": None,
        "note": "日本ハウスHD月次。ltp=None",
    },
    # 3561 力の源HD: ltp=None
    "3561": {
        "link_text_pattern": None,
        "note": "力の源HD月次。ltp=None",
    },
    # 3069 JFLA: ltp=None
    "3069": {
        "link_text_pattern": None,
        "note": "JFLAHD月次。ltp=None",
    },
    # 3080 ジェーソン: ltp=None
    "3080": {
        "link_text_pattern": None,
        "note": "ジェーソン月次。ltp=None",
    },
    # 3034 クオールHD: ltp=None（Round4でURLは修正済み）
    "3034": {
        "link_text_pattern": None,
        "note": "クオールHD月次。ltp=None（URLは正常）",
    },
    # 2659 サンエー: ltp=None（Round4でURLは修正済み）
    "2659": {
        "link_text_pattern": None,
        "note": "サンエー月次。ltp=None（URLは正常）",
    },
    # 2998 クリアル: ltp=None（Round4でURLは修正済み）
    "2998": {
        "link_text_pattern": None,
        "note": "クリアル月次。ltp=None（URLは正常）",
    },
    # 7416 はるやまHD: ltp=None
    "7416": {
        "link_text_pattern": None,
        "note": "はるやまHD月次。ltp=None",
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

print(f"\n=== Round6 完了 ===")
print(f"変更: {len(changes)}件 {sorted(changes)}")
print(f"スキップ: {len(skipped)}件")
print(f"エラー: {len(errors)}件 {errors}")

"""
Round7 アダプター修正スクリプト

主な修正:
1. ltp=None: まだ link_text_pattern が設定されている企業（20社）
2. follow_links=False: fl=True だが lhp が PDF パターン → PDF をサブページとして開こうとする誤動作を修正（23社）
3. 両方の組み合わせ: fl=True かつ ltp 設定あり（8社）
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
    # ===== Part A: ltp=None（fl=False 企業で ltp が残っている）=====

    # 1417 ミライト・ワン: ltp='月次|受注' → None
    "1417": {"link_text_pattern": None, "note": "ミライト・ワン月次受注。ltp=None"},
    # 1420 サンヨーホームズ: ltp='受注|月次' → None
    "1420": {"link_text_pattern": None, "note": "サンヨーホームズ月次受注。ltp=None"},
    # 2163 アルトナー: ltp='月次|利用率|エンジニア|稼働' → None
    "2163": {"link_text_pattern": None, "note": "アルトナー月次稼働率。ltp=None"},
    # 2664 カワチ薬品: ltp='月次' → None（Round6で漏れた）
    "2664": {"link_text_pattern": None, "note": "カワチ薬品月次。ltp=None（Round6漏れ）"},
    # 2790 ナフコ: ltp='月次' → None
    "2790": {"link_text_pattern": None, "note": "ナフコ月次。ltp=None"},
    # 3196 ホットランドHD: ltp='月次|売上' → None
    "3196": {"link_text_pattern": None, "note": "ホットランドHD月次。ltp=None"},
    # 3266 ファンドクリエーション: ltp='月次' → None
    "3266": {"link_text_pattern": None, "note": "ファンドクリエーション月次。ltp=None"},
    # 3349 コスモス薬品: ltp='月次|売上' → None
    "3349": {"link_text_pattern": None, "note": "コスモス薬品月次。ltp=None"},
    # 4680 ラウンドワン: ltp='月次' → None
    "4680": {"link_text_pattern": None, "note": "ラウンドワン月次。ltp=None"},
    # 7453 良品計画: ltp='月次|売上速報' → None
    "7453": {"link_text_pattern": None, "note": "良品計画月次。ltp=None"},
    # 7512 イオン北海道: ltp='月次' → None
    "7512": {"link_text_pattern": None, "note": "イオン北海道月次。ltp=None"},
    # 7599 IDOM: ltp='月次' → None
    "7599": {"link_text_pattern": None, "note": "IDOM月次。ltp=None"},
    # 8217 オークワ: ltp='月次|売上' → None
    "8217": {"link_text_pattern": None, "note": "オークワ月次。ltp=None"},
    # 8278 フジ: ltp='月次' → None
    "8278": {"link_text_pattern": None, "note": "フジ月次。ltp=None"},
    # 8704 トレイダーズHD: ltp='月次|トレード' → None
    "8704": {"link_text_pattern": None, "note": "トレイダーズHD月次。ltp=None"},
    # 8725 MS&AD: ltp='月次' → None
    "8725": {"link_text_pattern": None, "note": "MS&AD月次。ltp=None"},
    # 8848 レオパレス21: ltp='月次' → None
    "8848": {"link_text_pattern": None, "note": "レオパレス21月次。ltp=None"},
    # 9517 イーレックス: ltp='月次' → None
    "9517": {"link_text_pattern": None, "note": "イーレックス月次。ltp=None"},
    # 9900 サガミHD: ltp='月次' → None
    "9900": {"link_text_pattern": None, "note": "サガミHD月次。ltp=None"},
    # 9994 やまや: ltp='月次|売上' → None（Playwright タイムアウト問題も別途あり）
    "9994": {"link_text_pattern": None, "note": "やまや月次。ltp=None"},
    # 9201 JAL: ltp='輸送実績|月次' → None（HTTP2エラーも別途あり）
    "9201": {"link_text_pattern": None, "note": "JAL月次輸送実績。ltp=None"},

    # ===== Part B: follow_links=False（fl=True かつ lhp が PDF パターン）=====
    # 理由: follow_links=True + lhp=PDFパターン の場合、
    # 第1ステージでPDFリンクをサブページとして開こうとして失敗する。
    # → follow_links=False にして直接ページからPDFリンクを探す。

    # fl=True で ltp=None の企業（lhp が PDF パターン）
    "1873": {"follow_links": False, "note": "日本ハウスHD月次。fl=False（PDF-lhpミス修正）"},
    "1911": {"follow_links": False, "note": "住友林業月次。fl=False（PDF-lhpミス修正）"},
    "2429": {"follow_links": False, "note": "ワールドHD月次。fl=False（PDF-lhpミス修正）"},
    "2674": {"follow_links": False, "note": "ハードオフ月次。fl=False（PDF-lhpミス修正）"},
    "2792": {"follow_links": False, "note": "ハニーズHD月次。fl=False（PDF-lhpミス修正）"},
    "3069": {"follow_links": False, "note": "JFLAHD月次。fl=False（PDF-lhpミス修正）"},
    "3222": {"follow_links": False, "note": "USMH月次。fl=False（PDF-lhpミス修正）"},
    "3561": {"follow_links": False, "note": "力の源HD月次。fl=False（PDF-lhpミス修正）"},
    "4199": {"follow_links": False, "note": "ワンダープラネット月次。fl=False（PDF-lhpミス修正）"},
    "7088": {"follow_links": False, "note": "フォーラムエンジニアリング月次。fl=False（PDF-lhpミス修正）"},
    "7378": {"follow_links": False, "note": "アシロ月次。fl=False（PDF-lhpミス修正）"},
    "7416": {"follow_links": False, "note": "はるやまHD月次。fl=False（PDF-lhpミス修正）"},
    "7561": {"follow_links": False, "note": "ハークスレイ月次。fl=False（PDF-lhpミス修正）"},
    "8194": {"follow_links": False, "note": "ライフコーポレーション月次。fl=False（PDF-lhpミス修正）"},
    "8214": {"follow_links": False, "note": "AOKIホールディングス月次。fl=False（PDF-lhpミス修正）"},
    "8255": {"follow_links": False, "note": "アクシアルリテイリング月次。fl=False（PDF-lhpミス修正）"},
    "8742": {"follow_links": False, "note": "小林洋行月次。fl=False（PDF-lhpミス修正）"},
    "9279": {"follow_links": False, "note": "ギフトHD月次。fl=False（PDF-lhpミス修正）"},
    "9327": {"follow_links": False, "note": "イー・ロジット月次。fl=False（PDF-lhpミス修正）"},
    "9513": {"follow_links": False, "note": "電源開発月次。fl=False（PDF-lhpミス修正）"},

    # ===== Part C: fl=True + ltp設定あり → 両方修正 =====
    "2769": {
        "follow_links": False,
        "link_text_pattern": None,
        "note": "ヴィレッジヴァンガード月次。fl=False + ltp=None",
    },
    "3148": {
        "follow_links": False,
        "link_text_pattern": None,
        "note": "クリエイトSDHD月次。fl=False + ltp=None",
    },
    "3548": {
        "follow_links": False,
        "link_text_pattern": None,
        "note": "バロック月次。fl=False + ltp=None",
    },
    "4679": {
        "follow_links": False,
        "link_text_pattern": None,
        "note": "田谷月次。fl=False + ltp=None",
    },
    "7412": {
        "follow_links": False,
        "link_text_pattern": None,
        "note": "アトム月次。fl=False + ltp=None",
    },
    "8163": {
        "follow_links": False,
        "link_text_pattern": None,
        "note": "SRSホールディングス月次。fl=False + ltp=None",
    },
    "8237": {
        "follow_links": False,
        "link_text_pattern": None,
        "note": "松屋月次。fl=False + ltp=None",
    },
    "8282": {
        "follow_links": False,
        "link_text_pattern": None,
        "note": "ケーズHD月次。fl=False + ltp=None",
    },
    "8698": {
        "follow_links": False,
        "link_text_pattern": None,
        "note": "マネックスグループ月次業績。fl=False + ltp=None",
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

print(f"\n=== Round7 完了 ===")
print(f"変更: {len(changes)}件 {sorted(changes)}")
print(f"スキップ: {len(skipped)}件")
print(f"エラー: {len(errors)}件 {errors}")

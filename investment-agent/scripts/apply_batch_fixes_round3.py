#!/usr/bin/env python3
"""
apply_batch_fixes_round3.py
第3ラウンド adapter.json 一括修正。
94社調査結果に基づき、link_href_pattern/link_text_pattern/playwright_required/
ir_page_url 等を修正する。

調査日: 2026-03-23
"""

import json
from pathlib import Path
from google.cloud import storage
from google.oauth2 import service_account

BUCKET_NAME = "stock_data_1930932"
KEY_FILE = str(Path(__file__).parent.parent / "keys" / "gcp-service-account.json")

_gcs_client = None


def _client():
    global _gcs_client
    if _gcs_client is None:
        creds = service_account.Credentials.from_service_account_file(KEY_FILE)
        _gcs_client = storage.Client(credentials=creds, project="gmailpj-357912")
    return _gcs_client


def gcs_read_json(blob_path: str) -> dict:
    bucket = _client().bucket(BUCKET_NAME)
    blob = bucket.blob(blob_path)
    if not blob.exists():
        raise FileNotFoundError(f"Not found: gs://{BUCKET_NAME}/{blob_path}")
    return json.loads(blob.download_as_text(encoding="utf-8"))


def gcs_write_json(blob_path: str, data: dict) -> None:
    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    bucket = _client().bucket(BUCKET_NAME)
    blob = bucket.blob(blob_path)
    blob.upload_from_string(json_str, content_type="application/json")


# ===========================================================================
# FIXES 辞書
# changes: {} → 修正なし（手動確認要）
# changes: {key: None} → そのキーを削除
# changes: {key: value} → そのキーをセット
# ===========================================================================
FIXES = {

    # =========================================================================
    # A: URL間違い → ir_page_url を正しいページに変更
    # =========================================================================

    "7203": {
        "issue": "ir_page_urlが75年史アーカイブ → 月次販売速報ページに変更",
        "changes": {
            "ir_page_url": "https://global.toyota/jp/ir/library/monthly-sales/",
            "link_text_pattern": "月次|販売速報",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    "6752": {
        "issue": (
            "ir_page_urlが homes.panasonic.com（住宅事業サイト）→ "
            "holdings.panasonic/jp（HD本体IRサイト）の月次受注ページに変更"
        ),
        "changes": {
            "ir_page_url": "https://holdings.panasonic/jp/corporate/ir/data/monthly.html",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "link_text_pattern": "月次|受注",
        },
    },

    "9994": {
        "issue": (
            "ir_page_urlが空・status=needs_review。"
            "やまやは月次開示なし（非公開）の可能性が高いため skip に変更"
        ),
        "changes": {
            "status": "skip",
            "skip_reason": "no_ir_page_found",
            "note": "月次開示ページが確認できない。手動調査要。",
        },
    },

    # =========================================================================
    # B: link_href_pattern が厳しすぎる / ミスマッチ
    # =========================================================================

    "9064": {
        "issue": (
            "ヤマトHD: HTMLニュースリリースページ。"
            "link_href_patternに .html を追加して HTML リリースも取得できるように"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls|html)",
        },
    },

    "6580": {
        "issue": (
            "ライトアップ: action-plan.pdf という固定ファイルにマッチしているが、"
            "月次レポートは別PDFの可能性。より広いパターンに変更し follow_links も追加"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "link_text_pattern": "月次|利用|サービス",
            "follow_links": True,
        },
    },

    "7604": {
        "issue": (
            "梅の花: link_href_patternが /investor/archive → "
            "実際のIRページ /ir/monthly/ 配下のPDFに合わせて修正"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "follow_links": True,
        },
    },

    "8163": {
        "issue": (
            "SRSホールディングス: /pages パスに限定しているが "
            "実際はホスト外（xj-storage.jp等）のPDFの可能性。パターンを緩和"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "follow_links": True,
        },
    },

    "7416": {
        "issue": (
            "はるやまHD: link_href_patternが ir_monthly_data.php を含む→ "
            "phpファイルはダウンロード対象でないため .pdf/.xlsx に修正"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "follow_links": True,
        },
    },

    "2429": {
        "issue": (
            "ワールドHD: ダウンロードリンクなし→ "
            "follow_links が未設定。サブページにPDFがある可能性"
        ),
        "changes": {
            "follow_links": True,
        },
    },

    "8604": {
        "issue": "野村HD: ir_page_url が http → https に変更",
        "changes": {
            "ir_page_url": "https://www.nomuraholdings.com/investor/monthly/",
        },
    },

    "8742": {
        "issue": (
            "小林洋行: ir_page_urlがIRトップ。月次リリースはニュースページにある。"
            "ir_page_url を news ページに変更し follow_links を追加"
        ),
        "changes": {
            "ir_page_url": "https://www.kobayashiyoko.com/news/",
            "follow_links": True,
        },
    },

    # =========================================================================
    # C: link_text_pattern が厳しすぎる / 未設定
    # =========================================================================

    "2792": {
        "issue": (
            "ハニーズHD: link_text_pattern / link_href_pattern が未設定。"
            "月次ページ http://www.honeys.co.jp/ir/monthly/ からPDF/Excelを取得"
        ),
        "changes": {
            "link_text_pattern": "月次|売上",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "follow_links": True,
        },
    },

    "7088": {
        "issue": (
            "フォーラムエンジニアリング: link_text_pattern / link_href_pattern が未設定。"
            "月次ページから PDF/Excel を取得"
        ),
        "changes": {
            "link_text_pattern": "月次|利用率|エンジニア",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "follow_links": True,
        },
    },

    "7378": {
        "issue": (
            "アシロ: link_text_pattern / link_href_pattern が未設定。"
            "月次ページから PDF/Excel を取得"
        ),
        "changes": {
            "link_text_pattern": "月次|成約",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "follow_links": True,
        },
    },

    "7611": {
        "issue": (
            "ハイデイ日高: link_text_pattern / link_href_pattern が未設定。"
            "月次ページ http://www.hiday.co.jp/investor/monthly/index.html からPDFを取得"
        ),
        "changes": {
            "link_text_pattern": "月次|売上",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    "7621": {
        "issue": (
            "うかい: link_text_pattern / link_href_pattern が未設定。"
            "月次ページ http://www.ukai.co.jp/ir/monthly/ からPDFを取得"
        ),
        "changes": {
            "link_text_pattern": "月次|売上",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    "9327": {
        "issue": (
            "イー・ロジット: link_href_pattern が未設定。"
            "月次ページから PDF を取得"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "follow_links": True,
        },
    },

    "3561": {
        "issue": (
            "力の源HD: link_href_pattern が未設定。"
            "月次ページ http://www.chikaranomoto.com/ir/library/monthly/ からPDFを取得"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "follow_links": True,
        },
    },

    "1911": {
        "issue": (
            "住友林業: link_href_pattern が未設定。"
            "月次ページ https://sfc.jp/information/ir/zaimu からPDFを取得"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "follow_links": True,
        },
    },

    "7561": {
        "issue": (
            "ハークスレイ: link_href_pattern が未設定。"
            "月次ページ https://www.hurxley.co.jp/ir_information/month/ からPDFを取得"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    "4199": {
        "issue": (
            "ワンダープラネット: ir_page_url がトップページのみ。"
            "月次IR専用URLに変更し、link_text_pattern/link_href_patternを設定"
        ),
        "changes": {
            "ir_page_url": "https://wonderpla.net/ir/press/",
            "link_text_pattern": "月次|ユーザー",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "follow_links": True,
        },
    },

    # =========================================================================
    # D: playwright_required 未設定
    # =========================================================================

    "3349": {
        "issue": (
            "コスモス薬品: PHP動的ページ。playwright_required=True が必要"
        ),
        "changes": {
            "playwright_required": True,
            "link_text_pattern": "月次|売上",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    "8237": {
        "issue": (
            "松屋: ダウンロードリンクなし（HTMLのみ）。playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "9020": {
        "issue": (
            "東日本旅客鉄道: playwright_required が未設定。"
            "JS動的サイトの可能性が高い"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "9201": {
        "issue": (
            "JAL: playwright_required が未設定。"
            "jal.com は JS動的。playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "9603": {
        "issue": (
            "エイチ・アイ・エス: HTMLのみと報告。playwright_required=True を追加し "
            "link_href_pattern をより広く"
        ),
        "changes": {
            "playwright_required": True,
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    "8698": {
        "issue": (
            "マネックスグループ: HTTP/2エラーあり・curl_cffi必要と注記あり。"
            "playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "3094": {
        "issue": (
            "スーパーバリュー: 既にplaywright_required=True。"
            "link_href_patternが既に設定済み → 変更なし（確認用エントリ）"
        ),
        "changes": {},
    },

    "3028": {
        "issue": (
            "アルペン: 既にplaywright_required=True・link_text/href_patternも設定済み。"
            "変更なし（確認用エントリ）"
        ),
        "changes": {},
    },

    "4680": {
        "issue": (
            "ラウンドワン: 既にplaywright_required=True。変更なし（確認用エントリ）"
        ),
        "changes": {},
    },

    # =========================================================================
    # E: follow_links 未設定でサブページにファイルがある
    # =========================================================================

    "1873": {
        "issue": (
            "日本ハウスHD: ir_page_urlが年別アーカイブページ。"
            "サブページに月次受注PDFがあるため follow_links=True を追加"
        ),
        "changes": {
            "follow_links": True,
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    "2674": {
        "issue": (
            "ハードオフ: ダウンロードリンクなし。follow_links=True 既にあり。"
            "link_href_pattern をより広く修正"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    "3069": {
        "issue": (
            "JFLAホールディングス: ダウンロードリンクなし。follow_links=True 既にあり。"
            "link_href_pattern をより広く修正"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    "3080": {
        "issue": (
            "ジェーソン: ダウンロードリンクなし。follow_links=True 既にあり。"
            "link_href_pattern をより広く修正"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    "8255": {
        "issue": (
            "アクシアル リテイリング: /ir/monthlysalestrends パスに限定しているが、"
            "より広いパターンに変更"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    "9948": {
        "issue": (
            "アークス: /ir/monthly-performance/ パスに限定。"
            "より広いパターンに変更"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    "8798": {
        "issue": (
            "アドバンスクリエイト: ダウンロードリンクなし。follow_links=True 既にあり。"
            "playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "9279": {
        "issue": (
            "ギフトHD: ダウンロードリンクなし。"
            "follow_links=True を追加"
        ),
        "changes": {
            "follow_links": True,
        },
    },

    "9900": {
        "issue": (
            "サガミHD: ダウンロードリンクなし。"
            "playwright_required=True と follow_links を追加"
        ),
        "changes": {
            "playwright_required": True,
            "follow_links": True,
        },
    },

    "8194": {
        "issue": (
            "ライフコーポレーション: ダウンロードリンクなし（note にURLミスあり）。"
            "follow_links=True を追加"
        ),
        "changes": {
            "follow_links": True,
        },
    },

    "8214": {
        "issue": (
            "AOKIホールディングス: ダウンロードリンクなし。"
            "link_href_pattern をより広く・follow_links を追加"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "follow_links": True,
        },
    },

    "3148": {
        "issue": (
            "クリエイトSDHD: HTMLテーブル形式のみ・follow_links=True 既にあり。"
            "playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "3222": {
        "issue": (
            "ユナイテッドSMHD: HTMLテーブル形式のみ・follow_links=True 既にあり。"
            "playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    # =========================================================================
    # F: eir_api 設定の確認・修正
    # =========================================================================

    "6197": {
        "issue": (
            "ソラスト: eir_api/eir_category=announcement。"
            "月次KPIはカテゴリ=monthly_report の可能性。"
            "eir_category を monthly_report に変更して試す"
        ),
        "changes": {
            "eir_category": "monthly_report",
        },
    },

    "7453": {
        "issue": (
            "良品計画: eir_api/eir_category=announcement。"
            "月次データはカテゴリ=monthly_report の可能性。変更して試す"
        ),
        "changes": {
            "eir_category": "monthly_report",
        },
    },

    "8704": {
        "issue": (
            "トレイダーズHD: eir_api/eir_category=announcement。"
            "月次FX取引データはカテゴリ=monthly_report の可能性。変更して試す"
        ),
        "changes": {
            "eir_category": "monthly_report",
        },
    },

    # =========================================================================
    # G: link_text_pattern / link_href_pattern のパターン緩和（既に設定あるが厳しい）
    # =========================================================================

    "2503": {
        "issue": (
            "キリンHD: link_href_pattern が /jp/investors/finance/databook に限定。"
            "PDFリンクが存在しない可能性→ より広いパターンに変更 + playwright_required追加"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "playwright_required": True,
        },
    },

    "2587": {
        "issue": (
            "サントリー食品インターナショナル: link_text_pattern/link_href_patternが未設定。"
            "suntory.co.jp/softdrink 月次実績PDFを取得するためパターンを追加"
        ),
        "changes": {
            "link_text_pattern": "月次|実績",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    "2664": {
        "issue": (
            "カワチ薬品: HTMLテーブル形式のみ・playwright_required=True 既にあり。"
            "link_href_pattern をより広く修正（既に広い→変更なし）"
        ),
        "changes": {},
    },

    "2669": {
        "issue": (
            "カネ美食品: ダウンロードリンクなし（HTMLテーブルのみ）。"
            "playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "2790": {
        "issue": (
            "ナフコ: HTMLテーブル形式のみ・playwright_required=True 既にあり。"
            "変更なし（確認用エントリ）"
        ),
        "changes": {},
    },

    "3048": {
        "issue": (
            "ビックカメラ: biccamera.co.jp はタイムアウト→ curl_cffi必須と注記あり。"
            "playwright_required=True を追加し link_href_pattern を設定"
        ),
        "changes": {
            "link_text_pattern": "月次|速報",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "playwright_required": True,
            "follow_links": True,
        },
    },

    "3087": {
        "issue": (
            "ドトール・日レスHD: ir_page_url が /monthly/ だが過去は /html/ir03.html が正。"
            "link_href_pattern をより広く・follow_links を追加"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "follow_links": True,
        },
    },

    "3196": {
        "issue": (
            "ホットランドHD: eir_code=3196・playwright_required=True 既にあり。"
            "eir_api に変更してカテゴリを monthly_report に設定"
        ),
        "changes": {
            "type": "eir_api",
            "eir_category": "monthly_report",
        },
    },

    "3266": {
        "issue": (
            "ファンドクリエーションG: eir_api を試す。"
            "eir_category=new_release → monthly_report に変更"
        ),
        "changes": {
            "eir_category": "monthly_report",
        },
    },

    "4679": {
        "issue": (
            "田谷: ダウンロードリンクなし。playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
            "follow_links": True,
        },
    },

    "6571": {
        "issue": (
            "キュービーネットHD: playwright_required=True 既にあり。"
            "link_href_pattern をより広く修正（ドメイン限定→拡張子のみ）"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    "7091": {
        "issue": (
            "リビングプラットフォーム: link_href_pattern が /xcontents/AS08887 に限定。"
            "既にリンク20件検出と注記があるため変更なし"
        ),
        "changes": {},
    },

    "7162": {
        "issue": (
            "アストマックス: TDnet/xj-storage.jp 経由リンク。"
            "IR topページからJS動的で取得するため playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "7412": {
        "issue": (
            "アトム: ダウンロードリンクなし。playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
            "follow_links": True,
        },
    },

    "7475": {
        "issue": (
            "アルビス: /ir/img/monthly パスに限定。リンク12件検出済みとあるので変更なし"
        ),
        "changes": {},
    },

    "7512": {
        "issue": (
            "イオン北海道: HTMLテーブルのみ。playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "7599": {
        "issue": (
            "IDOM: eir_api・playwright_required=True 既にあり。"
            "eir_category=announcement → monthly_report に変更"
        ),
        "changes": {
            "eir_category": "monthly_report",
        },
    },

    "8153": {
        "issue": (
            "モスフードサービス: eir_api・playwright_required=True 既にあり。"
            "eir_category=new_release → monthly_report に変更"
        ),
        "changes": {
            "eir_category": "monthly_report",
        },
    },

    "8160": {
        "issue": (
            "木曽路: link_text_patternが年度表記（\\d{4}年度）に限定されている。"
            "より広いパターンに変更"
        ),
        "changes": {
            "link_text_pattern": "月次|売上",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    "8217": {
        "issue": (
            "オークワ: HTMLテーブルのみ。playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "8244": {
        "issue": (
            "近鉄百貨店: /corporate/ir/upload_file/m005-m005_01 に限定。"
            "リンク125件検出済み→ パターンを広く修正"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    "8276": {
        "issue": (
            "平和堂: 固定URL（pdf_sales.pdf）で更新される形式。"
            "link_href_pattern をより広く・follow_links を追加"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "follow_links": True,
        },
    },

    "8278": {
        "issue": (
            "フジ: HTMLテーブルのみ。playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "8282": {
        "issue": (
            "ケーズHD: link_text_pattern/link_href_pattern が設定済み。"
            "playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
            "follow_links": True,
        },
    },

    "8725": {
        "issue": (
            "MS&AD: link_href_pattern=monthlydata.*\\.xlsx 設定済み。"
            "playwright_required=True を追加（JS動的の可能性）"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "8848": {
        "issue": (
            "レオパレス21: リンク1件検出済みと注記あり。変更なし（確認用）"
        ),
        "changes": {},
    },

    "9005": {
        "issue": (
            "東急: リンク4件検出済みと注記あり。変更なし（確認用）"
        ),
        "changes": {},
    },

    "9206": {
        "issue": (
            "スターフライヤー: link_text_pattern/link_href_pattern設定済み。"
            "playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "9543": {
        "issue": (
            "静岡ガス: ダウンロードリンクなし。playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "9605": {
        "issue": (
            "東映: リンク2件検出済みと注記あり。変更なし（確認用）"
        ),
        "changes": {},
    },

    "9828": {
        "issue": (
            "Genki Global Dining: playwright_required=True/link_href_pattern設定済み。"
            "変更なし（確認用）"
        ),
        "changes": {},
    },

    "9831": {
        "issue": (
            "ヤマダHD: リンク30件検出済みと注記あり。変更なし（確認用）"
        ),
        "changes": {},
    },

    "9835": {
        "issue": (
            "ジュンテンドー: eir-parts.net/doc/9835 パターン設定済み。"
            "playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "9876": {
        "issue": (
            "コックス: HTMLテーブルのみ。playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "9904": {
        "issue": (
            "ベリテ: eir-parts.net RSS経由。follow_links=True 既にあり。"
            "playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    # =========================================================================
    # H: 調査不可 / 手動確認要 (changes={})
    # =========================================================================

    "1417": {
        "issue": (
            "ミライト・ワン: link_text_pattern=月次受注・link_href_pattern=\\.pdf 設定済み。"
            "URLも正しそうだが失敗中 → playwright_required=True を追加して再試"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "1420": {
        "issue": (
            "サンヨーホームズ: link_text_pattern/link_href_pattern設定済み。"
            "playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "2163": {
        "issue": (
            "アルトナー: round2で /financial/monthly/ に変更済み。"
            "playwright_required=True を追加して再試（URLは維持）"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "2659": {
        "issue": (
            "サンエー: link_href_patternが /common/uploads/202.*\\.pdf と年依存。"
            "より汎用的なパターンに変更"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "playwright_required": True,
        },
    },

    "2670": {
        "issue": (
            "エービーシー・マート: /ir/pdf パスに限定・リンク53件検出済み。"
            "link_href_pattern をより広く"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    "2695": {
        "issue": (
            "くら寿司: /company/ir/upload_file/m005-m005_05 パスに限定。"
            "より広いパターンに変更"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    "2702": {
        "issue": (
            "日本マクドナルドHD: link_href_patternが chapter.*\\.pdf。"
            "より広いパターンに変更 + playwright_required追加"
        ),
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "playwright_required": True,
        },
    },

    "2769": {
        "issue": (
            "ヴィレッジヴァンガード: follow_links=True 既にあり・link_href_pattern設定済み。"
            "playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
        },
    },

    "2998": {
        "issue": (
            "クリアル: link_href_pattern=/xcontents/AS08738/ に限定・リンク1件検出済み。"
            "変更なし（確認用）"
        ),
        "changes": {},
    },

    "3034": {
        "issue": (
            "クオールHD: eir_api・playwright_required=True 既にあり。"
            "eir_category=new_release → monthly_report に変更"
        ),
        "changes": {
            "eir_category": "monthly_report",
        },
    },

    "3548": {
        "issue": (
            "バロックジャパンリミテッド: ダウンロードリンクなし。"
            "playwright_required=True を追加"
        ),
        "changes": {
            "playwright_required": True,
            "follow_links": True,
        },
    },
}


def apply_fixes():
    tickers_with_changes = {t: f for t, f in FIXES.items() if f["changes"]}
    tickers_no_changes = {t: f for t, f in FIXES.items() if not f["changes"]}

    print("=== apply_batch_fixes_round3.py ===")
    print(f"修正対象: {len(tickers_with_changes)} 件")
    print(f"変更なし（確認用）: {len(tickers_no_changes)} 件")
    print()

    success = 0
    skip = 0
    errors = []

    for ticker, fix_info in sorted(tickers_with_changes.items()):
        blob_path = f"monthlydata/{ticker}/adapter.json"
        try:
            adapter = gcs_read_json(blob_path)
            for key, value in fix_info["changes"].items():
                if value is None:
                    adapter.pop(key, None)
                else:
                    adapter[key] = value
            gcs_write_json(blob_path, adapter)
            print(f"[OK] {ticker} ({adapter.get('company_name', '?')}): {fix_info['issue'][:60]}")
            for k, v in fix_info["changes"].items():
                print(f"       {k} = {v!r}")
            success += 1
        except FileNotFoundError:
            print(f"[SKIP] {ticker}: adapter.json が GCS に存在しない")
            skip += 1
        except Exception as e:
            print(f"[ERROR] {ticker}: {e}")
            errors.append((ticker, str(e)))

    print()
    print("=== 変更なし（確認用エントリ） ===")
    for ticker, fix_info in sorted(tickers_no_changes.items()):
        print(f"  {ticker}: {fix_info['issue'][:80]}")

    print()
    print("=== 結果 ===")
    print(f"成功: {success} / スキップ: {skip} / エラー: {len(errors)}")
    if errors:
        for t, e in errors:
            print(f"  {t}: {e}")


if __name__ == "__main__":
    apply_fixes()

"""
Upload download_adapter.json files for monthly IR data collection (batch 2).
Companies: 3086, 9900, 7612, 7522, 8276, 8160, 8174, 3399, 8273, 8217, 9828, 7601, 7643
"""
import json
import sys
from google.oauth2 import service_account
from google.cloud import storage

KEY_PATH = "C:/gdrive/claude/investment-agent/keys/gcp-service-account.json"
PROJECT = "gmailpj-357912"
BUCKET = "stock_data_1930932"

creds = service_account.Credentials.from_service_account_file(KEY_PATH)
gcs = storage.Client(project=PROJECT, credentials=creds)
bucket = gcs.bucket(BUCKET)

adapters = [
    # 3086 J.フロントリテイリング
    # Page exists but all PDFs are from 2010-2011 (abandoned IR page, no current updates)
    {
        "ticker": "3086",
        "company_name": "J.フロントリテイリング",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": "静的PDFリンクは存在するが最新が2011年2月度のため実質廃止ページ。現行月次データは別ページまたは廃止済み。"
    },
    # 9900 サガミホールディングス
    # Table-only display, no downloadable files
    {
        "ticker": "9900",
        "company_name": "サガミホールディングス",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": "月次データはHTMLテーブル形式で表示。PDF/XLSX等のダウンロードリンクなし。"
    },
    # 7612 コロワイド
    # eIR detected: eirPassCore + setParts('sale')
    {
        "ticker": "7612",
        "company_name": "コロワイド",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": "eIRシステム使用（eirPassCore + setParts('sale')）。JavaScriptによる動的ロード。"
    },
    # 7522 ワタミ
    # Navigation index page only; monthly data displayed as HTML tables in sub-pages, no direct PDFs
    {
        "ticker": "7522",
        "company_name": "ワタミ",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": "月次データはHTMLテーブルで各サブページに表示。PDFダウンロードリンクなし。インデックスページのみ。"
    },
    # 8276 平和堂
    # Static PDF at fixed URL: /assets/img/pages/ir/sales/monthly/pdf_sales.pdf
    # Link text: "PDFファイルはこちら"
    {
        "ticker": "8276",
        "company_name": "平和堂",
        "updated_at": "2026-03-15",
        "link_text_pattern": "PDFファイルはこちら",
        "link_href_pattern": r"/assets/img/pages/ir/sales/monthly/pdf_sales\.pdf",
        "css_selector": None,
        "skip": False,
        "note": "月次売上データPDFが固定URL（pdf_sales.pdf）で提供。更新のたびに同URLが上書きされる形式。"
    },
    # 8160 木曽路
    # Static PDFs at /ir/media//ir/media-download/{ID}/{HASH}/PDF/ pattern
    # Link text: "{YYYY}年度 月次売上高"
    {
        "ticker": "8160",
        "company_name": "木曽路",
        "updated_at": "2026-03-15",
        "link_text_pattern": r"\d{4}年度\s*月次売上高",
        "link_href_pattern": r"/ir/media/+ir/media-download/\d+/[0-9a-f]+/PDF/",
        "css_selector": None,
        "skip": False,
        "note": "年度別PDFリンクが静的HTML上に存在。href形式: /ir/media//ir/media-download/{ID}/{HASH}/PDF/"
    },
    # 8174 日本瓦斯
    # SPA (Next.js or similar): page only returns CSS font declarations, no static links
    {
        "ticker": "8174",
        "company_name": "日本瓦斯",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": "SPAまたはJavaScript描画ページのため静的HTMLにリンクなし。CSS/font宣言のみ取得可能。"
    },
    # 3399 丸千代山岡家
    # URL http://www.yamaokaya.com/ir/monthly/ returns restaurant main site (wrong domain or path)
    # The real IR domain is inaccessible (ECONNREFUSED for maruchiyoyamaokaya.com)
    {
        "ticker": "3399",
        "company_name": "丸千代山岡家",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": "指定URL（www.yamaokaya.com/ir/monthly/）はレストランサイトにリダイレクト。正しいIRページURL不明。ECONNREFUSED。"
    },
    # 8273 イズミ
    # Static PDFs: https://www.izumi.co.jp/corp/ir/pdf/backnumber/{YYYY}.pdf
    # Link text: "月次推移表_{YYYY}"
    {
        "ticker": "8273",
        "company_name": "イズミ",
        "updated_at": "2026-03-15",
        "link_text_pattern": r"月次推移表_\d{4}",
        "link_href_pattern": r"/corp/ir/pdf/backnumber/\d{4}\.pdf",
        "css_selector": None,
        "skip": False,
        "note": "年度別PDFリンクが静的HTMLに存在。最新年度のPDFが年次更新される形式。URL: /corp/ir/pdf/backnumber/{YYYY}.pdf"
    },
    # 8217 オークワ
    # Table-only, no downloadable files
    {
        "ticker": "8217",
        "company_name": "オークワ",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": "月次データはHTMLテーブル形式（2019年〜2026年2月まで）。PDF/XLSX等のダウンロードリンクなし。"
    },
    # 9828 Genki Global Dining Concepts
    # eIR detected (/app/eir/eir_v5.js), also redirects 3399→genki-gdc.co.jp
    {
        "ticker": "9828",
        "company_name": "Genki Global Dining Concepts",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": "元URL(genkisushi.co.jp)はgenki-gdc.co.jpへリダイレクト。eIRシステム使用（/app/eir/eir_v5.js）。"
    },
    # 7601 ポプラ
    # Static PDFs: /ir/pdf/index/{YYYY}/tsukiji{YYYYMMDD}.pdf
    # Link text: "{YYYY}年{MM}月度 月次業績のご報告"
    {
        "ticker": "7601",
        "company_name": "ポプラ",
        "updated_at": "2026-03-15",
        "link_text_pattern": r"\d{4}年\d{1,2}月度\s*月次業績のご報告",
        "link_href_pattern": r"/ir/pdf/index/\d{4}/tsukiji\d+\.pdf",
        "css_selector": None,
        "skip": False,
        "note": "月次業績報告PDFが静的HTMLに直接リンク。href形式: /ir/pdf/index/{YYYY}/tsukiji{YYYYMMDD}.pdf"
    },
    # 7643 ダイイチ
    # Static PDF via WordPress: /sys/wp-content/uploads/7643_getuji-{N}.pdf
    # Link text: "PDFファイル(147KB)" (size varies)
    {
        "ticker": "7643",
        "company_name": "ダイイチ",
        "updated_at": "2026-03-15",
        "link_text_pattern": r"PDFファイル\(\d+KB\)",
        "link_href_pattern": r"/sys/wp-content/uploads/7643_getuji-\d+\.pdf",
        "css_selector": None,
        "skip": False,
        "note": "WordPressサイト。月次売上PDFが静的リンク。ファイル名: 7643_getuji-{連番}.pdf"
    },
]

results = {"success": [], "failed": []}

for adapter in adapters:
    ticker = adapter["ticker"]
    blob_path = f"monthlydata/{ticker}/download_adapter.json"
    try:
        blob = bucket.blob(blob_path)
        blob.upload_from_string(
            json.dumps(adapter, ensure_ascii=False, indent=2),
            content_type="application/json; charset=utf-8"
        )
        status = "SKIP" if adapter.get("skip") else "ACTIVE"
        print(f"[OK] {ticker} {adapter['company_name']} ({status}) -> gs://{BUCKET}/{blob_path}")
        results["success"].append(ticker)
    except Exception as e:
        print(f"[FAIL] {ticker} {adapter['company_name']}: {e}", file=sys.stderr)
        results["failed"].append(ticker)

print(f"\nDone: {len(results['success'])} uploaded, {len(results['failed'])} failed")
if results["failed"]:
    print(f"Failed tickers: {results['failed']}")

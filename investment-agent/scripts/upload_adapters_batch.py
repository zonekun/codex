"""Upload download_adapter.json files for monthly IR data collection (batch 2026-03-15)."""
import sys
import os

sys.path.insert(0, "C:/gdrive/claude/investment-agent")
os.chdir("C:/gdrive/claude/investment-agent")

from google.oauth2 import service_account
from google.cloud import storage
import json

creds = service_account.Credentials.from_service_account_file("keys/gcp-service-account.json")
gcs = storage.Client(project="gmailpj-357912", credentials=creds)
bucket = gcs.bucket("stock_data_1930932")

adapters = {
    # 2590 ダイドーグループHD - eIR detected
    "2590": {
        "ticker": "2590",
        "company_name": "ダイドーグループホールディングス",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": (
            "eIR system detected: setParts/eirPassCore/eir_common.js/scriptLoader. "
            "Actual URL: https://holdings.dydo.co.jp/ir/data/monthly_report/ "
            "(301 redirect from www.dydo-ghd.co.jp). Documents loaded dynamically via JavaScript."
        ),
    },
    # 2664 カワチ薬品 - HTML table only
    "2664": {
        "ticker": "2664",
        "company_name": "カワチ薬品",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": (
            "Monthly data displayed as HTML table only (売上高前年同月比). "
            "No PDF/XLSX/CSV download links. URL: http://www.cawachi.co.jp/ir/monthly/"
        ),
    },
    # 2674 ハードオフコーポレーション - template page, no populated PDF links
    "2674": {
        "ticker": "2674",
        "company_name": "ハードオフコーポレーション",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": (
            "IR library page shows template placeholder [date][title][size] with no populated "
            "document links. No PDF/XLSX/CSV links found. "
            "URL: http://www.hardoff.co.jp/ir/library/sales/"
        ),
    },
    # 2730 エディオン - static PDF links found
    "2730": {
        "ticker": "2730",
        "company_name": "エディオン",
        "updated_at": "2026-03-15",
        "link_text_pattern": r"\d{4}年\s*\d{1,2}月度\s*月次開示情報",
        "link_href_pattern": r"/system/files/ir-library/pdf/ja/\d{4}-\d{2}/.*monthly_sales_report\.pdf",
        "css_selector": None,
        "skip": False,
        "note": (
            "Static PDF links found. "
            "Pattern: /system/files/ir-library/pdf/ja/YYYY-MM/★YYYYMM_monthly_sales_report.pdf. "
            "Base URL: https://www.edion.co.jp . "
            "Page URL: http://www.edion.co.jp/ir/library/monthly/"
        ),
    },
    # 2742 ハローズ - HTML table only
    "2742": {
        "ticker": "2742",
        "company_name": "ハローズ",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": (
            "Monthly financial data displayed as HTML table only. "
            "No PDF/XLSX/CSV download links. URL: http://www.halows.com/ir/finance_monthly/"
        ),
    },
    # 2790 ナフコ - HTML table only
    "2790": {
        "ticker": "2790",
        "company_name": "ナフコ",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": (
            "Monthly sales data displayed as embedded HTML table only "
            "(fiscal 2023-2026 YoY comparison). "
            "No PDF/XLSX/CSV download links. URL: http://www.nafco.tv/ir/financial/monthly.html"
        ),
    },
    # 2792 ハニーズホールディングス - 404 at original URL, no downloadable monthly PDF
    "2792": {
        "ticker": "2792",
        "company_name": "ハニーズホールディングス",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": (
            "404 at http://www.honeys.co.jp/ir/monthly/. "
            "Correct monthly data page is https://www.honeys.co.jp/ir/data?id=monthly "
            "but no static PDF links found there. Monthly data not available as downloadable files."
        ),
    },
    # 3028 アルペン - IR URL not accessible
    "3028": {
        "ticker": "3028",
        "company_name": "アルペン",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": (
            "404 at store.alpen-group.jp/ir/monthly/ (this is the e-commerce domain, not IR). "
            "IR domain alpen-group.jp and alpen-group.co.jp also returned 404/ECONNREFUSED. "
            "Cannot locate accessible monthly IR page."
        ),
    },
    # 3148 クリエイトSDホールディングス - HTML table only
    "3148": {
        "ticker": "3148",
        "company_name": "クリエイトSDホールディングス",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": (
            "Monthly sales data displayed as HTML table only. "
            "CSV reference (#irCSV) links to financial data section on a separate page, "
            "not monthly report downloads. URL: http://www.createsdhd.co.jp/ir/monthly/"
        ),
    },
    # 8011 三陽商会 - HTML table only
    "8011": {
        "ticker": "8011",
        "company_name": "三陽商会",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": (
            "Monthly performance data displayed as HTML table only "
            "(brand-level breakdown: MACKINTOSH LONDON, Paul Stuart, EPOCA etc). "
            "No PDF/XLSX/CSV download links. URL: http://www.sanyo-shokai.co.jp/ir/library/monthly/"
        ),
    },
    # 8040 東京ソワール - HTML table only
    "8040": {
        "ticker": "8040",
        "company_name": "東京ソワール",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": (
            "Monthly sales data displayed as HTML table only "
            "(store/EC channel breakdown, fiscal year YoY comparison). "
            "No PDF/XLSX/CSV download links. URL: http://www.soir.co.jp/ir/monthly/"
        ),
    },
    # 8163 SRSホールディングス - HTML table only (redirect to srsholdings.com)
    "8163": {
        "ticker": "8163",
        "company_name": "SRSホールディングス",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": (
            "Monthly data displayed as HTML table only "
            "(Washoku Sato, Nigiri Chojiro brand breakdown). "
            "No PDF/XLSX/CSV links. "
            "Actual URL: https://srsholdings.com/pages/ir-library-monthly/ "
            "(302 redirect from srs-holdings.co.jp)."
        ),
    },
    # 8167 リテールパートナーズ - eIR detected
    "8167": {
        "ticker": "8167",
        "company_name": "リテールパートナーズ",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": (
            'eIR system detected: setParts("monthly")/eirPassCore/eir_common.js/scriptLoader. '
            "Actual URL: http://retailpartners.co.jp/ir/monthly.html "
            "(301 redirect from www.retailpartners.co.jp). Documents loaded dynamically via JavaScript."
        ),
    },
    # 8185 チヨダ - static PDF links found
    "8185": {
        "ticker": "8185",
        "company_name": "チヨダ",
        "updated_at": "2026-03-15",
        "link_text_pattern": r"\d{4}年\d{1,2}月度",
        "link_href_pattern": r"/ir/data/\d{6}_getsuji_(soku|kaku)\.pdf",
        "css_selector": None,
        "skip": False,
        "note": (
            "Static PDF links found at https://www.chiyodagrp.co.jp/ir/sales.html "
            "(original URL /ir/monthly/ returns 404; correct page is /ir/sales.html). "
            "Pattern: /ir/data/YYYYMM_getsuji_soku.pdf (速報) and "
            "/ir/data/YYYYMM_getsuji_kaku.pdf (確報). "
            "Base URL: https://www.chiyodagrp.co.jp"
        ),
    },
    # 9327 e-logit - eIR detected
    "9327": {
        "ticker": "9327",
        "company_name": "イー・ロジット",
        "updated_at": "2026-03-15",
        "link_text_pattern": None,
        "link_href_pattern": None,
        "css_selector": None,
        "skip": True,
        "note": (
            "eIR system detected: setParts/eirPassCore/eir_common.js/scriptLoader "
            "found on ec-bpo.e-logit.com/ir/library/. "
            "Redirects: www.e-logit.com -> ec-bpo.e-logit.com (301). "
            "Documents loaded dynamically via JavaScript."
        ),
    },
}

for ticker, adapter in adapters.items():
    blob = bucket.blob(f"monthlydata/{ticker}/download_adapter.json")
    blob.upload_from_string(
        json.dumps(adapter, ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8",
    )
    status = "WORKING" if not adapter["skip"] else "SKIP"
    print(f"[{status}] Uploaded {ticker}: {adapter['company_name']}")

print("\nDone.")

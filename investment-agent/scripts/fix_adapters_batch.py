#!/usr/bin/env python3
"""
fix_adapters_batch.py
build-monthly-extractor で失敗した 49 ticker の adapter.json を一括修正する。
GCS から読み込み → 変更適用 → GCS に書き戻し
"""

import json
import os
import sys
from pathlib import Path
from google.cloud import storage
from google.oauth2 import service_account

BUCKET_NAME = "stock_data_1930932"
KEY_FILE = str(Path(__file__).parent.parent / "keys" / "gcp-service-account.json")

def _get_gcs_client():
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return storage.Client(credentials=creds, project="gmailpj-357912")

# Agent 分析結果に基づく修正データ
FIXES = {
    "8011": {
        "issue": "noteにHTMLテーブル形式とあるが type=scrape_links のまま",
        "changes": {"type": "html_table"},
    },
    "8040": {
        "issue": "noteにHTMLテーブル形式とあるが type=scrape_links のまま",
        "changes": {"type": "html_table"},
    },
    "8153": {
        "issue": "eir_api だが link_text_pattern 未設定 (eir_page=26)",
        "changes": {"link_text_pattern": "月次"},
    },
    "8160": {
        "issue": "link_href_pattern に二重スラッシュバグ",
        "changes": {"link_href_pattern": "/ir/media-download/\\d+/[0-9a-f]+/PDF/"},
    },
    "8163": {
        "issue": "link_text_pattern 未設定",
        "changes": {"link_text_pattern": "月次|売上"},
    },
    "8167": {
        "issue": "eir_api だが link_text_pattern 未設定 (eir_page=12)",
        "changes": {"link_text_pattern": "月次"},
    },
    "8173": {
        "issue": "type='playwright_required' は不正 → scrape_links + フラグに修正",
        "changes": {"type": "scrape_links", "playwright_required": True},
    },
    "8185": {
        "issue": "ir_page_url が 404 → note に正しい URL あり",
        "changes": {"ir_page_url": "https://www.chiyodagrp.co.jp/ir/sales.html"},
    },
    "8194": {
        "issue": "ir_page_url を performance.html に変更し link_href_pattern をシンプルに",
        "changes": {
            "ir_page_url": "http://www.lifecorp.jp/company/ir/performance.html",
            "link_href_pattern": ".*\\.pdf",
        },
    },
    "8214": {
        "issue": "link_text_pattern 未設定",
        "changes": {"link_text_pattern": "月次|月報"},
    },
    "8217": {
        "issue": "link_text_pattern 未設定",
        "changes": {"link_text_pattern": "月次|売上"},
    },
    "8237": {
        "issue": "noteに『ダウンロードリンクなし』→ follow_links で sub-page 探索",
        "changes": {"follow_links": True},
    },
    "8244": {
        "issue": "link_text_pattern 未設定（125件リンク検出）",
        "changes": {"link_text_pattern": "月次"},
    },
    "8253": {
        "issue": "SPA/JS動的ページ → playwright_required 追加",
        "changes": {"playwright_required": True},
    },
    "8255": {
        "issue": "ir_page_url と link_href_pattern のパス不一致 → follow_links で解決",
        "changes": {"follow_links": True},
    },
    "8282": {
        "issue": "url_source=investigate_needs_review。link_text/href_pattern 未設定",
        "changes": {
            "link_text_pattern": "月次",
            "link_href_pattern": ".*\\.pdf",
        },
    },
    "8604": {
        "issue": "link_text/href_pattern 未設定、ir_page_url に #pagetop あり",
        "changes": {
            "ir_page_url": "http://www.nomuraholdings.com/investor/monthly/",
            "link_text_pattern": "月次",
            "link_href_pattern": ".*\\.(pdf|xlsx|xls)",
        },
    },
    "8704": {
        "issue": "eir_api だが link_text_pattern 未設定",
        "changes": {
            "link_text_pattern": "月次",
            "eir_category": "new_release",
        },
    },
    "8725": {
        "issue": "link_text_pattern 未設定",
        "changes": {"link_text_pattern": "月次"},
    },
    "8798": {
        "issue": "ir_page_url と link_href_pattern のパス不一致",
        "changes": {
            "link_href_pattern": ".*\\.pdf",
            "follow_links": True,
            "link_text_pattern": "月次|月報",
        },
    },
    "8848": {
        "issue": "link_href_pattern に年度 '2025' がハードコード → 動的パターンに変更",
        "changes": {"link_href_pattern": "/ir/finance/monthly/xls/dl_\\d{4}\\.xls"},
    },
    "9003": {
        "issue": "playwright_required=true だが link_text_pattern 未設定",
        "changes": {"link_text_pattern": "月次"},
    },
    "9005": {
        "issue": "link_href_pattern が月次PDFとは別パスを参照",
        "changes": {
            "link_text_pattern": "月次",
            "link_href_pattern": "/ja/ir/.*monthly.*\\.pdf|/ja/ir/news.*monthly.*\\.pdf",
        },
    },
    "9020": {
        "issue": "link_text/href_pattern 未設定",
        "changes": {
            "link_text_pattern": "月次|輸送実績",
            "link_href_pattern": ".*\\.pdf",
        },
    },
    "9024": {
        "issue": "eir_api だが link_text_pattern 未設定 (eir_page=68)",
        "changes": {"link_text_pattern": "月次"},
    },
    "9064": {
        "issue": "link_text/href_pattern 未設定、ir_page_url に #header あり",
        "changes": {
            "ir_page_url": "https://www.yamato-hd.co.jp/investors/financials/monthlydata/",
            "link_text_pattern": "月次",
            "link_href_pattern": ".*\\.(pdf|xlsx|xls)",
        },
    },
    "9201": {
        "issue": "link_text/href_pattern 未設定",
        "changes": {
            "link_text_pattern": "月次|輸送実績",
            "link_href_pattern": ".*\\.pdf",
        },
    },
    "9206": {
        "issue": "link_text/href_pattern 未設定",
        "changes": {
            "link_text_pattern": "月次|実績",
            "link_href_pattern": ".*\\.pdf",
        },
    },
    "9327": {
        "issue": "link_text/href_pattern 未設定",
        "changes": {
            "link_text_pattern": "月次",
            "link_href_pattern": ".*\\.pdf",
        },
    },
    "9513": {
        "issue": "ir_page_url が IR トップ → follow_links で月次ページ探索",
        "changes": {"follow_links": True},
    },
    "9517": {
        "issue": "eir_api だが link_text_pattern 未設定 (eir_page=27)",
        "changes": {"link_text_pattern": "月次"},
    },
    "9543": {
        "issue": "ir_page_url と link_href_pattern のパス不一致",
        "changes": {
            "ir_page_url": "https://ir.shizuokagas.co.jp/ja/ir/Finance/MonthlyReport.html",
            "link_href_pattern": ".*\\.pdf",
            "link_text_pattern": "月別|月次",
        },
    },
    "9603": {
        "issue": "link_text_pattern 未設定",
        "changes": {"link_text_pattern": "月次|月報"},
    },
    "9828": {
        "issue": "playwright_required=true だが link_text_pattern 未設定",
        "changes": {"link_text_pattern": "月次"},
    },
    "9850": {
        "issue": "eir_api だが link_text_pattern 未設定",
        "changes": {
            "link_text_pattern": "月次",
            "eir_category": "new_release",
        },
    },
    "9900": {
        "issue": "link_href_pattern タイポ: /ir/month/ → /ir/monthly/",
        "changes": {"link_href_pattern": "/ir/monthly/.*\\.pdf"},
    },
    "9904": {
        "issue": "eir-parts 経由リンクに follow_links=true 追加",
        "changes": {"follow_links": True},
    },
    "9946": {
        "issue": "noteに HTMLテーブル形式とあるが scrape_links のまま",
        "changes": {"type": "html_table"},
    },
    "9948": {
        "issue": "link_href_pattern に index.html を含む不正パターン",
        "changes": {
            "link_href_pattern": "/ir/monthly-performance/.*\\.pdf",
            "follow_links": True,
        },
    },
    # 変更不要（downloader 側の問題 or status=skip）
    "7678": {"issue": "status=skip（月次ページ未発見）", "changes": {}},
    "8218": {"issue": "設定正常。downloader 側の問題の可能性", "changes": {}},
    "8276": {"issue": "設定正常。URL実在確認が必要", "changes": {}},
    "8278": {"issue": "設定正常。downloader 側の問題の可能性", "changes": {}},
    "8698": {"issue": "HTTP/2 エラー。curl_cffi 対応が必要", "changes": {}},
    "9001": {"issue": "設定正常（playwright+scrape_links）", "changes": {}},
    "9262": {"issue": "設定正常（playwright+xcontents）", "changes": {}},
    "9831": {"issue": "設定正常。30件リンク検出済み", "changes": {}},
    "9835": {"issue": "設定正常。eIR外部リンクフォロー確認が必要", "changes": {}},
    "9887": {"issue": "status=skip（月次ページ未発見）", "changes": {}},
}


_gcs_client = None

def _client():
    global _gcs_client
    if _gcs_client is None:
        _gcs_client = _get_gcs_client()
    return _gcs_client

def gcs_read_json(blob_path: str) -> dict:
    """GCS から JSON を読み込む (blob_path: monthlydata/8011/adapter.json)"""
    bucket = _client().bucket(BUCKET_NAME)
    blob = bucket.blob(blob_path)
    if not blob.exists():
        raise FileNotFoundError(f"Not found: gs://{BUCKET_NAME}/{blob_path}")
    content = blob.download_as_text(encoding="utf-8")
    return json.loads(content)


def gcs_write_json(blob_path: str, data: dict) -> None:
    """JSON を GCS に書き込む"""
    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    bucket = _client().bucket(BUCKET_NAME)
    blob = bucket.blob(blob_path)
    blob.upload_from_string(json_str, content_type="application/json")


def apply_fixes():
    tickers_with_changes = {t: f for t, f in FIXES.items() if f["changes"]}
    tickers_no_change = {t: f for t, f in FIXES.items() if not f["changes"]}

    print(f"=== adapter.json 一括修正 ===")
    print(f"修正対象: {len(tickers_with_changes)} 件")
    print(f"変更不要: {len(tickers_no_change)} 件")
    print()

    success_count = 0
    skip_count = 0
    error_count = 0
    errors = []

    for ticker, fix_info in sorted(tickers_with_changes.items()):
        blob_path = f"monthlydata/{ticker}/adapter.json"
        try:
            # 現在のアダプターを取得
            adapter = gcs_read_json(blob_path)

            # 変更を適用
            for key, value in fix_info["changes"].items():
                adapter[key] = value

            # 書き戻し
            gcs_write_json(blob_path, adapter)

            print(f"[OK] {ticker}: {fix_info['issue']}")
            for k, v in fix_info["changes"].items():
                print(f"       {k} = {v!r}")
            success_count += 1

        except FileNotFoundError as e:
            print(f"[SKIP] {ticker}: adapter.json が GCS に存在しない")
            skip_count += 1
        except Exception as e:
            print(f"[ERROR] {ticker}: {e}")
            errors.append((ticker, str(e)))
            error_count += 1

    print()
    print(f"=== 結果 ===")
    print(f"成功: {success_count}")
    print(f"スキップ（ファイルなし）: {skip_count}")
    print(f"エラー: {error_count}")
    if errors:
        print("エラー詳細:")
        for t, e in errors:
            print(f"  {t}: {e}")

    print()
    print("変更不要 tickers:")
    for ticker, fix_info in sorted(tickers_no_change.items()):
        print(f"  {ticker}: {fix_info['issue']}")


if __name__ == "__main__":
    apply_fixes()

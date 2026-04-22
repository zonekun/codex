#!/usr/bin/env python3
"""
apply_batch_fixes_round2.py
第2ラウンド adapter.json 一括修正。
- EIR API カテゴリ修正
- type: eir_api → scrape_links 変換
- lhp/ltp パターン修正
- follow_links / playwright_required 追加
- ir_url 修正
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


FIXES = {
    # =====================================================================
    # A: EIR API カテゴリ修正
    # =====================================================================
    "6197": {
        "issue": "eir_category=None → announcement に修正。ltp 追加",
        "changes": {
            "eir_category": "announcement",
            "link_text_pattern": "月次|Monthly",
        },
    },
    "7453": {
        "issue": "eir_category=None → announcement に修正。ltp 追加",
        "changes": {
            "eir_category": "announcement",
            "link_text_pattern": "月次",
        },
    },
    "8704": {
        "issue": "eir_category=new_release は不正。announcement に修正",
        "changes": {
            "eir_category": "announcement",
        },
    },

    # =====================================================================
    # B: type=eir_api → scrape_links 変換（EIR にコンテンツなし）
    #    月次ページ URL 確認済みグループ
    # =====================================================================
    "3543": {
        "issue": "EIR にコンテンツなし。月次ページは JS レンダリング",
        "changes": {
            "type": "scrape_links",
            "playwright_required": True,
            "link_text_pattern": "月次",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "4666": {
        "issue": "EIR にコンテンツなし。月次ページは JS レンダリング",
        "changes": {
            "type": "scrape_links",
            "playwright_required": True,
            "link_text_pattern": "月次",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "7630": {
        "issue": "EIR にコンテンツなし。月次ページは JS レンダリング",
        "changes": {
            "type": "scrape_links",
            "playwright_required": True,
            "link_text_pattern": "月次",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "8153": {
        "issue": "EIR にコンテンツなし。scrape_links 化（ir_url は月次ページを指している）",
        "changes": {
            "type": "scrape_links",
            "playwright_required": True,
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "8167": {
        "issue": "EIR にコンテンツなし。scrape_links 化",
        "changes": {
            "type": "scrape_links",
            "playwright_required": True,
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "9024": {
        "issue": "EIR にコンテンツなし。月次ページは JS レンダリング",
        "changes": {
            "type": "scrape_links",
            "playwright_required": True,
            "link_text_pattern": "月次|データ",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "9517": {
        "issue": "EIR にコンテンツなし。scrape_links 化",
        "changes": {
            "type": "scrape_links",
            "playwright_required": True,
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "9850": {
        "issue": "EIR にコンテンツなし。月次ページを scrape_links 化",
        "changes": {
            "type": "scrape_links",
            "playwright_required": True,
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    # EIR なし + IR トップしか URL なし（深い調査要）
    "3034": {
        "issue": "EIR にコンテンツなし。scrape_links 化（月次 URL 要調査）",
        "changes": {
            "type": "scrape_links",
            "playwright_required": True,
            "link_text_pattern": "月次",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "3196": {
        "issue": "EIR にコンテンツなし。scrape_links 化",
        "changes": {
            "type": "scrape_links",
            "playwright_required": True,
            "link_text_pattern": "月次",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "3266": {
        "issue": "EIR にコンテンツなし。scrape_links 化",
        "changes": {
            "type": "scrape_links",
            "playwright_required": True,
            "link_text_pattern": "月次",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "7455": {
        "issue": "EIR にコンテンツなし。月次ページ URL あり。scrape_links 化",
        "changes": {
            "type": "scrape_links",
            "playwright_required": True,
            "link_text_pattern": "月次",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "7506": {
        "issue": "EIR にコンテンツなし。scrape_links 化",
        "changes": {
            "type": "scrape_links",
            "playwright_required": True,
            "link_text_pattern": "月次|売上",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "7599": {
        "issue": "EIR にコンテンツなし。scrape_links 化",
        "changes": {
            "type": "scrape_links",
            "playwright_required": True,
            "link_text_pattern": "月次",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },

    # =====================================================================
    # C: scrape_links ir_url 修正
    # =====================================================================
    "2163": {
        "issue": "ir_url が /ir/monthly/ でナビのみ → 実際のデータページ /ir/financial/monthly/ に変更",
        "changes": {
            "ir_page_url": "https://www.artner.co.jp/ir/financial/monthly/",
        },
    },
    "2686": {
        "issue": "ir_url が www → ir サブドメインに変更（www は 404）",
        "changes": {
            "ir_page_url": "https://ir.g-foot.co.jp/ja/finance/monthly.html",
        },
    },
    "3087": {
        "issue": "ir_url が IR トップ + lhp が固定ファイル名 → 月次ページに変更し lhp を汎用化",
        "changes": {
            "ir_page_url": "https://www.dnh.co.jp/ir/monthly/",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "link_text_pattern": "月次",
        },
    },

    # =====================================================================
    # D: lhp/ltp パターン修正
    # =====================================================================
    "2669": {
        "issue": "lhp='/ir/monthly.html.*.(pdf)' は一致しない → 汎用パターンに変更",
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "2770": {
        # 2770 があれば
        "issue": "パターン修正",
        "changes": {},
    },
    "2790": {
        "issue": "lhp='/ir/financial/monthly.html.*.(pdf)' は一致しない → 汎用化 + playwright_required",
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "playwright_required": True,
        },
    },
    "4177": {
        "issue": "lhp にハードコードされたファイルID → 汎用パターンに変更",
        "changes": {
            "link_href_pattern": r"/doc/4177/tdnet/.*\.pdf",
        },
    },
    "4679": {
        "issue": "lhp='/ir/library/monthly.html.*.(pdf)' は一致しない → 汎用化",
        "changes": {
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "7601": {
        "issue": "lhp が tsukiji*.pdf のみ → news_*.pdf も対象に拡張",
        "changes": {
            "link_href_pattern": r"/ir/pdf/index/\d{4}/.*\.pdf",
        },
    },
    "8173": {
        "issue": "lhp='Joshin Monthly' は regex として意味不明 → URL パスパターンに修正",
        "changes": {
            "link_href_pattern": r"/ir/library/sales_report/.*|Joshin_Monthly.*\.pdf",
        },
    },
    "8276": {
        "issue": "lhp が固定ファイル名 + ltp が特殊テキスト → 月次ページに ltp/lhp を汎用化",
        "changes": {
            "link_text_pattern": "月次|売上",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "9005": {
        "issue": "lhp パターンが現在のURL構造に合わない → 汎用化",
        "changes": {
            "link_href_pattern": r".*monthly.*\.(pdf|xlsx|xls)|.*月次.*\.(pdf|xlsx|xls)",
        },
    },

    # =====================================================================
    # E: follow_links 追加（月次ページ → 子ページにPDF）
    # =====================================================================
    "2503": {
        "issue": "月次リスト → サブページに PDF → follow_links=True",
        "changes": {
            "follow_links": True,
        },
    },
    "2674": {
        "issue": "月次売上ページ → サブページにPDF → follow_links=True",
        "changes": {
            "follow_links": True,
        },
    },
    "2769": {
        "issue": "ltp/lhp 未設定 + follow_links 追加",
        "changes": {
            "link_text_pattern": "月次",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
            "follow_links": True,
        },
    },
    "3069": {
        "issue": "月次ページ → サブページ → follow_links=True + lhp 汎用化",
        "changes": {
            "follow_links": True,
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "3080": {
        "issue": "月次情報ページ → 年次サブページ → follow_links=True",
        "changes": {
            "follow_links": True,
        },
    },
    "3094": {
        "issue": "月次ページ JS レンダリング → playwright_required",
        "changes": {
            "playwright_required": True,
            "link_text_pattern": "月次",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "3148": {
        "issue": "月次ページ → tabid サブページ → follow_links=True + lhp 汎用化",
        "changes": {
            "follow_links": True,
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "3222": {
        "issue": "月次ページ → 年次サブページ → follow_links=True",
        "changes": {
            "follow_links": True,
        },
    },

    # =====================================================================
    # F: playwright_required 追加（確実にJSレンダリングが必要）
    # =====================================================================
    "2664": {
        "issue": "月次ページは JS レンダリング → playwright_required=True",
        "changes": {
            "playwright_required": True,
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "3028": {
        "issue": "月次ページは SPA → playwright_required=True",
        "changes": {
            "playwright_required": True,
            "link_text_pattern": "月次",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "4680": {
        "issue": "月次ページは JS レンダリング → playwright_required=True",
        "changes": {
            "playwright_required": True,
            "link_text_pattern": "月次",
            "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        },
    },
    "6571": {
        "issue": "月次ページは JS レンダリング → playwright_required=True",
        "changes": {
            "playwright_required": True,
        },
    },
}


def apply_fixes():
    tickers_with_changes = {t: f for t, f in FIXES.items() if f["changes"]}

    print(f"=== apply_batch_fixes_round2.py ===")
    print(f"修正対象: {len(tickers_with_changes)} 件")
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
            print(f"[OK] {ticker} ({adapter.get('company_name','?')}): {fix_info['issue']}")
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
    print(f"=== 結果 ===")
    print(f"成功: {success} / スキップ: {skip} / エラー: {len(errors)}")
    if errors:
        for t, e in errors:
            print(f"  {t}: {e}")


if __name__ == "__main__":
    apply_fixes()

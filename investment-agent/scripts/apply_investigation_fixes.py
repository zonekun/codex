#!/usr/bin/env python3
"""
apply_investigation_fixes.py
investigate_adapters.py の結果を元に adapter.json を GCS に書き戻す。
自動推定分10件 + 手動調整分を適用。
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


# 修正定義（investigate_adapters.py 結果 + 手動調整）
FIXES = {
    # --- 自動推定で ltp 追加 ---
    "1911": {
        "issue": "link_text_pattern 未設定（受注速報リンク検出）",
        "changes": {"link_text_pattern": "受注|速報|月次"},
    },
    "2429": {
        "issue": "link_text_pattern 未設定（月次リンク検出）",
        "changes": {"link_text_pattern": "月次"},
    },
    "2659": {
        "issue": "link_href_pattern 未設定（PDFリンク検出）。URLパターンから推定",
        "changes": {"link_href_pattern": r"/common/uploads/202.*\.pdf"},
    },
    "2702": {
        "issue": "link_text/href_pattern 未設定",
        "changes": {
            "link_text_pattern": "月次",
            "link_href_pattern": r".*chapter.*\.pdf",
        },
    },
    "2998": {
        "issue": "link_href_pattern 未設定（xcontents PDFリンク検出）",
        "changes": {"link_href_pattern": r"/xcontents/AS08738/.*\.pdf"},
    },
    "3083": {
        "issue": "lhp が電子公告PDFを指している（誤設定）→ 月次テキストリンクパターンに修正 + follow_links",
        "changes": {
            "link_text_pattern": "月次|速報",
            "link_href_pattern": None,  # 削除
            "follow_links": True,
        },
    },
    "3548": {
        "issue": "link_text_pattern 未設定（月次売上速報リンク検出）",
        "changes": {"link_text_pattern": "月次|売上速報|速報"},
    },
    "3561": {
        "issue": "link_text_pattern 未設定（月次リンク検出）",
        "changes": {"link_text_pattern": "月次"},
    },
    "7561": {
        "issue": "link_text_pattern 未設定 + follow_links で月次報告ページへ誘導",
        "changes": {
            "link_text_pattern": "月次",
            "follow_links": True,
        },
    },
    "9279": {
        "issue": "link_text_pattern 未設定（月次情報リンク検出）",
        "changes": {"link_text_pattern": "月次"},
    },
}


def apply_fixes():
    print("=== apply_investigation_fixes.py ===")
    print(f"適用件数: {len(FIXES)} 件")
    print()

    success = 0
    skip = 0
    errors = []

    for ticker, fix_info in sorted(FIXES.items()):
        blob_path = f"monthlydata/{ticker}/adapter.json"
        try:
            adapter = gcs_read_json(blob_path)
            changes = fix_info["changes"]

            for key, value in changes.items():
                if value is None:
                    # None = フィールド削除
                    adapter.pop(key, None)
                    print(f"  [{ticker}] {key} を削除")
                else:
                    adapter[key] = value

            gcs_write_json(blob_path, adapter)
            print(f"[OK] {ticker} ({adapter.get('company_name','?')}): {fix_info['issue']}")
            for k, v in changes.items():
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
    print(f"成功: {success}")
    print(f"スキップ: {skip}")
    print(f"エラー: {len(errors)}")
    if errors:
        for t, e in errors:
            print(f"  {t}: {e}")


if __name__ == "__main__":
    apply_fixes()

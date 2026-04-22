"""
4社のextract_adapter.jsonを修正してGCSにアップロード。

2305 スタジオアリス:
  - 各月売上高テーブルから {month_num}月度 行のデータを抽出
  - 撮影件数・お買上単価は散文部分から抽出

4177 i-plug:
  - OCRスペース込みパターン + 旧フォーマット(regex/group)で抽出
  - 早期定額型/成功報酬型 の月次受注高: group=1で2番目の「百万円」前の値（当期値）を取得

7185 ヒロセ通商:
  - {year}年セクションの各指標: use_last_number=True で最終月の値を取得

7674 NATTY SWANKY (ダンダダン):
  - 散文: 「全店売上高の前年比はX%」パターン

実行:
  PYTHONUTF8=1 uv run python scripts/fix_adapters_2305_4177_7185_7674.py
"""
import json
import datetime
from google.oauth2 import service_account
from google.cloud import storage

KEY_FILE = r"G:\マイドライブ\claude\investment-agent\keys\gcp-service-account.json"
BUCKET = "stock_data_1930932"

creds = service_account.Credentials.from_service_account_file(KEY_FILE)
gcs = storage.Client(credentials=creds, project="gmailpj-357912")
bucket = gcs.bucket(BUCKET)
now = datetime.date.today().isoformat()

ADAPTERS = {
    "2305": {
        "company_name": "株式会社スタジオアリス",
        "source": "tdnet",
        "month_from_title_regex": "(?P<month>\\d{1,2})月[度の]",
        "fields": [
            {
                "key": "monthly_sales_million_yen",
                "regex": "各月売上高[\\s\\S]*?{month_num}月度\\s+([\\d,]+)\\s+[\\d,]+\\s+[\\d.]+%",
                "group": 1,
                "value_type": "integer",
                "note": "個別月売上高(A) - 前年同月比表の当月行"
            },
            {
                "key": "customer_count_yoy_pct",
                "regex": "全店[\\s\\S]*?撮影件数[（(][^）)]*[）)]\\s*[：:]\\s*([\\d.]+)%",
                "group": 1,
                "value_type": "float",
                "note": "全店撮影件数(客数)の前年比"
            },
            {
                "key": "avg_spend_yoy_pct",
                "regex": "全店[\\s\\S]*?お買上単価[：:]\\s*([\\d.]+)%",
                "group": 1,
                "value_type": "float",
                "note": "全店お買上単価の前年比"
            }
        ],
        "updated_at": now,
        "note": "fix: {month_num}プレースホルダーで当月行を特定 (2026-03-24)"
    },

    "4177": {
        "company_name": "株式会社i-plug",
        "source": "tdnet",
        "month_from_title_regex": "(?P<month>\\d{1,2})月",
        "fields": [
            {
                "key": "early_fixed_monthly_million_yen",
                "regex": "早\\s*期\\s*定\\s*額\\s*型[^百]*百\\s*万\\s*円\\s*([\\d,]+)\\s*百\\s*万\\s*円",
                "group": 1,
                "value_type": "integer",
                "note": "早期定額型（単月）当期値 - 前年値を skip して当期値をキャプチャ"
            },
            {
                "key": "success_fee_monthly_million_yen",
                "regex": "成\\s*功\\s*報\\s*酬\\s*型[^百]*百\\s*万\\s*円\\s*([\\d,]+)\\s*百\\s*万\\s*円",
                "group": 1,
                "value_type": "integer",
                "note": "成功報酬型（単月）当期値"
            },
            {
                "key": "company_registrations_cumulative",
                "regex": "企\\s*業\\s*登\\s*録\\s*数[\\s\\S]*?当\\s*月\\s*[：:]\\s*([\\d,]+)\\s*社",
                "group": 1,
                "value_type": "integer",
                "note": "企業登録数（累積）当月"
            }
        ],
        "updated_at": now,
        "note": "fix: OCRスペース対応パターン + 旧フォーマット(regex/group) (2026-03-24)"
    },

    "7185": {
        "company_name": "ヒロセ通商株式会社",
        "source": "tdnet",
        "month_from_title_regex": "(?P<month>\\d{1,2})月",
        "fields": [
            {
                "key": "consolidated_operating_revenue_million_yen",
                "regex": "{year}年[\\s\\S]*?営業収益\\s*\\([^)]*\\)\\s*([\\d,\\s]+)",
                "group": 1,
                "use_last_number": True,
                "value_type": "integer",
                "note": "連結営業収益(百万円) - 当月が最終値"
            },
            {
                "key": "consolidated_customer_accounts",
                "regex": "{year}年[\\s\\S]*?顧客口座数\\s*\\([^)]*\\)\\s*([\\d,\\s]+)",
                "group": 1,
                "use_last_number": True,
                "value_type": "integer",
                "note": "連結顧客口座数(口座) - 当月が最終値"
            },
            {
                "key": "consolidated_fx_volume_million_currency",
                "regex": "{year}年[\\s\\S]*?外国為替取引高\\s*\\([^)]*\\)\\s*([\\d,\\s]+)",
                "group": 1,
                "use_last_number": True,
                "value_type": "integer",
                "note": "連結外国為替取引高(百万通貨) - 当月が最終値"
            },
            {
                "key": "consolidated_customer_margin_million_yen",
                "regex": "{year}年[\\s\\S]*?顧客預り証拠金\\s*\\([^)]*\\)\\s*([\\d,\\s]+)",
                "group": 1,
                "use_last_number": True,
                "value_type": "integer",
                "note": "連結顧客預り証拠金(百万円) - 当月が最終値"
            }
        ],
        "updated_at": now,
        "note": "fix: {year}プレースホルダー + use_last_number=True で当月値抽出 (2026-03-24)"
    },

    "7674": {
        "company_name": "株式会社NATTY SWANKYホールディングス",
        "source": "tdnet",
        "month_from_title_regex": "(?P<month>\\d{1,2})月",
        "fields": [
            {
                "key": "all_store_sales_yoy_pct",
                "regex": "全店売上高の前年比は([\\d.]+)%",
                "group": 1,
                "value_type": "float",
                "note": "全店売上高前年比 - 月次コメント散文から抽出"
            },
            {
                "key": "existing_store_sales_yoy_pct",
                "regex": "既存店売上高の前年比は([\\d.]+)%",
                "group": 1,
                "value_type": "float",
                "note": "既存店売上高前年比 - 月次コメント散文から抽出"
            }
        ],
        "updated_at": now,
        "note": "fix: 散文パターン（全店/既存店前年比） (2026-03-24)"
    },
}

print("=== adapter fixes: 2305, 4177, 7185, 7674 ===")
ok = 0
errors = 0
for ticker, adapter in ADAPTERS.items():
    path = f"monthlydata/{ticker}/extract_adapter.json"
    blob = bucket.blob(path)
    try:
        blob.upload_from_string(
            json.dumps(adapter, ensure_ascii=False, indent=2),
            content_type="application/json",
        )
        print(f"  [{ticker}] {adapter['company_name']} → uploaded ✅")
        ok += 1
    except Exception as e:
        print(f"  [{ticker}] ERROR: {e}")
        errors += 1

print(f"\n完了: {ok}件アップロード, {errors}件エラー")

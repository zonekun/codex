"""
2305 スタジオアリスのアダプター修正:
  {month_num}月度 → (?<!\d){month_num}月度 にして
  1月度が11月度に誤マッチしないようにする

実行:
  PYTHONUTF8=1 uv run python scripts/fix_adapter_2305_lookbehind.py
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

adapter = {
    "company_name": "株式会社スタジオアリス",
    "source": "tdnet",
    "month_from_title_regex": "(?P<month>\\d{1,2})月[度の]",
    "fields": [
        {
            "key": "monthly_sales_million_yen",
            "regex": "各月売上高[\\s\\S]*?(?<!\\d){month_num}月度\\s+([\\d,]+)\\s+[\\d,]+\\s+[\\d.]+%",
            "group": 1,
            "value_type": "integer",
            "note": "個別月売上高(A) - (?<!\\d)で11月度への誤マッチを防止"
        },
        {
            "key": "customer_count_yoy_pct",
            "regex": "撮影件数[（(][^）)]*[）)]\\s*[：:]\\s*([\\d.]+)%",
            "group": 1,
            "value_type": "float",
            "note": "全店撮影件数(客数)の前年比"
        },
        {
            "key": "avg_spend_yoy_pct",
            "regex": "お買上単価[：:]\\s*([\\d.]+)%",
            "group": 1,
            "value_type": "float",
            "note": "全店お買上単価の前年比"
        }
    ],
    "updated_at": now,
    "note": "fix: (?<!\\d){month_num}で11月度への誤マッチ防止 (2026-03-24)"
}

path = "monthlydata/2305/extract_adapter.json"
blob = bucket.blob(path)
blob.upload_from_string(
    json.dumps(adapter, ensure_ascii=False, indent=2),
    content_type="application/json",
)
print(f"✅ 2305 アダプター更新完了: {path}")
print(json.dumps(adapter, ensure_ascii=False, indent=2))

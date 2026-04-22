"""
Round8 adapter fixes (2026-03-24) based on local Chrome test results.

Changes:
  1. playwright_required=False: 1878, 7475, 8218, 8725, 8848
     - requests finds PDF links; Playwright finds same or fewer
  2. link_text_pattern=null: 6571
     - ltp='月次' was filtering out valid TDNET PDF links (xj-storage.jp)
  3. eir_api → scrape_links: 7203, 9828, 9904
     - eIR API finds no monthly announcements; correct IR page exists
  4. URL fix: 3266 (domain changed to fcg-r.co.jp)
  5. URL update: 3034 (monthly page path changed), 7599 (financial_data/)

実行:
  PYTHONUTF8=1 uv run python scripts/apply_batch_fixes_round8.py
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
changed = 0
errors = 0


def patch_adapter(ticker: str, patches: dict) -> bool:
    """GCS から adapter.json を読み込み、patches を適用して書き戻す。"""
    global changed, errors
    blob = bucket.blob(f"monthlydata/{ticker}/adapter.json")
    try:
        data = json.loads(blob.download_as_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [{ticker}] 読み込み失敗: {e}")
        errors += 1
        return False

    old_vals = {k: data.get(k) for k in patches}
    data.update(patches)
    data["updated_at"] = now

    try:
        blob.upload_from_string(
            json.dumps(data, ensure_ascii=False, indent=2),
            content_type="application/json",
        )
    except Exception as e:
        print(f"  [{ticker}] 書き込み失敗: {e}")
        errors += 1
        return False

    for k, new_v in patches.items():
        print(f"  [{ticker}] {k}: {old_vals[k]!r} → {new_v!r}")
    changed += 1
    return True


print("=== Round8 adapter fixes ===")
print()

# ------------------------------------------------------------------
# 1. playwright_required=False
#    (requests finds links; Playwright adds nothing or fails)
# ------------------------------------------------------------------
print("--- 1. playwright_required=False ---")
for ticker, reason in [
    ("1878", "requests=2件, Playwright=0件"),
    ("7475", "requests=12件, Playwright=12件 → requests で十分"),
    ("8218", "requests=11件, Playwright=11件 → requests で十分"),
    ("8725", "requests=10件, Playwright=0件"),
    ("8848", "requests=1件, Playwright=1件 → requests で十分"),
]:
    print(f"  {ticker}: {reason}")
    patch_adapter(ticker, {"playwright_required": False})

print()

# ------------------------------------------------------------------
# 2. link_text_pattern=null: 6571 キュービーネット
#    ltp='月次' was filtering out xj-storage.jp TDNET PDF links
#    (Playwright finds 8 links locally but ltp caused Cloud Run failures)
# ------------------------------------------------------------------
print("--- 2. link_text_pattern=null ---")
print("  6571: ltp='月次' → xj-storage.jp リンクにテキスト「月次」なし → 除去")
patch_adapter("6571", {"link_text_pattern": None})

print()

# ------------------------------------------------------------------
# 3. eir_api → scrape_links: 7203, 9828, 9904
#    eIR に月次アナウンスメントなし → 直接IRページをスクレイプ
# ------------------------------------------------------------------
print("--- 3. eir_api → scrape_links ---")
eir_fixes = {
    "7203": {
        "type": "scrape_links",
        "playwright_required": True,
        "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        "link_text_pattern": None,
        "note": "eIR月次アナウンスなし → scrape_links (2026-03-24 Round8)",
    },
    "9828": {
        "type": "scrape_links",
        "playwright_required": True,
        "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        "link_text_pattern": None,
        "note": "eIR月次アナウンスなし → scrape_links (2026-03-24 Round8)",
    },
    "9904": {
        "type": "scrape_links",
        "playwright_required": True,
        "link_href_pattern": r".*\.(pdf|xlsx|xls)",
        "link_text_pattern": None,
        "note": "eIR月次アナウンスなし → scrape_links (2026-03-24 Round8)",
    },
}
for ticker, patches in eir_fixes.items():
    print(f"  {ticker}: eir_api → scrape_links")
    patch_adapter(ticker, patches)

print()

# ------------------------------------------------------------------
# 4. URL fix: 3266 ファンドクリエーション
#    旧 fc-group.co.jp → 新 fcg-r.co.jp (requests 200 確認済み)
# ------------------------------------------------------------------
print("--- 4. URL fix: 3266 ---")
patch_adapter("3266", {
    "ir_page_url": "https://www.fcg-r.co.jp/ir/financial/monthly/",
    "playwright_required": True,
    "note": "旧 fc-group.co.jp が 404 → fcg-r.co.jp に変更 (2026-03-24 Round8)",
})

print()

# ------------------------------------------------------------------
# 5. URL update: 3034 クオールHD, 7599 IDOM
#    旧 URL が 404 → IR トップから月次ページへの follow_links=True に変更
# ------------------------------------------------------------------
print("--- 5. URL update: 3034, 7599 ---")
patch_adapter("3034", {
    "ir_page_url": "https://www.qolhd.co.jp/ir/finance.html",
    "follow_links": True,
    "playwright_required": True,
    "note": "旧 /ir/library/monthly/ が 404 → /ir/finance.html + follow_links (Round8)",
})
patch_adapter("7599", {
    "ir_page_url": "https://idom-inc.com/ir/financial_data/",
    "follow_links": True,
    "playwright_required": True,
    "note": "旧 /ir/financial/monthly/ が 404 → /ir/financial_data/ + follow_links (Round8)",
})

print()
print(f"=== 完了: {changed}件変更, {errors}件エラー ===")

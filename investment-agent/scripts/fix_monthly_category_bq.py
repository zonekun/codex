"""BQ TDNET_DOCUMENTS_ENHANCED の月次開示カテゴリを一括修正する.

DOC_TITLE が月次開示を示すパターンにマッチするが MAIN_CATEGORY が '月次開示' でない
レコードを UPDATE で修正する（PDF 再処理不要）。

Usage:
    PYTHONUTF8=1 uv run python scripts/fix_monthly_category_bq.py [--dry-run]
"""

import sys
import urllib3
import requests as _req
from requests.adapters import HTTPAdapter as _HA

urllib3.disable_warnings()
class _NoVerify(_HA):
    def send(self, req, **kw): kw["verify"] = False; return super().send(req, **kw)
_orig = _req.Session.__init__
def _p(self, *a, **kw): _orig(self, *a, **kw); self.mount("https://", _NoVerify()); self.verify = False
_req.Session.__init__ = _p

from google.cloud import bigquery
from google.oauth2 import service_account

KEY_PATH = "keys/gcp-service-account.json"
PROJECT  = "gmailpj-357912"
TABLE_ID = f"{PROJECT}.STOCK.TDNET_DOCUMENTS_ENHANCED"
DRY_RUN  = "--dry-run" in sys.argv

creds  = service_account.Credentials.from_service_account_file(KEY_PATH)
client = bigquery.Client(project=PROJECT, credentials=creds)

# ────────────────────────────────────────────────────────────────
# 月次開示を示す拡張パターン（BQ 用正規表現）
# 現行: 月次|月度売上|売上速報|売上推移速報|月度業績|受注速報
# 追加:
#   売上高速報   … 「月度売上高速報」「売上高速報」
#   月度連結     … 「月度連結営業概況」（イオン系）
#   月度販売     … 「月度販売状況」
#   月次売上     … 「月次売上高前年比速報」
#   月次業績     … 「月次業績速報」
#   月次報告     … 「月次報告」
#   月次データ   … 「月次データ」
#   月次速報     … 「月次速報」
# ────────────────────────────────────────────────────────────────
MONTHLY_PATTERN_BQ = (
    r"月次|月度売上|売上速報|売上高速報|売上推移速報|月度業績|受注速報"
    r"|月度連結|月度販売|月次売上|月次業績|月次報告|月次データ|月次速報"
)

# ── Step1: 修正対象の件数確認 ─────────────────────────────────────
count_query = f"""
SELECT COUNT(*) AS cnt
FROM `{TABLE_ID}`
WHERE MAIN_CATEGORY != '月次開示'
  AND NOT EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) sc WHERE sc = '月次開示')
  AND REGEXP_CONTAINS(DOC_TITLE, r'{MONTHLY_PATTERN_BQ}')
"""
print("修正対象件数を確認中...")
cnt = list(client.query(count_query).result())[0].cnt
print(f"  対象: {cnt} 件")

if cnt == 0:
    print("修正対象なし。終了します。")
    sys.exit(0)

# ── Step2: 修正前のカテゴリ分布確認 ──────────────────────────────────
dist_query = f"""
SELECT MAIN_CATEGORY, COUNT(*) AS cnt
FROM `{TABLE_ID}`
WHERE MAIN_CATEGORY != '月次開示'
  AND NOT EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) sc WHERE sc = '月次開示')
  AND REGEXP_CONTAINS(DOC_TITLE, r'{MONTHLY_PATTERN_BQ}')
GROUP BY MAIN_CATEGORY
ORDER BY cnt DESC
"""
print("\n修正前カテゴリ分布:")
for row in client.query(dist_query).result():
    print(f"  {row.cnt:5d}  {row.MAIN_CATEGORY}")

if DRY_RUN:
    print("\n[DRY RUN] 実際の UPDATE は実行しません。")
    sys.exit(0)

# ── Step3: BQ UPDATE 実行 ─────────────────────────────────────────
update_query = f"""
UPDATE `{TABLE_ID}`
SET MAIN_CATEGORY = '月次開示'
WHERE MAIN_CATEGORY != '月次開示'
  AND NOT EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) sc WHERE sc = '月次開示')
  AND REGEXP_CONTAINS(DOC_TITLE, r'{MONTHLY_PATTERN_BQ}')
"""
print(f"\nBQ UPDATE 実行中（{cnt} 件）...")
job = client.query(update_query)
job.result()
print(f"  完了。更新行数: {job.num_dml_affected_rows} 件")

# ── Step4: 修正後の確認 ───────────────────────────────────────────
verify_query = f"""
SELECT COUNT(*) AS remaining
FROM `{TABLE_ID}`
WHERE MAIN_CATEGORY != '月次開示'
  AND NOT EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) sc WHERE sc = '月次開示')
  AND REGEXP_CONTAINS(DOC_TITLE, r'{MONTHLY_PATTERN_BQ}')
"""
remaining = list(client.query(verify_query).result())[0].remaining
print(f"  修正後の残件数: {remaining} 件")

# ── Step5: 月次開示として認識されたティッカー数を再集計 ──────────────
new_count_query = f"""
SELECT COUNT(DISTINCT TICKER) AS ticker_cnt
FROM `{TABLE_ID}`
WHERE SUBMISSION_DATE >= '2025-03-14'
  AND (
    MAIN_CATEGORY = '月次開示'
    OR EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) sc WHERE sc = '月次開示')
  )
"""
new_cnt = list(client.query(new_count_query).result())[0].ticker_cnt
print(f"\n修正後の月次開示ティッカー数（直近12ヶ月）: {new_cnt} 社（修正前: 167社）")

"""D カテゴリ（GCS PDF なし・109社）の原因を詳細分類する.

分類:
  D1: BQ に（他カテゴリで）レコードあり → TDnet 使用者だが月次ダウンロード漏れ（真のD）
  D2: BQ にレコードなし              → TDnet 非使用 or 取り込み期間外 → type c 候補
  c : monthly_disclosure_master に c 登録済み → 独自 IR 開示確定

Usage:
    PYTHONUTF8=1 uv run python scripts/investigate_d_category.py
"""

import csv
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

KEY_PATH  = "keys/gcp-service-account.json"
PROJECT   = "gmailpj-357912"
TABLE_ID  = f"{PROJECT}.STOCK.TDNET_DOCUMENTS_ENHANCED"
GAPS_CSV  = "data/monthly_coverage_gaps.csv"
MASTER_CSV = "data/monthly_disclosure_master.csv"
OUT_CSV   = "data/d_category_detail.csv"

creds  = service_account.Credentials.from_service_account_file(KEY_PATH)
client = bigquery.Client(project=PROJECT, credentials=creds)

# ── Step1: D カテゴリ109社リスト ─────────────────────────────────────
d_companies: dict[str, str] = {}
with open(GAPS_CSV, encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        if row["GAP_TYPE"] == "D":
            d_companies[row["TICKER"]] = row["COMPANY_NAME"]

print(f"D カテゴリ: {len(d_companies)} 社")

# ── Step2: monthly_disclosure_master で c 登録済みか確認 ──────────────
master_c: set[str] = set()
try:
    with open(MASTER_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("DISCLOSURE_TYPE") == "c":
                master_c.add(row["TICKER"])
except FileNotFoundError:
    pass
print(f"master に c 登録済み: {len(master_c & set(d_companies))} 社")

# ── Step3: BQ で全期間での存在確認（TDnet 使用有無の判定）──────────────
ticker_list = ", ".join(f"'{t}'" for t in d_companies)
bq_any_query = f"""
SELECT DISTINCT TICKER
FROM `{TABLE_ID}`
WHERE TICKER IN ({ticker_list})
"""
print("BQ 全期間チェック中（TDnet使用有無）...")
bq_any_tickers = {row.TICKER for row in client.query(bq_any_query).result()}
print(f"  BQ に何らかのレコードあり: {len(bq_any_tickers)} 社")
print(f"  BQ に一切なし（TDnet非使用候補）: {len(d_companies) - len(bq_any_tickers)} 社")

# ── Step4: BQ ありの場合、どんなカテゴリを持っているか確認 ───────────────
if bq_any_tickers:
    bq_cat_query = f"""
    SELECT TICKER, MAIN_CATEGORY, COUNT(*) AS cnt
    FROM `{TABLE_ID}`
    WHERE TICKER IN ({', '.join(f"'{t}'" for t in bq_any_tickers)})
    GROUP BY TICKER, MAIN_CATEGORY
    ORDER BY TICKER, cnt DESC
    """
    bq_cats: dict[str, list[str]] = {}
    for row in client.query(bq_cat_query).result():
        if row.TICKER not in bq_cats:
            bq_cats[row.TICKER] = []
        bq_cats[row.TICKER].append(f"{row.MAIN_CATEGORY}({row.cnt})")
else:
    bq_cats = {}

# ── Step5: 分類・出力 ─────────────────────────────────────────────
results = []
d1_list, d2_list, c_list = [], [], []

for ticker, name in sorted(d_companies.items()):
    in_master_c = ticker in master_c
    in_bq       = ticker in bq_any_tickers
    cats        = ", ".join(bq_cats.get(ticker, []))

    if in_master_c:
        sub_type = "c"
        detail   = f"master登録済み（独自IR開示）"
        c_list.append((ticker, name, detail))
    elif in_bq:
        sub_type = "D1"
        detail   = f"TDnet使用（他カテゴリあり）: {cats}"
        d1_list.append((ticker, name, detail))
    else:
        sub_type = "D2"
        detail   = "BQ・GCSともに未登録（TDnet非使用候補・type c 要確認）"
        d2_list.append((ticker, name, detail))

    results.append((ticker, name, sub_type, detail))

# CSV 出力
with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["TICKER", "COMPANY_NAME", "SUB_TYPE", "DETAIL"])
    for row in results:
        w.writerow(row)

# ── Step6: サマリー表示 ───────────────────────────────────────────
print(f"\n=== D カテゴリ詳細分類 ===")
print(f"  c  : master登録済み（独自IR確定）  : {len(c_list):3d} 社")
print(f"  D1 : TDnet使用・月次ダウンロード漏れ: {len(d1_list):3d} 社")
print(f"  D2 : TDnet非使用候補（type c 要確認）: {len(d2_list):3d} 社")
print(f"\n出力: {OUT_CSV}")

print(f"\n--- D1（TDnet使用者・ダウンロード漏れ）先頭20件 ---")
for t, n, d in d1_list[:20]:
    print(f"  {t} {n}: {d}")

print(f"\n--- D2（TDnet非使用候補）先頭20件 ---")
for t, n, d in d2_list[:20]:
    print(f"  {t} {n}")

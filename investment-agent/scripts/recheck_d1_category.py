"""D1 カテゴリ（91社）のダウンロード・ETL 再チェック.

各社について:
  - GCS に最近の PDF が存在するか（ダウンロード済みか）
  - BQ に最近のレコードが存在するか（ETL 済みか）
  - GCS に PDF があるが BQ 未登録 → ETL 漏れ
  - GCS にも PDF なし → ダウンロード漏れ（真のD）
  - BQ にレコードあるが月次なし → カテゴリ分類漏れ（B相当）

Usage:
    PYTHONUTF8=1 uv run python scripts/recheck_d1_category.py

出力:
    data/d1_recheck.csv
"""

import csv
import re
import urllib3
import requests as _req
from requests.adapters import HTTPAdapter as _HA
from collections import defaultdict

urllib3.disable_warnings()
class _NoVerify(_HA):
    def send(self, req, **kw): kw["verify"] = False; return super().send(req, **kw)
_orig = _req.Session.__init__
def _p(self, *a, **kw): _orig(self, *a, **kw); self.mount("https://", _NoVerify()); self.verify = False
_req.Session.__init__ = _p

from google.cloud import bigquery, storage
from google.oauth2 import service_account

KEY_PATH  = "keys/gcp-service-account.json"
PROJECT   = "gmailpj-357912"
TABLE_ID  = f"{PROJECT}.STOCK.TDNET_DOCUMENTS_ENHANCED"
BUCKET    = "stock_data_1930932"
DETAIL_CSV = "data/d_category_detail.csv"
OUT_CSV   = "data/d1_recheck.csv"
CUTOFF    = "2025-03-14"

creds  = service_account.Credentials.from_service_account_file(KEY_PATH)
bq     = bigquery.Client(project=PROJECT, credentials=creds)
gcs    = storage.Client(project=PROJECT, credentials=creds)
bucket = gcs.bucket(BUCKET)

MONTHLY_PAT = re.compile(
    r"月次|月度売上|売上速報|売上高速報|売上推移速報|月度業績|受注速報"
    r"|月度連結|月度販売|月次売上|月次業績|月次報告|月次データ|月次速報"
)

# ── D1 銘柄リスト ─────────────────────────────────────────────────
d1: dict[str, str] = {}
with open(DETAIL_CSV, encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        if row["SUB_TYPE"] == "D1":
            d1[row["TICKER"]] = row["COMPANY_NAME"]
print(f"D1: {len(d1)} 社")

# ── BQ チェック: 直近12ヶ月の全レコード（カテゴリ問わず）────────────────
ticker_list = ", ".join(f"'{t}'" for t in d1)
bq_recent_query = f"""
SELECT
    TICKER,
    MAX(SUBMISSION_DATE) AS latest_date,
    COUNTIF(MAIN_CATEGORY = '月次開示'
            OR EXISTS(SELECT 1 FROM UNNEST(SUB_CATEGORIES) sc WHERE sc='月次開示')
           ) AS monthly_cnt,
    COUNT(*) AS total_cnt,
    STRING_AGG(DISTINCT MAIN_CATEGORY, ', ' ORDER BY MAIN_CATEGORY LIMIT 5) AS categories
FROM `{TABLE_ID}`
WHERE TICKER IN ({ticker_list})
  AND SUBMISSION_DATE >= '{CUTOFF}'
GROUP BY TICKER
"""
print("BQ 直近12ヶ月チェック中...")
bq_recent: dict[str, dict] = {}
for row in bq.query(bq_recent_query).result():
    bq_recent[row.TICKER] = {
        "latest_date":  str(row.latest_date),
        "monthly_cnt":  row.monthly_cnt,
        "total_cnt":    row.total_cnt,
        "categories":   row.categories,
    }
print(f"  直近12ヶ月に何らかのレコードあり: {len(bq_recent)} 社")

# ── GCS チェック: TICKER別プレフィックスで直近12ヶ月の全PDF（月次キーワード有無で分類）──
# 実際のGCSパス構造: tdnet/{TICKER}/{YYYYMMDD}_{TICKER}_{会社名}_{カテゴリ}_{タイトル}_xxx.pdf
print("GCS チェック中（tdnet/{TICKER}/ プレフィックス）...")
gcs_monthly:   dict[str, list[str]] = defaultdict(list)   # ticker → 月次キーワードあり blob
gcs_any:       dict[str, list[str]] = defaultdict(list)   # ticker → 任意 blob
ticker_set = set(d1.keys())

for ticker in ticker_set:
    prefix = f"tdnet/{ticker}/"
    for blob in bucket.list_blobs(prefix=prefix):
        if not blob.name.lower().endswith(".pdf"):
            continue
        fname = blob.name.split("/")[-1]
        parts = fname.split("_")
        if len(parts) < 2:
            continue
        # ファイル名先頭はYYYYMMDD形式 → 直近12ヶ月（2025-03-14以降）でフィルタ
        date_str = parts[0]
        if date_str < CUTOFF.replace("-", ""):
            continue
        gcs_any[ticker].append(blob.name)
        category = parts[3] if len(parts) > 3 else ""
        title    = parts[4] if len(parts) > 4 else ""
        if MONTHLY_PAT.search(title) or MONTHLY_PAT.search(category) or category == "月次開示":
            gcs_monthly[ticker].append(blob.name)

print(f"  GCS 月次PDF あり: {len(gcs_monthly)} 社")
print(f"  GCS 任意PDF あり: {len(gcs_any)} 社")

# ── 分類 ──────────────────────────────────────────────────────────
results = []
counts = {"ETL漏れ_月次": 0, "ETL漏れ_未分類": 0, "DL漏れ": 0, "カテゴリ分類漏れB相当": 0}

for ticker, name in sorted(d1.items()):
    has_gcs_monthly = ticker in gcs_monthly
    has_gcs_any     = ticker in gcs_any
    has_bq_recent   = ticker in bq_recent
    bq_info = bq_recent.get(ticker, {})

    if has_gcs_monthly and not has_bq_recent:
        status = "ETL漏れ_月次"
        detail = f"GCS月次PDF {len(gcs_monthly[ticker])}件あり・BQ未ロード"
        counts["ETL漏れ_月次"] += 1
    elif has_gcs_any and not has_bq_recent:
        status = "ETL漏れ_未分類"
        detail = f"GCS任意PDF {len(gcs_any[ticker])}件あり・BQ未ロード"
        counts["ETL漏れ_未分類"] += 1
    elif has_bq_recent and bq_info.get("monthly_cnt", 0) == 0:
        status = "カテゴリ分類漏れ_B相当"
        detail = f"BQ直近{bq_info['total_cnt']}件あり月次0件: {bq_info['categories']}"
        counts["カテゴリ分類漏れB相当"] += 1
    elif not has_gcs_any and not has_bq_recent:
        status = "DL漏れ"
        detail = "GCS/BQともに直近レコードなし"
        counts["DL漏れ"] += 1
    else:
        status = "その他"
        detail = f"GCS={has_gcs_any},BQ={has_bq_recent},月次={bq_info.get('monthly_cnt',0)}"

    results.append((ticker, name, status, detail))

# CSV 出力
with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["TICKER", "COMPANY_NAME", "STATUS", "DETAIL"])
    for row in results:
        w.writerow(row)

# サマリー
print(f"\n=== D1 再チェック結果 ===")
print(f"  ETL漏れ（月次PDF GCSあり・BQ未ロード）  : {counts['ETL漏れ_月次']:3d} 社")
print(f"  ETL漏れ（任意PDF GCSあり・BQ未ロード）  : {counts['ETL漏れ_未分類']:3d} 社")
print(f"  カテゴリ分類漏れ（B相当・直近BQ記録あり）: {counts['カテゴリ分類漏れB相当']:3d} 社")
print(f"  DLまたは真のDL漏れ（GCS/BQともになし）  : {counts['DL漏れ']:3d} 社")
print(f"\n出力: {OUT_CSV}")

# 各ステータス先頭10件
for status_key in ["ETL漏れ_月次", "ETL漏れ_未分類", "カテゴリ分類漏れ_B相当", "DL漏れ"]:
    items = [(t, n, d) for t, n, s, d in results if s == status_key]
    if items:
        print(f"\n--- {status_key} 先頭10件 ---")
        for t, n, d in items[:10]:
            print(f"  {t} {n}: {d}")

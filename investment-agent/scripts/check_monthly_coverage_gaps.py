"""月次開示370社の未カバー原因を分類する.

対象: data/monthly_missing_buffett.csv の370社
分類:
  A: BQ に月次開示あり（突合クエリの漏れ）
  B: BQ にレコードあるが月次開示カテゴリなし（カテゴリ分類漏れ）
  C: GCS に PDF あるが BQ に未登録（ETLロード不備）
  D: GCS にも PDF なし（ダウンロード不備）

Usage:
    PYTHONUTF8=1 uv run python scripts/check_monthly_coverage_gaps.py

出力:
    data/monthly_coverage_gaps.csv  全370社の分類結果
"""

import csv
import os
import re
import urllib3
import requests as _req
from requests.adapters import HTTPAdapter as _HA

# ────────────────────────────────────────────────
# BQ SSL 回避パッチ（requests.Session 全体に適用）
# ────────────────────────────────────────────────
urllib3.disable_warnings()

class _NoVerify(_HA):
    def send(self, req, **kw):
        kw["verify"] = False
        return super().send(req, **kw)

_orig = _req.Session.__init__
def _p(self, *a, **kw):
    _orig(self, *a, **kw)
    self.mount("https://", _NoVerify())
    self.verify = False
_req.Session.__init__ = _p

# ────────────────────────────────────────────────
# 設定
# ────────────────────────────────────────────────
from google.cloud import bigquery, storage
from google.oauth2 import service_account

KEY_PATH   = "keys/gcp-service-account.json"
PROJECT    = "gmailpj-357912"
TABLE_ID   = f"{PROJECT}.STOCK.TDNET_DOCUMENTS_ENHANCED"
BUCKET     = "stock_data_1930932"
MISSING_CSV = "data/monthly_missing_buffett.csv"
OUT_CSV     = "data/monthly_coverage_gaps.csv"
CUTOFF      = "2025-03-14"  # 直近12ヶ月

creds = service_account.Credentials.from_service_account_file(KEY_PATH)


# ────────────────────────────────────────────────
# Step 1: 370社リスト読み込み
# ────────────────────────────────────────────────
def load_missing_tickers() -> list[dict]:
    with open(MISSING_CSV, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ────────────────────────────────────────────────
# Step 2: BQ チェック（全370社を一括クエリ）
# ────────────────────────────────────────────────
def check_bq(tickers: list[str]) -> dict[str, list[str]]:
    """ticker → BQ に存在するカテゴリリストを返す（直近12ヶ月）。"""
    client = bigquery.Client(project=PROJECT, credentials=creds)

    # 月次開示かどうか（MAIN_CATEGORY or SUB_CATEGORIES）
    ticker_list = ", ".join(f"'{t}'" for t in tickers)
    query = f"""
    SELECT
        TICKER,
        MAIN_CATEGORY,
        LOGICAL_OR(
            MAIN_CATEGORY = '月次開示'
            OR EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) sc WHERE sc = '月次開示')
        ) AS has_monthly,
        COUNT(*) AS cnt
    FROM `{TABLE_ID}`
    WHERE SUBMISSION_DATE >= '{CUTOFF}'
      AND TICKER IN ({ticker_list})
    GROUP BY TICKER, MAIN_CATEGORY
    ORDER BY TICKER, cnt DESC
    """
    print("BQ クエリ実行中...")
    rows = list(client.query(query).result())
    print(f"  → {len(rows)} 行取得")

    # ticker → {categories, has_monthly}
    result: dict[str, dict] = {}
    for row in rows:
        t = row.TICKER
        if t not in result:
            result[t] = {"categories": [], "has_monthly": False}
        result[t]["categories"].append(row.MAIN_CATEGORY)
        if row.has_monthly:
            result[t]["has_monthly"] = True

    return result


# ────────────────────────────────────────────────
# Step 3: GCS チェック（BQ未登録の銘柄のみ）
# ────────────────────────────────────────────────
def check_gcs(tickers_to_check: list[str]) -> dict[str, bool]:
    """ticker → GCS に2025年以降の月次PDFが存在するか。

    TDnetのPDFファイル名パターン: tdnet/YYYYMMDD_TICKER_...pdf
    月次開示のタイトルキーワード: 月次, 月度売上, 売上速報
    """
    client = storage.Client(project=PROJECT, credentials=creds)
    bucket = client.bucket(BUCKET)

    ticker_set = set(tickers_to_check)
    found: dict[str, bool] = {t: False for t in ticker_set}

    MONTHLY_PAT = re.compile(r"月次|月度売上|売上速報|売上推移速報|月度業績|受注速報")

    print(f"GCS チェック中（{len(ticker_set)} 社）...")
    checked = 0

    # tdnet/ を年月単位でスキャン（2025-01 〜 2026-03）
    for year in (2025, 2026):
        months = range(1, 4) if year == 2026 else range(1, 13)
        for month in months:
            prefix = f"tdnet/{year}{month:02d}"
            for blob in bucket.list_blobs(prefix=prefix):
                if not blob.name.lower().endswith(".pdf"):
                    continue
                parts = blob.name.split("/")[-1].split("_")
                if len(parts) < 5:
                    continue
                ticker = parts[1][:4]
                if ticker not in ticker_set:
                    continue
                # ファイル名中のタイトル部分で月次判定
                title = parts[4] if len(parts) > 4 else ""
                category = parts[3] if len(parts) > 3 else ""
                if MONTHLY_PAT.search(title) or MONTHLY_PAT.search(category) or category == "月次開示":
                    if not found[ticker]:
                        found[ticker] = True
                        checked += 1
                        print(f"  GCS 月次PDF発見: {ticker} → {blob.name}")

    not_found = [t for t, v in found.items() if not v]
    print(f"  GCS 月次PDF あり: {checked} 社 / なし: {len(not_found)} 社")
    return found


# ────────────────────────────────────────────────
# Step 4: 分類・出力
# ────────────────────────────────────────────────
def run() -> None:
    missing = load_missing_tickers()
    tickers = [r["TICKER"] for r in missing]
    names   = {r["TICKER"]: r["COMPANY_NAME"] for r in missing}

    # BQ チェック
    bq_info = check_bq(tickers)
    bq_absent = [t for t in tickers if t not in bq_info]
    bq_present_no_monthly = [
        t for t in tickers
        if t in bq_info and not bq_info[t]["has_monthly"]
    ]
    bq_present_with_monthly = [
        t for t in tickers
        if t in bq_info and bq_info[t]["has_monthly"]
    ]

    print(f"\n[BQ チェック結果]")
    print(f"  A: BQ に月次開示あり（クエリ漏れ）: {len(bq_present_with_monthly)} 社")
    print(f"  B: BQ にレコードあるが月次なし（カテゴリ分類漏れ）: {len(bq_present_no_monthly)} 社")
    print(f"  C/D: BQ に未登録: {len(bq_absent)} 社")

    # GCS チェック（BQ未登録のみ）
    gcs_info = {}
    if bq_absent:
        gcs_info = check_gcs(bq_absent)

    # 分類
    results = []
    for ticker in tickers:
        name = names.get(ticker, "")
        if ticker in bq_present_with_monthly:
            gap_type = "A"
            detail   = "BQ月次あり（突合クエリ漏れ）"
        elif ticker in bq_present_no_monthly:
            cats = ", ".join(bq_info[ticker]["categories"])
            gap_type = "B"
            detail   = f"BQ有・月次カテゴリなし（{cats}）"
        elif gcs_info.get(ticker):
            gap_type = "C"
            detail   = "GCS月次PDFあり・BQ未ロード（ETL不備）"
        else:
            gap_type = "D"
            detail   = "GCS月次PDFなし（ダウンロード不備）"
        results.append((ticker, name, gap_type, detail))

    # CSV 出力
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["TICKER", "COMPANY_NAME", "GAP_TYPE", "DETAIL"])
        for row in results:
            w.writerow(row)

    # サマリー
    from collections import Counter
    counts = Counter(r[2] for r in results)
    print(f"\n=== 分類結果サマリー ===")
    print(f"  A: BQ月次あり（突合クエリ漏れ）   : {counts['A']:3d} 社")
    print(f"  B: カテゴリ分類漏れ               : {counts['B']:3d} 社")
    print(f"  C: GCS有・BQ未ロード（ETL不備）   : {counts['C']:3d} 社")
    print(f"  D: GCSなし（ダウンロード不備）     : {counts['D']:3d} 社")
    print(f"\n出力: {OUT_CSV}")

    # B の先頭20件表示
    b_items = [(t, n, d) for t, n, g, d in results if g == "B"]
    if b_items:
        print(f"\n[B カテゴリ先頭20件]（カテゴリ分類漏れ）")
        for t, n, d in b_items[:20]:
            print(f"  {t} {n}: {d}")


if __name__ == "__main__":
    run()

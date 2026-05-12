"""20日β計算 → GCS CSV キャッシュ保存.

全上場銘柄の直近20営業日βを計算し、GCS に CSV として保存する。
決算反応モデル（predict ノートブック）が因子9として参照する。

対応環境:
    - ローカルPC
    - Cloud Run Job

使い方:
    PYTHONUTF8=1 python scripts/beta_calc.py              # 当日分
    PYTHONUTF8=1 python scripts/beta_calc.py --dry-run    # GCS 書き込みなし
"""

import argparse
import os
import sys
from datetime import datetime, timezone, timedelta
from io import StringIO

import pandas as pd
from google.cloud import bigquery, storage

# ==========================================
# 設定
# ==========================================

PROJECT = "gmailpj-357912"
KEY_FILE = "keys/gcp-service-account.json"
GCS_BUCKET = "stock_data_1930932"
GCS_PATH = "earnings_model/zaraba_beta_20d/beta_20d.csv"
JST = timezone(timedelta(hours=+9), "JST")


# ==========================================
# 0. 実行環境判別
# ==========================================

def detect_runtime() -> str:
    """実行環境を自動判別する.

    Returns:
        "cloudrun" | "colab_personal" | "colab_enterprise" | "local"
    """
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    try:
        import google.colab  # noqa: F401
        if os.environ.get("GOOGLE_CLOUD_PROJECT"):
            return "colab_enterprise"
        return "colab_personal"
    except ImportError:
        return "local"


RUNTIME = detect_runtime()
print(f"[beta_calc] runtime={RUNTIME}")


def get_clients() -> tuple[bigquery.Client, storage.Client]:
    """BQ / GCS クライアントを取得する.

    Returns:
        (bigquery.Client, storage.Client)
    """
    if RUNTIME == "local":
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(KEY_FILE)
        return (
            bigquery.Client(project=PROJECT, credentials=creds),
            storage.Client(project=PROJECT, credentials=creds),
        )
    else:
        return (
            bigquery.Client(project=PROJECT),
            storage.Client(project=PROJECT),
        )


# ==========================================
# 1. β計算 SQL
# ==========================================

BETA_SQL = """
WITH market_daily AS (
  SELECT DATE,
    SAFE_DIVIDE(
      CLOSE - LAG(CLOSE) OVER (ORDER BY DATE),
      LAG(CLOSE) OVER (ORDER BY DATE)
    ) AS market_ret
  FROM `gmailpj-357912.STOCK.INDEX_PRICE`
  WHERE INDEX_CODE = '0000' AND DATE >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
),
stock_daily AS (
  SELECT TICKER, DATE,
    SAFE_DIVIDE(
      ADJ_CLOSE - LAG(ADJ_CLOSE) OVER (PARTITION BY TICKER ORDER BY DATE),
      LAG(ADJ_CLOSE) OVER (PARTITION BY TICKER ORDER BY DATE)
    ) AS stock_ret
  FROM `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS`
  WHERE IS_PREFERRED = FALSE
    AND DATE >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
),
combined AS (
  SELECT s.TICKER, s.DATE, s.stock_ret, m.market_ret
  FROM stock_daily s
  JOIN market_daily m ON s.DATE = m.DATE
  WHERE s.stock_ret IS NOT NULL AND m.market_ret IS NOT NULL
),
beta_calc AS (
  SELECT TICKER, DATE,
    SAFE_DIVIDE(
      AVG(stock_ret * market_ret) OVER w
        - AVG(stock_ret) OVER w * AVG(market_ret) OVER w,
      AVG(market_ret * market_ret) OVER w
        - POW(AVG(market_ret) OVER w, 2)
    ) AS beta_20d,
    COUNT(*) OVER w AS window_cnt
  FROM combined
  WINDOW w AS (
    PARTITION BY TICKER ORDER BY DATE
    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
  )
)
SELECT TICKER, beta_20d
FROM beta_calc
WHERE window_cnt >= 15
QUALIFY ROW_NUMBER() OVER (PARTITION BY TICKER ORDER BY DATE DESC) = 1
ORDER BY TICKER
"""


# ==========================================
# 2. メイン処理
# ==========================================

def main() -> None:
    """20日βを計算して GCS に保存する."""
    parser = argparse.ArgumentParser(description="20-day beta calculator")
    parser.add_argument("--dry-run", action="store_true", help="GCS 書き込みなし")
    args = parser.parse_args()

    now_jst = datetime.now(tz=JST)
    print(f"[beta_calc] start: {now_jst.strftime('%Y-%m-%d %H:%M:%S JST')}")

    bq_client, gcs_client = get_clients()

    # β計算
    print("[beta_calc] Querying BQ for 20-day beta...")
    df = bq_client.query(BETA_SQL).to_dataframe()
    df = df.dropna(subset=["beta_20d"])
    print(f"[beta_calc] {len(df)} tickers calculated")
    print(f"[beta_calc] beta stats: mean={df['beta_20d'].mean():.2f}, "
          f"median={df['beta_20d'].median():.2f}, "
          f"std={df['beta_20d'].std():.2f}")

    # 計算日を付与
    df["calc_date"] = now_jst.strftime("%Y-%m-%d")

    # GCS に保存
    if args.dry_run:
        print(f"[beta_calc] DRY RUN - skipping GCS upload")
        print(df.head(10).to_string(index=False))
    else:
        csv_buf = StringIO()
        df.to_csv(csv_buf, index=False)
        bucket = gcs_client.bucket(GCS_BUCKET)
        blob = bucket.blob(GCS_PATH)
        blob.upload_from_string(csv_buf.getvalue(), content_type="text/csv")
        print(f"[beta_calc] Saved to gs://{GCS_BUCKET}/{GCS_PATH} ({len(df)} rows)")

    elapsed = (datetime.now(tz=JST) - now_jst).total_seconds()
    print(f"[beta_calc] done in {elapsed:.1f}s")


if __name__ == "__main__":
    main()

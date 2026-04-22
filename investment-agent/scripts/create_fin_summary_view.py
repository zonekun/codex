"""BQ ビュー v_fin_summary_actual_for_q_on_q を作成・更新するスクリプト.

対象: gmailpj-357912.STOCK.v_fin_summary_actual_for_q_on_q

実行方法:
    PYTHONUTF8=1 python scripts/create_fin_summary_view.py
    PYTHONUTF8=1 python scripts/create_fin_summary_view.py --dry-run  # SQL 表示のみ
"""
import argparse
import os
import sys
from google.cloud import bigquery
from google.oauth2 import service_account

PROJECT = "gmailpj-357912"
DATASET = "STOCK"
VIEW_ID = "v_fin_summary_actual_for_q_on_q"
KEY_FILE = "keys/gcp-service-account.json"

# -----------------------------------------------------------
# ビュー定義 SQL
# -----------------------------------------------------------
VIEW_SQL = f"""
WITH

-- Step 1: 対象レコードに絞り込む（1Q / 2Q / 3Q / FY のみ）
filtered AS (
  SELECT *
  FROM `{PROJECT}.{DATASET}.fin_summary`
  WHERE TYPE_OF_CURRENT_PERIOD IN ('1Q', '2Q', '3Q', 'FY')
),

-- Step 2: 修正開示対応 — 同一銘柄・事業年度・期区分で最新開示のみ残す
latest AS (
  SELECT *,
    ROW_NUMBER() OVER (
      PARTITION BY
        LOCAL_CODE,
        CURRENT_FISCAL_YEAR_START_DATE,
        TYPE_OF_CURRENT_PERIOD
      ORDER BY DISCLOSED_DATE DESC, DISCLOSED_TIME DESC
    ) AS _rn
  FROM filtered
),
deduped AS (
  SELECT * EXCEPT(_rn)
  FROM latest
  WHERE _rn = 1
),

-- Step 3: 前期累計値を LAG で取得
--   PARTITION: 銘柄 × 事業年度開始日（会計年度単位）
--   ORDER: 期末日昇順（1Q < 2Q < 3Q < FY の順序が保証される）
--   半期報告企業（Q1/Q3 なし）は prev=NULL になり COALESCE で 0 扱い
--   → 2Q standalone = 2Q 累積そのまま（Q3=B の方針に合致）
with_prev AS (
  SELECT
    *,
    -- 連結
    LAG(NET_SALES)               OVER w AS _prev_net_sales,
    LAG(OPERATING_PROFIT)        OVER w AS _prev_op,
    LAG(ORDINARY_PROFIT)         OVER w AS _prev_odp,
    LAG(PROFIT)                  OVER w AS _prev_np,
    -- 非連結
    LAG(NON_CONSOLIDATED_NET_SALES)        OVER w AS _prev_nc_net_sales,
    LAG(NON_CONSOLIDATED_OPERATING_PROFIT) OVER w AS _prev_nc_op,
    LAG(NON_CONSOLIDATED_ORDINARY_PROFIT)  OVER w AS _prev_nc_odp,
    LAG(NON_CONSOLIDATED_PROFIT)           OVER w AS _prev_nc_np,
  FROM deduped
  WINDOW w AS (
    PARTITION BY LOCAL_CODE, CURRENT_FISCAL_YEAR_START_DATE
    ORDER BY CURRENT_PERIOD_END_DATE
  )
)

-- Step 4: 単独四半期値を算出して出力
--   1Q: prev=NULL → value - 0 = value（1Q 累積 = 1Q 単独）
--   2Q/3Q: value - prev_value
--   FY → "4Q" に変換し、value - Q3_value = Q4 単独値
SELECT

  -- 識別子
  LOCAL_CODE,
  DISCLOSED_DATE,
  DISCLOSED_TIME,
  TYPE_OF_DOCUMENT,

  -- 四半期ラベル（FY → 4Q に変換）
  CASE TYPE_OF_CURRENT_PERIOD
    WHEN 'FY' THEN '4Q'
    ELSE TYPE_OF_CURRENT_PERIOD
  END AS QUARTER,

  TYPE_OF_CURRENT_PERIOD,  -- 元の開示種別も保持（デバッグ用）

  -- 期間情報
  CURRENT_PERIOD_START_DATE,
  CURRENT_PERIOD_END_DATE,
  CURRENT_FISCAL_YEAR_START_DATE,
  CURRENT_FISCAL_YEAR_END_DATE,

  -- 連結 P&L 単独四半期値
  NET_SALES               - COALESCE(_prev_net_sales, 0)   AS NET_SALES,
  OPERATING_PROFIT        - COALESCE(_prev_op, 0)          AS OPERATING_PROFIT,
  ORDINARY_PROFIT         - COALESCE(_prev_odp, 0)         AS ORDINARY_PROFIT,
  PROFIT                  - COALESCE(_prev_np, 0)          AS PROFIT,

  -- 非連結 P&L 単独四半期値
  NON_CONSOLIDATED_NET_SALES
    - COALESCE(_prev_nc_net_sales, 0)        AS NON_CONSOLIDATED_NET_SALES,
  NON_CONSOLIDATED_OPERATING_PROFIT
    - COALESCE(_prev_nc_op, 0)               AS NON_CONSOLIDATED_OPERATING_PROFIT,
  NON_CONSOLIDATED_ORDINARY_PROFIT
    - COALESCE(_prev_nc_odp, 0)              AS NON_CONSOLIDATED_ORDINARY_PROFIT,
  NON_CONSOLIDATED_PROFIT
    - COALESCE(_prev_nc_np, 0)               AS NON_CONSOLIDATED_PROFIT,

FROM with_prev
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="BQ ビュー v_fin_summary_actual_for_q_on_q を作成")
    parser.add_argument("--dry-run", action="store_true", help="SQL を表示するだけで作成しない")
    args = parser.parse_args()

    if args.dry_run:
        print(VIEW_SQL)
        return

    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    client = bigquery.Client(project=PROJECT, credentials=creds)

    view_ref = f"{PROJECT}.{DATASET}.{VIEW_ID}"
    view = bigquery.Table(view_ref)
    view.view_query = VIEW_SQL

    # CREATE OR REPLACE VIEW 相当（既存なら上書き）
    try:
        client.delete_table(view_ref, not_found_ok=True)
        client.create_table(view)
        print(f"ビュー作成完了: {view_ref}")
    except Exception as e:
        print(f"エラー: {e}")
        raise


if __name__ == "__main__":
    main()

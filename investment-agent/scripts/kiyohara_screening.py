"""清原式 実質企業価値スクリーナー.

清原氏メソッドによるネットキャッシュ考慮後の実質時価総額・実質PERを
全上場銘柄で計算し、デスクトップに CSV 出力する。

計算式:
    ネットキャッシュ = 現預金 + 有価証券 × 0.7 - 有利子負債
    実質時価総額    = 時価総額 - ネットキャッシュ
    実質PER        = 実質時価総額 / 次期純利益予想

データソース:
    - BQ STOCK.STOCK_PRICE    : 最新終値
    - BQ STOCK.fin_summary    : 財務サマリー（株数・予想利益）
    - BQ STOCK.STOCK_CODE_LIST: 銘柄マスタ（業種・市場区分）
    - 四季報 Excel             : 現金等・有利子負債（単位変換あり）

⚠️ 四季報データの単位:
    - 有利子負債・総資産・自己資本 : 百万円（固定）
    - 現金等                      : CF単位（行ごとに百万円/億円）
    - 時価総額                    : 億円（固定）
    ※ 有価証券（持ち合い株等）は四季報に列なし → 0 として扱う（保守的）

使い方:
    PYTHONUTF8=1 python scripts/kiyohara_screening.py
"""

import logging
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from google.cloud import bigquery
from google.oauth2 import service_account

# ── 設定 ─────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent.parent / ".env")

PROJECT_ID = "gmailpj-357912"
DATASET    = "STOCK"
KEY_FILE   = str(Path(__file__).parent.parent / "keys" / "gcp-service-account.json")

# デスクトップ出力パス（Windows）
DESKTOP = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"

# 四季報 Excelファイルパス
SHIKIHO_EXCEL = Path(r"C:\Users\zonekun\Dropbox\stock\DA_四季報_2026_2.xlsx")

# 除外業種（銀行・保険・証券等、清原式では有利子負債の概念が異なるため）
EXCLUDE_INDUSTRIES = [
    "銀行業",
    "保険業",
    "証券、商品先物取引業",
    "その他金融業",
]

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)


def get_bq_client() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return bigquery.Client(project=PROJECT_ID, credentials=creds)


def fetch_stock_master(bq: bigquery.Client) -> pd.DataFrame:
    """銘柄マスタ（コード・名称・業種）を取得."""
    sql = f"""
    SELECT
        TICKER                  AS code,
        STOCK_NAME              AS company_name,
        MARKET_CATEGORY         AS market,
        INDUSTRY_33_CATEGORY    AS industry
    FROM `{PROJECT_ID}.{DATASET}.STOCK_CODE_LIST`
    """
    logger.info("銘柄マスタ取得中...")
    df = bq.query(sql).to_dataframe()
    logger.info(f"  → {len(df):,} 銘柄")
    return df


def fetch_latest_prices(bq: bigquery.Client) -> pd.DataFrame:
    """最新終値を取得（直近30営業日以内）."""
    sql = f"""
    SELECT TICKER AS code, CLOSE AS price, YEARDATE AS price_date
    FROM (
        SELECT TICKER, CLOSE, YEARDATE,
               ROW_NUMBER() OVER (PARTITION BY TICKER ORDER BY YEARDATE DESC) AS rn
        FROM `{PROJECT_ID}.{DATASET}.STOCK_PRICE`
        WHERE YEARDATE >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
          AND CLOSE IS NOT NULL AND CLOSE > 0
    )
    WHERE rn = 1
    """
    logger.info("最新株価取得中...")
    df = bq.query(sql).to_dataframe()
    logger.info(f"  → {len(df):,} 銘柄の株価")
    return df


def fetch_fin_summary(bq: bigquery.Client) -> pd.DataFrame:
    """fin_summary から銘柄ごとの最新 FY 通期決算データを取得."""
    sql = f"""
    WITH ranked AS (
        SELECT
            LOCAL_CODE                  AS code,
            DISCLOSED_DATE              AS disclosed_date,
            TYPE_OF_DOCUMENT            AS doc_type,
            TYPE_OF_CURRENT_PERIOD      AS period_type,
            CURRENT_FISCAL_YEAR_START_DATE  AS fy_start,
            CURRENT_FISCAL_YEAR_END_DATE    AS fy_end,

            -- 現預金（CF末残高: 良い近似）
            CASH_AND_EQUIVALENTS            AS cash_and_equivalents,

            -- 株数（自己株含む・除く）
            NUMBER_OF_ISSUED_AND_OUTSTANDING_SHARES_AT_THE_END_OF_FISCAL_YEAR_INCLUDING_TREASURY_STOCK
                                            AS shares_total,
            NUMBER_OF_TREASURY_STOCK_AT_THE_END_OF_FISCAL_YEAR
                                            AS treasury_shares,

            -- 財務健全性
            TOTAL_ASSETS                    AS total_assets,
            EQUITY                          AS equity,
            EQUITY_TO_ASSET_RATIO           AS equity_ratio,
            BOOK_VALUE_PER_SHARE            AS bps,

            -- 利益（実績・予想）
            PROFIT                          AS net_income,
            EARNINGS_PER_SHARE              AS eps,

            -- 次期予想純利益（連結優先 → 単体フォールバック）
            NEXT_YEAR_FORECAST_PROFIT               AS forecast_next_consolidated,
            FORECAST_PROFIT                         AS forecast_cur_consolidated,
            NEXT_YEAR_FORECAST_NON_CONSOLIDATED_PROFIT  AS forecast_next_noncon,
            FORECAST_NON_CONSOLIDATED_PROFIT            AS forecast_cur_noncon,

            ROW_NUMBER() OVER (
                PARTITION BY LOCAL_CODE
                ORDER BY DISCLOSED_DATE DESC, DISCLOSURE_NUMBER DESC
            ) AS rn
        FROM `{PROJECT_ID}.{DATASET}.fin_summary`
        WHERE
            DISCLOSED_DATE >= DATE_SUB(CURRENT_DATE(), INTERVAL 24 MONTH)
            AND TYPE_OF_DOCUMENT LIKE 'FY%'
            AND TYPE_OF_DOCUMENT NOT LIKE '%REIT%'
    )
    SELECT * EXCEPT(rn) FROM ranked WHERE rn = 1
    """
    logger.info("fin_summary 取得中（最新FY1件/銘柄）...")
    df = bq.query(sql).to_dataframe()
    logger.info(f"  → {len(df):,} 銘柄")
    return df


def fetch_shikiho() -> pd.DataFrame:
    """四季報 Excel からネットキャッシュ計算に必要な列を取得・単位変換する.

    単位:
        有利子負債・総資産・自己資本 → 百万円固定（×1,000,000 → yen）
        現金等                      → CF単位依存（百万円 or 億円）
        時価総額                    → 億円固定（スクリーニングには使わず参考値）
    """
    if not SHIKIHO_EXCEL.exists():
        logger.warning(f"四季報ファイルが見つかりません: {SHIKIHO_EXCEL}")
        return pd.DataFrame()

    logger.info(f"四季報データ読み込み中: {SHIKIHO_EXCEL.name} ...")
    raw = pd.read_excel(SHIKIHO_EXCEL, sheet_name="list", header=0)
    logger.info(f"  → {len(raw):,} 行")

    # コードを4桁文字列に統一（整数 1301 → "1301"、文字列 "130A" → そのまま）
    raw["code"] = raw["コード"].apply(
        lambda x: str(int(x)).zfill(4) if str(x).replace(".", "").isdigit() else str(x)
    )

    # CF単位ごとの倍率（現金等用）
    cf_multiplier = raw["CF単位"].map({"百万円": 1_000_000, "億円": 100_000_000}).fillna(1_000_000)

    df = pd.DataFrame()
    df["code"] = raw["code"]

    # 現金等（現預金の代理値）: CF単位 × 数値
    df["shikiho_cash"] = pd.to_numeric(raw["現金等"], errors="coerce") * cf_multiplier

    # 有利子負債: 百万円固定 × 1,000,000
    df["shikiho_debt"] = pd.to_numeric(raw["有利子負債"], errors="coerce") * 1_000_000

    # 参考列（百万円固定）
    df["shikiho_total_assets"] = pd.to_numeric(raw["総資産"], errors="coerce") * 1_000_000
    df["shikiho_equity"]       = pd.to_numeric(raw["自己資本"], errors="coerce") * 1_000_000
    df["shikiho_equity_ratio"] = pd.to_numeric(raw["自己資本比率"], errors="coerce")

    # 四季報独自情報（参考）
    df["shikiho_roe"]    = pd.to_numeric(raw["ROE"], errors="coerce")
    df["shikiho_sector"] = raw["セクター"].astype(str)

    # コード重複がある場合は最初の1件を使用
    df = df.drop_duplicates(subset="code", keep="first")
    logger.info(f"  → {len(df):,} 銘柄 (有利子負債有効: {df['shikiho_debt'].notna().sum():,})")
    return df


def calc_forecast_profit(row: pd.Series) -> float | None:
    """次期純利益予想を決定（連結翌期 → 連結当期 → 単体翌期 → 単体当期の優先順）."""
    for col in [
        "forecast_next_consolidated",
        "forecast_cur_consolidated",
        "forecast_next_noncon",
        "forecast_cur_noncon",
    ]:
        v = row.get(col)
        if pd.notna(v) and float(v) != 0:
            return float(v)
    return None


def safe_float(val, default: float = 0.0) -> float:
    """NaN / None を default に変換."""
    try:
        f = float(val)
        return default if pd.isna(f) else f
    except (TypeError, ValueError):
        return default


def main() -> None:
    bq = get_bq_client()

    # ── データ取得 ──────────────────────────────────────────
    df_master  = fetch_stock_master(bq)
    df_price   = fetch_latest_prices(bq)
    df_fin     = fetch_fin_summary(bq)
    df_shikiho = fetch_shikiho()

    # ── 結合 ────────────────────────────────────────────────
    df = df_fin.merge(df_master,  on="code", how="left")
    df = df.merge(df_price,       on="code", how="left")
    df = df.merge(df_shikiho,     on="code", how="left")  # 四季報

    # ── 金融業除外 ───────────────────────────────────────────
    before = len(df)
    df = df[~df["industry"].isin(EXCLUDE_INDUSTRIES)]
    logger.info(f"金融業除外: {before:,} → {len(df):,} 銘柄")

    # ── 計算 ────────────────────────────────────────────────
    df["shares_total"]    = pd.to_numeric(df["shares_total"],    errors="coerce").fillna(0)
    df["treasury_shares"] = pd.to_numeric(df["treasury_shares"], errors="coerce").fillna(0)
    df["price"]           = pd.to_numeric(df["price"],           errors="coerce")

    # 流通株式数（自己株除く）
    df["shares_net"] = (df["shares_total"] - df["treasury_shares"]).clip(lower=1)

    # 時価総額
    df["market_cap"] = df["price"] * df["shares_net"]

    # ── 現預金: 四季報 現金等 を優先、なければ fin_summary CASH_AND_EQUIVALENTS ──
    df["cash_shikiho"]    = pd.to_numeric(df["shikiho_cash"],         errors="coerce")
    df["cash_finsum"]     = pd.to_numeric(df["cash_and_equivalents"], errors="coerce")
    df["cash"]            = df["cash_shikiho"].combine_first(df["cash_finsum"]).fillna(0)
    df["cash_source"]     = df["cash_shikiho"].notna().map({True: "四季報", False: "fin_summary"})

    # 有価証券（四季報に列なし → 0）
    df["securities"] = 0.0  # ⚠️ 持ち合い株等は未考慮（保守的）

    # ── 有利子負債: 四季報データ（取得できた場合） ──────────
    df["interest_debt"] = pd.to_numeric(df["shikiho_debt"], errors="coerce").fillna(0)
    df["debt_source"]   = df["shikiho_debt"].notna().map({True: "四季報", False: "不明(0)"})

    # ── ネットキャッシュ（清原式） ──────────────────────────
    # 現預金 + 有価証券×0.7 - 有利子負債
    # 有価証券=0 のため: 現預金 - 有利子負債
    df["net_cash"] = df["cash"] + df["securities"] * 0.7 - df["interest_debt"]

    # 実質時価総額
    df["adj_market_cap"] = df["market_cap"] - df["net_cash"]

    # 次期純利益予想
    df["forecast_profit"] = df.apply(calc_forecast_profit, axis=1)
    df["forecast_profit"] = pd.to_numeric(df["forecast_profit"], errors="coerce")

    # 実質PER（次期純利益が正のときのみ）
    df["adj_per"] = df.apply(
        lambda r: r["adj_market_cap"] / r["forecast_profit"]
        if pd.notna(r["forecast_profit"]) and r["forecast_profit"] > 0
        and pd.notna(r["adj_market_cap"])
        else None,
        axis=1,
    )

    # 現金/時価総額比率
    df["cash_to_mcap_pct"] = df.apply(
        lambda r: r["cash"] / r["market_cap"] * 100
        if pd.notna(r["market_cap"]) and r["market_cap"] > 0
        else None,
        axis=1,
    )

    # 自己資本比率（四季報優先 → fin_summary フォールバック）
    df["eq_ratio_shikiho"] = pd.to_numeric(df["shikiho_equity_ratio"], errors="coerce") * 100
    df["eq_ratio_finsum"]  = pd.to_numeric(df["equity_ratio"],         errors="coerce") * 100
    df["equity_ratio_pct"] = df["eq_ratio_shikiho"].combine_first(df["eq_ratio_finsum"])

    # ── 出力列定義 ────────────────────────────────────────
    output = df[[
        "code", "company_name", "market", "industry",
        "price", "price_date",
        "shares_net", "market_cap",
        "cash",                         # 現預金（四季報 or CF末残高）
        "cash_source",                  # 現預金データ出所
        "interest_debt",                # 有利子負債（四季報）
        "debt_source",                  # 有利子負債データ出所
        "net_cash",                     # ネットキャッシュ（清原式: 現預金 - 有利子負債）
        "adj_market_cap",               # 実質時価総額
        "forecast_next_consolidated",   # 翌期予想純利益（連結）
        "forecast_cur_consolidated",    # 当期予想純利益（連結）
        "forecast_profit",              # 使用した予想純利益
        "adj_per",                      # 実質PER
        "cash_to_mcap_pct",             # 現金/時価総額比（%）
        "net_income",                   # 直近実績純利益
        "eps",                          # EPS
        "equity_ratio_pct",             # 自己資本比率（%）
        "equity",                       # 純資産（fin_summary）
        "total_assets",                 # 総資産（fin_summary）
        "bps",                          # BPS
        "shikiho_roe",                  # ROE（四季報）
        "doc_type",                     # 会計基準
        "fy_end",                       # 決算期末
        "disclosed_date",               # 開示日
    ]].copy()

    output.columns = [
        "銘柄コード", "会社名", "市場", "業種",
        "株価", "株価日付",
        "流通株式数", "時価総額",
        "現預金", "現預金出所",
        "有利子負債", "有利子負債出所",
        "ネットキャッシュ",
        "実質時価総額",
        "翌期予想純利益_連結", "当期予想純利益_連結",
        "使用予想純利益",
        "実質PER",
        "現金_時価総額比率%",
        "直近実績純利益", "EPS",
        "自己資本比率%",
        "純資産", "総資産", "BPS",
        "ROE四季報",
        "会計基準",
        "決算期末", "開示日",
    ]

    # 実質PER昇順でソート（NaN末尾）
    output = output.sort_values("実質PER", ascending=True, na_position="last")

    # ── CSV出力 ──────────────────────────────────────────
    today_str = date.today().strftime("%Y%m%d")
    out_path = DESKTOP / f"kiyohara_screening_{today_str}.csv"
    output.to_csv(out_path, index=False, encoding="utf-8-sig")

    shikiho_matched = df["shikiho_debt"].notna().sum()
    logger.info(f"\n{'='*60}")
    logger.info(f"出力完了: {out_path}")
    logger.info(f"総銘柄数          : {len(output):,}")
    logger.info(f"四季報マッチ数    : {shikiho_matched:,} / {len(output):,}")
    logger.info(f"実質PER算出済     : {output['実質PER'].notna().sum():,}")
    logger.info(f"ネットキャッシュ正 : {(output['ネットキャッシュ'] > 0).sum():,} 銘柄（現金 > 有利子負債）")
    logger.info(f"現金 > 時価総額   : {(output['現預金'] > output['時価総額']).sum():,} 銘柄")
    logger.info(f"実質PER < 5倍    : {(output['実質PER'] < 5).sum():,} 銘柄")
    logger.info(f"実質PER < 10倍   : {(output['実質PER'] < 10).sum():,} 銘柄")
    logger.info(f"{'='*60}")

    # サマリー表示（実質PER > 0 上位10件：正の実質時価総額＆割安）
    positive_per = output[(output["実質PER"].notna()) & (output["実質PER"] > 0)]
    top10 = positive_per.head(10)
    print("\n=== 実質PER 上位10銘柄（低いほど割安、正値のみ）===")
    print(top10[[
        "銘柄コード", "会社名", "株価", "時価総額", "現預金",
        "有利子負債", "ネットキャッシュ", "実質PER", "自己資本比率%"
    ]].to_string(index=False))

    print("\n⚠️  注意: 有価証券（持ち合い株等）は未考慮（保守的評価）")


if __name__ == "__main__":
    main()

"""EDINET大量保有報告書 遅延/訂正提出 → TOB候補スクリーニング.

Excelの遅延報告データと BQ 財務データを突合し、
TOB/MBO候補をスコアリングして出力する。

データソース:
  - Excel: Edinet遅延.xlsx（大量保有報告書の遅延提出一覧）
  - BQ: STOCK.fin_summary（財務サマリー: PBR, 特損推定, 配当予想）
  - BQ: STOCK.STOCK_PRICE_JQUANTS（直近株価）
  - BQ: STOCK.DELISTED_STOCKS（上場廃止・TOB/MBO判定）
  - BQ: STOCK.STOCK_CODE_LIST（銘柄マスタ）

使い方:
  PYTHONUTF8=1 python scripts/screen_edinet_delay_tob.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import structlog

# --- BQ SSL回避（ローカル専用） ---
import urllib3
import requests as _req
from requests.adapters import HTTPAdapter as _HA

urllib3.disable_warnings()


class _NoVerify(_HA):
    def send(self, req, **kw):  # type: ignore[override]
        kw["verify"] = False
        return super().send(req, **kw)


_orig = _req.Session.__init__


def _p(self, *a, **kw):  # type: ignore[no-untyped-def]
    _orig(self, *a, **kw)
    self.mount("https://", _NoVerify())
    self.verify = False


_req.Session.__init__ = _p  # type: ignore[assignment]

import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account

log = structlog.get_logger()

# --- 設定 ---
# "t"=ローカル実行 / "c"=Colab
MODE = "t"

EXCEL_PATH = Path(r"C:\Users\zonekun\Dropbox\stock\AI分析優待\Edinet遅延.xlsx")
CACHE_DIR = Path(r"C:\tmp\edinet_delay_screen")
OUTPUT_CSV = CACHE_DIR / "screening_result.csv"

# BQアクセスを再実行するか（False=キャッシュ利用）
FORCE_RELOAD = False

# スクリーニング閾値
PBR_THRESHOLD = 1.0          # PBR < 1.0 で加点
SPECIAL_LOSS_RATIO = 0.3     # 経常利益に対する特損比率（30%以上で加点）
MIN_DELAY_DAYS = 90          # 遅延日数が90日以上で対象
RECENT_FILING_DAYS = 365     # 直近N日以内の提出を重視

_CACHE = {
    "fin": CACHE_DIR / "fin_summary_latest.csv",
    "price": CACHE_DIR / "stock_price_latest.csv",
    "delisted": CACHE_DIR / "delisted_stocks.csv",
    "codelist": CACHE_DIR / "stock_code_list.csv",
}


def _get_bq_client() -> bigquery.Client:
    """GCP認証済みBQクライアントを返す."""
    key_path = os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS",
        r"C:\gdrive\claude\investment-agent\keys\gcp-service-account.json",
    )
    creds = service_account.Credentials.from_service_account_file(key_path)
    return bigquery.Client(credentials=creds, project=creds.project_id)


def _download_all(client: bigquery.Client) -> None:
    """BQから必要データを一括ダウンロード → CSV保存."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    queries = {
        # 各銘柄の最新開示の財務サマリー
        "fin": """
            WITH latest AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY LOCAL_CODE
                        ORDER BY DISCLOSED_DATE DESC, DISCLOSED_TIME DESC
                    ) AS rn
                FROM `gmailpj-357912.STOCK.fin_summary`
                WHERE LOCAL_CODE IS NOT NULL
            )
            SELECT
                LOCAL_CODE,
                DISCLOSED_DATE,
                BOOK_VALUE_PER_SHARE,
                ORDINARY_PROFIT,
                PROFIT,
                OPERATING_PROFIT,
                FORECAST_DIVIDEND_PER_SHARE_ANNUAL,
                RESULT_DIVIDEND_PER_SHARE_ANNUAL,
                EQUITY,
                NUMBER_OF_ISSUED_AND_OUTSTANDING_SHARES_AT_THE_END_OF_FISCAL_YEAR_INCLUDING_TREASURY_STOCK AS SHARES_OUTSTANDING,
                TYPE_OF_DOCUMENT
            FROM latest
            WHERE rn = 1
        """,
        # 各銘柄の直近株価
        "price": """
            WITH latest AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY TICKER
                        ORDER BY DATE DESC
                    ) AS rn
                FROM `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS`
            )
            SELECT TICKER, DATE AS PRICE_DATE, CLOSE, ADJ_CLOSE
            FROM latest
            WHERE rn = 1
        """,
        # 上場廃止（TOB/MBO判定済み）
        "delisted": """
            SELECT TICKER, DELISTING_DATE, DELISTING_REASON, IS_TOB_MBO
            FROM `gmailpj-357912.STOCK.DELISTED_STOCKS`
        """,
        # 銘柄マスタ
        "codelist": """
            SELECT TICKER, STOCK_NAME, MARKET_CATEGORY, INDUSTRY_33_CATEGORY
            FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
        """,
    }

    for name, query in queries.items():
        log.info("bq_download", table=name)
        df = client.query(query).to_dataframe()
        df.to_csv(_CACHE[name], index=False)
        log.info("bq_saved", table=name, rows=len(df))


def _load_excel() -> pd.DataFrame:
    """Excelの遅延報告データを読み込み."""
    df = pd.read_excel(EXCEL_PATH, sheet_name="MAIN", header=None)
    df.columns = ["TICKER", "COMPANY", "REPORTER", "OBLIGATION_DATE", "FILING_DATE", "DELAY_DAYS_STR"]
    # 遅延日数を数値化
    df["DELAY_DAYS"] = df["DELAY_DAYS_STR"].astype(str).str.replace("日", "", regex=False)
    df["DELAY_DAYS"] = pd.to_numeric(df["DELAY_DAYS"], errors="coerce")
    # TICKERを文字列4桁に
    df["TICKER"] = df["TICKER"].astype(str).str.zfill(4)
    # 提出日をdatetime変換
    df["FILING_DATE"] = pd.to_datetime(df["FILING_DATE"], errors="coerce")
    df["OBLIGATION_DATE"] = pd.to_datetime(df["OBLIGATION_DATE"], errors="coerce")
    return df


def _score(row: pd.Series) -> float:
    """各銘柄にスコアを付与（高いほどTOB候補として有望）."""
    score = 0.0

    # 1. PBR < 1.0（資産価値 > 時価総額）
    if pd.notna(row.get("PBR")) and row["PBR"] < PBR_THRESHOLD:
        score += 3.0
        if row["PBR"] < 0.5:
            score += 2.0  # PBR 0.5未満はさらに加点

    # 2. 特損推定（経常利益 - 純利益 が大きい）
    if pd.notna(row.get("SPECIAL_LOSS_RATIO")) and row["SPECIAL_LOSS_RATIO"] > SPECIAL_LOSS_RATIO:
        score += 2.0

    # 3. 配当未定（FORECAST_DIVIDEND が NULL）
    if row.get("DIVIDEND_UNDECIDED"):
        score += 2.0

    # 4. 遅延日数（長いほど加点）
    delay = row.get("MAX_DELAY_DAYS", 0)
    if pd.notna(delay):
        if delay >= 365:
            score += 2.0
        elif delay >= MIN_DELAY_DAYS:
            score += 1.0

    # 5. 同一銘柄で複数の遅延報告（買い集めの兆候）
    count = row.get("FILING_COUNT", 1)
    if count >= 3:
        score += 2.0
    elif count >= 2:
        score += 1.0

    # 6. 直近の提出（鮮度が高い）
    if pd.notna(row.get("DAYS_SINCE_LATEST_FILING")) and row["DAYS_SINCE_LATEST_FILING"] <= RECENT_FILING_DAYS:
        score += 1.0

    # 7. 過去にTOB/MBOで上場廃止された銘柄は除外（既に済み）
    if row.get("IS_DELISTED_TOB"):
        score = -1.0

    return score


def main() -> None:
    """メイン処理."""
    # --- BQダウンロード（初回 or FORCE_RELOAD時のみ） ---
    if FORCE_RELOAD or not all(p.exists() for p in _CACHE.values()):
        client = _get_bq_client()
        _download_all(client)
        log.info("bq_download_complete")
    else:
        log.info("using_cache", cache_dir=str(CACHE_DIR))

    # --- データ読み込み ---
    df_excel = _load_excel()
    df_fin = pd.read_csv(_CACHE["fin"])
    df_price = pd.read_csv(_CACHE["price"])
    df_delisted = pd.read_csv(_CACHE["delisted"])
    df_codelist = pd.read_csv(_CACHE["codelist"])

    log.info("data_loaded",
             excel_rows=len(df_excel),
             fin_rows=len(df_fin),
             price_rows=len(df_price),
             delisted_rows=len(df_delisted))

    # --- Excel集約（銘柄単位） ---
    today = pd.Timestamp.now()
    df_excel["DAYS_SINCE_FILING"] = (today - df_excel["FILING_DATE"]).dt.days

    agg = df_excel.groupby("TICKER").agg(
        COMPANY=("COMPANY", "first"),
        FILING_COUNT=("TICKER", "count"),
        MAX_DELAY_DAYS=("DELAY_DAYS", "max"),
        MEAN_DELAY_DAYS=("DELAY_DAYS", "mean"),
        LATEST_FILING_DATE=("FILING_DATE", "max"),
        REPORTERS=("REPORTER", lambda x: " / ".join(x.unique()[:3])),
    ).reset_index()
    agg["DAYS_SINCE_LATEST_FILING"] = (today - agg["LATEST_FILING_DATE"]).dt.days

    # --- fin_summary結合 ---
    df_fin["LOCAL_CODE"] = df_fin["LOCAL_CODE"].astype(str).str.zfill(4)
    agg = agg.merge(df_fin, left_on="TICKER", right_on="LOCAL_CODE", how="left")

    # --- 株価結合 ---
    df_price["TICKER"] = df_price["TICKER"].astype(str).str.zfill(4)
    agg = agg.merge(df_price, on="TICKER", how="left", suffixes=("", "_price"))

    # --- PBR計算 ---
    agg["PBR"] = None
    mask = (agg["BOOK_VALUE_PER_SHARE"].notna()) & (agg["BOOK_VALUE_PER_SHARE"] > 0) & (agg["ADJ_CLOSE"].notna())
    agg.loc[mask, "PBR"] = agg.loc[mask, "ADJ_CLOSE"] / agg.loc[mask, "BOOK_VALUE_PER_SHARE"]

    # --- 特損推定（経常利益 - 純利益）---
    agg["SPECIAL_LOSS_EST"] = None
    mask_profit = agg["ORDINARY_PROFIT"].notna() & agg["PROFIT"].notna()
    agg.loc[mask_profit, "SPECIAL_LOSS_EST"] = (
        agg.loc[mask_profit, "ORDINARY_PROFIT"] - agg.loc[mask_profit, "PROFIT"]
    )
    # 経常利益に対する比率
    agg["SPECIAL_LOSS_RATIO"] = None
    mask_ratio = mask_profit & (agg["ORDINARY_PROFIT"].abs() > 0)
    agg.loc[mask_ratio, "SPECIAL_LOSS_RATIO"] = (
        agg.loc[mask_ratio, "SPECIAL_LOSS_EST"] / agg.loc[mask_ratio, "ORDINARY_PROFIT"].abs()
    )

    # --- 配当未定判定 ---
    agg["DIVIDEND_UNDECIDED"] = agg["FORECAST_DIVIDEND_PER_SHARE_ANNUAL"].isna()

    # --- 上場廃止TOB突合 ---
    df_delisted["TICKER"] = df_delisted["TICKER"].astype(str).str.zfill(4)
    delisted_tob = df_delisted[df_delisted["IS_TOB_MBO"] == True][["TICKER"]].drop_duplicates()  # noqa: E712
    delisted_tob["IS_DELISTED_TOB"] = True
    agg = agg.merge(delisted_tob, on="TICKER", how="left")
    agg["IS_DELISTED_TOB"] = agg["IS_DELISTED_TOB"].fillna(False)

    # --- 銘柄名補完 ---
    df_codelist["TICKER"] = df_codelist["TICKER"].astype(str).str.zfill(4)
    agg = agg.merge(df_codelist[["TICKER", "STOCK_NAME", "INDUSTRY_33_CATEGORY"]],
                     on="TICKER", how="left")

    # --- スコアリング ---
    agg["SCORE"] = agg.apply(_score, axis=1)

    # --- 出力 ---
    output_cols = [
        "TICKER", "STOCK_NAME", "COMPANY", "INDUSTRY_33_CATEGORY",
        "SCORE",
        "PBR", "SPECIAL_LOSS_EST", "SPECIAL_LOSS_RATIO",
        "DIVIDEND_UNDECIDED",
        "FILING_COUNT", "MAX_DELAY_DAYS", "MEAN_DELAY_DAYS",
        "LATEST_FILING_DATE", "DAYS_SINCE_LATEST_FILING",
        "REPORTERS",
        "ADJ_CLOSE", "BOOK_VALUE_PER_SHARE",
        "ORDINARY_PROFIT", "PROFIT",
        "IS_DELISTED_TOB",
    ]
    # 存在するカラムのみ
    output_cols = [c for c in output_cols if c in agg.columns]
    result = agg[output_cols].sort_values("SCORE", ascending=False)

    # 既にTOB済みを末尾に
    result = result[result["SCORE"] >= 0]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    # --- サマリ表示 ---
    log.info("screening_complete", total=len(result))
    print("\n" + "=" * 80)
    print("EDINET遅延報告 TOBスクリーニング結果")
    print("=" * 80)
    print(f"対象銘柄: {len(result)}社 / 遅延報告: {len(df_excel)}件")
    print(f"出力: {OUTPUT_CSV}")
    print()

    # TOP20表示
    top = result.head(20)
    for _, row in top.iterrows():
        name = str(row.get("STOCK_NAME") or row.get("COMPANY") or "")
        pbr_str = f"PBR={row['PBR']:.2f}" if pd.notna(row.get("PBR")) else "PBR=N/A"
        sl_str = ""
        if pd.notna(row.get("SPECIAL_LOSS_EST")) and row["SPECIAL_LOSS_EST"] > 0:
            sl_str = f" 特損推定={row['SPECIAL_LOSS_EST']/1e6:.0f}百万"
        div_str = " 配当未定" if row.get("DIVIDEND_UNDECIDED") else ""
        print(
            f"  [{row['TICKER']}] {name:<20s} "
            f"SCORE={row['SCORE']:.1f}  {pbr_str}{sl_str}{div_str}  "
            f"遅延{row['FILING_COUNT']}件(max {row['MAX_DELAY_DAYS']:.0f}日)  "
            f"最新提出:{str(row.get('LATEST_FILING_DATE', ''))[:10]}"
        )

    # 統計
    print()
    print("--- 統計 ---")
    print(f"PBR < 1.0: {(result['PBR'].dropna() < 1.0).sum()}社")
    print(f"特損推定あり: {(result['SPECIAL_LOSS_RATIO'].dropna() > SPECIAL_LOSS_RATIO).sum()}社")
    print(f"配当未定: {result['DIVIDEND_UNDECIDED'].sum()}社")
    past_tob = df_delisted[df_delisted["IS_TOB_MBO"] == True]["TICKER"].nunique()  # noqa: E712
    matched_tob = agg[agg["IS_DELISTED_TOB"] == True]["TICKER"].nunique()  # noqa: E712
    print(f"過去TOB/MBO済み: {matched_tob}社（全上場廃止TOB: {past_tob}社）")


if __name__ == "__main__":
    main()

"""EDINET遅延報告 イベントスタディ型バックテスト.

遅延報告提出日をイベントとし、その後のリターン・TOB発生率を検証する。

設計:
  - シグナル: 遅延報告の提出日（Excel FILING_DATE）
  - エントリー: 提出日の翌営業日 寄り付き（ルックアヘッド回避）
  - ホールド: 60 / 120 / 250 営業日
  - ベンチマーク: TOPIX（BQ INDEX_PRICE）
  - 評価: CAR（累積異常リターン）、TOB発生率、勝率、MaxDD

データソース:
  - Excel: Edinet遅延.xlsx（2,081件）
  - BQ: STOCK.STOCK_PRICE_JQUANTS（個別株価）
  - BQ: STOCK.INDEX_PRICE（TOPIX）
  - BQ: STOCK.DELISTED_STOCKS（TOB/MBO判定）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import structlog
import numpy as np
import pandas as pd

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

from google.cloud import bigquery
from google.oauth2 import service_account

log = structlog.get_logger()

# --- 設定 ---
EXCEL_PATH = Path(r"C:\Users\zonekun\Dropbox\stock\AI分析優待\Edinet遅延.xlsx")
SCREENING_CSV = Path(r"C:\tmp\edinet_delay_screen\screening_result.csv")
CACHE_DIR = Path(r"C:\tmp\edinet_delay_backtest")
OUTPUT_CSV = CACHE_DIR / "backtest_result.csv"
FORCE_RELOAD = False

# ホールド期間（営業日）
HOLD_PERIODS = [60, 120, 250]

# 訓練・テスト分割
TRAIN_END = "2023-06-30"  # 前半60%
TEST_START = "2023-07-01"  # 後半40%

_CACHE = {
    "price": CACHE_DIR / "stock_price_all.csv",
    "topix": CACHE_DIR / "topix.csv",
    "delisted": CACHE_DIR / "delisted_stocks.csv",
    "activist": CACHE_DIR / "activist_tickers.csv",
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
    """BQからバックテスト用データを一括ダウンロード."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    queries = {
        "price": """
            SELECT TICKER, DATE, ADJ_CLOSE
            FROM `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS`
            WHERE DATE >= '2021-01-01'
              AND ADJ_CLOSE IS NOT NULL
              AND ADJ_CLOSE > 0
            ORDER BY TICKER, DATE
        """,
        "topix": """
            SELECT DATE, CLOSE AS TOPIX_CLOSE
            FROM `gmailpj-357912.STOCK.INDEX_PRICE`
            WHERE INDEX_CODE = '0000'
              AND DATE >= '2021-01-01'
            ORDER BY DATE
        """,
        "delisted": """
            SELECT TICKER, DELISTING_DATE, IS_TOB_MBO
            FROM `gmailpj-357912.STOCK.DELISTED_STOCKS`
            WHERE IS_TOB_MBO = TRUE
        """,
        "activist": """
            SELECT DISTINCT TICKER
            FROM `gmailpj-357912.STOCK.SHAREHOLDER_COMPOSITION`
            WHERE HAS_ACTIVIST = TRUE
        """,
    }

    for name, query in queries.items():
        log.info("bq_download", table=name)
        df = client.query(query).to_dataframe()
        df.to_csv(_CACHE[name], index=False)
        log.info("bq_saved", table=name, rows=len(df))


def _build_activist_tickers() -> set[str]:
    """BQ SHAREHOLDER_COMPOSITIONベースでアクティビスト保有銘柄セットを返す."""
    df = pd.read_csv(_CACHE["activist"], dtype=str)
    df["TICKER"] = df["TICKER"].astype(str).str.zfill(4)
    tickers = set(df["TICKER"])
    log.info("activist_tickers", count=len(tickers))
    return tickers


def _load_excel() -> pd.DataFrame:
    """Excel読み込み（銘柄×提出日のイベントリスト）."""
    df = pd.read_excel(EXCEL_PATH, sheet_name="MAIN", header=None)
    df.columns = ["TICKER", "COMPANY", "REPORTER", "OBLIGATION_DATE", "FILING_DATE", "DELAY_DAYS_STR"]
    df["TICKER"] = df["TICKER"].astype(str).str.zfill(4)
    df["FILING_DATE"] = pd.to_datetime(df["FILING_DATE"], errors="coerce")
    df["DELAY_DAYS"] = (
        df["DELAY_DAYS_STR"].astype(str).str.replace("日", "", regex=False)
    )
    df["DELAY_DAYS"] = pd.to_numeric(df["DELAY_DAYS"], errors="coerce")
    return df.dropna(subset=["FILING_DATE"])


def _calc_forward_return(
    price_pivot: pd.DataFrame,
    ticker: str,
    entry_date: pd.Timestamp,
    hold_days: int,
) -> float | None:
    """エントリー日からhold_days後のリターンを計算."""
    if ticker not in price_pivot.columns:
        return None
    ts = price_pivot[ticker].dropna()
    # エントリー日以降の最初の営業日
    valid = ts.index[ts.index >= entry_date]
    if len(valid) < 2:
        return None
    entry_idx = ts.index.get_loc(valid[0])
    exit_idx = entry_idx + hold_days
    if exit_idx >= len(ts):
        return None
    entry_price = ts.iloc[entry_idx]
    exit_price = ts.iloc[exit_idx]
    if entry_price <= 0:
        return None
    return (exit_price / entry_price) - 1.0


def _calc_topix_return(
    topix: pd.Series,
    entry_date: pd.Timestamp,
    hold_days: int,
) -> float | None:
    """TOPIX同期間リターン."""
    valid = topix.index[topix.index >= entry_date]
    if len(valid) < 2:
        return None
    entry_idx = topix.index.get_loc(valid[0])
    exit_idx = entry_idx + hold_days
    if exit_idx >= len(topix):
        return None
    return (topix.iloc[exit_idx] / topix.iloc[entry_idx]) - 1.0


def main() -> None:
    """バックテスト実行."""
    # --- データ準備 ---
    if FORCE_RELOAD or not all(p.exists() for p in _CACHE.values()):
        client = _get_bq_client()
        _download_all(client)
    else:
        log.info("using_cache")

    df_events = _load_excel()
    df_price = pd.read_csv(_CACHE["price"], parse_dates=["DATE"])
    df_topix = pd.read_csv(_CACHE["topix"], parse_dates=["DATE"])
    df_delisted = pd.read_csv(_CACHE["delisted"])

    log.info("data_loaded", events=len(df_events), price_rows=len(df_price))

    # 株価をpivot
    df_price["TICKER"] = df_price["TICKER"].astype(str).str.zfill(4)
    price_pivot = df_price.pivot_table(index="DATE", columns="TICKER", values="ADJ_CLOSE")

    # TOPIX
    topix = df_topix.set_index("DATE")["TOPIX_CLOSE"]

    # TOB銘柄セット
    df_delisted["TICKER"] = df_delisted["TICKER"].astype(str).str.zfill(4)
    tob_tickers = set(df_delisted["TICKER"])
    tob_dates = dict(zip(df_delisted["TICKER"], pd.to_datetime(df_delisted["DELISTING_DATE"])))

    # アクティビスト保有銘柄セット
    activist_tickers = _build_activist_tickers()

    # スクリーニングスコア読み込み
    score_map: dict[str, float] = {}
    if SCREENING_CSV.exists():
        df_screen = pd.read_csv(SCREENING_CSV)
        df_screen["TICKER"] = df_screen["TICKER"].astype(str).str.zfill(4)
        score_map = dict(zip(df_screen["TICKER"], df_screen["SCORE"]))
        log.info("screening_scores_loaded", tickers=len(score_map))
    else:
        log.warning("screening_csv_not_found", path=str(SCREENING_CSV))

    # --- イベントごとにリターン計算 ---
    # イベントを銘柄×提出日でユニーク化（同日複数報告は1イベント）
    events = df_events.groupby(["TICKER", "FILING_DATE"]).agg(
        DELAY_DAYS=("DELAY_DAYS", "max"),
        FILING_COUNT=("TICKER", "count"),
    ).reset_index()
    events["ENTRY_DATE"] = events["FILING_DATE"] + pd.Timedelta(days=1)

    log.info("unique_events", count=len(events))

    results = []
    for _, ev in events.iterrows():
        row = {
            "TICKER": ev["TICKER"],
            "FILING_DATE": ev["FILING_DATE"],
            "ENTRY_DATE": ev["ENTRY_DATE"],
            "DELAY_DAYS": ev["DELAY_DAYS"],
            "IS_TOB": ev["TICKER"] in tob_tickers,
            "HAS_ACTIVIST": ev["TICKER"] in activist_tickers,
            "SCORE": score_map.get(ev["TICKER"], 0.0),
            "PERIOD": "TRAIN" if ev["FILING_DATE"] <= pd.Timestamp(TRAIN_END) else "TEST",
        }
        # TOB発生日がイベント後かチェック
        if ev["TICKER"] in tob_dates:
            tob_dt = tob_dates[ev["TICKER"]]
            row["TOB_AFTER_EVENT"] = tob_dt > ev["FILING_DATE"]
            row["DAYS_TO_TOB"] = (tob_dt - ev["FILING_DATE"]).days if tob_dt > ev["FILING_DATE"] else None
        else:
            row["TOB_AFTER_EVENT"] = False
            row["DAYS_TO_TOB"] = None

        for hold in HOLD_PERIODS:
            ret = _calc_forward_return(price_pivot, ev["TICKER"], ev["ENTRY_DATE"], hold)
            topix_ret = _calc_topix_return(topix, ev["ENTRY_DATE"], hold)
            row[f"RET_{hold}D"] = ret
            row[f"TOPIX_{hold}D"] = topix_ret
            row[f"CAR_{hold}D"] = (ret - topix_ret) if ret is not None and topix_ret is not None else None

        results.append(row)

    df_result = pd.DataFrame(results)

    # --- 結果保存 ---
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df_result.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    # --- サマリ出力 ---
    print("\n" + "=" * 80)
    print("EDINET遅延報告 バックテスト結果")
    print("=" * 80)
    print(f"イベント数: {len(df_result)} (TRAIN={len(df_result[df_result['PERIOD']=='TRAIN'])}, "
          f"TEST={len(df_result[df_result['PERIOD']=='TEST'])})")
    print(f"出力: {OUTPUT_CSV}\n")

    for period in ["ALL", "TRAIN", "TEST"]:
        subset = df_result if period == "ALL" else df_result[df_result["PERIOD"] == period]
        if len(subset) == 0:
            continue
        print(f"\n--- {period} ({len(subset)}イベント) ---")

        # TOB発生率
        tob_after = subset["TOB_AFTER_EVENT"].sum()
        print(f"  TOBがイベント後に発生: {tob_after}/{len(subset)} = {tob_after/len(subset)*100:.1f}%")

        # 各ホールド期間のCAR
        for hold in HOLD_PERIODS:
            car_col = f"CAR_{hold}D"
            ret_col = f"RET_{hold}D"
            valid = subset[car_col].dropna()
            valid_ret = subset[ret_col].dropna()
            if len(valid) == 0:
                continue

            mean_car = valid.mean()
            median_car = valid.median()
            win_rate = (valid > 0).sum() / len(valid)
            mean_ret = valid_ret.mean()

            # TOB銘柄のみ
            tob_sub = subset[subset["TOB_AFTER_EVENT"]][car_col].dropna()
            tob_car = tob_sub.mean() if len(tob_sub) > 0 else float("nan")

            # 非TOB銘柄
            non_tob_sub = subset[~subset["TOB_AFTER_EVENT"]][car_col].dropna()
            non_tob_car = non_tob_sub.mean() if len(non_tob_sub) > 0 else float("nan")

            print(f"\n  [{hold}日ホールド] (有効={len(valid)}件)")
            print(f"    平均リターン:  {mean_ret*100:+.2f}%")
            print(f"    平均CAR:      {mean_car*100:+.2f}%  (中央値: {median_car*100:+.2f}%)")
            print(f"    勝率(CAR>0):  {win_rate*100:.1f}%")
            print(f"    TOB銘柄CAR:   {tob_car*100:+.2f}% ({len(tob_sub)}件)")
            print(f"    非TOB銘柄CAR: {non_tob_car*100:+.2f}% ({len(non_tob_sub)}件)")

    # アクティビスト有無別の分析
    print("\n\n--- アクティビスト株主 有無別 ---")
    act_count = df_result["HAS_ACTIVIST"].sum()
    print(f"  アクティビスト保有: {act_count}/{len(df_result)}件 ({act_count/len(df_result)*100:.1f}%)")
    for label, filt in [("アクティビストあり", True), ("アクティビストなし", False)]:
        sub = df_result[df_result["HAS_ACTIVIST"] == filt]
        if len(sub) == 0:
            continue
        tob_n = sub["TOB_AFTER_EVENT"].sum()
        print(f"\n  [{label}] ({len(sub)}件, TOB発生={tob_n}件={tob_n/len(sub)*100:.1f}%)")
        for hold in HOLD_PERIODS:
            car = sub[f"CAR_{hold}D"].dropna()
            if len(car) == 0:
                continue
            print(f"    {hold}日: 平均CAR={car.mean()*100:+.2f}%, "
                  f"中央値={car.median()*100:+.2f}%, "
                  f"勝率={((car>0).sum()/len(car))*100:.1f}%, N={len(car)}")

    # 遅延日数別の分析
    print("\n\n--- 遅延日数別CAR(250D) ---")
    for label, lo, hi in [("60-180日", 60, 180), ("180-365日", 180, 365), ("365日+", 365, 99999)]:
        sub = df_result[(df_result["DELAY_DAYS"] >= lo) & (df_result["DELAY_DAYS"] < hi)]
        car = sub["CAR_250D"].dropna()
        if len(car) > 0:
            print(f"  {label}: 平均CAR={car.mean()*100:+.2f}%, 中央値={car.median()*100:+.2f}%, N={len(car)}")

    # TOB発生までの日数分布
    print("\n--- TOBイベント後の日数分布 ---")
    tob_days = df_result[df_result["TOB_AFTER_EVENT"]]["DAYS_TO_TOB"].dropna()
    if len(tob_days) > 0:
        print(f"  件数: {len(tob_days)}")
        print(f"  平均: {tob_days.mean():.0f}日, 中央値: {tob_days.median():.0f}日")
        print(f"  最短: {tob_days.min():.0f}日, 最長: {tob_days.max():.0f}日")
        # 250日以内にTOB発生した割合
        within_250 = (tob_days <= 250).sum()
        print(f"  250日以内: {within_250}/{len(tob_days)} = {within_250/len(tob_days)*100:.1f}%")

    # === 複合条件分析（SCORE × アクティビスト） ===
    if score_map:
        print("\n\n" + "=" * 80)
        print("複合条件分析: SCORE × アクティビスト")
        print("=" * 80)

        for score_th in [8, 10, 12]:
            conditions = [
                (f"SCORE≥{score_th}", df_result["SCORE"] >= score_th),
                (f"SCORE≥{score_th} × アクティビスト", (df_result["SCORE"] >= score_th) & df_result["HAS_ACTIVIST"]),
                (f"SCORE≥{score_th} × 非アクティビスト", (df_result["SCORE"] >= score_th) & ~df_result["HAS_ACTIVIST"]),
            ]
            print(f"\n--- SCORE閾値={score_th} ---")
            for label, mask in conditions:
                sub = df_result[mask]
                if len(sub) == 0:
                    print(f"  [{label}] 該当なし")
                    continue
                tob_n = sub["TOB_AFTER_EVENT"].sum()
                tickers_unique = sub["TICKER"].nunique()
                print(f"\n  [{label}] ({len(sub)}件, {tickers_unique}銘柄, TOB={tob_n}件={tob_n/len(sub)*100:.1f}%)")
                for hold in HOLD_PERIODS:
                    car = sub[f"CAR_{hold}D"].dropna()
                    if len(car) == 0:
                        continue
                    print(f"    {hold}日: 平均CAR={car.mean()*100:+.2f}%, "
                          f"中央値={car.median()*100:+.2f}%, "
                          f"勝率={((car>0).sum()/len(car))*100:.1f}%, N={len(car)}")

        # SCORE≥10 × アクティビストの銘柄一覧
        combo = df_result[(df_result["SCORE"] >= 10) & df_result["HAS_ACTIVIST"]]
        if len(combo) > 0:
            print(f"\n--- SCORE≥10 × アクティビスト 銘柄一覧 ({combo['TICKER'].nunique()}銘柄) ---")
            for ticker in sorted(combo["TICKER"].unique()):
                t_sub = combo[combo["TICKER"] == ticker]
                car60 = t_sub["CAR_60D"].dropna()
                car_str = f"60D CAR={car60.mean()*100:+.2f}%" if len(car60) > 0 else "CAR=N/A"
                tob_str = " [TOB]" if t_sub["TOB_AFTER_EVENT"].any() else ""
                print(f"  {ticker} ({len(t_sub)}件) {car_str}{tob_str}")


if __name__ == "__main__":
    main()

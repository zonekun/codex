# -*- coding: utf-8 -*-
"""BB_債券履歴_new.xlsx -> data/csv/bond_history.csv 変換スクリプト.

実行タイミング: 明示的な指示があったときのみ。
直近90日はクレンジング未完了の可能性あり（データは保持するが分析時は除外推奨）。
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

SRC = Path(r"C:\Users\zonekun\Dropbox\stock\BB_債券履歴_new.xlsx")
DST = Path(__file__).parent.parent / "data" / "csv" / "bond_history.csv"

# 使用する列インデックスとCSVカラム名のマッピング
COLUMNS = {
    0:  "DATE",
    1:  "BEI",
    2:  "US10Y",
    3:  "US5Y",
    4:  "US2Y",
    5:  "SP500_DIV_YIELD",
    6:  "AAA_YIELD",
    7:  "AAA_SPREAD",
    8:  "USDJPY",
    9:  "HYG",
    10: "USDX",
    11: "SOX",
    12: "BADI",
    13: "CRB",
    14: "SKEW",
    15: "DOW",
    17: "NASDAQ",
    19: "VIX",
    23: "SP500",
    26: "JP10Y",
}


def main() -> None:
    print(f"Source: {SRC}")
    print(f"Dest  : {DST}")

    # 1行目ヘッダー、2〜5行目スキップ、6行目以降データ
    raw = pd.read_excel(SRC, sheet_name="LIST", header=0, skiprows=[1, 2, 3, 4], engine="openpyxl")
    print(f"Raw shape: {raw.shape}")

    # 必要列だけ抽出してリネーム
    df = raw.iloc[:, list(COLUMNS.keys())].copy()
    df.columns = list(COLUMNS.values())

    # DATE列を日付型に変換・無効行を除去
    df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
    before = len(df)
    df = df.dropna(subset=["DATE"])
    df["DATE"] = df["DATE"].dt.date
    df = df.sort_values("DATE").reset_index(drop=True)
    print(f"Valid rows: {len(df)} (dropped {before - len(df)} invalid)")

    # 小数点以下2桁に丸める列
    for col in ["USDJPY", "USDX"]:
        df[col] = df[col].round(2)

    # 直近90日の警告
    cutoff = date.today() - timedelta(days=90)
    n_recent = (df["DATE"] > cutoff).sum()
    print(f"Warning: {n_recent} rows in last 90 days (after {cutoff}) may be uncleaned.")

    # 保存
    DST.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(DST, index=False, encoding="utf-8")

    date_min = df["DATE"].min()
    date_max = df["DATE"].max()
    print(f"Saved: {len(df)} rows, {date_min} to {date_max} -> {DST}")


if __name__ == "__main__":
    main()

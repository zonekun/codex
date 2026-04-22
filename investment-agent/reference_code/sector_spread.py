"""セクター別スプレッド分析の参考コード.

品貸料（逆日歩）が発生している銘柄と発生していない銘柄の
セクター別リターンスプレッドを計算する。
"""
from __future__ import annotations

import pandas as pd
import numpy as np


def calc_sector_spread(
    prices: pd.DataFrame,
    gyakuhibu: pd.DataFrame,
    sector_map: dict[str, str],
) -> pd.DataFrame:
    """セクター別の品貸料あり/なしリターンスプレッドを計算.

    Args:
        prices: 日次株価データ (columns: ticker, date, close)
        gyakuhibu: 逆日歩データ (columns: ticker, date, rate)
        sector_map: {ticker: sector} のマッピング

    Returns:
        セクター別スプレッド
    """
    # リターン計算
    returns = prices.pivot(index="date", columns="ticker", values="close").pct_change()

    # 逆日歩フラグ
    has_gyakuhibu = gyakuhibu.pivot(index="date", columns="ticker", values="rate").fillna(0) > 0

    # セクター別に集計
    results = []
    for sector in set(sector_map.values()):
        tickers = [t for t, s in sector_map.items() if s == sector]
        sect_returns = returns[tickers] if tickers else pd.DataFrame()
        if sect_returns.empty:
            continue

        for date in sect_returns.index:
            has_flag = [t for t in tickers if has_gyakuhibu.get(t, pd.Series(False)).get(date, False)]
            no_flag = [t for t in tickers if t not in has_flag]

            if has_flag and no_flag:
                spread = sect_returns.loc[date, has_flag].mean() - sect_returns.loc[date, no_flag].mean()
                results.append({"date": date, "sector": sector, "spread": spread})

    return pd.DataFrame(results)

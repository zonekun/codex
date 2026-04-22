"""逆日歩ランク別分析の参考コード.

逆日歩の大きさ（ランク）別に銘柄を分類し、
各グループの株価推移を比較する。
"""
from __future__ import annotations

import pandas as pd
import numpy as np


def rank_analysis(
    prices: pd.DataFrame,
    gyakuhibu: pd.DataFrame,
    n_groups: int = 3,
    forward_days: int = 20,
) -> pd.DataFrame:
    """逆日歩ランク別のフォワードリターンを計算.

    Args:
        prices: 日次株価 (columns: ticker, date, close)
        gyakuhibu: 逆日歩データ (columns: ticker, date, rate)
        n_groups: ランク分割数（例: 3 = 高/中/低）
        forward_days: フォワードリターン計算日数

    Returns:
        ランク別の平均フォワードリターン
    """
    merged = prices.merge(gyakuhibu, on=["ticker", "date"], how="left")
    merged["rate"] = merged["rate"].fillna(0)

    # ランク分け（0=逆日歩なし, 1=低, 2=中, 3=高）
    mask = merged["rate"] > 0
    merged.loc[~mask, "rank"] = 0
    merged.loc[mask, "rank"] = pd.qcut(
        merged.loc[mask, "rate"], q=n_groups, labels=range(1, n_groups + 1)
    )

    # フォワードリターン計算
    pivot = prices.pivot(index="date", columns="ticker", values="close")
    fwd_return = pivot.shift(-forward_days) / pivot - 1

    # TODO: ランク別集計
    # ...

    return merged

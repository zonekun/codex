"""バックテストエンジン."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.core.config import app_config
from src.core.logger import get_logger

log = get_logger(__name__)


@dataclass
class BacktestResult:
    """バックテスト結果."""

    total_return: float
    annual_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    equity_curve: pd.Series
    trades: pd.DataFrame

    def passes_criteria(self) -> bool:
        """バックテスト基準をクリアしているか."""
        cfg = app_config["backtest"]
        return (
            self.sharpe_ratio >= cfg["min_sharpe_ratio"]
            and self.win_rate >= cfg["min_win_rate"]
            and self.max_drawdown <= cfg["max_drawdown"]
        )


class BacktestEngine:
    """ベクトル化バックテストエンジン."""

    def __init__(self) -> None:
        cfg = app_config["backtest"]
        self.initial_capital: float = cfg["initial_capital"]
        self.commission_rate: float = cfg["commission_rate"]
        self.slippage_bps: float = cfg["slippage_bps"]

    def run(self, prices: pd.DataFrame, signals: pd.Series) -> BacktestResult:
        """バックテストを実行する.

        Args:
            prices: 株価データ（OHLCV）
            signals: シグナル（1=買い, -1=売り, 0=ホールド）

        Returns:
            BacktestResult
        """
        log.info("backtest_start", num_days=len(prices))
        # TODO: 実装
        raise NotImplementedError("バックテストエンジンは未実装です")

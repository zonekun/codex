"""外部API統合クライアント.

J-Quants, yfinance等のAPIを呼び出し、キャッシュ機構と連携する。
"""
from __future__ import annotations

import pandas as pd

from src.core.logger import get_logger
from src.datastore.cache import DataCache

log = get_logger(__name__)


class APIClient:
    """外部API統合クライアント（キャッシュ付き）."""

    def __init__(self) -> None:
        self.cache = DataCache()

    async def fetch_jquants_prices(
        self, ticker: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """J-Quants APIで株価データを取得する（キャッシュ対応）."""
        cache_key = f"jquants_price_{ticker}_{start_date}_{end_date}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            log.info("jquants_cache_hit", ticker=ticker)
            return cached

        log.info("jquants_fetch", ticker=ticker, start=start_date, end=end_date)
        # TODO: J-Quants API呼び出し実装
        raise NotImplementedError

    async def fetch_yfinance_prices(
        self, ticker: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """yfinanceで株価データを取得する（キャッシュ対応）."""
        cache_key = f"yfinance_price_{ticker}_{start_date}_{end_date}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            log.info("yfinance_cache_hit", ticker=ticker)
            return cached

        log.info("yfinance_fetch", ticker=ticker)
        # TODO: yfinance呼び出し実装
        # import yfinance as yf
        # data = yf.download(f"{ticker}.T", start=start_date, end=end_date)
        raise NotImplementedError

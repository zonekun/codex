"""J-Quants/BQ クライアント — VCP スクリーナー用 FMPClient 互換ラッパー.

FMPClient (fmp_client.py) と同一インターフェースを持ち、東証銘柄を対象とする。
データソースは BigQuery (STOCK.STOCK_PRICE_JQUANTS / STOCK.STOCK_CODE_LIST /
STOCK.INDEX_PRICE) および parquet キャッシュ。

対応表:
  FMPClient.get_sp500_constituents()  → TSE 全銘柄 (STOCK_CODE_LIST WHERE EXCHANGE='TSE')
  FMPClient.get_batch_quotes()        → OHLCV 最終日から price / yearHigh / yearLow / avgVolume
  FMPClient.get_historical_prices()   → STOCK_PRICE_JQUANTS (1 銘柄 N 日分)
  FMPClient.get_historical_prices("SPY") → TOPIX (INDEX_PRICE WHERE INDEX_CODE='0000')
  FMPClient.get_batch_historical()    → get_historical_prices の複数銘柄版
  FMPClient.get_api_stats()           → ダミー統計

Usage:
    from jquants_bq_client import JQuantsBQClient
    client = JQuantsBQClient()
    constituents = client.get_sp500_constituents()
    quotes = client.get_batch_quotes(["8141", "8163"])
    hist = client.get_historical_prices("8141", days=260)

参照:
  - docs/plans/analysis-015_insider_pattern_mismatch_20260521_205454.md Step i-a
  - C:/tmp/claude-trading-skills/skills/vcp-screener/scripts/fmp_client.py
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import structlog
from google.cloud import bigquery

# screen_tob_insider.py の fetch_ohlcv / fetch_topix / _get_bq を再利用
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "tob_prediction"))

from screen_tob_insider import (  # noqa: E402
    BQ_PROJECT,
    FETCH_DAYS,
    TABLE_MASTER,
    _get_bq,
    fetch_ohlcv,
    fetch_topix,
)

log = structlog.get_logger()
JST = timezone(timedelta(hours=+9), "JST")

# 上場廃止銘柄マスタ（STOCK_CODE_LIST に存在しない廃止済み銘柄の補充用）
TABLE_PRICE: str = f"{BQ_PROJECT}.STOCK.STOCK_PRICE_JQUANTS"
TABLE_DELISTED: str = f"{BQ_PROJECT}.STOCK.DELISTED_STOCKS"

# 52 週高値・安値の計算期間（取引日）
YEAR_TRADING_DAYS: int = 260
# 平均出来高の計算期間（取引日）
AVG_VOL_DAYS: int = 65
# screen_vcp.py が TOPIX をリクエストする際のシンボル名（FMP 側は SPY を使う）
_SPY_ALIAS: str = "SPY"


class JQuantsBQClient:
    """J-Quants/BQ 東証スクリーナークライアント — FMPClient 互換.

    BQ クエリは __init__ で 1 回のみ実行し parquet キャッシュを利用する。
    その後の get_* 呼び出しはすべてインメモリ処理。

    Attributes:
        _ohlcv: TSE 全銘柄 OHLCV DataFrame (DATE, TICKER, STOCK_NAME, ADJ_*)
        _topix: TOPIX 日次終値 DataFrame (DATE, TOPIX_CLOSE)
        _master: 銘柄マスタ DataFrame (TICKER, STOCK_NAME, INDUSTRY_33_CATEGORY, ...)
        _cache: インメモリキャッシュ
        _api_calls: BQ クエリ発行回数
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        date_to: Optional[str] = None,
        fetch_days: int = FETCH_DAYS,
    ) -> None:
        """初期化 — BQ から OHLCV / TOPIX / 銘柄マスタを一括取得.

        Args:
            api_key: FMPClient 互換パラメータ（未使用）。
            date_to: 取得終了日 YYYY-MM-DD。省略時は今日 JST。
            fetch_days: 取得カレンダー日数。デフォルト 400 日 (≈280 取引日)。
                `get_batch_quotes` の yearHigh / yearLow は最大 YEAR_TRADING_DAYS=260
                取引日のデータから計算するため、fetch_days は最低 400 カレンダー日
                (≈280 取引日) が必要。過去日バックテスト時（--date を 1 年以上前に
                指定）も fetch_days=400 で十分だが、date_to が極めて古い場合は
                明示的に fetch_days を大きくすること。
        """
        self._date_to: str = date_to or datetime.now(tz=JST).date().isoformat()

        # バックテスト時: fetch_days がデフォルトでも 52 週分が確保できるよう
        # 最低 400 カレンダー日を保証する（400 日 ≈ 280 取引日 > YEAR_TRADING_DAYS=260）
        effective_fetch_days = max(fetch_days, 400)
        self._date_from: str = (
            date.fromisoformat(self._date_to) - timedelta(days=effective_fetch_days)
        ).isoformat()
        self._cache: dict = {}
        self._api_calls: int = 0

        log.info(
            "jquants_bq_client_init",
            date_from=self._date_from,
            date_to=self._date_to,
            fetch_days=fetch_days,
        )

        # BQ から全データ一括取得（parquet キャッシュあれば再利用）
        self._ohlcv: pd.DataFrame = fetch_ohlcv(self._date_from, self._date_to)
        self._topix: pd.DataFrame = fetch_topix(self._date_from, self._date_to)

        # 銘柄マスタ（sector / name 用）
        self._master: pd.DataFrame = self._fetch_master()

        # STOCK_CODE_LIST に存在しない廃止済み銘柄を DELISTED_STOCKS から補充
        # （バックテスト時に「評価日には上場していたが現在廃止済み」の銘柄をカバーする）
        self._supplement_delisted()

        log.info(
            "jquants_bq_client_ready",
            tickers=self._ohlcv["TICKER"].nunique(),
            ohlcv_rows=len(self._ohlcv),
            topix_rows=len(self._topix),
            master_rows=len(self._master),
        )

    # -------------------------------------------------------------------------
    # 内部ヘルパー
    # -------------------------------------------------------------------------

    def _fetch_master(self) -> pd.DataFrame:
        """STOCK_CODE_LIST から TSE 銘柄マスタを BQ 取得.

        Returns:
            TICKER, STOCK_NAME, INDUSTRY_33_CATEGORY, MARKET_CATEGORY 列を持つ DataFrame。
            BQ 取得失敗時は OHLCV から TICKER / STOCK_NAME のみのフォールバックを返す。
        """
        cache_key = "master_tse"
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]

        sql = f"""
        SELECT
            TICKER,
            STOCK_NAME,
            INDUSTRY_33_CATEGORY,
            MARKET_CATEGORY
        FROM `{TABLE_MASTER}`
        WHERE EXCHANGE = 'TSE'
        """
        try:
            df = _get_bq().query(sql).to_dataframe()
            df["TICKER"] = df["TICKER"].astype(str)
            self._api_calls += 1
            log.info("master_fetch_done", rows=len(df))
        except Exception as exc:  # pylint: disable=broad-except
            log.warning("master_fetch_failed_fallback", error=str(exc))
            # フォールバック: OHLCV の TICKER / STOCK_NAME から構築
            df = (
                self._ohlcv[["TICKER", "STOCK_NAME"]]
                .drop_duplicates("TICKER")
                .copy()
            )
            df["INDUSTRY_33_CATEGORY"] = None
            df["MARKET_CATEGORY"] = None

        self._cache[cache_key] = df
        return df

    def _supplement_delisted(self) -> None:
        """上場廃止済み銘柄の OHLCV / マスタを self に追記する.

        STOCK_CODE_LIST の INNER JOIN で除外された廃止済み銘柄を
        DELISTED_STOCKS + STOCK_PRICE_JQUANTS から補充する。
        バックテスト時に「評価日には上場していたが現在廃止済み」のケース（例: TOB
        ゴールデンサンプル）を検出可能にする。

        DELISTED_STOCKS.DELISTING_DATE >= date_from の銘柄を対象とする
        （date_from 時点で上場していた可能性があるもののみ）。
        """
        existing_tickers = set(self._ohlcv["TICKER"].unique())

        # date_from 以降に廃止された銘柄を DELISTED_STOCKS から取得
        sql_delisted = f"""
        SELECT TICKER, COMPANY_NAME, MARKET_SEGMENT
        FROM `{TABLE_DELISTED}`
        WHERE DELISTING_DATE >= @date_from
        """
        cfg = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("date_from", "DATE", self._date_from),
        ])
        try:
            delisted_df = _get_bq().query(sql_delisted, job_config=cfg).to_dataframe()
            self._api_calls += 1
        except Exception as exc:  # pylint: disable=broad-except
            log.warning("supplement_delisted_query_failed", error=str(exc))
            return

        delisted_df["TICKER"] = delisted_df["TICKER"].astype(str)
        # 既に OHLCV に存在する銘柄はスキップ
        missing_tickers = [
            t for t in delisted_df["TICKER"].unique()
            if t not in existing_tickers
        ]
        if not missing_tickers:
            log.debug("no_delisted_supplement_needed")
            return

        log.info("supplementing_delisted_ohlcv", count=len(missing_tickers))

        # STOCK_PRICE_JQUANTS から直接取得（STOCK_CODE_LIST join なし）
        sql_ohlcv = f"""
        SELECT
          CAST(DATE AS STRING) AS DATE,
          TICKER,
          ADJ_OPEN,
          ADJ_HIGH,
          ADJ_LOW,
          ADJ_CLOSE,
          ADJ_VOLUME
        FROM `{TABLE_PRICE}`
        WHERE DATE BETWEEN @date_from AND @date_to
          AND TICKER IN UNNEST(@tickers)
          AND IS_PREFERRED = FALSE
          AND ADJ_CLOSE IS NOT NULL
          AND ADJ_VOLUME IS NOT NULL
        ORDER BY TICKER, DATE
        """
        cfg2 = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("date_from", "DATE", self._date_from),
            bigquery.ScalarQueryParameter("date_to", "DATE", self._date_to),
            bigquery.ArrayQueryParameter("tickers", "STRING", missing_tickers),
        ])
        try:
            extra_ohlcv = _get_bq().query(sql_ohlcv, job_config=cfg2).to_dataframe()
            self._api_calls += 1
        except Exception as exc:  # pylint: disable=broad-except
            log.warning("supplement_ohlcv_fetch_failed", error=str(exc))
            return

        if extra_ohlcv.empty:
            log.info("supplement_delisted_empty_ohlcv")
            return

        # DELISTED_STOCKS から STOCK_NAME を補完
        name_map = dict(
            zip(
                delisted_df["TICKER"],
                delisted_df["COMPANY_NAME"].fillna("").astype(str),
            )
        )
        extra_ohlcv["TICKER"] = extra_ohlcv["TICKER"].astype(str)
        extra_ohlcv["DATE"] = extra_ohlcv["DATE"].astype(str)
        extra_ohlcv["STOCK_NAME"] = extra_ohlcv["TICKER"].map(name_map).fillna("")

        # 列順を self._ohlcv に合わせる
        for col in self._ohlcv.columns:
            if col not in extra_ohlcv.columns:
                extra_ohlcv[col] = None
        extra_ohlcv = extra_ohlcv[self._ohlcv.columns]

        self._ohlcv = pd.concat([self._ohlcv, extra_ohlcv], ignore_index=True)

        # self._master にも廃止銘柄を追記（get_sp500_constituents に含めるため）
        added = delisted_df[delisted_df["TICKER"].isin(extra_ohlcv["TICKER"].unique())].copy()
        added = added.rename(
            columns={"COMPANY_NAME": "STOCK_NAME", "MARKET_SEGMENT": "MARKET_CATEGORY"}
        )
        added["INDUSTRY_33_CATEGORY"] = "Unknown"
        # _master に必要な列のみ揃える
        for col in self._master.columns:
            if col not in added.columns:
                added[col] = None
        self._master = pd.concat(
            [self._master, added[self._master.columns]], ignore_index=True
        )

        log.info(
            "supplement_delisted_done",
            added_tickers=extra_ohlcv["TICKER"].nunique(),
        )

    def _name_sector_maps(self) -> tuple[dict[str, str], dict[str, str]]:
        """(name_map, sector_map) を返すヘルパー."""
        cache_key = "_name_sector"
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]

        name_map: dict[str, str] = {}
        sector_map: dict[str, str] = {}
        for _, row in self._master.iterrows():
            t = str(row["TICKER"])
            name_map[t] = str(row.get("STOCK_NAME") or t)
            sector_map[t] = str(row.get("INDUSTRY_33_CATEGORY") or "Unknown")

        result = (name_map, sector_map)
        self._cache[cache_key] = result
        return result

    # -------------------------------------------------------------------------
    # FMPClient 互換インターフェース
    # -------------------------------------------------------------------------

    def get_sp500_constituents(self) -> Optional[list[dict]]:
        """TSE 全銘柄リストを返す (FMPClient.get_sp500_constituents 互換).

        Returns:
            [{"symbol": ticker, "name": name, "sector": industry_33,
              "subSector": market_category}, ...] の list。
            取得失敗時は None。
        """
        cache_key = "tse_constituents"
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]

        name_map, sector_map = self._name_sector_maps()

        result: list[dict] = []
        for _, row in self._master.iterrows():
            t = str(row["TICKER"])
            result.append(
                {
                    "symbol": t,
                    "name": name_map.get(t, t),
                    "sector": sector_map.get(t, "Unknown"),
                    "subSector": str(row.get("MARKET_CATEGORY") or ""),
                }
            )

        log.info("tse_constituents_loaded", count=len(result))
        self._cache[cache_key] = result
        return result

    def get_batch_quotes(self, symbols: list[str]) -> dict[str, dict]:
        """各銘柄の最新クォートを返す (FMPClient.get_batch_quotes 互換).

        OHLCV の最終営業日データから price / yearHigh / yearLow / avgVolume を計算する。
        BQ 追加クエリは不要（インメモリ計算）。

        Args:
            symbols: ティッカーリスト。

        Returns:
            {ticker: {"symbol": str, "price": float, "yearHigh": float,
                      "yearLow": float, "avgVolume": float, "marketCap": int,
                      "name": str, "sector": str}} の dict。
        """
        self._api_calls += 1
        name_map, sector_map = self._name_sector_maps()
        results: dict[str, dict] = {}

        target_set = set(str(s) for s in symbols)
        ohlcv_filtered = self._ohlcv[self._ohlcv["TICKER"].isin(target_set)]

        for ticker_raw, grp in ohlcv_filtered.groupby("TICKER"):
            ticker = str(ticker_raw)
            grp = grp.sort_values("DATE")
            if grp.empty:
                continue

            # 最終行 = 最新の取引日
            last = grp.iloc[-1]
            price = float(last["ADJ_CLOSE"])

            # 52 週高値・安値（直近 YEAR_TRADING_DAYS 取引日）
            # データが YEAR_TRADING_DAYS 未満の場合は利用可能な全期間で計算する
            # （過去日バックテスト・新規上場銘柄で発生し得る）
            if len(grp) < YEAR_TRADING_DAYS:
                log.debug(
                    "insufficient_year_data",
                    ticker=ticker,
                    available=len(grp),
                    required=YEAR_TRADING_DAYS,
                )
            recent_year = grp.tail(YEAR_TRADING_DAYS)
            year_high = float(recent_year["ADJ_HIGH"].max())
            year_low = float(recent_year["ADJ_LOW"].min())

            # 平均出来高（AVG_VOL_DAYS 取引日）
            recent_vol = grp.tail(AVG_VOL_DAYS)
            avg_volume = (
                float(recent_vol["ADJ_VOLUME"].mean())
                if not recent_vol.empty
                else 0.0
            )

            results[ticker] = {
                "symbol": ticker,
                "price": price,
                "yearHigh": year_high,
                "yearLow": year_low,
                "avgVolume": avg_volume,
                "marketCap": 0,  # BQ データに時価総額なし
                "name": name_map.get(ticker, ticker),
                "sector": sector_map.get(ticker, "Unknown"),
            }

        log.info("batch_quotes_done", requested=len(symbols), returned=len(results))
        return results

    def get_historical_prices(
        self, symbol: str, days: int = 260
    ) -> Optional[dict]:
        """1 銘柄の OHLCV 履歴を FMP 互換形式で返す (FMPClient.get_historical_prices 互換).

        "SPY" は TOPIX にマッピング（相対強度計算用インデックス代替）。

        Args:
            symbol: ティッカー。"SPY" は TOPIX として扱う。
            days: 直近 N 取引日分を返す。

        Returns:
            {"historical": [{"date": "YYYY-MM-DD", "open": float, "high": float,
                             "low": float, "close": float, "adjClose": float,
                             "volume": float}, ...]}
            リストは降順（最新が先頭）。取得失敗時は None。
        """
        cache_key = f"hist_{symbol}_{days}"
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]

        self._api_calls += 1

        # TOPIX（市場インデックス代替）
        if symbol == _SPY_ALIAS:
            result = self._get_topix_historical(days)
            self._cache[cache_key] = result
            return result

        # 通常銘柄
        grp = self._ohlcv[self._ohlcv["TICKER"] == symbol].sort_values("DATE")
        if grp.empty:
            log.debug("historical_not_found", symbol=symbol)
            return None

        # 直近 days 取引日分を取得（昇順）
        grp = grp.tail(days)

        historical = []
        for _, row in grp.iterrows():
            close = float(row["ADJ_CLOSE"])
            historical.append(
                {
                    "date": str(row["DATE"]),
                    "open": float(row["ADJ_OPEN"]) if pd.notna(row["ADJ_OPEN"]) else close,
                    "high": float(row["ADJ_HIGH"]) if pd.notna(row["ADJ_HIGH"]) else close,
                    "low": float(row["ADJ_LOW"]) if pd.notna(row["ADJ_LOW"]) else close,
                    "close": close,
                    "adjClose": close,
                    "volume": (
                        float(row["ADJ_VOLUME"]) if pd.notna(row["ADJ_VOLUME"]) else 0.0
                    ),
                }
            )

        # FMP は降順（最新が先頭）
        historical.reverse()

        result = {"symbol": symbol, "historical": historical}
        self._cache[cache_key] = result
        return result

    def _get_topix_historical(self, days: int) -> Optional[dict]:
        """TOPIX 日次終値を FMP historical 形式（降順）で返す.

        Args:
            days: 直近 N 取引日分。

        Returns:
            {"historical": [...]} 形式の dict。データなし時は None。
        """
        if self._topix.empty:
            log.warning("topix_empty")
            return None

        topix = self._topix.sort_values("DATE").tail(days)
        historical = []
        for _, row in topix.iterrows():
            close = float(row["TOPIX_CLOSE"])
            historical.append(
                {
                    "date": str(row["DATE"]),
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "adjClose": close,
                    "volume": 0.0,
                }
            )

        historical.reverse()  # 降順（FMP 互換）
        return {"symbol": _SPY_ALIAS, "historical": historical}

    def get_batch_historical(
        self, symbols: list[str], days: int = 260
    ) -> dict[str, list[dict]]:
        """複数銘柄の履歴を一括取得 (FMPClient.get_batch_historical 互換).

        Args:
            symbols: ティッカーリスト。
            days: 直近 N 取引日分。

        Returns:
            {ticker: historical_list} の dict。取得できなかった銘柄はキーなし。
        """
        results: dict[str, list[dict]] = {}
        for symbol in symbols:
            data = self.get_historical_prices(symbol, days=days)
            if data and "historical" in data:
                results[symbol] = data["historical"]
        return results

    def get_api_stats(self) -> dict:
        """統計情報を返す (FMPClient.get_api_stats 互換).

        Returns:
            cache_entries, api_calls_made, rate_limit_reached に加え
            data_source, date_range, tickers を含む dict。
        """
        return {
            "cache_entries": len(self._cache),
            "api_calls_made": self._api_calls,
            "rate_limit_reached": False,
            "data_source": "J-Quants/BigQuery",
            "date_range": f"{self._date_from} to {self._date_to}",
            "tickers": int(self._ohlcv["TICKER"].nunique()),
        }

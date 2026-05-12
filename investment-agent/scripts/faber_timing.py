"""Faber (2007) 5資産タイミングモデル — 日次攻め/守りシグナル.

Faber "A Quantitative Approach to Tactical Asset Allocation" に基づき、
5資産クラスの200日SMAタイミングモデルで市場センチメントを判定する。

データソース:
  - BB_債券履歴_new.xlsx LISTシート (Dropbox API) → SP500, CRB, US10Y
  - yfinance → EFA (MSCI EAFE), VNQ (米REIT)
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import NamedTuple
from zoneinfo import ZoneInfo

import dropbox
import pandas as pd
import structlog
import yfinance as yf
from dropbox.exceptions import ApiError

sys.path.insert(0, os.path.dirname(__file__))
from notify import send_ntfy  # noqa: E402

# ── ログ設定 ──────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)
log = structlog.get_logger(__name__)

# ── 定数 ──────────────────────────────────────────────
JST = ZoneInfo("Asia/Tokyo")
SMA_WINDOW = 200

DBX_APP_KEY = "t8feblcw74hoeky"
DBX_APP_SECRET = "fcjgc37d034pw1n"
DBX_REFRESH_TOKEN = "XwOxZlA8jPUAAAAAAAAAAZxnT4qRFtWLcShpKy3cNjTf3euIMqEZxCNieAQiLSDw"
DBX_FILE_PATH = "/stock/BB_債券履歴_new.xlsx"

# LISTシート列インデッ��ス
COL_DATE = 0
COL_US10Y = 2   # Gbond 10y (利回り)
COL_CRB = 13    # CRB
COL_SP500 = 23  # S&P500

YF_TICKERS = ["EFA", "VNQ"]
YF_PERIOD = "2y"


class AssetSignal(NamedTuple):
    """1資産の判定結果."""

    name: str
    current: float
    sma200: float
    is_buy: bool
    inverted: bool  # 利回り反���判定か


def download_excel_from_dropbox() -> pd.DataFrame:
    """Dropbox APIでBB_債券履歴_new.xlsxのLISTシートを取得."""
    log.info("dropbox_download_start", path=DBX_FILE_PATH)
    dbx = dropbox.Dropbox(
        app_key=DBX_APP_KEY,
        app_secret=DBX_APP_SECRET,
        oauth2_refresh_token=DBX_REFRESH_TOKEN,
    )
    try:
        _, response = dbx.files_download(DBX_FILE_PATH)
    except ApiError as e:
        log.error("dropbox_download_failed", error=str(e))
        raise

    data = io.BytesIO(response.content)
    log.info("dropbox_download_ok", size_mb=f"{len(response.content) / 1024 / 1024:.1f}")

    df = pd.read_excel(data, sheet_name="LIST", header=None, skiprows=2)
    df = df.iloc[:, [COL_DATE, COL_US10Y, COL_CRB, COL_SP500]]
    df.columns = ["date", "us10y", "crb", "sp500"]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ["us10y", "crb", "sp500"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["date"]).set_index("date").sort_index()
    log.info("excel_parsed", rows=len(df), start=str(df.index[0].date()), end=str(df.index[-1].date()))
    return df


def download_yfinance() -> dict[str, pd.Series]:
    """yfinanceでEFA/VNQの終値を取得."""
    log.info("yfinance_download_start", tickers=YF_TICKERS, period=YF_PERIOD)
    raw = yf.download(YF_TICKERS, period=YF_PERIOD, auto_adjust=True, progress=False)
    result: dict[str, pd.Series] = {}
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"]
    else:
        close = raw[["Close"]].rename(columns={"Close": YF_TICKERS[0]})

    for ticker in YF_TICKERS:
        if ticker in close.columns:
            s = close[ticker].dropna()
            result[ticker] = s
            log.info("yfinance_ok", ticker=ticker, rows=len(s), last_date=str(s.index[-1].date()))
        else:
            log.warning("yfinance_missing", ticker=ticker)
    return result


def calc_signal(series: pd.Series, name: str, *, inverted: bool = False) -> AssetSignal | None:
    """200日SMAとの比較でBUY/SELL判定."""
    if len(series) < SMA_WINDOW:
        log.warning("insufficient_data", name=name, rows=len(series), required=SMA_WINDOW)
        return None

    sma = series.rolling(SMA_WINDOW).mean()
    current = series.iloc[-1]
    sma_val = sma.iloc[-1]

    if pd.isna(current) or pd.isna(sma_val):
        log.warning("nan_value", name=name, current=current, sma=sma_val)
        return None

    if inverted:
        is_buy = current < sma_val
    else:
        is_buy = current > sma_val

    return AssetSignal(
        name=name,
        current=float(current),
        sma200=float(sma_val),
        is_buy=is_buy,
        inverted=inverted,
    )


def format_report(signals: list[AssetSignal], as_of: str) -> str:
    """判定結果を人間可読なテキストに整形."""
    lines: list[str] = []
    lines.append(f"=== Faber Timing Model ({as_of}) ===")
    lines.append("")

    buy_count = sum(1 for s in signals if s.is_buy)
    total = len(signals)

    for s in signals:
        mark = "BUY" if s.is_buy else "SELL"
        icon = "✓" if s.is_buy else "✗"
        note = ""
        if s.inverted:
            note = " (利回り反転)"
        lines.append(f"  {s.name:<12s} {s.current:>10,.2f} vs SMA200 {s.sma200:>10,.2f} → {mark} {icon}{note}")

    lines.append("")
    if buy_count >= 4:
        stance = "リスクオン（攻め）"
    elif buy_count >= 3:
        stance = "やや攻め"
    elif buy_count >= 2:
        stance = "中立"
    else:
        stance = "リスクオフ（守り）"

    lines.append(f"総合: {buy_count}/{total} BUY → {stance}")
    return "\n".join(lines)


def main() -> None:
    """メインエントリーポイント."""
    parser = argparse.ArgumentParser(description="Faber (2007) 5-asset timing model")
    parser.add_argument("--dry-run", action="store_true", help="判定のみ実行、通知は送らない")
    args = parser.parse_args()

    now = datetime.now(tz=JST)
    as_of = now.strftime("%Y-%m-%d")
    log.info("faber_timing_start", as_of=as_of, dry_run=args.dry_run)

    # データ取得
    excel_df = download_excel_from_dropbox()
    yf_data = download_yfinance()

    # 各資産のシグナル算出
    signals: list[AssetSignal] = []

    asset_configs: list[tuple[str, pd.Series, bool]] = [
        ("S&P 500", excel_df["sp500"].dropna(), False),
        ("EAFE", yf_data.get("EFA", pd.Series(dtype=float)), False),
        ("CRB", excel_df["crb"].dropna(), False),
        ("NAREIT", yf_data.get("VNQ", pd.Series(dtype=float)), False),
        ("US10Y Bond", excel_df["us10y"].dropna(), True),
    ]

    for name, series, inverted in asset_configs:
        sig = calc_signal(series, name, inverted=inverted)
        if sig is not None:
            signals.append(sig)
            log.info("signal_calculated", name=name, buy=sig.is_buy,
                     current=f"{sig.current:.2f}", sma200=f"{sig.sma200:.2f}")
        else:
            log.warning("signal_skipped", name=name)

    if not signals:
        log.error("no_signals_calculated")
        sys.exit(1)

    report = format_report(signals, as_of)
    log.info("report_generated", buy_count=sum(1 for s in signals if s.is_buy), total=len(signals))

    if args.dry_run:
        log.info("dry_run_output")
        for line in report.split("\n"):
            log.info("report", line=line)
    else:
        send_ntfy(report, title=f"Faber Timing {as_of}")
        log.info("ntfy_sent")


if __name__ == "__main__":
    main()

"""011-4 Paper Trade P&L — ペーパートレード損益計算 + メール通知.

毎日19:00 JST に実行。BQ のシグナルと株価データから
当日の P&L を計算し、BQ に記録してメール通知する。

対応環境:
    - ローカルPC
    - Cloud Run Job

使い方:
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/paper_trade_011_4_pnl.py
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/paper_trade_011_4_pnl.py --dry-run
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/paper_trade_011_4_pnl.py --date=2026-04-14
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime, timezone, timedelta

import pandas as pd
import structlog
from google.cloud import bigquery
from tenacity import retry, stop_after_attempt, wait_exponential

log = structlog.get_logger()

# ==========================================
# 設定
# ==========================================

PROJECT = os.environ.get("GCP_PROJECT", "gmailpj-357912")
KEY_FILE = os.environ.get("GCP_KEY_FILE", "keys/gcp-service-account.json")
BQ_SIGNAL_TABLE = os.environ.get("BQ_SIGNAL_TABLE", f"{PROJECT}.STOCK.SIGNAL_011_4")
BQ_PNL_TABLE = os.environ.get("BQ_PNL_TABLE", f"{PROJECT}.STOCK.PAPER_TRADE_011_4")
BQ_PRICE_TABLE = os.environ.get("BQ_PRICE_TABLE", f"{PROJECT}.STOCK.STOCK_PRICE_JQUANTS")
JST = timezone(timedelta(hours=+9), "JST")

# AIR TRADE 用想定資金（KS ON 時は signal 側で POSITION_JPY=0 になるため、
# ペーパートレードの継続評価のためここで仮想ポジション額を上書きする）
AIR_TRADE_CAPITAL = float(os.environ.get("AIR_TRADE_CAPITAL", "30000000"))
AIR_TRADE_POS_PER_TICKER = AIR_TRADE_CAPITAL / 6.0  # 3 long + 3 short

# TOPIX-17 ETF 名称マッピング
ETF_NAMES: dict[str, str] = {
    "1617": "食品",
    "1618": "エネルギー資源",
    "1619": "建設・資材",
    "1620": "素材・化学",
    "1621": "医薬品",
    "1622": "自動車・輸送機",
    "1623": "鉄鋼・非鉄",
    "1624": "機械",
    "1625": "電機・精密",
    "1626": "情報通信・サービスその他",
    "1627": "電力・ガス",
    "1628": "運輸・物流",
    "1629": "商社・卸売",
    "1630": "小売",
    "1631": "銀行",
    "1632": "金融（除く銀行）",
    "1633": "不動産",
}


# ==========================================
# 実行環境判別
# ==========================================

def detect_runtime() -> str:
    """実行環境を自動判別する."""
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    try:
        import google.colab  # noqa: F401
        if os.environ.get("GOOGLE_CLOUD_PROJECT"):
            return "colab_enterprise"
        return "colab_personal"
    except ImportError:
        return "local"


RUNTIME = detect_runtime()
log.info("runtime detected", runtime=RUNTIME)


def get_bq_client() -> bigquery.Client:
    """BQ クライアントを取得する."""
    if RUNTIME == "local":
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(KEY_FILE)
        return bigquery.Client(project=PROJECT, credentials=creds)
    else:
        return bigquery.Client(project=PROJECT)


# ==========================================
# データ取得
# ==========================================

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def fetch_signal(bq_client: bigquery.Client, trade_date: str) -> pd.DataFrame:
    """BQ から当日のシグナルを取得.

    KS ON 時も SIDE は LONG/SHORT が入っている（POSITION_JPY=0）ので同じクエリで取れる.
    """
    query = f"""
    SELECT TICKER, SIDE, POSITION_JPY, KILL_SWITCH, ROLLING_IC
    FROM `{BQ_SIGNAL_TABLE}`
    WHERE DATE = '{trade_date}'
      AND SIDE IN ('LONG', 'SHORT')
    ORDER BY TICKER
    """
    log.info("Fetching signal", date=trade_date)
    job = bq_client.query(query)
    df = job.to_dataframe()
    log.info("Signal fetched", rows=len(df))
    return df


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def fetch_prices(bq_client: bigquery.Client, trade_date: str, tickers: list[str]) -> pd.DataFrame:
    """BQ から当日の株価を取得."""
    tickers_str = ", ".join(f"'{t}'" for t in tickers)
    query = f"""
    SELECT TICKER, ADJ_OPEN, ADJ_CLOSE
    FROM `{BQ_PRICE_TABLE}`
    WHERE DATE = '{trade_date}'
      AND TICKER IN ({tickers_str})
      AND ADJ_OPEN IS NOT NULL
      AND ADJ_CLOSE IS NOT NULL
    """
    log.info("Fetching prices", date=trade_date, tickers=len(tickers))
    job = bq_client.query(query)
    df = job.to_dataframe()
    log.info("Prices fetched", rows=len(df))
    return df


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def fetch_cumulative_pnl(bq_client: bigquery.Client, trade_date: str) -> float:
    """BQ から当日以前の累積 P&L を取得."""
    query = f"""
    SELECT COALESCE(SUM(PNL_JPY), 0) AS total_pnl
    FROM `{BQ_PNL_TABLE}`
    WHERE DATE < '{trade_date}'
    """
    try:
        job = bq_client.query(query)
        result = job.to_dataframe()
        return float(result["total_pnl"].iloc[0])
    except Exception:
        # テーブルがまだ無い場合
        return 0.0


# ==========================================
# P&L 計算
# ==========================================

def compute_pnl(signal_df: pd.DataFrame, price_df: pd.DataFrame) -> pd.DataFrame:
    """シグナルと株価から P&L を計算."""
    merged = signal_df.merge(price_df, on="TICKER", how="inner")

    if merged.empty:
        log.warning("No matching price data for signal tickers")
        return pd.DataFrame()

    results = []
    for _, row in merged.iterrows():
        open_price = float(row["ADJ_OPEN"])
        close_price = float(row["ADJ_CLOSE"])
        position_jpy = float(row["POSITION_JPY"])
        side = row["SIDE"]

        if side == "LONG":
            return_pct = (close_price - open_price) / open_price * 100
            pnl_jpy = (close_price - open_price) / open_price * position_jpy
        else:  # SHORT
            return_pct = (open_price - close_price) / open_price * 100
            pnl_jpy = (open_price - close_price) / open_price * position_jpy

        results.append({
            "TICKER": row["TICKER"],
            "SIDE": side,
            "POSITION_JPY": position_jpy,
            "OPEN_PRICE": open_price,
            "CLOSE_PRICE": close_price,
            "RETURN_PCT": return_pct,
            "PNL_JPY": pnl_jpy,
            "KILL_SWITCH": row["KILL_SWITCH"],
        })

    return pd.DataFrame(results)


# ==========================================
# BQ 書き込み
# ==========================================

def write_pnl_to_bq(
    bq_client: bigquery.Client,
    trade_date: str,
    pnl_df: pd.DataFrame,
    cumulative_pnl_before: float,
) -> int:
    """P&L 結果を BQ に書き込む."""
    if pnl_df.empty:
        log.info("No P&L data to write")
        return 0

    # 累積 P&L を計算
    running_cum = cumulative_pnl_before
    trade_date_obj = datetime.strptime(trade_date, "%Y-%m-%d").date()
    rows = []
    for _, row in pnl_df.iterrows():
        running_cum += float(row["PNL_JPY"])
        rows.append({
            "DATE": trade_date_obj,
            "TICKER": row["TICKER"],
            "SIDE": row["SIDE"],
            "POSITION_JPY": row["POSITION_JPY"],
            "OPEN_PRICE": row["OPEN_PRICE"],
            "CLOSE_PRICE": row["CLOSE_PRICE"],
            "RETURN_PCT": row["RETURN_PCT"],
            "PNL_JPY": row["PNL_JPY"],
            "CUMULATIVE_PNL": running_cum,
            "KILL_SWITCH": row["KILL_SWITCH"],
        })

    df = pd.DataFrame(rows)

    # 当日分を削除してから挿入（リラン安全）
    delete_query = f"DELETE FROM `{BQ_PNL_TABLE}` WHERE DATE = '{trade_date}'"
    try:
        bq_client.query(delete_query).result()
        log.info("Deleted existing PnL rows", date=trade_date)
    except Exception:
        log.info("No existing PnL rows to delete (table may not exist yet)")

    job_config = bigquery.LoadJobConfig(
        schema=[
            bigquery.SchemaField("DATE", "DATE"),
            bigquery.SchemaField("TICKER", "STRING"),
            bigquery.SchemaField("SIDE", "STRING"),
            bigquery.SchemaField("POSITION_JPY", "FLOAT64"),
            bigquery.SchemaField("OPEN_PRICE", "FLOAT64"),
            bigquery.SchemaField("CLOSE_PRICE", "FLOAT64"),
            bigquery.SchemaField("RETURN_PCT", "FLOAT64"),
            bigquery.SchemaField("PNL_JPY", "FLOAT64"),
            bigquery.SchemaField("CUMULATIVE_PNL", "FLOAT64"),
            bigquery.SchemaField("KILL_SWITCH", "BOOL"),
        ],
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        autodetect=False,
    )

    job = bq_client.load_table_from_dataframe(df, BQ_PNL_TABLE, job_config=job_config)
    job.result()
    log.info("PnL written to BQ", table=BQ_PNL_TABLE, rows=len(df))
    return len(df)


# ==========================================
# メール通知
# ==========================================

def build_mail_body(
    trade_date: str,
    pnl_df: pd.DataFrame,
    daily_pnl: float,
    cumulative_pnl: float,
    kill_switch: bool,
    rolling_ic: float,
    total_capital: float,
) -> tuple[str, str]:
    """メール件名と本文を構築.

    Returns:
        (subject, body)
    """
    daily_pct = daily_pnl / total_capital * 100
    cum_pct = cumulative_pnl / total_capital * 100

    tag = "[011-4 AIR TRADE KS=ON]" if kill_switch else "[011-4]"
    subject = (
        f"{tag} {trade_date} P&L: {daily_pnl:+,.0f}yen "
        f"(cumulative: {cumulative_pnl:+,.0f}yen)"
    )

    lines = []
    if kill_switch:
        lines.append(
            f"[AIR TRADE] Kill Switch ON (Rolling IC={rolling_ic:.4f} < -0.01). "
            f"本運用なら no trade だが、air trade として仮想ポジションで P&L 計算中."
        )
        lines.append("")
    lines.append("Today's positions:")

    # ロング
    long_rows = pnl_df[pnl_df["SIDE"] == "LONG"].sort_values("PNL_JPY", ascending=False)
    short_rows = pnl_df[pnl_df["SIDE"] == "SHORT"].sort_values("PNL_JPY", ascending=False)

    lines.append("  LONG:")
    for _, row in long_rows.iterrows():
        ticker = row["TICKER"]
        name = ETF_NAMES.get(ticker, "")
        pnl = row["PNL_JPY"]
        lines.append(f"    {ticker}({name}) {pnl:+,.0f}yen")

    lines.append("  SHORT:")
    for _, row in short_rows.iterrows():
        ticker = row["TICKER"]
        name = ETF_NAMES.get(ticker, "")
        pnl = row["PNL_JPY"]
        lines.append(f"    {ticker}({name}) {pnl:+,.0f}yen")

    lines.append("")
    lines.append(f"  Daily P&L: {daily_pnl:+,.0f}yen ({daily_pct:+.3f}%)")
    lines.append(f"  Cumulative P&L: {cumulative_pnl:+,.0f}yen ({cum_pct:+.3f}%)")
    ks_line = f"ON (IC={rolling_ic:.4f}) — AIR TRADE" if kill_switch else f"OFF (IC={rolling_ic:.4f})"
    lines.append(f"  Kill Switch: {ks_line}")
    lines.append("")
    lines.append("  * Paper trade (30M JPY virtual operation)")

    body = "\n".join(lines)
    return subject, body


# ==========================================
# メイン
# ==========================================

def main() -> int:
    """P&L 計算のメインエントリポイント."""
    ts0 = datetime.now(tz=JST)
    log.info("paper_trade_011_4_pnl start", ts=ts0.isoformat(), runtime=RUNTIME)

    # 引数パース
    parser = argparse.ArgumentParser(description="011-4 Paper Trade P&L")
    parser.add_argument("--dry-run", action="store_true", help="BQ書き込み・メール送信なし")
    parser.add_argument("--date", type=str, default=None, help="対象日（YYYY-MM-DD）")
    if RUNTIME in ("colab_personal", "colab_enterprise"):
        args = parser.parse_args([])
    else:
        args = parser.parse_args()

    # 通知ユーティリティ
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from notify import send_mail, LogCapture

    log_cap = LogCapture()
    log_cap.start()

    try:
        bq_client = get_bq_client()

        # 対象日
        if args.date:
            trade_date = args.date
        else:
            trade_date = datetime.now(tz=JST).strftime("%Y-%m-%d")

        log.info("Target date", date=trade_date)

        # 1. シグナル取得
        signal_df = fetch_signal(bq_client, trade_date)

        if signal_df.empty:
            # Kill Switch ON の場合もシグナルはあるがSIDE='NONE'
            # 完全にシグナルがない場合は休日・祝日
            log.info("No signal for today — likely holiday/weekend. Skipping.")
            log_cap.stop()
            return 0

        kill_switch = bool(signal_df["KILL_SWITCH"].iloc[0])
        rolling_ic = float(signal_df["ROLLING_IC"].iloc[0])

        if kill_switch:
            # KS ON でも air trade として P&L 計算を継続する。
            # signal テーブルの POSITION_JPY は 0 のまま残して本運用との区別を保ちつつ、
            # ここで仮想ポジション額に差し替える。
            log.info("Kill Switch ON — AIR TRADE mode",
                     ic=f"{rolling_ic:.4f}",
                     notional_per_ticker=AIR_TRADE_POS_PER_TICKER)
            signal_df = signal_df.copy()
            signal_df["POSITION_JPY"] = AIR_TRADE_POS_PER_TICKER
            total_capital = AIR_TRADE_CAPITAL
        else:
            total_capital = float(signal_df["POSITION_JPY"].sum())

        # 2. 株価取得
        tickers = signal_df["TICKER"].tolist()
        price_df = fetch_prices(bq_client, trade_date, tickers)

        if price_df.empty:
            log.warning("No price data available yet for", date=trade_date)
            log_cap.stop()
            return 0

        # 3. P&L 計算
        pnl_df = compute_pnl(signal_df, price_df)

        if pnl_df.empty:
            log.warning("No P&L computed — price/signal mismatch")
            log_cap.stop()
            return 0

        daily_pnl = float(pnl_df["PNL_JPY"].sum())
        cumulative_pnl_before = fetch_cumulative_pnl(bq_client, trade_date)
        cumulative_pnl = cumulative_pnl_before + daily_pnl

        log.info("P&L computed",
                 daily_pnl=f"{daily_pnl:+,.0f}",
                 cumulative_pnl=f"{cumulative_pnl:+,.0f}",
                 positions=len(pnl_df))

        # 4. BQ 書き込み
        if args.dry_run:
            log.info("Dry run mode — skipping BQ write and mail")
        else:
            write_pnl_to_bq(bq_client, trade_date, pnl_df, cumulative_pnl_before)

            # 5. メール通知
            subject, body = build_mail_body(
                trade_date, pnl_df, daily_pnl, cumulative_pnl,
                kill_switch, rolling_ic, total_capital,
            )
            send_mail(subject, body)
            log.info("P&L mail sent")

        log_cap.stop()
        ts1 = datetime.now(tz=JST)
        elapsed = (ts1 - ts0).total_seconds()
        log.info("Done", elapsed_s=f"{elapsed:.1f}")
        return 0

    except Exception as e:
        log_text = log_cap.stop()
        tb_str = traceback.format_exc()
        send_mail(
            f"[paper-trade-011-4] Error",
            f"Error: {e}\n\n{tb_str}",
            attachment_text=log_text or None,
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())

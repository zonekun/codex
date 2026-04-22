"""011-4 Daily Signal — 米国→日本セクターETF リードラグ シグナル計算.

毎朝06:30 JST に実行。米国ETF終値からシグナルを計算し、
当日のロング/ショート銘柄を確定して BQ に記録する。

対応環境:
    - ローカルPC
    - Cloud Run Job

使い方:
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/signal_011_4_daily.py
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/signal_011_4_daily.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import pickle
import sys
import traceback
from datetime import datetime, timezone, timedelta
from io import BytesIO

import numpy as np
import pandas as pd
import structlog
import yfinance as yf
from google.cloud import bigquery, storage
from scipy.stats import spearmanr
from tenacity import retry, stop_after_attempt, wait_exponential

log = structlog.get_logger()

# ==========================================
# 設定
# ==========================================

PROJECT = os.environ.get("GCP_PROJECT", "gmailpj-357912")
KEY_FILE = os.environ.get("GCP_KEY_FILE", "keys/gcp-service-account.json")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "stock_data_1930932")
GCS_C0_PATH = os.environ.get("GCS_C0_PATH", "signal_011_4/c0_v0.pkl")
BQ_SIGNAL_TABLE = os.environ.get("BQ_SIGNAL_TABLE", f"{PROJECT}.STOCK.SIGNAL_011_4")
JST = timezone(timedelta(hours=+9), "JST")

# 戦略パラメータ
WINDOW_L = int(os.environ.get("SIGNAL_WINDOW_L", "60"))
K_EIG = int(os.environ.get("SIGNAL_K_EIG", "3"))
LAMBDA_REG = float(os.environ.get("SIGNAL_LAMBDA_REG", "0.9"))
Q_TOP = int(os.environ.get("SIGNAL_Q_TOP", "3"))
TOTAL_CAPITAL = float(os.environ.get("SIGNAL_TOTAL_CAPITAL", "30000000"))
KILL_SWITCH_THRESHOLD = float(os.environ.get("SIGNAL_KS_THRESHOLD", "-0.01"))
KILL_SWITCH_WEEKS = int(os.environ.get("SIGNAL_KS_WEEKS", "26"))

# 米国セクターETF 11本
US_TICKERS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]
N_US = len(US_TICKERS)

# 日本 TOPIX-17 ETF
JP_TICKERS = [str(t) for t in range(1617, 1634)]
N_JP = len(JP_TICKERS)

N_TOTAL = N_US + N_JP

# XLC 開始日
XLC_INCEPTION = pd.Timestamp("2018-06-18")

# ポジション金額（各銘柄）
POS_PER_TICKER = TOTAL_CAPITAL / (Q_TOP * 2)


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


def get_gcs_client() -> storage.Client:
    """GCS クライアントを取得する."""
    if RUNTIME == "local":
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(KEY_FILE)
        return storage.Client(project=PROJECT, credentials=creds)
    else:
        return storage.Client(project=PROJECT)


# ==========================================
# C0/V0 ロード
# ==========================================

def load_c0_v0(gcs_client: storage.Client) -> dict:
    """GCS から C0/V0 の pickle を読み込む."""
    bucket = gcs_client.bucket(GCS_BUCKET)
    blob = bucket.blob(GCS_C0_PATH)
    pkl_bytes = blob.download_as_bytes()
    payload = pickle.loads(pkl_bytes)
    log.info("C0/V0 loaded from GCS",
             path=f"gs://{GCS_BUCKET}/{GCS_C0_PATH}",
             C0_shape=payload["C0"].shape,
             V0_shape=payload["V0"].shape,
             created_at=payload.get("created_at", "unknown"))
    return payload


# ==========================================
# データ取得
# ==========================================

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def fetch_us_prices(lookback_days: int = 120) -> pd.DataFrame:
    """yfinance から米国セクターETF の直近データを取得.

    Args:
        lookback_days: 取得する過去日数（ローリング窓 + バッファ用）
    """
    end_date = datetime.now(tz=JST).strftime("%Y-%m-%d")
    start_date = (datetime.now(tz=JST) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    log.info("Fetching US ETF prices", start=start_date, end=end_date)
    raw = yf.download(US_TICKERS, start=start_date, end=end_date, auto_adjust=True)
    close = raw["Close"][US_TICKERS].copy()
    missing = [t for t in US_TICKERS if close[t].dropna().empty]
    if missing:
        raise ValueError(f"US ETF download incomplete, missing: {missing}")
    close_long = close.stack().reset_index()
    close_long.columns = ["Date", "Ticker", "Close"]
    log.info("US prices fetched", rows=len(close_long))
    return close_long


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def fetch_jp_prices(bq_client: bigquery.Client, lookback_days: int = 120) -> pd.DataFrame:
    """BQ から日本 TOPIX-17 ETF の直近データを取得."""
    start_date = (datetime.now(tz=JST) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    tickers_str = ", ".join(f"'{t}'" for t in JP_TICKERS)
    query = f"""
    SELECT DATE, TICKER, ADJ_OPEN, ADJ_CLOSE
    FROM `{PROJECT}.STOCK.STOCK_PRICE_JQUANTS`
    WHERE TICKER IN ({tickers_str})
      AND DATE >= '{start_date}'
      AND ADJ_OPEN IS NOT NULL
      AND ADJ_CLOSE IS NOT NULL
    ORDER BY TICKER, DATE
    """
    log.info("Querying BQ for JP ETF prices", start=start_date)
    job = bq_client.query(query)
    df = job.to_dataframe()
    df["DATE"] = pd.to_datetime(df["DATE"])
    log.info("JP prices fetched", rows=len(df))
    return df


# ==========================================
# リターン構築
# ==========================================

def build_us_returns(df_us: pd.DataFrame) -> pd.DataFrame:
    """米国セクター close-to-close 対数リターン."""
    df = df_us.copy()
    df = df.sort_values(["Ticker", "Date"]).drop_duplicates(["Ticker", "Date"], keep="last")
    pivot_close = df.pivot(index="Date", columns="Ticker", values="Close").sort_index()
    pivot_close = pivot_close.ffill(limit=3)
    if "XLC" in pivot_close.columns:
        xlc_mask = pivot_close.index < XLC_INCEPTION
        pivot_close.loc[xlc_mask, "XLC"] = np.nan
    log_ret = np.log(pivot_close / pivot_close.shift(1))
    return log_ret


def build_jp_returns(df_jp: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """日本セクター open-to-close / close-to-close 対数リターン."""
    df = df_jp.copy()
    df = df.sort_values(["TICKER", "DATE"]).drop_duplicates(["TICKER", "DATE"], keep="last")
    pivot_open = df.pivot(index="DATE", columns="TICKER", values="ADJ_OPEN").sort_index()
    pivot_close = df.pivot(index="DATE", columns="TICKER", values="ADJ_CLOSE").sort_index()
    pivot_open = pivot_open.ffill(limit=5)
    pivot_close = pivot_close.ffill(limit=5)
    pivot_open = pivot_open[JP_TICKERS]
    pivot_close = pivot_close[JP_TICKERS]
    oc = np.log(pivot_close / pivot_open)
    c2c = np.log(pivot_close / pivot_close.shift(1))
    return oc, c2c


def align_common_dates(
    us_c2c: pd.DataFrame,
    jp_oc: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """米国 day t → 日本 day t+1 でアライン."""
    us_dates = us_c2c.dropna(how="all").index.sort_values()
    jp_dates = jp_oc.dropna(how="all").index.sort_values()
    jp_dates_sorted = sorted(jp_dates)

    pairs: list[tuple] = []
    jp_idx = 0
    for us_d in us_dates:
        target = us_d + pd.Timedelta(days=1)
        while jp_idx < len(jp_dates_sorted) and jp_dates_sorted[jp_idx] < target:
            jp_idx += 1
        if jp_idx >= len(jp_dates_sorted):
            break
        jp_d = jp_dates_sorted[jp_idx]
        if (jp_d - us_d).days <= 7:
            pairs.append((us_d, jp_d))

    if not pairs:
        raise ValueError("No aligned date pairs found")

    pair_df = pd.DataFrame(pairs, columns=["US_DATE", "JP_DATE"])
    pair_df = pair_df.sort_values("US_DATE").drop_duplicates("JP_DATE", keep="last")
    pair_df = pair_df.sort_values("US_DATE").reset_index(drop=True)

    us_dates_aligned = pd.DatetimeIndex(pair_df["US_DATE"].values)
    jp_dates_aligned = pd.DatetimeIndex(pair_df["JP_DATE"].values)

    us_aligned = us_c2c.loc[us_dates_aligned].copy()
    us_aligned.index = range(len(us_aligned))

    jp_aligned = jp_oc.loc[jp_dates_aligned].copy()
    jp_aligned.index = range(len(jp_aligned))

    date_info = pd.DataFrame({
        "US_DATE": us_dates_aligned.values,
        "JP_DATE": jp_dates_aligned.values,
    })

    return us_aligned, jp_aligned, date_info


# ==========================================
# シグナル計算
# ==========================================

def compute_predictor(
    C_window: np.ndarray,
    C0: np.ndarray,
    K: int,
    lam: float,
) -> np.ndarray:
    """正則化 PCA で B_t = V_JP @ V_US.T を計算."""
    C_reg = (1 - lam) * C_window + lam * C0
    C_reg = (C_reg + C_reg.T) / 2.0

    vals, vecs = np.linalg.eigh(C_reg)
    order = np.argsort(vals)[::-1][:K]
    V = vecs[:, order]

    V_US = V[:N_US, :]
    V_JP = V[N_US:, :]
    B = V_JP @ V_US.T
    return B


def compute_rolling_ic(
    us_vals: np.ndarray,
    jp_vals: np.ndarray,
    date_info: pd.DataFrame,
    C0: np.ndarray,
) -> float:
    """直近 KILL_SWITCH_WEEKS 週の Rolling IC（Spearman相関の平均）を計算.

    過去データからシグナル vs 実現リターンの相関を週次で計算し、
    KILL_SWITCH_WEEKS 週の移動平均を返す。
    """
    T = len(us_vals)
    lookback_days = KILL_SWITCH_WEEKS * 5 + WINDOW_L  # 概算

    start_idx = max(WINDOW_L, T - lookback_days)

    daily_ics: list[float] = []
    for t_idx in range(start_idx, T):
        window = slice(t_idx - WINDOW_L, t_idx)
        us_win = us_vals[window]
        jp_win = jp_vals[window]

        Z_win = np.concatenate([us_win, jp_win], axis=1)
        Z_win = Z_win - Z_win.mean(axis=0, keepdims=True)
        std_win = Z_win.std(axis=0, keepdims=True)
        std_win[std_win == 0] = 1.0
        Z_win = Z_win / std_win
        C_win = (Z_win.T @ Z_win) / max(Z_win.shape[0] - 1, 1)

        B = compute_predictor(C_win, C0, K_EIG, LAMBDA_REG)

        us_today = us_vals[t_idx]
        us_mu = us_vals[window].mean(axis=0)
        us_sig = us_vals[window].std(axis=0)
        us_sig[us_sig == 0] = 1.0
        z_us = (us_today - us_mu) / us_sig

        z_pred = B @ z_us
        jp_actual = jp_vals[t_idx]

        if np.all(np.isfinite(z_pred)) and np.all(np.isfinite(jp_actual)):
            corr, _ = spearmanr(z_pred, jp_actual)
            daily_ics.append(corr)

    if len(daily_ics) == 0:
        log.warning("No valid IC values computed")
        return 0.0

    # 週次 IC を計算（5日ごとに平均）
    weekly_ics: list[float] = []
    for i in range(0, len(daily_ics), 5):
        chunk = daily_ics[i:i + 5]
        if chunk:
            weekly_ics.append(float(np.mean(chunk)))

    # 直近 KILL_SWITCH_WEEKS 週の平均
    recent = weekly_ics[-KILL_SWITCH_WEEKS:]
    rolling_ic = float(np.mean(recent)) if recent else 0.0

    log.info("Rolling IC computed",
             total_daily_ics=len(daily_ics),
             total_weekly_ics=len(weekly_ics),
             recent_weeks=len(recent),
             rolling_ic=f"{rolling_ic:.4f}")

    return rolling_ic


def compute_daily_signal(
    us_vals: np.ndarray,
    jp_vals: np.ndarray,
    C0: np.ndarray,
) -> np.ndarray:
    """最新のシグナル z_hat_JP を計算."""
    T = len(us_vals)
    t_idx = T - 1

    window = slice(t_idx - WINDOW_L, t_idx)
    us_win = us_vals[window]
    jp_win = jp_vals[window]

    Z_win = np.concatenate([us_win, jp_win], axis=1)
    Z_win = Z_win - Z_win.mean(axis=0, keepdims=True)
    std_win = Z_win.std(axis=0, keepdims=True)
    std_win[std_win == 0] = 1.0
    Z_win = Z_win / std_win
    C_win = (Z_win.T @ Z_win) / max(Z_win.shape[0] - 1, 1)

    B = compute_predictor(C_win, C0, K_EIG, LAMBDA_REG)

    us_today = us_vals[t_idx]
    us_mu = us_vals[window].mean(axis=0)
    us_sig = us_vals[window].std(axis=0)
    us_sig[us_sig == 0] = 1.0
    z_us = (us_today - us_mu) / us_sig

    z_pred = B @ z_us
    return z_pred


# ==========================================
# BQ 書き込み
# ==========================================

def write_signal_to_bq(
    bq_client: bigquery.Client,
    trade_date: str,
    signal: np.ndarray,
    rolling_ic: float,
    kill_switch: bool,
) -> int:
    """シグナル結果を BQ に書き込む.

    Returns:
        書き込み行数
    """
    now_jst = datetime.now(tz=JST)

    # ランキング（シグナル絶対値の降順）
    abs_signal = np.abs(signal)
    rank_order = np.argsort(-abs_signal)

    trade_date_obj = datetime.strptime(trade_date, "%Y-%m-%d").date()
    ranking_desc = np.argsort(-signal)

    rows = []
    for j, ticker in enumerate(JP_TICKERS):
        rank = int(np.where(rank_order == j)[0][0]) + 1
        sig_val = float(signal[j])

        # SIDE 判定
        if j in ranking_desc[:Q_TOP]:
            side = "LONG"
        elif j in ranking_desc[-Q_TOP:]:
            side = "SHORT"
        else:
            side = "NONE"

        # ポジション金額
        if kill_switch:
            pos_jpy = 0.0
        elif side in ("LONG", "SHORT"):
            pos_jpy = POS_PER_TICKER
        else:
            pos_jpy = 0.0

        rows.append({
            "DATE": trade_date_obj,
            "SIGNAL_DATE": now_jst.replace(tzinfo=None),
            "TICKER": ticker,
            "SIGNAL_VALUE": sig_val,
            "SIDE": side,
            "RANK": rank,
            "POSITION_JPY": pos_jpy,
            "KILL_SWITCH": kill_switch,
            "ROLLING_IC": rolling_ic,
        })

    df = pd.DataFrame(rows)

    # 当日分を削除してから挿入（リラン安全）
    delete_query = f"DELETE FROM `{BQ_SIGNAL_TABLE}` WHERE DATE = '{trade_date}'"
    try:
        bq_client.query(delete_query).result()
        log.info("Deleted existing signal rows", date=trade_date)
    except Exception:
        # テーブルが存在しない場合は無視（初回実行）
        log.info("No existing rows to delete (table may not exist yet)")

    job_config = bigquery.LoadJobConfig(
        schema=[
            bigquery.SchemaField("DATE", "DATE"),
            bigquery.SchemaField("SIGNAL_DATE", "DATETIME"),
            bigquery.SchemaField("TICKER", "STRING"),
            bigquery.SchemaField("SIGNAL_VALUE", "FLOAT64"),
            bigquery.SchemaField("SIDE", "STRING"),
            bigquery.SchemaField("RANK", "INT64"),
            bigquery.SchemaField("POSITION_JPY", "FLOAT64"),
            bigquery.SchemaField("KILL_SWITCH", "BOOL"),
            bigquery.SchemaField("ROLLING_IC", "FLOAT64"),
        ],
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        autodetect=False,
    )

    job = bq_client.load_table_from_dataframe(df, BQ_SIGNAL_TABLE, job_config=job_config)
    job.result()
    log.info("Signal written to BQ", table=BQ_SIGNAL_TABLE, rows=len(df))
    return len(df)


# ==========================================
# メイン
# ==========================================

def main() -> int:
    """日次シグナル計算のメインエントリポイント."""
    ts0 = datetime.now(tz=JST)
    log.info("signal_011_4_daily start", ts=ts0.isoformat(), runtime=RUNTIME)

    # 引数パース（Cloud Run は sys.argv で引数を受ける）
    parser = argparse.ArgumentParser(description="011-4 Daily Signal")
    parser.add_argument("--dry-run", action="store_true", help="BQ書き込みなし")
    if RUNTIME in ("colab_personal", "colab_enterprise"):
        args = parser.parse_args([])
    else:
        args = parser.parse_args()

    # エラー時通知用
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from notify import send_mail, send_ntfy, LogCapture

    log_cap = LogCapture()
    log_cap.start()

    try:
        # 1. クライアント初期化
        bq_client = get_bq_client()
        gcs_client = get_gcs_client()

        # 2. C0/V0 ロード
        c0_v0 = load_c0_v0(gcs_client)
        C0 = c0_v0["C0"]

        # 3. データ取得（lookback: WINDOW_L + バッファ + KS用）
        lookback = max(WINDOW_L * 3, KILL_SWITCH_WEEKS * 5 + WINDOW_L) + 30
        lookback_days = int(lookback * 1.5)  # 営業日→カレンダー日換算

        df_us = fetch_us_prices(lookback_days=lookback_days)
        df_jp = fetch_jp_prices(bq_client, lookback_days=lookback_days)

        # 4. リターン構築
        us_c2c = build_us_returns(df_us)
        jp_oc, _ = build_jp_returns(df_jp)

        # 5. 日付アライン
        us_aligned, jp_aligned, date_info = align_common_dates(us_c2c, jp_oc)

        us_vals = np.nan_to_num(us_aligned.values, nan=0.0)
        jp_vals = np.nan_to_num(jp_aligned.values, nan=0.0)

        # 6. シグナル計算
        signal = compute_daily_signal(us_vals, jp_vals, C0)

        # 7. Rolling IC / Kill Switch
        rolling_ic = compute_rolling_ic(us_vals, jp_vals, date_info, C0)
        kill_switch = rolling_ic < KILL_SWITCH_THRESHOLD

        # 8. 取引日の決定（日本の次の営業日 = date_info の最後の JP_DATE の翌営業日）
        last_jp_date = pd.Timestamp(date_info["JP_DATE"].values[-1])
        # Cloud Run 06:30 JST 実行時: 当日が取引日
        today_jst = datetime.now(tz=JST).date()
        trade_date = str(today_jst)

        # 9. 結果表示
        ranking_desc = np.argsort(-signal)
        log.info("=== 011-4 Daily Signal ===",
                 trade_date=trade_date,
                 kill_switch="ON" if kill_switch else "OFF",
                 rolling_ic=f"{rolling_ic:.4f}",
                 threshold=KILL_SWITCH_THRESHOLD)

        long_tickers = [JP_TICKERS[i] for i in ranking_desc[:Q_TOP]]
        short_tickers = [JP_TICKERS[i] for i in ranking_desc[-Q_TOP:]]

        for i in ranking_desc[:Q_TOP]:
            log.info("LONG", ticker=JP_TICKERS[i],
                     signal=f"{signal[i]:+.4f}",
                     position_jpy=0.0 if kill_switch else POS_PER_TICKER)
        for i in ranking_desc[-Q_TOP:]:
            log.info("SHORT", ticker=JP_TICKERS[i],
                     signal=f"{signal[i]:+.4f}",
                     position_jpy=0.0 if kill_switch else POS_PER_TICKER)

        # 10. BQ 書き込み
        if args.dry_run:
            log.info("Dry run mode — skipping BQ write")
            written = 0
        else:
            written = write_signal_to_bq(
                bq_client, trade_date, signal, rolling_ic, kill_switch,
            )

        log_cap.stop()
        ts1 = datetime.now(tz=JST)
        elapsed = (ts1 - ts0).total_seconds()
        log.info("Done", elapsed_s=f"{elapsed:.1f}", rows_written=written)

        # 正常終了時の結果メール通知
        ks_label = "FLAT (KS=ON)" if kill_switch else "ACTIVE"
        long_str = "\n".join(f"  {JP_TICKERS[i]}  signal={signal[i]:+.4f}" for i in ranking_desc[:Q_TOP])
        short_str = "\n".join(f"  {JP_TICKERS[i]}  signal={signal[i]:+.4f}" for i in ranking_desc[-Q_TOP:])
        mail_body = (
            f"=== 011-4 Daily Signal ({trade_date}) ===\n\n"
            f"状態: {ks_label}\n"
            f"Rolling IC: {rolling_ic:.4f} (閾値 {KILL_SWITCH_THRESHOLD})\n\n"
            f"LONG:\n{long_str}\n\n"
            f"SHORT:\n{short_str}\n\n"
            f"実行時間: {elapsed:.1f}s / BQ書込: {written}行"
        )
        send_mail(f"[signal-011-4] {trade_date} {ks_label}", mail_body)

        return 0

    except Exception as e:
        log_text = log_cap.stop()
        tb_str = traceback.format_exc()
        send_mail(
            f"[signal-011-4] Error",
            f"Error: {e}\n\n{tb_str}",
            attachment_text=log_text or None,
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())

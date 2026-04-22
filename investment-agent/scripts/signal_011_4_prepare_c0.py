"""C0/V0 事前計算 → GCS pickle 保存.

011-4 戦略（米国→日本セクターETF リードラグ ロングショート）の
長期相関行列 C0 と事前固有ベクトル V0 を warm-up 期間（2016-2017）から計算し、
GCS に pickle で保存する。

初回のみ実行、または年次更新時に再実行する。

使い方:
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/signal_011_4_prepare_c0.py
"""
from __future__ import annotations

import os
import pickle
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import structlog
import yfinance as yf
from google.cloud import bigquery, storage
from google.oauth2 import service_account

log = structlog.get_logger()

# ==========================================
# 設定
# ==========================================

PROJECT = "gmailpj-357912"
KEY_FILE = "keys/gcp-service-account.json"
GCS_BUCKET = "stock_data_1930932"
GCS_PATH = "signal_011_4/c0_v0.pkl"
JST = timezone(timedelta(hours=+9), "JST")

# 米国セクターETF 11本
US_TICKERS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]
N_US = len(US_TICKERS)

# 日本 TOPIX-17 ETF
JP_TICKERS = [str(t) for t in range(1617, 1634)]
N_JP = len(JP_TICKERS)

N_TOTAL = N_US + N_JP

# シクリカル/ディフェンシブ分類
US_CYCLICAL = {"XLB", "XLE", "XLF", "XLRE"}
US_DEFENSIVE = {"XLK", "XLP", "XLU", "XLV"}
JP_CYCLICAL = {"1618", "1625", "1629", "1631"}
JP_DEFENSIVE = {"1617", "1621", "1627", "1630"}

# Warm-up 期間
WARMUP_START = pd.Timestamp("2016-01-01")
WARMUP_END = pd.Timestamp("2017-12-31")

# XLC 開始日（2018-06-18）→ warm-up 期間では NaN
XLC_INCEPTION = pd.Timestamp("2018-06-18")


# ==========================================
# V0 構築（論文 Section 3.1）
# ==========================================

def build_V0() -> np.ndarray:
    """V0 in R^(28 x 3): global, country-spread, cyclical/defensive を構築."""
    N = N_TOTAL
    v1 = np.ones(N) / np.sqrt(N)

    v2_raw = np.zeros(N)
    v2_raw[:N_US] = 1.0 / np.sqrt(N_US)
    v2_raw[N_US:] = -1.0 / np.sqrt(N_JP)
    v2_raw = v2_raw - np.dot(v2_raw, v1) * v1
    v2 = v2_raw / np.linalg.norm(v2_raw)

    v3_raw = np.zeros(N)
    for i, t in enumerate(US_TICKERS):
        if t in US_CYCLICAL:
            v3_raw[i] = 1.0
        elif t in US_DEFENSIVE:
            v3_raw[i] = -1.0
    for j, t in enumerate(JP_TICKERS):
        if t in JP_CYCLICAL:
            v3_raw[N_US + j] = 1.0
        elif t in JP_DEFENSIVE:
            v3_raw[N_US + j] = -1.0
    v3_raw = v3_raw - np.dot(v3_raw, v1) * v1
    v3_raw = v3_raw - np.dot(v3_raw, v2) * v2
    v3 = v3_raw / np.linalg.norm(v3_raw)

    V0 = np.column_stack([v1, v2, v3])
    log.info("V0 constructed", shape=V0.shape,
             orthogonality_check=float(np.max(np.abs(V0.T @ V0 - np.eye(3)))))
    return V0


# ==========================================
# C0 計算
# ==========================================

def compute_C0(
    us_c2c_warm: np.ndarray,
    jp_oc_warm: np.ndarray,
    V0: np.ndarray,
) -> np.ndarray:
    """Warm-up 期間からターゲット相関行列 C0 を計算."""
    Z = np.concatenate([us_c2c_warm, jp_oc_warm], axis=1)
    Z = Z - Z.mean(axis=0, keepdims=True)
    std = Z.std(axis=0, keepdims=True)
    std[std == 0] = 1.0
    Z = Z / std

    C_full = (Z.T @ Z) / max(Z.shape[0] - 1, 1)

    D0 = np.diag(np.diag(V0.T @ C_full @ V0))
    C_raw = V0 @ D0 @ V0.T
    Delta = np.diag(C_raw).copy()
    Delta[Delta <= 0] = 1e-8
    Delta_inv_sqrt = np.diag(1.0 / np.sqrt(Delta))
    C0 = Delta_inv_sqrt @ C_raw @ Delta_inv_sqrt

    log.info("C0 computed", shape=C0.shape, trace=float(np.trace(C0)))
    return C0


# ==========================================
# データ取得
# ==========================================

def fetch_us_data() -> pd.DataFrame:
    """yfinance から米国セクターETF の warm-up 期間データを取得."""
    log.info("Downloading US sector ETFs from yfinance",
             tickers=US_TICKERS, start="2016-01-01", end="2018-01-01")
    raw = yf.download(US_TICKERS, start="2016-01-01", end="2018-01-01", auto_adjust=True)

    close = raw["Close"][US_TICKERS].copy()
    close_long = close.stack().reset_index()
    close_long.columns = ["Date", "Ticker", "Close"]
    return close_long


def fetch_jp_data(bq_client: bigquery.Client) -> pd.DataFrame:
    """BQ から日本 TOPIX-17 ETF の warm-up 期間データを取得."""
    tickers_str = ", ".join(f"'{t}'" for t in JP_TICKERS)
    query = f"""
    SELECT DATE, TICKER, ADJ_OPEN, ADJ_CLOSE
    FROM `{PROJECT}.STOCK.STOCK_PRICE_JQUANTS`
    WHERE TICKER IN ({tickers_str})
      AND DATE >= '2016-01-01'
      AND DATE <= '2017-12-31'
      AND ADJ_OPEN IS NOT NULL
      AND ADJ_CLOSE IS NOT NULL
    ORDER BY TICKER, DATE
    """
    log.info("Querying BQ for JP sector ETFs (warm-up)")
    job = bq_client.query(query)
    df = job.to_dataframe()
    total_bytes = job.total_bytes_processed or 0
    cost_usd = total_bytes / 1e12 * 6.25
    log.info("BQ query done", rows=len(df), bytes=total_bytes,
             cost_usd=f"{cost_usd:.4f}")
    df["DATE"] = pd.to_datetime(df["DATE"])
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
    # XLC は warm-up 期間に存在しない場合がある → 列を追加して NaN で埋める
    for t in US_TICKERS:
        if t not in pivot_close.columns:
            pivot_close[t] = np.nan
    if "XLC" in pivot_close.columns:
        xlc_mask = pivot_close.index < XLC_INCEPTION
        pivot_close.loc[xlc_mask, "XLC"] = np.nan
    pivot_close = pivot_close[US_TICKERS]
    log_ret = np.log(pivot_close / pivot_close.shift(1))
    return log_ret


def build_jp_returns(df_jp: pd.DataFrame) -> pd.DataFrame:
    """日本セクター open-to-close 対数リターン."""
    df = df_jp.copy()
    df = df.sort_values(["TICKER", "DATE"]).drop_duplicates(["TICKER", "DATE"], keep="last")
    pivot_open = df.pivot(index="DATE", columns="TICKER", values="ADJ_OPEN").sort_index()
    pivot_close = df.pivot(index="DATE", columns="TICKER", values="ADJ_CLOSE").sort_index()
    pivot_open = pivot_open.ffill(limit=5)
    pivot_close = pivot_close.ffill(limit=5)
    pivot_open = pivot_open[JP_TICKERS]
    pivot_close = pivot_close[JP_TICKERS]
    oc = np.log(pivot_close / pivot_open)
    return oc


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

    log.info("Date alignment", total_pairs=len(pair_df),
             us_range=f"{us_dates_aligned[0].date()}..{us_dates_aligned[-1].date()}",
             jp_range=f"{jp_dates_aligned[0].date()}..{jp_dates_aligned[-1].date()}")

    return us_aligned, jp_aligned, date_info


# ==========================================
# メイン
# ==========================================

def main() -> int:
    """C0/V0 を計算して GCS に pickle 保存."""
    ts0 = datetime.now(tz=JST)
    log.info("signal_011_4_prepare_c0 start", ts=ts0.isoformat())

    # GCP クライアント
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    bq_client = bigquery.Client(project=PROJECT, credentials=creds)
    gcs_client = storage.Client(project=PROJECT, credentials=creds)

    # 1. データ取得
    df_us = fetch_us_data()
    df_jp = fetch_jp_data(bq_client)

    # 2. リターン構築
    us_c2c = build_us_returns(df_us)
    jp_oc = build_jp_returns(df_jp)
    log.info("Returns built", us_c2c_shape=us_c2c.shape, jp_oc_shape=jp_oc.shape)

    # 3. 日付アライン
    us_aligned, jp_aligned, date_info = align_common_dates(us_c2c, jp_oc)

    # 4. Warm-up 期間のフィルタ
    jp_dates = date_info["JP_DATE"].values
    warmup_mask = (
        (jp_dates >= np.datetime64(WARMUP_START))
        & (jp_dates <= np.datetime64(WARMUP_END))
    )
    us_warm = np.nan_to_num(us_aligned.values[warmup_mask], nan=0.0)
    jp_warm = np.nan_to_num(jp_aligned.values[warmup_mask], nan=0.0)
    log.info("Warm-up data", days=int(warmup_mask.sum()))

    # 5. V0/C0 計算
    V0 = build_V0()
    C0 = compute_C0(us_warm, jp_warm, V0)

    # 6. pickle 化
    payload = {
        "C0": C0,
        "V0": V0,
        "warmup_start": str(WARMUP_START.date()),
        "warmup_end": str(WARMUP_END.date()),
        "warmup_days": int(warmup_mask.sum()),
        "us_tickers": US_TICKERS,
        "jp_tickers": JP_TICKERS,
        "created_at": datetime.now(tz=JST).isoformat(),
    }
    pkl_bytes = pickle.dumps(payload)
    log.info("Pickle created", size_bytes=len(pkl_bytes))

    # 7. GCS アップロード
    bucket = gcs_client.bucket(GCS_BUCKET)
    blob = bucket.blob(GCS_PATH)
    blob.upload_from_string(pkl_bytes, content_type="application/octet-stream")
    log.info("Uploaded to GCS",
             path=f"gs://{GCS_BUCKET}/{GCS_PATH}",
             size_bytes=len(pkl_bytes))

    # 8. ローカルにもバックアップ
    local_path = Path(r"C:\tmp\signal_011_4")
    local_path.mkdir(parents=True, exist_ok=True)
    local_file = local_path / "c0_v0.pkl"
    local_file.write_bytes(pkl_bytes)
    log.info("Local backup saved", path=str(local_file))

    ts1 = datetime.now(tz=JST)
    elapsed = (ts1 - ts0).total_seconds()
    log.info("Done", elapsed_s=f"{elapsed:.1f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

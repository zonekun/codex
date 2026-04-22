# -*- coding: utf-8 -*-
"""野菜価格急騰イベント後の空売り戦略バックテスト.

シグナル: Δ平年比の白色化残差が訓練期間の上位15%を超えた週
エントリー: シグナル週の終値で空売り（翌週以降の実際運用では翌週月曜が望ましいが
            週次データなのでシグナル週同週エントリーとして近似）
イグジット: エントリーから2週後
対象: くら寿司(2695), 松屋フーズHD(9887), 王将フードサービス(9936), イオン(8267)

分割:
  訓練期間 2017/11 〜 2021/12: ARモデル学習 + 閾値決定
  テスト期間 2022/01 〜 2026/02: アウトオブサンプル検証

手数料: 片道0.1% (往復0.2%)
"""

import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account
from statsmodels.tsa.ar_model import AutoReg

warnings.filterwarnings("ignore")

# --- 設定 ---
KEY_PATH   = Path(r"C:\users\Administrator\Dropbox\claude\investment-agent\keys\gcp-service-account.json")
PROJECT_ID = "gmailpj-357912"
CACHE_DIR  = Path(__file__).parent.parent / "data" / "cache"
GENGO      = {"\u4ee4\u548c": 2018, "\u5e73\u6210": 1988, "\u662d\u548c": 1925}

TRAIN_END  = pd.Timestamp("2021-12-31")
HOLD_WEEKS = 2          # ホールド期間
THRESHOLD_PCT = 85      # イベント閾値パーセンタイル（上位15%）
COMMISSION = 0.001      # 片道手数料 0.1%
INITIAL_CAPITAL = 10_000_000  # 初期資金 1000万円

# バックテスト対象銘柄（v3でCAR有意）
TARGETS = {
    "\u304f\u3089\u5bff\u53f8":           "2695",
    "\u677e\u5c4b\u30d5\u30fc\u30baHD":   "9887",
    "\u738b\u5c06\u30d5\u30fc\u30c9\u30b5\u30fc\u30d3\u30b9": "9936",
    "\u30a4\u30aa\u30f3":                 "8267",
}

# ============================================================
# データ読み込み（v3と同じ）
# ============================================================
def parse_jp_date(s: str):
    m = re.match(r"([\u4ee4\u548c\u5e73\u6210\u662d\u548c]{2})(\d+)\u5e74(\d+)\u6708(\d+)\u65e5", str(s).strip())
    if not m:
        return None
    g, y, mo, d = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return pd.Timestamp(GENGO.get(g, 0) + y, mo, d)


def load_yasai_heinen() -> pd.DataFrame:
    def _load(path):
        raw = pd.read_excel(path, sheet_name="\u5e73\u5e74\u6bd4", header=None)
        rows = []
        for _, row in raw.iloc[2:].iterrows():
            dt = parse_jp_date(row.iloc[0])
            if dt is None:
                continue
            vals = {}
            for i in range(1, len(row)):
                try:
                    vals[f"c{i}"] = float(row.iloc[i])
                except Exception:
                    vals[f"c{i}"] = np.nan
            rows.append((dt, vals))
        if not rows:
            return pd.DataFrame(columns=["HEINEN_AVG"])
        idx, data = zip(*rows)
        df = pd.DataFrame(list(data), index=pd.DatetimeIndex(idx))
        df.sort_index(inplace=True)
        df["HEINEN_AVG"] = df.mean(axis=1)
        return df[["HEINEN_AVG"]]

    df = pd.concat([
        _load(CACHE_DIR / "yasai_price_maff_past.xlsx"),
        _load(CACHE_DIR / "yasai_price_maff.xlsx"),
    ])
    return df[~df.index.duplicated(keep="last")].sort_index().dropna()


WEEKLY_PRICE_SQL = """
WITH
wld AS (
  SELECT TICKER,
         DATE_TRUNC(YEARDATE, WEEK(MONDAY)) AS WS,
         MAX(YEARDATE) AS LTD
  FROM `gmailpj-357912.STOCK.STOCK_PRICE`
  WHERE TICKER IN UNNEST(@tickers)
    AND YEARDATE >= '2017-01-01'
    AND CLOSE IS NOT NULL AND CLOSE > 0
  GROUP BY TICKER, WS
),
wc AS (
  SELECT w.TICKER, w.WS, s.CLOSE AS WEEK_CLOSE
  FROM wld w
  JOIN `gmailpj-357912.STOCK.STOCK_PRICE` s
    ON w.TICKER = s.TICKER AND w.LTD = s.YEARDATE
)
SELECT TICKER, WS, WEEK_CLOSE
FROM wc
ORDER BY TICKER, WS
"""


def fetch_weekly_prices(tickers: list) -> pd.DataFrame:
    print(f"BigQuery: {len(tickers)}\u9280\u67c4\u306e\u9031\u6b21\u7d42\u5024\u53d6\u5f97\u4e2d...")
    creds = service_account.Credentials.from_service_account_file(str(KEY_PATH))
    client = bigquery.Client(project=PROJECT_ID, credentials=creds)
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("tickers", "STRING", tickers)]
    )
    df = client.query(WEEKLY_PRICE_SQL, job_config=cfg).to_dataframe()
    df["WS"] = pd.to_datetime(df["WS"]).dt.tz_localize(None)
    price_wide = df.pivot(index="WS", columns="TICKER", values="WEEK_CLOSE")
    price_wide.index = pd.to_datetime(price_wide.index)
    # 週次リターン計算
    ret_wide = price_wide.pct_change()
    print(f"  -> {price_wide.index.min().date()} ~ {price_wide.index.max().date()}, "
          f"{len(price_wide)}\u9031")
    return price_wide, ret_wide


# ============================================================
# AR モデルで事前白色化
# ============================================================
def fit_ar(series: np.ndarray, max_ar: int = 8) -> tuple:
    """AIC最小のAR(p)を学習して返す。"""
    best_aic = np.inf
    best_fit = None
    best_p   = 1
    for p in range(1, max_ar + 1):
        try:
            fit = AutoReg(series, lags=p, old_names=False).fit()
            if fit.aic < best_aic:
                best_aic = fit.aic
                best_fit = fit
                best_p   = p
        except Exception:
            break
    return best_fit, best_p


def apply_ar_residual(fit, series: np.ndarray) -> np.ndarray:
    """学習済みAR(p)をnew seriesに適用して残差を返す。"""
    lags_list = fit.model.ar_lags   # [1, 2, ..., p] のリスト
    p = max(lags_list) if lags_list else 1
    if len(series) <= p:
        return np.full(len(series), np.nan)
    resid = np.full(len(series), np.nan)
    # AR(p) 残差: e_t = x_t - (intercept + sum(coef_k * x_{t-k}))
    intercept = fit.params[0]
    coefs     = fit.params[1:p+1]  # lag1, lag2, ..., lagp
    for i in range(p, len(series)):
        lag_vals = np.array([series[i - lag] for lag in lags_list])
        fitted = intercept + np.dot(coefs, lag_vals)
        resid[i] = series[i] - fitted
    return resid


# ============================================================
# バックテストロジック
# ============================================================
def run_backtest(dh_all: np.ndarray, dates_all: pd.DatetimeIndex,
                 price_wide: pd.DataFrame, ret_wide: pd.DataFrame,
                 company: str, ticker: str) -> pd.DataFrame:
    """1銘柄の空売りバックテストを実行。"""
    # 訓練/テスト分割
    train_mask = dates_all <= TRAIN_END
    test_mask  = dates_all > TRAIN_END

    dh_train = dh_all[train_mask]
    dh_test  = dh_all[test_mask]
    dates_test = dates_all[test_mask]

    if len(dh_train) < 50 or len(dh_test) < 20:
        return pd.DataFrame()

    # AR学習（訓練期間のΔ平年比）
    ar_fit, ar_p = fit_ar(dh_train)
    if ar_fit is None:
        return pd.DataFrame()

    # テスト期間全体に残差を計算（訓練データ末尾をバッファとして使用）
    dh_full = dh_all  # ARには全体系列を使ってテスト期間の残差を得る
    resid_full = apply_ar_residual(ar_fit, dh_full)

    # 訓練期間の残差でイベント閾値を決定
    train_resid = resid_full[train_mask]
    threshold   = np.nanpercentile(train_resid[~np.isnan(train_resid)], THRESHOLD_PCT)

    # テスト期間の残差
    test_resid = resid_full[test_mask]

    # 株価データ準備
    if company not in price_wide.columns:
        return pd.DataFrame()
    price_s = price_wide[company].dropna()
    ret_s   = ret_wide[company].dropna()

    trades = []
    n_test = len(dates_test)

    for i, (dt, resid) in enumerate(zip(dates_test, test_resid)):
        if np.isnan(resid) or resid < threshold:
            continue

        # シグナル週の終値で空売りエントリー
        # 実務では翌週月曜エントリー。週次データなので「翌週の終値」をエントリーとする
        entry_week_idx = i + 1  # 翌週インデックス
        if entry_week_idx >= n_test:
            continue
        entry_date = dates_test[entry_week_idx]

        # イグジット週
        exit_week_idx = entry_week_idx + HOLD_WEEKS
        if exit_week_idx >= n_test:
            continue
        exit_date = dates_test[exit_week_idx]

        # 価格確認
        if entry_date not in price_s.index or exit_date not in price_s.index:
            continue
        entry_price = price_s.loc[entry_date]
        exit_price  = price_s.loc[exit_date]

        # 空売りP&L（手数料込み）
        gross_ret = (entry_price - exit_price) / entry_price  # 空売り利益
        net_ret   = gross_ret - COMMISSION * 2  # 往復手数料

        trades.append({
            "signal_date": dt,
            "entry_date": entry_date,
            "exit_date": exit_date,
            "entry_price": round(entry_price, 1),
            "exit_price":  round(exit_price, 1),
            "gross_ret_pct": round(gross_ret * 100, 3),
            "net_ret_pct":   round(net_ret * 100, 3),
            "dh_resid": round(resid, 4),
        })

    return pd.DataFrame(trades)


def calc_metrics(trade_df: pd.DataFrame, company: str) -> dict:
    """バックテスト統計量を計算。"""
    if trade_df.empty:
        return {"company": company, "n_trades": 0}

    rets = trade_df["net_ret_pct"].values / 100
    wins = (rets > 0).sum()
    n    = len(rets)

    # 累積リターン曲線
    cum = np.cumprod(1 + rets)
    max_dd = 0
    peak = cum[0]
    for c in cum:
        if c > peak:
            peak = c
        dd = (peak - c) / peak
        if dd > max_dd:
            max_dd = dd

    # シャープレシオ（年率換算: 週次 × √52）
    mean_w = np.mean(rets)
    std_w  = np.std(rets, ddof=1) if n > 1 else np.nan
    sharpe_annual = (mean_w / std_w * np.sqrt(52)) if (std_w and std_w > 0) else np.nan

    # 年率リターン: 累積リターンを期間で年率化
    first_date = trade_df["entry_date"].min()
    last_date  = trade_df["exit_date"].max()
    years = (last_date - first_date).days / 365.25
    total_ret = cum[-1] - 1
    annual_ret = (1 + total_ret) ** (1 / max(years, 0.1)) - 1 if years > 0 else np.nan

    return {
        "company":      company,
        "n_trades":     n,
        "win_rate_%":   round(wins / n * 100, 1),
        "avg_ret_%":    round(mean_w * 100, 3),
        "total_ret_%":  round(total_ret * 100, 2),
        "annual_ret_%": round(annual_ret * 100, 2),
        "sharpe_52w":   round(sharpe_annual, 3) if not np.isnan(sharpe_annual) else "N/A",
        "max_dd_%":     round(max_dd * 100, 2),
    }


# ============================================================
# メイン
# ============================================================
def main():
    print("=" * 70)
    print("\u91ce\u83dc\u4fa1\u683c\u6025\u9a30\u30a4\u30d9\u30f3\u30c8\u5f8c \u7a7a\u58f2\u308a\u6226\u7565 \u30d0\u30c3\u30af\u30c6\u30b9\u30c8")
    print(f"  \u8a93\u7df4\u671f\u9593: 2017/11 ~ 2021/12  (\u30c6\u30b9\u30c8\u671f\u9593: 2022/01 ~ 2026/02)")
    print(f"  \u30db\u30fc\u30eb\u30c9\u671f\u9593: {HOLD_WEEKS}\u9031  |\u95be\u5024: \u4e0a\u4f4d{100-THRESHOLD_PCT}%  |\u624b\u6570\u6599: {COMMISSION*100:.1f}%\u7247\u9053")
    print("=" * 70)

    # データ読み込み
    yasai  = load_yasai_heinen()
    h      = yasai["HEINEN_AVG"].values
    dh     = np.diff(h)
    dates  = yasai.index[1:]

    tickers = list(TARGETS.values())
    price_wide, ret_wide = fetch_weekly_prices(tickers)

    # TICKER -> 会社名
    rev = {v: k for k, v in TARGETS.items()}
    price_wide.columns = [rev.get(c, c) for c in price_wide.columns]
    ret_wide.columns   = [rev.get(c, c) for c in ret_wide.columns]

    # 各銘柄バックテスト
    all_metrics = []
    all_trades  = {}

    for company, ticker in TARGETS.items():
        trade_df = run_backtest(dh, dates, price_wide, ret_wide, company, ticker)
        metrics  = calc_metrics(trade_df, company)
        all_metrics.append(metrics)
        all_trades[company] = trade_df
        print(f"\n--- {company} ({ticker}) ---")
        if trade_df.empty:
            print("  \u30c8\u30ec\u30fc\u30c9\u306a\u3057")
        else:
            print(trade_df[["signal_date","entry_date","exit_date",
                            "entry_price","exit_price","net_ret_pct"]].to_string(index=False))

    # サマリー表示
    print("\n" + "=" * 70)
    print("\u300c\u30b5\u30de\u30ea\u30fc\u300d\u30a2\u30a6\u30c8\u30aa\u30d6\u30b5\u30f3\u30d7\u30eb\u30d0\u30c3\u30af\u30c6\u30b9\u30c8 (2022/01~2026/02)")
    print("=" * 70)
    metrics_df = pd.DataFrame(all_metrics)
    print(metrics_df.to_string(index=False))

    # バスケット戦略（全銘柄で同時にシグナルが出た場合の均等分散）
    print("\n" + "=" * 70)
    print("\u300c\u30d0\u30b9\u30b1\u30c3\u30c8\u6226\u7565\u300d\u5168\u9280\u67c4\u5747\u7b49\u5206\u6563\u30b7\u30e7\u30fc\u30c8")
    print("=" * 70)
    basket_rets = []
    all_trade_rows = []
    for company, td in all_trades.items():
        if not td.empty:
            for _, row in td.iterrows():
                all_trade_rows.append({
                    "entry_date": row["entry_date"],
                    "exit_date": row["exit_date"],
                    "net_ret": row["net_ret_pct"] / 100,
                    "company": company,
                })

    if all_trade_rows:
        bt_df = pd.DataFrame(all_trade_rows)
        # 同じエントリー日でまとめて平均リターン
        basket = (bt_df.groupby(["entry_date", "exit_date"])["net_ret"]
                  .mean().reset_index().sort_values("entry_date"))
        basket["cum_ret"] = (1 + basket["net_ret"]).cumprod() - 1
        n_b = len(basket)
        wins_b = (basket["net_ret"] > 0).sum()
        print(f"\u30c8\u30ec\u30fc\u30c9\u6570: {n_b}  \u52dd\u7387: {wins_b/n_b*100:.1f}%")
        print(f"\u5e73\u5747\u30ea\u30bf\u30fc\u30f3: {basket['net_ret'].mean()*100:.3f}%  \u7d2f\u7a4d: {basket['cum_ret'].iloc[-1]*100:.2f}%")
        print(basket[["entry_date","exit_date","net_ret","cum_ret"]].to_string(index=False))

    # 判定
    print("\n" + "=" * 70)
    m = metrics_df
    pass_count = 0
    for _, row in m.iterrows():
        if row.get("n_trades", 0) >= 3 and row.get("win_rate_%", 0) >= 55:
            pass_count += 1
    if pass_count >= 2:
        print("-> BACKTEST_PASS: \u83ab\u30c7\u30e2\u30c8\u30ec\u30fc\u30c9\u79fb\u884c\u3092\u691c\u8a0e")
    else:
        print("-> BACKTEST_FAIL: \u6709\u610f\u306a\u7d50\u679c\u306a\u3057\u3002\u30d1\u30e9\u30e1\u30fc\u30bf\u8abf\u6574\u307e\u305f\u306f\u88dc\u52a9\u6307\u6a19\u306e\u691c\u8a0e\u304c\u5fc5\u8981")


if __name__ == "__main__":
    main()

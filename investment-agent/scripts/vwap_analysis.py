"""
VWAPトレンド戦略バックテスト（日足ベース）
週次・年次アンカードVWAP ±1σ バンドへのタッチ後リターン検証
"""
import os
import sys
import warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

for line in open(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from google.cloud import bigquery
from google.oauth2 import service_account
import pandas as pd
import numpy as np
from scipy import stats
from datetime import datetime

KEY    = os.path.join(os.path.dirname(__file__), "..", "keys", "gcp-service-account.json")
PROJ   = "gmailpj-357912"
creds  = service_account.Credentials.from_service_account_file(KEY)
client = bigquery.Client(project=PROJ, credentials=creds)

# ============================================================
# ① BQ: 週次・年次アンカードVWAP + 出来高加重標準偏差
# ============================================================
print(f"[{datetime.now():%H:%M:%S}] BQからVWAP計算中...")

SQL_VWAP = """
WITH base AS (
  SELECT
    YEARDATE,
    TICKER,
    OPEN, HIGH, LOW, CLOSE, VOLUME,
    (HIGH + LOW + CLOSE) / 3.0             AS tp,
    DATE_TRUNC(YEARDATE, WEEK(MONDAY))     AS week_anchor,
    DATE_TRUNC(YEARDATE, YEAR)             AS year_anchor
  FROM `gmailpj-357912.STOCK.STOCK_PRICE`
  WHERE YEARDATE BETWEEN '2021-01-01' AND '2026-03-06'
    AND CLOSE IS NOT NULL AND VOLUME > 0
    AND HIGH IS NOT NULL AND LOW IS NOT NULL
    AND HIGH >= LOW
),
vwap_calc AS (
  SELECT *,
    SUM(tp * VOLUME) OVER w_week / NULLIF(SUM(VOLUME) OVER w_week, 0) AS w_vwap,
    SUM(tp * tp * VOLUME) OVER w_week / NULLIF(SUM(VOLUME) OVER w_week, 0) AS w_e_tp2,
    SUM(VOLUME) OVER w_week AS w_cum_vol,
    SUM(tp * VOLUME) OVER w_year / NULLIF(SUM(VOLUME) OVER w_year, 0) AS y_vwap,
    SUM(tp * tp * VOLUME) OVER w_year / NULLIF(SUM(VOLUME) OVER w_year, 0) AS y_e_tp2,
    SUM(VOLUME) OVER w_year AS y_cum_vol
  FROM base
  WINDOW
    w_week AS (PARTITION BY TICKER, week_anchor ORDER BY YEARDATE
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
    w_year AS (PARTITION BY TICKER, year_anchor ORDER BY YEARDATE
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
)
SELECT
  YEARDATE, TICKER, OPEN, HIGH, LOW, CLOSE, VOLUME, tp,
  week_anchor, year_anchor,
  w_vwap,
  SQRT(GREATEST(w_e_tp2 - w_vwap * w_vwap, 0)) AS w_sigma,
  w_cum_vol,
  y_vwap,
  SQRT(GREATEST(y_e_tp2 - y_vwap * y_vwap, 0)) AS y_sigma,
  y_cum_vol
FROM vwap_calc
ORDER BY TICKER, YEARDATE
"""

df = client.query(SQL_VWAP).to_dataframe()
print(f"[{datetime.now():%H:%M:%S}]   取得完了: {len(df):,}行, {df.TICKER.nunique():,}銘柄")

# バンド計算
df["w_upper"] = df["w_vwap"] + df["w_sigma"]
df["w_lower"] = df["w_vwap"] - df["w_sigma"]
df["y_upper"] = df["y_vwap"] + df["y_sigma"]
df["y_lower"] = df["y_vwap"] - df["y_sigma"]

# sigma=0（週初など累積1日分）は除外
df = df[(df["w_sigma"] > 0) & (df["y_sigma"] > 0)].copy()
df = df.sort_values(["TICKER", "YEARDATE"]).reset_index(drop=True)

# ============================================================
# ② タッチシグナル検出
# ============================================================
print(f"[{datetime.now():%H:%M:%S}] シグナル検出中...")

SLOPE_DAYS   = 5      # バンド傾斜を計算する日数
SLOPE_THRESH = 0.0002 # バンドの最小傾斜（0.02%/日）

def detect_touches(grp, vwap_col, upper_col, lower_col):
    grp = grp.copy().reset_index(drop=True)
    n = len(grp)
    if n < SLOPE_DAYS + 2:
        return pd.DataFrame()

    signals = []
    for i in range(SLOPE_DAYS, n):
        upper      = grp[upper_col].iloc[i]
        lower      = grp[lower_col].iloc[i]
        close      = grp["CLOSE"].iloc[i]
        low        = grp["LOW"].iloc[i]
        high       = grp["HIGH"].iloc[i]
        prev_close = grp["CLOSE"].iloc[i - 1]
        prev_upper = grp[upper_col].iloc[i - 1]
        prev_lower = grp[lower_col].iloc[i - 1]

        ref_upper  = grp[upper_col].iloc[i - SLOPE_DAYS]
        ref_lower  = grp[lower_col].iloc[i - SLOPE_DAYS]
        upper_slope = (upper - ref_upper) / (ref_upper + 1e-9)
        lower_slope = (lower - ref_lower) / (abs(ref_lower) + 1e-9)

        date   = grp["YEARDATE"].iloc[i]
        ticker = grp["TICKER"].iloc[i]

        # ロングシグナル: バンド上昇トレンド × 押し目タッチ
        if (upper_slope > SLOPE_THRESH
                and prev_close > prev_upper
                and low <= upper
                and close > upper * 0.97):
            signals.append({
                "date": date, "ticker": ticker,
                "signal": "LONG",
                "entry_close": close,
                "vwap": grp[vwap_col].iloc[i],
                "band": upper,
                "slope": upper_slope,
            })

        # ショートシグナル: バンド下降トレンド × 戻りタッチ
        if (lower_slope < -SLOPE_THRESH
                and prev_close < prev_lower
                and high >= lower
                and close < lower * 1.03):
            signals.append({
                "date": date, "ticker": ticker,
                "signal": "SHORT",
                "entry_close": close,
                "vwap": grp[vwap_col].iloc[i],
                "band": lower,
                "slope": lower_slope,
            })

    return pd.DataFrame(signals)


sig_w = df.groupby("TICKER", group_keys=False).apply(
    lambda g: detect_touches(g, "w_vwap", "w_upper", "w_lower")
)
sig_y = df.groupby("TICKER", group_keys=False).apply(
    lambda g: detect_touches(g, "y_vwap", "y_upper", "y_lower")
)
sig_w["anchor"] = "weekly"
sig_y["anchor"] = "annual"
sigs = pd.concat([sig_w, sig_y], ignore_index=True)
print(f"[{datetime.now():%H:%M:%S}]   シグナル数: {len(sigs):,}  (週次={len(sig_w):,}, 年次={len(sig_y):,})")

# ============================================================
# ③ フォワードリターン計算
# ============================================================
print(f"[{datetime.now():%H:%M:%S}] フォワードリターン計算中...")

pivot         = df.pivot_table(index="YEARDATE", columns="TICKER", values="CLOSE")
dates_sorted  = sorted(pivot.index)
date_to_idx   = {d: i for i, d in enumerate(dates_sorted)}

HORIZONS = [1, 3, 5, 10]

def get_fwd_return(row, n):
    idx = date_to_idx.get(row["date"])
    if idx is None or idx + n >= len(dates_sorted):
        return np.nan
    t0 = row["entry_close"]
    tn = pivot.at[dates_sorted[idx + n], row["ticker"]] if row["ticker"] in pivot.columns else np.nan
    if pd.isna(tn) or t0 == 0:
        return np.nan
    ret = (tn / t0 - 1)
    return ret if row["signal"] == "LONG" else -ret

for h in HORIZONS:
    sigs[f"ret_{h}d"] = sigs.apply(lambda r: get_fwd_return(r, h), axis=1)

# ============================================================
# ④ 統計検定
# ============================================================
print(f"\n[{datetime.now():%H:%M:%S}] 統計検定結果")
print("=" * 72)
print(f"{'アンカー':8s}  {'N日後':5s}  {'n':>6s}  {'平均リターン':>10s}  {'勝率':>7s}  {'t値':>7s}  {'p値':>7s}")
print("-" * 72)

results = []
for anchor in ["weekly", "annual"]:
    for h in HORIZONS:
        col = f"ret_{h}d"
        s   = sigs[sigs["anchor"] == anchor][col].dropna()
        if len(s) < 30:
            continue
        t_stat, p_val = stats.ttest_1samp(s, 0)
        mean_r = s.mean()
        win_r  = (s > 0).mean()
        mark   = " ★" if p_val < 0.05 else (" △" if p_val < 0.10 else "")
        results.append({
            "anchor": anchor, "horizon_days": h, "n": len(s),
            "mean_ret_pct": round(mean_r * 100, 4),
            "win_rate_pct": round(win_r * 100, 2),
            "t_stat": round(t_stat, 4), "p_val": round(p_val, 6),
        })
        print(f"  {anchor:8s}  {h:>3d}日後  {len(s):>6,}  "
              f"{mean_r*100:>+9.3f}%  {win_r*100:>6.1f}%  "
              f"{t_stat:>+7.3f}  {p_val:>7.4f}{mark}")

print()
print("--- ロング / ショート 別（全アンカー合算）---")
print(f"{'シグナル':8s}  {'N日後':5s}  {'n':>6s}  {'平均リターン':>10s}  {'勝率':>7s}  {'t値':>7s}  {'p値':>7s}")
print("-" * 72)
for sig_type in ["LONG", "SHORT"]:
    for h in HORIZONS:
        col = f"ret_{h}d"
        s   = sigs[sigs["signal"] == sig_type][col].dropna()
        if len(s) < 10:
            continue
        t_stat, p_val = stats.ttest_1samp(s, 0)
        mean_r = s.mean()
        win_r  = (s > 0).mean()
        mark   = " ★" if p_val < 0.05 else (" △" if p_val < 0.10 else "")
        print(f"  {sig_type:8s}  {h:>3d}日後  {len(s):>6,}  "
              f"{mean_r*100:>+9.3f}%  {win_r*100:>6.1f}%  "
              f"{t_stat:>+7.3f}  {p_val:>7.4f}{mark}")

# ============================================================
# ⑤ 保存
# ============================================================
os.makedirs(os.path.join(os.path.dirname(__file__), "..", "data", "csv"), exist_ok=True)
sigs_path    = os.path.join(os.path.dirname(__file__), "..", "data", "csv", "vwap_signals.csv")
results_path = os.path.join(os.path.dirname(__file__), "..", "data", "csv", "vwap_results.csv")
sigs.to_csv(sigs_path, index=False, encoding="utf-8")
pd.DataFrame(results).to_csv(results_path, index=False, encoding="utf-8")
print(f"\n[{datetime.now():%H:%M:%S}] 保存完了")
print(f"  シグナル : {sigs_path}")
print(f"  結果     : {results_path}")
print(f"\nシグナル合計: {len(sigs):,}  (LONG={( sigs.signal=='LONG').sum():,}, SHORT={(sigs.signal=='SHORT').sum():,})")

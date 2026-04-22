"""
VWAPトレンド戦略 バックテスト
- エントリー: シグナル翌営業日の始値（寄付き）
- エグジット: エントリーからN日後の終値
- 取引コスト: 往復 0.2%
- 流動性フィルタ: シグナル日の出来高 > 100,000株
- 同時保有上限: 各方向20銘柄（傾斜上位から選択）
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
from datetime import datetime

KEY    = os.path.join(os.path.dirname(__file__), "..", "keys", "gcp-service-account.json")
PROJ   = "gmailpj-357912"
creds  = service_account.Credentials.from_service_account_file(KEY)
client = bigquery.Client(project=PROJ, credentials=creds)

# ============================================================
# パラメータ
# ============================================================
HOLD_DAYS       = 10      # 保有日数
COST_RT         = 0.002   # 往復コスト（0.2%）
MIN_VOLUME      = 100_000 # 流動性フィルタ（株/日）
MAX_POSITIONS   = 20      # 同時保有上限（方向ごと）
SLOPE_DAYS      = 5
SLOPE_THRESH    = 0.0002

# ============================================================
# ① シグナルファイル読み込み（前回分析結果を再利用）
# ============================================================
sig_path = os.path.join(os.path.dirname(__file__), "..", "data", "csv", "vwap_signals.csv")
print(f"[{datetime.now():%H:%M:%S}] シグナルファイル読み込み...")
sigs = pd.read_csv(sig_path)
sigs["date"] = pd.to_datetime(sigs["date"])
sigs = sigs[sigs["anchor"] == "weekly"].copy()  # 週次アンカーのみ
print(f"  週次シグナル: {len(sigs):,}件")

# ============================================================
# ② 価格データ取得（始値・終値・出来高）
# ============================================================
print(f"[{datetime.now():%H:%M:%S}] BQから価格データ取得中...")
SQL_PRICE = """
SELECT YEARDATE, TICKER, OPEN, CLOSE, VOLUME
FROM `gmailpj-357912.STOCK.STOCK_PRICE`
WHERE YEARDATE BETWEEN '2021-01-01' AND '2026-03-06'
  AND CLOSE IS NOT NULL AND OPEN IS NOT NULL AND VOLUME > 0
ORDER BY TICKER, YEARDATE
"""
price_df = client.query(SQL_PRICE).to_dataframe()
print(f"  取得完了: {len(price_df):,}行")

# 日付型をdatetime64に統一
price_df["YEARDATE"] = pd.to_datetime(price_df["YEARDATE"])

# pivot（終値・始値・出来高）
close_pivot  = price_df.pivot_table(index="YEARDATE", columns="TICKER", values="CLOSE")
open_pivot   = price_df.pivot_table(index="YEARDATE", columns="TICKER", values="OPEN")
vol_pivot    = price_df.pivot_table(index="YEARDATE", columns="TICKER", values="VOLUME")
dates_sorted = sorted(close_pivot.index)
date_to_idx  = {d: i for i, d in enumerate(dates_sorted)}

# ============================================================
# ③ バックテスト
# ============================================================
print(f"[{datetime.now():%H:%M:%S}] バックテスト実行中...")

# 流動性フィルタ
sigs["date_key"] = pd.to_datetime(sigs["date"]).dt.date
sigs["vol_on_signal"] = sigs.apply(
    lambda r: vol_pivot.at[r["date"], r["ticker"]]
    if r["ticker"] in vol_pivot.columns and r["date"] in vol_pivot.index
    else 0,
    axis=1
)
sigs = sigs[sigs["vol_on_signal"] >= MIN_VOLUME].copy()
print(f"  流動性フィルタ後: {len(sigs):,}件")

# 同時保有上限（傾斜上位から選択）
sigs["abs_slope"] = sigs["slope"].abs()
sigs_filtered = (
    sigs
    .sort_values(["date", "signal", "abs_slope"], ascending=[True, True, False])
    .groupby(["date", "signal"])
    .head(MAX_POSITIONS)
    .reset_index(drop=True)
)
print(f"  同時保有上限フィルタ後: {len(sigs_filtered):,}件")

trades = []
for _, row in sigs_filtered.iterrows():
    sig_date = row["date"]
    ticker   = row["ticker"]
    sig_type = row["signal"]

    idx = date_to_idx.get(sig_date)
    if idx is None or idx + HOLD_DAYS >= len(dates_sorted):
        continue
    if ticker not in open_pivot.columns:
        continue

    # エントリー: 翌営業日の始値
    entry_date  = dates_sorted[idx + 1]
    entry_price = open_pivot.at[entry_date, ticker]
    if pd.isna(entry_price) or entry_price == 0:
        continue

    # エグジット: HOLD_DAYS日後の終値
    exit_date  = dates_sorted[idx + HOLD_DAYS]
    exit_price = close_pivot.at[exit_date, ticker]
    if pd.isna(exit_price) or exit_price == 0:
        continue

    # 損益計算
    raw_ret = (exit_price / entry_price - 1)
    if sig_type == "SHORT":
        raw_ret = -raw_ret
    net_ret = raw_ret - COST_RT  # 取引コスト控除

    trades.append({
        "sig_date":    sig_date,
        "entry_date":  entry_date,
        "exit_date":   exit_date,
        "ticker":      ticker,
        "signal":      sig_type,
        "entry_price": entry_price,
        "exit_price":  exit_price,
        "raw_ret":     raw_ret,
        "net_ret":     net_ret,
        "slope":       row["slope"],
    })

trades_df = pd.DataFrame(trades)
print(f"  有効トレード数: {len(trades_df):,}件")

# ============================================================
# ④ パフォーマンス集計
# ============================================================
print(f"\n[{datetime.now():%H:%M:%S}] パフォーマンス集計")
print("=" * 65)

def summarize(df, label):
    if len(df) == 0:
        print(f"  {label}: データなし")
        return {}
    n        = len(df)
    mean_net = df["net_ret"].mean()
    mean_raw = df["raw_ret"].mean()
    win_rate = (df["net_ret"] > 0).mean()
    sharpe   = df["net_ret"].mean() / (df["net_ret"].std() + 1e-9) * np.sqrt(252 / max(HOLD_DAYS, 1))
    total    = df["net_ret"].sum()
    print(f"  [{label}]")
    print(f"    トレード数  : {n:,}")
    print(f"    平均リターン: {mean_net*100:+.3f}% (コスト前: {mean_raw*100:+.3f}%)")
    print(f"    勝率        : {win_rate*100:.1f}%")
    print(f"    Sharpe比    : {sharpe:+.3f} (年率換算)")
    print(f"    累積リターン: {total*100:+.1f}%")
    return {"label": label, "n": n, "mean_net_pct": round(mean_net*100, 4),
            "win_rate_pct": round(win_rate*100, 2), "sharpe": round(sharpe, 4)}

results = []
results.append(summarize(trades_df, "全シグナル"))
if len(trades_df) > 0:
    results.append(summarize(trades_df[trades_df["signal"] == "LONG"], "LONG"))
    results.append(summarize(trades_df[trades_df["signal"] == "SHORT"], "SHORT"))

# 年別集計
print(f"\n--- 年別パフォーマンス（全シグナル）---")
trades_df["year"] = pd.to_datetime(trades_df["entry_date"]).dt.year
for year, grp in trades_df.groupby("year"):
    mean_net = grp["net_ret"].mean()
    win_rate = (grp["net_ret"] > 0).mean()
    print(f"  {year}: n={len(grp):5,}  平均={mean_net*100:+.3f}%  勝率={win_rate*100:.1f}%")

# ============================================================
# ⑤ 保存
# ============================================================
os.makedirs(os.path.join(os.path.dirname(__file__), "..", "data", "csv"), exist_ok=True)
trades_path  = os.path.join(os.path.dirname(__file__), "..", "data", "csv", "vwap_backtest_trades.csv")
results_path = os.path.join(os.path.dirname(__file__), "..", "data", "csv", "vwap_backtest_results.csv")
trades_df.to_csv(trades_path, index=False, encoding="utf-8")
pd.DataFrame([r for r in results if r]).to_csv(results_path, index=False, encoding="utf-8")
print(f"\n[{datetime.now():%H:%M:%S}] 保存完了")
print(f"  トレード詳細: {trades_path}")
print(f"  集計結果    : {results_path}")

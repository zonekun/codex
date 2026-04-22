"""
d_atr_5 上位N% ロングのみ バックテスト（案C: 日次PnLモデル）

シグナル: d_atr_5（日中変動5日MA）上位N%銘柄を毎日選定
保有期間: 20営業日（ローリング）
ポジション: 等金額ウェイト

【日次PnLモデル（案C）の仕組み】
  シグナル日S に選定 → S+1〜S+20 の間ポジション保有
  任意の日D のポートフォリオ = D-20〜D-1 に選定された全銘柄の等金額合算
  → 重複保有を正しく扱い、真の日次P&Lを算出

ベンチマーク: TOPIX / 日経225 / 全銘柄均等

前提: analyze_uki_predictor.py で生成したキャッシュが存在すること

Usage:
    PYTHONUTF8=1 python scripts/backtest_datr_long.py
    PYTHONUTF8=1 python scripts/backtest_datr_long.py --top 10
    PYTHONUTF8=1 python scripts/backtest_datr_long.py --top 30
"""

import os, sys, argparse, warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import subprocess
from datetime import datetime

warnings.filterwarnings("ignore")
plt.rcParams['font.family'] = 'Yu Gothic'

OUT_DIR    = os.path.join(os.path.dirname(__file__), '..', 'data', 'csv', 'uki_predictor')
CACHE_TECH = os.path.join(OUT_DIR, "tech_features_v2.parquet")
TEST_START = "2023-06-01"
HOLD_DAYS  = 20

def log(msg): print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=float, default=20.0, help="上位N%をロング対象（デフォルト20）")
    return p.parse_args()


# ==========================================
# データ読み込み
# ==========================================
def load_data() -> pd.DataFrame:
    import pyarrow.parquet as pq
    def read_no_meta(path):
        tbl = pq.read_table(path)
        tbl = tbl.replace_schema_metadata({})
        return tbl.to_pandas()

    log("キャッシュ読み込み...")
    tech = read_no_meta(CACHE_TECH)
    tech["YEARDATE"] = pd.to_datetime(tech["YEARDATE"])

    cols = ["YEARDATE", "TICKER", "CLOSE", "d_atr_5"]
    cols = [c for c in cols if c in tech.columns]
    tech = tech[cols].dropna(subset=["d_atr_5", "CLOSE"])

    log(f"  {len(tech):,} 行 / {tech['TICKER'].nunique()} 銘柄")
    return tech


# ==========================================
# インデックスデータ取得（TOPIX・日経225）
# ==========================================
def load_indices(date_from: str, date_to: str) -> dict:
    try:
        import yfinance as yf
    except ImportError:
        log("  yfinance 未インストール → スキップ")
        return {}

    log("インデックスデータ取得中（yfinance）...")
    result = {}
    targets = {"TOPIX": "1306.T", "日経225": "^N225"}  # 1306.T = NEXT FUNDS TOPIX ETF
    for name, ticker in targets.items():
        try:
            df = yf.download(ticker, start=date_from, end=date_to,
                             progress=False, auto_adjust=True)
            if df.empty:
                log(f"  {name}({ticker}): データなし")
                continue
            close = df["Close"].squeeze()
            close.index = pd.to_datetime(close.index).tz_localize(None)
            result[name] = close
            log(f"  {name}: {len(close)} 日分取得")
        except Exception as e:
            log(f"  {name}: 取得エラー {e}")
    return result


# ==========================================
# 日次PnLバックテスト（案C）
# ==========================================
def run_backtest(df: pd.DataFrame, top_pct: float) -> pd.DataFrame:
    """
    シグナル日S → S+1〜S+20 保有。
    日Dのポートフォリオ = 直近HOLD_DAYS日に選定された銘柄の等金額平均日次リターン。
    """
    test = df[df["YEARDATE"] >= TEST_START].copy()
    test = test.sort_values(["TICKER", "YEARDATE"])

    # 日次リターン
    test["daily_ret"] = test.groupby("TICKER")["CLOSE"].pct_change()

    log("  ピボット変換中...")
    daily_ret_piv = test.pivot(index="YEARDATE", columns="TICKER", values="daily_ret")
    signal_piv    = test.pivot(index="YEARDATE", columns="TICKER", values="d_atr_5")

    dates = sorted(signal_piv.index)
    threshold = 1 - top_pct / 100

    # 各日の選定銘柄を記録（シグナル日）
    log("  シグナル計算中...")
    selected_by_date: dict[pd.Timestamp, set] = {}
    for dt in dates:
        row = signal_piv.loc[dt].dropna()
        if len(row) < 10:
            continue
        cutoff = row.quantile(threshold)
        selected_by_date[dt] = set(row[row >= cutoff].index.tolist())

    # 日次PnL
    log("  日次PnL計算中...")
    port_daily = []
    for i, dt in enumerate(dates):
        # 保有銘柄: シグナル日が D-HOLD_DAYS 〜 D-1 の全銘柄
        held: set = set()
        for lag in range(1, HOLD_DAYS + 1):
            si = i - lag
            if si < 0:
                continue
            s_date = dates[si]
            if s_date in selected_by_date:
                held |= selected_by_date[s_date]

        if not held or dt not in daily_ret_piv.index:
            continue

        rets = daily_ret_piv.loc[dt, list(held)].dropna()
        # 极端な外れ値（上下ストップ高/安相当）を除去
        rets = rets[(rets > -0.30) & (rets < 0.30)]
        if len(rets) == 0:
            continue

        port_daily.append({
            "date":     dt,
            "ret":      rets.mean(),
            "n_stocks": len(rets),
        })

    result = pd.DataFrame(port_daily).set_index("date")
    return result


# ==========================================
# ベンチマーク（全銘柄均等・日次）
# ==========================================
def calc_eq_benchmark(df: pd.DataFrame) -> pd.Series:
    test = df[df["YEARDATE"] >= TEST_START].copy()
    test = test.sort_values(["TICKER", "YEARDATE"])
    test["daily_ret"] = test.groupby("TICKER")["CLOSE"].pct_change()
    test = test[(test["daily_ret"] > -0.30) & (test["daily_ret"] < 0.30)]
    bm = test.groupby("YEARDATE")["daily_ret"].mean()
    return (1 + bm).cumprod()


# ==========================================
# パフォーマンス評価
# ==========================================
def evaluate(result: pd.DataFrame, top_pct: float) -> tuple[dict, pd.Series]:
    r   = result["ret"].copy()
    cum = (1 + r).cumprod()
    n_days = len(r)
    years  = n_days / 252

    total_ret  = cum.iloc[-1] - 1
    annual_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0
    sharpe     = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
    win_rate   = (r > 0).mean()
    max_dd     = (cum / cum.cummax() - 1).min()

    metrics = {
        "top_pct":     top_pct,
        "期間":         f"{result.index[0].date()} 〜 {result.index[-1].date()}",
        "銘柄数(平均)": f"{result['n_stocks'].mean():.0f}",
        "総リターン":   f"{total_ret:.1%}",
        "年率リターン": f"{annual_ret:.1%}",
        "Sharpe":      f"{sharpe:.2f}",
        "MaxDD":       f"{max_dd:.1%}",
        "勝率(日次)":  f"{win_rate:.1%}",
        "平均日次ret":  f"{r.mean():.4%}",
    }
    log("\n=== パフォーマンス ===")
    for k, v in metrics.items():
        log(f"  {k}: {v}")
    return metrics, cum


# ==========================================
# 可視化
# ==========================================
def visualize(result: pd.DataFrame, cum: pd.Series,
              eq_bm: pd.Series, indices: dict,
              metrics: dict, top_pct: float):

    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    fig.suptitle(
        f"d_atr_5 上位{top_pct:.0f}% ロング バックテスト（日次PnLモデル）\n"
        f"Sharpe={metrics['Sharpe']}  年率={metrics['年率リターン']}  MaxDD={metrics['MaxDD']}",
        fontsize=13
    )

    # 1. 累積リターン比較
    ax = axes[0, 0]
    ax.plot(cum.index, (cum - 1) * 100,
            label=f"d_atr_5上位{top_pct:.0f}%", color="#2ca02c", lw=2)

    eq_al = eq_bm.reindex(cum.index, method="ffill")
    ax.plot(eq_al.index, (eq_al - 1) * 100,
            label="全銘柄均等", color="gray", lw=1, linestyle="--")

    idx_colors = {"TOPIX": "#1f77b4", "日経225": "#ff7f0e"}
    for name, s in indices.items():
        s_norm = s / s.iloc[0]
        s_al   = s_norm.reindex(cum.index, method="ffill")
        ax.plot(s_al.index, (s_al - 1) * 100,
                label=name, color=idx_colors.get(name, "purple"),
                lw=1.2, linestyle=":")

    ax.set_title("累積リターン (%)")
    ax.set_ylabel("%"); ax.legend(); ax.axhline(0, color="black", lw=0.5)
    ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter('%Y-%m'))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30)

    # 2. ドローダウン
    ax = axes[0, 1]
    dd = (cum / cum.cummax() - 1) * 100
    ax.fill_between(dd.index, dd.values, 0, alpha=0.4, color="#d62728")
    ax.plot(dd.index, dd.values, color="#d62728", lw=0.8)
    ax.set_title(f"ドローダウン (MaxDD={metrics['MaxDD']})")
    ax.set_ylabel("%"); ax.axhline(0, color="black", lw=0.5)
    ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter('%Y-%m'))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30)

    # 3. 月別リターン
    ax = axes[1, 0]
    monthly = result["ret"].copy()
    monthly.index = pd.to_datetime(monthly.index)
    monthly = monthly.resample("ME").apply(lambda x: (1 + x).prod() - 1) * 100
    colors = ["#2ca02c" if v >= 0 else "#d62728" for v in monthly.values]
    ax.bar(monthly.index, monthly.values, color=colors, width=20)
    ax.set_title("月別リターン (%)")
    ax.set_ylabel("%"); ax.axhline(0, color="black", lw=0.5)
    ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter('%Y-%m'))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

    # 4. 日次リターン分布
    ax = axes[1, 1]
    r = result["ret"] * 100
    ax.hist(r, bins=60, color="#1f77b4", alpha=0.7, edgecolor="white")
    ax.axvline(r.mean(), color="red", lw=1.5, linestyle="--",
               label=f"平均 {r.mean():.3f}%")
    ax.axvline(0, color="black", lw=0.8)
    ax.set_title(f"日次リターン分布  勝率={metrics['勝率(日次)']}")
    ax.set_xlabel("%"); ax.set_ylabel("頻度"); ax.legend()

    fig.tight_layout()
    outpath = os.path.join(OUT_DIR, f"backtest_datr_long_top{int(top_pct)}.png")
    fig.savefig(outpath, dpi=150, bbox_inches="tight")
    log(f"\nグラフ保存: {outpath}")
    subprocess.Popen(["explorer", outpath])


# ==========================================
# メイン
# ==========================================
if __name__ == "__main__":
    args    = parse_args()
    top_pct = args.top
    log(f"=== d_atr_5 上位{top_pct:.0f}% ロングのみ バックテスト（日次PnLモデル） ===")

    if not os.path.exists(CACHE_TECH):
        log(f"キャッシュが見つかりません: {CACHE_TECH}")
        log("先に analyze_uki_predictor.py を実行してください。")
        sys.exit(1)

    df = load_data()

    log(f"\nバックテスト実行中 (上位{top_pct:.0f}%)...")
    result = run_backtest(df, top_pct)
    log(f"  計算対象日数: {len(result)} 日")

    metrics, cum = evaluate(result, top_pct)

    log("\n全銘柄均等ベンチマーク計算中...")
    eq_bm = calc_eq_benchmark(df)

    date_to = result.index[-1].strftime("%Y-%m-%d")
    indices = load_indices(TEST_START, date_to)

    visualize(result, cum, eq_bm, indices, metrics, top_pct)

    out_csv = os.path.join(OUT_DIR, f"backtest_datr_long_top{int(top_pct)}.csv")
    result.to_csv(out_csv, encoding="utf-8")
    log(f"結果CSV保存: {out_csv}")

    log("=== 完了 ===")

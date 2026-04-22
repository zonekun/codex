"""
d_atr（日中変動）深掘り分析

UKI予測モデル v2 で最重要特徴量だった d_atr_20 を多角的に検証する。

分析内容:
    1. d_atr 単変量分位別CAR（5分位 × label_high_20/low_20）
    2. ウィンドウ最適化（day_range / d_atr_5 / d_atr_20 の Spearman 相関比較）
    3. d_atr × m_sales 交差分析（2×3マトリックス）
    4. d_atr の自己相関（持続性・シグナル安定性）

前提: analyze_uki_predictor.py で生成した以下のキャッシュが存在すること
    data/csv/uki_predictor/tech_features_v2.parquet
    data/csv/uki_predictor/fin_features_v2.parquet

Usage:
    PYTHONUTF8=1 python scripts/analyze_datr_deep.py
"""

import os, sys, warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import subprocess
from datetime import datetime
from scipy import stats

warnings.filterwarnings("ignore")

plt.rcParams['font.family'] = 'Yu Gothic'

OUT_DIR    = os.path.join(os.path.dirname(__file__), '..', 'data', 'csv', 'uki_predictor')
CACHE_TECH = os.path.join(OUT_DIR, "tech_features_v2.parquet")
CACHE_FIN  = os.path.join(OUT_DIR, "fin_features_v2.parquet")
TEST_START = "2023-06-01"
TRAIN_END  = "2023-03-31"

def log(msg): print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ==========================================
# データ準備
# ==========================================
def load_data() -> pd.DataFrame:
    log("キャッシュ読み込み中...")
    import pyarrow.parquet as pq
    import pyarrow as pa
    # BQ由来の dbdate 型に対応: メタデータを除去してから変換
    def read_no_meta(path):
        tbl = pq.read_table(path)
        # pandas metadata（dbdate等のBQ拡張型情報）を除去
        tbl = tbl.replace_schema_metadata({})
        df = tbl.to_pandas()
        return df
    tech = read_no_meta(CACHE_TECH)
    fin  = read_no_meta(CACHE_FIN)
    tech["YEARDATE"]     = pd.to_datetime(tech["YEARDATE"])
    fin["DISCLOSED_DATE"] = pd.to_datetime(fin["DISCLOSED_DATE"])

    # as-of join（analyze_uki_predictor と同じ）
    fin_cols = ["TICKER","DISCLOSED_DATE","m_sales","m_ope_income","m_net_income",
                "op_margin","roe","roa","equity_ratio"]
    fin = fin[[c for c in fin_cols if c in fin.columns]].dropna(subset=["TICKER"])

    parts = []
    for ticker, gp in tech.groupby("TICKER"):
        fd = fin[fin["TICKER"] == ticker].sort_values("DISCLOSED_DATE")
        if fd.empty:
            parts.append(gp)
            continue
        gp = gp.sort_values("YEARDATE")
        gp = pd.merge_asof(gp, fd.drop(columns="TICKER"),
                           left_on="YEARDATE", right_on="DISCLOSED_DATE",
                           direction="backward")
        parts.append(gp)

    df = pd.concat(parts, ignore_index=True)
    df["YEARDATE"] = pd.to_datetime(df["YEARDATE"])
    log(f"  合計 {len(df):,} 行  /  ユニーク銘柄: {df['TICKER'].nunique()}")
    return df


# ==========================================
# 1. d_atr 単変量分位別CAR
# ==========================================
def analysis_quintile_car(test: pd.DataFrame, ax_hi, ax_lo):
    log("【1】d_atr 分位別CAR...")
    result = []
    for feat in ["day_range", "d_atr_5", "d_atr_20"]:
        if feat not in test.columns:
            continue
        tmp = test[[feat, "label_high_20", "label_low_20"]].dropna()
        tmp["q"] = pd.qcut(tmp[feat], 5, labels=False, duplicates="drop")
        hi = tmp.groupby("q")["label_high_20"].mean() * 100
        lo = tmp.groupby("q")["label_low_20"].mean() * 100
        sr_hi, _ = stats.spearmanr(tmp[feat], tmp["label_high_20"])
        sr_lo, _ = stats.spearmanr(tmp[feat], tmp["label_low_20"])
        result.append({"feat": feat, "spearman_high": sr_hi, "spearman_low": sr_lo})
        log(f"  {feat}: Spearman high={sr_hi:.3f}  low={sr_lo:.3f}")

    # 最重要 d_atr_20 を可視化
    feat = "d_atr_20" if "d_atr_20" in test.columns else "d_atr_5"
    tmp = test[[feat, "label_high_20", "label_low_20"]].dropna()
    tmp["q"] = pd.qcut(tmp[feat], 5, labels=["Q1(低)","Q2","Q3","Q4","Q5(高)"], duplicates="drop")
    hi = tmp.groupby("q")["label_high_20"].mean() * 100
    lo = tmp.groupby("q")["label_low_20"].mean() * 100

    colors_hi = ["#d62728" if v < hi.mean() else "#2ca02c" for v in hi]
    colors_lo = ["#2ca02c" if v > lo.mean() else "#d62728" for v in lo]
    ax_hi.bar(hi.index, hi.values, color=colors_hi)
    ax_hi.axhline(hi.mean(), color="gray", linestyle="--", lw=0.8, label=f"平均 {hi.mean():.1f}%")
    ax_hi.set_title(f"d_atr_20 分位別 label_high_20 (Spearman={result[-1]['spearman_high']:.3f})")
    ax_hi.set_ylabel("平均実績 (%)")
    ax_hi.legend(); ax_hi.tick_params(axis="x", rotation=20)

    ax_lo.bar(lo.index, lo.values, color=colors_lo)
    ax_lo.axhline(lo.mean(), color="gray", linestyle="--", lw=0.8, label=f"平均 {lo.mean():.1f}%")
    ax_lo.set_title(f"d_atr_20 分位別 label_low_20 (Spearman={result[-1]['spearman_low']:.3f})")
    ax_lo.set_ylabel("平均実績 (%)")
    ax_lo.legend(); ax_lo.tick_params(axis="x", rotation=20)

    return pd.DataFrame(result)


# ==========================================
# 2. ウィンドウ最適化（Spearman 比較）
# ==========================================
def analysis_window_comparison(test: pd.DataFrame, ax):
    log("【2】ウィンドウ最適化...")
    feats = ["day_range","d_atr_5","d_atr_20","gap_range","g_atr_5","g_atr_20",
             "hig_range","h_atr_5","h_atr_20","vola_20","vola_60","atr_20"]
    feats = [f for f in feats if f in test.columns]

    rows = []
    for f in feats:
        tmp = test[[f, "label_high_20", "label_low_20"]].dropna()
        if len(tmp) < 100:
            continue
        sr_hi, _ = stats.spearmanr(tmp[f], tmp["label_high_20"])
        sr_lo, _ = stats.spearmanr(tmp[f], tmp["label_low_20"])
        rows.append({"feature": f, "spearman_high": sr_hi, "spearman_low": abs(sr_lo)})

    df = pd.DataFrame(rows).sort_values("spearman_high", ascending=False)
    log(f"\n{'特徴量':<15} {'高値Spearman':>13} {'安値|Spearman|':>14}")
    for _, r in df.iterrows():
        log(f"  {r['feature']:<15} {r['spearman_high']:>12.3f} {r['spearman_low']:>13.3f}")

    x = np.arange(len(df))
    w = 0.35
    ax.bar(x - w/2, df["spearman_high"].values, w, label="label_high_20", color="#2ca02c")
    ax.bar(x + w/2, df["spearman_low"].values,  w, label="|label_low_20|", color="#d62728", alpha=0.7)
    ax.set_xticks(x)
    ax.set_xticklabels(df["feature"].values, rotation=45, ha="right", fontsize=8)
    ax.set_title("特徴量別 Spearman 順位相関（テスト期間）")
    ax.set_ylabel("Spearman R")
    ax.axhline(0, color="black", lw=0.5)
    ax.legend()
    return df


# ==========================================
# 3. d_atr × m_sales 交差分析（2×3マトリックス）
# ==========================================
def analysis_cross_datr_msales(test: pd.DataFrame, ax):
    log("【3】d_atr × m_sales 交差分析...")
    needed = ["d_atr_20", "m_sales", "label_high_20"]
    if not all(c in test.columns for c in needed):
        log("  必要カラム不足でスキップ")
        return None

    tmp = test[needed].dropna()
    tmp = tmp[tmp["m_sales"].between(-5, 5)]  # 極端な外れ値除去

    # d_atr_20: 3分位（低・中・高）
    tmp["datr_q"] = pd.qcut(tmp["d_atr_20"], 3, labels=["低","中","高"])
    # m_sales: 正（好決算）/ 負（悪決算）
    tmp["msales_sign"] = np.where(tmp["m_sales"] > 0, "好決算(m_sales>0)", "悪決算(m_sales<0)")

    matrix = tmp.groupby(["datr_q", "msales_sign"])["label_high_20"].agg(["mean","count"])
    matrix["mean"] *= 100
    log("\n  d_atr × m_sales 交差リターン (label_high_20 平均%):")
    log(matrix.to_string())

    # ヒートマップ用ピボット
    pivot = matrix["mean"].unstack("msales_sign")
    im = ax.imshow(pivot.values, cmap="RdYlGn", aspect="auto",
                   vmin=pivot.values.min(), vmax=pivot.values.max())
    ax.set_xticks(range(len(pivot.columns))); ax.set_xticklabels(pivot.columns, fontsize=9)
    ax.set_yticks(range(len(pivot.index)));   ax.set_yticklabels(pivot.index, fontsize=9)
    ax.set_title("d_atr_20 × m_sales 交差\n(セルの値: label_high_20 平均%)")
    ax.set_xlabel("決算サプライズ"); ax.set_ylabel("d_atr_20 水準")
    plt.colorbar(im, ax=ax, label="%")

    # セルに数値を書き込む
    cnt_pivot = matrix["count"].unstack("msales_sign")
    for i in range(len(pivot.index)):
        for j in range(len(pivot.columns)):
            val  = pivot.values[i, j]
            cnt  = cnt_pivot.values[i, j]
            color = "white" if abs(val) > pivot.values.std() * 1.5 else "black"
            ax.text(j, i, f"{val:.1f}%\n(n={int(cnt):,})",
                    ha="center", va="center", fontsize=9, color=color)
    return matrix


# ==========================================
# 4. d_atr 持続性分析（自己相関）
# ==========================================
def analysis_persistence(df: pd.DataFrame, ax):
    log("【4】d_atr 持続性分析...")
    feat = "d_atr_20"
    if feat not in df.columns:
        log("  d_atr_20 なし、スキップ")
        return

    df = df.sort_values(["TICKER", "YEARDATE"]).copy()
    lags = range(1, 21)
    corrs = []
    for lag in lags:
        df["lag"] = df.groupby("TICKER")[feat].shift(lag)
        tmp = df[[feat, "lag"]].dropna()
        r, _ = stats.spearmanr(tmp[feat], tmp["lag"])
        corrs.append(r)

    ax.bar(list(lags), corrs, color="#1f77b4")
    ax.axhline(0, color="black", lw=0.5)
    ax.set_xlabel("ラグ（営業日）")
    ax.set_ylabel("Spearman 自己相関")
    ax.set_title(f"d_atr_20 自己相関（持続性）\nラグ1日目: {corrs[0]:.3f}")
    ax.set_xticks(list(lags))
    log(f"  1日後: {corrs[0]:.3f}  5日後: {corrs[4]:.3f}  10日後: {corrs[9]:.3f}  20日後: {corrs[19]:.3f}")


# ==========================================
# メイン
# ==========================================
if __name__ == "__main__":
    log("=== d_atr 深掘り分析 ===")

    if not os.path.exists(CACHE_TECH):
        log(f"キャッシュが見つかりません: {CACHE_TECH}")
        log("先に analyze_uki_predictor.py を実行してください。")
        sys.exit(1)

    df = load_data()

    # テスト期間のみ
    test = df[df["YEARDATE"] >= TEST_START].copy()
    # 外れ値除去
    for col in ["label_high_20", "label_low_20"]:
        lo, hi = test[col].quantile(0.005), test[col].quantile(0.995)
        test = test[(test[col] >= lo) & (test[col] <= hi)]
    log(f"テスト期間: {len(test):,} 行")

    # --- グラフ描画 ---
    fig = plt.figure(figsize=(18, 14))
    fig.suptitle("d_atr（日中変動）深掘り分析", fontsize=15, y=1.01)

    ax_hi   = fig.add_subplot(3, 3, 1)
    ax_lo   = fig.add_subplot(3, 3, 2)
    ax_win  = fig.add_subplot(3, 3, (3, 6))  # ウィンドウ比較（縦2段分）
    ax_cros = fig.add_subplot(3, 3, 7)
    ax_pers = fig.add_subplot(3, 3, (8, 9))

    # 実行
    quintile_df  = analysis_quintile_car(test, ax_hi, ax_lo)
    window_df    = analysis_window_comparison(test, ax_win)
    cross_matrix = analysis_cross_datr_msales(test, ax_cros)
    analysis_persistence(df, ax_pers)

    fig.tight_layout()
    outpath = os.path.join(OUT_DIR, "datr_deep_analysis.png")
    fig.savefig(outpath, dpi=150, bbox_inches="tight")
    log(f"\nグラフ保存: {outpath}")
    subprocess.Popen(["explorer", outpath])

    # サマリー出力
    log("\n=== サマリー ===")
    if quintile_df is not None:
        log("d_atr Spearman:")
        for _, r in quintile_df.iterrows():
            log(f"  {r['feat']}: high={r['spearman_high']:.3f}  low={r['spearman_low']:.3f}")
    if window_df is not None:
        log("\nTop5特徴量 (label_high_20):")
        for _, r in window_df.head(5).iterrows():
            log(f"  {r['feature']}: {r['spearman_high']:.3f}")

    log("=== 完了 ===")

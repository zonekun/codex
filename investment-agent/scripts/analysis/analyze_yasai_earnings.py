# -*- coding: utf-8 -*-
"""野菜価格 × 四半期業績（営業利益率）相関分析.

仮説: 農水省週次野菜価格が、外食チェーン等の四半期営業利益率に先行指標として機能するか。
アプローチ:
  - 各四半期区間の MAFF 野菜価格平均変化率（前週比累積）を計算
  - 各四半期の単独（累積から差分した）営業利益率を計算
  - Spearman 相関を検定
"""

import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

# --- 設定 ---
CACHE_DIR  = Path(__file__).parent.parent / "data" / "cache"
EXCEL_PATH = CACHE_DIR / "yasai_price_maff.xlsx"
JQUANTS_API_KEY = "B0k4DUrka1yBSQixGCXo2OA8UCseDWyBFU-OmSod-QE"

TARGETS = {
    "ゼンショーHD":       "75500",
    "すかいらーくHD":     "31970",
    "吉野家HD":           "98610",
    "くら寿司":           "26950",
    "日本マクドナルドHD":  "27020",
    "味の素":             "28020",
}

GENGO = {"令和": 2018, "平成": 1988, "昭和": 1925}


# ============================================================
# MAFF 野菜価格データ読み込み
# ============================================================
def parse_jp_date(s: str):
    m = re.match(r"(令和|平成|昭和)(\d+)年(\d+)月(\d+)日", str(s).strip())
    if not m:
        return None
    g, y, mo, d = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return pd.Timestamp(GENGO[g] + y, mo, d)


def load_yasai_weekly(path: Path) -> pd.DataFrame:
    """前週比シートを読み込み、変化率（0基準）の週次データを返す."""
    raw = pd.read_excel(path, sheet_name="前週比", header=None)
    cols = ["DATE"] + [str(c) for c in raw.iloc[1, 1:]]
    rows = []
    for _, row in raw.iloc[2:].iterrows():
        dt = parse_jp_date(row.iloc[0])
        if dt is None:
            continue
        vals = {"DATE": dt}
        for i, col in enumerate(cols[1:], 1):
            try:
                v = float(row.iloc[i])
                vals[col] = v - 1.0  # 前週比→変化率
            except (ValueError, TypeError):
                vals[col] = np.nan
        rows.append(vals)
    df = pd.DataFrame(rows).set_index("DATE").sort_index()
    df["YASAI_AVG"] = df.mean(axis=1)
    return df


def yasai_quarterly_avg(yasai: pd.DataFrame, period_start, period_end) -> float:
    """指定期間内の週次野菜平均変化率を平均して返す（データなければ NaN）."""
    mask = (yasai.index >= period_start) & (yasai.index <= period_end)
    vals = yasai.loc[mask, "YASAI_AVG"].dropna()
    if len(vals) < 4:  # 4週未満は信頼性低い
        return np.nan
    return vals.mean()


# ============================================================
# J-Quants 四半期財務データ取得
# ============================================================
def fetch_fin_data(api_key: str, targets: dict) -> pd.DataFrame:
    """J-Quants から各社の四半期財務サマリを取得."""
    import jquantsapi
    cli = jquantsapi.ClientV2(api_key=api_key)

    dfs = []
    for name, code in targets.items():
        df = cli.get_fin_summary(code=code)
        df["Company"] = name
        dfs.append(df)

    all_df = pd.concat(dfs, ignore_index=True)

    # 連結・1Q/2Q/3Q/FY のみ
    mask = (
        all_df["CurPerType"].isin(["1Q", "2Q", "3Q", "FY"])
        & all_df["DocType"].str.contains("Consolidated", na=False)
    )
    fin = all_df[mask].copy()

    # 数値変換
    for col in ["Sales", "OP"]:
        fin[col] = pd.to_numeric(fin[col], errors="coerce")

    fin = fin.dropna(subset=["Sales", "OP"])
    fin = fin[fin["Sales"] > 0]

    cols = ["Company", "CurPerType", "CurPerSt", "CurPerEn", "Sales", "OP"]
    fin = fin[cols].copy()
    fin["CurPerSt"] = pd.to_datetime(fin["CurPerSt"])
    fin["CurPerEn"] = pd.to_datetime(fin["CurPerEn"])

    # 重複除去（同一会社×期間の重複開示は最新のみ使用）
    fin = fin.sort_values(["Company", "CurPerSt", "CurPerType"])
    fin = fin.drop_duplicates(subset=["Company", "CurPerSt", "CurPerType"], keep="last")

    return fin


# ============================================================
# 四半期単独（差分）営業利益率を計算
# ============================================================
def calc_standalone_margin(fin: pd.DataFrame) -> pd.DataFrame:
    """累積値から四半期単独の Sales・OP を導出し、単独 OP_margin を計算.

    期間種別の順序: 1Q < 2Q < 3Q < FY
    差分ルール:
      Q1_standalone = 1Q
      Q2_standalone = 2Q - 1Q
      Q3_standalone = 3Q - 2Q
      Q4_standalone = FY - 3Q
    """
    order = {"1Q": 1, "2Q": 2, "3Q": 3, "FY": 4}
    fin = fin.copy()
    fin["period_order"] = fin["CurPerType"].map(order)
    fin = fin.sort_values(["Company", "CurPerSt", "period_order"])

    rows = []
    for (company, fy_start), grp in fin.groupby(["Company", "CurPerSt"]):
        grp = grp.sort_values("period_order")
        prev_sales = 0.0
        prev_op    = 0.0
        for _, row in grp.iterrows():
            sa_sales = row["Sales"] - prev_sales
            sa_op    = row["OP"]    - prev_op
            if sa_sales <= 0:
                prev_sales = row["Sales"]
                prev_op    = row["OP"]
                continue
            # 当四半期の期間を正確に計算
            if row["CurPerType"] == "1Q":
                q_start = row["CurPerSt"]
            else:
                # 前回の CurPerEn + 1日 が四半期開始
                # ただしCurPerStはFY開始なので直接使えない
                # → grp の前行 CurPerEn を使う
                prev_rows = grp[grp["period_order"] < row["period_order"]]
                if prev_rows.empty:
                    q_start = row["CurPerSt"]
                else:
                    q_start = prev_rows.iloc[-1]["CurPerEn"] + pd.Timedelta(days=1)
            q_end = row["CurPerEn"]

            rows.append({
                "Company":     company,
                "FY_start":    fy_start,
                "CurPerType":  row["CurPerType"],
                "Q_start":     q_start,
                "Q_end":       q_end,
                "SA_Sales":    sa_sales,
                "SA_OP":       sa_op,
                "SA_OP_margin": sa_op / sa_sales,
            })
            prev_sales = row["Sales"]
            prev_op    = row["OP"]

    return pd.DataFrame(rows)


# ============================================================
# アライン: 野菜価格 × 四半期業績
# ============================================================
def align_yasai_fin(sa: pd.DataFrame, yasai: pd.DataFrame) -> pd.DataFrame:
    """各四半期に対応する期間の野菜価格平均変化率をマッピング."""
    sa = sa.copy()
    sa["yasai_avg_chg"] = sa.apply(
        lambda r: yasai_quarterly_avg(yasai, r["Q_start"], r["Q_end"]),
        axis=1,
    )
    return sa.dropna(subset=["yasai_avg_chg", "SA_OP_margin"])


# ============================================================
# 相関分析
# ============================================================
def correlation_analysis(aligned: pd.DataFrame) -> pd.DataFrame:
    results = []

    # 全社プール
    x = aligned["yasai_avg_chg"]
    y = aligned["SA_OP_margin"]
    mask = x.notna() & y.notna()
    if mask.sum() >= 10:
        corr, pval = stats.spearmanr(x[mask], y[mask])
        results.append({
            "分析対象": "全社プール",
            "n": int(mask.sum()),
            "Spearman相関": round(corr, 3),
            "p値": round(pval, 4),
            "有意": "★" if pval < 0.05 else ("△" if pval < 0.10 else ""),
        })

    # 会社別
    for company, grp in aligned.groupby("Company"):
        x = grp["yasai_avg_chg"]
        y = grp["SA_OP_margin"]
        mask = x.notna() & y.notna()
        if mask.sum() < 8:
            continue
        corr, pval = stats.spearmanr(x[mask], y[mask])
        results.append({
            "分析対象": company,
            "n": int(mask.sum()),
            "Spearman相関": round(corr, 3),
            "p値": round(pval, 4),
            "有意": "★" if pval < 0.05 else ("△" if pval < 0.10 else ""),
        })

    return pd.DataFrame(results).sort_values("p値")


# ============================================================
# イベント分析: 野菜価格急騰四半期のOP_margin
# ============================================================
def event_analysis(aligned: pd.DataFrame, threshold: float = 0.03) -> pd.DataFrame:
    """野菜価格が平均より高かった四半期 vs 低かった四半期のOP_margin比較."""
    spike  = aligned["yasai_avg_chg"] >= threshold
    normal = aligned["yasai_avg_chg"].abs() < 0.01

    results = []
    for company, grp in aligned.groupby("Company"):
        sp = grp.loc[spike & (aligned["Company"] == company), "SA_OP_margin"]
        nm = grp.loc[normal & (aligned["Company"] == company), "SA_OP_margin"]
        if len(sp) < 3 or len(nm) < 3:
            continue
        _, pval = stats.mannwhitneyu(sp, nm, alternative="less")
        results.append({
            "会社": company,
            "急騰四半期N": len(sp),
            "急騰時平均OP_margin(%)": round(sp.mean() * 100, 2),
            "通常時平均OP_margin(%)": round(nm.mean() * 100, 2),
            "差(pp)": round((sp.mean() - nm.mean()) * 100, 2),
            "p値": round(pval, 4),
            "有意": "★" if pval < 0.05 else ("△" if pval < 0.10 else ""),
        })

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results).sort_values("差(pp)")


# ============================================================
# メイン
# ============================================================
def main():
    print("=" * 65)
    print("野菜価格 × 四半期業績（営業利益率）相関分析")
    print("=" * 65)

    # 1. MAFF 野菜価格
    print("\n1. 野菜価格データ読み込み中...")
    yasai = load_yasai_weekly(EXCEL_PATH)
    print(f"   {yasai.index.min().date()} ～ {yasai.index.max().date()}, {len(yasai)}週")

    # 2. J-Quants 四半期財務データ
    print("\n2. J-Quants 四半期財務データ取得中...")
    fin = fetch_fin_data(JQUANTS_API_KEY, TARGETS)
    print(f"   {len(fin)}件取得 ({fin['Company'].nunique()}社)")

    # 3. 四半期単独 OP_margin 計算
    print("\n3. 四半期単独 OP_margin 計算中...")
    sa = calc_standalone_margin(fin)
    print(f"   四半期数: {len(sa)}")
    print("\n   サンプル（ゼンショーHD）:")
    sample = sa[sa["Company"] == "ゼンショーHD"].head(8)
    print(sample[["CurPerType", "Q_start", "Q_end", "SA_Sales", "SA_OP", "SA_OP_margin"]].to_string(index=False))

    # 4. アライン
    print("\n4. 野菜価格と四半期業績をアライン中...")
    aligned = align_yasai_fin(sa, yasai)
    print(f"   有効四半期数: {len(aligned)}")

    # データ期間の確認（野菜データがある範囲）
    yasai_start = yasai.index.min()
    yasai_end   = yasai.index.max()
    aligned = aligned[
        (aligned["Q_start"] >= yasai_start) & (aligned["Q_end"] <= yasai_end)
    ]
    print(f"   野菜データ期間内: {len(aligned)}")

    # 5. 相関分析
    print("\n" + "=" * 65)
    print("【相関分析】野菜価格平均変化率 × 四半期単独 OP_margin (Spearman)")
    print("有意(p<0.05)★ / 参考(p<0.10)△")
    print("=" * 65)
    corr_df = correlation_analysis(aligned)
    print(corr_df.to_string(index=False))

    # 6. イベント分析
    print("\n" + "=" * 65)
    print("【イベント分析】野菜価格平均変化率+3%超の四半期のOP_margin比較")
    print("=" * 65)
    event_df = event_analysis(aligned, threshold=0.03)
    if event_df.empty:
        print("有意なサンプルなし（急騰四半期 or 通常四半期が少ない）")
    else:
        print(event_df.to_string(index=False))

    # 7. 詳細データ表示
    print("\n" + "=" * 65)
    print("【データ詳細】アライン済み四半期データ")
    print("=" * 65)
    display_cols = ["Company", "CurPerType", "Q_start", "Q_end", "SA_OP_margin", "yasai_avg_chg"]
    print(aligned[display_cols].sort_values(["Company", "Q_start"]).to_string(index=False))

    # 8. サマリー
    n_sig = len(corr_df[corr_df["有意"] == "★"])
    print("\n" + "=" * 65)
    print("【サマリー】")
    sig = corr_df[corr_df["有意"].isin(["★", "△"])]
    for _, row in sig.iterrows():
        sign = "負" if row["Spearman相関"] < 0 else "正"
        print(f"  {row['分析対象']}: 相関={row['Spearman相関']} ({sign}), p={row['p値']} {row['有意']}")
    print(f"\n有意(p<0.05): {n_sig}件")
    if n_sig >= 2:
        print("→ ANALYZED_PASS候補: バックテストに進む価値あり")
    elif len(sig) > 0:
        print("→ 限定的な結果。追加検証を推奨")
    else:
        print("→ ANALYZED_FAIL: 有意な結果なし")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""野菜価格 × 株価 相関・イベント分析 (ANALYZING フェーズ).

仮説: 野菜価格が業績に影響する銘柄において、農水省週次価格が先行指標になり得る。
"""

import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats

warnings.filterwarnings("ignore")

CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
EXCEL_PATH = CACHE_DIR / "yasai_price_maff.xlsx"

# 野菜価格が業績に影響しやすい銘柄（外食・食品スーパー・食品メーカー）
TARGETS = {
    "ゼンショーHD":     "7550.T",
    "イオン":           "8267.T",
    "セブン&アイHD":    "3382.T",
    "日本マクドナルドHD": "2702.T",
    "吉野家HD":         "9861.T",
    "すかいらーくHD":   "3197.T",
    "くら寿司":         "2695.T",
    "味の素":           "2802.T",
}

# 元号→西暦オフセット
GENGO = {"令和": 2018, "平成": 1988, "昭和": 1925}


def parse_jp_date(s: str) -> pd.Timestamp | None:
    """'令和3年4月5日の週' → Timestamp."""
    s = str(s).strip()
    m = re.match(r"(令和|平成|昭和)(\d+)年(\d+)月(\d+)日", s)
    if not m:
        return None
    g, y, mo, d = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return pd.Timestamp(GENGO[g] + y, mo, d)


def load_price(path: Path) -> pd.DataFrame:
    """価格シートをDataFrameに変換."""
    raw = pd.read_excel(path, sheet_name="価格", header=None)
    cols = ["DATE"] + list(raw.iloc[1, 1:].fillna("unknown"))
    data = raw.iloc[2:].copy()
    data.columns = raw.columns
    rows = []
    for _, row in data.iterrows():
        dt = parse_jp_date(row.iloc[0])
        if dt is None:
            continue
        vals = {"DATE": dt}
        for i, col in enumerate(cols[1:], 1):
            v = row.iloc[i]
            try:
                vals[col] = float(v)
            except (ValueError, TypeError):
                vals[col] = np.nan
        rows.append(vals)
    df = pd.DataFrame(rows).set_index("DATE").sort_index()
    # 野菜価格指数（単純平均）を追加
    df["YASAI_AVG"] = df.mean(axis=1)
    return df


def load_stocks(tickers: dict, start: str, end: str) -> pd.DataFrame:
    """yfinanceで週次終値を取得."""
    print(f"株価取得: {list(tickers.values())} ({start} - {end})")
    raw = yf.download(
        list(tickers.values()), start=start, end=end,
        interval="1wk", auto_adjust=True, progress=False
    )
    close = raw["Close"].copy()
    close.columns = [k for k, v in tickers.items() if v in close.columns]
    close.index = pd.to_datetime(close.index).tz_localize(None)
    return close


def align_weekly(yasai: pd.DataFrame, stocks: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """農水省データ（月曜基準）と株価（週末基準）を週単位で合わせる."""
    # 農水省データの週を月曜→金曜に移動して株価と合わせる
    y = yasai.copy()
    y.index = y.index + pd.Timedelta(days=4)  # 月曜→金曜
    # 共通期間に絞る
    common_start = max(y.index.min(), stocks.index.min())
    common_end = min(y.index.max(), stocks.index.max())
    y = y[(y.index >= common_start) & (y.index <= common_end)]
    s = stocks[(stocks.index >= common_start) & (stocks.index <= common_end)]
    # 週次リターン
    s_ret = s.pct_change().dropna()
    y_chg = y.pct_change().dropna()
    # インデックスを週番号で合わせる
    y_chg["week"] = y_chg.index.isocalendar().week.astype(str) + "_" + y_chg.index.year.astype(str)
    s_ret["week"] = s_ret.index.isocalendar().week.astype(str) + "_" + s_ret.index.year.astype(str)
    merged_y = y_chg.set_index("week")
    merged_s = s_ret.set_index("week")
    common_weeks = merged_y.index.intersection(merged_s.index)
    return merged_y.loc[common_weeks], merged_s.loc[common_weeks]


def correlation_analysis(y_chg: pd.DataFrame, s_ret: pd.DataFrame) -> pd.DataFrame:
    """野菜価格変化率 × 株式リターン のスピアマン相関."""
    results = []
    for stock in s_ret.columns:
        for veg in ["YASAI_AVG", "キャベツ", "ねぎ", "レタス", "トマト", "きゅうり"]:
            if veg not in y_chg.columns:
                continue
            x = y_chg[veg].dropna()
            y_s = s_ret[stock].dropna()
            common = x.index.intersection(y_s.index)
            if len(common) < 30:
                continue
            corr, pval = stats.spearmanr(x[common], y_s[common])
            results.append({
                "銘柄": stock, "野菜": veg,
                "相関係数": round(corr, 3), "p値": round(pval, 4), "n": len(common),
                "有意(p<0.05)": "★" if pval < 0.05 else ""
            })
    return pd.DataFrame(results).sort_values("p値")


def event_analysis(y_chg: pd.DataFrame, s_ret: pd.DataFrame, threshold: float = 0.10) -> pd.DataFrame:
    """野菜価格が急騰(+10%以上)した週の翌週株価リターン."""
    spike_weeks = y_chg[y_chg["YASAI_AVG"] >= threshold].index
    normal_weeks = y_chg[y_chg["YASAI_AVG"].abs() < 0.03].index
    results = []
    for stock in s_ret.columns:
        spike_ret = s_ret.loc[s_ret.index.intersection(spike_weeks), stock].dropna()
        normal_ret = s_ret.loc[s_ret.index.intersection(normal_weeks), stock].dropna()
        if len(spike_ret) < 5:
            continue
        t_stat, pval = stats.mannwhitneyu(spike_ret, normal_ret, alternative="less")
        results.append({
            "銘柄": stock,
            "急騰週N": len(spike_ret),
            "急騰週平均リターン": round(spike_ret.mean() * 100, 2),
            "通常週平均リターン": round(normal_ret.mean() * 100, 2),
            "差(pp)": round((spike_ret.mean() - normal_ret.mean()) * 100, 2),
            "p値(急騰→下落?)": round(pval, 4),
            "有意(p<0.10)": "★" if pval < 0.10 else ""
        })
    df = pd.DataFrame(results)
    if df.empty:
        return df
    return df.sort_values("差(pp)")


def main():
    print("=" * 60)
    print("野菜価格 × 株価 相関・イベント分析")
    print("=" * 60)

    # 1. 野菜価格データ読み込み
    yasai = load_price(EXCEL_PATH)
    print(f"\n野菜価格データ: {yasai.index.min().date()} - {yasai.index.max().date()}, {len(yasai)}週")
    print(yasai.tail(3).to_string())

    # 2. 株価取得
    start = str(yasai.index.min().date())
    end   = str((yasai.index.max() + pd.Timedelta(days=7)).date())
    stocks = load_stocks(TARGETS, start, end)
    print(f"\n株価データ: {stocks.index.min().date()} - {stocks.index.max().date()}")
    print(stocks.tail(3).to_string())

    # 3. 週次アライン
    y_chg, s_ret = align_weekly(yasai, stocks)
    print(f"\n共通週数: {len(y_chg)}")

    # 4. 相関分析
    print("\n" + "=" * 60)
    print("【相関分析】野菜価格変化率 × 翌週株式リターン (Spearman)")
    print("=" * 60)
    corr_df = correlation_analysis(y_chg, s_ret)
    print(corr_df.to_string(index=False))

    # 5. イベント分析
    print("\n" + "=" * 60)
    print("【イベント分析】野菜価格急騰週(+10%以上)の株価リターン比較")
    print("=" * 60)
    event_df = event_analysis(y_chg, s_ret, threshold=0.03)
    print(event_df.to_string(index=False))

    # 6. サマリー
    sig_corr = corr_df[corr_df["有意(p<0.05)"] == "★"]
    sig_event = event_df[event_df["有意(p<0.10)"] == "★"]
    print("\n" + "=" * 60)
    print("【サマリー】")
    print(f"有意な相関ペア (p<0.05): {len(sig_corr)}件")
    if not sig_corr.empty:
        print(sig_corr[["銘柄", "野菜", "相関係数", "p値"]].to_string(index=False))
    print(f"\n有意なイベント効果 (p<0.10): {len(sig_event)}件")
    if not sig_event.empty:
        print(sig_event[["銘柄", "急騰週平均リターン", "通常週平均リターン", "差(pp)", "p値(急騰→下落?)"]].to_string(index=False))

    print("\n結論:")
    if len(sig_corr) == 0 and len(sig_event) == 0:
        print("→ 有意な結果なし。仮説は現データでは支持されない。ANALYZED_FAIL。")
    elif len(sig_corr) + len(sig_event) >= 3:
        print("→ 複数の有意結果あり。バックテストに進む価値あり。ANALYZED_PASS候補。")
    else:
        print("→ 限定的な有意結果。解釈に注意が必要。追加検証を推奨。")


if __name__ == "__main__":
    main()

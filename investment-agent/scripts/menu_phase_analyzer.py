"""株式市場4局面判定プログラム.

PSメニュー「株式市場4局面判定」
詳細: docs/knowledges/tools/023_powershell_menu.md

局面定義:
  金融相場   : 金融緩和 × 景気弱い → 株高
  業績相場   : 緩和継続 × 景気強い → 株高
  逆金融相場 : 金融引き締め × 景気強い → 株安
  逆業績相場 : 引き締め緩む × 景気弱い → 株安

出力:
  1. 過去3週の局面確率バーグラフ（推移）
  2. 現在のセクタートレンド（BUY/FLAT/SELL）
  3. 遷移解説テキスト
"""

import sys
import warnings
from datetime import datetime, timedelta, date

import subprocess
import os
import urllib.request
import json as _json

import numpy as np
import pandas as pd
import yfinance as yf
import matplotlib
matplotlib.use("Agg")  # tkinter 不要バックエンド
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec

plt.rcParams["font.family"] = "Yu Gothic"

FRED_API_KEY = "d65e47bf3adc90feda8f3fb5ad3c75e5"

warnings.filterwarnings("ignore")

# ─── 定数 ──────────────────────────────────────────────────────────────
PHASE_NAMES = ["金融相場", "業績相場", "逆金融相場", "逆業績相場"]
PHASE_COLORS = ["#4CAF50", "#2196F3", "#FF5722", "#9C27B0"]

# US セクターETF（vs SPY）
US_SECTOR_ETF = {
    "XLRE": ("不動産", "金融相場"),
    "XLK":  ("テクノロジー", "金融相場"),
    "XLU":  ("公益事業", "金融相場"),
    "XLB":  ("素材", "業績相場"),
    "XLI":  ("資本財", "業績相場"),
    "XLY":  ("一般消費財", "業績相場"),
    "XLF":  ("金融", "逆金融相場"),
    "XLE":  ("エネルギー", "逆金融相場"),
    "XLV":  ("ヘルスケア", "逆業績相場"),
    "XLP":  ("生活必需品", "逆業績相場"),
}

# 日本セクターETF（vs 1306.T = TOPIX ETF）
JP_SECTOR_ETF = {
    "1620.T": ("建設・不動産", "金融相場"),
    "1626.T": ("情報・通信", "金融相場"),
    "1621.T": ("素材・化学", "業績相場"),
    "1624.T": ("機械・精密", "業績相場"),
    "1623.T": ("自動車", "業績相場"),
    "1615.T": ("銀行", "逆金融相場"),
    "1628.T": ("金融(除く銀行)", "逆金融相場"),
    "1622.T": ("医薬品", "逆業績相場"),
    "1617.T": ("食品", "逆業績相場"),
    "1619.T": ("電力・ガス", "逆業績相場"),
}

LOOKBACK_SECTOR = 30   # セクタートレンド判定の日数
LOOKBACK_MACRO  = 63   # マクロ指標の参照期間（約3ヶ月）
WEEK_OFFSETS    = [21, 14, 7, 0]  # 3週前・2週前・1週前・直近（営業日換算）


# ─── データ取得 ──────────────────────────────────────────────────────
def _dl(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    """yfinance でClose価格を取得。失敗は NaN。"""
    try:
        raw = yf.download(tickers, start=start, end=end,
                          auto_adjust=True, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            return raw["Close"]
        return raw.rename(columns={"Close": tickers[0]}) if len(tickers) == 1 else raw
    except Exception:
        return pd.DataFrame()


def fetch_macro_data() -> pd.DataFrame:
    """マクロ指標を一括ダウンロード。"""
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=180)).isoformat()

    tickers = [
        "^TNX",   # 米10年金利
        "^IRX",   # 米3ヶ月T-Bill
        "^VIX",   # VIX
        "^GSPC",  # S&P500
        "1306.T", # TOPIX ETF
        "^N225",  # 日経225
    ]
    df = _dl(tickers, start, end)

    # FRED: 10Y-2Y スプレッド・FF金利・失業率
    for series_id, col_name in [
        ("T10Y2Y",   "FRED_T10Y2Y"),
        ("FEDFUNDS", "FRED_FEDFUNDS"),
        ("UNRATE",   "FRED_UNRATE"),
    ]:
        try:
            url = (
                f"https://api.stlouisfed.org/fred/series/observations"
                f"?series_id={series_id}&api_key={FRED_API_KEY}"
                f"&file_type=json&observation_start={start}&frequency=d"
            )
            with urllib.request.urlopen(url, timeout=10) as r:
                obs = _json.loads(r.read())["observations"]
            vals = {o["date"]: float(o["value"]) for o in obs if o["value"] != "."}
            s = pd.Series(vals)
            s.index = pd.to_datetime(s.index)
            s = s.resample("B").ffill()
            df[col_name] = s
        except Exception:
            pass

    return df


# ─── スコア計算 ────────────────────────────────────────────────────────
def _sigmoid(x: float) -> float:
    return 1 / (1 + np.exp(-x))


def _normalize(val: float, lo: float, hi: float) -> float:
    """[lo, hi] → [-1, +1] に正規化。"""
    mid = (lo + hi) / 2
    half = (hi - lo) / 2
    if half == 0:
        return 0.0
    return max(-1.0, min(1.0, (val - mid) / half))


def calc_monetary_score(df: pd.DataFrame, as_of: pd.Timestamp) -> float:
    """
    金融政策スコア: 正 = 緩和, 負 = 引き締め

    指標:
      - 米10Y-3M スプレッド水準  (正=緩和)
      - 米3Mレート3ヶ月変化      (低下=緩和)
      - VIX水準                  (低=リスクオン=緩和的)
    """
    scores = []

    # 直近時点までのデータをスライス
    sub = df[df.index <= as_of].tail(LOOKBACK_MACRO)

    # 1. FRED 10Y-2Y スプレッド（正=緩和, 負=逆イールド=引き締め）
    if "FRED_T10Y2Y" in sub.columns:
        sp = sub["FRED_T10Y2Y"].dropna()
        if len(sp) > 0:
            spread = sp.iloc[-1]
            scores.append(_normalize(spread, -1.5, 2.0))
    elif "^TNX" in sub.columns and "^IRX" in sub.columns:
        tnx = sub["^TNX"].dropna()
        irx = sub["^IRX"].dropna()
        if len(tnx) > 0 and len(irx) > 0:
            spread = tnx.iloc[-1] - irx.iloc[-1]
            scores.append(_normalize(spread, -2.0, 3.0))

    # 2. FF金利の3ヶ月トレンド（低下=緩和）
    if "FRED_FEDFUNDS" in sub.columns:
        ff = sub["FRED_FEDFUNDS"].dropna()
        if len(ff) >= 40:
            chg = ff.iloc[-1] - ff.iloc[-40]
            scores.append(_normalize(-chg, -2.0, 2.0))
    elif "^IRX" in sub.columns:
        irx = sub["^IRX"].dropna()
        if len(irx) >= 40:
            chg = irx.iloc[-1] - irx.iloc[-40]
            scores.append(_normalize(-chg, -2.0, 2.0))

    # 3. VIX（低い=市場安定=緩和的な環境）
    if "^VIX" in sub.columns:
        vix = sub["^VIX"].dropna()
        if len(vix) > 0:
            v = vix.iloc[-1]
            scores.append(_normalize(-v, -40, -12))  # VIX高い=引き締め的

    return float(np.mean(scores)) if scores else 0.0


def calc_economy_score(df: pd.DataFrame, as_of: pd.Timestamp) -> float:
    """
    景気/業績スコア: 正 = 強い, 負 = 弱い

    指標:
      - S&P500 3ヶ月リターン
      - TOPIX/日経 3ヶ月リターン
      - VIX水準（低=景気強い）
    """
    scores = []
    sub = df[df.index <= as_of].tail(LOOKBACK_MACRO)

    # 失業率トレンド（低下=景気強い）
    if "FRED_UNRATE" in sub.columns:
        ur = sub["FRED_UNRATE"].dropna()
        if len(ur) >= 40:
            chg = ur.iloc[-1] - ur.iloc[-40]
            scores.append(_normalize(-chg, -1.0, 1.0))

    for ticker in ["^GSPC", "^N225", "1306.T"]:
        if ticker in sub.columns:
            col = sub[ticker].dropna()
            if len(col) >= 40:
                ret = col.iloc[-1] / col.iloc[-40] - 1  # 約2ヶ月リターン
                scores.append(_normalize(ret, -0.20, 0.20))

    # VIX（低=景気強い）
    if "^VIX" in sub.columns:
        vix = sub["^VIX"].dropna()
        if len(vix) > 0:
            v = vix.iloc[-1]
            scores.append(_normalize(-v, -40, -12))

    return float(np.mean(scores)) if scores else 0.0


def calc_phase_probs(m_score: float, e_score: float) -> dict[str, float]:
    """
    M (金融: 正=緩和) × E (景気: 正=強い) → 4局面確率

    金融相場   : 緩和 (+M) × 弱い (-E)
    業績相場   : 緩和 (+M) × 強い (+E)
    逆金融相場 : 引き締め (-M) × 強い (+E)
    逆業績相場 : 引き締め (-M) × 弱い (-E)
    """
    p_ease   = _sigmoid(m_score * 3)    # 緩和確率
    p_tight  = 1 - p_ease               # 引き締め確率
    p_strong = _sigmoid(e_score * 3)    # 景気強い確率
    p_weak   = 1 - p_strong             # 景気弱い確率

    raw = {
        "金融相場":   p_ease  * p_weak,
        "業績相場":   p_ease  * p_strong,
        "逆金融相場": p_tight * p_strong,
        "逆業績相場": p_tight * p_weak,
    }
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}


# ─── セクタートレンド ──────────────────────────────────────────────────
def calc_sector_trends() -> dict[str, dict]:
    """
    各局面の代表セクターETFが直近N日でベンチマーク対比アウトパフォームしているか。

    Returns:
        {phase_name: {"us": float, "jp": float, "label": str}}
    """
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=90)).isoformat()

    us_tickers = list(US_SECTOR_ETF.keys()) + ["SPY"]
    jp_tickers = list(JP_SECTOR_ETF.keys()) + ["1306.T"]

    us_df = _dl(us_tickers, start, end)
    jp_df = _dl(jp_tickers, start, end)

    def _excess_return(df, sector_col, bench_col, n=LOOKBACK_SECTOR):
        """n日間の超過リターン(%)を計算。"""
        if sector_col not in df.columns or bench_col not in df.columns:
            return None
        col_s = df[sector_col].dropna()
        col_b = df[bench_col].dropna()
        if len(col_s) < n or len(col_b) < n:
            return None
        ret_s = col_s.iloc[-1] / col_s.iloc[-n] - 1
        ret_b = col_b.iloc[-1] / col_b.iloc[-n] - 1
        return (ret_s - ret_b) * 100

    result = {p: {"us": [], "jp": []} for p in PHASE_NAMES}

    for etf, (_, phase) in US_SECTOR_ETF.items():
        er = _excess_return(us_df, etf, "SPY")
        if er is not None:
            result[phase]["us"].append(er)

    for etf, (_, phase) in JP_SECTOR_ETF.items():
        er = _excess_return(jp_df, etf, "1306.T")
        if er is not None:
            result[phase]["jp"].append(er)

    out = {}
    for phase in PHASE_NAMES:
        us_vals = result[phase]["us"]
        jp_vals = result[phase]["jp"]
        us_avg = np.mean(us_vals) if us_vals else 0.0
        jp_avg = np.mean(jp_vals) if jp_vals else 0.0
        combined = (us_avg + jp_avg) / 2

        if combined > 1.0:
            label = "✅ BUY"
        elif combined < -1.0:
            label = "❌ SELL"
        else:
            label = "→ FLAT"

        out[phase] = {
            "us": us_avg,
            "jp": jp_avg,
            "combined": combined,
            "label": label,
        }

    return out


# ─── 遷移解説 ─────────────────────────────────────────────────────────
def generate_transition_commentary(
    history: list[tuple[str, dict[str, float]]]
) -> str:
    """
    3〜4週分の確率推移から遷移の解説テキストを生成。

    history: [(label, {phase: prob}), ...]  古い順
    """
    lines = []

    # 最有力局面の推移
    dominants = [max(probs, key=probs.get) for _, probs in history]
    changes = [d for i, d in enumerate(dominants) if i == 0 or d != dominants[i - 1]]

    # 直近の確率
    latest_label, latest_probs = history[-1]
    dominant_now = dominants[-1]
    prob_now = latest_probs[dominant_now]

    lines.append(f"【現在の最有力局面: {dominant_now}（確率 {prob_now:.0%}）】")
    lines.append("")

    # 変化の有無
    if len(set(dominants)) == 1:
        lines.append(f"▶ 過去3週間、一貫して「{dominant_now}」が最有力局面です。")
        lines.append("  局面の変化は見られず、現トレンドが継続しています。")
    else:
        prev = dominants[0]
        curr = dominants[-1]
        lines.append(f"▶ 局面推移: {' → '.join(dominants)}")
        lines.append(f"  「{prev}」から「{curr}」への移行の兆しが見られます。")

    lines.append("")

    # 各局面の確率変化
    lines.append("【各局面の確率変化】")
    first_probs = history[0][1]
    for phase in PHASE_NAMES:
        p_first = first_probs.get(phase, 0)
        p_last  = latest_probs.get(phase, 0)
        delta   = p_last - p_first
        arrow   = "↑" if delta > 0.03 else ("↓" if delta < -0.03 else "→")
        lines.append(f"  {phase:8s}: {p_first:.0%} → {p_last:.0%}  {arrow}{abs(delta):.0%}")

    lines.append("")

    # 局面ごとの解説
    transition_hints = {
        "金融相場":   "金融緩和・景気底打ち。不動産・IT・グロース株が優位。",
        "業績相場":   "景気回復・企業業績拡大。素材・機械・自動車が優位。",
        "逆金融相場": "引き締め継続・業績ピーク。銀行・商社・エネルギーが優位。",
        "逆業績相場": "景気後退・業績悪化。医薬品・食品・インフラが優位。",
    }
    lines.append(f"【{dominant_now}について】")
    lines.append(f"  {transition_hints[dominant_now]}")

    # 注目すべき動き
    max_rise = max(PHASE_NAMES,
                   key=lambda p: latest_probs.get(p, 0) - first_probs.get(p, 0))
    delta_max = latest_probs.get(max_rise, 0) - first_probs.get(max_rise, 0)
    if delta_max > 0.04:
        lines.append("")
        lines.append(f"⚠ 注目: 「{max_rise}」の確率が{delta_max:.0%}上昇中。")
        lines.append(f"  次局面への移行シグナルに注意してください。")

    return "\n".join(lines)


# ─── メイン描画 ────────────────────────────────────────────────────────
def run():
    print("データ取得中...", flush=True)

    # マクロデータ取得
    df = fetch_macro_data()
    if df.empty:
        print("エラー: マクロデータを取得できませんでした。")
        return

    # 3週前・2週前・1週前・直近 の4時点で確率を計算
    today_ts = pd.Timestamp(date.today())
    biz_days = pd.bdate_range(end=today_ts, periods=30).tolist()

    def _snap(offset_bdays: int) -> pd.Timestamp:
        """offset営業日前の日付。"""
        idx = max(0, len(biz_days) - 1 - offset_bdays)
        return biz_days[idx]

    time_points = [
        ("3週前", _snap(15)),
        ("2週前", _snap(10)),
        ("1週前", _snap(5)),
        ("直近",  _snap(0)),
    ]

    print("局面確率計算中...", flush=True)
    history = []
    m_scores = []
    e_scores = []
    for label, ts in time_points:
        m = calc_monetary_score(df, ts)
        e = calc_economy_score(df, ts)
        probs = calc_phase_probs(m, e)
        history.append((label, probs))
        m_scores.append(m)
        e_scores.append(e)

    print("セクタートレンド計算中...", flush=True)
    sector_trends = calc_sector_trends()

    # ─── 描画 ────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(16, 10))
    fig.patch.set_facecolor("#1a1a2e")
    gs = GridSpec(3, 4, figure=fig, hspace=0.45, wspace=0.35,
                  top=0.90, bottom=0.08, left=0.06, right=0.97)

    # タイトル
    latest_probs = history[-1][1]
    dominant = max(latest_probs, key=latest_probs.get)
    fig.suptitle(
        f"株式市場4局面判定  /  現在: {dominant}（確率 {latest_probs[dominant]:.0%}）",
        color="white", fontsize=14, fontweight="bold", y=0.97
    )

    # --- 上段: 3時点のバーグラフ + 直近 ─────────────────────────────
    axes_bar = []
    for col, (label, probs) in enumerate(history):
        ax = fig.add_subplot(gs[0, col])
        ax.set_facecolor("#16213e")
        vals = [probs[p] * 100 for p in PHASE_NAMES]
        bars = ax.bar(PHASE_NAMES, vals, color=PHASE_COLORS, width=0.6, alpha=0.85)
        ax.set_ylim(0, 80)
        ax.set_title(label, color="white", fontsize=10, fontweight="bold")
        ax.tick_params(colors="white", labelsize=7)
        ax.set_xticklabels([p[:4] for p in PHASE_NAMES], color="#aaaaaa", fontsize=7)
        ax.set_ylabel("確率 (%)", color="#aaaaaa", fontsize=7)
        for spine in ax.spines.values():
            spine.set_color("#333366")

        # 値ラベル
        for bar, val in zip(bars, vals):
            if val > 5:
                ax.text(bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + 0.5,
                        f"{val:.0f}%", ha="center", va="bottom",
                        color="white", fontsize=7, fontweight="bold")

        # 最有力に枠
        dom_idx = PHASE_NAMES.index(max(probs, key=probs.get))
        bars[dom_idx].set_edgecolor("white")
        bars[dom_idx].set_linewidth(2)
        axes_bar.append(ax)

    # --- 中段: 金融軸・景気軸スコア推移 (折れ線) ──────────────────────
    ax_m = fig.add_subplot(gs[1, :2])
    ax_e = fig.add_subplot(gs[1, 2:])

    labels = [h[0] for h in history]
    x = range(len(history))

    for ax, scores, title, pos_label, neg_label in [
        (ax_m, m_scores, "金融政策スコア", "緩和↑", "引き締め↓"),
        (ax_e, e_scores, "景気/業績スコア", "強い↑", "弱い↓"),
    ]:
        ax.set_facecolor("#16213e")
        ax.plot(x, scores, "o-", color="#FFD700", linewidth=2, markersize=6)
        ax.axhline(0, color="#888888", linestyle="--", linewidth=0.8)
        ax.fill_between(x, scores, 0,
                        where=[s > 0 for s in scores], alpha=0.2, color="#4CAF50")
        ax.fill_between(x, scores, 0,
                        where=[s <= 0 for s in scores], alpha=0.2, color="#FF5722")
        ax.set_ylim(-1.1, 1.1)
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, color="#aaaaaa", fontsize=8)
        ax.tick_params(colors="white", labelsize=7)
        ax.set_title(f"{title}  ({pos_label} / {neg_label})",
                     color="white", fontsize=9)
        ax.set_ylabel("スコア (-1〜+1)", color="#aaaaaa", fontsize=7)
        for spine in ax.spines.values():
            spine.set_color("#333366")
        for i, s in enumerate(scores):
            ax.text(i, s + 0.05 * (1 if s >= 0 else -1),
                    f"{s:+.2f}", ha="center", va="bottom" if s >= 0 else "top",
                    color="white", fontsize=7)

    # --- 下段: セクタートレンド ──────────────────────────────────────
    ax_s = fig.add_subplot(gs[2, :])
    ax_s.set_facecolor("#16213e")
    ax_s.axis("off")

    sector_x = 0.0
    col_w = 1.0 / len(PHASE_NAMES)
    for pi, phase in enumerate(PHASE_NAMES):
        info = sector_trends.get(phase, {})
        label = info.get("label", "→ FLAT")
        us_val = info.get("us", 0.0)
        jp_val = info.get("jp", 0.0)
        combined = info.get("combined", 0.0)

        color = PHASE_COLORS[pi]
        cx = col_w * pi + col_w / 2

        # フェーズ名
        ax_s.text(cx, 0.92, phase, ha="center", va="top",
                  color=color, fontsize=9, fontweight="bold",
                  transform=ax_s.transAxes)
        # ラベル (BUY/FLAT/SELL)
        ax_s.text(cx, 0.72, label, ha="center", va="top",
                  color="white", fontsize=11, fontweight="bold",
                  transform=ax_s.transAxes)
        # 数値
        ax_s.text(cx, 0.52,
                  f"米:{us_val:+.1f}%  日:{jp_val:+.1f}%",
                  ha="center", va="top", color="#aaaaaa", fontsize=7,
                  transform=ax_s.transAxes)

        # 区切り線
        if pi < len(PHASE_NAMES) - 1:
            ax_s.axvline(col_w * (pi + 1), color="#333366", linewidth=1)

    ax_s.set_title("セクタートレンド（vs ベンチマーク, 直近30日超過リターン）",
                   color="white", fontsize=9, pad=6)

    # ─── 遷移解説テキスト (右側パネル or コンソール) ───────────────────
    plt.tight_layout(rect=[0, 0, 1, 0.95])

    # PNG保存 → Windows標準ビューアで開く
    outpath = os.path.join(os.environ.get("TEMP", "C:/Temp"), "phase_chart.png")
    fig.savefig(outpath, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    subprocess.Popen(["explorer", outpath])

    # コンソールに解説を出力
    print()
    print("=" * 60)
    commentary = generate_transition_commentary(history)
    print(commentary)
    print("=" * 60)
    print()
    print("セクタートレンド:")
    for phase in PHASE_NAMES:
        info = sector_trends.get(phase, {})
        print(f"  {phase:8s}: {info.get('label','?'):8s}  "
              f"米:{info.get('us', 0):+.1f}%  日:{info.get('jp', 0):+.1f}%")
    print()
    print(f"[グラフを保存しました: {outpath}]")


if __name__ == "__main__":
    run()

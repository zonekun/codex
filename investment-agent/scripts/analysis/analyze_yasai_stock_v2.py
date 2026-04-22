# -*- coding: utf-8 -*-
"""野菜価格（平年比）× 週次株価リターン 相関・ラグ・イベント分析 v2.

改善点（前バージョンからの変更）:
  - 四半期業績 → 週次株価リターン（株価は期待先行で動くため）
  - 前週比 → 平年比（過去5ヶ年平均比: 季節性補正済みの最適指標）
  - MAFF過去Excel (y_past-1.xlsx) と現在Excel を結合 → 2017～2026の8年分
  - 対象企業を外食・スーパー・専用卸に拡張
  - ラグ分析追加 (lag 0, 1, 2, 4週)
設計方針:
  - BigQueryに週次リターン計算をオフロード
  - Python側は MAFF Excel 読み込み + 統計検定のみ
"""

import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account
from scipy import stats

warnings.filterwarnings("ignore")

# --- 設定 ---
KEY_PATH   = Path(r"C:\users\Administrator\Dropbox\claude\investment-agent\keys\gcp-service-account.json")
PROJECT_ID = "gmailpj-357912"
CACHE_DIR  = Path(__file__).parent.parent / "data" / "cache"
GENGO      = {"令和": 2018, "平成": 1988, "昭和": 1925}

# 対象企業 (TSE 4桁コード: BigQuery で使用)
TARGETS = {
    # --- 外食 ---
    "ゼンショーHD":       "7550",
    "すかいらーくHD":     "3197",
    "吉野家HD":           "9861",
    "くら寿司":           "2695",
    "日本マクドナルドHD":  "2702",
    "松屋フーズHD":       "9887",
    "ハイデイ日高":        "7611",
    "王将フードサービス":   "9936",
    "リンガーハット":      "8200",
    # --- スーパー ---
    "イオン":             "8267",
    "ライフコーポレーション": "8194",
    "ベルク":             "9974",
    "バローHD":           "9956",
    "アクシアルリテイリング": "8255",
    # --- 専用卸 ---
    "加藤産業":           "9869",
}

# ============================================================
# BigQuery: 指定銘柄の週次リターン
# ============================================================
WEEKLY_RETURN_SQL = """
WITH
weekly_last_day AS (
  SELECT
    sp.TICKER,
    DATE_TRUNC(sp.YEARDATE, WEEK(MONDAY)) AS WEEK_START,
    MAX(sp.YEARDATE)                       AS LAST_TRADE_DATE
  FROM `gmailpj-357912.STOCK.STOCK_PRICE` sp
  WHERE sp.TICKER IN UNNEST(@tickers)
    AND sp.YEARDATE >= '2017-01-01'
    AND sp.CLOSE IS NOT NULL AND sp.CLOSE > 0
  GROUP BY sp.TICKER, DATE_TRUNC(sp.YEARDATE, WEEK(MONDAY))
),
weekly_close AS (
  SELECT
    wld.TICKER,
    wld.WEEK_START,
    sp.CLOSE AS WEEK_CLOSE
  FROM weekly_last_day wld
  INNER JOIN `gmailpj-357912.STOCK.STOCK_PRICE` sp
    ON wld.TICKER = sp.TICKER AND wld.LAST_TRADE_DATE = sp.YEARDATE
),
weekly_ret AS (
  SELECT
    TICKER,
    WEEK_START,
    SAFE_DIVIDE(
      WEEK_CLOSE - LAG(WEEK_CLOSE) OVER (PARTITION BY TICKER ORDER BY WEEK_START),
      LAG(WEEK_CLOSE) OVER (PARTITION BY TICKER ORDER BY WEEK_START)
    ) AS RET
  FROM weekly_close
)
SELECT *
FROM weekly_ret
WHERE RET IS NOT NULL
  AND ABS(RET) < 0.4   -- 極端異常値除外
ORDER BY TICKER, WEEK_START
"""


def get_bq_client():
    creds = service_account.Credentials.from_service_account_file(str(KEY_PATH))
    return bigquery.Client(project=PROJECT_ID, credentials=creds)


def fetch_weekly_returns(client, tickers: list) -> pd.DataFrame:
    print(f"BigQuery: {len(tickers)}銘柄の週次リターン取得中...")
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("tickers", "STRING", tickers)
        ]
    )
    df = client.query(WEEKLY_RETURN_SQL, job_config=job_config).to_dataframe()
    df["WEEK_START"] = pd.to_datetime(df["WEEK_START"]).dt.tz_localize(None)
    print(f"  → {len(df):,}行, {df['TICKER'].nunique()}銘柄, "
          f"{df['WEEK_START'].min().date()} - {df['WEEK_START'].max().date()}")
    return df


# ============================================================
# MAFF 野菜価格: 過去+現在ファイルを結合 → 平年比シート
# ============================================================
def parse_jp_date(s: str):
    m = re.match(r"(令和|平成|昭和)(\d+)年(\d+)月(\d+)日", str(s).strip())
    if not m:
        return None
    g, y, mo, d = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return pd.Timestamp(GENGO[g] + y, mo, d)


def load_heinen_sheet(path: Path) -> pd.DataFrame:
    """平年比シートを週次DataFrameに変換（値は比率: 1.0=平年並み）."""
    raw = pd.read_excel(path, sheet_name="平年比", header=None)
    cols = ["DATE"] + [str(c) for c in raw.iloc[1, 1:]]
    rows = []
    for _, row in raw.iloc[2:].iterrows():
        dt = parse_jp_date(row.iloc[0])
        if dt is None:
            continue
        vals = {"DATE": dt}
        for i, col in enumerate(cols[1:], 1):
            try:
                vals[col] = float(row.iloc[i])
            except (ValueError, TypeError):
                vals[col] = np.nan
        rows.append(vals)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("DATE").sort_index()
    # 対象野菜の平均平年比
    veg_cols = [c for c in df.columns if c not in ["DATE"]]
    df["HEINEN_AVG"] = df[veg_cols].mean(axis=1)
    return df


def load_yasai_heinen() -> pd.DataFrame:
    """過去ファイル + 現在ファイルを結合して返す."""
    past_path    = CACHE_DIR / "yasai_price_maff_past.xlsx"
    current_path = CACHE_DIR / "yasai_price_maff.xlsx"

    df_past    = load_heinen_sheet(past_path)    # 2017/11 ~ 2021/3
    df_current = load_heinen_sheet(current_path) # 2021/4  ~ 現在

    # 重複除去 (current 優先)
    combined = pd.concat([df_past, df_current])
    combined = combined[~combined.index.duplicated(keep="last")]
    combined.sort_index(inplace=True)
    return combined


# ============================================================
# アライン + ラグ相関分析
# ============================================================
def align(yasai: pd.DataFrame, ret_wide: pd.DataFrame):
    """週キー (YYYY-Www) で揃える."""
    y_idx = yasai.index.strftime("%Y-W%W")
    s_idx = ret_wide.index.strftime("%Y-W%W")
    common = np.intersect1d(y_idx, s_idx)
    y = yasai[np.isin(y_idx, common)]
    s = ret_wide[np.isin(s_idx, common)]
    return y, s


def lag_correlation(yasai: pd.DataFrame, ret_wide: pd.DataFrame,
                    lags: list[int] = [0, 1, 2, 4]) -> pd.DataFrame:
    """野菜平年比（当週）× 株価リターン（当週+lag週後）のSpearman相関."""
    results = []
    for ticker in ret_wide.columns:
        for lag in lags:
            x = yasai["HEINEN_AVG"]
            y = ret_wide[ticker].shift(-lag)  # lag週後のリターン
            mask = x.notna() & y.notna()
            if mask.sum() < 30:
                continue
            corr, pval = stats.spearmanr(x[mask], y[mask])
            results.append({
                "TICKER": ticker,
                "lag週": lag,
                "Spearman相関": round(corr, 3),
                "p値": round(pval, 4),
                "n": int(mask.sum()),
                "有意": "★" if pval < 0.05 else ("△" if pval < 0.10 else ""),
            })
    df = pd.DataFrame(results)
    if df.empty:
        return df
    return df.sort_values("p値")


def event_study(yasai: pd.DataFrame, ret_wide: pd.DataFrame,
                threshold: float = 1.15, lags: list[int] = [0, 1, 2]) -> pd.DataFrame:
    """野菜平年比 >= threshold の週の翌lag週リターンを比較."""
    spike_mask  = yasai["HEINEN_AVG"] >= threshold
    normal_mask = (yasai["HEINEN_AVG"] >= 0.90) & (yasai["HEINEN_AVG"] <= 1.05)
    results = []
    for ticker in ret_wide.columns:
        for lag in lags:
            y = ret_wide[ticker].shift(-lag)
            sp = y[spike_mask & y.notna()]
            nm = y[normal_mask & y.notna()]
            if len(sp) < 8 or len(nm) < 10:
                continue
            _, pval = stats.mannwhitneyu(sp, nm, alternative="less")
            results.append({
                "TICKER": ticker,
                "lag週": lag,
                "高騰N": len(sp),
                "高騰時平均(%)": round(sp.mean() * 100, 2),
                "通常時平均(%)": round(nm.mean() * 100, 2),
                "差(pp)": round((sp.mean() - nm.mean()) * 100, 2),
                "p値": round(pval, 4),
                "有意": "★" if pval < 0.05 else ("△" if pval < 0.10 else ""),
            })
    df = pd.DataFrame(results)
    if df.empty:
        return df
    return df.sort_values("差(pp)")


# ============================================================
# メイン
# ============================================================
def main():
    print("=" * 70)
    print("野菜価格（平年比）× 週次株価リターン 相関・ラグ・イベント分析")
    print("=" * 70)

    # 1. 野菜価格（平年比）
    print("\n1. 野菜価格（平年比）ロード中...")
    yasai = load_yasai_heinen()
    print(f"   期間: {yasai.index.min().date()} ～ {yasai.index.max().date()}, {len(yasai)}週")
    print(f"   品目: {[c for c in yasai.columns if c != 'HEINEN_AVG']}")
    print(f"   平年比範囲: {yasai['HEINEN_AVG'].min():.3f} ～ {yasai['HEINEN_AVG'].max():.3f}")

    # 2. BigQuery 週次リターン
    client   = get_bq_client()
    tickers  = list(TARGETS.values())
    ret_df   = fetch_weekly_returns(client, tickers)

    # ピボット: 行=WEEK_START, 列=TICKER
    ret_wide = ret_df.pivot(index="WEEK_START", columns="TICKER", values="RET")
    ret_wide.index = pd.to_datetime(ret_wide.index)

    # TICKER→社名マッピング
    rev = {v: k for k, v in TARGETS.items()}
    ret_wide.columns = [rev.get(t, t) for t in ret_wide.columns]
    print(f"\n   取得銘柄: {list(ret_wide.columns)}")

    # 3. アライン
    y, s = align(yasai, ret_wide)
    print(f"\n3. 共通週数: {len(y)}")

    # 4. ラグ相関分析
    print("\n" + "=" * 70)
    print("【ラグ相関分析】野菜平年比 × 株価リターン (Spearman)")
    print("lag=0: 同週  lag=1: 翌週  lag=2: 翌々週  lag=4: 1ヶ月後")
    print("有意(p<0.05)★ / 参考(p<0.10)△")
    print("=" * 70)
    lag_df = lag_correlation(y, s, lags=[0, 1, 2, 4])
    sig_lag = lag_df[lag_df["有意"].isin(["★", "△"])]
    if sig_lag.empty:
        print("有意な相関なし")
    else:
        print(sig_lag.to_string(index=False))

    # 5. イベント分析 (平年比 >= 1.15)
    print("\n" + "=" * 70)
    print("【イベント分析】野菜平年比≥1.15（平年比15%超）の週の株価リターン")
    spike_count = int((y["HEINEN_AVG"] >= 1.15).sum())
    print(f"   高騰週数: {spike_count}週 / 全{len(y)}週")
    print("=" * 70)
    ev_df = event_study(y, s, threshold=1.15, lags=[0, 1, 2])
    sig_ev = ev_df[ev_df["有意"].isin(["★", "△"])]
    if sig_ev.empty:
        print("有意な結果なし")
    else:
        print(sig_ev.to_string(index=False))

    # 6. 全結果表示（有意フラグなしも含む）
    print("\n" + "=" * 70)
    print("【全ラグ相関結果】（有意なし含む / ソート: p値）")
    print("=" * 70)
    print(lag_df.head(30).to_string(index=False))

    # 7. サマリー
    n_star_lag = len(lag_df[lag_df["有意"] == "★"])
    n_star_ev  = len(ev_df[ev_df["有意"] == "★"]) if not ev_df.empty else 0
    print("\n" + "=" * 70)
    print("【サマリー】")
    print(f"有意な相関(p<0.05): {n_star_lag}件 / 有意なイベント効果: {n_star_ev}件")
    total = n_star_lag + n_star_ev
    if total >= 3:
        print("→ ANALYZED_PASS: バックテストに進む価値あり")
    elif total > 0:
        print("→ 限定的な結果。追加検証を推奨")
    else:
        print("→ ANALYZED_FAIL: 有意な結果なし")


if __name__ == "__main__":
    main()

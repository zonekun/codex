# -*- coding: utf-8 -*-
"""野菜価格（平年比）× 週次株価リターン 時系列相関分析 v3.

旧手法の問題点を修正:
  - スピアマン相関 → 見せかけの相関が発生（平年比 ACF≈0.96 が原因）
  - 新手法:
      1. ADF 検定で定常性確認
      2. 1階差分（Δ平年比）で定常化
      3. 事前白色化（Prewhitening）後の CCF
      4. グレンジャー因果性検定（VAR フレームワーク）
      5. CAR ベースのイベント分析

参考: docs/knowledges/tools/002_timeseries_correlation_algorithm.md
"""

import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account
from scipy import stats
from statsmodels.tsa.stattools import adfuller, ccf as sm_ccf
from statsmodels.tsa.api import VAR
from statsmodels.stats.multitest import multipletests

warnings.filterwarnings("ignore")

# --- 設定 ---
KEY_PATH   = Path(r"C:\users\Administrator\Dropbox\claude\investment-agent\keys\gcp-service-account.json")
PROJECT_ID = "gmailpj-357912"
CACHE_DIR  = Path(__file__).parent.parent / "data" / "cache"
GENGO      = {"令和": 2018, "平成": 1988, "昭和": 1925}

TARGETS = {
    # 外食
    "ゼンショーHD":       "7550",
    "すかいらーくHD":     "3197",
    "吉野家HD":           "9861",
    "くら寿司":           "2695",
    "日本マクドナルドHD":  "2702",
    "松屋フーズHD":       "9887",
    "ハイデイ日高":        "7611",
    "王将フードサービス":   "9936",
    "リンガーハット":      "8200",
    # スーパー
    "イオン":             "8267",
    "ライフコーポレーション": "8194",
    "ベルク":             "9974",
    "バローHD":           "9956",
    "アクシアルリテイリング": "8255",
    # 専用卸
    "加藤産業":           "9869",
}

# ============================================================
# データ読み込み（前回と同じ）
# ============================================================
def parse_jp_date(s: str):
    m = re.match(r"(令和|平成|昭和)(\d+)年(\d+)月(\d+)日", str(s).strip())
    if not m:
        return None
    g, y, mo, d = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return pd.Timestamp(GENGO[g] + y, mo, d)


def load_yasai_heinen() -> pd.DataFrame:
    def _load(path):
        raw = pd.read_excel(path, sheet_name="平年比", header=None)
        rows = []
        for _, row in raw.iloc[2:].iterrows():
            dt = parse_jp_date(row.iloc[0])
            if dt is None:
                continue
            vals = {"DATE": dt}
            for i in range(1, len(row)):
                try:
                    vals[f"c{i}"] = float(row.iloc[i])
                except:
                    vals[f"c{i}"] = np.nan
            rows.append(vals)
        df = pd.DataFrame(rows).set_index("DATE").sort_index()
        df["HEINEN_AVG"] = df.mean(axis=1)
        return df[["HEINEN_AVG"]]

    df = pd.concat([
        _load(CACHE_DIR / "yasai_price_maff_past.xlsx"),
        _load(CACHE_DIR / "yasai_price_maff.xlsx"),
    ])
    return df[~df.index.duplicated(keep="last")].sort_index().dropna()


WEEKLY_RETURN_SQL = """
WITH
wld AS (
  SELECT TICKER, DATE_TRUNC(YEARDATE, WEEK(MONDAY)) AS WS, MAX(YEARDATE) AS LTD
  FROM `gmailpj-357912.STOCK.STOCK_PRICE`
  WHERE TICKER IN UNNEST(@tickers) AND YEARDATE >= '2017-01-01'
    AND CLOSE IS NOT NULL AND CLOSE > 0
  GROUP BY TICKER, WS
),
wc AS (
  SELECT w.TICKER, w.WS, s.CLOSE
  FROM wld w JOIN `gmailpj-357912.STOCK.STOCK_PRICE` s
    ON w.TICKER = s.TICKER AND w.LTD = s.YEARDATE
),
wr AS (
  SELECT TICKER, WS,
    SAFE_DIVIDE(CLOSE - LAG(CLOSE) OVER (PARTITION BY TICKER ORDER BY WS),
                LAG(CLOSE) OVER (PARTITION BY TICKER ORDER BY WS)) AS RET
  FROM wc
)
SELECT * FROM wr WHERE RET IS NOT NULL AND ABS(RET) < 0.4
ORDER BY TICKER, WS
"""


def fetch_returns(tickers: list) -> pd.DataFrame:
    creds = service_account.Credentials.from_service_account_file(str(KEY_PATH))
    client = bigquery.Client(project=PROJECT_ID, credentials=creds)
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("tickers", "STRING", tickers)]
    )
    df = client.query(WEEKLY_RETURN_SQL, job_config=cfg).to_dataframe()
    df["WS"] = pd.to_datetime(df["WS"]).dt.tz_localize(None)
    return df


# ============================================================
# Step 1: ADF 検定
# ============================================================
def adf_report(series: np.ndarray, name: str) -> bool:
    res = adfuller(series, autolag="AIC")
    is_stat = res[1] < 0.05
    print(f"  {name}: ADF={res[0]:.3f}, p={res[1]:.4f}, lag={res[2]}"
          f" → {'✅ I(0) 定常' if is_stat else '⚠ I(1) 非定常'}")
    return is_stat


# ============================================================
# Step 2 & 3: 事前白色化後の CCF
# ============================================================
def prewhiten(series: np.ndarray, max_ar: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """AR(p) を AIC で選択して当てはめ、残差を返す。"""
    from statsmodels.tsa.ar_model import AutoReg
    best_aic = np.inf
    best_resid = series - series.mean()
    best_p = 0
    for p in range(1, max_ar + 1):
        try:
            fit = AutoReg(series, lags=p, old_names=False).fit()
            if fit.aic < best_aic:
                best_aic = fit.aic
                best_resid = fit.resid
                best_p = p
        except:
            break
    return best_resid, best_p


def ccf_analysis(x_resid: np.ndarray, y_resid: np.ndarray,
                 max_lag: int = 8, n: int = None) -> pd.DataFrame:
    """事前白色化後の CCF（正ラグ: x が y を先行）。"""
    if n is None:
        n = len(x_resid)
    ci = 1.96 / np.sqrt(n)
    rows = []
    for lag in range(0, max_lag + 1):
        # lag>0: x_t → y_{t+lag}（野菜が株価を先行）
        if lag == 0:
            xv, yv = x_resid, y_resid
        else:
            xv, yv = x_resid[:-lag], y_resid[lag:]
        mask = ~(np.isnan(xv) | np.isnan(yv))
        if mask.sum() < 20:
            continue
        r = np.corrcoef(xv[mask], yv[mask])[0, 1]
        rows.append({"lag週": lag, "CCF": round(r, 4),
                     "95%CI": round(ci, 4),
                     "有意": "★" if abs(r) > ci else ""})
    return pd.DataFrame(rows)


# ============================================================
# Step 4: グレンジャー因果性検定
# ============================================================
def granger_test(delta_h: np.ndarray, r: np.ndarray,
                 max_lags: int = 4) -> pd.DataFrame:
    """Δ平年比 → 株価リターン のグレンジャー因果性を VAR で検定。"""
    df_var = pd.DataFrame({"dh": delta_h, "ret": r}).dropna()
    if len(df_var) < 50:
        return pd.DataFrame()

    # AIC でラグ次数選択
    try:
        model = VAR(df_var)
        lag_order = model.select_order(maxlags=max_lags)
        p = lag_order.aic
        if p == 0:
            p = 1
    except:
        p = 2

    # VAR 推定 & グレンジャー検定
    try:
        res = model.fit(p)
        # 「dh は ret を Granger-cause するか」
        gc = res.test_causality("ret", "dh", kind="f")
        rows = [{
            "ラグ次数": p,
            "F統計量": round(gc.test_statistic, 3),
            "p値": round(gc.pvalue, 4),
            "有意": "★" if gc.pvalue < 0.05 else ("△" if gc.pvalue < 0.10 else ""),
        }]
        return pd.DataFrame(rows)
    except:
        return pd.DataFrame()


# ============================================================
# Step 5: CAR ベースのイベント分析（白色化後）
# ============================================================
def car_event_analysis(delta_h_resid: np.ndarray, r_resid: np.ndarray,
                       threshold_pct: float = 90,
                       car_windows: list[int] = [1, 2, 4]) -> pd.DataFrame:
    """
    Δ平年比残差が上位 threshold_pct% 超の週をイベントとし、
    その後 car_windows 週の累積超過リターンを分析。
    """
    thr = np.nanpercentile(delta_h_resid, threshold_pct)
    event_mask = delta_h_resid >= thr
    non_event  = delta_h_resid < np.nanpercentile(delta_h_resid, 50)  # 下位50%

    rows = []
    for w in car_windows:
        # 白色化済みリターン残差の cumsum で CAR を近似
        ev_car  = []
        non_car = []
        for i in range(len(r_resid) - w):
            if event_mask[i]:
                ev_car.append(np.nansum(r_resid[i:i+w]))
            elif non_event[i]:
                non_car.append(np.nansum(r_resid[i:i+w]))
        if len(ev_car) < 5 or len(non_car) < 10:
            continue
        ev_arr  = np.array(ev_car)
        non_arr = np.array(non_car)
        _, pval = stats.mannwhitneyu(ev_arr, non_arr, alternative="less")
        rows.append({
            "累積ウィンドウ(週)": w,
            "イベントN": len(ev_arr),
            "イベント時CAR平均": round(ev_arr.mean() * 100, 3),
            "非イベントCAR平均": round(non_arr.mean() * 100, 3),
            "差(pp)": round((ev_arr.mean() - non_arr.mean()) * 100, 3),
            "p値": round(pval, 4),
            "有意": "★" if pval < 0.05 else ("△" if pval < 0.10 else ""),
        })
    return pd.DataFrame(rows)


# ============================================================
# メイン
# ============================================================
def main():
    print("=" * 70)
    print("野菜価格（Δ平年比）× 週次株価リターン  時系列分析 v3")
    print("手法: 事前白色化 CCF + グレンジャー因果性 + CAR イベント分析")
    print("=" * 70)

    # --- データ準備 ---
    yasai  = load_yasai_heinen()
    h      = yasai["HEINEN_AVG"].values
    dh     = np.diff(h)           # 1階差分（Δ平年比）
    dates  = yasai.index[1:]      # diff で1行短くなる

    ret_raw = fetch_returns(list(TARGETS.values()))
    ret_wide = (ret_raw.pivot(index="WS", columns="TICKER", values="RET")
                .rename(columns={v: k for k, v in TARGETS.items()}))
    ret_wide.index = pd.to_datetime(ret_wide.index)

    print(f"\n野菜データ: {yasai.index.min().date()} ～ {yasai.index.max().date()}, {len(h)}週")
    print(f"株価データ: {ret_wide.index.min().date()} ～ {ret_wide.index.max().date()}")

    # --- ADF 検定 ---
    print("\n" + "=" * 70)
    print("【Step 1】ADF 定常性検定")
    print("=" * 70)
    adf_report(h,  "平年比（水準）")
    adf_report(dh, "平年比（Δ1階差分）")

    dh_ser = pd.Series(dh, index=dates)  # Δ平年比（インデックス付き）

    # --- 事前白色化（グローバル）---
    dh_resid, dh_p = prewhiten(dh)
    # AR(p) で最初の p 行が消えるため、対応するインデックスを末尾から合わせる
    dhr_ser = pd.Series(dh_resid, index=dates[-len(dh_resid):])
    print(f"\n  Δ平年比 の AR ラグ次数（AIC）: p = {dh_p}")

    # --- 銘柄ごとに分析 ---
    ccf_summary    = []
    granger_summary = []
    car_summary    = []

    for company, ticker in TARGETS.items():
        # 株価リターンとアライン
        if company not in ret_wide.columns:
            continue
        r_series = ret_wide[company].dropna()

        # Δ平年比（水準）とのアライン
        common_idx = dh_ser.index.intersection(r_series.index)
        if len(common_idx) < 60:
            continue

        dh_aln = dh_ser.loc[common_idx].values
        r_aln  = r_series.loc[common_idx].values
        n      = len(common_idx)

        # 白色化済みΔ平年比との共通インデックス（短め）
        common_idx2 = dhr_ser.index.intersection(r_series.index)
        dhr_aln = dhr_ser.loc[common_idx2].values

        # 株価リターンも白色化（白色化済みΔ平年比と同じ共通インデックスで）
        r_aln2   = r_series.loc[common_idx2].values
        r_resid, r_p = prewhiten(r_aln2)
        # 白色化後の長さを揃える
        min_len  = min(len(dhr_aln), len(r_resid))
        dhr_trim = dhr_aln[-min_len:]
        r_trim   = r_resid[-min_len:]

        # CCF
        ccf_df = ccf_analysis(dhr_trim, r_trim, max_lag=4, n=n)
        if not ccf_df.empty:
            best = ccf_df.loc[ccf_df["CCF"].abs().idxmax()]
            ccf_summary.append({
                "会社": company,
                "最大CCFのlag週": int(best["lag週"]),
                "CCF": best["CCF"],
                "95%CI": best["95%CI"],
                "有意": best["有意"],
            })

        # グレンジャー因果性
        gc_df = granger_test(dh_aln, r_aln, max_lags=4)
        if not gc_df.empty:
            gc_df.insert(0, "会社", company)
            granger_summary.append(gc_df)

        # CAR イベント分析
        car_df = car_event_analysis(dhr_trim, r_trim, threshold_pct=85, car_windows=[1, 2, 4])
        if not car_df.empty:
            car_df.insert(0, "会社", company)
            car_summary.append(car_df)

    # --- 出力 ---
    print("\n" + "=" * 70)
    print("【Step 3】事前白色化後 CCF（Δ平年比 → 株価リターン の各ラグ相関）")
    print(f"  95%CI = ±{1.96/np.sqrt(len(dh)):.4f}  有意 ★")
    print("=" * 70)
    ccf_df_all = pd.DataFrame(ccf_summary).sort_values("CCF")
    print(ccf_df_all.to_string(index=False))

    print("\n" + "=" * 70)
    print("【Step 4】グレンジャー因果性検定（Δ平年比 → 株価リターン）")
    print("  H0: 野菜価格変化は株価リターンを Granger-cause しない")
    print("=" * 70)
    if granger_summary:
        gc_all = pd.concat(granger_summary, ignore_index=True)
        # 多重比較補正 (Benjamini-Hochberg)
        if len(gc_all) > 1:
            reject, pvals_corr, _, _ = multipletests(gc_all["p値"].values, method="fdr_bh")
            gc_all["p値(BH補正)"] = pvals_corr.round(4)
            gc_all["有意(補正後)"] = ["★" if p < 0.05 else ("△" if p < 0.10 else "")
                                       for p in pvals_corr]
        print(gc_all.sort_values("p値").to_string(index=False))
    else:
        print("  データ不足")

    print("\n" + "=" * 70)
    print("【Step 5】CAR イベント分析（Δ平年比残差 上位15% → CAR）")
    print("  イベント定義: 野菜価格変化の白色化残差が上位 15% の週")
    print("=" * 70)
    if car_summary:
        car_all = pd.concat(car_summary, ignore_index=True)
        sig_car = car_all[car_all["有意"].isin(["★", "△"])]
        if sig_car.empty:
            print("  有意な結果なし")
        else:
            print(sig_car.to_string(index=False))
        print("\n--- 全結果（有意無し含む、差の小さい順） ---")
        print(car_all.sort_values("差(pp)").head(15).to_string(index=False))
    else:
        print("  データ不足")

    # --- サマリー ---
    n_ccf_sig   = sum(1 for r in ccf_summary if r["有意"] == "★")
    n_gc_sig    = (gc_all["有意"].value_counts().get("★", 0)
                   if granger_summary else 0)
    n_car_sig   = (car_all["有意"].value_counts().get("★", 0)
                   if car_summary else 0)

    print("\n" + "=" * 70)
    print("【サマリー】")
    print(f"  CCF 有意(★):      {n_ccf_sig}件")
    print(f"  Granger 有意(★):  {n_gc_sig}件（BH補正後）")
    print(f"  CAR 有意(★):      {n_car_sig}件")
    total = n_ccf_sig + n_gc_sig + n_car_sig
    if total >= 3:
        print("→ ANALYZED_PASS: バックテストに進む価値あり")
    elif total > 0:
        print("→ 限定的。追加検証を推奨")
    else:
        print("→ ANALYZED_FAIL: 有意な結果なし（時系列的に適切な手法でも仮説支持されず）")


if __name__ == "__main__":
    main()

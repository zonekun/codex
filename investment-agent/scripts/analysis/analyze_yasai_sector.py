# -*- coding: utf-8 -*-
"""野菜価格 × セクター別株価リターン 相関・イベント分析.

設計方針:
  - 週次終値取得・リターン計算・セクター平均化 → BigQueryにオフロード
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
KEY_PATH    = Path(r"C:\users\Administrator\Dropbox\claude\investment-agent\keys\gcp-service-account.json")
PROJECT_ID  = "gmailpj-357912"
CACHE_DIR   = Path(__file__).parent.parent / "data" / "cache"
EXCEL_PATH  = CACHE_DIR / "yasai_price_maff.xlsx"
GENGO       = {"令和": 2018, "平成": 1988, "昭和": 1925}

# ============================================================
# BigQuery: セクター別週次リターンをまるごと計算して返す
# ============================================================
SECTOR_RETURN_SQL = """
WITH
-- 各銘柄×週の「最終取引日」を特定
weekly_last_day AS (
  SELECT
    sp.TICKER,
    cl.INDUSTRY_33_CATEGORY                       AS SECTOR,
    DATE_TRUNC(sp.YEARDATE, WEEK(MONDAY))          AS WEEK_START,
    MAX(sp.YEARDATE)                               AS LAST_TRADE_DATE
  FROM `gmailpj-357912.STOCK.STOCK_PRICE` sp
  INNER JOIN `gmailpj-357912.STOCK.STOCK_CODE_LIST` cl
    ON sp.TICKER = cl.TICKER AND cl.EXCHANGE = 'TSE'
  WHERE sp.YEARDATE >= '2021-01-01'
    AND cl.INDUSTRY_33_CATEGORY IS NOT NULL
    AND cl.MARKET_CATEGORY IN (
      'プライム（内国株式）',
      'スタンダード（内国株式）',
      'グロース（内国株式）'
    )
    AND sp.CLOSE IS NOT NULL AND sp.CLOSE > 0
  GROUP BY sp.TICKER, cl.INDUSTRY_33_CATEGORY,
           DATE_TRUNC(sp.YEARDATE, WEEK(MONDAY))
),

-- 最終取引日の終値を取得（週次終値）
weekly_close AS (
  SELECT
    wld.TICKER,
    wld.SECTOR,
    wld.WEEK_START,
    sp.CLOSE AS WEEK_CLOSE
  FROM weekly_last_day wld
  INNER JOIN `gmailpj-357912.STOCK.STOCK_PRICE` sp
    ON wld.TICKER = sp.TICKER
   AND wld.LAST_TRADE_DATE = sp.YEARDATE
),

-- 銘柄別週次リターン計算（前週終値との比）
stock_weekly_ret AS (
  SELECT
    TICKER,
    SECTOR,
    WEEK_START,
    SAFE_DIVIDE(
      WEEK_CLOSE - LAG(WEEK_CLOSE) OVER (PARTITION BY TICKER ORDER BY WEEK_START),
      LAG(WEEK_CLOSE) OVER (PARTITION BY TICKER ORDER BY WEEK_START)
    ) AS RET
  FROM weekly_close
),

-- セクター別週次平均リターン
sector_return AS (
  SELECT
    WEEK_START,
    SECTOR,
    COUNT(DISTINCT TICKER) AS N_STOCKS,
    AVG(RET)               AS AVG_RETURN,
    STDDEV(RET)            AS STD_RETURN
  FROM stock_weekly_ret
  WHERE RET IS NOT NULL
    AND ABS(RET) < 0.5     -- 上場廃止等の極端な異常値を除外
  GROUP BY WEEK_START, SECTOR
)

SELECT *
FROM sector_return
WHERE N_STOCKS >= 5               -- 5銘柄以上のセクターのみ
ORDER BY WEEK_START, SECTOR
"""


def get_bq_client():
    creds = service_account.Credentials.from_service_account_file(str(KEY_PATH))
    return bigquery.Client(project=PROJECT_ID, credentials=creds)


def fetch_sector_returns(client) -> pd.DataFrame:
    """BigQuery でセクター別週次リターンを取得."""
    print("BigQuery: セクター別週次リターンを計算中...")
    df = client.query(SECTOR_RETURN_SQL).to_dataframe()
    df["WEEK_START"] = pd.to_datetime(df["WEEK_START"]).dt.tz_localize(None)
    print(f"  → {len(df):,}行, {df['SECTOR'].nunique()}セクター, "
          f"{df['WEEK_START'].min().date()} - {df['WEEK_START'].max().date()}")
    return df


# ============================================================
# Python: MAFF Excelから野菜価格データを読み込む
# ============================================================
def parse_jp_date(s: str):
    m = re.match(r"(令和|平成|昭和)(\d+)年(\d+)月(\d+)日", str(s).strip())
    if not m:
        return None
    g, y, mo, d = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return pd.Timestamp(GENGO[g] + y, mo, d)


def load_yasai_price(path: Path) -> pd.DataFrame:
    """農水省Excelの「前週比」シートを使う（変化率が直接得られる）."""
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
                vals[col] = v - 1.0   # 前週比→変化率（0基準）
            except (ValueError, TypeError):
                vals[col] = np.nan
        rows.append(vals)
    df = pd.DataFrame(rows).set_index("DATE").sort_index()
    # 野菜平均変化率（NaNを除く列平均）
    df["YASAI_AVG"] = df.mean(axis=1)
    return df


# ============================================================
# 分析
# ============================================================
def align(yasai: pd.DataFrame, sector_wide: pd.DataFrame) -> tuple:
    """農水省（月曜基準）とBQ（月曜基準 WEEK_START）を週キーで合わせる."""
    # 農水省は「X日の週」= その週の月曜日
    y_idx = yasai.index.strftime("%Y-W%W")
    s_idx = sector_wide.index.strftime("%Y-W%W")
    common = np.intersect1d(y_idx, s_idx)
    y = yasai[np.isin(y_idx, common)]
    s = sector_wide[np.isin(s_idx, common)]
    return y, s


def correlation_analysis(yasai: pd.DataFrame, sector_wide: pd.DataFrame) -> pd.DataFrame:
    results = []
    for sector in sector_wide.columns:
        for veg in ["YASAI_AVG", "キャベツ", "ねぎ", "レタス", "トマト", "きゅうり", "たまねぎ"]:
            if veg not in yasai.columns:
                continue
            x = yasai[veg]
            y = sector_wide[sector]
            mask = x.notna() & y.notna()
            if mask.sum() < 30:
                continue
            corr, pval = stats.spearmanr(x[mask], y[mask])
            results.append({
                "セクター": sector, "野菜": veg,
                "相関係数": round(corr, 3), "p値": round(pval, 4),
                "n": int(mask.sum()),
                "有意": "★" if pval < 0.05 else ("△" if pval < 0.10 else "")
            })
    return pd.DataFrame(results).sort_values("p値")


def event_analysis(yasai: pd.DataFrame, sector_wide: pd.DataFrame,
                   threshold: float = 0.03) -> pd.DataFrame:
    spike = yasai["YASAI_AVG"] >= threshold
    normal = yasai["YASAI_AVG"].abs() < 0.01
    results = []
    for sector in sector_wide.columns:
        y = sector_wide[sector]
        sp = y[spike & y.notna()]
        nm = y[normal & y.notna()]
        if len(sp) < 5 or len(nm) < 10:
            continue
        _, pval = stats.mannwhitneyu(sp, nm, alternative="less")
        results.append({
            "セクター": sector,
            "急騰週N": len(sp),
            "急騰週平均(%)": round(sp.mean() * 100, 2),
            "通常週平均(%)": round(nm.mean() * 100, 2),
            "差(pp)": round((sp.mean() - nm.mean()) * 100, 2),
            "p値": round(pval, 4),
            "有意": "★" if pval < 0.05 else ("△" if pval < 0.10 else "")
        })
    return pd.DataFrame(results).sort_values("差(pp)")


def main():
    print("=" * 65)
    print("野菜価格 × セクター週次リターン 相関・イベント分析")
    print("=" * 65)

    # 1. BigQuery: セクター別週次リターン
    client = get_bq_client()
    sector_df = fetch_sector_returns(client)

    # ピボット: 行=WEEK_START, 列=SECTOR
    sector_wide = sector_df.pivot(index="WEEK_START", columns="SECTOR", values="AVG_RETURN")
    sector_wide.index = pd.to_datetime(sector_wide.index)

    print(f"\nセクター一覧 ({sector_wide.shape[1]}件):")
    print("  " + ", ".join(sector_wide.columns.tolist()))

    # 2. Python: 農水省野菜価格
    yasai = load_yasai_price(EXCEL_PATH)
    print(f"\n野菜価格データ: {yasai.index.min().date()} - {yasai.index.max().date()}, {len(yasai)}週")

    # 3. アライン
    y, s = align(yasai, sector_wide)
    print(f"共通週数: {len(y)}")

    # 4. 相関分析
    print("\n" + "=" * 65)
    print("【相関分析】野菜価格変化率 × セクター週次リターン（Spearman）")
    print("有意（p<0.05）★ / 参考（p<0.10）△")
    print("=" * 65)
    corr_df = correlation_analysis(y, s)
    sig = corr_df[corr_df["有意"].isin(["★", "△"])]
    print(sig.to_string(index=False))

    # 5. イベント分析
    print("\n" + "=" * 65)
    print("【イベント分析】野菜平均前週比+3%超の週のセクターリターン")
    print("=" * 65)
    event_df = event_analysis(y, s, threshold=0.03)
    sig_ev = event_df[event_df["有意"].isin(["★", "△"])]
    print(sig_ev.to_string(index=False) if not sig_ev.empty else "有意なセクターなし")

    # 6. サマリー
    n_star = len(corr_df[corr_df["有意"] == "★"])
    n_ev_star = len(event_df[event_df["有意"] == "★"])
    print("\n" + "=" * 65)
    print("【サマリー】")
    print(f"相関: 有意(p<0.05) {n_star}件 / イベント効果: 有意(p<0.05) {n_ev_star}件")
    if n_star + n_ev_star >= 3:
        print("→ ANALYZED_PASS: バックテストに進む価値あり")
    elif n_star + n_ev_star > 0:
        print("→ 限定的な有意結果。追加検証推奨")
    else:
        print("→ ANALYZED_FAIL: 有意な結果なし")


if __name__ == "__main__":
    main()

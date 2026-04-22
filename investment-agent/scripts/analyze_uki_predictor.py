"""
UKI予測モデル v2: 20営業日先の高値・安値予測による短期売買シグナル
ノートブック: Copy_of_20220915_jquantsapi_uki_predictor.ipynb を参考に実装

特徴量 v2（アイデア①「財務開示後の価格調整ラグ」を深化）:
  テクニカル: リターン, 出来高比率, ボラティリティ, 値幅, ATR,
              ヒゲ幅(hig_range), ギャップ(gap_range), 日中変動(day_range), 市場インパクト(MI)
  ファンダメンタルズ: 収益性指標, 前期比変化率, ROA, 自己資本比率
  マッチング特徴量（決算サプライズ）: m_sales, m_ope_income, m_net_income

ターゲット:
  label_high_20: 翌20営業日の最高終値上昇率
  label_low_20:  翌20営業日の最低終値下落率

モデル: XGBoost（high用・low用 2本）
評価:  Pearson R², 方向正解率, CAR分析
"""

import os, sys, warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import subprocess
from datetime import datetime

warnings.filterwarnings("ignore")

# パス設定
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '.venv', 'Lib', 'site-packages'))
KEY_FILE  = os.path.join(os.path.dirname(__file__), '..', 'keys', 'gcp-service-account.json')
OUT_DIR   = os.path.join(os.path.dirname(__file__), '..', 'data', 'csv', 'uki_predictor')
os.makedirs(OUT_DIR, exist_ok=True)

TRAIN_END   = "2023-03-31"
TEST_START  = "2023-06-01"   # purge 45日後
TEST_END    = "2025-12-31"
PRICE_START = "2019-01-01"   # 特徴量計算の余裕期間含む
UNIVERSE_N  = 1000            # 出来高上位N銘柄

plt.rcParams['font.family'] = 'Yu Gothic'

def log(msg): print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# ==========================================
# BQ接続
# ==========================================
def get_bq():
    from google.oauth2 import service_account
    from google.cloud import bigquery
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return bigquery.Client(project="gmailpj-357912", credentials=creds)

# ==========================================
# Step 1: ユニバース取得（出来高上位N銘柄）
# ==========================================
def fetch_universe(bq) -> list:
    log("ユニバース取得中...")
    sql = f"""
    SELECT p.TICKER
    FROM (
        SELECT TICKER, AVG(VOLUME) AS avg_vol
        FROM `gmailpj-357912.STOCK.STOCK_PRICE`
        WHERE YEARDATE BETWEEN '2020-01-01' AND '{TRAIN_END}'
          AND VOLUME > 0
        GROUP BY TICKER
    ) p
    JOIN (
        SELECT TICKER
        FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
        WHERE EXCHANGE = 'TSE'
          AND MARKET_CATEGORY IN (
              'プライム（内国株式）',
              'スタンダード（内国株式）',
              'グロース（内国株式）'
          )
    ) m ON p.TICKER = m.TICKER
    ORDER BY avg_vol DESC
    LIMIT {UNIVERSE_N}
    """
    tickers = [r.TICKER for r in bq.query(sql).result()]
    log(f"  → {len(tickers)} 銘柄")
    return tickers

# ==========================================
# Step 2: 株価テクニカル特徴量 + ラベル生成（BQ SQL）
# ==========================================
TECH_SQL = """
WITH base AS (
    SELECT
        YEARDATE,
        TICKER,
        OPEN,
        HIGH,
        LOW,
        CLOSE,
        VOLUME,
        -- 日次リターン
        SAFE_DIVIDE(CLOSE - LAG(CLOSE,1) OVER w, LAG(CLOSE,1) OVER w) AS ret_1d,
        -- N日リターン
        SAFE_DIVIDE(CLOSE - LAG(CLOSE,5)  OVER w, LAG(CLOSE,5)  OVER w) AS ror_5,
        SAFE_DIVIDE(CLOSE - LAG(CLOSE,10) OVER w, LAG(CLOSE,10) OVER w) AS ror_10,
        SAFE_DIVIDE(CLOSE - LAG(CLOSE,20) OVER w, LAG(CLOSE,20) OVER w) AS ror_20,
        SAFE_DIVIDE(CLOSE - LAG(CLOSE,40) OVER w, LAG(CLOSE,40) OVER w) AS ror_40,
        -- 出来高移動平均
        AVG(VOLUME) OVER (w ROWS BETWEEN 4  PRECEDING AND CURRENT ROW) AS vol_5,
        AVG(VOLUME) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS vol_20,
        AVG(VOLUME) OVER (w ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS vol_60,
        -- True Range
        GREATEST(
            CAST(HIGH - LOW AS FLOAT64),
            ABS(CAST(HIGH AS FLOAT64) - LAG(CLOSE,1) OVER w),
            ABS(CAST(LOW  AS FLOAT64) - LAG(CLOSE,1) OVER w)
        ) AS tr,
        -- ギャップ幅（始値と前日終値の乖離） / 前日終値
        SAFE_DIVIDE(
            ABS(CAST(OPEN AS FLOAT64) - LAG(CLOSE,1) OVER w),
            LAG(CLOSE,1) OVER w
        ) AS gap_range,
        -- 日中変動（高安値幅） / 前日終値
        SAFE_DIVIDE(
            CAST(HIGH AS FLOAT64) - CAST(LOW AS FLOAT64),
            LAG(CLOSE,1) OVER w
        ) AS day_range,
        -- ヒゲ幅（高安値幅 - 実体幅）/ 前日終値  ← 反転シグナル
        SAFE_DIVIDE(
            (CAST(HIGH AS FLOAT64) - CAST(LOW AS FLOAT64))
            - ABS(CAST(CLOSE AS FLOAT64) - CAST(OPEN AS FLOAT64)),
            LAG(CLOSE,1) OVER w
        ) AS hig_range,
        -- High-Low Range
        MAX(HIGH) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) -
        MIN(LOW)  OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS hl_20,
        MAX(HIGH) OVER (w ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) -
        MIN(LOW)  OVER (w ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS hl_60,
        -- 翌20営業日のラベル（FORWARDウィンドウ）
        MAX(CLOSE) OVER (PARTITION BY TICKER ORDER BY YEARDATE
                         ROWS BETWEEN 1 FOLLOWING AND 20 FOLLOWING) AS fwd_high,
        MIN(CLOSE) OVER (PARTITION BY TICKER ORDER BY YEARDATE
                         ROWS BETWEEN 1 FOLLOWING AND 20 FOLLOWING) AS fwd_low
    FROM `gmailpj-357912.STOCK.STOCK_PRICE`
    WHERE TICKER IN UNNEST(@tickers)
      AND YEARDATE BETWEEN @price_start AND @test_end
      AND CLOSE > 0
      AND VOLUME > 0
    WINDOW w AS (PARTITION BY TICKER ORDER BY YEARDATE)
),
feat AS (
    SELECT
        YEARDATE,
        TICKER,
        CLOSE,
        -- リターン
        ret_1d  AS ror_1,
        ror_5,
        ror_10,
        ror_20,
        ror_40,
        -- 出来高比率（当日/20日平均）
        SAFE_DIVIDE(VOLUME, vol_20) AS d_vol,
        -- ATR（20日平均TR）
        AVG(tr) OVER (PARTITION BY TICKER ORDER BY YEARDATE
                      ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS atr_20,
        -- ボラティリティ（20日・60日）
        STDDEV(ret_1d) OVER (PARTITION BY TICKER ORDER BY YEARDATE
                             ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS vola_20,
        STDDEV(ret_1d) OVER (PARTITION BY TICKER ORDER BY YEARDATE
                             ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS vola_60,
        -- High-Low / Close 正規化
        SAFE_DIVIDE(hl_20, CLOSE) AS hl_20_ratio,
        SAFE_DIVIDE(hl_60, CLOSE) AS hl_60_ratio,
        -- ギャップ移動平均（5/20日）
        gap_range,
        AVG(gap_range) OVER (PARTITION BY TICKER ORDER BY YEARDATE
                             ROWS BETWEEN 4  PRECEDING AND CURRENT ROW) AS g_atr_5,
        AVG(gap_range) OVER (PARTITION BY TICKER ORDER BY YEARDATE
                             ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS g_atr_20,
        -- 日中変動移動平均（5/20日）
        day_range,
        AVG(day_range) OVER (PARTITION BY TICKER ORDER BY YEARDATE
                             ROWS BETWEEN 4  PRECEDING AND CURRENT ROW) AS d_atr_5,
        AVG(day_range) OVER (PARTITION BY TICKER ORDER BY YEARDATE
                             ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS d_atr_20,
        -- ヒゲ幅移動平均（5/20日）
        hig_range,
        AVG(hig_range) OVER (PARTITION BY TICKER ORDER BY YEARDATE
                             ROWS BETWEEN 4  PRECEDING AND CURRENT ROW) AS h_atr_5,
        AVG(hig_range) OVER (PARTITION BY TICKER ORDER BY YEARDATE
                             ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS h_atr_20,
        -- 市場インパクト（MI = TR / (出来高×終値)）移動平均（5/20日）
        SAFE_DIVIDE(tr, CAST(VOLUME AS FLOAT64) * CAST(CLOSE AS FLOAT64)) AS mi_raw,
        AVG(SAFE_DIVIDE(tr, CAST(VOLUME AS FLOAT64) * CAST(CLOSE AS FLOAT64)))
            OVER (PARTITION BY TICKER ORDER BY YEARDATE
                  ROWS BETWEEN 4  PRECEDING AND CURRENT ROW) AS mi_5,
        AVG(SAFE_DIVIDE(tr, CAST(VOLUME AS FLOAT64) * CAST(CLOSE AS FLOAT64)))
            OVER (PARTITION BY TICKER ORDER BY YEARDATE
                  ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS mi_20,
        -- ラベル
        SAFE_DIVIDE(fwd_high - CLOSE, CLOSE) AS label_high_20,
        SAFE_DIVIDE(fwd_low  - CLOSE, CLOSE) AS label_low_20
    FROM base
)
SELECT * FROM feat
WHERE YEARDATE >= @feat_start
  AND label_high_20 IS NOT NULL
  AND label_low_20  IS NOT NULL
ORDER BY TICKER, YEARDATE
"""

def fetch_tech_features(bq, tickers: list) -> pd.DataFrame:
    from google.cloud.bigquery import ArrayQueryParameter, ScalarQueryParameter, QueryJobConfig
    log(f"テクニカル特徴量取得中... (銘柄数={len(tickers)})")
    BATCH = 500
    frames = []
    for i in range(0, len(tickers), BATCH):
        batch = tickers[i:i+BATCH]
        cfg = QueryJobConfig(query_parameters=[
            ArrayQueryParameter("tickers",      "STRING", batch),
            ScalarQueryParameter("price_start", "STRING", PRICE_START),
            ScalarQueryParameter("test_end",    "STRING", TEST_END),
            ScalarQueryParameter("feat_start",  "STRING", "2020-01-01"),
        ])
        df = bq.query(TECH_SQL, job_config=cfg).to_dataframe()
        frames.append(df)
        log(f"  バッチ {i//BATCH+1}/{(len(tickers)-1)//BATCH+1}: {len(df):,} 行")
    result = pd.concat(frames, ignore_index=True)
    log(f"  合計 {len(result):,} 行")
    return result

# ==========================================
# Step 3: ファンダメンタルズ特徴量（fin_summary）
# ==========================================
FIN_SQL = """
WITH fin AS (
    SELECT
        LOCAL_CODE                                  AS TICKER,
        DISCLOSED_DATE,
        TYPE_OF_CURRENT_PERIOD,
        -- 収益性
        SAFE_DIVIDE(OPERATING_PROFIT, NET_SALES)    AS op_margin,
        SAFE_DIVIDE(PROFIT, EQUITY)                 AS roe,
        SAFE_DIVIDE(PROFIT, TOTAL_ASSETS)           AS roa,
        SAFE_DIVIDE(EQUITY, TOTAL_ASSETS)           AS equity_ratio,
        EARNINGS_PER_SHARE                          AS eps,
        -- 前期比（実績）
        SAFE_DIVIDE(
            NET_SALES - LAG(NET_SALES,1) OVER (PARTITION BY LOCAL_CODE ORDER BY DISCLOSED_DATE),
            ABS(LAG(NET_SALES,1) OVER (PARTITION BY LOCAL_CODE ORDER BY DISCLOSED_DATE))
        )                                           AS sales_chg,
        SAFE_DIVIDE(
            PROFIT - LAG(PROFIT,1) OVER (PARTITION BY LOCAL_CODE ORDER BY DISCLOSED_DATE),
            ABS(LAG(PROFIT,1) OVER (PARTITION BY LOCAL_CODE ORDER BY DISCLOSED_DATE))
        )                                           AS profit_chg,
        -- マッチング特徴量：実績 - 前期末の会社予想（決算サプライズ）
        SAFE_DIVIDE(
            NET_SALES
            - LAG(FORECAST_NET_SALES,1) OVER (PARTITION BY LOCAL_CODE ORDER BY DISCLOSED_DATE),
            ABS(LAG(FORECAST_NET_SALES,1) OVER (PARTITION BY LOCAL_CODE ORDER BY DISCLOSED_DATE))
        )                                           AS m_sales,
        SAFE_DIVIDE(
            OPERATING_PROFIT
            - LAG(FORECAST_OPERATING_PROFIT,1) OVER (PARTITION BY LOCAL_CODE ORDER BY DISCLOSED_DATE),
            ABS(LAG(FORECAST_OPERATING_PROFIT,1) OVER (PARTITION BY LOCAL_CODE ORDER BY DISCLOSED_DATE))
        )                                           AS m_ope_income,
        SAFE_DIVIDE(
            PROFIT
            - LAG(FORECAST_PROFIT,1) OVER (PARTITION BY LOCAL_CODE ORDER BY DISCLOSED_DATE),
            ABS(LAG(FORECAST_PROFIT,1) OVER (PARTITION BY LOCAL_CODE ORDER BY DISCLOSED_DATE))
        )                                           AS m_net_income
    FROM `gmailpj-357912.STOCK.fin_summary`
    WHERE LOCAL_CODE IN UNNEST(@tickers)
      AND DISCLOSED_DATE BETWEEN @price_start AND @test_end
      AND NET_SALES IS NOT NULL
)
SELECT * FROM fin
ORDER BY TICKER, DISCLOSED_DATE
"""

def fetch_fin_features(bq, tickers: list) -> pd.DataFrame:
    from google.cloud.bigquery import ArrayQueryParameter, ScalarQueryParameter, QueryJobConfig
    log("ファンダメンタルズ特徴量取得中...")
    BATCH = 500
    frames = []
    for i in range(0, len(tickers), BATCH):
        batch = tickers[i:i+BATCH]
        cfg = QueryJobConfig(query_parameters=[
            ArrayQueryParameter("tickers",      "STRING", batch),
            ScalarQueryParameter("price_start", "STRING", PRICE_START),
            ScalarQueryParameter("test_end",    "STRING", TEST_END),
        ])
        df = bq.query(FIN_SQL, job_config=cfg).to_dataframe()
        frames.append(df)
    result = pd.concat(frames, ignore_index=True)
    log(f"  {len(result):,} 行")
    return result

def merge_fin(price_df: pd.DataFrame, fin_df: pd.DataFrame) -> pd.DataFrame:
    """各日付に対して最新の決算発表を as-of join"""
    price_df = price_df.copy()
    fin_df   = fin_df.copy()
    price_df["YEARDATE"]      = pd.to_datetime(price_df["YEARDATE"])
    fin_df["DISCLOSED_DATE"]  = pd.to_datetime(fin_df["DISCLOSED_DATE"])

    fin_cols = ["TICKER","DISCLOSED_DATE",
                "op_margin","roe","eps","profit_chg","sales_chg",
                "roa","equity_ratio","m_sales","m_ope_income","m_net_income"]
    fin_df   = fin_df[fin_cols].dropna(subset=["TICKER"])

    merged_parts = []
    for ticker, gp in price_df.groupby("TICKER"):
        fd = fin_df[fin_df["TICKER"] == ticker].sort_values("DISCLOSED_DATE")
        if fd.empty:
            merged_parts.append(gp)
            continue
        gp = gp.sort_values("YEARDATE")
        gp = pd.merge_asof(gp, fd.drop(columns="TICKER"),
                           left_on="YEARDATE", right_on="DISCLOSED_DATE",
                           direction="backward")
        merged_parts.append(gp)

    return pd.concat(merged_parts, ignore_index=True)

# ==========================================
# Step 4: 学習・推論
# ==========================================
FEAT_COLS = [
    # テクニカル（既存）
    "ror_1","ror_5","ror_10","ror_20","ror_40",
    "d_vol","atr_20","vola_20","vola_60",
    "hl_20_ratio","hl_60_ratio",
    # テクニカル（新規）: ギャップ・日中変動・ヒゲ・市場インパクト
    "gap_range","g_atr_5","g_atr_20",
    "day_range","d_atr_5","d_atr_20",
    "hig_range","h_atr_5","h_atr_20",
    "mi_5","mi_20",
    # ファンダ（既存）
    "op_margin","roe","eps","profit_chg","sales_chg",
    # ファンダ（新規）
    "roa","equity_ratio",
    # マッチング特徴量（決算サプライズ）← アイデア①の核心
    "m_sales","m_ope_income","m_net_income",
]

def train_evaluate(df: pd.DataFrame):
    try:
        import xgboost as xgb
    except ImportError:
        log("xgboost が未インストール。pip install xgboost を実行してください。")
        return None, None

    from scipy.stats import pearsonr

    df["YEARDATE"] = pd.to_datetime(df["YEARDATE"])

    # 外れ値除去（コピーせずフィルタ）
    for col in ["label_high_20","label_low_20"]:
        lo, hi = df[col].quantile(0.005), df[col].quantile(0.995)
        df = df[(df[col] >= lo) & (df[col] <= hi)]

    available = [c for c in FEAT_COLS if c in df.columns]
    log(f"使用特徴量 ({len(available)}): {available}")

    # float32に変換してメモリ削減（float64の半分）
    for c in available + ["label_high_20","label_low_20"]:
        if c in df.columns:
            df[c] = df[c].astype("float32")

    train = df[df["YEARDATE"] <= TRAIN_END].dropna(subset=available+["label_high_20","label_low_20"])
    test  = df[df["YEARDATE"] >= TEST_START].dropna(subset=available+["label_high_20","label_low_20"])
    log(f"訓練: {len(train):,} 行 | テスト: {len(test):,} 行")

    X_tr = train[available].values
    X_te = test[available].values

    models, preds = {}, {}
    for target in ["label_high_20","label_low_20"]:
        y_tr = train[target].values
        y_te = test[target].values

        model = xgb.XGBRegressor(
            max_depth=5, learning_rate=0.05, n_estimators=500,
            colsample_bytree=0.5, subsample=0.5,
            tree_method="hist",   # メモリ効率の高いヒストグラム法
            n_jobs=-1, random_state=0, verbosity=0
        )
        model.fit(X_tr, y_tr,
                  eval_set=[(X_te, y_te)],
                  verbose=False)
        pred = model.predict(X_te)

        r, p = pearsonr(y_te, pred)
        dir_acc = np.mean(np.sign(y_te) == np.sign(pred))
        log(f"  [{target}] Pearson R={r:.3f} (p={p:.2e}), 方向正解率={dir_acc:.1%}")

        models[target] = model
        preds[target]  = pred

    test = test.copy()
    test["pred_high_20"] = preds["label_high_20"]
    test["pred_low_20"]  = preds["label_low_20"]
    return models, test

# ==========================================
# Step 5: 可視化
# ==========================================
def visualize(models, test_df: pd.DataFrame):
    import matplotlib.dates as mdates

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    for ax, (target, pred_col) in zip(axes[0], [
        ("label_high_20","pred_high_20"),
        ("label_low_20", "pred_low_20"),
    ]):
        y, p = test_df[target], test_df[pred_col]
        ax.scatter(p, y, alpha=0.05, s=2)
        lim = max(abs(p.quantile(0.01)), abs(p.quantile(0.99))) * 1.2
        ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim)
        ax.axhline(0, color="gray", lw=0.5); ax.axvline(0, color="gray", lw=0.5)
        ax.set_xlabel("予測値"); ax.set_ylabel("実際値")
        ax.set_title(target.replace("label_",""))

    # スコア分位別CAR
    for ax, (target, pred_col) in zip(axes[1], [
        ("label_high_20","pred_high_20"),
        ("label_low_20", "pred_low_20"),
    ]):
        test_df = test_df.copy()
        test_df["q"] = pd.qcut(test_df[pred_col], 5, labels=False)
        means = test_df.groupby("q")[target].mean()
        ax.bar(means.index, means.values * 100)
        ax.set_xlabel("予測スコア分位（0=低 4=高）")
        ax.set_ylabel("平均実績 (%)")
        ax.set_title(f"{target} 分位別平均実績")
        ax.axhline(0, color="red", lw=0.5)

    fig.suptitle("UKI予測モデル v2 テスト結果", fontsize=14)
    fig.tight_layout()
    outpath = os.path.join(OUT_DIR, "uki_result_v2.png")
    fig.savefig(outpath, dpi=150)
    log(f"グラフ保存: {outpath}")
    subprocess.Popen(["explorer", outpath])

    # 特徴量重要度
    available_feat = [c for c in FEAT_COLS if c in test_df.columns]
    fig2, axes2 = plt.subplots(1, 2, figsize=(16, 6))
    for ax, (name, model) in zip(axes2, models.items()):
        imp = model.feature_importances_
        feat_names = available_feat[:len(imp)]
        idx = np.argsort(imp)[::-1]
        ax.bar(range(len(imp)), imp[idx])
        ax.set_xticks(range(len(imp)))
        ax.set_xticklabels([feat_names[i] for i in idx], rotation=45, ha="right", fontsize=8)
        ax.set_title(f"特徴量重要度: {name}")
    fig2.tight_layout()
    outpath2 = os.path.join(OUT_DIR, "uki_importance_v2.png")
    fig2.savefig(outpath2, dpi=150)
    log(f"重要度グラフ保存: {outpath2}")
    subprocess.Popen(["explorer", outpath2])

# ==========================================
# メイン
# ==========================================
if __name__ == "__main__":
    log("=== UKI予測モデル v2 開始 (特徴量拡張版) ===")
    bq = get_bq()

    # キャッシュチェック
    cache_tech = os.path.join(OUT_DIR, "tech_features_v2.parquet")
    cache_fin  = os.path.join(OUT_DIR, "fin_features_v2.parquet")

    if os.path.exists(cache_tech):
        log("キャッシュからテクニカル特徴量を読み込み")
        tech_df = pd.read_parquet(cache_tech)
    else:
        tickers = fetch_universe(bq)
        tech_df = fetch_tech_features(bq, tickers)
        tech_df.to_parquet(cache_tech, index=False)
        log(f"テクニカル特徴量をキャッシュに保存: {cache_tech}")

    if os.path.exists(cache_fin):
        log("キャッシュからファンダメンタルズ特徴量を読み込み")
        fin_df = pd.read_parquet(cache_fin)
    else:
        tickers = tech_df["TICKER"].unique().tolist()
        fin_df = fetch_fin_features(bq, tickers)
        fin_df.to_parquet(cache_fin, index=False)
        log(f"ファンダメンタルズ特徴量をキャッシュに保存: {cache_fin}")

    # マージ前にfloat32化・不要列削除でメモリ削減
    tech_keep = ["YEARDATE","TICKER","CLOSE","label_high_20","label_low_20"] + \
                [c for c in FEAT_COLS if c in tech_df.columns]
    tech_df = tech_df[[c for c in tech_keep if c in tech_df.columns]]
    for c in tech_df.select_dtypes("float64").columns:
        tech_df[c] = tech_df[c].astype("float32")

    fin_keep = ["TICKER","DISCLOSED_DATE"] + [c for c in FEAT_COLS if c in fin_df.columns]
    fin_df = fin_df[[c for c in fin_keep if c in fin_df.columns]]
    for c in fin_df.select_dtypes("float64").columns:
        fin_df[c] = fin_df[c].astype("float32")

    log("特徴量マージ中...")
    merged = merge_fin(tech_df, fin_df)
    log(f"マージ後: {len(merged):,} 行")

    # 不要列を削除してメモリ削減
    keep_cols = ["YEARDATE","TICKER","CLOSE","label_high_20","label_low_20"] + FEAT_COLS
    keep_cols = [c for c in keep_cols if c in merged.columns]
    merged = merged[keep_cols]

    models, result_df = train_evaluate(merged)

    if result_df is not None:
        out_csv = os.path.join(OUT_DIR, "uki_test_result_v2.csv")
        result_df.to_csv(out_csv, index=False, encoding="utf-8")
        log(f"結果CSV保存: {out_csv}")
        visualize(models, result_df)

    log("=== 完了 ===")

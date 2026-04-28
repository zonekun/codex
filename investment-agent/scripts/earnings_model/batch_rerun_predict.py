"""決算反応モデル predict/answer のバッチ再実行（BQ共通化版）.

Usage:
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
        scripts/earnings_model/batch_rerun_predict.py

- 2026-04-01 〜 2026-04-13 の営業日 9 日を一括処理
- BQクエリは各テーブル1回のみ、日付ごとの絞り込みは pandas 側で実施
- 04-14 の prediction/actual は読まない・書かない（正プログラム版を保持）
- accuracy_summary.json は全日再集計（04-14含む）
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── SSL 迂回（ローカル環境）──
import urllib3
import requests as _req
from requests.adapters import HTTPAdapter as _HA

urllib3.disable_warnings()


class _NoVerify(_HA):
    def send(self, req, **kw):  # type: ignore[no-untyped-def]
        kw["verify"] = False
        return super().send(req, **kw)


_orig_init = _req.Session.__init__


def _patched_init(self, *a, **kw):  # type: ignore[no-untyped-def]
    _orig_init(self, *a, **kw)
    self.mount("https://", _NoVerify())
    self.verify = False


_req.Session.__init__ = _patched_init  # type: ignore[assignment]

PROJECT_ROOT = Path(r"C:\gdrive\claude\investment-agent")
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from google.cloud import bigquery, storage
from google.oauth2 import service_account
import jquantsapi

JST = ZoneInfo("Asia/Tokyo")
KEY_FILE = str(PROJECT_ROOT / "keys" / "gcp-service-account.json")
_creds = service_account.Credentials.from_service_account_file(KEY_FILE)
bq = bigquery.Client(credentials=_creds, project="gmailpj-357912")
gcs = storage.Client(credentials=_creds, project="gmailpj-357912")
_JQ_KEY = os.environ["JQUANTS_API_KEY"]
jq_cli = jquantsapi.ClientV2(api_key=_JQ_KEY)

GCS_BUCKET_NAME = "stock_data_1930932"
GCS_BUCKET = f"gs://{GCS_BUCKET_NAME}"
GCS_PREDICTIONS = f"{GCS_BUCKET}/earnings_model/predictions"
GCS_ACTUALS = f"{GCS_BUCKET}/earnings_model/actuals"
GCS_ACCURACY = f"{GCS_BUCKET}/earnings_model/accuracy"

# 04-14 は対象外（正プログラム版保持）
DATE_PAIRS: list[tuple[str, str]] = [
    ("20260401", "20260402"),
    ("20260402", "20260403"),
    ("20260403", "20260406"),
    ("20260406", "20260407"),
    ("20260407", "20260408"),
    ("20260408", "20260409"),
    ("20260409", "20260410"),
    ("20260410", "20260413"),
    ("20260413", "20260414"),
]

DATE_MIN_PREDICT = "20260401"
DATE_MAX_PREDICT = "20260413"
DATE_MAX_ACTUAL = "20260414"
DATE_PRICE_MIN = "20260325"  # 04-01 の前営業日確保のためバッファ


def hy(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def gcs_save_json(data: dict | list, gcs_path: str) -> None:
    path = gcs_path.replace(f"gs://{GCS_BUCKET_NAME}/", "")
    blob = gcs.bucket(GCS_BUCKET_NAME).blob(path)
    blob.upload_from_string(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        content_type="application/json",
    )
    print(f"  Saved: {gcs_path}")


def gcs_load_json(gcs_path: str) -> dict | list | None:
    path = gcs_path.replace(f"gs://{GCS_BUCKET_NAME}/", "")
    blob = gcs.bucket(GCS_BUCKET_NAME).blob(path)
    try:
        return json.loads(blob.download_as_text())
    except Exception as e:
        print(f"  Failed load {gcs_path}: {e}")
        return None


def gcs_list_blobs(prefix: str) -> list[str]:
    return [b.name for b in gcs.bucket(GCS_BUCKET_NAME).list_blobs(prefix=prefix)]


# ══════════════ STEP A: 共通データ一括取得 ══════════════


def fetch_shared_data() -> dict:
    """BQ 7 本 + GCS 1 件を一括取得."""
    shared: dict = {}

    print("[1/7] STOCK_CODE_LIST ...")
    df_names = bq.query(
        "SELECT TICKER, STOCK_NAME, INDUSTRY_33_CODE, MARKET_CATEGORY "
        "FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`"
    ).to_dataframe()
    shared["name_map"] = dict(zip(df_names["TICKER"], df_names["STOCK_NAME"]))
    shared["sector_map"] = dict(
        zip(df_names["TICKER"], df_names["INDUSTRY_33_CODE"].astype(str))
    )
    shared["market_map"] = dict(
        zip(df_names["TICKER"], df_names["MARKET_CATEGORY"].fillna("").astype(str))
    )
    print(f"  {len(df_names)} 銘柄")

    print(f"[2/7] STOCK_PRICE_JQUANTS ({hy(DATE_PRICE_MIN)}〜{hy(DATE_MAX_ACTUAL)}) ...")
    q_price = f"""SELECT TICKER, DATE AS dt, ADJ_CLOSE
    FROM `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS`
    WHERE DATE BETWEEN '{hy(DATE_PRICE_MIN)}' AND '{hy(DATE_MAX_ACTUAL)}'
      AND IS_PREFERRED = FALSE"""
    df_price = bq.query(q_price).to_dataframe()
    df_price["dt"] = df_price["dt"].astype(str)
    shared["df_price"] = df_price
    print(f"  {len(df_price)} 行")

    # 9日間のPREDICT_DATEをまたいで各 (TICKER,QUARTER,TARGET) の as-of 最新を取るため、
    # DATAAT <= DATE_MAX_PREDICT で直近20件/groupに絞る（13日間に20回以上更新される
    # ケースは考えにくいので全PREDICT_DATEのas-of最新を再現可能）
    print(f"[3/7] CONSENSUS (per-group top 20, DATAAT <= {hy(DATE_MAX_PREDICT)}) ...")
    q_cons = f"""SELECT TICKER, QUARTER, TARGET, DATAAT, CONSENSUS_PROFIT FROM (
      SELECT TICKER, QUARTER, TARGET, DATAAT, PROFIT AS CONSENSUS_PROFIT,
        ROW_NUMBER() OVER (PARTITION BY TICKER, QUARTER, TARGET ORDER BY DATAAT DESC) AS rn
      FROM `gmailpj-357912.STOCK.CONSENSUS`
      WHERE DATAAT <= '{hy(DATE_MAX_PREDICT)}'
        AND TARGET IN ('CURRENT', 'NEXT')
    ) WHERE rn <= 20"""
    df_cons = bq.query(q_cons).to_dataframe()
    df_cons["DATAAT"] = df_cons["DATAAT"].astype(str)
    shared["df_cons"] = df_cons
    print(f"  {len(df_cons)} 行")

    # 前回予想は過去18ヶ月で充分（直近の四半期開示を拾えればよい）
    _prev_from = "2024-10-01"
    print(f"[4/7] fin_summary ({_prev_from}〜{hy(DATE_MAX_PREDICT)}) ...")
    q_prev = f"""SELECT LOCAL_CODE, DISCLOSED_DATE,
      FORECAST_OPERATING_PROFIT, FORECAST_PROFIT,
      FORECAST_DIVIDEND_PER_SHARE_ANNUAL,
      OPERATING_PROFIT,
      TYPE_OF_CURRENT_PERIOD,
      CURRENT_FISCAL_YEAR_START_DATE
    FROM `gmailpj-357912.STOCK.fin_summary`
    WHERE DISCLOSED_DATE >= '{_prev_from}'
      AND DISCLOSED_DATE < '{hy(DATE_MAX_PREDICT)}'"""
    df_prev = bq.query(q_prev).to_dataframe()
    df_prev["DISCLOSED_DATE"] = df_prev["DISCLOSED_DATE"].astype(str)
    df_prev["tk"] = df_prev["LOCAL_CODE"].astype(str).str[:4]
    shared["df_prev"] = df_prev
    print(f"  {len(df_prev)} 行")

    # baseline YoY (FY 履歴中央値) は3-4期分あれば充分。2020-04以降で10年分を確保
    _qoq_from = "2020-04-01"
    print(f"[5/7] v_fin_summary_actual_for_q_on_q (FY_START >= {_qoq_from}) ...")
    q_qoq = f"""SELECT LOCAL_CODE, DISCLOSED_DATE, QUARTER,
      CURRENT_FISCAL_YEAR_START_DATE, OPERATING_PROFIT, PROFIT
    FROM `gmailpj-357912.STOCK.v_fin_summary_actual_for_q_on_q`
    WHERE CURRENT_FISCAL_YEAR_START_DATE >= '{_qoq_from}'
      AND DISCLOSED_DATE <= '{hy(DATE_MAX_PREDICT)}'"""
    df_qoq = bq.query(q_qoq).to_dataframe()
    df_qoq["tk"] = df_qoq["LOCAL_CODE"].astype(str).str[:4]
    df_qoq["DISCLOSED_DATE"] = df_qoq["DISCLOSED_DATE"].astype(str)
    df_qoq["CURRENT_FISCAL_YEAR_START_DATE"] = df_qoq["CURRENT_FISCAL_YEAR_START_DATE"].astype(str)
    shared["df_qoq"] = df_qoq
    print(f"  {len(df_qoq)} 行")

    print(f"[6/7] TDNET_DOCUMENTS_ENHANCED ({hy(DATE_MIN_PREDICT)}〜{hy(DATE_MAX_PREDICT)}) ...")
    q_tdnet = f"""SELECT DISTINCT TICKER, SUBMISSION_DATE, DOC_TITLE
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE SUBMISSION_DATE BETWEEN '{hy(DATE_MIN_PREDICT)}' AND '{hy(DATE_MAX_PREDICT)}'
      AND (DOC_TITLE LIKE '%記念配当%' OR DOC_TITLE LIKE '%特別配当%'
           OR DOC_TITLE LIKE '%自己株式の取得%' OR DOC_TITLE LIKE '%自社株買い%')"""
    df_tdnet = bq.query(q_tdnet).to_dataframe()
    df_tdnet["SUBMISSION_DATE"] = df_tdnet["SUBMISSION_DATE"].astype(str)
    shared["df_tdnet"] = df_tdnet
    print(f"  {len(df_tdnet)} 行")

    print(f"[7/7] INDEX_PRICE (TOPIX, {hy(DATE_PRICE_MIN)}〜{hy(DATE_MAX_PREDICT)}) ...")
    q_topix = f"""SELECT DATE AS dt, CLOSE
    FROM `gmailpj-357912.STOCK.INDEX_PRICE`
    WHERE INDEX_CODE = '0000'
      AND DATE BETWEEN '{hy(DATE_PRICE_MIN)}' AND '{hy(DATE_MAX_PREDICT)}'
    ORDER BY DATE"""
    df_topix = bq.query(q_topix).to_dataframe()
    df_topix["dt"] = df_topix["dt"].astype(str)
    df_topix = df_topix.sort_values("dt").reset_index(drop=True)
    df_topix["topix_ret"] = df_topix["CLOSE"].pct_change()
    shared["df_topix"] = df_topix
    print(f"  {len(df_topix)} 行")

    print("[GCS] beta_20d.csv ...")
    _beta_csv = (
        gcs.bucket(GCS_BUCKET_NAME).blob("earnings_model/beta_20d.csv").download_as_text()
    )
    df_beta = pd.read_csv(io.StringIO(_beta_csv))
    shared["beta_map"] = dict(zip(df_beta["TICKER"], df_beta["beta_20d"]))
    print(f"  β: {len(df_beta)} 銘柄")

    return shared


# ══════════════ STEP B: per-date 特徴量計算 ══════════════


def compute_features(predict_date: str, shared: dict) -> pd.DataFrame | None:
    """指定日の特徴量データフレームを構築."""
    ph = hy(predict_date)

    print(f"  J-Quants fin_summary {predict_date} ...")
    df_fin = jq_cli.get_fin_summary(date_yyyymmdd=predict_date)
    if df_fin.empty:
        return None

    df_fin = df_fin[df_fin["DocType"].str.contains("FinancialStatements", na=False)].copy()

    # 訂正報告除外
    cutoff = pd.Timestamp(ph) - pd.DateOffset(months=9)
    df_fin["_cp"] = pd.to_datetime(df_fin["CurPerEn"], errors="coerce")
    n_before = len(df_fin)
    df_fin = df_fin[df_fin["_cp"] >= cutoff].copy()
    df_fin.drop(columns=["_cp"], inplace=True)
    if len(df_fin) < n_before:
        print(f"  訂正報告除外: {n_before - len(df_fin)} 件")

    close_time = "15:30:00" if predict_date >= "20241105" else "15:00:00"
    df_fin["is_intraday"] = df_fin["DiscTime"] < close_time
    df_fin["ticker"] = df_fin["Code"].astype(str).str[:4]

    for col in [
        "OP", "OdP", "NP", "FOP", "FOdP", "FNP",
        "NxFOP", "NxFOdP", "NxFNp", "FDivAnn", "ForEPS",
    ]:
        if col in df_fin.columns:
            df_fin[col] = pd.to_numeric(df_fin[col], errors="coerce")

    tickers: set[str] = set(df_fin["ticker"].unique())

    # ── 株価マップ（当日・前日） ──
    dfp = shared["df_price"]
    today_p = dfp[dfp["dt"] == ph]
    price_map: dict[str, float] = dict(zip(today_p["TICKER"], today_p["ADJ_CLOSE"]))
    prev_p = (
        dfp[dfp["dt"] < ph]
        .sort_values(["TICKER", "dt"], ascending=[True, False])
        .drop_duplicates("TICKER", keep="first")
    )
    prev_price_map: dict[str, float] = dict(zip(prev_p["TICKER"], prev_p["ADJ_CLOSE"]))

    # ── コンセンサス as-of ──
    dfc = shared["df_cons"]
    dfc_asof = (
        dfc[dfc["DATAAT"] <= ph]
        .sort_values("DATAAT", ascending=False)
        .drop_duplicates(["TICKER", "QUARTER", "TARGET"])
    )
    cons_map: dict[tuple[str, str, str], float] = {
        (r["TICKER"], r["QUARTER"], r["TARGET"]): r["CONSENSUS_PROFIT"]
        for _, r in dfc_asof.iterrows()
    }

    # ── 前回予想（DISCLOSED_DATE < predict_date, 銘柄ごと最新） ──
    dfpv = shared["df_prev"]
    dfpv_before = dfpv[dfpv["DISCLOSED_DATE"] < ph].copy()
    dfpv_asof = (
        dfpv_before
        .sort_values("DISCLOSED_DATE", ascending=False)
        .drop_duplicates("tk", keep="first")
    )
    prev_forecast_map: dict[str, dict] = {
        r["tk"]: {
            "FOP": r["FORECAST_OPERATING_PROFIT"],
            "FNP": r["FORECAST_PROFIT"],
        }
        for _, r in dfpv_asof.iterrows()
    }
    # 配当予想は NOT NULL > 0 の最新
    dfpd = dfpv[
        (dfpv["DISCLOSED_DATE"] < ph)
        & dfpv["FORECAST_DIVIDEND_PER_SHARE_ANNUAL"].notna()
        & (dfpv["FORECAST_DIVIDEND_PER_SHARE_ANNUAL"] > 0)
    ]
    dfpd = dfpd.sort_values("DISCLOSED_DATE", ascending=False).drop_duplicates("tk", keep="first")
    prev_div_map: dict[str, float] = dict(
        zip(dfpd["tk"], dfpd["FORECAST_DIVIDEND_PER_SHARE_ANNUAL"].astype(float))
    )

    # ── QoQ / YoY マップ ──
    dfq = shared["df_qoq"]
    # BQの当日行が存在する過去再実行でも、当期OPはJ-Quantsから算出し、
    # BQは予測日時点で既に開示済みの前年同期・前Q累積だけに使う。
    dfq_asof = dfq[dfq["DISCLOSED_DATE"] < ph].copy()
    Q_MAP = {"1Q": "1Q", "2Q": "2Q", "3Q": "3Q", "FY": "4Q"}
    CUM_PREV_Q = {"2Q": "1Q", "3Q": "2Q", "FY": "3Q"}
    PREV_Q_MAP = {"2Q": "1Q", "3Q": "2Q", "4Q": "3Q"}

    def date_key(value: object) -> str:
        ts = pd.to_datetime(value, errors="coerce")
        return "" if pd.isna(ts) else ts.strftime("%Y-%m-%d")

    prev_cum_op_map: dict[str, float] = {}
    for _, row in df_fin.iterrows():
        tk = row["ticker"]
        prev_q_type = CUM_PREV_Q.get(row.get("CurPerType", ""))
        cur_fy_start = date_key(row.get("CurFYStartDt", row.get("CurFYSt")))
        if not prev_q_type or not cur_fy_start:
            continue
        matched = dfpv_before[
            (dfpv_before["tk"] == tk)
            & (dfpv_before["TYPE_OF_CURRENT_PERIOD"] == prev_q_type)
            & (
                dfpv_before["CURRENT_FISCAL_YEAR_START_DATE"].astype(str)
                == cur_fy_start
            )
        ].sort_values("DISCLOSED_DATE", ascending=False)
        if not matched.empty and pd.notna(matched.iloc[0]["OPERATING_PROFIT"]):
            prev_cum_op_map[tk] = float(matched.iloc[0]["OPERATING_PROFIT"])

    qoq_map: dict[str, dict] = {}
    for tk in tickers:
        row0 = df_fin[df_fin["ticker"] == tk].iloc[0]
        cur_per = row0.get("CurPerType", "")
        q_label = Q_MAP.get(cur_per, "")
        cur_fy_start = date_key(row0.get("CurFYStartDt", row0.get("CurFYSt")))
        jq_op_cum = row0.get("OP")
        cur_standalone_op: float | None = None
        if pd.notna(jq_op_cum):
            if cur_per == "1Q":
                cur_standalone_op = float(jq_op_cum)
            else:
                prev_cum = prev_cum_op_map.get(tk)
                cur_standalone_op = (
                    float(jq_op_cum) - prev_cum
                    if prev_cum is not None
                    else float(jq_op_cum)
                )

        tk_qoq = dfq_asof[(dfq_asof["tk"] == tk) & (dfq_asof["QUARTER"] == q_label)]
        if cur_fy_start:
            tk_qoq = tk_qoq[
                tk_qoq["CURRENT_FISCAL_YEAR_START_DATE"].astype(str) < cur_fy_start
            ]
        tk_qoq = tk_qoq.sort_values("CURRENT_FISCAL_YEAR_START_DATE", ascending=False)
        prev_year_op = tk_qoq.iloc[0]["OPERATING_PROFIT"] if len(tk_qoq) >= 1 else None
        yoy_op: float | None = None
        if (
            cur_standalone_op is not None
            and prev_year_op is not None
            and pd.notna(prev_year_op)
            and prev_year_op != 0
        ):
            yoy_op = float((cur_standalone_op - float(prev_year_op)) / abs(float(prev_year_op)))

        # QoQ 単独四半期比較: 同一FYの直前Q（1Qは前Qなし）
        qoq_op: float | None = None
        prev_q_label = PREV_Q_MAP.get(q_label)
        if prev_q_label and cur_standalone_op is not None and cur_fy_start:
            prev_q_row = dfq_asof[
                (dfq_asof["tk"] == tk)
                & (dfq_asof["QUARTER"] == prev_q_label)
                & (dfq_asof["CURRENT_FISCAL_YEAR_START_DATE"].astype(str) == cur_fy_start)
            ].sort_values("DISCLOSED_DATE", ascending=False)
            if not prev_q_row.empty:
                prev_q_op = prev_q_row.iloc[0]["OPERATING_PROFIT"]
                if pd.notna(prev_q_op) and prev_q_op != 0:
                    qoq_op = float((cur_standalone_op - float(prev_q_op)) / abs(float(prev_q_op)))
        qoq_map[tk] = {"yoy_op": yoy_op, "qoq_op": qoq_op}

    # ── baseline YoY OP（FY履歴中央値） ──
    dff = dfq_asof[dfq_asof["QUARTER"] == "4Q"]
    baseline_yoy_op_map: dict[str, float] = {}
    for tk in tickers:
        tk_fy = dff[dff["tk"] == tk].sort_values("CURRENT_FISCAL_YEAR_START_DATE", ascending=False)
        if len(tk_fy) < 3:
            continue
        ops = tk_fy["OPERATING_PROFIT"].tolist()
        yoy_list: list[float] = []
        for j in range(len(ops) - 1):
            c, p = ops[j], ops[j + 1]
            if pd.notna(c) and pd.notna(p) and p != 0:
                yoy_list.append(float((c - p) / abs(p)))
        if yoy_list:
            baseline_yoy_op_map[tk] = float(pd.Series(yoy_list).median())

    # ── TDnet イベント ──
    dftd = shared["df_tdnet"]
    dftd_day = dftd[dftd["SUBMISSION_DATE"] == ph]
    BUYBACK_KW = ["自己株式の取得", "自社株買い"]
    SPECIAL_DIV_KW = ["記念配当", "特別配当"]
    special_div_tickers: set[str] = set()
    buyback_tickers: dict[str, float] = {}
    for _, r in dftd_day.iterrows():
        tk, title = r["TICKER"], r["DOC_TITLE"]
        if tk not in tickers:
            continue
        if any(kw in title for kw in SPECIAL_DIV_KW):
            special_div_tickers.add(tk)
        if any(kw in title for kw in BUYBACK_KW):
            buyback_tickers[tk] = 0.0

    # ── TOPIX 当日リターン ──
    dft = shared["df_topix"]
    topix_row = dft[dft["dt"] == ph]
    topix_ret = (
        float(topix_row["topix_ret"].iloc[0])
        if len(topix_row) and pd.notna(topix_row["topix_ret"].iloc[0])
        else 0.0
    )

    beta_map = shared["beta_map"]
    name_map = shared["name_map"]
    sector_map = shared["sector_map"]
    market_map = shared["market_map"]

    # ── 特徴量構築 ──
    results: list[dict] = []
    for _, row in df_fin.iterrows():
        tk = row["ticker"]
        cur_per: str = row.get("CurPerType", "")
        op = row.get("OP")
        fop = row.get("FOP")
        odp = row.get("OdP")
        nx_fop = row.get("NxFOP")
        nx_fodp = row.get("NxFOdP")

        progress_op: float | None = None
        if pd.notna(op) and pd.notna(fop) and fop != 0 and cur_per in ("1Q", "2Q", "3Q"):
            progress_op = float(op / fop)

        prev_fop = prev_forecast_map.get(tk, {}).get("FOP")
        has_guidance_revision = False
        guidance_op_change: float | None = None
        if pd.notna(fop) and pd.notna(prev_fop) and prev_fop != 0:
            change = (fop - prev_fop) / abs(prev_fop)
            if abs(change) > 0.001:
                has_guidance_revision = True
                guidance_op_change = float(change)

        yoy_op = qoq_map.get(tk, {}).get("yoy_op")

        consensus_deviation: float | None = None
        f4_source = ""
        if cur_per == "FY":
            cons_next = cons_map.get((tk, "FY", "NEXT"))
            if pd.notna(nx_fodp) and cons_next is not None and cons_next != 0:
                cn = cons_next * 1_000_000
                consensus_deviation = float((nx_fodp - cn) / abs(cn))
                f4_source = "FY_NEXT"
            else:
                cons_cur = cons_map.get((tk, "FY", "CURRENT"))
                if pd.notna(odp) and cons_cur is not None and cons_cur != 0:
                    cc = cons_cur * 1_000_000
                    consensus_deviation = float((odp - cc) / abs(cc))
                    f4_source = "FY_CURRENT_fallback"
        else:
            cons_cur = cons_map.get((tk, cur_per, "CURRENT"))
            if pd.notna(odp) and cons_cur is not None and cons_cur != 0:
                cc = cons_cur * 1_000_000
                consensus_deviation = float((odp - cc) / abs(cc))
                f4_source = "Q_CURRENT"

        next_year_op_change: float | None = None
        if cur_per == "FY" and pd.notna(nx_fop) and pd.notna(op) and op != 0:
            next_year_op_change = float((nx_fop - op) / abs(op))

        selloff_risk = (
            cur_per == "3Q"
            and progress_op is not None
            and progress_op > 0.90
            and not has_guidance_revision
        )

        beta = beta_map.get(tk)
        theme_boost = beta is not None and beta > 1.0 and topix_ret > 0

        div_change: float | None = None
        if pd.notna(row.get("FDivAnn")) and tk in prev_div_map and prev_div_map[tk] > 0:
            div_change = float(
                (float(row.get("FDivAnn", 0)) - prev_div_map[tk]) / prev_div_map[tk]
            )

        per: float | None = None
        _close = price_map.get(tk)
        _eps = row.get("ForEPS")
        if _close and pd.notna(_eps) and float(_eps) > 0:
            per = float(_close / float(_eps))

        results.append({
            "ticker": tk,
            "name": name_map.get(tk, ""),
            "industry_33": sector_map.get(tk, ""),
            "market_division": market_map.get(tk, ""),
            "quarter": cur_per,
            "is_intraday": bool(row["is_intraday"]),
            "disc_time": str(row.get("DiscTime", "")),
            "op": op, "fop": fop, "odp": odp,
            "adj_close": price_map.get(tk),
            "prev_close": prev_price_map.get(tk),
            "progress_op": progress_op,
            "has_guidance_revision": has_guidance_revision,
            "guidance_op_change": guidance_op_change,
            "yoy_op": yoy_op,
            "consensus_deviation": consensus_deviation,
            "f4_source": f4_source if f4_source else None,
            "next_year_op_change": next_year_op_change,
            "next_year_disclosed": bool(cur_per == "FY" and pd.notna(nx_fop)),
            "selloff_risk": bool(selloff_risk),
            "baseline_yoy_op": baseline_yoy_op_map.get(tk),
            "has_special_dividend": tk in special_div_tickers,
            "has_buyback": tk in buyback_tickers,
            "div_change": div_change,
            "qoq_op": qoq_map.get(tk, {}).get("qoq_op"),
            "per": per,
            "beta_20d": beta,
            "theme_boost": bool(theme_boost),
        })

    return pd.DataFrame(results)


# ══════════════ STEP C: スコアリング ══════════════


def compute_score(row: pd.Series) -> tuple[int, list[str]]:
    """ノートブック compute_score と完全一致."""
    score = 0
    reasons: list[str] = []
    cur_per: str = row["quarter"]
    exp: float | None = {"1Q": 0.25, "2Q": 0.50, "3Q": 0.75}.get(cur_per)

    prog = row.get("progress_op")
    if prog is not None and exp is not None:
        if prog > exp * 1.2:
            score += 1; reasons.append(f"進捗率高 {prog:.0%} (期待{exp:.0%})")
        elif prog < exp * 0.8:
            score -= 1; reasons.append(f"進捗率低 {prog:.0%} (期待{exp:.0%})")

    gc = row.get("guidance_op_change")
    if row.get("has_guidance_revision") and gc is not None:
        if gc > 0:
            score += 1; reasons.append(f"上方修正 {gc:+.1%}")
        elif gc < 0:
            score -= 1; reasons.append(f"下方修正 {gc:+.1%}")

    yoy = row.get("yoy_op")
    if yoy is not None:
        if yoy > 0.30:
            score += 1; reasons.append(f"YoY OP +{yoy:.0%}")
        elif yoy < -0.30:
            score -= 1; reasons.append(f"YoY OP {yoy:.0%}")

    cd = row.get("consensus_deviation")
    if cd is not None and pd.notna(cd):
        if cd > 0.10:
            score += 3; reasons.append(f"コンセ乖離 {cd:+.1%}")
        elif cd > 0.05:
            score += 2; reasons.append(f"コンセ乖離 {cd:+.1%}")
        elif cd > 0:
            score += 1; reasons.append(f"コンセ乖離 {cd:+.1%}")
        elif cd < -0.10:
            score -= 3; reasons.append(f"コンセ乖離 {cd:+.1%}")
        elif cd < -0.05:
            score -= 2; reasons.append(f"コンセ乖離 {cd:+.1%}")
        elif cd < 0:
            score -= 1; reasons.append(f"コンセ乖離 {cd:+.1%}")

    nyc = row.get("next_year_op_change")
    if nyc is not None and pd.notna(nyc):
        if nyc > 0.10:
            score += 2; reasons.append(f"来期OP増益 {nyc:+.1%}")
        elif nyc < -0.10:
            score -= 2; reasons.append(f"来期OP減益 {nyc:+.1%}")
    elif cur_per == "FY" and row.get("next_year_disclosed") is False:
        reasons.append("来期予想未開示 (F5/F7/F12無効)")

    if row.get("selloff_risk"):
        score -= 2; reasons.append("売り圧力リスク(3Q高進捗+修正なし)")

    baseline = row.get("baseline_yoy_op")
    if (
        cur_per == "FY"
        and nyc is not None and pd.notna(nyc)
        and baseline is not None and pd.notna(baseline)
    ):
        gap = nyc - baseline
        if gap > 0.20:
            score += 1; reasons.append(f"成長加速 (翌期{nyc:+.0%} vs baseline{baseline:+.0%})")
        elif gap < -0.20:
            score -= 1; reasons.append(f"成長減速 (翌期{nyc:+.0%} vs baseline{baseline:+.0%})")

    if row.get("has_special_dividend"):
        score += 1; reasons.append("記念配当/特別配当")

    if row.get("theme_boost"):
        score += 1
        b = row.get("beta_20d", 0)
        reasons.append(f"テーマブースト (β={b:.1f}, TOPIX+)")

    if row.get("has_buyback"):
        score += 2; reasons.append("自社株買い")

    div_chg = row.get("div_change")
    if div_chg is not None and pd.notna(div_chg):
        if div_chg > 0.05:
            score += 1; reasons.append(f"増配 {div_chg:+.0%}")
        elif div_chg < -0.20:
            score -= 3; reasons.append(f"大幅減配 {div_chg:+.0%}")
        elif div_chg < -0.05:
            score -= 2; reasons.append(f"減配 {div_chg:+.0%}")

    per = row.get("per")
    if (
        per is not None and pd.notna(per) and per > 0
        and nyc is not None and pd.notna(nyc) and nyc > 0
    ):
        growth_pct = nyc * 100
        peg = per / growth_pct if growth_pct > 0 else float("inf")
        if peg < 0.5:
            score += 2; reasons.append(f"PEG割安 {peg:.1f} (PER{per:.0f}x/成長{growth_pct:.0f}%)")
        elif peg < 1.0:
            score += 1; reasons.append(f"PEG割安 {peg:.1f} (PER{per:.0f}x/成長{growth_pct:.0f}%)")
        elif peg > 2.0:
            score -= 1; reasons.append(f"PEG割高 {peg:.1f} (PER{per:.0f}x/成長{growth_pct:.0f}%)")

    qoq = row.get("qoq_op")
    if qoq is not None and pd.notna(qoq):
        if qoq > 0.50:
            score += 1; reasons.append(f"QoQ OP急伸 {qoq:+.0%}")
        elif qoq < -0.50:
            score -= 2; reasons.append(f"QoQ OP急落 {qoq:+.0%}")

    return score, reasons


def score_to_prediction(s: int) -> str:
    if s >= 2:
        return "UP"
    if s <= -2:
        return "DOWN"
    return "NEUTRAL"


def classify_return(ret: float) -> str:
    if ret > 0.02:
        return "UP"
    if ret < -0.02:
        return "DOWN"
    return "NEUTRAL"


# ══════════════ STEP D: 1 日分の predict + answer ══════════════


def run_one(predict_date: str, actual_date: str, shared: dict) -> dict | None:
    print(f"\n━━━ {predict_date} → {actual_date} ━━━")

    df_feat = compute_features(predict_date, shared)
    if df_feat is None or df_feat.empty:
        print("  fin_summary なし、スキップ")
        return None

    scores, preds, all_reasons = [], [], []
    for _, row in df_feat.iterrows():
        s, r = compute_score(row)
        scores.append(s)
        preds.append(score_to_prediction(s))
        all_reasons.append(" / ".join(r) if r else "シグナルなし")
    df_feat["score"] = scores
    df_feat["prediction"] = preds
    df_feat["reasons"] = all_reasons

    n_intra = int(df_feat["is_intraday"].sum())
    n_after = len(df_feat) - n_intra
    print(f"  特徴量: {len(df_feat)} (ザラバ:{n_intra}, 引け後:{n_after})")
    print(f"  予測分布: {df_feat['prediction'].value_counts().to_dict()}")

    now = datetime.now(tz=JST)

    # ── prediction 保存 ──
    pred_records = df_feat[[
        "ticker", "name", "industry_33", "market_division", "quarter",
        "is_intraday", "disc_time", "score", "prediction", "reasons",
        "progress_op", "has_guidance_revision", "guidance_op_change",
        "yoy_op", "consensus_deviation", "f4_source",
        "next_year_op_change", "next_year_disclosed", "selloff_risk",
        "baseline_yoy_op", "has_special_dividend",
        "has_buyback", "div_change", "qoq_op", "per",
        "adj_close", "prev_close",
    ]].to_dict(orient="records")

    pred_payload = {
        "predict_date": predict_date,
        "created_at": now.isoformat(),
        "count": len(pred_records),
        "predictions": pred_records,
    }
    pred_path = (
        f"{GCS_PREDICTIONS}/prediction_{predict_date}_{now.strftime('%H%M%S')}.json"
    )
    gcs_save_json(pred_payload, pred_path)

    # ── answer check ──
    ph = hy(predict_date)
    ah = hy(actual_date)
    dfp = shared["df_price"]

    actual_close_df = (
        dfp[dfp["dt"] == ah][["TICKER", "ADJ_CLOSE"]]
        .rename(columns={"ADJ_CLOSE": "actual_close_price"})
    )
    predict_close_df = (
        dfp[dfp["dt"] == ph][["TICKER", "ADJ_CLOSE"]]
        .rename(columns={"ADJ_CLOSE": "predict_close_price"})
    )

    df_pred = df_feat.copy()

    # ザラバ: before=前日close, after=当日close（予測データに含む）
    df_intra = df_pred[df_pred["is_intraday"]].copy()
    df_intra["compare_before_close"] = pd.to_numeric(df_intra["prev_close"], errors="coerce")
    df_intra["compare_after_close"] = pd.to_numeric(df_intra["adj_close"], errors="coerce")
    df_intra["actual_return"] = (
        (df_intra["compare_after_close"] - df_intra["compare_before_close"])
        / df_intra["compare_before_close"]
    )

    # 引け後: before=当日close, after=翌日close
    df_after = df_pred[~df_pred["is_intraday"]].copy()
    df_after = df_after.merge(
        actual_close_df, left_on="ticker", right_on="TICKER", how="left"
    ).drop(columns=["TICKER"])
    df_after = df_after.merge(
        predict_close_df, left_on="ticker", right_on="TICKER", how="left"
    ).drop(columns=["TICKER"])
    df_after["compare_before_close"] = pd.to_numeric(df_after["predict_close_price"], errors="coerce")
    df_after["compare_after_close"] = pd.to_numeric(df_after["actual_close_price"], errors="coerce")
    df_after["actual_return"] = (
        (df_after["compare_after_close"] - df_after["compare_before_close"])
        / df_after["compare_before_close"]
    )

    cols = [c for c in df_pred.columns] + [
        "compare_before_close", "compare_after_close", "actual_return",
    ]
    df_compare = pd.concat([df_intra[cols], df_after[cols]], ignore_index=True)

    df_compare["actual_category"] = df_compare["actual_return"].apply(
        lambda x: classify_return(x) if pd.notna(x) else None
    )
    dir_map = {"UP": 1, "NEUTRAL": 0, "DOWN": -1}
    df_compare["pred_dir"] = df_compare["prediction"].map(dir_map)
    df_compare["actual_dir"] = df_compare["actual_category"].map(dir_map)
    df_compare["direction_match"] = df_compare["pred_dir"] == df_compare["actual_dir"]

    matched = df_compare["actual_return"].notna()
    n_matched = int(matched.sum())
    dir_acc = (
        float(df_compare.loc[matched, "direction_match"].mean()) if n_matched else 0.0
    )
    score_ret_corr = (
        float(df_compare.loc[matched, ["score", "actual_return"]].corr().iloc[0, 1])
        if n_matched > 1
        else 0.0
    )
    print(
        f"  突合 {n_matched}/{len(df_compare)}  方向一致率 {dir_acc:.1%}  相関 {score_ret_corr:.3f}"
    )

    actual_records = df_compare[[
        "ticker", "name", "quarter", "is_intraday", "score", "prediction",
        "actual_return", "actual_category", "direction_match",
        "compare_before_close", "compare_after_close",
    ]].to_dict(orient="records")

    actual_payload = {
        "predict_date": predict_date,
        "actual_date": actual_date,
        "created_at": now.isoformat(),
        "count": len(actual_records),
        "direction_accuracy": dir_acc if n_matched else None,
        "score_return_correlation": score_ret_corr if n_matched else None,
        "actuals": actual_records,
    }
    save_date = now.strftime("%Y%m%d")
    actual_path = (
        f"{GCS_ACTUALS}/actual_{save_date}_{now.strftime('%H%M%S')}_for_{predict_date}.json"
    )
    gcs_save_json(actual_payload, actual_path)

    return {
        "predict_date": predict_date,
        "actual_date": actual_date,
        "n_matched": n_matched,
        "direction_accuracy": dir_acc,
        "score_return_correlation": score_ret_corr,
    }


# ══════════════ STEP E: accuracy_summary 更新 ══════════════


def update_accuracy_summary() -> None:
    print("\n━━━ accuracy_summary 更新 ━━━")
    actual_blobs = [
        b for b in gcs_list_blobs("earnings_model/actuals/") if b.endswith(".json")
    ]
    # predict_date 単位で最新を選ぶ
    by_pd: dict[str, str] = {}
    for b in sorted(actual_blobs):
        name = b.rsplit("/", 1)[-1].replace(".json", "")
        parts = name.split("_")
        if "for" in parts:
            pd_key = parts[parts.index("for") + 1]
        else:
            pd_key = parts[1] if len(parts) >= 2 else name
        by_pd[pd_key] = b  # sorted 昇順なので最新が残る

    all_records: list[dict] = []
    for b in by_pd.values():
        data = gcs_load_json(f"gs://{GCS_BUCKET_NAME}/{b}")
        if data and "actuals" in data:
            for rec in data["actuals"]:
                rec["predict_date"] = data.get("predict_date", "")
                rec["actual_date"] = data.get("actual_date", "")
                all_records.append(rec)

    df_all = pd.DataFrame(all_records)
    if df_all.empty:
        print("  actual 無し")
        return
    df_all["actual_return"] = pd.to_numeric(df_all["actual_return"], errors="coerce")
    df_valid = df_all.dropna(subset=["actual_return"])

    dir_acc_total = float(df_valid["direction_match"].mean())
    corr = (
        float(df_valid[["score", "actual_return"]].corr().iloc[0, 1])
        if len(df_valid) > 1
        else 0.0
    )

    cat_order = ["DOWN", "NEUTRAL", "UP"]
    cat_stats = df_valid.groupby("prediction").agg(
        count=("actual_return", "size"),
        mean_return=("actual_return", "mean"),
        median_return=("actual_return", "median"),
        direction_accuracy=("direction_match", "mean"),
    ).reindex(cat_order).dropna(how="all")

    now = datetime.now(tz=JST)
    summary = {
        "updated_at": now.isoformat(),
        "total_records": len(df_valid),
        "num_dates": int(df_valid["predict_date"].nunique()),
        "direction_accuracy": dir_acc_total,
        "score_return_correlation": corr,
        "category_stats": {
            cat: {
                "count": int(row["count"]),
                "mean_return": float(row["mean_return"]),
                "median_return": float(row["median_return"]),
                "direction_accuracy": float(row["direction_accuracy"]),
            }
            for cat, row in cat_stats.iterrows()
        },
        "predict_dates": sorted(df_valid["predict_date"].unique().tolist()),
    }
    gcs_save_json(summary, f"{GCS_ACCURACY}/accuracy_summary.json")
    print(
        f"  total={len(df_valid)}  dates={summary['num_dates']}  "
        f"dir_acc={dir_acc_total:.1%}  corr={corr:.3f}"
    )


# ══════════════ MAIN ══════════════


def main() -> None:
    t0 = time.time()
    print("=== batch_rerun_predict ===")
    print(f"対象: {len(DATE_PAIRS)} 日 ({DATE_PAIRS[0][0]}〜{DATE_PAIRS[-1][0]})")

    shared = fetch_shared_data()
    print(f"\n共通データ取得完了: {time.time() - t0:.1f}秒")

    summaries: list[dict] = []
    for pd_date, ac_date in DATE_PAIRS:
        r = run_one(pd_date, ac_date, shared)
        if r:
            summaries.append(r)

    update_accuracy_summary()

    print("\n━━━ 日別サマリ ━━━")
    for s in summaries:
        print(
            f"  {s['predict_date']}→{s['actual_date']}: "
            f"n={s['n_matched']}  dir_acc={s['direction_accuracy']:.1%}  "
            f"corr={s['score_return_correlation']:.3f}"
        )
    print(f"\n全処理完了: {time.time() - t0:.1f}秒")


if __name__ == "__main__":
    main()

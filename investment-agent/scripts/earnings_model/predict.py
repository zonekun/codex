"""決算反応予測モデル — 統合 CLI.

Usage:
    # 今日の答え合わせ（前営業日を自動算出して predict + answer）
    PYTHONUTF8=1 python scripts/earnings_model/predict.py today --actual-date 20260507

    # 日次予測（当日）
    PYTHONUTF8=1 python scripts/earnings_model/predict.py predict --date 20260505

    # 答え合わせ（翌営業日に実行）
    PYTHONUTF8=1 python scripts/earnings_model/predict.py answer --date 20260505 --actual-date 20260506

    # 過去バッチ再実行（答え合わせ日の範囲）
    PYTHONUTF8=1 python scripts/earnings_model/predict.py backfill --from 20260401 --to 20260428

    # リビルド（GCS既存全期間を再構築）
    PYTHONUTF8=1 python scripts/earnings_model/predict.py backfill

    # 精度集計（GCS全期間）
    PYTHONUTF8=1 python scripts/earnings_model/predict.py accuracy
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── SSL 迂回（ローカル環境） ──
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
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "earnings_model"))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from google.cloud import bigquery, storage
from google.oauth2 import service_account
import jquantsapi

from earnings_model_core import (
    LOW_PROFIT_THRESHOLD,
    Q_MAP,
    CUM_PREV_Q,
    PRED_COLUMNS,
    PREV_Q_MAP,
    classify_return,
    compute_score,
    score_to_prediction,
)

JST = ZoneInfo("Asia/Tokyo")
KEY_FILE = str(PROJECT_ROOT / "keys" / "gcp-service-account.json")
_creds = service_account.Credentials.from_service_account_file(KEY_FILE)
bq = bigquery.Client(credentials=_creds, project="gmailpj-357912")
gcs = storage.Client(credentials=_creds, project="gmailpj-357912")
_JQ_KEY = os.environ["JQUANTS_API_KEY"]
jq_cli = jquantsapi.ClientV2(api_key=_JQ_KEY)

GCS_BUCKET_NAME = "stock_data_1930932"
GCS_BUCKET = f"gs://{GCS_BUCKET_NAME}"
GCS_PREDICTIONS = f"{GCS_BUCKET}/earnings_model/earnings_reaction_predictions"
GCS_ACTUALS = f"{GCS_BUCKET}/earnings_model/earnings_reaction_actuals"
GCS_ACCURACY = f"{GCS_BUCKET}/earnings_model/earnings_reaction_accuracy"
GCS_PREDICTIONS_PREFIX = "earnings_model/earnings_reaction_predictions/"
GCS_ACTUALS_PREFIX = "earnings_model/earnings_reaction_actuals/"

import structlog

log = structlog.get_logger()


# ══════════════ ユーティリティ ══════════════


def hy(yyyymmdd: str) -> str:
    """YYYYMMDD → YYYY-MM-DD."""
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def gcs_save_json(data: dict | list, gcs_path: str) -> None:
    """GCS に JSON を保存する."""
    path = gcs_path.replace(f"gs://{GCS_BUCKET_NAME}/", "")
    blob = gcs.bucket(GCS_BUCKET_NAME).blob(path)
    blob.upload_from_string(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        content_type="application/json",
    )
    log.info("gcs_saved", path=gcs_path)


def gcs_load_json(gcs_path: str) -> dict | list | None:
    """GCS から JSON をロードする."""
    path = gcs_path.replace(f"gs://{GCS_BUCKET_NAME}/", "")
    blob = gcs.bucket(GCS_BUCKET_NAME).blob(path)
    try:
        return json.loads(blob.download_as_text())
    except Exception as e:
        log.warning("gcs_load_failed", path=gcs_path, error=str(e))
        return None


def gcs_list_blobs(prefix: str) -> list[str]:
    """GCS プレフィックス配下の blob 名リストを返す."""
    return [b.name for b in gcs.bucket(GCS_BUCKET_NAME).list_blobs(prefix=prefix)]


def _derive_current_fy(prev_disc_type: str | None, prev_disc_fy_end: str | None) -> str | None:
    """fin_summary の直近開示 Q と FY末日から、当期 FY（YYYYMM）を導出する.

    直近が FY 発表済み → 次は翌FYの1Q → current_fy = 翌FY
    直近が 1Q/2Q/3Q → 同FYの次Q → current_fy = 同FY
    """
    if not prev_disc_fy_end:
        return None
    fy_end_str = str(prev_disc_fy_end).replace("-", "")[:8]
    if len(fy_end_str) < 6:
        return None
    fy_yyyymm = fy_end_str[:6]

    if prev_disc_type == "FY":
        y = int(fy_yyyymm[:4])
        m = fy_yyyymm[4:6]
        return f"{y + 1}{m}"
    return fy_yyyymm


def _build_cons_map_from_df(
    df_cons: pd.DataFrame, df_prev_disc: pd.DataFrame, predict_date_hyphen: str
) -> dict[tuple[str, str, str], dict]:
    """CONSENSUS v4 DataFrame から cons_map を構築する.

    Returns:
        cons_map[(ticker, quarter, "CURRENT"|"NEXT")] = {"ORD_PROFIT": ..., "NET_PROFIT": ..., "EPS": ...}
    """
    # 各銘柄の current_fy を特定
    current_fy_map: dict[str, str] = {}
    for _, row in df_prev_disc.iterrows():
        tk = str(row.get("tk", row.get("TICKER", "")))[:4]
        if tk in current_fy_map:
            continue
        prev_type = row.get("TYPE_OF_CURRENT_PERIOD") or row.get("prev_disc_type")
        prev_fy_end = row.get("CURRENT_FISCAL_YEAR_END_DATE") or row.get("prev_disc_fy_end")
        if prev_fy_end is None:
            fy_start = row.get("CURRENT_FISCAL_YEAR_START_DATE")
            if fy_start:
                ts = pd.to_datetime(fy_start, errors="coerce")
                if pd.notna(ts):
                    prev_fy_end = (ts + pd.DateOffset(years=1) - pd.DateOffset(days=1)).strftime("%Y%m%d")
        cfy = _derive_current_fy(prev_type, prev_fy_end)
        if cfy:
            current_fy_map[tk] = cfy

    # as-of フィルタ + QUICK優先マージ（V_CONSENSUS_MERGED と同等ロジック）
    dfc = df_cons[df_cons["DATAAT"] <= predict_date_hyphen].copy()
    q_rows = (
        dfc[dfc["SOURCE"] == "QUICK"]
        .sort_values("DATAAT", ascending=False)
        .drop_duplicates(["TICKER", "FY", "QUARTER"])
    )
    i_rows = (
        dfc[dfc["SOURCE"] == "IFIS"]
        .sort_values("DATAAT", ascending=False)
        .drop_duplicates(["TICKER", "FY", "QUARTER"])
        [["TICKER", "FY", "QUARTER", "ORD_PROFIT"]]
        .rename(columns={"ORD_PROFIT": "_ifis_ord"})
    )
    dfc = q_rows.merge(i_rows, on=["TICKER", "FY", "QUARTER"], how="outer")
    dfc["ORD_PROFIT"] = dfc["ORD_PROFIT"].fillna(dfc["_ifis_ord"])
    dfc = dfc.drop(columns=["_ifis_ord", "SOURCE"], errors="ignore")

    cons_map: dict[tuple[str, str, str], dict] = {}
    for _, row in dfc.iterrows():
        tk = row["TICKER"]
        fy = str(row["FY"]).replace("-", "")[:6]
        quarter = row["QUARTER"]
        cfy = current_fy_map.get(tk)
        if not cfy:
            continue

        if fy == cfy:
            period_rel = "CURRENT"
        elif fy > cfy:
            period_rel = "NEXT"
        else:
            continue

        key = (tk, quarter, period_rel)
        if key not in cons_map:
            cons_map[key] = {}
        if pd.notna(row.get("ORD_PROFIT")):
            cons_map[key]["ORD_PROFIT"] = float(row["ORD_PROFIT"])
        if pd.notna(row.get("NET_PROFIT")):
            cons_map[key]["NET_PROFIT"] = float(row["NET_PROFIT"])
        if pd.notna(row.get("EPS")):
            cons_map[key]["EPS"] = float(row["EPS"])

    return cons_map


# ══════════════ TDnet フォールバック（3段） ══════════════


def _fetch_tdnet_yanoshin(date_str: str) -> list[dict]:
    """yanoshin JSON API から当日全開示を取得."""
    import httpx

    url = f"https://webapi.yanoshin.jp/webapi/tdnet/list/{date_str}-{date_str}.json?limit=9999"
    try:
        resp = httpx.get(url, timeout=30)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        return [
            {"code": (t.get("company_code") or "")[:4], "title": t.get("title", "")}
            for item in items
            if (t := item.get("Tdnet"))
        ]
    except Exception as e:
        log.warning("yanoshin_fetch_failed", error=str(e))
        return []


def _fetch_tdnet_html(date_str: str) -> list[dict]:
    """TDnet HTML スクレイピングでフォールバック取得."""
    import httpx
    from bs4 import BeautifulSoup

    results = []
    page = 1
    while True:
        url = f"https://www.release.tdnet.info/inbs/I_list_{page:03d}_{date_str}.html"
        try:
            resp = httpx.get(url, timeout=15, verify=False)
        except Exception:
            break
        if resp.status_code == 404:
            break
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "lxml")
        rows_found = 0
        for tr in soup.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) < 4:
                continue
            if "kjTime" not in " ".join(cells[0].get("class", [])):
                continue
            rows_found += 1
            results.append(
                {"code": cells[1].get_text(strip=True)[:4], "title": cells[3].get_text(strip=True)}
            )
        if rows_found == 0:
            break
        page += 1
        time.sleep(0.5)
    return results


def _fetch_tdnet_events(
    predict_date: str, target_tickers: set[str], shared: dict | None = None
) -> tuple[set[str], dict[str, float], set[str]]:
    """TDnet イベント取得（3段フォールバック）.

    Returns:
        (special_div_tickers, buyback_tickers, stock_split_tickers)
    """
    BUYBACK_KW = ["自己株式の取得", "自己株式取得", "自己株式の買付", "自社株買い"]
    BUYBACK_CAT = "自己株式取得"
    SPECIAL_DIV_KW = ["記念配当", "特別配当"]
    STOCK_SPLIT_KW = ["株式分割"]

    special_div: set[str] = set()
    buyback: dict[str, float] = {}
    stock_split: set[str] = set()

    today_str = datetime.now(tz=JST).strftime("%Y%m%d")
    ph = hy(predict_date)

    if predict_date >= today_str:
        # 当日/未来: yanoshin → HTML フォールバック
        log.info("tdnet_fetch", source="yanoshin", date=predict_date)
        disclosures = _fetch_tdnet_yanoshin(predict_date)
        if not disclosures:
            log.info("tdnet_fallback", source="html")
            disclosures = _fetch_tdnet_html(predict_date)
        for d in disclosures:
            code, title = d["code"], d["title"]
            if code not in target_tickers:
                continue
            if any(kw in title for kw in SPECIAL_DIV_KW):
                special_div.add(code)
            if any(kw in title for kw in BUYBACK_KW):
                buyback[code] = 0.0
            if any(kw in title for kw in STOCK_SPLIT_KW):
                stock_split.add(code)
    elif shared and "df_tdnet" in shared:
        # backfill: 共有データから
        dftd = shared["df_tdnet"]
        dftd_day = dftd[dftd["SUBMISSION_DATE"] == ph]
        for _, r in dftd_day.iterrows():
            tk, title = r["TICKER"], r["DOC_TITLE"]
            if tk not in target_tickers:
                continue
            main_cat = r.get("MAIN_CATEGORY", "")
            raw_sc = r.get("SUB_CATEGORIES")
            sub_cats = list(raw_sc) if raw_sc is not None and len(raw_sc) > 0 else []
            if any(kw in title for kw in SPECIAL_DIV_KW):
                special_div.add(tk)
            if main_cat == BUYBACK_CAT or BUYBACK_CAT in sub_cats:
                buyback[tk] = 0.0
            if any(kw in title for kw in STOCK_SPLIT_KW):
                stock_split.add(tk)
    else:
        # 過去: BQ
        log.info("tdnet_fetch", source="bq", date=predict_date)
        tickers_sql = ",".join([f"'{t}'" for t in target_tickers])
        q = f"""SELECT DISTINCT TICKER, DOC_TITLE, MAIN_CATEGORY, SUB_CATEGORIES
        FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
        WHERE SUBMISSION_DATE = '{ph}'
          AND TICKER IN ({tickers_sql})
          AND (DOC_TITLE LIKE '%記念配当%' OR DOC_TITLE LIKE '%特別配当%'
               OR MAIN_CATEGORY = '{BUYBACK_CAT}'
               OR '{BUYBACK_CAT}' IN UNNEST(SUB_CATEGORIES)
               OR DOC_TITLE LIKE '%株式分割%')"""
        df_td = bq.query(q).to_dataframe()
        for _, r in df_td.iterrows():
            tk, title = r["TICKER"], r["DOC_TITLE"]
            main_cat = r.get("MAIN_CATEGORY", "")
            raw_sc = r.get("SUB_CATEGORIES")
            sub_cats = list(raw_sc) if raw_sc is not None and len(raw_sc) > 0 else []
            if any(kw in title for kw in SPECIAL_DIV_KW):
                special_div.add(tk)
            if main_cat == BUYBACK_CAT or BUYBACK_CAT in sub_cats:
                buyback[tk] = 0.0
            if any(kw in title for kw in STOCK_SPLIT_KW):
                stock_split.add(tk)

    return special_div, buyback, stock_split


# ══════════════ データ取得 ══════════════


def fetch_shared_data(date_min: str, date_max: str, date_max_actual: str) -> dict:
    """BQ 共通データを一括取得する（backfill 用）。date_max は actual_date ベースの終了日を渡してよい."""
    shared: dict = {}
    price_min = (pd.Timestamp(hy(date_min)) - pd.DateOffset(days=7)).strftime("%Y-%m-%d")

    log.info("fetch_shared", step="1/8", table="STOCK_CODE_LIST")
    df_names = bq.query(
        "SELECT TICKER, STOCK_NAME, INDUSTRY_33_CODE, MARKET_CATEGORY "
        "FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`"
    ).to_dataframe()
    shared["name_map"] = dict(zip(df_names["TICKER"], df_names["STOCK_NAME"]))
    shared["sector_map"] = dict(zip(df_names["TICKER"], df_names["INDUSTRY_33_CODE"].astype(str)))
    shared["market_map"] = dict(zip(df_names["TICKER"], df_names["MARKET_CATEGORY"].fillna("").astype(str)))

    log.info("fetch_shared", step="2/8", table="STOCK_PRICE_JQUANTS")
    q_price = f"""SELECT TICKER, DATE AS dt, ADJ_CLOSE
    FROM `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS`
    WHERE DATE BETWEEN '{price_min}' AND '{hy(date_max_actual)}'
      AND IS_PREFERRED = FALSE"""
    df_price = bq.query(q_price).to_dataframe()
    df_price["dt"] = df_price["dt"].astype(str)
    shared["df_price"] = df_price

    # CONSENSUS v4: 1-pass 全件取得 → pandas as-of フィルタ（SOURCE付きでQUICK優先マージ）
    log.info("fetch_shared", step="3/8", table="CONSENSUS_v4")
    q_cons = f"""SELECT TICKER, FY, QUARTER, DATAAT, SOURCE, ORD_PROFIT, NET_PROFIT, EPS
    FROM `gmailpj-357912.STOCK.CONSENSUS`
    WHERE DATAAT <= '{hy(date_max)}'"""
    df_cons = bq.query(q_cons).to_dataframe()
    df_cons["DATAAT"] = df_cons["DATAAT"].astype(str)
    df_cons["FY"] = df_cons["FY"].astype(str).str.replace("-", "")
    shared["df_cons"] = df_cons

    # 前回開示情報（_derive_current_fy 入力 + 前回予想）
    _prev_from = (pd.Timestamp(hy(date_min)) - pd.DateOffset(months=18)).strftime("%Y-%m-%d")
    log.info("fetch_shared", step="4/8", table="fin_summary")
    q_prev = f"""SELECT LOCAL_CODE, DISCLOSED_DATE,
      FORECAST_OPERATING_PROFIT, FORECAST_PROFIT,
      FORECAST_DIVIDEND_PER_SHARE_ANNUAL,
      OPERATING_PROFIT,
      TYPE_OF_CURRENT_PERIOD,
      CURRENT_FISCAL_YEAR_START_DATE,
      CURRENT_FISCAL_YEAR_END_DATE
    FROM `gmailpj-357912.STOCK.fin_summary`
    WHERE DISCLOSED_DATE >= '{_prev_from}'
      AND DISCLOSED_DATE <= '{hy(date_max)}'"""
    df_prev = bq.query(q_prev).to_dataframe()
    df_prev["DISCLOSED_DATE"] = df_prev["DISCLOSED_DATE"].astype(str)
    df_prev["tk"] = df_prev["LOCAL_CODE"].astype(str).str[:4]
    shared["df_prev"] = df_prev

    _qoq_from = "2020-04-01"
    log.info("fetch_shared", step="5/8", table="v_fin_summary_actual_for_q_on_q")
    q_qoq = f"""SELECT LOCAL_CODE, DISCLOSED_DATE, QUARTER,
      CURRENT_FISCAL_YEAR_START_DATE, OPERATING_PROFIT, PROFIT
    FROM `gmailpj-357912.STOCK.v_fin_summary_actual_for_q_on_q`
    WHERE CURRENT_FISCAL_YEAR_START_DATE >= '{_qoq_from}'
      AND DISCLOSED_DATE <= '{hy(date_max)}'"""
    df_qoq = bq.query(q_qoq).to_dataframe()
    df_qoq["tk"] = df_qoq["LOCAL_CODE"].astype(str).str[:4]
    df_qoq["DISCLOSED_DATE"] = df_qoq["DISCLOSED_DATE"].astype(str)
    df_qoq["CURRENT_FISCAL_YEAR_START_DATE"] = df_qoq["CURRENT_FISCAL_YEAR_START_DATE"].astype(str)
    shared["df_qoq"] = df_qoq

    log.info("fetch_shared", step="6/8", table="TDNET_DOCUMENTS_ENHANCED")
    q_tdnet = f"""SELECT DISTINCT TICKER, SUBMISSION_DATE, DOC_TITLE, MAIN_CATEGORY, SUB_CATEGORIES
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE SUBMISSION_DATE BETWEEN '{hy(date_min)}' AND '{hy(date_max)}'
      AND (DOC_TITLE LIKE '%記念配当%' OR DOC_TITLE LIKE '%特別配当%'
           OR MAIN_CATEGORY = '自己株式取得'
           OR '自己株式取得' IN UNNEST(SUB_CATEGORIES)
           OR DOC_TITLE LIKE '%株式分割%')"""
    df_tdnet = bq.query(q_tdnet).to_dataframe()
    df_tdnet["SUBMISSION_DATE"] = df_tdnet["SUBMISSION_DATE"].astype(str)
    shared["df_tdnet"] = df_tdnet

    log.info("fetch_shared", step="7/8", table="INDEX_PRICE")
    q_topix = f"""SELECT DATE AS dt, CLOSE
    FROM `gmailpj-357912.STOCK.INDEX_PRICE`
    WHERE INDEX_CODE = '0000'
      AND DATE BETWEEN '{price_min}' AND '{hy(date_max)}'
    ORDER BY DATE"""
    df_topix = bq.query(q_topix).to_dataframe()
    df_topix["dt"] = df_topix["dt"].astype(str)
    df_topix = df_topix.sort_values("dt").reset_index(drop=True)
    df_topix["topix_ret"] = df_topix["CLOSE"].pct_change()
    shared["df_topix"] = df_topix

    log.info("fetch_shared", step="GCS", blob="beta_20d.csv")
    _beta_csv = gcs.bucket(GCS_BUCKET_NAME).blob("earnings_model/zaraba_beta_20d/beta_20d.csv").download_as_text()
    df_beta = pd.read_csv(io.StringIO(_beta_csv))
    shared["beta_map"] = dict(zip(df_beta["TICKER"], df_beta["beta_20d"]))

    log.info("fetch_shared", step="8/8", table="YF_STOCK_INFO")
    q_yf = f"""SELECT TICKER, MARKET_CAP, FORWARD_PE, LOADED_AT, LOADED_DATE
    FROM `gmailpj-357912.STOCK.YF_STOCK_INFO`
    WHERE LOADED_DATE <= '{hy(date_max)}'"""
    df_yf = bq.query(q_yf).to_dataframe()
    df_yf["LOADED_DATE"] = df_yf["LOADED_DATE"].astype(str)
    shared["df_yf_info"] = df_yf

    return shared


# ══════════════ 特徴量計算 ══════════════


def compute_features(predict_date: str, shared: dict) -> pd.DataFrame | None:
    """指定日の特徴量データフレームを構築する."""
    ph = hy(predict_date)

    log.info("compute_features", date=predict_date, step="jquants_fin_summary")
    df_fin = jq_cli.get_fin_summary(date_yyyymmdd=predict_date)
    if df_fin.empty:
        return None

    df_fin = df_fin[df_fin["DocType"].str.contains("FinancialStatements", na=False)].copy()

    # 訂正報告除外
    cutoff = pd.Timestamp(ph) - pd.DateOffset(months=9)
    df_fin["_cp"] = pd.to_datetime(df_fin["CurPerEn"], errors="coerce")
    df_fin = df_fin[df_fin["_cp"] >= cutoff].copy()
    df_fin.drop(columns=["_cp"], inplace=True)

    close_time = "15:30:00" if predict_date >= "20241105" else "15:00:00"
    df_fin["is_intraday"] = df_fin["DiscTime"] < close_time
    df_fin["ticker"] = df_fin["Code"].astype(str).str[:4]

    for col in [
        "OP", "OdP", "NP", "FOP", "FOdP", "FNP",
        "NxFOP", "NxFOdP", "NxFNp", "FDivAnn", "ForEPS", "EPS", "NxFEPS",
    ]:
        if col in df_fin.columns:
            df_fin[col] = pd.to_numeric(df_fin[col], errors="coerce")

    tickers: set[str] = set(df_fin["ticker"].unique())

    # ── 株価マップ ──
    dfp = shared["df_price"]
    today_p = dfp[dfp["dt"] == ph]
    price_map: dict[str, float] = dict(zip(today_p["TICKER"], today_p["ADJ_CLOSE"]))
    prev_p = (
        dfp[dfp["dt"] < ph]
        .sort_values(["TICKER", "dt"], ascending=[True, False])
        .drop_duplicates("TICKER", keep="first")
    )
    prev_price_map: dict[str, float] = dict(zip(prev_p["TICKER"], prev_p["ADJ_CLOSE"]))

    # ── CONSENSUS v3 as-of → cons_map ──
    dfc = shared["df_cons"]
    dfpv = shared["df_prev"]

    # 前回開示情報（_derive_current_fy 用）
    # 同一日に3Q実績とFY予想修正がある場合、FYを後回しにして3Qを優先する
    dfpv_before = dfpv[dfpv["DISCLOSED_DATE"] < ph].copy()
    dfpv_before["_fy_last"] = (dfpv_before["TYPE_OF_CURRENT_PERIOD"] == "FY").astype(int)
    df_prev_disc = (
        dfpv_before.sort_values(["DISCLOSED_DATE", "_fy_last"], ascending=[False, True])
        .drop_duplicates("tk", keep="first")
        .drop(columns=["_fy_last"])
    )
    cons_map = _build_cons_map_from_df(dfc, df_prev_disc, ph)

    # ── 前回予想 ──
    dfpv_asof = df_prev_disc.copy()
    prev_forecast_map: dict[str, dict] = {
        r["tk"]: {"FOP": r["FORECAST_OPERATING_PROFIT"], "FNP": r["FORECAST_PROFIT"]}
        for _, r in dfpv_asof.iterrows()
    }
    # 配当予想
    dfpd = dfpv[
        (dfpv["DISCLOSED_DATE"] < ph)
        & dfpv["FORECAST_DIVIDEND_PER_SHARE_ANNUAL"].notna()
        & (dfpv["FORECAST_DIVIDEND_PER_SHARE_ANNUAL"] > 0)
    ]
    dfpd = dfpd.sort_values("DISCLOSED_DATE", ascending=False).drop_duplicates("tk", keep="first")
    prev_div_map: dict[str, float] = dict(
        zip(dfpd["tk"], dfpd["FORECAST_DIVIDEND_PER_SHARE_ANNUAL"].astype(float))
    )

    # ── QoQ / YoY ──
    dfq = shared["df_qoq"]
    dfq_asof = dfq[dfq["DISCLOSED_DATE"] < ph].copy()

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
            & (dfpv_before["CURRENT_FISCAL_YEAR_START_DATE"].astype(str) == cur_fy_start)
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
                    float(jq_op_cum) - prev_cum if prev_cum is not None else float(jq_op_cum)
                )

        tk_qoq = dfq_asof[(dfq_asof["tk"] == tk) & (dfq_asof["QUARTER"] == q_label)]
        if cur_fy_start:
            tk_qoq = tk_qoq[tk_qoq["CURRENT_FISCAL_YEAR_START_DATE"].astype(str) < cur_fy_start]
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

        _is_turnaround = False
        if cur_standalone_op is not None and cur_standalone_op > 0:
            if prev_year_op is not None and pd.notna(prev_year_op) and float(prev_year_op) < 0:
                _is_turnaround = True
        qoq_map[tk] = {"yoy_op": yoy_op, "qoq_op": qoq_op, "is_turnaround": _is_turnaround}

    # ── baseline YoY OP（通期OP集約ベース）+ 5年中央値OP ──
    baseline_yoy_op_map: dict[str, float] = {}
    median_5y_op_map: dict[str, float] = {}
    for tk in tickers:
        tk_all = dfq_asof[dfq_asof["tk"] == tk]
        fy_ops: dict[str, float] = {}
        for fy_start, grp in tk_all.groupby("CURRENT_FISCAL_YEAR_START_DATE"):
            grp_valid = grp.dropna(subset=["OPERATING_PROFIT"]).drop_duplicates(["QUARTER"], keep="first")
            if set(grp_valid["QUARTER"].tolist()) >= {"1Q", "2Q", "3Q", "4Q"}:
                total = grp_valid["OPERATING_PROFIT"].sum()
                fy_ops[str(fy_start)] = float(total)
        sorted_fys = sorted(fy_ops.items(), reverse=True)
        if len(sorted_fys) >= 3:
            yoy_list: list[float] = []
            for j in range(len(sorted_fys) - 1):
                c, p = sorted_fys[j][1], sorted_fys[j + 1][1]
                if p != 0:
                    yoy_list.append((c - p) / abs(p))
            if yoy_list:
                baseline_yoy_op_map[tk] = float(pd.Series(yoy_list).median())
        ops_5y = [v for _, v in sorted_fys[:5]]
        if len(ops_5y) >= 3:
            median_5y_op_map[tk] = float(np.median(ops_5y))

    # ── TDnet イベント ──
    special_div_tickers, buyback_tickers, stock_split_tickers = _fetch_tdnet_events(
        predict_date, tickers, shared
    )

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

    # ── YF_STOCK_INFO as-of ──
    df_yf = shared["df_yf_info"]
    df_yf_asof = (
        df_yf[df_yf["LOADED_DATE"] <= ph]
        .sort_values("LOADED_AT", ascending=False)
        .drop_duplicates("TICKER", keep="first")
    )
    market_cap_map: dict[str, float] = {}
    forward_pe_map: dict[str, float] = {}
    for _, r in df_yf_asof.iterrows():
        if pd.notna(r["MARKET_CAP"]) and r["MARKET_CAP"] > 0:
            market_cap_map[r["TICKER"]] = float(r["MARKET_CAP"]) / 1e8
        if pd.notna(r["FORWARD_PE"]) and r["FORWARD_PE"] > 0:
            forward_pe_map[r["TICKER"]] = float(r["FORWARD_PE"])

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

        # F4: コンセンサス乖離（v4: ORD_PROFIT + NET_PROFIT）
        consensus_deviation: float | None = None
        np_consensus_deviation: float | None = None
        f4_source = ""
        f4_reasons: list[str] = []
        _np = row.get("NP")
        if cur_per == "FY":
            cons_next = cons_map.get((tk, "FY", "NEXT"), {})
            cons_next_ord = cons_next.get("ORD_PROFIT")
            cons_next_np = cons_next.get("NET_PROFIT")
            # F4a: 翌期予想ODP vs 翌期コンセORD_PROFIT
            if pd.notna(nx_fodp) and cons_next_ord is not None and cons_next_ord != 0:
                cn = cons_next_ord * 1_000_000
                consensus_deviation = float((nx_fodp - cn) / abs(cn))
                f4_source = "FY_NEXT"
            elif pd.isna(nx_fodp) and cons_next_ord is not None:
                f4_reasons.append("IFRS/OdP欠損→経常コンセ比較スキップ")
            # F4b: 翌期予想NP vs 翌期コンセNET_PROFIT
            _nx_fnp = row.get("NxFNp")
            if pd.notna(_nx_fnp) and cons_next_np is not None and cons_next_np != 0:
                cn_np = cons_next_np * 1_000_000
                np_consensus_deviation = float((float(_nx_fnp) - cn_np) / abs(cn_np))
        else:
            cons_cur = cons_map.get((tk, cur_per, "CURRENT"), {})
            cons_cur_ord = cons_cur.get("ORD_PROFIT")
            cons_cur_np = cons_cur.get("NET_PROFIT")
            # F4a: 実績ODP vs コンセORD_PROFIT
            if pd.notna(odp) and cons_cur_ord is not None and cons_cur_ord != 0:
                cc = cons_cur_ord * 1_000_000
                consensus_deviation = float((odp - cc) / abs(cc))
                f4_source = "Q_CURRENT"
            elif pd.isna(odp) and cons_cur_ord is not None:
                f4_reasons.append("IFRS/OdP欠損→経常コンセ比較スキップ")
            # F4b: 実績NP vs コンセNET_PROFIT
            if pd.notna(_np) and cons_cur_np is not None and cons_cur_np != 0:
                cc_np = cons_cur_np * 1_000_000
                np_consensus_deviation = float((float(_np) - cc_np) / abs(cc_np))

        next_year_op_change: float | None = None
        if cur_per == "FY" and pd.notna(nx_fop) and pd.notna(op) and op != 0:
            next_year_op_change = float((nx_fop - op) / abs(op))

        next_year_eps_change: float | None = None
        _jq_eps = row.get("EPS")
        _jq_nx_feps = row.get("NxFEPS")
        if cur_per == "FY" and pd.notna(_jq_eps) and pd.notna(_jq_nx_feps) and float(_jq_eps) > 0:
            next_year_eps_change = float((float(_jq_nx_feps) - float(_jq_eps)) / abs(float(_jq_eps)))

        _is_low_base = False
        _median_5y_op = median_5y_op_map.get(tk)
        _is_low_profit = (_median_5y_op is not None and _median_5y_op < LOW_PROFIT_THRESHOLD)
        if cur_per == "FY" and _median_5y_op is not None and _median_5y_op > 0:
            if pd.notna(op) and float(op) < _median_5y_op * 0.5:
                _is_low_base = True
                next_year_eps_change = None
                if pd.notna(nx_fop) and _median_5y_op != 0:
                    next_year_op_change = float((nx_fop - _median_5y_op) / abs(_median_5y_op))

        _fy_achievement: float | None = None
        if cur_per == "FY" and pd.notna(op) and pd.notna(fop) and fop != 0:
            _fy_achievement = float(op / fop)

        selloff_risk = (
            cur_per == "3Q"
            and progress_op is not None
            and progress_op > 0.90
            and not has_guidance_revision
        )

        beta = beta_map.get(tk)

        div_change: float | None = None
        if pd.notna(row.get("FDivAnn")) and tk in prev_div_map and prev_div_map[tk] > 0:
            div_change = float((float(row.get("FDivAnn", 0)) - prev_div_map[tk]) / prev_div_map[tk])

        _for_eps_per: float | None = None
        _close = price_map.get(tk)
        _eps = row.get("ForEPS")
        if _close and pd.notna(_eps) and float(_eps) > 0:
            _for_eps_per = float(_close / float(_eps))
        per = _for_eps_per if _for_eps_per is not None else forward_pe_map.get(tk)

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
            "np_consensus_deviation": np_consensus_deviation,
            "f4_source": f4_source if f4_source else None,
            "f4_reasons": f4_reasons if f4_reasons else None,
            "next_year_op_change": next_year_op_change,
            "next_year_eps_change": next_year_eps_change,
            "next_year_disclosed": bool(cur_per == "FY" and pd.notna(nx_fop)),
            "selloff_risk": bool(selloff_risk),
            "baseline_yoy_op": baseline_yoy_op_map.get(tk),
            "has_special_dividend": tk in special_div_tickers,
            "has_buyback": tk in buyback_tickers,
            "has_stock_split": tk in stock_split_tickers,
            "market_cap_oku": market_cap_map.get(tk),
            "div_change": div_change,
            "qoq_op": qoq_map.get(tk, {}).get("qoq_op"),
            "per": per,
            "beta_20d": beta,
            "is_low_base": _is_low_base,
            "is_low_profit": _is_low_profit,
            "median_5y_op": _median_5y_op,
            "is_turnaround": qoq_map.get(tk, {}).get("is_turnaround", False),
            "fy_achievement": _fy_achievement,
        })

    return pd.DataFrame(results)


# ══════════════ サブコマンド: predict ══════════════


def cmd_predict(args: argparse.Namespace) -> None:
    """当日予測を実行し GCS に保存する."""
    predict_date = args.date
    ph = hy(predict_date)
    log.info("predict_start", date=predict_date)

    # predict 単日: 共有データを1日分として取得
    shared = fetch_shared_data(predict_date, predict_date, predict_date)

    df_feat = compute_features(predict_date, shared)
    if df_feat is None or df_feat.empty:
        log.warning("no_fin_summary", date=predict_date)
        return

    # スコアリング
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
    log.info("predict_done", count=len(df_feat), intraday=n_intra, after_close=n_after,
             distribution=df_feat["prediction"].value_counts().to_dict())

    # GCS 保存
    now = datetime.now(tz=JST)
    pred_records = df_feat[PRED_COLUMNS].to_dict(orient="records")
    pred_payload = {
        "predict_date": predict_date,
        "created_at": now.isoformat(),
        "count": len(pred_records),
        "predictions": pred_records,
    }
    pred_path = f"{GCS_PREDICTIONS}/prediction_{predict_date}_{now.strftime('%H%M%S')}.json"
    gcs_save_json(pred_payload, pred_path)


# ══════════════ サブコマンド: answer ══════════════


def cmd_answer(args: argparse.Namespace) -> None:
    """答え合わせを実行し GCS に保存する."""
    predict_date = args.date
    actual_date = args.actual_date
    ph = hy(predict_date)
    ah = hy(actual_date)

    # 安全ガード
    if predict_date == actual_date:
        log.error("same_date", predict=predict_date, actual=actual_date)
        print(f"ERROR: PREDICT_DATE({predict_date}) == ACTUAL_DATE({actual_date})")
        sys.exit(1)

    # ACTUAL_DATE の株価投入チェック
    n_rows = int(
        bq.query(
            f"SELECT COUNT(*) AS cnt FROM `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS` WHERE DATE = '{ah}'"
        ).to_dataframe().iloc[0]["cnt"]
    )
    if n_rows < 3000:
        log.error("price_not_ready", actual_date=actual_date, rows=n_rows)
        print(f"ERROR: ACTUAL_DATE={ah} の株価データ未投入 ({n_rows} rows < 3000)")
        sys.exit(1)

    log.info("answer_start", predict=predict_date, actual=actual_date)

    # 最新の prediction ファイルをロード
    pred_blobs = [
        b for b in gcs_list_blobs(f"{GCS_PREDICTIONS_PREFIX}prediction_{predict_date}_")
        if b.endswith(".json")
    ]
    if not pred_blobs:
        log.error("no_prediction_file", date=predict_date)
        print(f"ERROR: prediction file not found for {predict_date}")
        sys.exit(1)
    pred_blob = sorted(pred_blobs)[-1]
    pred_data = gcs_load_json(f"gs://{GCS_BUCKET_NAME}/{pred_blob}")
    if pred_data is None:
        sys.exit(1)
    df_pred = pd.DataFrame(pred_data["predictions"])
    if "is_intraday" not in df_pred.columns:
        df_pred["is_intraday"] = False

    # 実績株価取得
    q_price = f"""SELECT TICKER, DATE AS dt, ADJ_CLOSE
    FROM `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS`
    WHERE DATE IN ('{ph}', '{ah}')
      AND IS_PREFERRED = FALSE"""
    df_price = bq.query(q_price).to_dataframe()
    df_price["dt"] = df_price["dt"].astype(str)

    # ザラバ: 前日close→当日close
    df_intra = df_pred[df_pred["is_intraday"]].copy()
    df_intra["compare_before_close"] = pd.to_numeric(df_intra["prev_close"], errors="coerce")
    df_intra["compare_after_close"] = pd.to_numeric(df_intra["adj_close"], errors="coerce")
    df_intra["actual_return"] = (
        (df_intra["compare_after_close"] - df_intra["compare_before_close"])
        / df_intra["compare_before_close"]
    )

    # 引け後: 当日close→翌日close
    df_after = df_pred[~df_pred["is_intraday"]].copy()
    price_predict = df_price[df_price["dt"] == ph][["TICKER", "ADJ_CLOSE"]].rename(
        columns={"ADJ_CLOSE": "compare_before_close"}
    )
    price_actual = df_price[df_price["dt"] == ah][["TICKER", "ADJ_CLOSE"]].rename(
        columns={"ADJ_CLOSE": "compare_after_close"}
    )
    df_after = df_after.merge(price_predict, left_on="ticker", right_on="TICKER", how="left").drop(columns=["TICKER"])
    df_after = df_after.merge(price_actual, left_on="ticker", right_on="TICKER", how="left").drop(columns=["TICKER"])
    df_after["actual_return"] = (
        (df_after["compare_after_close"] - df_after["compare_before_close"])
        / df_after["compare_before_close"]
    )

    cols = [c for c in df_pred.columns] + ["compare_before_close", "compare_after_close", "actual_return"]
    df_compare = pd.concat(
        [df_intra[[c for c in cols if c in df_intra.columns]],
         df_after[[c for c in cols if c in df_after.columns]]],
        ignore_index=True,
    )

    df_compare["actual_category"] = df_compare["actual_return"].apply(
        lambda x: classify_return(x) if pd.notna(x) else None
    )
    dir_map = {"UP": 1, "NEUTRAL": 0, "DOWN": -1}
    df_compare["pred_dir"] = df_compare["prediction"].map(dir_map)
    df_compare["actual_dir"] = df_compare["actual_category"].map(dir_map)
    df_compare["direction_match"] = df_compare["pred_dir"] == df_compare["actual_dir"]

    matched = df_compare["actual_return"].notna()
    n_matched = int(matched.sum())
    dir_acc = float(df_compare.loc[matched, "direction_match"].mean()) if n_matched else 0.0
    score_ret_corr = (
        float(df_compare.loc[matched, ["score", "actual_return"]].corr().iloc[0, 1])
        if n_matched > 1
        else 0.0
    )
    log.info("answer_done", matched=n_matched, total=len(df_compare),
             direction_accuracy=f"{dir_acc:.1%}", correlation=f"{score_ret_corr:.3f}")

    # GCS 保存
    now = datetime.now(tz=JST)
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
    actual_path = (
        f"{GCS_ACTUALS}/actual_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}_for_{predict_date}.json"
    )
    gcs_save_json(actual_payload, actual_path)


# ══════════════ サブコマンド: backfill ══════════════


def _get_business_day_pairs(date_from: str, date_to: str) -> list[tuple[str, str]]:
    """答え合わせ日（actual_date）の範囲から営業日ペア（predict_date, actual_date）を生成する."""
    q = f"""SELECT DISTINCT DATE
    FROM `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS`
    WHERE DATE BETWEEN DATE_SUB('{hy(date_from)}', INTERVAL 10 DAY) AND '{hy(date_to)}'
    ORDER BY DATE"""
    df = bq.query(q).to_dataframe()
    dates = sorted(df["DATE"].astype(str).tolist())

    pairs: list[tuple[str, str]] = []
    date_from_h = hy(date_from)
    date_to_h = hy(date_to)
    for i, d in enumerate(dates):
        if d < date_from_h or d > date_to_h:
            continue
        # 前営業日を探す（= predict_date）
        for j in range(i - 1, -1, -1):
            if dates[j] < d:
                pairs.append((dates[j].replace("-", ""), d.replace("-", "")))
                break
    return pairs


def _get_prev_business_day(actual_date: str) -> str:
    """actual_date の直前営業日を BQ の株価テーブルから算出する."""
    ah = hy(actual_date)
    q = f"""SELECT MAX(DATE) AS prev_bd
    FROM `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS`
    WHERE DATE < '{ah}'"""
    df = bq.query(q).to_dataframe()
    if df.empty or pd.isna(df.iloc[0]["prev_bd"]):
        raise ValueError(f"{actual_date} の前営業日が見つかりません")
    return str(df.iloc[0]["prev_bd"]).replace("-", "")[:8]


def cmd_today(args: argparse.Namespace) -> None:
    """今日を答え合わせ日として predict + answer を連続実行する.

    Args:
        args: argparse.Namespace with actual_date (YYYYMMDD).

    Raises:
        ValueError: 前営業日が BQ から算出できない場合。
        SystemExit: 株価データ未投入で answer が実行できない場合
            （predict は成功済みの旨をログ出力してから exit）。
    """
    actual_date = args.actual_date
    predict_date = _get_prev_business_day(actual_date)
    log.info("today_mode", predict_date=predict_date, actual_date=actual_date)

    pred_args = argparse.Namespace(date=predict_date)
    cmd_predict(pred_args)
    log.info("predict_completed", predict_date=predict_date)

    try:
        answer_args = argparse.Namespace(date=predict_date, actual_date=actual_date)
        cmd_answer(answer_args)
    except SystemExit as e:
        if e.code != 0:
            log.warning(
                "answer_skipped",
                predict_date=predict_date,
                actual_date=actual_date,
                msg=f"predict は完了済み。株価投入後に実行: "
                    f"answer --date {predict_date} --actual-date {actual_date}",
            )
            raise


def _resolve_backfill_range() -> tuple[str, str]:
    """GCS の既存 prediction/actual ファイルから答え合わせ日（actual_date）の範囲を自動算出する."""
    pred_blobs = gcs_list_blobs(GCS_PREDICTIONS_PREFIX)
    actual_blobs = gcs_list_blobs(GCS_ACTUALS_PREFIX)
    predict_dates: set[str] = set()
    for b in pred_blobs:
        parts = b.rsplit("/", 1)[-1].replace(".json", "").split("_")
        if len(parts) >= 2 and len(parts[1]) == 8 and parts[1].isdigit():
            predict_dates.add(parts[1])
    for b in actual_blobs:
        if "for_" in b:
            d = b.rsplit("for_", 1)[-1].replace(".json", "")
            if len(d) == 8 and d.isdigit():
                predict_dates.add(d)
    if not predict_dates:
        raise ValueError("GCS に既存データが見つかりません")
    sorted_pds = sorted(predict_dates)
    min_pd_h = hy(sorted_pds[0])
    max_pd_h = hy(sorted_pds[-1])
    q = f"""WITH dates AS (
      SELECT DISTINCT DATE FROM `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS`
      WHERE DATE BETWEEN '{min_pd_h}' AND DATE_ADD('{max_pd_h}', INTERVAL 10 DAY)
    )
    SELECT
      (SELECT MIN(d2.DATE) FROM dates d2 WHERE d2.DATE > '{min_pd_h}') AS min_actual,
      (SELECT MIN(d2.DATE) FROM dates d2 WHERE d2.DATE > '{max_pd_h}') AS max_actual"""
    df = bq.query(q).to_dataframe()
    raw_min = df.iloc[0]["min_actual"]
    raw_max = df.iloc[0]["max_actual"]
    if pd.isna(raw_min) or pd.isna(raw_max):
        raise ValueError(
            f"翌営業日が算出できません（min_pd={sorted_pds[0]}, max_pd={sorted_pds[-1]}）。"
            "株価データが最新日まで投入されているか確認してください"
        )
    min_actual = str(raw_min).replace("-", "")[:8]
    max_actual = str(raw_max).replace("-", "")[:8]
    return min_actual, max_actual


def cmd_backfill(args: argparse.Namespace) -> None:
    """過去日付のバッチ再実行."""
    date_from = getattr(args, "from")
    date_to = args.to
    if bool(date_from) != bool(date_to):
        log.error("partial_range", date_from=date_from, date_to=date_to)
        raise ValueError("--from と --to は両方指定するか、両方省略してください")
    if not date_from:
        date_from, date_to = _resolve_backfill_range()
        log.info("backfill_auto_range", date_from=date_from, date_to=date_to)
    log.info("backfill_start", date_from=date_from, date_to=date_to)

    # 営業日ペアを生成
    pairs = _get_business_day_pairs(date_from, date_to)
    if not pairs:
        log.warning("no_business_days", date_from=date_from, date_to=date_to)
        return
    log.info("business_day_pairs", count=len(pairs))

    date_min_predict = pairs[0][0]
    date_max_actual = pairs[-1][1]
    shared = fetch_shared_data(date_min_predict, date_to, date_max_actual)
    log.info("shared_data_fetched")

    summaries: list[dict] = []
    for pd_date, ac_date in pairs:
        log.info("backfill_day", predict=pd_date, actual=ac_date)
        df_feat = compute_features(pd_date, shared)
        if df_feat is None or df_feat.empty:
            log.info("skip_no_data", date=pd_date)
            continue

        # スコアリング
        scores, preds, all_reasons = [], [], []
        for _, row in df_feat.iterrows():
            s, r = compute_score(row)
            scores.append(s)
            preds.append(score_to_prediction(s))
            all_reasons.append(" / ".join(r) if r else "シグナルなし")
        df_feat["score"] = scores
        df_feat["prediction"] = preds
        df_feat["reasons"] = all_reasons

        now = datetime.now(tz=JST)

        # prediction 保存
        pred_records = df_feat[PRED_COLUMNS].to_dict(orient="records")
        pred_payload = {
            "predict_date": pd_date,
            "created_at": now.isoformat(),
            "count": len(pred_records),
            "predictions": pred_records,
        }
        pred_path = f"{GCS_PREDICTIONS}/prediction_{pd_date}_{now.strftime('%H%M%S')}.json"
        gcs_save_json(pred_payload, pred_path)

        # answer check
        ph = hy(pd_date)
        ah = hy(ac_date)
        dfp = shared["df_price"]

        df_intra = df_feat[df_feat["is_intraday"]].copy()
        df_intra["compare_before_close"] = pd.to_numeric(df_intra["prev_close"], errors="coerce")
        df_intra["compare_after_close"] = pd.to_numeric(df_intra["adj_close"], errors="coerce")
        df_intra["actual_return"] = (
            (df_intra["compare_after_close"] - df_intra["compare_before_close"])
            / df_intra["compare_before_close"]
        )

        df_after = df_feat[~df_feat["is_intraday"]].copy()
        actual_close_df = dfp[dfp["dt"] == ah][["TICKER", "ADJ_CLOSE"]].rename(
            columns={"ADJ_CLOSE": "compare_after_close"}
        )
        predict_close_df = dfp[dfp["dt"] == ph][["TICKER", "ADJ_CLOSE"]].rename(
            columns={"ADJ_CLOSE": "compare_before_close"}
        )
        df_after = df_after.merge(predict_close_df, left_on="ticker", right_on="TICKER", how="left").drop(columns=["TICKER"], errors="ignore")
        df_after = df_after.merge(actual_close_df, left_on="ticker", right_on="TICKER", how="left").drop(columns=["TICKER"], errors="ignore")
        df_after["actual_return"] = (
            (df_after["compare_after_close"] - df_after["compare_before_close"])
            / df_after["compare_before_close"]
        )

        cols = [c for c in df_feat.columns] + ["compare_before_close", "compare_after_close", "actual_return"]
        df_compare = pd.concat(
            [df_intra[[c for c in cols if c in df_intra.columns]],
             df_after[[c for c in cols if c in df_after.columns]]],
            ignore_index=True,
        )

        df_compare["actual_category"] = df_compare["actual_return"].apply(
            lambda x: classify_return(x) if pd.notna(x) else None
        )
        dir_map_local = {"UP": 1, "NEUTRAL": 0, "DOWN": -1}
        df_compare["pred_dir"] = df_compare["prediction"].map(dir_map_local)
        df_compare["actual_dir"] = df_compare["actual_category"].map(dir_map_local)
        df_compare["direction_match"] = df_compare["pred_dir"] == df_compare["actual_dir"]

        matched = df_compare["actual_return"].notna()
        n_matched = int(matched.sum())
        dir_acc = float(df_compare.loc[matched, "direction_match"].mean()) if n_matched else 0.0
        score_ret_corr = (
            float(df_compare.loc[matched, ["score", "actual_return"]].corr().iloc[0, 1])
            if n_matched > 1
            else 0.0
        )
        log.info("day_result", date=pd_date, matched=n_matched,
                 dir_acc=f"{dir_acc:.1%}", corr=f"{score_ret_corr:.3f}")

        actual_records = df_compare[[
            "ticker", "name", "quarter", "is_intraday", "score", "prediction",
            "actual_return", "actual_category", "direction_match",
            "compare_before_close", "compare_after_close",
        ]].to_dict(orient="records")

        actual_payload = {
            "predict_date": pd_date,
            "actual_date": ac_date,
            "created_at": now.isoformat(),
            "count": len(actual_records),
            "direction_accuracy": dir_acc if n_matched else None,
            "score_return_correlation": score_ret_corr if n_matched else None,
            "actuals": actual_records,
        }
        actual_path = (
            f"{GCS_ACTUALS}/actual_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}_for_{pd_date}.json"
        )
        gcs_save_json(actual_payload, actual_path)

        summaries.append({
            "predict_date": pd_date,
            "actual_date": ac_date,
            "n_matched": n_matched,
            "direction_accuracy": dir_acc,
            "score_return_correlation": score_ret_corr,
        })

    # accuracy summary 更新
    _update_accuracy_summary()

    # 日別サマリ出力
    print("\n━━━ 日別サマリ ━━━")
    for s in summaries:
        print(
            f"  {s['predict_date']}→{s['actual_date']}: "
            f"n={s['n_matched']}  dir_acc={s['direction_accuracy']:.1%}  "
            f"corr={s['score_return_correlation']:.3f}"
        )


# ══════════════ サブコマンド: accuracy ══════════════


def _update_accuracy_summary() -> None:
    """GCS 全期間の actual を集計し accuracy_summary.json を更新する."""
    actual_blobs = [
        b for b in gcs_list_blobs(GCS_ACTUALS_PREFIX) if b.endswith(".json")
    ]
    by_pd: dict[str, str] = {}
    for b in sorted(actual_blobs):
        name = b.rsplit("/", 1)[-1].replace(".json", "")
        parts = name.split("_")
        if "for" in parts:
            pd_key = parts[parts.index("for") + 1]
        else:
            pd_key = parts[1] if len(parts) >= 2 else name
        by_pd[pd_key] = b

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
        log.warning("no_actual_data")
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
    log.info("accuracy_updated", total=len(df_valid), dates=summary["num_dates"],
             dir_acc=f"{dir_acc_total:.1%}", corr=f"{corr:.3f}")


def cmd_accuracy(args: argparse.Namespace) -> None:
    """精度集計を実行する."""
    log.info("accuracy_start")
    _update_accuracy_summary()


# ══════════════ MAIN ══════════════


def main() -> None:
    parser = argparse.ArgumentParser(
        description="決算反応予測モデル — 統合 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # predict
    p_predict = sub.add_parser("predict", help="当日予測を実行")
    p_predict.add_argument("--date", required=True, help="予測対象日 (YYYYMMDD)")

    # answer
    p_answer = sub.add_parser("answer", help="答え合わせを実行")
    p_answer.add_argument("--date", required=True, help="予測対象日 (YYYYMMDD)")
    p_answer.add_argument("--actual-date", required=True, help="翌営業日 (YYYYMMDD)")

    # backfill
    p_backfill = sub.add_parser("backfill", help="過去日付のバッチ再実行")
    p_backfill.add_argument("--from", required=False, help="開始答え合わせ日 (YYYYMMDD)。省略時=GCS既存全期間")
    p_backfill.add_argument("--to", required=False, help="終了答え合わせ日 (YYYYMMDD)。省略時=GCS既存全期間")

    # today
    p_today = sub.add_parser("today", help="今日の答え合わせ（前営業日を自動算出）")
    p_today.add_argument("--actual-date", required=True, help="答え合わせ日=今日 (YYYYMMDD)")

    # accuracy
    sub.add_parser("accuracy", help="精度集計")

    args = parser.parse_args()

    t0 = time.time()
    if args.command == "predict":
        cmd_predict(args)
    elif args.command == "answer":
        cmd_answer(args)
    elif args.command == "today":
        cmd_today(args)
    elif args.command == "backfill":
        cmd_backfill(args)
    elif args.command == "accuracy":
        cmd_accuracy(args)

    log.info("done", elapsed=f"{time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""ザラバ決算リアクションツール.

事前準備を BQ で行い、ザラバ中の決算発表を TDnet 適時開示ポーリング +
XBRL 数値抽出でリアルタイム検知し、スコアリングで買い/売り候補を表示する。

サブコマンド:
  prepare  : 事前準備（BQ から銘柄情報キャッシュ取得）
  catchup  : 指定時刻までの決算短信を TDnet から取得しスコアリング + results 追記
  watch    : ザラバ監視（TDnet ポーリング + XBRL 抽出 + スコアリング + rich Live 表示）
  review   : 過去 watch/catchup 結果を時系列表示

設計ドラフト: docs/plans/20260405_zaraba_earnings_tool.md
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from typing import TypedDict

import jpholiday
import pandas as pd
import structlog

# ── ザラバ決算モニター表の列幅（横幅調整はここ） ──────
WATCH_TABLE_WIDTH_TIME = 6
WATCH_TABLE_WIDTH_SCORE = 5
WATCH_TABLE_WIDTH_CODE = 5
WATCH_TABLE_WIDTH_NAME = 14
WATCH_TABLE_WIDTH_CAP = 5
WATCH_TABLE_WIDTH_Q = 4
WATCH_TABLE_WIDTH_JUDGE = 6
WATCH_TABLE_WIDTH_POS = 22
WATCH_TABLE_WIDTH_NEG = 22

# ── パスを通す ──────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_SCRIPT_DIR))
sys.path.insert(0, str(_PROJECT_ROOT))

from jquants_common import jquants_get
from src.core.config import settings  # noqa: E402

# ── ロギング（structlog 25.x 対応） ─────────────────
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)
log = structlog.get_logger(__name__)

# ── 定数 ────────────────────────────────────────────
JST = timezone(timedelta(hours=+9), "JST")
PROJECT_ID = "gmailpj-357912"
DATASET = "STOCK"

CACHE_BASE = Path.home() / "zaraba_cache" if sys.platform != "win32" else Path(r"C:\tmp\zaraba_cache")
CONSENSUS_CACHE_DIR = CACHE_BASE  # consensus は日付フォルダの外

# ポーリング設定
POLL_INTERVAL_SEC = 1.0       # 初期ポーリング間隔
POLL_BACKOFF_MAX_SEC = 30.0   # 429 バックオフ上限
POLL_BACKOFF_FACTOR = 2.0     # バックオフ倍率
# 指定時間前後の高速ポーリング設定
POLL_BURST_SEC = 0.05         # 指定時間±15秒の超高速ポーリング
POLL_FAST_SEC = 0.2           # 指定時間+15s〜+60s の高速ポーリング
POLL_BURST_WINDOW_SEC = 15    # 指定時間前後何秒を burst とするか
POLL_FAST_WINDOW_SEC = 60     # 指定時間後何秒を fast とするか

PREPARE_TARGET_SCHEDULED = "scheduled"
PREPARE_TARGET_ALL = "all"
PREPARE_DATA_FULL = "full"
PREPARE_DATA_CONSENSUS = "consensus"

# jpholiday が対応しない特別休日（大晦日・年始休暇）
_SPECIAL_HOLIDAYS: set[date] = {
    date(2026, 12, 31),
    date(2027,  1,  2),
    date(2027,  1,  3),
}


def _is_trading_day(d: date) -> bool:
    """取引日なら True（土日・祝日・特別休日を除く）."""
    return d.weekday() < 5 and not jpholiday.is_holiday(d) and d not in _SPECIAL_HOLIDAYS


def _next_trading_day(d: date) -> date:
    """d の翌取引日を返す."""
    d = d + timedelta(days=1)
    while not _is_trading_day(d):
        d += timedelta(days=1)
    return d


def _prev_trading_day(d: date) -> date:
    """d の前取引日を返す."""
    d = d - timedelta(days=1)
    while not _is_trading_day(d):
        d -= timedelta(days=1)
    return d


def resolve_date(value: str) -> str:
    """日付ショートカットを YYYYMMDD に変換する.

    Args:
        value: "t" (今日), "p" (前取引日), "n" (次取引日), または YYYYMMDD

    Returns:
        YYYYMMDD 形式の文字列
    """
    today = datetime.now(JST).date()
    shortcut = value.strip().lower()
    if shortcut == "t":
        return today.strftime("%Y%m%d")
    elif shortcut == "p":
        return _prev_trading_day(today).strftime("%Y%m%d")
    elif shortcut == "n":
        return _next_trading_day(today).strftime("%Y%m%d")
    else:
        # バリデーション: YYYYMMDD 形式かチェック
        try:
            datetime.strptime(value, "%Y%m%d")
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"無効な日付: '{value}'。YYYYMMDD または t/p/n を指定してください"
            )
        return value


# ====================================================================
# BQ クライアント
# ====================================================================
def _get_bq_client():
    """BQ クライアントをサービスアカウントで取得."""
    from google.cloud import bigquery
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        settings.google_application_credentials
    )
    return bigquery.Client(project=PROJECT_ID, credentials=creds)


# ====================================================================
# キャッシュ管理
# ====================================================================
def _cache_dir(target_date: str) -> Path:
    """日付別キャッシュディレクトリを返す（なければ作成）."""
    d = CACHE_BASE / target_date
    d.mkdir(parents=True, exist_ok=True)
    return d


def _prior_data_path(target_date: str) -> Path:
    """全銘柄 prepare 用の共有キャッシュパス。

    target_date は互換用に受け取るが、全銘柄の BQ コストを避けるため日付別にはしない。
    日付ごとの状態管理が必要な seen/results は引き続き _cache_dir(target_date) 配下。
    決算予定銘柄 prepare は日ごとに対象が変わるため、従来通り日付別キャッシュを使う。
    """
    return CACHE_BASE / "prior_data.json"


def _prepare_meta_path(target_date: str) -> Path:
    return CACHE_BASE / "prepare_meta.json"


def _calendar_path(target_date: str) -> Path:
    return _cache_dir(target_date) / "calendar.csv"


def _seen_path(target_date: str) -> Path:
    return _cache_dir(target_date) / "seen_disc_nos.json"


def _results_path(target_date: str) -> Path:
    return _cache_dir(target_date) / "results.csv"


def _master_all_path(target_date: str) -> Path:
    return CACHE_BASE / "master_all.csv"


def _legacy_prior_data_path(target_date: str) -> Path:
    return _cache_dir(target_date) / "prior_data.json"


def _legacy_prepare_meta_path(target_date: str) -> Path:
    return _cache_dir(target_date) / "prepare_meta.json"


def _legacy_master_all_path(target_date: str) -> Path:
    return _cache_dir(target_date) / "master_all.csv"


def _load_seen(target_date: str) -> dict:
    """DiscNo キャッシュを読み込む."""
    p = _seen_path(target_date)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {"jquants": []}


def _save_seen(target_date: str, seen: dict) -> None:
    """DiscNo キャッシュを保存."""
    with open(_seen_path(target_date), "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


# ====================================================================
# Phase 1: prepare — 事前準備
# ====================================================================
def cmd_prepare(
    target_date: str,
    force: bool = False,
    target: str = PREPARE_TARGET_SCHEDULED,
    data: str = PREPARE_DATA_FULL,
) -> None:
    """事前準備: BQ から対象銘柄と事前情報を取得してキャッシュ."""
    if target not in {PREPARE_TARGET_SCHEDULED, PREPARE_TARGET_ALL}:
        raise ValueError(f"invalid prepare target: {target}")
    if data not in {PREPARE_DATA_FULL, PREPARE_DATA_CONSENSUS}:
        raise ValueError(f"invalid prepare data: {data}")

    log.info("prepare_start", date=target_date, force=force, target=target, data=data)

    if target == PREPARE_TARGET_ALL:
        prior_path = _prior_data_path(target_date)
        meta_path = _prepare_meta_path(target_date)
    else:
        prior_path = _legacy_prior_data_path(target_date)
        meta_path = _legacy_prepare_meta_path(target_date)
    cal_path = _calendar_path(target_date)

    if data == PREPARE_DATA_FULL and prior_path.exists() and not force:
        cached_target = PREPARE_TARGET_SCHEDULED
        if meta_path.exists():
            with open(meta_path, encoding="utf-8") as f:
                cached_target = json.load(f).get("target", PREPARE_TARGET_SCHEDULED)
        if cached_target == target:
            log.info("cache_exists", path=str(prior_path), target=target)
            print(f"キャッシュ済み: {prior_path}")
            print("再取得するには --force を指定してください")
            if target == PREPARE_TARGET_ALL:
                print(f"全銘柄共有キャッシュのため指定日 {target_date} では再取得しません")
            # キャッシュから読み込んでサマリー表示
            with open(prior_path, encoding="utf-8") as f:
                prior = json.load(f)
            _print_prepare_summary(target_date, prior, target)
            return
        log.info("cache_target_mismatch", cached_target=cached_target, requested_target=target)

    bq = _get_bq_client()
    ds = f"{PROJECT_ID}.{DATASET}"

    if data == PREPARE_DATA_CONSENSUS:
        df_conse = _load_or_fetch_consensus(bq, force=force)
        updated = _refresh_prior_consensus(target_date, target, df_conse)
        print(f"コンセンサスのみ取得完了: {len(df_conse)}件")
        if updated is not None:
            print(f"prior_data.json のコンセンサス更新: {updated}銘柄")
        return

    # ── 1-1. ターゲット銘柄取得 ─────────────────────────
    # YYYYMMDD → YYYY-MM-DD
    d = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]}"
    cal_sql = f"""
    SELECT DISTINCT
        TICKER,
        DISCLOSURE_TIME,
        QUARTER,
        CATEGORY
    FROM `{ds}.EARNINGS_DISCLOSURE_CALENDAR`
    WHERE DISCLOSURE_DATE = '{d}'
      AND RECORD_TYPE = 'S'
      AND CATEGORY = 'R'
    ORDER BY DISCLOSURE_TIME, TICKER
    """
    df_cal = bq.query(cal_sql).to_dataframe()

    if target == PREPARE_TARGET_SCHEDULED and df_cal.empty:
        print(f"対象日 {d} の決算予定銘柄が見つかりません")
        log.warning("no_earnings_scheduled", date=d)
        return

    if target == PREPARE_TARGET_ALL:
        target_sql = f"""
        SELECT DISTINCT TICKER
        FROM `{ds}.STOCK_CODE_LIST`
        WHERE REGEXP_CONTAINS(CAST(TICKER AS STRING), r'^[0-9]{{4}}$')
        ORDER BY TICKER
        """
        df_target = bq.query(target_sql).to_dataframe()
        if df_target.empty:
            print("全銘柄マスタが見つかりません")
            log.warning("no_master_tickers")
            return
        tickers = df_target["TICKER"].astype(str).str[:4].unique().tolist()
    else:
        tickers = df_cal["TICKER"].astype(str).str[:4].unique().tolist()
    ticker_csv = ", ".join(f"'{t}'" for t in tickers)
    log.info("target_tickers", target=target, count=len(tickers))

    # ── 1-2. 全データを BQ から一括取得 ───────────────────
    target_label = "全銘柄" if target == PREPARE_TARGET_ALL else "決算予定銘柄"
    print(f"BQ から {target_label} {len(tickers)} 銘柄分のデータを取得中...")

    # (1) 会社予想（通期）+ 前回発表予想: 最新レコード
    # 銘柄ごとに「直近2件」のみ取得（最新=iloc[0]、その前=iloc[1]）
    # PARTITION BY (ticker, Q, FY_END) だと1Qが先頭に来るバグで誤値を拾うため、
    # 銘柄単位の DISCLOSED_DATE DESC で絞る
    fin_sql = f"""
    SELECT *
    FROM `{ds}.fin_summary`
    WHERE LOCAL_CODE IN ({ticker_csv})
      AND TYPE_OF_DOCUMENT LIKE '%FinancialStatements%'
    QUALIFY ROW_NUMBER() OVER (PARTITION BY LOCAL_CODE ORDER BY DISCLOSED_DATE DESC) <= 2
    ORDER BY LOCAL_CODE, DISCLOSED_DATE DESC
    """

    # (2) 事前修正有無
    revision_sql = f"""
    SELECT
        LOCAL_CODE AS TICKER,
        DISCLOSED_DATE,
        TYPE_OF_DOCUMENT
    FROM `{ds}.fin_summary`
    WHERE LOCAL_CODE IN ({ticker_csv})
      AND TYPE_OF_DOCUMENT LIKE '%Revision%'
      AND DISCLOSED_DATE >= DATE_SUB('{d}', INTERVAL 90 DAY)
    ORDER BY LOCAL_CODE, DISCLOSED_DATE DESC
    """

    # (3) QoQ + 前年同期
    qoq_sql = f"""
    SELECT *
    FROM `{ds}.v_fin_summary_actual_for_q_on_q`
    WHERE LOCAL_CODE IN ({ticker_csv})
      AND CURRENT_FISCAL_YEAR_START_DATE >= DATE_SUB('{d}', INTERVAL 2 YEAR)
    ORDER BY LOCAL_CODE, CURRENT_PERIOD_END_DATE DESC
    """

    # (4) 直近株価 + 出来高（20日分）
    price_sql = f"""
    SELECT
        TICKER, DATE AS dt,
        ADJ_CLOSE AS close, VOLUME AS volume
    FROM `{ds}.STOCK_PRICE_JQUANTS`
    WHERE TICKER IN ({ticker_csv})
      AND IS_PREFERRED = FALSE
      AND DATE >= DATE_SUB('{d}', INTERVAL 30 DAY)
    ORDER BY TICKER, DATE DESC
    """

    # (5) 信用残（最新）
    margin_sql = f"""
    SELECT
        TICKER, YEARDATE AS dt,
        LEND_BAL_STOCKS AS sell_balance,
        FIN_BAL_STOCKS AS buy_balance
    FROM `{ds}.MARGIN_BALANCE`
    WHERE TICKER IN ({ticker_csv})
    QUALIFY ROW_NUMBER() OVER (PARTITION BY TICKER ORDER BY YEARDATE DESC) = 1
    """

    # (6) 銘柄マスタ
    master_sql = f"""
    SELECT DISTINCT
        TICKER, STOCK_NAME, INDUSTRY_33_CATEGORY, SIZE_CODE, MARKET_CATEGORY
    FROM `{ds}.STOCK_CODE_LIST`
    WHERE TICKER IN ({ticker_csv})
    """

    # (7) 時価総額（YF_STOCK_INFO の最新ロード分）
    mcap_sql = f"""
    SELECT TICKER, MARKET_CAP
    FROM `{ds}.YF_STOCK_INFO`
    WHERE TICKER IN ({ticker_csv})
    QUALIFY ROW_NUMBER() OVER (PARTITION BY TICKER ORDER BY LOADED_AT DESC) = 1
    """

    # 一括実行
    queries = {
        "fin_summary": fin_sql,
        "revisions": revision_sql,
        "qoq": qoq_sql,
        "price": price_sql,
        "margin": margin_sql,
        "master": master_sql,
        "mcap": mcap_sql,
    }

    raw: dict[str, pd.DataFrame] = {}
    for name, sql in queries.items():
        log.info("bq_query", name=name)
        raw[name] = bq.query(sql).to_dataframe()
        log.info("bq_result", name=name, rows=len(raw[name]))

    # ── コンセンサス（別管理） ─────────────────────────
    df_conse = _load_or_fetch_consensus(bq, force=force)

    # ── β20d（GCS） ────────────────────────────────
    beta_map = _load_beta_20d()

    # ── 事前情報を銘柄単位に集約 ──────────────────────
    prior = _build_prior_data(
        tickers=tickers,
        df_cal=df_cal,
        df_fin=raw["fin_summary"],
        df_rev=raw["revisions"],
        df_qoq=raw["qoq"],
        df_price=raw["price"],
        df_margin=raw["margin"],
        df_master=raw["master"],
        df_mcap=raw["mcap"],
        df_conse=df_conse,
        beta_map=beta_map,
    )

    # ── マスタ全件キャッシュ（カレンダー外銘柄の名前引き用） ──
    master_all_sql = f"""
    SELECT DISTINCT TICKER, STOCK_NAME
    FROM `{ds}.STOCK_CODE_LIST`
    """
    df_master_all = bq.query(master_all_sql).to_dataframe()
    log.info("bq_result", name="master_all", rows=len(df_master_all))
    df_master_all.to_csv(_master_all_path(target_date), index=False, encoding="utf-8")

    # ── 保存 ─────────────────────────────────────
    df_cal.to_csv(cal_path, index=False, encoding="utf-8")
    with open(prior_path, "w", encoding="utf-8") as f:
        json.dump(prior, f, ensure_ascii=False, indent=2, default=str)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "date": target_date,
                "target": target,
                "data": data,
                "ticker_count": len(tickers),
                "created_at": datetime.now(JST).isoformat(),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    log.info("prepare_done", target=target, tickers=len(tickers), cache=str(prior_path))
    _print_prepare_summary(target_date, prior, target)


def _load_or_fetch_consensus(bq, force: bool = False) -> pd.DataFrame:
    """コンセンサス: ローカルキャッシュの DATAAT と BQ の MAX(DATAAT) を比較し、必要時のみ全件取得."""
    ds = f"{PROJECT_ID}.{DATASET}"
    CONSENSUS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ローカルキャッシュを探す
    existing = sorted(CONSENSUS_CACHE_DIR.glob("consensus_*.csv"), reverse=True)
    local_dataat: str | None = None
    local_path: Path | None = None
    if existing:
        local_path = existing[0]
        # ファイル名から DATAAT 抽出: consensus_YYYYMMDD.csv
        local_dataat = local_path.stem.replace("consensus_", "")

    # BQ の最新 DATAAT を確認（軽量クエリ）
    max_sql = f"SELECT FORMAT_DATE('%Y%m%d', MAX(DATAAT)) AS max_dataat FROM `{ds}.V_CONSENSUS_MERGED`"
    df_max = bq.query(max_sql).to_dataframe()
    bq_dataat = df_max["max_dataat"].iloc[0] if not df_max.empty else None

    if not force and local_dataat and bq_dataat and local_dataat == bq_dataat:
        log.info("consensus_cache_hit", dataat=local_dataat)
        return pd.read_csv(local_path, encoding="utf-8")

    # 全件取得
    log.info("consensus_fetch", bq_dataat=bq_dataat, local_dataat=local_dataat)
    conse_sql = f"""
    SELECT TICKER, FY, QUARTER, DATAAT,
           REVENUE, OP_PROFIT, ORD_PROFIT, NET_PROFIT, EPS
    FROM `{ds}.V_CONSENSUS_MERGED`
    """
    df = bq.query(conse_sql).to_dataframe()

    # 古いキャッシュを削除して新規保存
    for old in CONSENSUS_CACHE_DIR.glob("consensus_*.csv"):
        old.unlink()
    new_path = CONSENSUS_CACHE_DIR / f"consensus_{bq_dataat}.csv"
    df.to_csv(new_path, index=False, encoding="utf-8")
    log.info("consensus_cached", path=str(new_path), rows=len(df))

    return df


def _refresh_prior_consensus(
    target_date: str,
    target: str,
    df_conse: pd.DataFrame,
) -> int | None:
    """既存 prior_data.json の consensus_profit だけを最新コンセンサスで差し替える.

    --data consensus は「コンセのみ更新」の指定なので、元CSVだけでなく watch が読む
    prior_data.json 内のスナップショットも更新する。prior が無い場合は何もしない。
    """
    prior_path = (
        _prior_data_path(target_date)
        if target == PREPARE_TARGET_ALL
        else _legacy_prior_data_path(target_date)
    )
    if not prior_path.exists():
        log.info("prior_consensus_refresh_skipped", reason="prior_not_found", path=str(prior_path))
        return None

    with open(prior_path, encoding="utf-8") as f:
        prior = json.load(f)

    if df_conse.empty or "ORD_PROFIT" not in df_conse.columns:
        log.warning("prior_consensus_refresh_skipped", reason="no_consensus_data")
        return 0

    df_local = df_conse.copy()
    df_local["TICKER"] = df_local["TICKER"].astype(str).str[:4]

    # schema v3: ticker → ConsensusFields（FY判定は prior 内の prev_disc_type/prev_disc_fy_end で実施）
    V2_KEYS = ("consensus_profit_by_q", "consensus_profit_unit",
               "consensus_profit_next", "consensus_profit_next_fy")
    V3_KEYS = ("consensus_by_q", "consensus_next", "consensus_next_fy")
    updated = 0
    for ticker, info in prior.items():
        if not isinstance(info, dict):
            continue
        # 旧キーを全除去
        info.pop("consensus_profit", None)
        for k in V2_KEYS:
            info.pop(k, None)
        for k in V3_KEYS:
            info.pop(k, None)

        tk = str(ticker)[:4]
        conse_t = df_local[df_local["TICKER"] == tk]
        if conse_t.empty:
            continue
        current_fy = _derive_current_fy(
            info.get("prev_disc_type"), info.get("prev_disc_fy_end")
        )
        fields = _consensus_to_prior_fields(conse_t, current_fy)
        for k, v in fields.items():
            info[k] = v
        if "consensus_by_q" in fields:
            updated += 1

    with open(prior_path, "w", encoding="utf-8") as f:
        json.dump(prior, f, ensure_ascii=False, indent=2, default=str)
    log.info("prior_consensus_refreshed", path=str(prior_path), updated=updated)
    return updated


def _load_beta_20d() -> dict[str, float]:
    """GCS から beta_20d.csv を読み込み TICKER → beta_20d の辞書を返す."""
    try:
        from google.cloud import storage
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(
            str(settings.google_application_credentials)
        )
        client = storage.Client(project=PROJECT_ID, credentials=creds)
        bucket = client.bucket("stock_data_1930932")
        blob = bucket.blob("earnings_model/zaraba_beta_20d/beta_20d.csv")

        local_path = CACHE_BASE / "beta_20d.csv"
        local_path.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(local_path))

        df = pd.read_csv(local_path, encoding="utf-8")
        beta_map: dict[str, float] = {}
        for _, row in df.iterrows():
            tk = str(row.get("TICKER", ""))[:4]
            b = _to_num(row.get("beta_20d"))
            if tk and b is not None:
                beta_map[tk] = b
        log.info("beta_20d_loaded", count=len(beta_map))
        return beta_map
    except Exception:
        log.warning("beta_20d_load_failed", exc_info=True)
        return {}


def _fetch_topix_realtime() -> float:
    """JPX 公式 JSON から TOPIX 前日比(%) をリアルタイム取得.

    https://www.jpx.co.jp/markets/indices/realvalues/01.html のバックエンド JSON。
    ザラ場中は直近値、引け後は終値ベースの前日比が返る。
    """
    import urllib.request

    url = "https://www.jpx.co.jp/market/indices/indices_stock_price3.1.txt"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        topix = data.get("MainStockIndex", {}).get("Topix", {})
        ratio_str = topix.get("previousDayRatio", "0").replace(",", "")
        ret = float(ratio_str) / 100  # % → 小数 (-0.04 → -0.0004)
        log.info("topix_realtime", ratio_pct=ratio_str, ret=f"{ret:+.4f}")
        return ret
    except Exception:
        log.warning("topix_realtime_failed", exc_info=True)
        return 0.0


def _build_prior_data(
    tickers: list[str],
    df_cal: pd.DataFrame,
    df_fin: pd.DataFrame,
    df_rev: pd.DataFrame,
    df_qoq: pd.DataFrame,
    df_price: pd.DataFrame,
    df_margin: pd.DataFrame,
    df_master: pd.DataFrame,
    df_mcap: pd.DataFrame,
    df_conse: pd.DataFrame,
    beta_map: dict[str, float] | None = None,
) -> dict:
    """銘柄単位の事前情報辞書を構築."""
    prior: dict = {}
    total = len(tickers)

    for i, ticker in enumerate(tickers):
        info: dict = {"ticker": ticker}

        # カレンダー
        cal = df_cal[df_cal["TICKER"] == ticker]
        if not cal.empty:
            row = cal.iloc[0]
            info["disc_time"] = str(row.get("DISCLOSURE_TIME", ""))
            info["quarter"] = str(row.get("QUARTER", ""))

        # 銘柄マスタ
        m = df_master[df_master["TICKER"] == ticker]
        if not m.empty:
            mr = m.iloc[0]
            info["name"] = str(mr.get("STOCK_NAME", ""))
            info["sector"] = str(mr.get("INDUSTRY_33_CATEGORY", ""))
            info["size"] = str(mr.get("SIZE_CODE", ""))

        # 会社予想（最新 fin_summary）
        # LOCAL_CODE は5桁の場合あり → 先頭4桁で比較
        # SQL 側で DISCLOSED_DATE DESC 順に絞っているので iloc[0] が最新
        fin = df_fin[df_fin["LOCAL_CODE"].astype(str).str[:4] == ticker[:4]].sort_values(
            "DISCLOSED_DATE", ascending=False
        )
        if not fin.empty:
            latest = fin.iloc[0]
            # 1Q 発表の場合、直前の prior は前期FY 行になり FORECAST_OPERATING_PROFIT は NULL。
            # その場合は NEXT_YEAR_FORECAST_OPERATING_PROFIT（前期FY時点の当期予想）にフォールバック
            def _fin_forecast(col_fop: str, col_nx: str | None) -> float | None:
                v = _to_num(latest.get(col_fop))
                if v is None and col_nx is not None:
                    v = _to_num(latest.get(col_nx))
                return v

            info["forecast_op"] = _fin_forecast(
                "FORECAST_OPERATING_PROFIT", "NEXT_YEAR_FORECAST_OPERATING_PROFIT"
            )
            info["forecast_odp"] = _fin_forecast(
                "FORECAST_ORDINARY_PROFIT", "NEXT_YEAR_FORECAST_ORDINARY_PROFIT"
            )
            info["forecast_np"] = _fin_forecast(
                "FORECAST_PROFIT", "NEXT_YEAR_FORECAST_PROFIT"
            )
            info["forecast_sales"] = _fin_forecast(
                "FORECAST_NET_SALES", "NEXT_YEAR_FORECAST_NET_SALES"
            )
            info["forecast_div_ann"] = _fin_forecast(
                "FORECAST_DIVIDEND_PER_SHARE_ANNUAL",
                "NEXT_YEAR_FORECAST_DIVIDEND_PER_SHARE_ANNUAL",
            )
            info["prev_disc_type"] = str(latest.get("TYPE_OF_CURRENT_PERIOD", ""))
            info["prev_disc_fy_end"] = str(latest.get("CURRENT_FISCAL_YEAR_END_DATE", ""))
            # 前Qまでの累計実績 OP（Q単独算出用）
            # fin_summary の OPERATING_PROFIT = 直前開示時の累計値
            # 1Q 発表前（= prior が前期FY）の場合、累計は存在しないため None（=0扱いに）
            if str(latest.get("TYPE_OF_CURRENT_PERIOD", "")) == "FY":
                # 直前開示がFY。FY自体を再処理する場合にQ4単独算出が必要。
                # rn=2が3Qの場合のみ累計値を採用。それ以外（前年度FY等）はNoneフォールバック。
                if len(fin) >= 2 and str(fin.iloc[1].get("TYPE_OF_CURRENT_PERIOD", "")) == "3Q":
                    prev_row = fin.iloc[1]
                    info["prev_cumulative_op"] = _to_num(prev_row.get("OPERATING_PROFIT"))
                    info["prev_cumulative_np"] = _to_num(prev_row.get("PROFIT"))
                else:
                    info["prev_cumulative_op"] = None
                    info["prev_cumulative_np"] = None
            else:
                info["prev_cumulative_op"] = _to_num(latest.get("OPERATING_PROFIT"))
                info["prev_cumulative_np"] = _to_num(latest.get("PROFIT"))
            # 前回予想（rn=2: 同銘柄のさらに前の開示）
            if len(fin) >= 2:
                prev = fin.iloc[1]
                info["prev_forecast_op"] = _to_num(prev.get("FORECAST_OPERATING_PROFIT"))
                info["prev_forecast_np"] = _to_num(prev.get("FORECAST_PROFIT"))

        # 事前修正
        rev = df_rev[df_rev["TICKER"] == ticker]
        info["has_prior_revision"] = not rev.empty
        info["revision_count"] = len(rev)

        # QoQ（v_fin_summary_actual_for_q_on_q は Q単独変換済み）
        qoq = df_qoq[df_qoq["LOCAL_CODE"].astype(str).str[:4] == ticker[:4]]
        if not qoq.empty:
            latest_q = qoq.iloc[0]
            info["latest_q_op"] = _to_num(latest_q.get("OPERATING_PROFIT"))
            info["latest_q_np"] = _to_num(latest_q.get("PROFIT"))
            info["latest_q_quarter"] = str(latest_q.get("QUARTER", ""))
            info["latest_q_fy_start"] = str(latest_q.get("CURRENT_FISCAL_YEAR_START_DATE", ""))
            # 前年同期 Q単独をQUARTER別にマップ化
            # scoring 時は「今日の新開示の QUARTER」で引くため、前期FY全Qをまとめて保存
            fy_start = latest_q.get("CURRENT_FISCAL_YEAR_START_DATE")
            prior_fys = qoq[qoq["CURRENT_FISCAL_YEAR_START_DATE"] < fy_start]
            if not prior_fys.empty:
                prev_fy_start = prior_fys["CURRENT_FISCAL_YEAR_START_DATE"].max()
                prev_fy_rows = qoq[qoq["CURRENT_FISCAL_YEAR_START_DATE"] == prev_fy_start]
                op_map: dict[str, float | None] = {}
                np_map: dict[str, float | None] = {}
                for _, pyrow in prev_fy_rows.iterrows():
                    pq = str(pyrow.get("QUARTER", ""))
                    if pq:
                        op_map[pq] = _to_num(pyrow.get("OPERATING_PROFIT"))
                        np_map[pq] = _to_num(pyrow.get("PROFIT"))
                info["prev_year_q_op_map"] = op_map
                info["prev_year_q_np_map"] = np_map
                # 互換キー: latest_q の QUARTER に対応する前年同Q単独
                q_label_latest = str(latest_q.get("QUARTER", ""))
                info["prev_year_q_op"] = op_map.get(q_label_latest)
                info["prev_year_q_np"] = np_map.get(q_label_latest)

        # 成長ベースライン（過去FY YoY OP の median）
        fy_rows = qoq[qoq["QUARTER"] == "4Q"].sort_values(
            "CURRENT_FISCAL_YEAR_START_DATE", ascending=False,
        )
        if len(fy_rows) >= 3:  # 少なくとも3期分（2期分のYoY）
            fy_ops = fy_rows["OPERATING_PROFIT"].tolist()
            yoy_list: list[float] = []
            for j in range(len(fy_ops) - 1):
                cur_op, prev_op = fy_ops[j], fy_ops[j + 1]
                if cur_op is not None and prev_op is not None and not pd.isna(cur_op) and not pd.isna(prev_op) and prev_op != 0:
                    yoy_list.append(float((cur_op - prev_op) / abs(prev_op)))
            if yoy_list:
                info["baseline_yoy_op"] = float(pd.Series(yoy_list).median())

        # 株価 + 出来高
        pr = df_price[df_price["TICKER"] == ticker].head(20)
        if not pr.empty:
            latest_close = _to_num(pr.iloc[0].get("close"))
            oldest_close = _to_num(pr.iloc[-1].get("close")) if len(pr) >= 2 else None
            info["latest_close"] = latest_close
            if latest_close and oldest_close and oldest_close > 0:
                info["momentum_20d"] = round((latest_close - oldest_close) / oldest_close, 4)
            close_5d = _to_num(pr.iloc[4].get("close")) if len(pr) >= 5 else None
            if latest_close and close_5d and close_5d > 0:
                info["momentum_5d"] = round((latest_close - close_5d) / close_5d, 4)

            # 出来高: 直近5日平均 vs 20日平均
            volumes = pr["volume"].dropna().tolist()
            if len(volumes) >= 5:
                avg_5d = sum(volumes[:5]) / 5
                avg_20d = sum(volumes) / len(volumes)
                info["vol_avg_5d"] = avg_5d
                info["vol_avg_20d"] = avg_20d
                info["vol_ratio"] = round(avg_5d / avg_20d, 2) if avg_20d > 0 else None

        # 信用残
        mg = df_margin[df_margin["TICKER"] == ticker]
        if not mg.empty:
            mgr = mg.iloc[0]
            sell_bal = _to_num(mgr.get("sell_balance"))
            buy_bal = _to_num(mgr.get("buy_balance"))
            info["margin_sell"] = sell_bal
            info["margin_buy"] = buy_bal
            if sell_bal and buy_bal and sell_bal > 0:
                info["margin_ratio"] = round(buy_bal / sell_bal, 2)

        # コンセンサス（schema v3: 5項目対応 + FY判定ベース）
        conse = df_conse[df_conse["TICKER"] == ticker]
        if not conse.empty:
            # current_fy 導出: prev_disc_type/prev_disc_fy_end から対象FYを特定
            current_fy = _derive_current_fy(
                info.get("prev_disc_type"), info.get("prev_disc_fy_end")
            )
            for k, v in _consensus_to_prior_fields(conse, current_fy).items():
                info[k] = v

        # β20d（テーマブースト用。TOPIX当日リターンは watch 側でリアルタイム取得）
        if beta_map:
            b = beta_map.get(ticker[:4])
            if b is not None:
                info["beta_20d"] = b

        # 時価総額（YF_STOCK_INFO MARKET_CAP、円単位 → 億円）
        if df_mcap is not None and not df_mcap.empty:
            mc_rows = df_mcap[df_mcap["TICKER"].astype(str).str[:4] == ticker[:4]]
            if not mc_rows.empty:
                mc_yen = _to_num(mc_rows.iloc[0].get("MARKET_CAP"))
                if mc_yen is not None and mc_yen > 0:
                    info["market_cap_oku"] = int(round(mc_yen / 1e8))

        prior[ticker] = info
        if (i + 1) % 500 == 0 or (i + 1) == total:
            log.info("build_prior_progress", done=i + 1, total=total)

    return prior


def _to_num(v) -> float | None:
    """数値変換（None/NaN/空文字はNone）."""
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _s(v) -> str:
    """NaN/None 安全な文字列変換（CSV reload で空文字が NaN になる対策）."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v)


# ── コンセンサス schema v3: 5項目対応 + FY判定ベース（TARGET列廃止） ──────
_QUARTER_LABEL_MAP = {
    "本決算": "FY",
    "第1四半期": "1Q",
    "第１四半期": "1Q",
    "第2四半期": "2Q",
    "第２四半期": "2Q",
    "中間": "2Q",
    "第3四半期": "3Q",
    "第３四半期": "3Q",
}


def _normalize_quarter(q: str | None) -> str | None:
    """日本語/英語混在の quarter ラベルを {"1Q","2Q","3Q","FY"} に正規化."""
    if not q:
        return None
    q_str = str(q).strip()
    if q_str in {"1Q", "2Q", "3Q", "FY"}:
        return q_str
    return _QUARTER_LABEL_MAP.get(q_str)


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
        # FY 発表済み → 次の決算は翌年度1Q。current_fy = +12ヶ月
        y = int(fy_yyyymm[:4])
        m = fy_yyyymm[4:6]
        return f"{y + 1}{m}"
    return fy_yyyymm


_CONSENSUS_VALUE_COLS = ("REVENUE", "OP_PROFIT", "ORD_PROFIT", "NET_PROFIT", "EPS")


class ConsensusFields(TypedDict, total=False):
    """`prior_data.json` に書き込むコンセンサス関連フィールド (schema v3).

    v3: 5項目対応 + FY判定ベース（TARGET列廃止）。
    consensus_by_q は QUARTER → {項目名: 値} の nested dict。
    """
    consensus_by_q: dict[str, dict[str, float | None]]
    consensus_next: dict[str, float | None]
    consensus_next_fy: str


def _consensus_to_prior_fields(
    conse_one_ticker: pd.DataFrame,
    current_fy: str | None,
) -> ConsensusFields:
    """1 銘柄分の CONSENSUS DataFrame から prior に書き込むキー群を組み立てる.

    CURRENT/NEXT の判定は current_fy（対象FY の YYYYMM）で行う:
    - QUARTER in (1Q/2Q/3Q): 常に CURRENT（IFIS は当期のみ提供）
    - QUARTER == "FY" かつ FY == current_fy: CURRENT
    - QUARTER == "FY" かつ FY > current_fy の最小: NEXT

    current_fy は _build_prior_data 側で fin_summary の prev_disc_type / prev_disc_fy_end
    から導出して渡す（追加BQなし）。

    Args:
        conse_one_ticker: 1 銘柄分の V_CONSENSUS_MERGED 行。
        current_fy: 当期 FY の YYYYMM。None 時は FY 判定スキップ（1Q-3Q のみ格納）。
    """
    out: ConsensusFields = {}
    by_q: dict[str, dict[str, float | None]] = {}

    for _, row in conse_one_ticker.iterrows():
        q = str(row.get("QUARTER", "")).upper()
        fy = str(row.get("FY", ""))
        if q not in {"1Q", "2Q", "3Q", "FY"}:
            continue

        vals = {col: _to_num(row.get(col)) for col in _CONSENSUS_VALUE_COLS}
        if all(v is None for v in vals.values()):
            continue

        if q in {"1Q", "2Q", "3Q"}:
            if current_fy is None or fy == current_fy:
                by_q[q] = vals
        elif q == "FY" and current_fy:
            if fy == current_fy:
                by_q["FY"] = vals
            elif fy > current_fy:
                if "consensus_next" not in out or fy < out.get("consensus_next_fy", "9"):
                    out["consensus_next"] = vals
                    out["consensus_next_fy"] = fy

    if by_q:
        out["consensus_by_q"] = by_q
    return out


def _check_prior_schema(prior: dict) -> None:
    """prior_data.json のコンセスキーマ検査. 旧スキーマが過半数なら abort.

    v1 = `consensus_profit` のみ（iloc[0] バグ）
    v2 = `consensus_profit_by_q`（TARGET依存）
    v3 = `consensus_by_q`（5項目 nested dict、FY判定ベース）
    """
    legacy = 0
    current = 0
    for v in prior.values():
        if not isinstance(v, dict):
            continue
        if v.get("consensus_by_q") is not None:
            current += 1
        elif v.get("consensus_profit_by_q") is not None or v.get("consensus_profit") is not None:
            legacy += 1
    total = legacy + current
    if legacy > 0:
        log.warning(
            "prior_legacy_consensus_schema",
            legacy=legacy, current=current, total=total,
            hint="`prepare --data consensus --force` を実行して再生成してください",
        )
    if total > 0 and legacy / total > 0.5:
        raise SystemExit(
            f"旧コンセンサススキーマ {legacy}/{total} 件検出。"
            f"`PYTHONUTF8=1 python scripts/zaraba_earnings.py prepare "
            f"--date <today> --data consensus --force` を実行してから再起動してください。"
        )


def _print_prepare_summary(target_date: str, prior: dict, target: str = "") -> None:
    """事前サマリーをターミナルに表示."""
    d = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]}"
    items = sorted(prior.values(), key=lambda x: x.get("disc_time", "99:99"))
    print()
    print(f"=== {d} ザラバ決算 事前サマリー（{len(items)}銘柄） ===")

    if target == PREPARE_TARGET_ALL:
        print(f"（全銘柄モード: {len(items)}銘柄のキャッシュを作成済み）")
        print()
        return

    print(f"{'時刻':<7} | {'Code':<5} | {'銘柄名':<14} | {'Q':^4} | {'会社OP':>12} | {'コンセ':>10} | {'修正':^4} | {'折込':^6} | {'出来高':^6}")
    print("-" * 100)
    for item in items:
        t = item.get("disc_time", "??:??")[:5]
        code = item.get("ticker", "????")
        name = _s(item.get("name"))[:WATCH_TABLE_WIDTH_NAME // 2]
        q = item.get("quarter", "?")
        fop = item.get("forecast_op")
        fop_s = _fmt_yen(fop) if fop else "-"
        by_q = item.get("consensus_by_q") or {}
        q_label = _normalize_quarter(item.get("quarter")) or "FY"
        conse_data = by_q.get(q_label) or by_q.get("FY") if isinstance(by_q, dict) else None
        conse_key = "ORD_PROFIT"
        conse = conse_data.get(conse_key) if isinstance(conse_data, dict) else None
        conse_s = _fmt_yen(conse * 1e6) if conse else "-"
        rev = "有" if item.get("has_prior_revision") else "無"
        mom = item.get("momentum_20d")
        vol_r = item.get("vol_ratio")
        orikomi = "通常"
        if mom is not None and mom > 0.10 and not item.get("has_prior_revision"):
            orikomi = "高⚠"
        if vol_r is not None and vol_r > 2.0:
            orikomi = "急増⚠" if orikomi == "通常" else "高+急⚠"
        vol_s = f"x{vol_r:.1f}" if vol_r else "-"

        print(f"{t:<7} | {code:<5} | {name:<14} | {q:^4} | {fop_s:>12} | {conse_s:>10} | {rev:^4} | {orikomi:^6} | {vol_s:^6}")
    print()


def _fmt_yen(v: float | None) -> str:
    """円を兆/億 表示."""
    if v is None:
        return "-"
    av = abs(v)
    sign = "-" if v < 0 else ""
    if av >= 1e12:
        return f"{sign}{av / 1e12:.1f}兆"
    if av >= 1e8:
        return f"{sign}{av / 1e8:.0f}億"
    if av >= 1e4:
        return f"{sign}{av / 1e4:.0f}万"
    return f"{sign}{av:.0f}"


def _fmt_cap(oku: float | int | None) -> str:
    """時価総額（億円）を右詰め数字のみ、カンマ無しで表示."""
    if oku is None:
        return "-"
    try:
        return str(int(round(float(oku))))
    except (ValueError, TypeError):
        return "-"


_VERDICT_SHORT = {
    "STRONG_BUY": "S-Buy",
    "BUY": "N-Buy",
    "SLIGHT_BUY": "W-Buy",
    "NEUTRAL": "中立",
    "SLIGHT_SELL": "W-Sell",
    "SELL": "Sell",
}


def _short_verdict(verdict: str) -> str:
    """判定ラベルを略称化."""
    return _VERDICT_SHORT.get(verdict, verdict)


def _split_factors(factors: str | list[str]) -> tuple[str, str]:
    """因子リストを Pos / Neg に分離.

    プラス因子: 上方修正・増配・進捗↑・着地経↑・YoY+・翌期経↑・コンセ乖離+・翌コ純+・QoQ+・成長加速・
                自社株買い・記念配当・PEG割安・売り長・株式分割
    マイナス因子: 下方修正・減配・大幅減配・進捗↓・着地経↓・YoY-・通期予想非開示・翌期経↓・翌期予想非開示・コンセ乖離-・翌コ純-・QoQ-・
                成長減速・PEG割高・折込⚠・出来高x..⚠・出尽くし
    """
    if isinstance(factors, str):
        items = [f for f in factors.split(" ") if f]
    else:
        items = list(factors)
    NEG_MARKERS = (
        "下方", "減配", "進捗↓", "着地経↓", "通期予想非開示", "翌期経↓", "翌期予想非開示", "QoQ-", "成長減速", "PEG割高",
        "折込", "出来高", "出尽くし",
    )
    pos, neg = [], []
    for f in items:
        is_neg = False
        # 明示的な負の記号
        if any(m in f for m in NEG_MARKERS):
            is_neg = True
        # コンセ乖離・YoY は記号で判定
        elif f.startswith("コンセ乖離") or f.startswith("翌コ純") or f.startswith("YoY"):
            is_neg = "-" in f
        if is_neg:
            neg.append(f)
        else:
            pos.append(f)
    return " ".join(pos), " ".join(neg)


# ====================================================================
# Phase 2: catchup — TDnet ポーラーで指定時刻までの決算を一括取得 & スコアリング
# ====================================================================
def cmd_catchup(target_date: str, until_time: str) -> None:
    """指定時刻までの決算短信を TDnet から取得し、XBRL 抽出 & スコアリングして results に追加."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from zaraba_tdnet_poller import create_poller, XbrlExtractor

    log.info("catchup_start", date=target_date, until=until_time)

    # ── 事前キャッシュ読み込み（watch と同じ） ──
    prior_path = _prior_data_path(target_date)
    prior: dict = {}
    if prior_path.exists():
        with open(prior_path, encoding="utf-8") as f:
            prior = json.load(f)
    elif _legacy_prior_data_path(target_date).exists():
        with open(_legacy_prior_data_path(target_date), encoding="utf-8") as f:
            prior = json.load(f)
    if prior:
        log.info("prior_loaded", count=len(prior))
        _check_prior_schema(prior)
    else:
        log.warning("no_prior_cache", msg="prepare 未実行。事前情報なしでスコアリング（一部因子無効）")

    master_lookup: dict[str, str] = {}
    master_all_p = _master_all_path(target_date)
    if not master_all_p.exists() and _legacy_master_all_path(target_date).exists():
        master_all_p = _legacy_master_all_path(target_date)
    if master_all_p.exists():
        df_ma = pd.read_csv(master_all_p, encoding="utf-8", dtype=str)
        master_lookup = dict(zip(df_ma["TICKER"], df_ma["STOCK_NAME"]))

    topix_ret = _fetch_topix_realtime()

    # ── TDnet ポーラー & XBRL 抽出器 ──
    poller_source = os.environ.get("ZARABA_POLLER", "tdnet_html")
    poller = create_poller(poller_source)
    extractor = XbrlExtractor(target_date)

    print(f"TDnet ({poller_source}) から {target_date} の開示を取得中...")
    try:
        disclosures = poller.fetch_recent(target_date)
    except Exception as e:
        print(f"TDnet 取得失敗: {e}")
        return

    if not disclosures:
        print(f"{target_date} の TDnet 開示なし")
        return

    # ── until_time 以前 + 決算短信 + XBRL ありでフィルタ ──
    cutoff = until_time.replace(":", "")
    related_titles_by_code: dict[str, list[str]] = {}
    for d in disclosures:
        disc_time = d.pubdate.split(" ")[-1][:5].replace(":", "") if " " in d.pubdate else ""
        if disc_time <= cutoff and d.company_code:
            related_titles_by_code.setdefault(d.company_code, []).append(d.title)

    earnings = [
        d for d in disclosures
        if d.is_earnings and d.has_xbrl
        and (d.pubdate.split(" ")[-1][:5].replace(":", "") if " " in d.pubdate else "") <= cutoff
    ]

    # 既存 seen/results をロード
    seen = _load_seen(target_date)
    seen_ids: set[str] = set(seen.get("tdnet", []))

    scored_results: list[dict] = []
    results_p = _results_path(target_date)
    if results_p.exists():
        try:
            df_prev = pd.read_csv(results_p, encoding="utf-8")
            scored_results = df_prev.to_dict("records")
        except Exception:
            pass

    # 未処理の決算のみ
    new_earnings = [d for d in earnings if d.id not in seen_ids]

    if not new_earnings:
        print(f"catchup: 新規決算短信 0 件（全 {len(disclosures)} 開示中、決算 {len(earnings)} 件は処理済み）")
        return

    print(f"決算短信 {len(new_earnings)} 件を XBRL 抽出 & スコアリング中...")

    # seen_ids を先に更新（重複防止）
    for disc in new_earnings:
        seen_ids.add(disc.id)

    # XBRL ダウンロード & パースを並列実行
    def _process_one(disc):
        extracted = extractor.process_disclosure(disc)
        return disc, extracted

    scored_count = 0
    workers = min(len(new_earnings), 8)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process_one, d): d for d in new_earnings}
        for fut in as_completed(futures):
            try:
                disc, extracted = fut.result()
                titles = related_titles_by_code.get(disc.company_code, [])
                rec = _xbrl_to_jquants_rec(disc, extracted, related_titles=titles)
                result = _score_record(rec, prior, master_lookup, topix_ret=topix_ret)
                if result:
                    scored_results.append(result)
                    scored_count += 1
                    log.info("catchup_scored", ticker=result["ticker"], score=result["score"])
            except Exception as e:
                log.warning("catchup_process_error", disc_id=futures[fut].id, error=str(e))

    # キャッシュ & 結果保存
    seen["tdnet"] = list(seen_ids)
    _save_seen(target_date, seen)
    _save_results(target_date, scored_results)

    print(f"catchup 完了: {scored_count} 件スコアリング（〜{until_time}）")
    print(f"  結果合計: {len(scored_results)} 件 → {_results_path(target_date)}")
    log.info("catchup_done", new=scored_count, total=len(scored_results))


# ====================================================================
# Phase 2.5: review — 過去 watch 結果の時系列表示
# ====================================================================
def cmd_review(target_date: str) -> None:
    """指定日の results.csv を時系列（開示時刻順）でテーブル表示."""
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    p = _results_path(target_date)
    if not p.exists():
        print(f"結果ファイルが存在しません: {p}")
        return

    try:
        df = pd.read_csv(p, encoding="utf-8")
    except Exception as e:
        print(f"読み込み失敗: {e}")
        return

    if df.empty:
        print(f"{p} は空です")
        return

    # 時刻順にソート
    if "disc_time" in df.columns:
        df = df.sort_values("disc_time", kind="stable")

    # 旧 results.csv 互換: pos_factors/neg_factors が無ければ factors から分割
    if "pos_factors" not in df.columns or "neg_factors" not in df.columns:
        df = df.copy()
        pos_list, neg_list = [], []
        for v in df.get("factors", [""] * len(df)).fillna(""):
            ps, ns = _split_factors(str(v))
            pos_list.append(ps)
            neg_list.append(ns)
        df["pos_factors"] = pos_list
        df["neg_factors"] = neg_list

    table = Table(
        title=f"Review {target_date} (時系列 {len(df)}件)",
        show_lines=False,
    )
    table.add_column("Time", width=WATCH_TABLE_WIDTH_TIME)
    table.add_column("Score", justify="right", width=WATCH_TABLE_WIDTH_SCORE)
    table.add_column("Code", width=WATCH_TABLE_WIDTH_CODE)
    table.add_column("Name", width=WATCH_TABLE_WIDTH_NAME, no_wrap=True)
    table.add_column("Cap", justify="right", width=WATCH_TABLE_WIDTH_CAP)
    table.add_column("Q", width=WATCH_TABLE_WIDTH_Q)
    table.add_column("Judge", width=WATCH_TABLE_WIDTH_JUDGE)
    table.add_column("Pos", width=WATCH_TABLE_WIDTH_POS)
    table.add_column("Neg", width=WATCH_TABLE_WIDTH_NEG)

    for _, r in df.iterrows():
        try:
            score = float(r.get("score", 0))
        except (ValueError, TypeError):
            score = 0.0
        score_str = f"+{score:g}" if score > 0 else f"{score:g}"
        if score >= 3:
            style = "bold green"
        elif score >= 1:
            style = "green"
        elif score <= -2:
            style = "bold red"
        elif score <= -1:
            style = "red"
        else:
            style = ""
        disc_t = _s(r.get("disc_time"))[:5]
        verdict = _s(r.get("verdict"))
        name = _s(r.get("name"))[:WATCH_TABLE_WIDTH_NAME // 2]
        mc = r.get("market_cap_oku")
        if pd.isna(mc) if isinstance(mc, float) else mc is None:
            mc = None
        table.add_row(
            disc_t,
            Text(score_str, style=style),
            _s(r.get("ticker")),
            name,
            _fmt_cap(mc),
            _s(r.get("cur_per_type")),
            Text(_short_verdict(verdict), style=style),
            _s(r.get("pos_factors")),
            _s(r.get("neg_factors")),
        )

    Console().print(table)


# ====================================================================
# Phase 3: watch — ザラバ監視
# ====================================================================
def _parse_hhmm_to_dt(target_date: str, hhmm: str) -> datetime:
    """HHMM 4桁文字列を JST datetime に変換する.

    Args:
        target_date: YYYYMMDD 形式の日付文字列
        hhmm: HHMM 形式の時刻文字列（例: "1100"）

    Returns:
        JST タイムゾーン付き datetime

    Raises:
        ValueError: hhmm が 4桁数字でない、または時刻として不正な場合
    """
    if not hhmm or len(hhmm) != 4 or not hhmm.isdigit():
        raise ValueError(f"HHMM (4桁数字) を入力してください: '{hhmm}'")
    hh, mm = int(hhmm[:2]), int(hhmm[2:])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"時刻が不正です: HH={hh}, MM={mm}")
    base = datetime.strptime(target_date, "%Y%m%d")
    return base.replace(hour=hh, minute=mm, second=0, microsecond=0, tzinfo=JST)


def _compute_poll_interval(target_dt: datetime) -> float:
    """指定時間からの経過秒に応じてポーリング間隔を返す.

    - 指定時間 -15s 〜 +15s: POLL_BURST_SEC (0.05秒)
    - 指定時間 +15s 〜 +60s: POLL_FAST_SEC (0.2秒)
    - それ以外: POLL_INTERVAL_SEC (1.0秒)
    """
    delta = (datetime.now(JST) - target_dt).total_seconds()
    if -POLL_BURST_WINDOW_SEC <= delta <= POLL_BURST_WINDOW_SEC:
        return POLL_BURST_SEC
    if POLL_BURST_WINDOW_SEC < delta <= POLL_FAST_WINDOW_SEC:
        return POLL_FAST_SEC
    return POLL_INTERVAL_SEC


def cmd_watch(target_date: str) -> None:
    """ザラバ監視: TDnet ポーリング + XBRL 抽出 + スコアリング + rich Live 表示."""
    from rich.live import Live
    from rich.table import Table
    from rich.text import Text
    from zaraba_tdnet_poller import create_poller, XbrlExtractor

    # 指定時間（HHMM 4桁）を対話入力。前後でポーリング間隔を高速化する
    hhmm_raw = input("指定時間 HHMM (例: 1100): ").strip()
    target_dt = _parse_hhmm_to_dt(target_date, hhmm_raw)
    log.info("watch_start", date=target_date, target_time=target_dt.strftime("%H:%M"))

    # ── 事前キャッシュ全件メモリロード ──────────────────
    prior_path = _prior_data_path(target_date)
    prior: dict = {}
    if prior_path.exists():
        with open(prior_path, encoding="utf-8") as f:
            prior = json.load(f)
        log.info("prior_loaded", count=len(prior))
    elif _legacy_prior_data_path(target_date).exists():
        legacy_path = _legacy_prior_data_path(target_date)
        with open(legacy_path, encoding="utf-8") as f:
            prior = json.load(f)
        log.info("prior_loaded_legacy", count=len(prior), path=str(legacy_path))
    else:
        log.warning("no_prior_cache", msg="prepare 未実行。事前情報なしでスコアリング（一部因子無効）")

    if prior:
        _check_prior_schema(prior)

    # マスタ全件（カレンダー外銘柄の名前引き用）
    master_lookup: dict[str, str] = {}
    master_all_p = _master_all_path(target_date)
    if not master_all_p.exists() and _legacy_master_all_path(target_date).exists():
        master_all_p = _legacy_master_all_path(target_date)
    if master_all_p.exists():
        df_ma = pd.read_csv(master_all_p, encoding="utf-8", dtype=str)
        master_lookup = dict(zip(df_ma["TICKER"], df_ma["STOCK_NAME"]))

    # ── TOPIX リアルタイム取得（F9 テーマブースト用、起動時1回）────
    topix_ret = _fetch_topix_realtime()

    # ── TDnet ポーラー & XBRL 抽出器 初期化 ────────────
    # TDnet HTML（公式）を第一選択。yanoshin は遅延・廃止リスクで不安定なため
    # デフォルトから外す。環境変数 ZARABA_POLLER=yanoshin で切替可能。
    poller_source = os.environ.get("ZARABA_POLLER", "tdnet_html")
    poller = create_poller(poller_source)
    log.info("poller_selected", source=poller_source)
    extractor = XbrlExtractor(target_date)

    seen = _load_seen(target_date)
    seen_ids: set[str] = set(seen.get("tdnet", []))

    # watch 再開時に既存 results.csv をロードして重複書き込みを防ぐ
    # 旧実装は空配列で開始 → 最後の watch ぶんだけで上書きする問題があった
    scored_results: list[dict] = []
    results_p = _results_path(target_date)
    if results_p.exists():
        try:
            df_prev = pd.read_csv(results_p, encoding="utf-8")
            scored_results = df_prev.to_dict("records")
            log.info("results_reloaded", count=len(scored_results), path=str(results_p))
        except Exception:
            log.warning("results_reload_failed", exc_info=True)

    # catchup済み・前回watch確認済みの件数を記録 → 表示は今回の新規のみ
    _baseline_count = len(scored_results)

    poll_interval = _compute_poll_interval(target_dt)
    poll_count = 0
    # 銘柄コード別の関連開示タイトル（自社株買い・記念配当等の検知用）
    related_titles_by_code: dict[str, list[str]] = {}

    def _build_table() -> Table:
        """rich Table を構築."""
        now = datetime.now(JST).strftime("%H:%M:%S")
        src = f"TDnet/{poller_source}"
        table = Table(
            title=f"ザラバ決算モニター {target_date}  [{src} | {now} | T={target_dt.strftime('%H:%M')} | {poll_interval:.2f}s間隔]",
            show_lines=False,
        )
        table.add_column("Score", justify="right", width=WATCH_TABLE_WIDTH_SCORE)
        table.add_column("Code", width=WATCH_TABLE_WIDTH_CODE)
        table.add_column("Name", width=WATCH_TABLE_WIDTH_NAME, no_wrap=True)
        table.add_column("Cap", justify="right", width=WATCH_TABLE_WIDTH_CAP)
        table.add_column("Q", width=WATCH_TABLE_WIDTH_Q)
        table.add_column("Judge", width=WATCH_TABLE_WIDTH_JUDGE)
        table.add_column("Pos", width=WATCH_TABLE_WIDTH_POS)
        table.add_column("Neg", width=WATCH_TABLE_WIDTH_NEG)
        table.add_column("Time", width=WATCH_TABLE_WIDTH_TIME)

        new_results = scored_results[_baseline_count:]
        if not new_results:
            table.add_row("", "", "  待機中...", "", "", "", "", "", "")
        else:
            sorted_res = sorted(new_results, key=lambda x: x["score"], reverse=True)
            for r in sorted_res:
                score = r["score"]
                score_str = f"+{score}" if score > 0 else str(score)
                verdict = r["verdict"]
                if score >= 3:
                    style = "bold green"
                elif score >= 1:
                    style = "green"
                elif score <= -2:
                    style = "bold red"
                elif score <= -1:
                    style = "red"
                else:
                    style = ""
                table.add_row(
                    Text(score_str, style=style),
                    _s(r.get("ticker")),
                    _s(r.get("name"))[:WATCH_TABLE_WIDTH_NAME // 2],
                    _fmt_cap(r.get("market_cap_oku")),
                    _s(r.get("cur_per_type")),
                    Text(_short_verdict(verdict), style=style),
                    _s(r.get("pos_factors")),
                    _s(r.get("neg_factors")),
                    _s(r.get("disc_time"))[:5],
                )

        new_count = len(scored_results) - _baseline_count
        total = len(prior) if prior else "?"
        table.caption = f"新規: {new_count}  |  既知: {_baseline_count}  |  対象: {total}"
        return table

    print("ザラバ決算モニター起動（TDnet XBRL モード）。Ctrl+C で終了。")
    print()

    try:
        with Live(_build_table(), refresh_per_second=2) as live:
            while True:
                poll_interval = _compute_poll_interval(target_dt)
                poll_count += 1
                try:
                    disclosures = poller.fetch_recent(target_date)
                except Exception as e:
                    log.warning("poll_error", error=str(e))
                    time.sleep(poll_interval)
                    continue

                # 全開示のタイトルを銘柄別に蓄積（自社株買い・記念配当等の非決算開示検知用）
                for d in disclosures:
                    if d.company_code and d.id not in seen_ids:
                        related_titles_by_code.setdefault(d.company_code, []).append(d.title)

                # 決算短信 + XBRL 付き + 未処理 のみ
                new_disclosures = [
                    d for d in disclosures
                    if d.is_earnings and d.has_xbrl and d.id not in seen_ids
                ]

                if new_disclosures:
                    from concurrent.futures import ThreadPoolExecutor, as_completed

                    # seen_ids を先に更新（重複防止）
                    for disc in new_disclosures:
                        seen_ids.add(disc.id)

                    # XBRL ダウンロード & パースを並列実行
                    def _process_one(disc):
                        extracted = extractor.process_disclosure(disc)
                        return disc, extracted

                    workers = min(len(new_disclosures), 8)
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futures = {pool.submit(_process_one, d): d for d in new_disclosures}
                        for fut in as_completed(futures):
                            disc, extracted = fut.result()
                            titles = related_titles_by_code.get(disc.company_code, [])
                            rec = _xbrl_to_jquants_rec(disc, extracted, related_titles=titles)
                            result = _score_record(rec, prior, master_lookup, topix_ret=topix_ret)
                            if result:
                                scored_results.append(result)
                                log.info(
                                    "new_earnings",
                                    ticker=result["ticker"],
                                    score=result["score"],
                                    verdict=result["verdict"],
                                )
                            live.update(_build_table())
                    # キャッシュ保存
                    seen["tdnet"] = list(seen_ids)
                    _save_seen(target_date, seen)
                    _save_results(target_date, scored_results)

                live.update(_build_table())
                poll_interval = _compute_poll_interval(target_dt)
                time.sleep(poll_interval)

    except KeyboardInterrupt:
        print("\n\n=== 監視終了 ===")
        if scored_results:
            _save_results(target_date, scored_results)
            print(f"結果保存: {_results_path(target_date)}")


def _xbrl_to_jquants_rec(disc, extracted, related_titles: list[str] | None = None) -> dict:
    """TDnet Disclosure + XBRL 抽出結果を _score_record が受け取る rec 形式に変換.

    extract_pipeline で予想値抽出が実装されたら FORECAST_OP 等もここでマッピングする。
    """
    rec: dict = {
        "Code": disc.company_code + "0",  # 5桁化
        "DiscNo": disc.id,
        "DiscTime": disc.pubdate.split(" ")[-1][:5] if " " in disc.pubdate else "",
        "_title": disc.title,
        "_company_name": disc.company_name,
        "_related_titles": related_titles or [],
    }

    # タイトルから四半期種別を推定
    title = disc.title
    if "第1四半期" in title or "第１四半期" in title:
        rec["CurPerType"] = "1Q"
    elif "第2四半期" in title or "第２四半期" in title or "中間" in title:
        rec["CurPerType"] = "2Q"
    elif "第3四半期" in title or "第３四半期" in title:
        rec["CurPerType"] = "3Q"
    else:
        rec["CurPerType"] = "FY"

    rec["DocType"] = "FinancialStatements"

    if extracted:
        rec["OP"] = extracted.operating_profit  # 営業利益（Operating Profit）
        rec["NetSales"] = extracted.net_sales
        rec["OrdinaryProfit"] = extracted.ordinary_profit
        rec["Profit"] = extracted.profit
        rec["EPS"] = extracted.earnings_per_share
        # 予想値は extract_pipeline 側の対応待ち（現時点では None）
        rec["FOP"] = extracted.raw_extract.get("FORECAST_OP", {}).get("value") if extracted.raw_extract.get("FORECAST_OP") else None
        rec["ShortFOP"] = extracted.raw_extract.get("SHORT_TERM_FORECAST_OP", {}).get("value") if extracted.raw_extract.get("SHORT_TERM_FORECAST_OP") else None
        rec["NxFOP"] = extracted.raw_extract.get("NEXT_YEAR_FORECAST_OP", {}).get("value") if extracted.raw_extract.get("NEXT_YEAR_FORECAST_OP") else None
        rec["NxFODP"] = extracted.raw_extract.get("NEXT_YEAR_FORECAST_ODP", {}).get("value") if extracted.raw_extract.get("NEXT_YEAR_FORECAST_ODP") else None
        rec["NxFNP"] = extracted.raw_extract.get("NEXT_YEAR_FORECAST_NP", {}).get("value") if extracted.raw_extract.get("NEXT_YEAR_FORECAST_NP") else None
        rec["FDivAnn"] = extracted.raw_extract.get("FORECAST_DIV_ANN", {}).get("value") if extracted.raw_extract.get("FORECAST_DIV_ANN") else None
        rec["ForEPS"] = extracted.raw_extract.get("FORECAST_EPS", {}).get("value") if extracted.raw_extract.get("FORECAST_EPS") else None

    return rec


def _save_results(target_date: str, results: list[dict]) -> None:
    """スコアリング結果を CSV 保存."""
    df = pd.DataFrame(results)
    df.to_csv(_results_path(target_date), index=False, encoding="utf-8")


def _guidance_vs_consensus(rec: dict, prior: dict) -> float | None:
    """翌期会社予想（営業利益）vs 来期コンセンサス（営業利益）の乖離率。観察用。"""
    nx_op = _to_num(rec.get("NxFOP"))
    cons_next = prior.get("consensus_next")
    cons_next_op = cons_next.get("OP_PROFIT") if isinstance(cons_next, dict) else None
    if nx_op is None or not cons_next_op or cons_next_op == 0:
        return None
    cons_yen = cons_next_op * 1_000_000
    return round((nx_op - cons_yen) / abs(cons_yen), 4)


# ====================================================================
# スコアリング
# ====================================================================
def _score_record(
    rec: dict,
    prior: dict,
    master_lookup: dict[str, str] | None = None,
    topix_ret: float = 0.0,
) -> dict | None:
    """1件の決算レコードをスコアリング.

    Args:
        rec: J-Quants fins/summary の1レコード
        prior: prepare で構築した銘柄別事前情報
        master_lookup: TICKER→STOCK_NAME のマスタ辞書（カレンダー外銘柄の名前引き用）

    Returns:
        スコアリング結果辞書。対象外銘柄は None。
    """
    code_5 = rec.get("Code", "")
    code_4 = code_5[:4]

    p = prior.get(code_4)
    if not p:
        # prepare 対象外の銘柄（カレンダー未登録のザラバ決算）
        name = (master_lookup or {}).get(code_4, "")
        if not name:
            name = rec.get("_company_name", "")
            if name and master_lookup is not None:
                master_lookup[code_4] = name
        p = {"ticker": code_4, "name": name}

    score = 0
    factors: list[str] = []

    # ── F1: 進捗率サプライズ ─────────────────────────
    # J-Quants OP は累計値。通期予想に対する進捗率で判定。
    cumulative_op = _to_num(rec.get("OP"))  # 今回発表の累計営業利益（Operating Profit）
    forecast_op = p.get("forecast_op")      # 直前最新の通期会社予想OP（priorから）
    prev_forecast_op = p.get("prev_forecast_op")  # 2つ前の通期予想（F2/F5では使わない）
    prev_cumulative_op = p.get("prev_cumulative_op")  # 前Q累計OP

    # 「最新の通期予想」= 今日のFOPがあればそれを優先、なければpriorの最新
    # 今日がガイダンス修正を含む場合、F1進捗・F4翌期比較・F5出尽くしは新FOPベースで判定
    today_forecast_op = _to_num(rec.get("FOP"))
    short_term_forecast_op = _to_num(rec.get("ShortFOP"))
    cur_per = rec.get("CurPerType", "")
    effective_forecast_op: float | None = (
        today_forecast_op if (today_forecast_op is not None and today_forecast_op != 0)
        else forecast_op
    )

    if cur_per == "FY" and today_forecast_op is None and short_term_forecast_op is not None:
        score -= 1
        factors.append("通期予想非開示")

    # Q単独OP = 今回累計 - 前Q累計（1Qの場合 prev=0）
    standalone_op: float | None = None
    if cumulative_op is not None:
        if cur_per == "1Q" or prev_cumulative_op is None:
            standalone_op = cumulative_op  # 1Qは累計=単独
        else:
            standalone_op = cumulative_op - prev_cumulative_op

    if cur_per in ("1Q", "2Q", "3Q") and cumulative_op is not None and effective_forecast_op and effective_forecast_op != 0:
        progress = cumulative_op / effective_forecast_op
        expected_progress = {"1Q": 0.25, "2Q": 0.50, "3Q": 0.75}
        expected = expected_progress.get(cur_per, 0.25)

        if expected > 0:
            surprise = progress / expected  # 1.0 = 期待通り
            if surprise > 1.2:
                score += 1
                factors.append(f"進捗↑{progress:.0%}")
            elif surprise < 0.8:
                score -= 1
                factors.append(f"進捗↓{progress:.0%}")

    # ── F15: 通期着地サプライズ（FYのみ、経常利益ベース、ODP不在時はOPフォールバック）─
    cumulative_odp_f15 = _to_num(rec.get("OrdinaryProfit"))
    forecast_odp_f15 = p.get("forecast_odp")
    if cur_per == "FY" and cumulative_odp_f15 is not None and forecast_odp_f15 and forecast_odp_f15 != 0:
        fy_surprise = (cumulative_odp_f15 - forecast_odp_f15) / abs(forecast_odp_f15)
    elif cur_per == "FY" and cumulative_op is not None and effective_forecast_op and effective_forecast_op != 0:
        fy_surprise = (cumulative_op - effective_forecast_op) / abs(effective_forecast_op)
    else:
        fy_surprise = None

    if fy_surprise is not None:
        if fy_surprise > 0.20:
            score += 2
            factors.append(f"着地経↑{fy_surprise:+.0%}")
        elif fy_surprise > 0.05:
            score += 1
            factors.append(f"着地経↑{fy_surprise:+.0%}")
        elif fy_surprise < -0.20:
            score -= 2
            factors.append(f"着地経↓{fy_surprise:+.0%}")
        elif fy_surprise < -0.05:
            score -= 1
            factors.append(f"着地経↓{fy_surprise:+.0%}")

    # ── F2: ガイダンス修正（今日の新FOP vs 直前最新FOP） ─────
    new_forecast_op = _to_num(rec.get("FOP"))
    if new_forecast_op is not None and forecast_op and forecast_op != 0:
        chg = (new_forecast_op - forecast_op) / abs(forecast_op)
        if chg > 0.05:
            score += 1
            factors.append(f"上方修正+{chg:.0%}")
        elif chg < -0.05:
            score -= 1
            factors.append(f"下方修正{chg:.0%}")

    # ── F3: YoY 営業利益（今日の新開示Q単独 vs 前年同QのQ単独） ─
    # 修正前は latest_q_quarter（=前回開示Q）で引いていたため、今日が2Q開示なら
    # 「今2Q単独 vs 前期1Q単独」という別Q比較になっていた
    prev_year_map = p.get("prev_year_q_op_map") or {}
    yoy_key = "4Q" if cur_per == "FY" else cur_per
    prev_year_q_op = prev_year_map.get(yoy_key)
    if prev_year_q_op is None and cur_per == "FY":
        prev_year_q_op = prev_year_map.get("FY")
    # 互換フォールバック（旧prepareデータ）: 旧値は latest_q_quarter 固有なので、
    # cur_per と一致する場合のみ採用
    if prev_year_q_op is None and cur_per == p.get("latest_q_quarter"):
        prev_year_q_op = p.get("prev_year_q_op")
    if standalone_op is not None and prev_year_q_op and prev_year_q_op != 0:
        yoy = (standalone_op - prev_year_q_op) / abs(prev_year_q_op)
        if yoy > 0.30:
            score += 1
            factors.append(f"YoY+{yoy:.0%}")
        elif yoy < -0.30:
            score -= 1
            factors.append(f"YoY{yoy:.0%}")

    # ── F4: 翌期見通し（FYのみ、翌期予想 vs 今期実績） ─────────────
    # 経常利益ベース。IFRS等でODP不在時はOPにフォールバック。
    cur_per = rec.get("CurPerType", "")
    if cur_per == "FY":
        nx_odp = _to_num(rec.get("NxFODP"))
        cumulative_odp = _to_num(rec.get("OrdinaryProfit"))
        if nx_odp is not None and cumulative_odp and cumulative_odp != 0:
            nx_chg = (nx_odp - cumulative_odp) / abs(cumulative_odp)
        elif _to_num(rec.get("NxFOP")) is not None and cumulative_op and cumulative_op != 0:
            nx_chg = (_to_num(rec.get("NxFOP")) - cumulative_op) / abs(cumulative_op)
        else:
            nx_chg = None

        if nx_chg is not None:
            if nx_chg > 0.10:
                score += 2
                factors.append(f"翌期経↑{nx_chg:+.0%}")
            elif nx_chg < -0.10:
                score -= 2
                factors.append(f"翌期経↓{nx_chg:+.0%}")
        else:
            cap = p.get("market_cap_oku")
            if cap is not None and cap >= 3000:
                score -= 1
                factors.append("翌期予想非開示")

    # ── F5: 出尽くしリスク（3Q） ─────────────────────
    if cur_per == "3Q" and cumulative_op is not None and effective_forecast_op and effective_forecast_op > 0:
        # 3Q累計 / 最新通期予想 = 進捗率
        progress = cumulative_op / effective_forecast_op
        # ガイダンス据え置き判定: old=直前最新(prior), new=今日のFOP or old
        old_fop = forecast_op
        new_fop = today_forecast_op or forecast_op
        unchanged = abs(new_fop - old_fop) / abs(old_fop) < 0.02 if old_fop else True
        if progress > 0.90 and unchanged:
            score -= 2
            factors.append(f"出尽くし({progress:.0%})")

    # ── 株式分割検知（F6/F12 無効化判定用）──────────────
    # 暫定措置: 分割発表時は per-share 指標（DPS/EPS）が分割比率で変動し
    # 前回値・コンセンサスとの比較が壊れるため、F6/F12 を無効化する。
    # 将来は分割比率を抽出して調整する。
    _all_related = rec.get("_related_titles", [])
    has_stock_split = any("株式分割" in t for t in _all_related)

    # ── F6: 増配/減配（非対称ウェイト）─────────────────
    actual_div = _to_num(rec.get("FDivAnn"))
    prev_div = p.get("forecast_div_ann")
    div_detected = False
    if not has_stock_split and actual_div is not None and prev_div and prev_div > 0:
        div_chg = (actual_div - prev_div) / prev_div
        if div_chg > 0.05:
            score += 1
            factors.append(f"増配+{div_chg:.0%}")
            div_detected = True
        elif div_chg < -0.20:
            score -= 3
            factors.append(f"大幅減配{div_chg:.0%}")
            div_detected = True
        elif div_chg < -0.05:
            score -= 2
            factors.append(f"減配{div_chg:.0%}")
            div_detected = True

    # F6 フォールバック: XBRL FDivAnn が取れなかった場合、related_titles の
    # 「配当予想の修正」開示を検知して情報タグだけ付ける（スコアは加算しない）
    if not div_detected:
        related_titles = rec.get("_related_titles", [])
        for t in related_titles:
            if "配当予想" in t and ("修正" in t or "変更" in t):
                factors.append("配当修正開示?")
                break

    # ── F7: 折込度合い（事前調整） ─────────────────────
    momentum = p.get("momentum_20d")
    vol_ratio = p.get("vol_ratio")
    has_rev = p.get("has_prior_revision", False)

    if momentum is not None and momentum > 0.10 and not has_rev:
        score -= 1
        factors.append("折込⚠")
    if vol_ratio is not None and vol_ratio > 2.0:
        score -= 1
        factors.append(f"出来高x{vol_ratio:.1f}⚠")

    # ── F8: 信用売り残倍率 ─────────────────────────
    margin_ratio = p.get("margin_ratio")
    if margin_ratio is not None and margin_ratio < 1.0:
        score += 0.5
        factors.append("売り長")

    # ── F10: 自社株買い ─────────────────────────
    related_titles = rec.get("_related_titles", [])
    if any("自己株式の取得" in t for t in related_titles):
        score += 2
        factors.append("自社株買い")

    # ── F8b: 記念配当/特別配当 ─────────────────────
    all_titles = [rec.get("_title", "")] + related_titles
    if any("記念配当" in t or "特別配当" in t for t in all_titles):
        score += 1
        factors.append("記念配当")

    # ── F9: 廃止（旧テーマブースト）──────────────
    # TODO: 個人投資家の関心度合いが高い銘柄のマーキング（βでは不十分。別指標を検討）

    # ── F4c: コンセンサス乖離（全Q経常利益ベース）────
    cons_by_q = p.get("consensus_by_q")
    cons_q_data = cons_by_q.get(cur_per) if isinstance(cons_by_q, dict) else None
    cons_val = cons_q_data.get("ORD_PROFIT") if isinstance(cons_q_data, dict) else None
    actual_val = _to_num(rec.get("OrdinaryProfit"))  # 経常利益（累計）
    if actual_val is not None and cons_val and cons_val != 0:
        cons_yen = cons_val * 1_000_000  # 百万円 → 円
        cd = (actual_val - cons_yen) / abs(cons_yen)
        if cd > 0.10:
            score += 3
            factors.append(f"コンセ乖離{cd:+.1%}")
        elif cd > 0.05:
            score += 2
            factors.append(f"コンセ乖離{cd:+.1%}")
        elif cd > 0:
            score += 1
            factors.append(f"コンセ乖離{cd:+.1%}")
        elif cd < -0.30:
            score -= 5
            factors.append(f"コンセ乖離{cd:+.1%}")
        elif cd < -0.20:
            score -= 4
            factors.append(f"コンセ乖離{cd:+.1%}")
        elif cd < -0.10:
            score -= 3
            factors.append(f"コンセ乖離{cd:+.1%}")
        elif cd < -0.05:
            score -= 2
            factors.append(f"コンセ乖離{cd:+.1%}")
        elif cd < 0:
            score -= 1
            factors.append(f"コンセ乖離{cd:+.1%}")

    # ── F4n: 翌期コンセンサス乖離（FYのみ、純利益ベース）────
    if cur_per == "FY":
        nx_np = _to_num(rec.get("NxFNP"))
        cons_next = p.get("consensus_next")
        cons_next_np = cons_next.get("NET_PROFIT") if isinstance(cons_next, dict) else None
        if nx_np is not None and cons_next_np and cons_next_np != 0:
            cons_np_yen = cons_next_np * 1_000_000
            cn = (nx_np - cons_np_yen) / abs(cons_np_yen)
            if cn > 0.10:
                score += 3
                factors.append(f"翌コ純{cn:+.1%}")
            elif cn > 0.05:
                score += 2
                factors.append(f"翌コ純{cn:+.1%}")
            elif cn > 0:
                score += 1
                factors.append(f"翌コ純{cn:+.1%}")
            elif cn < -0.30:
                score -= 5
                factors.append(f"翌コ純{cn:+.1%}")
            elif cn < -0.20:
                score -= 4
                factors.append(f"翌コ純{cn:+.1%}")
            elif cn < -0.10:
                score -= 3
                factors.append(f"翌コ純{cn:+.1%}")
            elif cn < -0.05:
                score -= 2
                factors.append(f"翌コ純{cn:+.1%}")
            elif cn < 0:
                score -= 1
                factors.append(f"翌コ純{cn:+.1%}")

    # ── F13: QoQ OP急変（前Q比）──────────────────
    # FYは4Q standalone が year-end 調整含みでノイジー → F15で代替。小分母も除外
    latest_q_op = p.get("latest_q_op")
    annual_ref = effective_forecast_op or cumulative_op
    if (
        cur_per != "FY"
        and standalone_op is not None
        and latest_q_op
        and latest_q_op != 0
        and (not annual_ref or abs(latest_q_op) >= abs(annual_ref) * 0.05)
    ):
        qoq = (standalone_op - latest_q_op) / abs(latest_q_op)
        if qoq > 0.50:
            score += 1
            factors.append(f"QoQ+{qoq:.0%}")
        elif qoq < -0.50:
            score -= 2
            factors.append(f"QoQ{qoq:.0%}")

    # ── F7g: 成長加速/減速（FYのみ、経常利益ベース、ODP不在時はOPフォールバック）─
    if cur_per == "FY":
        nx_odp_7g = _to_num(rec.get("NxFODP"))
        cum_odp_7g = _to_num(rec.get("OrdinaryProfit"))
        baseline = p.get("baseline_yoy_op")
        if nx_odp_7g is not None and cum_odp_7g and cum_odp_7g != 0:
            nyc = (nx_odp_7g - cum_odp_7g) / abs(cum_odp_7g)
        elif _to_num(rec.get("NxFOP")) is not None and cumulative_op and cumulative_op != 0:
            nyc = (_to_num(rec.get("NxFOP")) - cumulative_op) / abs(cumulative_op)
        else:
            nyc = None
        if nyc is not None and baseline is not None:
            gap = nyc - baseline
            if gap > 0.20:
                score += 1
                factors.append(f"成長加速(翌期{nyc:+.0%}vs基準{baseline:+.0%})")
            elif gap < -0.20:
                score -= 1
                factors.append(f"成長減速(翌期{nyc:+.0%}vs基準{baseline:+.0%})")

    # ── F14: 株式分割 ─────────────────────────
    if has_stock_split:
        score += 1
        factors.append("株式分割")

    # ── F12: PER割安度 PEG（FYのみ、経常利益ベース、ODP不在時はOPフォールバック）─
    if cur_per == "FY" and not has_stock_split:
        latest_close = p.get("latest_close")
        for_eps = _to_num(rec.get("ForEPS"))
        nx_odp_12 = _to_num(rec.get("NxFODP"))
        cum_odp_12 = _to_num(rec.get("OrdinaryProfit"))
        if nx_odp_12 is not None and cum_odp_12 and cum_odp_12 != 0:
            nyc_12_num, nyc_12_den = nx_odp_12, cum_odp_12
        elif _to_num(rec.get("NxFOP")) is not None and cumulative_op and cumulative_op != 0:
            nyc_12_num, nyc_12_den = _to_num(rec.get("NxFOP")), cumulative_op
        else:
            nyc_12_num, nyc_12_den = None, None
        if (latest_close and for_eps and for_eps > 0
                and nyc_12_num is not None and nyc_12_den):
            per = latest_close / for_eps
            nyc_12 = (nyc_12_num - nyc_12_den) / abs(nyc_12_den)
            if nyc_12 > 0 and per > 0:
                growth_pct = nyc_12 * 100  # 0.20 → 20
                peg = per / growth_pct if growth_pct > 0 else float("inf")
                if peg < 0.5:
                    score += 2
                    factors.append(f"PEG割安{peg:.1f}(PER{per:.0f}x/成長{growth_pct:.0f}%)")
                elif peg < 1.0:
                    score += 1
                    factors.append(f"PEG割安{peg:.1f}(PER{per:.0f}x/成長{growth_pct:.0f}%)")
                elif peg > 2.0:
                    score -= 1
                    factors.append(f"PEG割高{peg:.1f}(PER{per:.0f}x/成長{growth_pct:.0f}%)")

    # ── 判定 ─────────────────────────────────
    score = round(score, 1)
    if score >= 3:
        verdict = "STRONG_BUY"
    elif score >= 2:
        verdict = "BUY"
    elif score >= 1:
        verdict = "SLIGHT_BUY"
    elif score >= 0:
        verdict = "NEUTRAL"
    elif score >= -1:
        verdict = "SLIGHT_SELL"
    else:
        verdict = "SELL"

    pos_s, neg_s = _split_factors(factors)
    name = p.get("name", "") or rec.get("_company_name", "")
    return {
        "ticker": code_4,
        "name": name,
        "market_cap_oku": p.get("market_cap_oku"),
        "score": score,
        "verdict": verdict,
        "factors": " ".join(factors),
        "pos_factors": pos_s,
        "neg_factors": neg_s,
        "disc_time": rec.get("DiscTime", ""),
        "cur_per_type": cur_per,
        "cumulative_op": cumulative_op,
        "standalone_op": standalone_op,
        "forecast_op": effective_forecast_op,
        "forecast_op_source": "xbrl" if (today_forecast_op is not None and today_forecast_op != 0) else ("prior" if forecast_op else None),
        # 観察用（スコア非参照）— F16 事前期待並み検証用
        "obs_guidance_vs_consensus": _guidance_vs_consensus(rec, p),
        "obs_momentum_5d": p.get("momentum_5d"),
        "obs_momentum_20d": p.get("momentum_20d"),
        "obs_beta_20d": p.get("beta_20d"),
    }


# ====================================================================
# Phase 3: GCS upload / download
# ====================================================================
GCS_BUCKET = "stock_data_1930932"
GCS_RESULTS_PREFIX = "earnings_model/zaraba_scoring_results/"


def _gcs_client():
    """GCS クライアントを認証情報付きで返す."""
    from google.cloud import storage
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        str(settings.google_application_credentials)
    )
    return storage.Client(project=PROJECT_ID, credentials=creds)


def cmd_upload_results(target_date: str) -> None:
    """ローカル results.csv を GCS にアップロード."""
    p = _results_path(target_date)
    if not p.exists():
        print(f"結果ファイルが存在しません: {p}")
        return

    blob_name = f"{GCS_RESULTS_PREFIX}results_{target_date}.csv"
    client = _gcs_client()
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(p))
    log.info("results_uploaded", blob=blob_name, size=p.stat().st_size)
    print(f"アップロード完了: gs://{GCS_BUCKET}/{blob_name}")


def cmd_backup_cache() -> None:
    """キャッシュディレクトリ全体を zip で保管（バグ調査用スナップショット）."""
    import zipfile

    if not CACHE_BASE.exists():
        print(f"キャッシュが存在しません: {CACHE_BASE}")
        return

    now = datetime.now(tz=timezone(timedelta(hours=9)))
    zip_name = f"backup_{now.strftime('%Y%m%d_%H%M%S')}.zip"
    zip_path = CACHE_BASE / zip_name

    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in CACHE_BASE.rglob("*"):
            if f.is_dir():
                continue
            if f.name.startswith("backup_") and f.suffix == ".zip":
                continue
            arcname = f.relative_to(CACHE_BASE)
            zf.write(f, arcname)
            count += 1

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    log.info("cache_backup_created", path=str(zip_path), files=count, size_mb=round(size_mb, 1))
    print(f"保管完了: {zip_path} ({count}ファイル, {size_mb:.1f}MB)")

    # GCS にアップロード
    gcs_blob = f"{GCS_RESULTS_PREFIX}{zip_name}"
    client = _gcs_client()
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(gcs_blob)
    blob.upload_from_filename(str(zip_path))
    print(f"GCS保管完了: gs://{GCS_BUCKET}/{gcs_blob}")


def cmd_gcs_review(target_date: str) -> None:
    """GCS から results を取得し review と同じレイアウトで表示."""
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    blob_name = f"{GCS_RESULTS_PREFIX}results_{target_date}.csv"
    client = _gcs_client()
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(blob_name)

    if not blob.exists():
        print(f"GCS にデータがありません: gs://{GCS_BUCKET}/{blob_name}")
        return

    import io

    content = blob.download_as_text(encoding="utf-8")
    df = pd.read_csv(io.StringIO(content))

    if df.empty:
        print(f"GCS の results は空です: {blob_name}")
        return

    if "disc_time" in df.columns:
        df = df.sort_values("disc_time", kind="stable")

    if "pos_factors" not in df.columns or "neg_factors" not in df.columns:
        df = df.copy()
        pos_list, neg_list = [], []
        for v in df.get("factors", [""] * len(df)).fillna(""):
            ps, ns = _split_factors(str(v))
            pos_list.append(ps)
            neg_list.append(ns)
        df["pos_factors"] = pos_list
        df["neg_factors"] = neg_list

    table = Table(
        title=f"GCS Review {target_date} (時系列 {len(df)}件)",
        show_lines=False,
    )
    table.add_column("Time", width=WATCH_TABLE_WIDTH_TIME)
    table.add_column("Score", justify="right", width=WATCH_TABLE_WIDTH_SCORE)
    table.add_column("Code", width=WATCH_TABLE_WIDTH_CODE)
    table.add_column("Name", width=WATCH_TABLE_WIDTH_NAME, no_wrap=True)
    table.add_column("Cap", justify="right", width=WATCH_TABLE_WIDTH_CAP)
    table.add_column("Q", width=WATCH_TABLE_WIDTH_Q)
    table.add_column("Judge", width=WATCH_TABLE_WIDTH_JUDGE)
    table.add_column("Pos", width=WATCH_TABLE_WIDTH_POS)
    table.add_column("Neg", width=WATCH_TABLE_WIDTH_NEG)

    for _, r in df.iterrows():
        try:
            score = float(r.get("score", 0))
        except (ValueError, TypeError):
            score = 0.0
        score_str = f"+{score:g}" if score > 0 else f"{score:g}"
        if score >= 3:
            style = "bold green"
        elif score >= 1:
            style = "green"
        elif score <= -2:
            style = "bold red"
        elif score <= -1:
            style = "red"
        else:
            style = ""
        disc_t = _s(r.get("disc_time"))[:5]
        verdict = _s(r.get("verdict"))
        name = _s(r.get("name"))[:WATCH_TABLE_WIDTH_NAME // 2]
        mc = r.get("market_cap_oku")
        if pd.isna(mc) if isinstance(mc, float) else mc is None:
            mc = None
        table.add_row(
            disc_t,
            Text(score_str, style=style),
            _s(r.get("ticker")),
            name,
            _fmt_cap(mc),
            _s(r.get("cur_per_type")),
            Text(_short_verdict(verdict), style=style),
            _s(r.get("pos_factors")),
            _s(r.get("neg_factors")),
        )

    Console().print(table)


# ====================================================================
# CLI
# ====================================================================
def main() -> None:
    parser = argparse.ArgumentParser(
        description="ザラバ決算リアクションツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
使い方（--date 省略時は今日）:
  prepare  : PYTHONUTF8=1 python scripts/zaraba_earnings.py prepare
  catchup  : PYTHONUTF8=1 python scripts/zaraba_earnings.py catchup --until 13:50
  watch    : PYTHONUTF8=1 python scripts/zaraba_earnings.py watch
  review   : PYTHONUTF8=1 python scripts/zaraba_earnings.py review

日付指定: --date YYYYMMDD | t(今日) | p(前取引日) | n(次取引日)
        """,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # prepare
    p_prep = sub.add_parser("prepare", help="事前準備（BQ キャッシュ取得）")
    p_prep.add_argument("--date", type=resolve_date, default="t", help="対象日 YYYYMMDD（既定=今日）")
    p_prep.add_argument("--force", action="store_true", help="キャッシュを無視して再取得")
    p_prep.add_argument(
        "--target",
        choices=[PREPARE_TARGET_SCHEDULED, PREPARE_TARGET_ALL],
        default=PREPARE_TARGET_SCHEDULED,
        help="取得対象: scheduled=決算予定銘柄（既定・日付別） / all=全銘柄（共有キャッシュ）",
    )
    p_prep.add_argument(
        "--data",
        choices=[PREPARE_DATA_FULL, PREPARE_DATA_CONSENSUS],
        default=PREPARE_DATA_FULL,
        help="取得データ: full=全データ（既定） / consensus=コンセのみ（prior_data内のコンセも更新）",
    )

    # catchup
    p_catch = sub.add_parser("catchup", help="指定時刻までの DiscNo キャッシュ作成")
    p_catch.add_argument("--date", type=resolve_date, default="t", help="対象日 YYYYMMDD（既定=今日）")
    p_catch.add_argument("--until", required=True, help="キャッシュ対象時刻 HH:MM")

    # watch
    p_watch = sub.add_parser("watch", help="ザラバ監視（リアルタイムスコアリング）")
    p_watch.add_argument("--date", type=resolve_date, default="t", help="対象日 YYYYMMDD（既定=今日）")

    # review
    p_rev = sub.add_parser("review", help="過去 watch 結果を時系列表示")
    p_rev.add_argument("--date", type=resolve_date, default="t", help="対象日 YYYYMMDD（既定=今日）")

    # upload
    p_up = sub.add_parser("upload", help="results.csv を GCS にアップロード")
    p_up.add_argument("--date", type=resolve_date, default="t", help="対象日 YYYYMMDD（既定=今日）")

    # gcs-review
    p_gcsr = sub.add_parser("gcs-review", help="GCS の results を watch レイアウトで表示")
    p_gcsr.add_argument("--date", type=resolve_date, default="t", help="対象日 YYYYMMDD（既定=今日）")

    # backup
    sub.add_parser("backup", help="キャッシュ全体を zip でスナップショット保管")

    args = parser.parse_args()

    if args.command == "prepare":
        cmd_prepare(args.date, force=args.force, target=args.target, data=args.data)
    elif args.command == "catchup":
        cmd_catchup(args.date, until_time=getattr(args, "until"))
    elif args.command == "watch":
        cmd_watch(args.date)
    elif args.command == "review":
        cmd_review(args.date)
    elif args.command == "upload":
        cmd_upload_results(args.date)
    elif args.command == "gcs-review":
        cmd_gcs_review(args.date)
    elif args.command == "backup":
        cmd_backup_cache()


if __name__ == "__main__":
    main()

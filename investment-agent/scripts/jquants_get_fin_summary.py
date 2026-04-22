# -*- coding: utf-8 -*-
"""J-Quants API V2 — /fins/summary 全データ取得 → BigQuery 直接ロード

実行環境: Google Colab personal / Colab enterprise / Cloud Run Job
条件    : 全企業、指定期間（START_DATE ～ END_DATE）
ロード先: BigQuery gmailpj-357912.STOCK.fin_summary（WRITE_APPEND）
備考    : jquants-api-client は使用しない（V2 未対応のため requests で直呼び）

認証（環境別）:
    colab_personal   … Colab Secrets: JQUANTS_API_KEY, GCP_SA_KEY（JSON文字列）
    colab_enterprise … Colab Secrets: JQUANTS_API_KEY  / BQ認証: ADC
    cloudrun         … 環境変数: JQUANTS_API_KEY       / BQ認証: ADC

日付指定（環境別）:
    Colab      … ファイル冒頭の START_DATE / END_DATE を直接書き換える
    Cloud Run  … --from / --to 引数で指定（省略時は設定ブロックの値を使用）
"""

import argparse
import json
import os
import sys
import traceback
import pandas as pd
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=+9), "JST")
from google.cloud import bigquery

# 同ディレクトリの共通モジュールをインポート
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import send_mail, LogCapture
from jquants_common import get_jquants_api_key, jquants_get

# ============================================================
# 実行環境の自動判別
# ============================================================

def detect_runtime() -> str:
    """実行環境を自動判別する.

    Returns:
        "colab_personal" | "colab_enterprise" | "cloudrun"
    """
    # Cloud Run Jobs: CLOUD_RUN_JOB / Cloud Run Services: K_SERVICE
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    try:
        import google.colab  # noqa: F401
        if os.environ.get("GOOGLE_CLOUD_PROJECT"):
            return "colab_enterprise"
        return "colab_personal"
    except ImportError:
        return "cloudrun"  # ローカルは想定外だが Cloud Run 扱いでフォールバック


RUNTIME: str = detect_runtime()

# ╔══════════════════════════════════════════════════════════════╗
# ║  ★ 実行設定（Colab ではここを直接書き換えて使う）                 ║
# ║    Cloud Run では --from / --to 引数で上書き可能                ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  DATE_MODE を選択:                                            ║
# ║    "t"  今日の日付で取得 ← デフォルト                           ║
# ║    "1"  DATE_SINGLE の1日のみ取得                              ║
# ║    "r"  DATE_FROM ～ DATE_TO の期間で取得                      ║
# ╚══════════════════════════════════════════════════════════════╝

DATE_MODE   = "t"          # "t"=今日 / "1"=特定の1日 / "r"=期間

DATE_SINGLE = "20260228"   # MODE="1" のときの日付 (YYYYMMDD)

DATE_FROM   = "20170101"   # MODE="r" のときの開始日 (YYYYMMDD)
DATE_TO     = "20171231"   # MODE="r" のときの終了日 (YYYYMMDD)

SLEEP_SEC = 0.5   # レートリミット対策（プランに応じて調整）

PROJECT_ID = "gmailpj-357912"
DATASET_ID = "STOCK"
TABLE_ID   = "fin_summary"

# ════════════════════════════════════════════════════════════════

ENDPOINT = "/fins/summary"


# ============================================================
# 環境別: BigQuery クライアント取得
# ============================================================

def get_bq_client() -> bigquery.Client:
    """実行環境に応じた BigQuery クライアントを返す."""
    if RUNTIME == "colab_personal":
        from google.colab import userdata
        from google.oauth2 import service_account
        key_info = json.loads(userdata.get("GCP_SA_KEY"))
        creds    = service_account.Credentials.from_service_account_info(key_info)
        return bigquery.Client(credentials=creds, project=PROJECT_ID)
    else:  # colab_enterprise / cloudrun: ADC
        return bigquery.Client(project=PROJECT_ID)


# ============================================================
# 日付解決（DATE_MODE に従う）
# ============================================================

def _resolve_dates() -> tuple[datetime, datetime]:
    """DATE_MODE に従って (date_from, date_to) を解決する."""
    today = datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    if DATE_MODE == "t":
        return today, today
    elif DATE_MODE == "1":
        d = datetime.strptime(DATE_SINGLE, "%Y%m%d")
        return d, d
    elif DATE_MODE == "r":
        return datetime.strptime(DATE_FROM, "%Y%m%d"), datetime.strptime(DATE_TO, "%Y%m%d")
    else:
        raise ValueError(f"DATE_MODE が不正: {DATE_MODE!r}  ('t' / '1' / 'r')")


# ============================================================
# 引数パース（Cloud Run 用。Colab ではスキップ）
# ============================================================

def parse_args() -> argparse.Namespace:
    """日付引数をパースする.

    Colab 環境では sys.argv に Jupyter カーネルの引数が混入するため
    argparse をスキップし、設定ブロックの値を使用する。
    """
    if RUNTIME in ("colab_personal", "colab_enterprise"):
        return argparse.Namespace(date_from=None, date_to=None)

    parser = argparse.ArgumentParser(
        description="J-Quants /fins/summary → BigQuery ロード",
        epilog=(
            "例:\n"
            "  --from 20170101 --to 20171231\n"
            "  (省略時はファイル冒頭の START_DATE / END_DATE を使用)"
        ),
    )
    parser.add_argument("--from", dest="date_from", default=None,
                        help="開始日 YYYYMMDD")
    parser.add_argument("--to",   dest="date_to",   default=None,
                        help="終了日 YYYYMMDD（省略時は --from と同日）")
    return parser.parse_args()


# ============================================================
# 1. データ取得
# ============================================================

def fetch_fin_summary_by_date(date_str: str, headers: dict) -> list[dict]:
    """指定日（YYYYMMDD）の fins/summary をページネーションで全件取得する."""
    return jquants_get(ENDPOINT, {"date": date_str}, headers, sleep_sec=SLEEP_SEC)


# ============================================================
# 2. BigQuery ロード前処理
# ============================================================

# API レスポンスの省略カラム名 → BQ テーブルのカラム名（UPPER_SNAKE_CASE）マッピング
_COLUMN_MAP: dict[str, str] = {
    "DiscDate":     "DISCLOSED_DATE",
    "DiscTime":     "DISCLOSED_TIME",
    "Code":         "LOCAL_CODE",
    "DiscNo":       "DISCLOSURE_NUMBER",
    "DocType":      "TYPE_OF_DOCUMENT",
    "CurPerType":   "TYPE_OF_CURRENT_PERIOD",
    "CurPerSt":     "CURRENT_PERIOD_START_DATE",
    "CurPerEn":     "CURRENT_PERIOD_END_DATE",
    "CurFYSt":      "CURRENT_FISCAL_YEAR_START_DATE",
    "CurFYEn":      "CURRENT_FISCAL_YEAR_END_DATE",
    "NxtFYSt":      "NEXT_FISCAL_YEAR_START_DATE",
    "NxtFYEn":      "NEXT_FISCAL_YEAR_END_DATE",
    "Sales":        "NET_SALES",
    "OP":           "OPERATING_PROFIT",
    "OdP":          "ORDINARY_PROFIT",
    "NP":           "PROFIT",
    "EPS":          "EARNINGS_PER_SHARE",
    "DEPS":         "DILUTED_EARNINGS_PER_SHARE",
    "TA":           "TOTAL_ASSETS",
    "Eq":           "EQUITY",
    "EqAR":         "EQUITY_TO_ASSET_RATIO",
    "BPS":          "BOOK_VALUE_PER_SHARE",
    "CFO":          "CASH_FLOWS_FROM_OPERATING_ACTIVITIES",
    "CFI":          "CASH_FLOWS_FROM_INVESTING_ACTIVITIES",
    "CFF":          "CASH_FLOWS_FROM_FINANCING_ACTIVITIES",
    "CashEq":       "CASH_AND_EQUIVALENTS",
    "Div1Q":        "RESULT_DIVIDEND_PER_SHARE_1ST_QUARTER",
    "Div2Q":        "RESULT_DIVIDEND_PER_SHARE_2ND_QUARTER",
    "Div3Q":        "RESULT_DIVIDEND_PER_SHARE_3RD_QUARTER",
    "DivFY":        "RESULT_DIVIDEND_PER_SHARE_FISCAL_YEAR_END",
    "DivAnn":       "RESULT_DIVIDEND_PER_SHARE_ANNUAL",
    "DivUnit":      "DISTRIBUTIONS_PER_UNIT_REIT",
    "DivTotalAnn":  "RESULT_TOTAL_DIVIDEND_PAID_ANNUAL",
    "PayoutRatioAnn": "RESULT_PAYOUT_RATIO_ANNUAL",
    "FDiv1Q":       "FORECAST_DIVIDEND_PER_SHARE_1ST_QUARTER",
    "FDiv2Q":       "FORECAST_DIVIDEND_PER_SHARE_2ND_QUARTER",
    "FDiv3Q":       "FORECAST_DIVIDEND_PER_SHARE_3RD_QUARTER",
    "FDivFY":       "FORECAST_DIVIDEND_PER_SHARE_FISCAL_YEAR_END",
    "FDivAnn":      "FORECAST_DIVIDEND_PER_SHARE_ANNUAL",
    "FDivUnit":     "FORECAST_DISTRIBUTIONS_PER_UNIT_REIT",
    "FDivTotalAnn": "FORECAST_TOTAL_DIVIDEND_PAID_ANNUAL",
    "FPayoutRatioAnn": "FORECAST_PAYOUT_RATIO_ANNUAL",
    "NxFDiv1Q":     "NEXT_YEAR_FORECAST_DIVIDEND_PER_SHARE_1ST_QUARTER",
    "NxFDiv2Q":     "NEXT_YEAR_FORECAST_DIVIDEND_PER_SHARE_2ND_QUARTER",
    "NxFDiv3Q":     "NEXT_YEAR_FORECAST_DIVIDEND_PER_SHARE_3RD_QUARTER",
    "NxFDivFY":     "NEXT_YEAR_FORECAST_DIVIDEND_PER_SHARE_FISCAL_YEAR_END",
    "NxFDivAnn":    "NEXT_YEAR_FORECAST_DIVIDEND_PER_SHARE_ANNUAL",
    "NxFDivUnit":   "NEXT_YEAR_FORECAST_DISTRIBUTIONS_PER_UNIT_REIT",
    "NxFPayoutRatioAnn": "NEXT_YEAR_FORECAST_PAYOUT_RATIO_ANNUAL",
    "FSales2Q":     "FORECAST_NET_SALES_2ND_QUARTER",
    "FOP2Q":        "FORECAST_OPERATING_PROFIT_2ND_QUARTER",
    "FOdP2Q":       "FORECAST_ORDINARY_PROFIT_2ND_QUARTER",
    "FNP2Q":        "FORECAST_PROFIT_2ND_QUARTER",
    "FEPS2Q":       "FORECAST_EARNINGS_PER_SHARE_2ND_QUARTER",
    "NxFSales2Q":   "NEXT_YEAR_FORECAST_NET_SALES_2ND_QUARTER",
    "NxFOP2Q":      "NEXT_YEAR_FORECAST_OPERATING_PROFIT_2ND_QUARTER",
    "NxFOdP2Q":     "NEXT_YEAR_FORECAST_ORDINARY_PROFIT_2ND_QUARTER",
    "NxFNp2Q":      "NEXT_YEAR_FORECAST_PROFIT_2ND_QUARTER",
    "NxFEPS2Q":     "NEXT_YEAR_FORECAST_EARNINGS_PER_SHARE_2ND_QUARTER",
    "FSales":       "FORECAST_NET_SALES",
    "FOP":          "FORECAST_OPERATING_PROFIT",
    "FOdP":         "FORECAST_ORDINARY_PROFIT",
    "FNP":          "FORECAST_PROFIT",
    "FEPS":         "FORECAST_EARNINGS_PER_SHARE",
    "NxFSales":     "NEXT_YEAR_FORECAST_NET_SALES",
    "NxFOP":        "NEXT_YEAR_FORECAST_OPERATING_PROFIT",
    "NxFOdP":       "NEXT_YEAR_FORECAST_ORDINARY_PROFIT",
    "NxFNp":        "NEXT_YEAR_FORECAST_PROFIT",
    "NxFEPS":       "NEXT_YEAR_FORECAST_EARNINGS_PER_SHARE",
    "MatChgSub":    "MATERIAL_CHANGES_IN_SUBSIDIARIES",
    "SigChgInC":    "SIGNIFICANT_CHANGES_IN_THE_SCOPE_OF_CONSOLIDATION",
    "ChgByASRev":   "CHANGES_BASED_ON_REVISIONS_OF_ACCOUNTING_STANDARD",
    "ChgNoASRev":   "CHANGES_OTHER_THAN_ONES_BASED_ON_REVISIONS_OF_ACCOUNTING_STANDARD",
    "ChgAcEst":     "CHANGES_IN_ACCOUNTING_ESTIMATES",
    "RetroRst":     "RETROSPECTIVE_RESTATEMENT",
    "ShOutFY":      "NUMBER_OF_ISSUED_AND_OUTSTANDING_SHARES_AT_THE_END_OF_FISCAL_YEAR_INCLUDING_TREASURY_STOCK",
    "TrShFY":       "NUMBER_OF_TREASURY_STOCK_AT_THE_END_OF_FISCAL_YEAR",
    "AvgSh":        "AVERAGE_NUMBER_OF_SHARES",
    "NCSales":      "NON_CONSOLIDATED_NET_SALES",
    "NCOP":         "NON_CONSOLIDATED_OPERATING_PROFIT",
    "NCOdP":        "NON_CONSOLIDATED_ORDINARY_PROFIT",
    "NCNP":         "NON_CONSOLIDATED_PROFIT",
    "NCEPS":        "NON_CONSOLIDATED_EARNINGS_PER_SHARE",
    "NCTA":         "NON_CONSOLIDATED_TOTAL_ASSETS",
    "NCEq":         "NON_CONSOLIDATED_EQUITY",
    "NCEqAR":       "NON_CONSOLIDATED_EQUITY_TO_ASSET_RATIO",
    "NCBPS":        "NON_CONSOLIDATED_BOOK_VALUE_PER_SHARE",
    "FNCSales2Q":   "FORECAST_NON_CONSOLIDATED_NET_SALES_2ND_QUARTER",
    "FNCOP2Q":      "FORECAST_NON_CONSOLIDATED_OPERATING_PROFIT_2ND_QUARTER",
    "FNCOdP2Q":     "FORECAST_NON_CONSOLIDATED_ORDINARY_PROFIT_2ND_QUARTER",
    "FNCNP2Q":      "FORECAST_NON_CONSOLIDATED_PROFIT_2ND_QUARTER",
    "FNCEPS2Q":     "FORECAST_NON_CONSOLIDATED_EARNINGS_PER_SHARE_2ND_QUARTER",
    "NxFNCSales2Q": "NEXT_YEAR_FORECAST_NON_CONSOLIDATED_NET_SALES_2ND_QUARTER",
    "NxFNCOP2Q":    "NEXT_YEAR_FORECAST_NON_CONSOLIDATED_OPERATING_PROFIT_2ND_QUARTER",
    "NxFNCOdP2Q":   "NEXT_YEAR_FORECAST_NON_CONSOLIDATED_ORDINARY_PROFIT_2ND_QUARTER",
    "NxFNCNP2Q":    "NEXT_YEAR_FORECAST_NON_CONSOLIDATED_PROFIT_2ND_QUARTER",
    "NxFNCEPS2Q":   "NEXT_YEAR_FORECAST_NON_CONSOLIDATED_EARNINGS_PER_SHARE_2ND_QUARTER",
    "FNCSales":     "FORECAST_NON_CONSOLIDATED_NET_SALES",
    "FNCOP":        "FORECAST_NON_CONSOLIDATED_OPERATING_PROFIT",
    "FNCOdP":       "FORECAST_NON_CONSOLIDATED_ORDINARY_PROFIT",
    "FNCNP":        "FORECAST_NON_CONSOLIDATED_PROFIT",
    "FNCEPS":       "FORECAST_NON_CONSOLIDATED_EARNINGS_PER_SHARE",
    "NxFNCSales":   "NEXT_YEAR_FORECAST_NON_CONSOLIDATED_NET_SALES",
    "NxFNCOP":      "NEXT_YEAR_FORECAST_NON_CONSOLIDATED_OPERATING_PROFIT",
    "NxFNCOdP":     "NEXT_YEAR_FORECAST_NON_CONSOLIDATED_ORDINARY_PROFIT",
    "NxFNCNP":      "NEXT_YEAR_FORECAST_NON_CONSOLIDATED_PROFIT",
    "NxFNCEPS":     "NEXT_YEAR_FORECAST_NON_CONSOLIDATED_EARNINGS_PER_SHARE",
}


# BQ DATE 型カラム一覧（pyarrow に渡す前に datetime.date へ変換が必要）
_DATE_COLS: frozenset[str] = frozenset({
    "DISCLOSED_DATE",
    "CURRENT_PERIOD_START_DATE",
    "CURRENT_PERIOD_END_DATE",
    "CURRENT_FISCAL_YEAR_START_DATE",
    "CURRENT_FISCAL_YEAR_END_DATE",
    "NEXT_FISCAL_YEAR_START_DATE",
    "NEXT_FISCAL_YEAR_END_DATE",
})

# BQ STRING 型カラム一覧（数値変換対象から除外）
_BQ_STRING_COLS: frozenset[str] = frozenset({
    "LOCAL_CODE",
    "DISCLOSURE_NUMBER",
    "TYPE_OF_DOCUMENT",
    "TYPE_OF_CURRENT_PERIOD",
})

# BQ BOOLEAN 型カラム一覧（API は "true"/"false" 文字列で返すため bool 変換が必要）
_BQ_BOOL_COLS: frozenset[str] = frozenset({
    "MATERIAL_CHANGES_IN_SUBSIDIARIES",
    "SIGNIFICANT_CHANGES_IN_THE_SCOPE_OF_CONSOLIDATION",
    "CHANGES_BASED_ON_REVISIONS_OF_ACCOUNTING_STANDARD",
    "CHANGES_OTHER_THAN_ONES_BASED_ON_REVISIONS_OF_ACCOUNTING_STANDARD",
    "CHANGES_IN_ACCOUNTING_ESTIMATES",
    "RETROSPECTIVE_RESTATEMENT",
})


def preprocess(df: pd.DataFrame) -> pd.DataFrame:
    """BigQuery ロード前のクレンジング処理.

    処理内容:
        1. カラム名リネーム: API省略名 → BQ UPPER_SNAKE_CASE名
        2. 空文字列 → None 統一（API は欠損値を "" で返すため）
        3. LOCAL_CODE: 5 桁 → 4 桁（末尾を除去）
        4. DATE 型カラム: 文字列 "YYYY-MM-DD" → datetime.date
        5. DISCLOSED_TIME: 文字列 "HH:MM" → datetime.time
        6. BOOLEAN 型カラム: "true"/"false" 文字列 → bool
        7. object 型の数値カラム → pd.to_numeric（空文字→None 後の残留 object を変換）
        8. float64 → Int64: 全値が整数である列を nullable 整数型に変換
    """
    import datetime

    # 1. カラム名リネーム（BQ テーブルスキーマに合わせる）
    df = df.rename(columns=_COLUMN_MAP)

    # 2. 空文字列 → None（API は欠損値を "" で返す。pyarrow は "" を数値に変換できない）
    df = df.replace("", None)

    # 3. LOCAL_CODE: "75500" → "7550"
    if "LOCAL_CODE" in df.columns:
        df["LOCAL_CODE"] = df["LOCAL_CODE"].apply(
            lambda x: x[:4] if isinstance(x, str) and len(x) == 5 else x
        )

    # 4. DATE 型カラム: 文字列 → datetime.date（None/NaT → None）
    for col in _DATE_COLS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date

    # 5. DISCLOSED_TIME: "HH:MM" or "HH:MM:SS" → datetime.time
    if "DISCLOSED_TIME" in df.columns:
        def _to_time(x: object) -> datetime.time | None:
            if not isinstance(x, str) or not x:
                return None
            if len(x) == 5 and ":" in x:   # "HH:MM" → "HH:MM:00"
                x = x + ":00"
            try:
                return datetime.time.fromisoformat(x)
            except ValueError:
                return None
        df["DISCLOSED_TIME"] = df["DISCLOSED_TIME"].apply(_to_time)

    # 6. BOOLEAN 型カラム: "true"/"false" 文字列 → bool（None はそのまま）
    for col in _BQ_BOOL_COLS:
        if col in df.columns:
            df[col] = df[col].map(
                lambda x: True if x == "true" else (False if x == "false" else None)
            )

    # 7. 残留 object カラム（STRING/DATE/TIME/BOOL 以外）を数値に変換
    #    API が "" を返した列は step2 で None になったが、Python int が混在すると
    #    object dtype のまま残るため pd.to_numeric で float64 に統一する
    _non_numeric_cols = _BQ_STRING_COLS | _DATE_COLS | _BQ_BOOL_COLS | {"DISCLOSED_TIME"}
    for col in df.select_dtypes(include="object").columns:
        if col not in _non_numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 8. float64 → Int64（全値が整数である列のみ）
    for col in df.select_dtypes(include="float64").columns:
        non_null = df[col].dropna()
        if len(non_null) > 0 and (non_null == non_null.round()).all():
            df[col] = df[col].astype("Int64")

    return df


# ============================================================
# 3. BigQuery ロード
# ============================================================

def load_to_bq(df: pd.DataFrame, date_from: datetime, date_to: datetime) -> None:
    """対象日の既存レコードを DELETE してから APPEND ロードする.

    同日に複数回実行しても重複レコードが発生しない。
    """
    client     = get_bq_client()
    table_ref  = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

    # 対象日の既存レコードを削除（重複防止）
    delete_sql = (
        f"DELETE FROM `{table_ref}` "
        f"WHERE DISCLOSED_DATE BETWEEN '{date_from:%Y-%m-%d}' AND '{date_to:%Y-%m-%d}'"
    )
    print(f"既存レコード削除中: DISCLOSED_DATE {date_from:%Y-%m-%d} ～ {date_to:%Y-%m-%d}")
    result = client.query(delete_sql).result()
    print(f"削除完了: {result.num_dml_affected_rows} 件削除")

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )

    print("BigQuery へロード中...")
    job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
    job.result()  # 完了待機
    print(f"ロード完了: {TABLE_ID} に {job.output_rows} 件追加")


# ============================================================
# メイン
# ============================================================

def main() -> None:
    start_time = datetime.now(JST)
    log_cap    = LogCapture()
    log_cap.start()
    date_label = "不明"

    try:
        print(f"実行環境: {RUNTIME}")

        # 日付解決（--from/--to 引数 > DATE_MODE 設定ブロック）
        args = parse_args()
        if args.date_from:
            date_from = datetime.strptime(args.date_from, "%Y%m%d")
            date_to   = datetime.strptime(args.date_to or args.date_from, "%Y%m%d")
        else:
            date_from, date_to = _resolve_dates()

        date_label = (
            f"{date_from:%Y-%m-%d} ～ {date_to:%Y-%m-%d}"
            if date_from != date_to
            else f"{date_from:%Y-%m-%d}"
        )

        headers = {"x-api-key": get_jquants_api_key(RUNTIME)}

        all_data: list[dict] = []
        current = date_from

        print(f"取得期間: {date_from:%Y-%m-%d} ～ {date_to:%Y-%m-%d}")
        print("=" * 50)

        while current <= date_to:
            date_str = current.strftime("%Y%m%d")
            records  = fetch_fin_summary_by_date(date_str, headers)

            if records:
                print(f"{current:%Y-%m-%d}: {len(records)} 件取得")
                all_data.extend(records)
            # 0 件の日（土日・祝日等）は表示スキップ

            current += timedelta(days=1)

        print("=" * 50)
        print(f"合計: {len(all_data)} 件")

        if not all_data:
            print("データが 0 件のため終了します。")
            log_cap.stop()
            return

        df = pd.DataFrame(all_data)
        print(f"\nDataFrame shape: {df.shape}")
        print(f"カラム一覧: {list(df.columns)}")
        print(df.head(3).to_string())   # display() は Cloud Run 非対応のため print に統一

        df = preprocess(df)
        load_to_bq(df, date_from, date_to)

        elapsed = datetime.now(JST) - start_time

        log_cap.stop()

    except Exception as e:
        log_text = log_cap.stop()
        tb_str   = traceback.format_exc()
        print(f"[FATAL] {e}\n{tb_str}", file=sys.stderr)
        send_mail(
            f"[JQUANTS] エラー {date_label}",
            f"J-Quants /fins/summary 取得でエラーが発生しました。\n\n"
            f"対象期間  : {date_label}\n"
            f"エラー    : {e}\n\n"
            f"トレースバック:\n{tb_str}",
            attachment_text=log_text or None,
            attachment_name="jquants_log.txt",
        )
        raise


if __name__ == "__main__":
    main()

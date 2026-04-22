"""J-Quants 株価四本値 → BigQuery STOCK.STOCK_PRICE_JQUANTS ロード.

対応環境:
    - ローカルPC
    - Cloud Run Job

使い方:
    PYTHONUTF8=1 python scripts/stock_price_jquants_load.py              # daily（BQ最新日の翌日〜当日）
    PYTHONUTF8=1 python scripts/stock_price_jquants_load.py --date 20260327  # 日付指定
    PYTHONUTF8=1 python scripts/stock_price_jquants_load.py --backfill   # 全期間バックフィル
    PYTHONUTF8=1 python scripts/stock_price_jquants_load.py --backfill --from 20260101 --to 20260327
    PYTHONUTF8=1 python scripts/stock_price_jquants_load.py --dry-run    # BQ 書き込みなし
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import jpholiday
import pandas as pd
import requests
from google.cloud import bigquery

# ==========================================
# 設定
# ==========================================

PROJECT          = "gmailpj-357912"
DATASET          = "STOCK"
TABLE            = "STOCK_PRICE_JQUANTS"
KEY_FILE         = "keys/gcp-service-account.json"
JQUANTS_BASE_URL = "https://api.jquants.com/v2"
BACKFILL_FROM    = "20160328"   # J-Quants equities/bars/daily の実データ開始日（実測）
BATCH_SIZE       = 50000        # BQ バッチ INSERT 行数
SLEEP_SEC        = 0.5          # J-Quants API リクエスト間スリープ（ページング含む）
JST              = timezone(timedelta(hours=+9), "JST")


# ==========================================
# 0. 実行環境判別
# ==========================================

def detect_runtime() -> str:
    """実行環境を自動判別する.

    Returns:
        "cloudrun" | "local"
    """
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    try:
        import google.colab  # noqa: F401
        return "colab"
    except ImportError:
        return "local"


RUNTIME: str = detect_runtime()


# ==========================================
# 1. SSL パッチ（ローカルのみ）
# ==========================================

def _apply_ssl_patch() -> None:
    """Windows ローカル環境の SSLEOFError を回避するパッチ."""
    import urllib3
    from requests.adapters import HTTPAdapter

    urllib3.disable_warnings()

    class _NoVerify(HTTPAdapter):
        def send(self, req, **kw):
            kw["verify"] = False
            return super().send(req, **kw)

    _orig = requests.Session.__init__

    def _patched(self, *a, **kw):
        _orig(self, *a, **kw)
        self.mount("https://", _NoVerify())
        self.verify = False

    requests.Session.__init__ = _patched


if RUNTIME == "local":
    _apply_ssl_patch()


# ==========================================
# 2. J-Quants 認証
# ==========================================

def get_jquants_api_key() -> str:
    """環境変数 JQUANTS_API_KEY から API キーを取得する."""
    key = os.environ.get("JQUANTS_API_KEY", "")
    if not key:
        raise ValueError("JQUANTS_API_KEY が設定されていません")
    return key


# ==========================================
# 3. J-Quants 株価取得
# ==========================================

def fetch_eq_bars_one_day(date_str: str, api_key: str) -> list[dict]:
    """指定日の全銘柄株価四本値を取得する（pagination対応）.

    Args:
        date_str: YYYYMMDD 形式
        api_key: J-Quants API キー

    Returns:
        レコードリスト。非取引日・範囲外の場合は空リスト。
    """
    records: list[dict] = []
    pagination_key: str | None = None
    headers = {"x-api-key": api_key}

    while True:
        time.sleep(SLEEP_SEC)
        params: dict = {"date": date_str}
        if pagination_key:
            params["pagination_key"] = pagination_key

        r = requests.get(
            f"{JQUANTS_BASE_URL}/equities/bars/daily",
            params=params,
            headers=headers,
        )
        if r.status_code in (400, 403):
            return []
        r.raise_for_status()

        body = r.json()
        records.extend(body.get("data", []))
        pagination_key = body.get("pagination_key") or None
        if not pagination_key:
            break

    return records


# ==========================================
# 4. BigQuery 操作
# ==========================================

def get_bq_client() -> bigquery.Client:
    """実行環境に応じた BigQuery クライアントを返す."""
    if RUNTIME == "local":
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(KEY_FILE)
        return bigquery.Client(project=PROJECT, credentials=creds)
    else:
        return bigquery.Client(project=PROJECT)


def create_table_if_not_exists(client: bigquery.Client) -> None:
    """STOCK_PRICE_JQUANTS テーブルが存在しなければ作成する."""
    table_ref = f"{PROJECT}.{DATASET}.{TABLE}"
    schema = [
        bigquery.SchemaField("DATE",         "DATE",    mode="REQUIRED"),
        bigquery.SchemaField("CODE5",        "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("TICKER",       "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("IS_PREFERRED", "BOOL",    mode="REQUIRED"),
        bigquery.SchemaField("OPEN",         "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("HIGH",         "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("LOW",          "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("CLOSE",        "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("VOLUME",       "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("TURNOVER",     "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("STOP_HIGH",    "STRING",  mode="NULLABLE"),
        bigquery.SchemaField("STOP_LOW",     "STRING",  mode="NULLABLE"),
        bigquery.SchemaField("ADJ_FACTOR",   "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("ADJ_OPEN",     "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("ADJ_HIGH",     "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("ADJ_LOW",      "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("ADJ_CLOSE",    "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("ADJ_VOLUME",   "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("LOADED_AT",    "DATETIME", mode="NULLABLE"),
    ]
    bq_table = bigquery.Table(table_ref, schema=schema)
    bq_table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="DATE",
    )
    bq_table.clustering_fields = ["TICKER"]
    try:
        client.create_table(bq_table)
        print(f"テーブル作成: {table_ref}")
    except Exception as e:
        if "Already Exists" in str(e):
            print(f"テーブル既存: {table_ref}")
        else:
            raise


def get_latest_date(client: bigquery.Client) -> str | None:
    """BQ の最新 DATE を YYYY-MM-DD 形式で返す。データなしの場合 None。"""
    query = f"SELECT CAST(MAX(DATE) AS STRING) AS d FROM `{PROJECT}.{DATASET}.{TABLE}`"
    rows = list(client.query(query).result())
    return rows[0].d if rows and rows[0].d else None


def get_existing_dates(client: bigquery.Client) -> set[str]:
    """BQ に既存の DATE 一覧を返す（YYYY-MM-DD 形式）。バックフィル重複チェック用。"""
    query = f"SELECT DISTINCT CAST(DATE AS STRING) AS d FROM `{PROJECT}.{DATASET}.{TABLE}`"
    rows = client.query(query).result()
    return {row.d for row in rows}


def delete_date(client: bigquery.Client, date_ymd: str, dry_run: bool) -> None:
    """指定日のレコードを削除する（リラン対応）.

    Args:
        date_ymd: YYYY-MM-DD 形式
    """
    if dry_run:
        print(f"  [dry-run] DELETE DATE={date_ymd} スキップ")
        return
    query = f"DELETE FROM `{PROJECT}.{DATASET}.{TABLE}` WHERE DATE = '{date_ymd}'"
    client.query(query).result()
    print(f"  DELETE: DATE={date_ymd}")


def bq_insert(client: bigquery.Client, rows: list[dict], dry_run: bool = False) -> None:
    """rows を BQ に WRITE_APPEND でバッチロードする."""
    if not rows:
        return
    if dry_run:
        print(f"  [dry-run] {len(rows)} 行 INSERT スキップ")
        return

    table_ref = f"{PROJECT}.{DATASET}.{TABLE}"
    df = pd.DataFrame(rows)
    df["DATE"]      = pd.to_datetime(df["DATE"]).dt.date
    df["LOADED_AT"] = pd.to_datetime(df["LOADED_AT"])

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
    job.result()
    print(f"  BQ INSERT: {len(rows)} 行 → {table_ref}")


# ==========================================
# 5. 変換ヘルパー
# ==========================================

def to_bq_rows(records: list[dict]) -> list[dict]:
    """J-Quants レスポンスを BQ INSERT 用行に変換する."""
    loaded_at = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for r in records:
        code5 = r["Code"]
        rows.append({
            "DATE":         r["Date"],
            "CODE5":        code5,
            "TICKER":       code5[:4],
            "IS_PREFERRED": len(code5) == 5 and code5[4] != "0",
            "OPEN":         float(r["O"])         if r.get("O")         is not None else None,
            "HIGH":         float(r["H"])         if r.get("H")         is not None else None,
            "LOW":          float(r["L"])         if r.get("L")         is not None else None,
            "CLOSE":        float(r["C"])         if r.get("C")         is not None else None,
            "VOLUME":       float(r["Vo"])        if r.get("Vo")        is not None else None,
            "TURNOVER":     float(r["Va"])        if r.get("Va")        is not None else None,
            "STOP_HIGH":    r.get("UL"),
            "STOP_LOW":     r.get("LL"),
            "ADJ_FACTOR":   float(r["AdjFactor"]) if r.get("AdjFactor") is not None else None,
            "ADJ_OPEN":     float(r["AdjO"])      if r.get("AdjO")      is not None else None,
            "ADJ_HIGH":     float(r["AdjH"])      if r.get("AdjH")      is not None else None,
            "ADJ_LOW":      float(r["AdjL"])      if r.get("AdjL")      is not None else None,
            "ADJ_CLOSE":    float(r["AdjC"])      if r.get("AdjC")      is not None else None,
            "ADJ_VOLUME":   float(r["AdjVo"])     if r.get("AdjVo")     is not None else None,
            "LOADED_AT":    loaded_at,
        })
    return rows


def today_jst() -> str:
    """JST での当日日付を YYYYMMDD 形式で返す.

    _generate_business_days() が営業日フィルタを行うため、
    end_date は「当日」をそのまま渡せばよい。
    以前の today_or_prev_business_day() は週末・祝日に前営業日を返していたが、
    BQ 最新日と一致して from > end になり 0 日取得となるバグがあった。
    """
    return datetime.now(JST).strftime("%Y%m%d")


def yyyymmdd_to_ymd(s: str) -> str:
    """YYYYMMDD → YYYY-MM-DD"""
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _generate_business_days(from_str: str, to_str: str) -> list[str]:
    """from_str〜to_str の日本の営業日を YYYYMMDD リストで返す."""
    from_dt = datetime.strptime(from_str, "%Y%m%d").date()
    to_dt   = datetime.strptime(to_str,   "%Y%m%d").date()
    result  = []
    cur = from_dt
    while cur <= to_dt:
        if cur.weekday() < 5 and not jpholiday.is_holiday(cur):
            result.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return result


# ==========================================
# 6. daily モード
# ==========================================

def run_daily(
    client: bigquery.Client,
    api_key: str,
    target_date: str | None,
    dry_run: bool,
) -> None:
    """当日（または指定日）までのギャップを埋める。

    BQ 最新日の翌営業日〜当日（または指定日）の範囲で営業日を順にロードする。
    非営業日は _generate_business_days() でフィルタされるため、
    end_date が週末・祝日でも問題ない。
    各日は DELETE → INSERT でリラン安全。
    """
    end_date = target_date if target_date else today_jst()

    latest = get_latest_date(client)
    if latest is None:
        from_str = BACKFILL_FROM
    else:
        from_dt  = datetime.strptime(latest, "%Y-%m-%d").date() + timedelta(days=1)
        from_str = from_dt.strftime("%Y%m%d")

    pending = _generate_business_days(from_str, end_date)
    print(f"[daily] 補完対象: {len(pending)} 日 ({from_str} 〜 {end_date})")

    for date_str in pending:
        date_ymd = yyyymmdd_to_ymd(date_str)
        print(f"  処理中: {date_ymd}")
        delete_date(client, date_ymd, dry_run)

        records = fetch_eq_bars_one_day(date_str, api_key)
        if not records:
            print(f"  データなし（非取引日 or 未公開）: {date_ymd}")
            continue

        rows = to_bq_rows(records)
        bq_insert(client, rows, dry_run)
        print(f"  完了: {date_ymd} {len(records)} 銘柄")

    print("[daily] 完了")


# ==========================================
# 7. backfill モード
# ==========================================

def run_backfill(
    client: bigquery.Client,
    api_key: str,
    from_str: str,
    to_str: str,
    dry_run: bool,
) -> None:
    """全期間バックフィル。既存日はスキップ（中断再開可能）。"""
    print(f"[backfill] {from_str} 〜 {to_str}")

    trading_dates = _generate_business_days(from_str, to_str)
    print(f"  営業日候補: {len(trading_dates)} 日")

    existing = get_existing_dates(client)
    print(f"  BQ 既存: {len(existing)} 日")

    pending = [d for d in trading_dates if yyyymmdd_to_ymd(d) not in existing]
    print(f"  未取得: {len(pending)} 日")

    batch: list[dict] = []
    for i, date_str in enumerate(pending, 1):
        if i % 20 == 1:
            print(f"  [{i}/{len(pending)}] {date_str} ...")

        records = fetch_eq_bars_one_day(date_str, api_key)
        if records:
            batch.extend(to_bq_rows(records))

        if len(batch) >= BATCH_SIZE:
            bq_insert(client, batch, dry_run)
            batch = []

    if batch:
        bq_insert(client, batch, dry_run)

    print("[backfill] 完了")


# ==========================================
# 8. main
# ==========================================

def main() -> None:
    """エントリポイント."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="J-Quants 株価四本値 → BQ STOCK_PRICE_JQUANTS ロード"
    )
    parser.add_argument("--backfill", action="store_true", help="全期間バックフィル")
    parser.add_argument("--date",     type=str, default=None,
                        help="対象日 YYYYMMDD（daily 時）")
    parser.add_argument("--from",     dest="from_str", type=str, default=BACKFILL_FROM,
                        help="バックフィル開始日 YYYYMMDD")
    parser.add_argument("--to",       dest="to_str",   type=str, default=None,
                        help="バックフィル終了日 YYYYMMDD（未指定=今日）")
    parser.add_argument("--dry-run",  action="store_true", help="BQ 書き込みを行わない")
    args = parser.parse_args()

    print(f"RUNTIME: {RUNTIME}")
    print(f"モード: {'backfill' if args.backfill else 'daily'}")

    api_key = get_jquants_api_key()
    print("J-Quants API キー OK")

    client = get_bq_client()
    create_table_if_not_exists(client)

    if args.backfill:
        to_str = args.to_str or datetime.now(JST).strftime("%Y%m%d")
        run_backfill(client, api_key, args.from_str, to_str, args.dry_run)
    else:
        run_daily(client, api_key, args.date, args.dry_run)


if __name__ == "__main__":
    main()

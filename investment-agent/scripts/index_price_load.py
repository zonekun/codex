"""J-Quants 全インデックス日次データ + 日経225 → BigQuery STOCK.INDEX_PRICE ロード.

対応環境:
    - ローカルPC
    - Cloud Run Job

使い方:
    PYTHONUTF8=1 python scripts/index_price_load.py              # daily（前営業日）
    PYTHONUTF8=1 python scripts/index_price_load.py --date 20260313  # 日付指定
    PYTHONUTF8=1 python scripts/index_price_load.py --backfill   # 全期間バックフィル
    PYTHONUTF8=1 python scripts/index_price_load.py --dry-run    # BQ 書き込みなし
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import jpholiday
import pandas as pd
import requests
import yfinance as yf
from google.cloud import bigquery

# ==========================================
# 設定
# ==========================================

PROJECT          = "gmailpj-357912"
DATASET          = "STOCK"
TABLE            = "INDEX_PRICE"
KEY_FILE         = "keys/gcp-service-account.json"
JQUANTS_BASE_URL = "https://api.jquants.com/v2"
BACKFILL_FROM    = "20160305"   # J-Quants Standard プランの最古日
BATCH_SIZE       = 5000         # BQ バッチ INSERT 行数
SLEEP_SEC        = 0.3          # J-Quants API リクエスト間スリープ
JST              = timezone(timedelta(hours=+9), "JST")


# ==========================================
# 0. 実行環境判別
# ==========================================

def detect_runtime() -> str:
    """実行環境を自動判別する.

    Returns:
        "cloudrun" | "colab_personal" | "colab_enterprise" | "local"
    """
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    try:
        import google.colab  # noqa: F401
        if os.environ.get("GOOGLE_CLOUD_PROJECT"):
            return "colab_enterprise"
        return "colab_personal"
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
# 2. J-Quants 認証（API キー取得）
# ==========================================

def get_jquants_api_key() -> str:
    """環境変数 JQUANTS_API_KEY から API キーを取得する."""
    key = os.environ.get("JQUANTS_API_KEY", "")
    if not key:
        raise ValueError("JQUANTS_API_KEY が設定されていません")
    return key


# ==========================================
# 3. J-Quants インデックス取得
# ==========================================

def fetch_indices_one_day(date_str: str, api_key: str) -> list[dict]:
    """指定日の全74指数を取得する.

    Args:
        date_str: YYYYMMDD 形式
        api_key: J-Quants API キー

    Returns:
        [{"Date": "YYYY-MM-DD", "Code": "0000", "Open": ..., ...}, ...]
        非取引日・範囲外の場合は空リスト。
    """
    time.sleep(SLEEP_SEC)
    headers = {"x-api-key": api_key}
    r = requests.get(
        f"{JQUANTS_BASE_URL}/indices/bars/daily",
        params={"date": date_str},
        headers=headers,
    )
    if r.status_code in (400, 403):
        return []
    r.raise_for_status()
    return r.json().get("data", [])


def fetch_topix_range(from_str: str, to_str: str, api_key: str) -> list[dict]:
    """TOPIX の期間データを取得（取引日一覧取得用）.

    Args:
        from_str, to_str: YYYYMMDD 形式
        api_key: J-Quants API キー

    Returns:
        [{"Date": "YYYY-MM-DD", "Open": ..., "High": ..., "Low": ..., "Close": ...}, ...]
    """
    headers = {"x-api-key": api_key}
    r = requests.get(
        f"{JQUANTS_BASE_URL}/indices/bars/daily/topix",
        params={"from": from_str, "to": to_str},
        headers=headers,
    )
    r.raise_for_status()
    return r.json().get("data", [])


# ==========================================
# 4. yfinance 日経225 取得
# ==========================================

def fetch_nk225(from_ymd: str, to_ymd_exclusive: str) -> list[dict]:
    """yfinance で日経225 OHLC を取得する.

    Args:
        from_ymd: YYYY-MM-DD 形式（inclusive）
        to_ymd_exclusive: YYYY-MM-DD 形式（exclusive: yfinance の end は含まない）

    Returns:
        [{"Date": "YYYY-MM-DD", "Code": "N225", "Open": ..., "SOURCE": "yfinance"}, ...]
    """
    df = yf.download(
        "^N225",
        start=from_ymd,
        end=to_ymd_exclusive,
        auto_adjust=True,
        progress=False,
    )
    if df.empty:
        return []

    # MultiIndex 解消（yfinance >= 0.2.40）
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    df = df[["Open", "High", "Low", "Close"]].dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None)

    rows = []
    for dt, row in df.iterrows():
        rows.append({
            "Date":  dt.strftime("%Y-%m-%d"),
            "Code":  "N225",
            "Open":  float(row["Open"]),
            "High":  float(row["High"]),
            "Low":   float(row["Low"]),
            "Close": float(row["Close"]),
        })
    return rows


# ==========================================
# 5. BigQuery 操作
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
    """INDEX_PRICE テーブルが存在しなければ作成する."""
    table_ref = f"{PROJECT}.{DATASET}.{TABLE}"
    schema = [
        bigquery.SchemaField("DATE",       "DATE",     mode="REQUIRED"),
        bigquery.SchemaField("INDEX_CODE", "STRING",   mode="REQUIRED"),
        bigquery.SchemaField("OPEN",       "FLOAT64",  mode="NULLABLE"),
        bigquery.SchemaField("HIGH",       "FLOAT64",  mode="NULLABLE"),
        bigquery.SchemaField("LOW",        "FLOAT64",  mode="NULLABLE"),
        bigquery.SchemaField("CLOSE",      "FLOAT64",  mode="NULLABLE"),
        bigquery.SchemaField("SOURCE",     "STRING",   mode="NULLABLE"),
        bigquery.SchemaField("LOADED_AT",  "DATETIME", mode="NULLABLE"),
    ]
    bq_table = bigquery.Table(table_ref, schema=schema)
    bq_table.clustering_fields = ["DATE"]
    try:
        client.create_table(bq_table)
        print(f"テーブル作成: {table_ref}")
    except Exception as e:
        if "Already Exists" in str(e):
            print(f"テーブル既存: {table_ref}")
        else:
            raise


def get_existing_dates_for_code(client: bigquery.Client, index_code: str) -> set[str]:
    """BQ に既存の DATE 一覧を返す（YYYY-MM-DD 形式）.

    Args:
        index_code: INDEX_CODE でフィルタ（例: "0000", "N225"）
    """
    query = f"""
    SELECT DISTINCT CAST(DATE AS STRING) AS d
    FROM `{PROJECT}.{DATASET}.{TABLE}`
    WHERE INDEX_CODE = @code
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("code", "STRING", index_code)]
    )
    rows = client.query(query, job_config=job_config).result()
    return {row.d for row in rows}


def bq_insert(client: bigquery.Client, rows: list[dict], dry_run: bool = False) -> None:
    """rows を BQ に WRITE_APPEND でバッチロードする."""
    if not rows:
        return
    if dry_run:
        print(f"  [dry-run] {len(rows)} 行 INSERT スキップ")
        return

    table_ref = f"{PROJECT}.{DATASET}.{TABLE}"
    df = pd.DataFrame(rows)

    # DATE 列を date 型に変換
    df["DATE"] = pd.to_datetime(df["DATE"]).dt.date
    # LOADED_AT 列を datetime 型に変換
    df["LOADED_AT"] = pd.to_datetime(df["LOADED_AT"])

    # テーブル既存のため schema は渡さない（スキーマは CREATE 済みのものを使用）
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
    job.result()
    print(f"  BQ INSERT: {len(rows)} 行 → {table_ref}")


# ==========================================
# 6. 変換ヘルパー
# ==========================================

def to_bq_rows(records: list[dict], source: str = "jquants") -> list[dict]:
    """J-Quants V2 API レスポンスを BQ INSERT 用行に変換する.

    V2 API ではカラム名が O/H/L/C（短縮形）に変更されています。
    """
    loaded_at = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    return [
        {
            "DATE":       r["Date"],
            "INDEX_CODE": r["Code"],
            "OPEN":       float(r["O"])  if r.get("O")  is not None else None,
            "HIGH":       float(r["H"])  if r.get("H")  is not None else None,
            "LOW":        float(r["L"])  if r.get("L")  is not None else None,
            "CLOSE":      float(r["C"])  if r.get("C")  is not None else None,
            "SOURCE":     source,
            "LOADED_AT":  loaded_at,
        }
        for r in records
    ]


def nk225_to_bq_rows(records: list[dict]) -> list[dict]:
    """yfinance NK225 レスポンスを BQ INSERT 用行に変換する."""
    loaded_at = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    return [
        {
            "DATE":       r["Date"],
            "INDEX_CODE": r["Code"],
            "OPEN":       r["Open"],
            "HIGH":       r["High"],
            "LOW":        r["Low"],
            "CLOSE":      r["Close"],
            "SOURCE":     "yfinance",
            "LOADED_AT":  loaded_at,
        }
        for r in records
    ]


def prev_business_day() -> str:
    """前営業日を YYYYMMDD 形式で返す."""
    dt = datetime.now(JST).date() - timedelta(days=1)
    while dt.weekday() >= 5 or jpholiday.is_holiday(dt):
        dt -= timedelta(days=1)
    return dt.strftime("%Y%m%d")


def yyyymmdd_to_ymd(s: str) -> str:
    """YYYYMMDD → YYYY-MM-DD"""
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


# ==========================================
# 7. daily モード
# ==========================================

def get_latest_date_for_code(client: bigquery.Client, index_code: str) -> str | None:
    """BQ の最新 DATE を YYYY-MM-DD 形式で返す。データなしの場合 None。"""
    query = f"""
    SELECT CAST(MAX(DATE) AS STRING) AS d
    FROM `{PROJECT}.{DATASET}.{TABLE}`
    WHERE INDEX_CODE = @code
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("code", "STRING", index_code)]
    )
    rows = list(client.query(query, job_config=job_config).result())
    return rows[0].d if rows and rows[0].d else None


def run_daily(
    client: bigquery.Client,
    api_key: str,
    target_date: str | None,
    dry_run: bool,
) -> None:
    """前営業日（または指定日）までのギャップを埋める。

    BQ 最新日の翌営業日〜前営業日（または指定日）まで全日をループして
    欠損を一括補完する。
    """
    end_date = target_date if target_date else prev_business_day()
    end_ymd  = yyyymmdd_to_ymd(end_date)

    # ---- J-Quants 74指数 ----
    latest_jq = get_latest_date_for_code(client, "0000")
    if latest_jq is None:
        from_str_jq = BACKFILL_FROM
    else:
        from_dt = datetime.strptime(latest_jq, "%Y-%m-%d").date() + timedelta(days=1)
        from_str_jq = from_dt.strftime("%Y%m%d")

    pending_jq = _generate_business_days(from_str_jq, end_date)
    print(f"[daily] J-Quants 補完対象: {len(pending_jq)} 日 ({from_str_jq} 〜 {end_date})")

    batch: list[dict] = []
    for date_str in pending_jq:
        records = fetch_indices_one_day(date_str, api_key)
        if records:
            batch.extend(to_bq_rows(records))
            print(f"  J-Quants: {yyyymmdd_to_ymd(date_str)} {len(records)} 指数取得")
        else:
            print(f"  J-Quants: {yyyymmdd_to_ymd(date_str)} データなし（非取引日 or 未公開）")
    if batch:
        bq_insert(client, batch, dry_run)

    # ---- NK225 ----
    latest_n225 = get_latest_date_for_code(client, "N225")
    if latest_n225 is None:
        from_ymd_n225 = yyyymmdd_to_ymd(BACKFILL_FROM)
    else:
        from_dt_n225 = datetime.strptime(latest_n225, "%Y-%m-%d").date() + timedelta(days=1)
        from_ymd_n225 = from_dt_n225.strftime("%Y-%m-%d")

    if from_ymd_n225 <= end_ymd:
        to_exclusive = (datetime.strptime(end_ymd, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"[daily] NK225 補完対象: {from_ymd_n225} 〜 {end_ymd}")
        nk_records = fetch_nk225(from_ymd_n225, to_exclusive)
        existing_n225 = get_existing_dates_for_code(client, "N225")
        nk_new = [r for r in nk_records if r["Date"] not in existing_n225]
        if nk_new:
            bq_insert(client, nk225_to_bq_rows(nk_new), dry_run)
            print(f"  NK225: {len(nk_new)} 行 INSERT")
        else:
            print(f"  NK225: 新規データなし")
    else:
        print(f"[daily] NK225: BQ最新 ({latest_n225}) が補完終端 ({end_ymd}) 以降。スキップ。")

    print(f"[daily] 完了")


# ==========================================
# 8. backfill モード
# ==========================================

def _generate_business_days(from_str: str, to_str: str) -> list[str]:
    """from_str〜to_str の日本の営業日（平日かつ祝日でない日）を YYYYMMDD リストで返す."""
    from_dt = datetime.strptime(from_str, "%Y%m%d").date()
    to_dt   = datetime.strptime(to_str,   "%Y%m%d").date()
    result  = []
    cur = from_dt
    while cur <= to_dt:
        if cur.weekday() < 5 and not jpholiday.is_holiday(cur):
            result.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return result


def run_backfill(
    client: bigquery.Client,
    api_key: str,
    from_str: str,
    to_str: str,
    dry_run: bool,
) -> None:
    """全期間バックフィル."""
    print(f"[backfill] {from_str} 〜 {to_str}")

    # jpholiday で営業日候補を生成（API で非取引日は 400 が返り自動スキップ）
    print("[backfill] 営業日候補を生成中...")
    trading_dates = _generate_business_days(from_str, to_str)
    print(f"  -> 営業日候補: {len(trading_dates)} 日")

    # --- J-Quants 74指数 backfill ---
    existing_jq = get_existing_dates_for_code(client, "0000")
    print(f"  -> J-Quants BQ 既存: {len(existing_jq)} 日")
    pending = [d for d in trading_dates if yyyymmdd_to_ymd(d) not in existing_jq]
    print(f"  -> J-Quants 未取得: {len(pending)} 日")

    batch: list[dict] = []
    for i, date_str in enumerate(pending, 1):
        if i % 50 == 1:
            print(f"  [{i}/{len(pending)}] {date_str} ...")
        records = fetch_indices_one_day(date_str, api_key)
        if records:
            batch.extend(to_bq_rows(records))
        if len(batch) >= BATCH_SIZE:
            bq_insert(client, batch, dry_run)
            batch = []
    if batch:
        bq_insert(client, batch, dry_run)

    # --- NK225 backfill ---
    print("[backfill] NK225 全期間取得中...")
    from_ymd = yyyymmdd_to_ymd(from_str)
    to_exclusive = (datetime.strptime(yyyymmdd_to_ymd(to_str), "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    nk_records = fetch_nk225(from_ymd, to_exclusive)

    existing_n225 = get_existing_dates_for_code(client, "N225")
    nk_new = [r for r in nk_records if r["Date"] not in existing_n225]
    print(f"  -> NK225: {len(nk_records)} 行取得 / うち新規 {len(nk_new)} 行")
    if nk_new:
        bq_insert(client, nk225_to_bq_rows(nk_new), dry_run)

    print(f"[backfill] 完了")


# ==========================================
# 9. main
# ==========================================

def main() -> None:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="J-Quants インデックスデータ → BQ INDEX_PRICE ロード")
    parser.add_argument("--backfill", action="store_true",  help="全期間バックフィル")
    parser.add_argument("--date",     type=str, default=None, help="対象日 YYYYMMDD（daily 時）")
    parser.add_argument("--from",     dest="from_str", type=str, default=BACKFILL_FROM,
                        help="バックフィル開始日 YYYYMMDD")
    parser.add_argument("--to",       dest="to_str",   type=str, default=None,
                        help="バックフィル終了日 YYYYMMDD（未指定=今日）")
    parser.add_argument("--dry-run",  action="store_true", help="BQ 書き込みを行わない")
    args = parser.parse_args()

    print(f"RUNTIME: {RUNTIME}")
    print(f"モード: {'backfill' if args.backfill else 'daily'}")

    # J-Quants API キー取得
    api_key = get_jquants_api_key()
    print("J-Quants API キー OK")

    # BQ クライアント + テーブル確認
    client = get_bq_client()
    create_table_if_not_exists(client)

    if args.backfill:
        to_str = args.to_str or datetime.now(JST).strftime("%Y%m%d")
        run_backfill(client, api_key, args.from_str, to_str, args.dry_run)
    else:
        run_daily(client, api_key, args.date, args.dry_run)


if __name__ == "__main__":
    main()

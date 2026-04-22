"""type c 企業の IR URL 調査用情報収集スクリプト。

Step 1（月次 IR URL 自動収集プログラム）の設計に必要な情報を収集する。

入力:
  - data/monthly_disclosure_master.csv  ← type c 企業一覧
  - GCS monthlydata/{ticker}/structure.json ← 月次開示 KPI 項目（buffett-code スクレイプ済み）
  - BQ STOCK.STOCK_CODE_LIST            ← 会社名補完

※ 四季報 URL カラムは現版では空のため使用しない

出力:
  - data/type_c_ir_survey.csv
  - GCS: gs://stock_data_1930932/config/type_c_ir_survey.csv

出力 CSV スキーマ:
  TICKER, COMPANY_NAME, IR_URL_CURRENT, MONTHLY_ITEMS, STRUCTURE_SOURCE,
  HAS_STRUCTURE_JSON, NOTES
"""
import csv
import io
import json
import logging
import os
import sys
from pathlib import Path

from google.cloud import bigquery, storage
from google.oauth2 import service_account

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ---- 実行環境判別 ----
IS_CLOUD_RUN = bool(os.environ.get("CLOUD_RUN_JOB"))

# ---- 設定 ----
BASE_DIR = Path(__file__).resolve().parent.parent
MASTER_CSV_LOCAL = BASE_DIR / "data" / "monthly_disclosure_master.csv"
SURVEY_CSV_LOCAL = BASE_DIR / "data" / "type_c_ir_survey.csv"
GCS_BUCKET = "stock_data_1930932"
GCS_MASTER_PATH = "config/monthly_disclosure_master.csv"
GCS_SURVEY_PATH = "config/type_c_ir_survey.csv"
KEY_FILE = BASE_DIR / "keys" / "gcp-service-account.json"


def _get_credentials():
    """実行環境に応じた GCP 認証情報を返す。"""
    if IS_CLOUD_RUN:
        import google.auth
        creds, _ = google.auth.default()
        return creds
    # ローカル: SSL 回避パッチを適用してからキーファイル認証
    try:
        import urllib3, requests as _req
        from requests.adapters import HTTPAdapter as _HA
        urllib3.disable_warnings()
        class _NoVerify(_HA):
            def send(self, req, **kw):
                kw["verify"] = False
                return super().send(req, **kw)
        _orig = _req.Session.__init__
        def _p(self, *a, **kw):
            _orig(self, *a, **kw)
            self.mount("https://", _NoVerify())
            self.verify = False
        _req.Session.__init__ = _p
    except ImportError:
        pass
    return service_account.Credentials.from_service_account_file(str(KEY_FILE))


def load_type_c_companies(bucket) -> list[dict]:
    """monthly_disclosure_master.csv から type c 企業を読み込む。
    Cloud Run: GCS から読む / ローカル: ローカルファイルから読む。
    """
    if IS_CLOUD_RUN:
        blob = bucket.blob(GCS_MASTER_PATH)
        text = blob.download_as_text(encoding="utf-8")
        f = io.StringIO(text)
    else:
        f = open(MASTER_CSV_LOCAL, encoding="utf-8")

    companies = []
    try:
        reader = csv.DictReader(f)
        for row in reader:
            if row["DISCLOSURE_TYPE"].strip() == "c":
                companies.append({
                    "ticker": row["TICKER"].strip(),
                    "company_name": row["COMPANY_NAME"].strip(),
                    "ir_url_current": row["IR_URL"].strip(),
                })
    finally:
        if not IS_CLOUD_RUN:
            f.close()

    logger.info("type c 企業: %d社", len(companies))
    return companies


def load_company_names_from_bq(tickers: list[str], bq_client) -> dict[str, str]:
    """BQ STOCK_CODE_LIST から会社名を取得。"""
    query = """
    SELECT TICKER, STOCK_NAME
    FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
    WHERE TICKER IN UNNEST(@tickers)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("tickers", "STRING", tickers)]
    )
    df = bq_client.query(query, job_config=job_config).to_dataframe()
    result = dict(zip(df["TICKER"].astype(str), df["STOCK_NAME"]))
    logger.info("BQ 会社名取得: %d社", len(result))
    return result


def load_structure_jsons(tickers: list[str], bucket) -> dict[str, dict]:
    """GCS から structure.json を一括取得。存在しないものはスキップ。"""
    results = {}
    for ticker in tickers:
        blob = bucket.blob(f"monthlydata/{ticker}/structure.json")
        try:
            data = json.loads(blob.download_as_text())
            results[ticker] = data
        except Exception:
            pass  # 存在しない場合はスキップ

    logger.info("structure.json 取得: %d件 / %d件", len(results), len(tickers))
    return results


def extract_kpi_summary(structure: dict) -> str:
    """structure.json の monthly_items から主要 KPI 名を抽出して文字列化。"""
    items = structure.get("monthly_items", [])
    # 年数値・月名・空文字は除外し、意味のある項目名だけ抽出
    skip_patterns = {"年", "月", ""}
    kpi_names = []
    for item in items:
        name = item.get("name", "").strip()
        if not name:
            continue
        # 純粋な年数値（例: "2023年"）や月名（例: "1月"）はスキップ
        if name.endswith("年") or (name.endswith("月") and len(name) <= 3):
            continue
        if name not in kpi_names:
            kpi_names.append(name)
        if len(kpi_names) >= 5:  # 最大5件
            break
    return " / ".join(kpi_names)


def main() -> None:
    logger.info("実行環境: %s", "Cloud Run" if IS_CLOUD_RUN else "ローカル")

    # GCS / BQ クライアント初期化
    creds = _get_credentials()
    gcs_client = storage.Client(project="gmailpj-357912", credentials=creds)
    bq_client = bigquery.Client(project="gmailpj-357912", credentials=creds)
    bucket = gcs_client.bucket(GCS_BUCKET)

    # ① type c 企業読み込み（Cloud Run: GCS / ローカル: ローカルファイル）
    companies = load_type_c_companies(bucket)
    tickers = [c["ticker"] for c in companies]

    # ② BQ から会社名を補完
    bq_names = load_company_names_from_bq(tickers, bq_client)

    # ③ GCS structure.json 一括取得
    structures = load_structure_jsons(tickers, bucket)

    # ④ 統合してサーベイ CSV 作成
    rows = []
    for c in companies:
        ticker = c["ticker"]
        struct = structures.get(ticker, {})
        kpi_summary = extract_kpi_summary(struct) if struct else ""
        # 会社名: マスタに入っていればそちらを優先、なければ BQ から補完
        company_name = c["company_name"] or bq_names.get(ticker, "")
        # 備考: 状況をメモ
        notes = []
        if not c["ir_url_current"]:
            notes.append("IR_URL未設定")
        if not struct:
            notes.append("structure.jsonなし")
        elif not kpi_summary:
            notes.append("KPI項目抽出不可")
        rows.append({
            "TICKER": ticker,
            "COMPANY_NAME": company_name,
            "IR_URL_CURRENT": c["ir_url_current"],
            "MONTHLY_ITEMS": kpi_summary,
            "STRUCTURE_SOURCE": struct.get("source", ""),
            "HAS_STRUCTURE_JSON": "Y" if struct else "N",
            "NOTES": " / ".join(notes),
        })

    # ⑤ CSV テキストを生成
    fieldnames = ["TICKER", "COMPANY_NAME", "IR_URL_CURRENT",
                  "MONTHLY_ITEMS", "STRUCTURE_SOURCE", "HAS_STRUCTURE_JSON", "NOTES"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    csv_text = buf.getvalue()

    # ⑥ GCS アップロード
    blob = bucket.blob(GCS_SURVEY_PATH)
    blob.upload_from_string(csv_text.encode("utf-8"), content_type="text/csv; charset=utf-8")
    logger.info("GCS アップロード: gs://%s/%s", GCS_BUCKET, GCS_SURVEY_PATH)

    # ⑦ ローカル保存（ローカル実行時のみ）
    if not IS_CLOUD_RUN:
        SURVEY_CSV_LOCAL.write_text(csv_text, encoding="utf-8")
        logger.info("ローカル保存: %s", SURVEY_CSV_LOCAL)

    # ⑦ サマリー表示
    has_ir_url = sum(1 for r in rows if r["IR_URL_CURRENT"])
    has_structure = sum(1 for r in rows if r["HAS_STRUCTURE_JSON"] == "Y")
    no_ir_no_struct = sum(1 for r in rows if not r["IR_URL_CURRENT"] and r["HAS_STRUCTURE_JSON"] == "N")
    logger.info("=== サマリー ===")
    logger.info("  type c 総数:              %d社", len(rows))
    logger.info("  IR_URL 登録済み:           %d社", has_ir_url)
    logger.info("  structure.json あり:       %d社", has_structure)
    logger.info("  IR_URL も structure もなし: %d社", no_ir_no_struct)
    logger.info("  MONTHLY_ITEMS 抽出できた:   %d社",
                sum(1 for r in rows if r["MONTHLY_ITEMS"]))


if __name__ == "__main__":
    main()

"""EDINET 大量保有変更報告書 遅延提出 — 過去データバックフィル.

2021/01/01〜2025/01/06 の範囲で EDINET API を叩き、
遅延報告（60日超）を GCS CSV に保存する一回限りのジョブ。

出力先: gs://stock_data_1930932/edinet_delay/backfill.csv
"""

import csv
import io
import os
import sys
import time
import traceback
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import send_mail, LogCapture

# --- 定数 ---
JST = timezone(timedelta(hours=+9), "JST")

# Phase 1: 2021/01/01〜2022/02/25 → 完了（タイムアウトで消失、再取得）
# Phase 2: 2022/02/26〜2025/01/06
START_DATE = os.environ.get("BACKFILL_START", "2021/01/01")
END_DATE = os.environ.get("BACKFILL_END", "2025/01/06")
DELAY_THRESHOLD_DAYS = 60

EDINET_API_KEY = os.environ.get("EDINET_API_KEY", "0607081d467e4c7b8229ac4f41ec3408")

GCS_BUCKET = "stock_data_1930932"
GCS_DIR = "edinet_delay"
_start_tag = os.environ.get("BACKFILL_START", "2021/01/01").replace("/", "")
GCS_PATH = f"{GCS_DIR}/backfill_{_start_tag}.csv"

# 中間保存間隔（日数）
CHECKPOINT_INTERVAL = 200

# API呼び出し間隔（秒）
API_SLEEP = 0.3


def detect_runtime() -> str:
    """実行環境を自動判別する."""
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    try:
        import google.colab  # noqa: F401
        if os.environ.get("GOOGLE_CLOUD_PROJECT"):
            return "colab_enterprise"
        return "colab_personal"
    except ImportError:
        return "local"


RUNTIME = detect_runtime()


# --- EDINET API（既存ロジック流用） ---

def search_edinet_documents(date_str: str) -> dict:
    """EDINET API で指定日の書類一覧を取得する."""
    formatted_date = date_str.replace("/", "-")
    params = {"date": formatted_date, "type": 2, "Subscription-Key": EDINET_API_KEY}
    response = requests.get(
        "https://api.edinet-fsa.go.jp/api/v2/documents.json",
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def download_xbrl_document(doc_id: str) -> bytes:
    """EDINET API から XBRL ZIP をダウンロードする."""
    params = {"type": 1, "Subscription-Key": EDINET_API_KEY}
    response = requests.get(
        f"https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}",
        params=params,
        timeout=60,
    )
    response.raise_for_status()
    return response.content


def extract_xbrl_data(zip_content: bytes) -> dict:
    """ZIP 内の XBRL から提出日・義務発生日・銘柄コード等を抽出する."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
            for xbrl_file in [name for name in zf.namelist() if name.endswith('.xbrl')]:
                try:
                    with zf.open(xbrl_file) as f:
                        content = f.read()
                        ET.fromstring(content)
                        actual_ns = dict(
                            node for _, node in ET.iterparse(io.BytesIO(content), events=['start-ns'])
                        )
                        namespaces = {
                            'jplvh_cor': 'http://disclosure.edinet-fsa.go.jp/taxonomy/jplvh/2014-07-31/jplvh_cor',
                            'jpdei_cor': 'http://disclosure.edinet-fsa.go.jp/taxonomy/jpdei/2013-08-31/jpdei_cor',
                        }
                        for prefix, uri in actual_ns.items():
                            if 'jplvh' in uri and 'cor' in uri:
                                namespaces['jplvh_cor'] = uri
                            if 'jpdei' in uri and 'cor' in uri:
                                namespaces['jpdei_cor'] = uri

                        root = ET.fromstring(content)
                        tags = {
                            'filing_date': './/jplvh_cor:FilingDateCoverPage',
                            'obligation_date': './/jplvh_cor:DateWhenFilingRequirementAroseCoverPage',
                            'security_code': './/jplvh_cor:SecurityCodeOfIssuer',
                            'issuer_name': './/jplvh_cor:NameOfIssuer',
                            'filer_name': './/jpdei_cor:FilerNameInJapaneseDEI',
                        }
                        res = {k: root.find(v, namespaces) for k, v in tags.items()}
                        if res['filing_date'] is not None and res['obligation_date'] is not None:
                            code = res['security_code'].text if res['security_code'] is not None else "N/A"
                            return {
                                'security_code': code[:4] if len(code) > 4 else code,
                                'issuer_name': res['issuer_name'].text if res['issuer_name'] is not None else "N/A",
                                'filer_name': res['filer_name'].text if res['filer_name'] is not None else "N/A",
                                'obligation_date': res['obligation_date'].text,
                                'filing_date': res['filing_date'].text,
                            }
                except Exception:
                    continue
    except Exception:
        pass
    return {}


def calculate_date_difference(date1_str: str, date2_str: str) -> int:
    """2つの YYYY-MM-DD 文字列の差分日数（絶対値）を返す."""
    try:
        d1 = datetime.strptime(date1_str, "%Y-%m-%d")
        d2 = datetime.strptime(date2_str, "%Y-%m-%d")
        return abs((d2 - d1).days)
    except Exception:
        return 0


# --- GCS保存 ---

def upload_to_gcs(reports: list[dict]) -> None:
    """結果CSVをGCSにアップロードする."""
    from google.cloud import storage

    if RUNTIME == "local":
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            os.environ.get(
                "GOOGLE_APPLICATION_CREDENTIALS",
                "keys/gcp-service-account.json",
            )
        )
        client = storage.Client(credentials=creds, project=creds.project_id)
    else:
        client = storage.Client()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["security_code", "issuer_name", "filer_name",
                     "obligation_date", "filing_date", "delay_days"])
    for r in reports:
        writer.writerow([
            r["security_code"],
            r["issuer_name"],
            r["filer_name"],
            r["obligation_date"],
            r["filing_date"],
            r["days_diff"],
        ])

    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(GCS_PATH)
    blob.upload_from_string(buf.getvalue(), content_type="text/csv")
    print(f"GCS アップロード完了: gs://{GCS_BUCKET}/{GCS_PATH} ({len(reports)}件)")


def _get_gcs_client():
    """GCSクライアントを返す（キャッシュなし）."""
    from google.cloud import storage
    if RUNTIME == "local":
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
        )
        return storage.Client(credentials=creds, project=creds.project_id)
    return storage.Client()


def _save_checkpoint(reports: list[dict], gcs_path: str) -> None:
    """中間結果をGCSに保存する."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["security_code", "issuer_name", "filer_name",
                     "obligation_date", "filing_date", "delay_days"])
    for r in reports:
        writer.writerow([r["security_code"], r["issuer_name"], r["filer_name"],
                         r["obligation_date"], r["filing_date"], r["days_diff"]])
    client = _get_gcs_client()
    bucket = client.bucket(GCS_BUCKET)
    bucket.blob(gcs_path).upload_from_string(buf.getvalue(), content_type="text/csv")


# --- 日付生成（土日スキップ） ---

def generate_dates(start: str, end: str) -> list[str]:
    """開始〜終了日のリストを生成（土日スキップ）."""
    start_d = datetime.strptime(start, "%Y/%m/%d")
    end_d = datetime.strptime(end, "%Y/%m/%d")
    dates = []
    curr = start_d
    while curr <= end_d:
        if curr.weekday() < 5:  # 月〜金のみ
            dates.append(curr.strftime("%Y/%m/%d"))
        curr += timedelta(days=1)
    return dates


# --- メイン ---

def main() -> None:
    """バックフィルメイン処理."""
    start_time = datetime.now(JST)
    log_cap = LogCapture()
    log_cap.start()

    try:
        print(f"=== EDINET遅延報告バックフィル ===")
        print(f"範囲: {START_DATE} 〜 {END_DATE}")
        print(f"閾値: {DELAY_THRESHOLD_DAYS}日以上")
        print(f"環境: {RUNTIME}")

        send_mail(
            "[EDINET_DELAY_BACKFILL] 開始",
            f"範囲: {START_DATE} 〜 {END_DATE}\n閾値: {DELAY_THRESHOLD_DAYS}日",
        )

        target_dates = generate_dates(START_DATE, END_DATE)
        total_days = len(target_dates)
        print(f"対象日数: {total_days}日（土日除外済み）")

        all_reports: list[dict] = []
        errors = 0

        for i, date_str in enumerate(target_dates):
            if i % 100 == 0:
                elapsed = (datetime.now(JST) - start_time).total_seconds()
                print(f"[{i}/{total_days}] {date_str} ... "
                      f"({elapsed:.0f}秒経過, {len(all_reports)}件検出)")

            # 中間保存（タイムアウト対策）
            if i > 0 and i % CHECKPOINT_INTERVAL == 0 and all_reports:
                cp_path = f"{GCS_DIR}/checkpoint_{i}.csv"
                _save_checkpoint(all_reports, cp_path)
                print(f"  [checkpoint] {cp_path} ({len(all_reports)}件)")

            try:
                search_results = search_edinet_documents(date_str)
                time.sleep(API_SLEEP)

                if "results" not in search_results:
                    continue

                target_docs = [
                    doc for doc in search_results["results"]
                    if str(doc.get("docTypeCode")) == "350"
                    and "変更報告書" in (doc.get("docDescription") or "")
                ]

                for doc in target_docs:
                    try:
                        zip_content = download_xbrl_document(doc.get("docID"))
                        time.sleep(API_SLEEP)
                        xbrl_data = extract_xbrl_data(zip_content)
                        if not xbrl_data:
                            continue
                        days_diff = calculate_date_difference(
                            xbrl_data["obligation_date"], xbrl_data["filing_date"]
                        )
                        if days_diff >= DELAY_THRESHOLD_DAYS:
                            xbrl_data["days_diff"] = days_diff
                            all_reports.append(xbrl_data)
                    except Exception:
                        errors += 1
                        continue

            except Exception as e:
                errors += 1
                if errors % 10 == 0:
                    print(f"  エラー累計: {errors}件 (最新: {e})")
                time.sleep(1)
                continue

        # --- 結果保存 ---
        elapsed = (datetime.now(JST) - start_time).total_seconds()
        print(f"\n=== 完了 ===")
        print(f"検出件数: {len(all_reports)}件")
        print(f"エラー: {errors}件")
        print(f"所要時間: {elapsed:.0f}秒 ({elapsed/60:.1f}分)")

        if all_reports:
            upload_to_gcs(all_reports)

        log_text = log_cap.stop()

        send_mail(
            f"[EDINET_DELAY_BACKFILL] 完了 ({len(all_reports)}件, {elapsed/60:.1f}分)",
            f"範囲: {START_DATE} 〜 {END_DATE}\n"
            f"検出: {len(all_reports)}件\n"
            f"エラー: {errors}件\n"
            f"保存先: gs://{GCS_BUCKET}/{GCS_PATH}",
            attachment_text=log_text,
        )

    except Exception as e:
        log_text = log_cap.stop()
        send_mail(
            "[EDINET_DELAY_BACKFILL] エラー",
            f"エラーが発生しました: {e}\n\n{traceback.format_exc()}",
            attachment_text=log_text,
        )
        raise


if __name__ == "__main__":
    main()

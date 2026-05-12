"""月次開示パイプライン初回RUNのデータ検証スクリプト。

正本（extract出力）とバックアップ（BC由来）を比較し、桁違い・単位ミス・欠損を検出する。

Usage:
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/validate_monthly_first_run.py
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/validate_monthly_first_run.py --tickers 3097 2670
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/validate_monthly_first_run.py --ratio-threshold 3.0
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import structlog
from google.cloud import storage
from google.cloud.exceptions import NotFound
from google.oauth2 import service_account

log = structlog.get_logger()

JST = ZoneInfo("Asia/Tokyo")

GCS_BUCKET = "stock_data_1930932"
GCS_RECORD = "monthly/record"
GCS_BACKUP = "monthly/_backup/20260503_1527_bc_historical/record"
KEY_FILE = os.path.join(os.path.dirname(__file__), "..", "keys", "gcp-service-account.json")
PROJECT = "gmailpj-357912"
OUT_DIR = Path(r"C:\tmp")

RATIO_THRESHOLD_DEFAULT = 5.0
MONTH_OFFSET_DEFAULT = 6


def _get_gcs() -> storage.Client:
    """GCSクライアントを取得する。"""
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return storage.Client(project=PROJECT, credentials=creds)


def _list_tickers(gcs: storage.Client, prefix: str) -> list[str]:
    """指定プレフィックス配下のticker一覧を返す。"""
    tickers: list[str] = []
    for blob in gcs.list_blobs(GCS_BUCKET, prefix=f"{prefix}/"):
        if blob.name.endswith("/monthly_records.json"):
            parts = blob.name.split("/")
            ticker = parts[-2]
            tickers.append(ticker)
    return sorted(set(tickers))


def _load_records(gcs: storage.Client, blob_path: str) -> Optional[dict]:
    """GCSからmonthly_records.jsonを読み込む。NotFoundのみNone、他はraise。"""
    try:
        blob = gcs.bucket(GCS_BUCKET).blob(blob_path)
        return json.loads(blob.download_as_text())
    except NotFound:
        return None
    except Exception:
        log.warning("GCS読込失敗", path=blob_path, exc_info=True)
        raise


def _is_valid_ym(ym: str) -> bool:
    """year_monthが有効なYYYY-MM形式かを判定する。"""
    if not ym or len(ym) < 7 or ym[4] != "-":
        return False
    try:
        year = int(ym[:4])
        month = int(ym[5:7])
        return 2000 <= year <= 2100 and 1 <= month <= 12
    except (ValueError, IndexError):
        return False


def _get_latest_extract_record(records_data: dict) -> Optional[dict]:
    """最新のextract由来レコード（source != bc_historical）を返す。"""
    extract_records = [
        r for r in records_data.get("records", [])
        if r.get("source") != "bc_historical" and _is_valid_ym(r.get("year_month", ""))
    ]
    if not extract_records:
        return None
    return max(extract_records, key=lambda r: r.get("year_month", ""))


def _find_bc_record_near(records_data: dict, target_ym: str, offset_months: int) -> Optional[dict]:
    """target_ymからoffset_months前付近のbc_historicalレコードを探す。"""
    year = int(target_ym[:4])
    month = int(target_ym[5:7])

    month -= offset_months
    while month <= 0:
        month += 12
        year -= 1
    target_past = f"{year:04d}-{month:02d}"

    bc_records = [
        r for r in records_data.get("records", [])
        if r.get("source") == "bc_historical"
    ]
    if not bc_records:
        return None

    bc_records.sort(key=lambda r: abs(_ym_diff(r.get("year_month", ""), target_past)))
    return bc_records[0] if bc_records else None


def _ym_diff(ym1: str, ym2: str) -> int:
    """2つのyear_month間の月数差を返す。"""
    try:
        y1, m1 = int(ym1[:4]), int(ym1[5:7])
        y2, m2 = int(ym2[:4]), int(ym2[5:7])
        return (y1 - y2) * 12 + (m1 - m2)
    except (ValueError, IndexError):
        return 9999


MIN_ABS_FOR_RATIO = 10.0


def _classify_alert(
    prod_val: float,
    bc_val: float,
    ratio_threshold: float,
) -> Optional[str]:
    """異常種別を判定する。"""
    if bc_val == 0:
        return None

    if abs(bc_val) < MIN_ABS_FOR_RATIO and abs(prod_val) < MIN_ABS_FOR_RATIO:
        return None

    ratio = prod_val / bc_val
    if ratio > ratio_threshold or ratio < (1.0 / ratio_threshold):
        return "ORDER_DIFF"

    is_bc_pct_range = 20.0 <= abs(bc_val) <= 200.0
    is_prod_pct_range = 20.0 <= abs(prod_val) <= 200.0
    if is_bc_pct_range and not is_prod_pct_range and abs(prod_val) > 500:
        return "SUSPICIOUS_PCT"
    if is_prod_pct_range and not is_bc_pct_range and abs(bc_val) > 500:
        return "SUSPICIOUS_PCT"

    return None


def validate(
    gcs: storage.Client,
    tickers: Optional[list[str]],
    ratio_threshold: float,
    month_offset: int,
) -> list[dict]:
    """全tickerを検証し、異常リストを返す。"""
    if tickers is None:
        log.info("正本tickerリストを取得中...")
        tickers = _list_tickers(gcs, GCS_RECORD)
    log.info("検証対象ticker数", count=len(tickers))

    alerts: list[dict] = []
    stats = {"total": 0, "no_extract": 0, "no_backup": 0, "no_bc_near": 0, "checked": 0}

    for ticker in tickers:
        stats["total"] += 1

        prod_data = _load_records(gcs, f"{GCS_RECORD}/{ticker}/monthly_records.json")
        if prod_data is None:
            continue

        latest = _get_latest_extract_record(prod_data)
        if latest is None:
            stats["no_extract"] += 1
            continue

        backup_data = _load_records(gcs, f"{GCS_BACKUP}/{ticker}/monthly_records.json")
        if backup_data is None:
            stats["no_backup"] += 1
            continue

        prod_ym = latest.get("year_month", "")
        bc_record = _find_bc_record_near(backup_data, prod_ym, month_offset)
        if bc_record is None:
            stats["no_bc_near"] += 1
            continue

        stats["checked"] += 1
        prod_fields = latest.get("fields", {})
        bc_fields = bc_record.get("fields", {})
        bc_ym = bc_record.get("year_month", "")

        bc_field_names = set(bc_fields.keys())
        prod_field_names = set(prod_fields.keys())
        missing = bc_field_names - prod_field_names
        if missing and len(prod_field_names) < len(bc_field_names):
            alerts.append({
                "ticker": ticker,
                "field_name": f"[MISSING {len(missing)} fields]",
                "prod_month": prod_ym,
                "prod_value": f"has {len(prod_field_names)}",
                "bc_month": bc_ym,
                "bc_value": f"has {len(bc_field_names)}",
                "ratio": "",
                "alert_type": "FIELD_MISSING",
                "missing_fields": "; ".join(sorted(missing)),
            })

        for field_name in bc_field_names & prod_field_names:
            prod_val = prod_fields.get(field_name)
            bc_val = bc_fields.get(field_name)

            if prod_val is None or prod_val == 0:
                alerts.append({
                    "ticker": ticker,
                    "field_name": field_name,
                    "prod_month": prod_ym,
                    "prod_value": prod_val,
                    "bc_month": bc_ym,
                    "bc_value": bc_val,
                    "ratio": "",
                    "alert_type": "ZERO_OR_NULL",
                    "missing_fields": "",
                })
                continue

            if bc_val is None or bc_val == 0:
                continue

            try:
                prod_f = float(prod_val)
                bc_f = float(bc_val)
            except (ValueError, TypeError):
                alerts.append({
                    "ticker": ticker,
                    "field_name": field_name,
                    "prod_month": prod_ym,
                    "prod_value": prod_val,
                    "bc_month": bc_ym,
                    "bc_value": bc_val,
                    "ratio": "",
                    "alert_type": "TYPE_ERROR",
                    "missing_fields": "",
                })
                continue

            alert_type = _classify_alert(prod_f, bc_f, ratio_threshold)
            if alert_type:
                ratio = prod_f / bc_f
                alerts.append({
                    "ticker": ticker,
                    "field_name": field_name,
                    "prod_month": prod_ym,
                    "prod_value": prod_val,
                    "bc_month": bc_ym,
                    "bc_value": bc_val,
                    "ratio": f"{ratio:.3f}",
                    "alert_type": alert_type,
                    "missing_fields": "",
                })

    log.info("検証完了", **stats)
    return alerts


def main() -> None:
    """エントリーポイント。"""
    parser = argparse.ArgumentParser(description="月次開示初回RUNデータ検証")
    parser.add_argument("--tickers", nargs="*", help="検証対象ticker（省略時は全件）")
    parser.add_argument(
        "--ratio-threshold", type=float, default=RATIO_THRESHOLD_DEFAULT,
        help=f"桁違い判定の倍率閾値（デフォルト: {RATIO_THRESHOLD_DEFAULT}）",
    )
    parser.add_argument(
        "--month-offset", type=int, default=MONTH_OFFSET_DEFAULT,
        help=f"BC比較レコードの月オフセット（デフォルト: {MONTH_OFFSET_DEFAULT}）",
    )
    args = parser.parse_args()

    gcs = _get_gcs()
    alerts = validate(gcs, args.tickers, args.ratio_threshold, args.month_offset)

    now = datetime.now(tz=JST)
    out_path = OUT_DIR / f"monthly_validation_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "ticker", "field_name", "prod_month", "prod_value",
        "bc_month", "bc_value", "ratio", "alert_type", "missing_fields",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(alerts)

    log.info("結果出力完了", path=str(out_path), alert_count=len(alerts))

    if alerts:
        log.info("--- アラートサマリ ---")
        by_type: dict[str, int] = {}
        for a in alerts:
            by_type[a["alert_type"]] = by_type.get(a["alert_type"], 0) + 1
        for t, c in sorted(by_type.items()):
            log.info(f"  {t}: {c}件")
        sys.exit(1)


if __name__ == "__main__":
    main()

"""BC月次KPIデータを過去データとしてGCSにアップロードする。

BC(バフェットコード)のデータはダウンロードデータと単位・表現が異なる場合がある。
アダプタ定義(unit_scale/yoy_offset)を**逆適用**して、ダウンロードデータ基準の値に
変換してからGCSにアップロードする。既存のextractデータがあればそちらを優先し、
BCデータは存在しない月の穴埋めにのみ使用する。

Usage:
    PYTHONUTF8=1 python scripts/upload_bc_historical.py --dry-run
    PYTHONUTF8=1 python scripts/upload_bc_historical.py
    PYTHONUTF8=1 python scripts/upload_bc_historical.py --tickers 2670 2726
    PYTHONUTF8=1 python scripts/upload_bc_historical.py --cutoff 2025-12
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger()

JST = timezone(timedelta(hours=9), "JST")

GCS_BUCKET = "stock_data_1930932"
GCS_RECORD = "monthly/record"
BQ_PROJECT = "gmailpj-357912"
LOCAL_KEY_FILE = os.path.join(
    os.path.dirname(__file__), "..", "keys", "gcp-service-account.json"
)
BC_CSV_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "csv", "bc_monthly_kpi.csv"
)
ADAPTER_DIR = os.path.join(
    os.path.dirname(__file__), "..", "meta", "monthly"
)
DEFAULT_CUTOFF = "2026-03"


# ── GCP 認証 ──────────────────────────────────────────────────────────────
def _get_credentials():
    """GCP認証情報を取得する。"""
    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_file(LOCAL_KEY_FILE)


def _get_gcs():
    """GCSクライアントを取得する。"""
    from google.cloud import storage

    return storage.Client(project=BQ_PROJECT, credentials=_get_credentials())


# ── アダプタ読み込み ───────────────────────────────────────────────────────
def load_adapter(ticker: str) -> Optional[dict]:
    """ローカルアダプタファイルを読み込む。"""
    path = Path(ADAPTER_DIR) / f"{ticker}_extract_adapter.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_bc_to_key_mapping(adapter: dict) -> dict[str, dict]:
    """アダプタfieldsからBC field名→{key, unit_scale, yoy_offset}のマッピングを構築する。

    明示的bc_keyが設定されたフィールドを優先し、
    bc_key未設定のフィールドはkeyをbc_keyとみなして補完する。
    bc_ignore=trueのフィールドはスキップ。
    """
    mapping: dict[str, dict] = {}
    fields = adapter.get("fields", [])

    for field in fields:
        if field.get("bc_ignore"):
            continue
        bc_key = field.get("bc_key")
        if bc_key:
            mapping[bc_key] = {
                "key": field["key"],
                "unit_scale": field.get("unit_scale"),
                "yoy_offset": field.get("yoy_offset"),
            }

    for field in fields:
        if field.get("bc_ignore"):
            continue
        if "bc_key" not in field:
            key = field["key"]
            if key not in mapping:
                mapping[key] = {
                    "key": key,
                    "unit_scale": field.get("unit_scale"),
                    "yoy_offset": field.get("yoy_offset"),
                }
    return mapping


def reverse_convert(
    bc_value: float,
    unit_scale: Optional[float],
    yoy_offset: Optional[float],
) -> float:
    """BC値にアダプタ変換を逆適用してダウンロードデータ基準の値に変換する。

    Forward (extract→BC比較): compare_val = our_val * unit_scale + yoy_offset
    Reverse (BC→extract基準): our_val = (bc_val - yoy_offset) / unit_scale
    """
    val = bc_value
    if yoy_offset is not None:
        val = val - yoy_offset
    if unit_scale is not None and unit_scale != 0:
        val = val / unit_scale
    return round(val, 4)


# ── BC CSV 読み込み ────────────────────────────────────────────────────────
def load_bc_data(
    cutoff: str,
    target_tickers: Optional[list[str]] = None,
) -> dict[str, dict[str, dict[str, float]]]:
    """BC CSVを読み込み {ticker: {year_month: {field: value}}} を返す。"""
    data: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    with open(BC_CSV_PATH, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ym = row["year_month"]
            if ym > cutoff:
                continue
            ticker = row["ticker"]
            if target_tickers and ticker not in target_tickers:
                continue
            try:
                value = float(row["value"])
            except (ValueError, TypeError):
                continue
            data[ticker][ym][row["field"]] = value

    log.info("bc_csv_loaded", tickers=len(data), cutoff=cutoff)
    return dict(data)


# ── GCS既存レコード読み込み ────────────────────────────────────────────────
def load_existing_records(gcs, ticker: str) -> Optional[dict]:
    """GCSから既存monthly_records.jsonを読み込む。"""
    blob = gcs.bucket(GCS_BUCKET).blob(
        f"{GCS_RECORD}/{ticker}/monthly_records.json"
    )
    if not blob.exists():
        return None
    return json.loads(blob.download_as_text(encoding="utf-8"))


def save_records(gcs, ticker: str, records_doc: dict) -> None:
    """monthly_records.jsonをGCSに保存する。"""
    blob = gcs.bucket(GCS_BUCKET).blob(
        f"{GCS_RECORD}/{ticker}/monthly_records.json"
    )
    blob.upload_from_string(
        json.dumps(records_doc, ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8",
    )


# ── メイン処理 ─────────────────────────────────────────────────────────────
def process_ticker(
    gcs,
    ticker: str,
    bc_months: dict[str, dict[str, float]],
    dry_run: bool,
) -> dict:
    """1銘柄分のBC過去データ変換・マージ・アップロードを行う。"""
    adapter = load_adapter(ticker)

    is_excluded = False
    if adapter:
        is_excluded = adapter.get("_excluded", False) or adapter.get(
            "excluded", False
        )

    if is_excluded:
        log.info("skip_excluded", ticker=ticker)
        return {"status": "skipped_excluded"}

    bc_to_key = build_bc_to_key_mapping(adapter) if adapter else {}
    company_name = ""
    if adapter:
        cn = adapter.get("company_name", "")
        if cn and cn != ticker:
            company_name = cn

    existing = load_existing_records(gcs, ticker)
    existing_months: set[str] = set()
    existing_records: list[dict] = []
    if existing:
        existing_records = existing.get("records", [])
        existing_months = {r["year_month"] for r in existing_records}
        if not company_name:
            company_name = existing.get("company_name", "")

    new_records: list[dict] = []
    conversion_log: list[str] = []

    for ym in sorted(bc_months.keys()):
        if ym in existing_months:
            continue

        year, month = int(ym[:4]), int(ym[5:7])
        bc_fields = bc_months[ym]
        converted_fields: dict[str, Optional[float]] = {}

        for bc_field_name, bc_val in bc_fields.items():
            if bc_field_name in bc_to_key:
                m = bc_to_key[bc_field_name]
                key = m["key"]
                val = reverse_convert(bc_val, m["unit_scale"], m["yoy_offset"])
                if m["unit_scale"] or m["yoy_offset"]:
                    conversion_log.append(
                        f"  {ticker}/{ym} {bc_field_name}: "
                        f"{bc_val} → {val} "
                        f"(scale={m['unit_scale']}, offset={m['yoy_offset']})"
                    )
            else:
                key = bc_field_name
                val = bc_val

            converted_fields[key] = val

        new_records.append(
            {
                "year_month": ym,
                "fields": converted_fields,
                "year": year,
                "month": month,
                "source": "bc_historical",
            }
        )

    if not new_records:
        log.info(
            "no_new_months",
            ticker=ticker,
            existing=len(existing_months),
        )
        return {"status": "no_new", "existing": len(existing_months)}

    for line in conversion_log:
        log.info("reverse_conversion", detail=line)

    merged = existing_records + new_records
    merged.sort(key=lambda r: r["year_month"])

    records_doc = {
        "ticker": ticker,
        "company_name": company_name,
        "updated_at": datetime.now(tz=JST).isoformat(),
        "record_count": len(merged),
        "records": merged,
    }

    if dry_run:
        log.info(
            "dry_run",
            ticker=ticker,
            new_months=len(new_records),
            total=len(merged),
            sample_months=[r["year_month"] for r in new_records[:5]],
        )
    else:
        save_records(gcs, ticker, records_doc)
        log.info(
            "uploaded",
            ticker=ticker,
            new_months=len(new_records),
            total=len(merged),
        )

    return {
        "status": "uploaded" if not dry_run else "dry_run",
        "new": len(new_records),
        "existing": len(existing_months),
        "total": len(merged),
    }


def main() -> None:
    """エントリポイント。"""
    parser = argparse.ArgumentParser(
        description="BC月次KPIデータをGCSに過去データとしてアップロード"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="GCS書き込みなし。対象一覧のみ表示",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="対象銘柄コード（複数指定可）。省略時はBC CSV全銘柄",
    )
    parser.add_argument(
        "--cutoff",
        default=DEFAULT_CUTOFF,
        help=f"取り込み上限月 (yyyy-mm)。デフォルト: {DEFAULT_CUTOFF}",
    )
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            structlog.dev.ConsoleRenderer(),
        ]
    )

    log.info(
        "start",
        cutoff=args.cutoff,
        tickers=args.tickers,
        dry_run=args.dry_run,
    )

    bc_data = load_bc_data(args.cutoff, args.tickers)
    if not bc_data:
        log.warning("no_bc_data")
        return

    gcs = _get_gcs()

    summary: dict[str, dict] = {}
    for ticker in sorted(bc_data.keys()):
        result = process_ticker(gcs, ticker, bc_data[ticker], args.dry_run)
        summary[ticker] = result

    uploaded = sum(1 for v in summary.values() if v["status"] == "uploaded")
    skipped = sum(
        1 for v in summary.values() if v["status"] == "skipped_excluded"
    )
    no_new = sum(1 for v in summary.values() if v["status"] == "no_new")
    dry = sum(1 for v in summary.values() if v["status"] == "dry_run")
    total_new = sum(v.get("new", 0) for v in summary.values())

    log.info(
        "complete",
        uploaded=uploaded,
        dry_run=dry,
        skipped_excluded=skipped,
        no_new_months=no_new,
        total_new_records=total_new,
    )


if __name__ == "__main__":
    main()

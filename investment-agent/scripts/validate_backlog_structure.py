# -*- coding: utf-8 -*-
"""Validate order-backlog structure.json quality.

Usage:
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
        scripts/validate_backlog_structure.py --target regression
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
        scripts/validate_backlog_structure.py --target full
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

import pdfplumber
from google.cloud import bigquery
from google.cloud import storage
from google.oauth2 import service_account


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_KEY_FILE = PROJECT_ROOT / "keys" / "gcp-service-account.json"
CLAUDE_KEY_FILE = Path("C:/gdrive/claude/investment-agent/keys/gcp-service-account.json")
GCP_PROJECT = "gmailpj-357912"
GCS_BUCKET = "stock_data_1930932"
GCS_META_PREFIX = "quarterly/meta"
TMP_ROOT = Path("C:/tmp/structure_qa")
PHASE_A_RESULTS = TMP_ROOT / "results.csv"
REGRESSION_OUTPUT = TMP_ROOT / "regression_results.csv"
FULL_OUTPUT = TMP_ROOT / "checker_results.csv"
E4_WORK_ROOT = TMP_ROOT / "e4_pdf_work"

ORDER_KEYWORDS = ("受注", "繰越", "手持", "backlog", "完成工事")
E4_KEYWORDS = ("受注", "繰越", "手持", "backlog")
SALES_KEYWORDS = ("売上", "売上高", "売上収益", "revenue", "sales", "完成工事高")
ORDER_INTAKE_KEYWORDS = ("受注高", "受注額", "受注")
BACKLOG_KEYWORDS = ("受注残", "受注残高", "繰越", "手持", "backlog")
COMPLETED_KEYWORDS = ("売上", "売上高", "売上収益", "完成工事高", "revenue", "sales")
ALLOWED_UNITS = {"百万円", "千円", "億円", "%"}
MONEY_UNITS = {"百万円", "千円", "億円"}
NUMERIC_RE = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class StructureRecord:
    """Single company's active structure data."""

    ticker: str
    company_name: str
    source_pdf: str
    data_available: bool
    metrics: list[dict[str, Any]]
    breakdown_dimensions: Any
    raw: dict[str, Any]

    @property
    def metrics_count(self) -> int:
        """Return metric count."""
        return len(self.metrics)


@dataclass(frozen=True)
class Finding:
    """One checker finding row."""

    ticker: str
    company_name: str
    check_id: str
    severity: str
    message: str
    metrics_count: int


def get_credentials() -> service_account.Credentials:
    """Load GCP service account credentials."""
    key_file = LOCAL_KEY_FILE if LOCAL_KEY_FILE.exists() else CLAUDE_KEY_FILE
    if not key_file.exists():
        raise FileNotFoundError(f"GCP key file not found: {key_file}")
    return service_account.Credentials.from_service_account_file(str(key_file))


def storage_client() -> storage.Client:
    """Create a GCS client."""
    return storage.Client(project=GCP_PROJECT, credentials=get_credentials())


def bigquery_client() -> bigquery.Client:
    """Create a BigQuery client."""
    return bigquery.Client(project=GCP_PROJECT, credentials=get_credentials())


def active_version(raw: dict[str, Any]) -> dict[str, Any]:
    """Return current version block from a structure.json payload."""
    versions = raw.get("versions")
    if not isinstance(versions, list) or not versions:
        return raw
    current = raw.get("current_version")
    for version in versions:
        if isinstance(version, dict) and version.get("valid_from") == current:
            return version
    last = versions[-1]
    return last if isinstance(last, dict) else raw


def parse_structure(raw: dict[str, Any]) -> StructureRecord:
    """Convert structure JSON payload into a normalized record."""
    version = active_version(raw)
    metrics = version.get("metrics") if isinstance(version.get("metrics"), list) else []
    return StructureRecord(
        ticker=str(raw.get("ticker", "")).strip(),
        company_name=str(raw.get("company_name", "")).strip(),
        source_pdf=str(raw.get("_source_pdf", "")).strip(),
        data_available=bool(version.get("data_available", raw.get("data_available", False))),
        metrics=[m for m in metrics if isinstance(m, dict)],
        breakdown_dimensions=version.get("breakdown_dimensions"),
        raw=raw,
    )


def download_structure(bucket: storage.Bucket, ticker: str) -> StructureRecord | None:
    """Download one structure JSON from GCS."""
    blob = bucket.blob(f"{GCS_META_PREFIX}/{ticker}/structure.json")
    if not blob.exists():
        LOGGER.warning("structure.json not found: %s", ticker)
        return None
    raw = json.loads(blob.download_as_text(encoding="utf-8"))
    return parse_structure(raw)


def list_available_structures(bucket: storage.Bucket) -> list[StructureRecord]:
    """List all data_available=true structure records from GCS."""
    records: list[StructureRecord] = []
    for blob in bucket.list_blobs(prefix=f"{GCS_META_PREFIX}/"):
        if not blob.name.endswith("/structure.json"):
            continue
        raw = json.loads(blob.download_as_text(encoding="utf-8"))
        record = parse_structure(raw)
        if record.data_available:
            records.append(record)
    records.sort(key=lambda item: item.ticker)
    return records


def read_regression_tickers() -> dict[str, str]:
    """Read Phase A ticker and judgment values."""
    if not PHASE_A_RESULTS.exists():
        raise FileNotFoundError(f"Phase A result CSV not found: {PHASE_A_RESULTS}")
    tickers: dict[str, str] = {}
    with PHASE_A_RESULTS.open("r", encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        for row in reader:
            ticker = str(row.get("ticker", "")).strip()
            if ticker:
                tickers[ticker] = str(row.get("judgment", "")).strip()
    return tickers


def metric_name(metric: dict[str, Any]) -> str:
    """Return metric name as string."""
    return str(metric.get("name", "")).strip()


def metric_type(metric: dict[str, Any]) -> str:
    """Return metric type as lowercase string."""
    return str(metric.get("type", "")).strip().lower()


def has_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    """Return whether text contains any keyword, case-insensitive for ASCII."""
    lower_text = text.lower()
    return any(keyword.lower() in lower_text for keyword in keywords)


def has_order_related_metric(record: StructureRecord) -> bool:
    """Return whether any metric name appears order/backlog related."""
    return any(has_keyword(metric_name(metric), ORDER_KEYWORDS) for metric in record.metrics)


def is_sales_only_metric(metric: dict[str, Any]) -> bool:
    """Return whether a metric is sales/completed only."""
    name = metric_name(metric)
    type_value = metric_type(metric)
    return (
        (has_keyword(name, SALES_KEYWORDS) or type_value == "completed")
        and not has_keyword(name, BACKLOG_KEYWORDS)
        and not type_value in {"order_intake", "backlog"}
    )


def has_order_intake(record: StructureRecord) -> bool:
    """Return whether the record has order intake metrics."""
    return any(
        metric_type(metric) == "order_intake"
        or has_keyword(metric_name(metric), ORDER_INTAKE_KEYWORDS)
        for metric in record.metrics
    )


def has_backlog(record: StructureRecord) -> bool:
    """Return whether the record has backlog metrics."""
    return any(
        metric_type(metric) == "backlog" or has_keyword(metric_name(metric), BACKLOG_KEYWORDS)
        for metric in record.metrics
    )


def has_completed(record: StructureRecord) -> bool:
    """Return whether the record has completed/sales metrics."""
    return any(
        metric_type(metric) == "completed" or has_keyword(metric_name(metric), COMPLETED_KEYWORDS)
        for metric in record.metrics
    )


def add_finding(
    findings: list[Finding],
    record: StructureRecord,
    check_id: str,
    severity: str,
    message: str,
) -> None:
    """Append one finding."""
    findings.append(
        Finding(
            ticker=record.ticker,
            company_name=record.company_name,
            check_id=check_id,
            severity=severity,
            message=message,
            metrics_count=record.metrics_count,
        )
    )


def check_structure_metadata(
    record: StructureRecord,
    sector_medians: dict[str, float],
    ticker_to_sector: dict[str, str],
) -> list[Finding]:
    """Run metadata-only checks."""
    findings: list[Finding] = []
    names = [metric_name(metric) for metric in record.metrics]

    if not record.metrics:
        add_finding(findings, record, "E-1", "error", "metrics is empty or missing")

    if record.metrics and not has_order_related_metric(record):
        add_finding(findings, record, "E-2", "warning", "no order-related keyword in metric names")

    if record.metrics_count == 1 and is_sales_only_metric(record.metrics[0]):
        add_finding(findings, record, "E-3", "error", "single metric is sales/revenue only")

    units = [str(metric.get("unit", "")).strip() for metric in record.metrics]
    invalid_units = sorted(
        {
            str(metric.get("unit", "")).strip()
            for metric in record.metrics
            if metric_type(metric) in {"order_intake", "completed", "backlog"}
            and str(metric.get("unit", "")).strip()
            and str(metric.get("unit", "")).strip() not in ALLOWED_UNITS
        }
    )
    if invalid_units:
        add_finding(findings, record, "D-1", "error", f"invalid unit value(s): {', '.join(invalid_units)}")

    distinct_units = sorted({unit for unit in units if unit})
    distinct_money_units = sorted({unit for unit in distinct_units if unit in MONEY_UNITS})
    if len(distinct_money_units) > 1:
        add_finding(
            findings,
            record,
            "D-2",
            "warning",
            f"multiple monetary unit values: {', '.join(distinct_money_units)}",
        )

    if (
        has_order_intake(record)
        and has_backlog(record)
        and not has_completed(record)
        and 3 <= record.metrics_count <= 4
    ):
        add_finding(findings, record, "A-1", "warning", "order intake exists but sales/completed metric is absent")

    if has_order_intake(record) and not has_backlog(record) and not has_completed(record):
        add_finding(findings, record, "A-2", "warning", "order intake exists but backlog metric is absent")

    sector = ticker_to_sector.get(record.ticker)
    sector_median = sector_medians.get(sector or "")
    if sector and sector_median and record.metrics_count <= sector_median / 3:
        add_finding(
            findings,
            record,
            "A-3",
            "warning",
            f"metrics count {record.metrics_count} is <= one-third of sector median {sector_median:g}",
        )

    if record.metrics_count > 15:
        add_finding(findings, record, "G-1", "warning", "metrics count exceeds 15")

    duplicate_names = sorted(name for name, count in Counter(names).items() if name and count > 1)
    if duplicate_names:
        add_finding(findings, record, "G-2", "error", f"duplicate metric name(s): {', '.join(duplicate_names)}")

    dimensions = record.breakdown_dimensions
    if isinstance(dimensions, list):
        empty_dimensions = [
            str(item.get("dimension_name", "")).strip() or "(unnamed)"
            for item in dimensions
            if isinstance(item, dict) and not item.get("items")
        ]
        if empty_dimensions:
            add_finding(
                findings,
                record,
                "G-3",
                "warning",
                f"breakdown dimension without items: {', '.join(empty_dimensions)}",
            )
    elif dimensions is not None:
        add_finding(findings, record, "G-3", "warning", "breakdown_dimensions is defined but not a list")

    return findings


def e4_should_run(existing_findings: list[Finding]) -> bool:
    """Return whether E-4 should run after E-1 to E-3 filters."""
    return not any(finding.check_id in {"E-1", "E-2", "E-3"} for finding in existing_findings)


def e4_pdf_has_numeric_keyword(pdf_path: Path) -> tuple[bool, int]:
    """Scan PDF text for numeric values near order/backlog keywords."""
    keyword_hits = 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines = text.splitlines()
            for index, line in enumerate(lines):
                if not has_keyword(line, E4_KEYWORDS):
                    continue
                keyword_hits += 1
                window = "\n".join(lines[max(0, index - 2) : min(len(lines), index + 3)])
                if NUMERIC_RE.search(window):
                    return True, keyword_hits
    return False, keyword_hits


def check_e4(record: StructureRecord, bucket: storage.Bucket) -> list[Finding]:
    """Run E-4 PDF text scan for one company."""
    findings: list[Finding] = []
    if not record.source_pdf:
        add_finding(findings, record, "E-4", "error", "_source_pdf is missing")
        return findings

    work_dir = E4_WORK_ROOT / record.ticker
    pdf_path = work_dir / "source.pdf"
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        blob = bucket.blob(record.source_pdf)
        if not blob.exists():
            add_finding(findings, record, "E-4", "error", f"source PDF not found: {record.source_pdf}")
            return findings
        blob.download_to_filename(str(pdf_path))
        has_numeric, keyword_hits = e4_pdf_has_numeric_keyword(pdf_path)
        if not has_numeric:
            add_finding(
                findings,
                record,
                "E-4",
                "error",
                f"no numeric value near order/backlog keyword in PDF text; keyword_hits={keyword_hits}",
            )
    except Exception as exc:  # noqa: BLE001 - surface per-ticker scan failure as checker output.
        add_finding(findings, record, "E-4", "error", f"PDF scan failed: {exc}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    return findings


def load_ticker_sectors() -> dict[str, str]:
    """Load ticker to 33-sector mapping from BigQuery."""
    query = """
        SELECT CAST(TICKER AS STRING) AS ticker, CAST(INDUSTRY_33_CODE AS STRING) AS sector
        FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
        WHERE TICKER IS NOT NULL AND INDUSTRY_33_CODE IS NOT NULL
    """
    result = bigquery_client().query(query).result()
    return {str(row.ticker).strip(): str(row.sector).strip() for row in result}


def compute_sector_medians(
    records: list[StructureRecord],
    ticker_to_sector: dict[str, str],
) -> dict[str, float]:
    """Compute median metrics count per sector, skipping groups under 3 companies."""
    sector_counts: dict[str, list[int]] = defaultdict(list)
    for record in records:
        sector = ticker_to_sector.get(record.ticker)
        if sector:
            sector_counts[sector].append(record.metrics_count)
    return {
        sector: float(median(counts))
        for sector, counts in sector_counts.items()
        if len(counts) >= 3
    }


def write_findings(path: Path, findings: list[Finding]) -> None:
    """Write checker findings CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(
            file_obj,
            fieldnames=["ticker", "company_name", "check_id", "severity", "message", "metrics_count"],
        )
        writer.writeheader()
        for finding in findings:
            writer.writerow(
                {
                    "ticker": finding.ticker,
                    "company_name": finding.company_name,
                    "check_id": finding.check_id,
                    "severity": finding.severity,
                    "message": finding.message,
                    "metrics_count": finding.metrics_count,
                }
            )


def print_summary(records: list[StructureRecord], findings: list[Finding]) -> None:
    """Print checker summary."""
    flagged_tickers = {finding.ticker for finding in findings}
    error_tickers = {finding.ticker for finding in findings if finding.severity == "error"}
    warning_tickers = {finding.ticker for finding in findings if finding.severity == "warning"}
    by_severity = Counter(finding.severity for finding in findings)
    by_check = Counter(finding.check_id for finding in findings)
    print(f"total_structures={len(records)}")
    print(f"flagged_tickers={len(flagged_tickers)}")
    print(f"error_tickers={len(error_tickers)}")
    print(f"warning_tickers={len(warning_tickers)}")
    print("severity_counts=" + json.dumps(dict(sorted(by_severity.items())), ensure_ascii=False))
    print("check_counts=" + json.dumps(dict(sorted(by_check.items())), ensure_ascii=False))


def print_regression_summary(findings: list[Finding], phase_a_judgments: dict[str, str]) -> None:
    """Print Phase A regression criteria summary."""
    major_tickers = {ticker for ticker, judgment in phase_a_judgments.items() if judgment == "major"}
    correct_tickers = {ticker for ticker, judgment in phase_a_judgments.items() if judgment == "correct"}
    flagged_tickers = {finding.ticker for finding in findings}
    error_tickers = {finding.ticker for finding in findings if finding.severity == "error"}
    warning_tickers = {finding.ticker for finding in findings if finding.severity == "warning"}
    missed_major = sorted(major_tickers - flagged_tickers)
    correct_with_error = sorted(correct_tickers & error_tickers)
    correct_with_warning = sorted(correct_tickers & warning_tickers)
    passed = not missed_major and not correct_with_error and len(correct_with_warning) <= 3
    print(f"regression_major_tickers={','.join(sorted(major_tickers))}")
    print(f"regression_missed_major={','.join(missed_major)}")
    print(f"regression_correct_with_error={','.join(correct_with_error)}")
    print(f"regression_correct_with_warning={','.join(correct_with_warning)}")
    print(f"regression_pass={str(passed).lower()}")


def validate_records(records: list[StructureRecord], bucket: storage.Bucket) -> list[Finding]:
    """Run all checks over structure records."""
    ticker_to_sector = load_ticker_sectors()
    sector_medians = compute_sector_medians(records, ticker_to_sector)
    findings: list[Finding] = []
    for index, record in enumerate(records, start=1):
        LOGGER.info("checking %s (%s/%s)", record.ticker, index, len(records))
        record_findings = check_structure_metadata(record, sector_medians, ticker_to_sector)
        if e4_should_run(record_findings):
            record_findings.extend(check_e4(record, bucket))
        findings.extend(record_findings)
    findings.sort(key=lambda item: (item.ticker, item.check_id, item.severity, item.message))
    return findings


def load_regression_records(bucket: storage.Bucket) -> tuple[list[StructureRecord], dict[str, str]]:
    """Load Phase A regression structures from GCS."""
    judgments = read_regression_tickers()
    records: list[StructureRecord] = []
    for ticker in sorted(judgments):
        record = download_structure(bucket, ticker)
        if record and record.data_available:
            records.append(record)
    return records, judgments


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["regression", "full"], required=True)
    parser.add_argument("--limit", type=int, default=0, help="Optional debug limit after loading target records.")
    parser.add_argument("--ticker", action="append", default=[], help="Optional ticker filter for debugging.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bucket = storage_client().bucket(GCS_BUCKET)

    phase_a_judgments: dict[str, str] = {}
    if args.target == "regression":
        records, phase_a_judgments = load_regression_records(bucket)
        output_path = REGRESSION_OUTPUT
    else:
        records = list_available_structures(bucket)
        output_path = FULL_OUTPUT

    if args.ticker:
        ticker_filter = {str(ticker).strip() for ticker in args.ticker}
        records = [record for record in records if record.ticker in ticker_filter]
    if args.limit:
        records = records[: args.limit]

    findings = validate_records(records, bucket)
    write_findings(output_path, findings)
    print(f"output={output_path}")
    print_summary(records, findings)
    if args.target == "regression":
        print_regression_summary(findings, phase_a_judgments)


if __name__ == "__main__":
    main()

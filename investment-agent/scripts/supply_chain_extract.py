"""Extract major customer facts from EDINET annual-report chunks.

Workflow:
  1. Fetch candidate chunks from BigQuery.
  2. Review the chunks with Codex and write a JSONL extraction file.
  3. Resolve customer names against STOCK_CODE_LIST and write supply_chain.csv.

The LLM extraction step is intentionally external to this script because this
task is delegated to Codex itself, not to a separate Gemini/OpenAI API key.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from google.cloud import bigquery
from google.oauth2 import service_account
from rapidfuzz import fuzz, process

PROJECT_ID = "gmailpj-357912"
IR_TABLE = f"{PROJECT_ID}.STOCK.ir_documents_enhanced"
STOCK_TABLE = f"{PROJECT_ID}.STOCK.STOCK_CODE_LIST"
KEY_FILE = "keys/gcp-service-account.json"
DEFAULT_OUTPUT = Path("data/master/supply_chain.csv")
DEFAULT_CANDIDATES = Path("data/tmp/supply_chain_candidates.jsonl")

log = structlog.get_logger()


@dataclass(frozen=True)
class StockName:
    """Normalized listed-company name."""

    ticker: str
    stock_name: str
    normalized: str


def configure_logging() -> None:
    """Configure structlog console logging."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_bq_client() -> bigquery.Client:
    """Build a BigQuery client with explicit service-account credentials.

    Returns:
        Authenticated BigQuery client.
    """
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return bigquery.Client(project=PROJECT_ID, credentials=creds)


def normalize_company_name(name: str) -> str:
    """Normalize company names for loose matching.

    Args:
        name: Raw company or organization name.

    Returns:
        Normalized name string.
    """
    text = name.strip()
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("㈱", "株式会社")
    text = text.replace("（株）", "株式会社").replace("(株)", "株式会社")
    text = text.replace("　", " ").replace("・", "")
    text = re.sub(r"\s+", "", text)
    text = text.upper()
    suffixes = [
        "株式会社",
        "有限会社",
        "合同会社",
        "ホールディングス",
        "HOLDINGS",
        "INC.",
        "INC",
        "LTD.",
        "LTD",
        "LLC",
        "CO.,",
        "CO.",
        "CORPORATION",
    ]
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if text.endswith(suffix):
                text = text[: -len(suffix)]
                changed = True
    return text


def fetch_candidates(client: bigquery.Client, limit: int | None) -> list[dict[str, Any]]:
    """Fetch candidate chunks that may contain major-customer disclosures.

    Args:
        client: BigQuery client.
        limit: Maximum number of chunks. None fetches all candidates.

    Returns:
        Candidate rows as dictionaries.
    """
    limit_clause = "\n    LIMIT @limit" if limit is not None else ""
    sql = f"""
    SELECT SECURITY_CODE, FILER_NAME, SECTION_CATEGORY, CHUNK_TEXT
    FROM `{IR_TABLE}`
    WHERE SUBMISSION_DATE >= '2025-01-01'
      AND DOC_TYPE = '有価証券報告書'
      AND CHUNK_TEXT LIKE '%販売実績%'
      AND (CHUNK_TEXT LIKE '%100分の10%' OR CHUNK_TEXT LIKE '%10％%')
    ORDER BY SECURITY_CODE
    {limit_clause}
    """
    config = None
    if limit is not None:
        config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("limit", "INT64", limit)]
        )
    rows = [dict(row) for row in client.query(sql, job_config=config).result()]
    log.info("candidate_chunks_fetched", count=len(rows))
    return rows


def write_candidates(rows: list[dict[str, Any]], path: Path) -> None:
    """Write candidate chunks for Codex review.

    Args:
        rows: Candidate rows.
        path: JSONL output path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str))
            f.write("\n")
    log.info("candidate_jsonl_written", path=str(path), count=len(rows))


def load_stock_names(client: bigquery.Client) -> list[StockName]:
    """Load listed company names from STOCK_CODE_LIST.

    Args:
        client: BigQuery client.

    Returns:
        Normalized stock-name records.
    """
    sql = f"""
    SELECT DISTINCT TICKER, STOCK_NAME
    FROM `{STOCK_TABLE}`
    WHERE EXCHANGE = 'TSE'
      AND STOCK_NAME IS NOT NULL
    """
    records: list[StockName] = []
    for row in client.query(sql).result():
        name = str(row["STOCK_NAME"])
        records.append(
            StockName(
                ticker=str(row["TICKER"]),
                stock_name=name,
                normalized=normalize_company_name(name),
            )
        )
    log.info("stock_names_loaded", count=len(records))
    return records


def resolve_customer_code(customer_name: str, stocks: list[StockName]) -> tuple[str, str, int]:
    """Resolve a customer name to a listed ticker when confidence is high.

    Args:
        customer_name: Extracted customer name.
        stocks: Listed company names.

    Returns:
        Tuple of customer_code, matched_stock_name, match_score. Empty strings
        indicate no confident listed-company match.
    """
    normalized = normalize_company_name(customer_name)
    if not normalized:
        return "", "", 0

    exact = {stock.normalized: stock for stock in stocks}
    if normalized in exact:
        stock = exact[normalized]
        return stock.ticker, stock.stock_name, 100

    choices = {stock.normalized: stock for stock in stocks if stock.normalized}
    match = process.extractOne(normalized, choices.keys(), scorer=fuzz.WRatio)
    if match is None:
        return "", "", 0
    matched_norm, score, _ = match
    stock = choices[matched_norm]
    if score < 90:
        return "", "", int(score)
    return stock.ticker, stock.stock_name, int(score)


def load_manual_extractions(path: Path) -> list[dict[str, Any]]:
    """Load Codex-reviewed extraction JSONL.

    Args:
        path: JSONL file path.

    Returns:
        Extraction records.
    """
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
    log.info("manual_extractions_loaded", path=str(path), records=len(records))
    return records


_REGION_PATTERNS = re.compile(
    r"^(北海道|東北|関東|中部|北陸|東海|近畿|関西|中国|四国|九州|沖縄|"
    r"青森|岩手|宮城|秋田|山形|福島|茨城|栃木|群馬|埼玉|千葉|東京|神奈川|"
    r"新潟|富山|石川|福井|山梨|長野|岐阜|静岡|愛知|三重|滋賀|京都|大阪|"
    r"兵庫|奈良|和歌山|鳥取|島根|岡山|広島|山口|徳島|香川|愛媛|高知|"
    r"福岡|佐賀|長崎|熊本|大分|宮崎|鹿児島)(都|道|府|県|地区|地方|エリア)?$"
)


def _is_region_name(name: str) -> bool:
    """Return True if the customer name is a region/prefecture, not a company."""
    return bool(_REGION_PATTERNS.match(name.strip()))


def build_output_rows(
    extractions: list[dict[str, Any]],
    stocks: list[StockName],
) -> tuple[list[dict[str, Any]], int]:
    """Build output CSV rows from Codex extractions.

    Args:
        extractions: Codex-reviewed extraction records.
        stocks: Listed company names.

    Returns:
        Tuple of output rows and skipped supplier count.
    """
    rows: list[dict[str, Any]] = []
    seen_rows: set[tuple[str, str, float, int]] = set()
    skipped = 0
    for record in extractions:
        customers = record.get("customers") or []
        supplier_code = record.get("supplier_code") or record.get("security_code")
        supplier_name = record.get("supplier_name") or record.get("filer_name")
        if not customers:
            skipped += 1
            log.info(
                "supplier_skipped",
                supplier_code=supplier_code,
                supplier_name=supplier_name,
                reason=record.get("skip_reason", "no_customers"),
            )
            continue
        for customer in customers:
            customer_name = str(customer["customer_name"])
            if _is_region_name(customer_name):
                continue
            revenue_pct = float(customer["revenue_pct"])
            source_year = int(record.get("source_year", 2025))
            row_key = (str(supplier_code), customer_name, revenue_pct, source_year)
            if row_key in seen_rows:
                continue
            seen_rows.add(row_key)
            customer_code, matched_name, score = resolve_customer_code(customer_name, stocks)
            rows.append(
                {
                    "supplier_code": str(supplier_code),
                    "supplier_name": str(supplier_name),
                    "customer_code": customer_code,
                    "customer_name": customer_name,
                    "revenue_pct": revenue_pct,
                    "source_year": source_year,
                    "matched_stock_name": matched_name,
                    "match_score": score,
                    "source": "edinet_yuho_2025",
                }
            )
    return rows, skipped


def write_supply_chain_csv(rows: list[dict[str, Any]], path: Path) -> None:
    """Write supply-chain facts to CSV.

    Args:
        rows: Output rows.
        path: CSV path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "supplier_code",
        "supplier_name",
        "customer_code",
        "customer_name",
        "revenue_pct",
        "source_year",
        "matched_stock_name",
        "match_score",
        "source",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    matched = sum(1 for row in rows if row["customer_code"])
    rate = matched / len(rows) if rows else 0.0
    log.info(
        "supply_chain_csv_written",
        path=str(path),
        extracted_rows=len(rows),
        name_resolved=matched,
        name_resolve_rate=round(rate, 3),
    )


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--candidates-jsonl", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--manual-jsonl", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--fetch-only",
        action="store_true",
        help="Only fetch candidate chunks for Codex review.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the extraction workflow."""
    configure_logging()
    args = parse_args()
    client = get_bq_client()

    candidates = fetch_candidates(client, args.limit)
    write_candidates(candidates, args.candidates_jsonl)
    if args.fetch_only:
        return 0

    if args.manual_jsonl is None:
        log.error("manual_jsonl_required", hint="Review candidates with Codex and pass --manual-jsonl")
        return 2

    extractions = load_manual_extractions(args.manual_jsonl)
    stocks = load_stock_names(client)
    rows, skipped = build_output_rows(extractions, stocks)
    write_supply_chain_csv(rows, args.output)
    log.info(
        "summary",
        reviewed_records=len(extractions),
        skipped_records=skipped,
        extracted_customer_rows=len(rows),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

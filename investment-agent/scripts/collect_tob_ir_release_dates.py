"""Collect official TOB IR release dates for DELISTED_STOCKS.

This script is read-only against BigQuery. It exports an investigation artifact
for Claude Code to review/load later.

Sources:
  1. BQ STOCK.TDNET_DOCUMENTS_ENHANCED candidates.
  2. irbank.net TDnet listing pages, which mirror official TDnet disclosures.

It intentionally does not update STOCK.DELISTED_STOCKS.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from google.cloud import bigquery
from google.oauth2 import service_account


PROJECT = "gmailpj-357912"
DELISTED_TABLE = f"{PROJECT}.STOCK.DELISTED_STOCKS"
TDNET_TABLE = f"{PROJECT}.STOCK.TDNET_DOCUMENTS_ENHANCED"
KEY_FILE = "keys/gcp-service-account.json"
JST = timezone(timedelta(hours=9))

DEFAULT_OUT_DIR = Path("data/reports/tob_ir_release_dates")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

NOTICE_HINTS = [
    "公開買付けの開始予定",
    "公開買付け開始予定",
    "公開買付けの実施予定",
    "公開買付け実施予定",
    "公開買付けの開始に向け",
    "公開買付けの実施に向け",
    "公開買付けを開始する予定",
    "公開買付けを実施する予定",
]

FORMAL_HINTS = [
    "公開買付けの開始",
    "公開買付け開始",
    "公開買付けに関するお知らせ",
    "公開買い付けの開始",
    "公開買い付け開始",
    "ＴＯＢの実施",
    "TOBの実施",
    "MBOの実施",
    "ＭＢＯの実施",
    "意見表明",
    "賛同",
    "応募推奨",
]

EXCLUDE_HINTS = [
    "公開買付けへの応募",
    "公開買い付けへの応募",
    "公開買付への応募",
    "応募及び特別利益",
    "結果",
    "成立",
    "終了",
    "応募状況",
    "買付期間の延長",
    "条件変更",
    "一部訂正",
    "訂正",
    "変更に関するお知らせ",
    "株式併合",
    "株式売渡請求",
    "吸収合併",
    "臨時株主総会",
    "上場廃止",
    "親会社及び主要株主",
]


@dataclass
class Target:
    ticker: str
    company_name: str | None
    market: str | None
    delisting_date: date | None
    tob_announcement_date: date | None
    tob_price: float | None
    tob_doc_id: str | None


@dataclass
class Candidate:
    ticker: str
    company_name: str | None
    market: str | None
    delisting_date: str | None
    tob_price: float | None
    ir_release_date: str | None
    ir_release_kind: str
    source: str
    doc_id_or_url: str
    doc_title: str
    evidence_text: str
    confidence: str
    notes: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=0.35)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    return parser.parse_args()


def bq_client() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return bigquery.Client(project=PROJECT, credentials=creds)


def load_targets(client: bigquery.Client, limit: int | None) -> list[Target]:
    sql = f"""
    SELECT
      TICKER,
      COMPANY_NAME,
      MARKET_SEGMENT,
      DELISTING_DATE,
      TOB_ANNOUNCEMENT_DATE,
      TOB_PRICE,
      TOB_DOC_ID
    FROM `{DELISTED_TABLE}`
    WHERE IS_TOB_MBO = TRUE
    ORDER BY DELISTING_DATE, TICKER
    """
    if limit is not None:
        sql += f"\nLIMIT {int(limit)}"
    rows = client.query(sql).result()
    return [
        Target(
            ticker=str(r.TICKER).zfill(4),
            company_name=r.COMPANY_NAME,
            market=r.MARKET_SEGMENT,
            delisting_date=r.DELISTING_DATE,
            tob_announcement_date=r.TOB_ANNOUNCEMENT_DATE,
            tob_price=r.TOB_PRICE,
            tob_doc_id=r.TOB_DOC_ID,
        )
        for r in rows
    ]


def _date_str(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _window(target: Target) -> tuple[date, date]:
    end = target.delisting_date or date.today()
    anchor = target.tob_announcement_date or target.delisting_date or date.today()
    start = anchor - timedelta(days=540)
    return start, end


def classify_title(title: str) -> tuple[int, str, str]:
    normalized = title.replace("（", "(").replace("）", ")")
    if any(h in normalized for h in EXCLUDE_HINTS):
        return 0, "除外", "excluded_followup_or_correction"
    if any(h in normalized for h in NOTICE_HINTS):
        return 100, "予告", "notice_title_match"
    if any(h in normalized for h in FORMAL_HINTS):
        return 80, "正式", "formal_title_match"
    if "公開買付" in normalized or "公開買い付け" in normalized:
        return 60, "正式", "generic_tob_title_match"
    return 0, "不明", "no_match"


def parse_irbank_entries(html: str, ticker: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict[str, Any]] = []
    current_date: str | None = None
    for node in soup.find_all(["dt", "dd"]):
        if node.name == "dt":
            txt = node.get_text(" ", strip=True)
            if re.match(r"^\d{4}/\d{2}/\d{2}$", txt):
                current_date = txt.replace("/", "-")
            continue
        if node.name != "dd" or not current_date:
            continue
        a = node.find("a")
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        href = a.get("href") or ""
        doc_id = ""
        m = re.search(r"/(\d{4})/(\d{18})", href)
        if m:
            doc_id = m.group(2)
        elif href:
            doc_id = href.rsplit("/", 1)[-1]
        entries.append(
            {
                "date": current_date,
                "title": title,
                "href": "https://irbank.net" + href if href.startswith("/") else href,
                "doc_id": doc_id,
                "ticker": ticker,
            }
        )
    return entries


def fetch_irbank_entries(ticker: str, session: requests.Session) -> tuple[list[dict[str, Any]], str | None]:
    url = f"https://irbank.net/{ticker}/tdnet"
    try:
        resp = session.get(url, timeout=25)
        resp.raise_for_status()
    except Exception as exc:
        return [], f"irbank_fetch_error: {exc}"
    return parse_irbank_entries(resp.text, ticker), None


def choose_candidate(target: Target, entries: list[dict[str, Any]], source: str) -> Candidate | None:
    start, end = _window(target)
    scored: list[tuple[date, int, str, str, dict[str, Any]]] = []
    for entry in entries:
        try:
            entry_date = date.fromisoformat(str(entry["date"]))
        except ValueError:
            continue
        if not (start <= entry_date <= end):
            continue
        score, kind, note = classify_title(str(entry["title"]))
        if score <= 0:
            continue
        scored.append((entry_date, -score, kind, note, entry))
    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], x[1]))
    entry_date, neg_score, kind, note, entry = scored[0]
    confidence = "high" if -neg_score >= 80 else "medium"
    return Candidate(
        ticker=target.ticker,
        company_name=target.company_name,
        market=target.market,
        delisting_date=_date_str(target.delisting_date),
        tob_price=target.tob_price,
        ir_release_date=entry_date.isoformat(),
        ir_release_kind=kind,
        source=source,
        doc_id_or_url=entry.get("doc_id") or entry.get("href") or "",
        doc_title=str(entry["title"]),
        evidence_text=str(entry["title"]),
        confidence=confidence,
        notes=note,
    )


def load_bq_tdnet_candidates(client: bigquery.Client, targets: list[Target]) -> dict[tuple[str, str | None], Candidate]:
    terms = [
        "公開買付",
        "公開買い付け",
        "ＴＯＢ",
        "TOB",
        "MBO",
        "ＭＢＯ",
        "賛同",
        "応募推奨",
        "開始予定",
        "実施予定",
    ]
    where_terms = " OR ".join([f"t.DOC_TITLE LIKE '%{term}%'" for term in terms])
    sql = f"""
    WITH d AS (
      SELECT TICKER, COMPANY_NAME, MARKET_SEGMENT, DELISTING_DATE, TOB_ANNOUNCEMENT_DATE
      FROM `{DELISTED_TABLE}`
      WHERE IS_TOB_MBO = TRUE
    ), td AS (
      SELECT
        d.TICKER,
        d.COMPANY_NAME,
        d.MARKET_SEGMENT,
        d.DELISTING_DATE,
        d.TOB_ANNOUNCEMENT_DATE,
        t.SUBMISSION_DATE,
        t.DOC_ID,
        t.DOC_TITLE,
        ROW_NUMBER() OVER (
          PARTITION BY d.TICKER, d.DELISTING_DATE, t.DOC_ID
          ORDER BY t.CHUNK_INDEX NULLS LAST
        ) AS rn
      FROM d
      JOIN `{TDNET_TABLE}` t
        ON t.TICKER = d.TICKER
       AND t.SUBMISSION_DATE BETWEEN DATE_SUB(COALESCE(d.TOB_ANNOUNCEMENT_DATE, d.DELISTING_DATE, CURRENT_DATE()), INTERVAL 540 DAY)
                                 AND COALESCE(d.DELISTING_DATE, CURRENT_DATE())
      WHERE ({where_terms})
    )
    SELECT * FROM td WHERE rn = 1
    """
    by_key: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for r in client.query(sql).result():
        key = (str(r.TICKER).zfill(4), _date_str(r.DELISTING_DATE))
        by_key.setdefault(key, []).append(
            {
                "date": _date_str(r.SUBMISSION_DATE),
                "title": r.DOC_TITLE,
                "doc_id": r.DOC_ID,
                "href": "",
            }
        )

    target_map = {(t.ticker, _date_str(t.delisting_date)): t for t in targets}
    selected: dict[tuple[str, str | None], Candidate] = {}
    for key, entries in by_key.items():
        target = target_map.get(key)
        if not target:
            continue
        cand = choose_candidate(target, entries, "BQ_TDNET")
        if cand:
            selected[key] = cand
    return selected


def unresolved_candidate(target: Target, note: str) -> Candidate:
    return Candidate(
        ticker=target.ticker,
        company_name=target.company_name,
        market=target.market,
        delisting_date=_date_str(target.delisting_date),
        tob_price=target.tob_price,
        ir_release_date=None,
        ir_release_kind="不明",
        source="UNRESOLVED",
        doc_id_or_url="",
        doc_title="",
        evidence_text="",
        confidence="unresolved",
        notes=note,
    )


def write_outputs(out_dir: Path, rows: list[Candidate]) -> tuple[Path, Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"tob_ir_release_dates_{ts}.csv"
    jsonl_path = out_dir / f"tob_ir_release_dates_{ts}.jsonl"
    claude_csv_path = out_dir / f"tob_announcement_dates_for_claude_{ts}.csv"
    summary_path = out_dir / f"tob_ir_release_dates_{ts}_summary.json"

    fieldnames = list(asdict(rows[0]).keys()) if rows else list(Candidate.__dataclass_fields__.keys())
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    with jsonl_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")

    claude_rows = build_claude_rows(rows)
    with claude_csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["ticker", "tob_announcement_date", "tob_price", "market"],
        )
        writer.writeheader()
        writer.writerows(claude_rows)

    summary = {
        "generated_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
        "total": len(rows),
        "claude_export_rows": len(claude_rows),
        "claude_export_path": str(claude_csv_path),
        "by_source": {},
        "by_confidence": {},
        "by_kind": {},
    }
    for row in rows:
        summary["by_source"][row.source] = summary["by_source"].get(row.source, 0) + 1
        summary["by_confidence"][row.confidence] = summary["by_confidence"].get(row.confidence, 0) + 1
        summary["by_kind"][row.ir_release_kind] = summary["by_kind"].get(row.ir_release_kind, 0) + 1
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return csv_path, jsonl_path, claude_csv_path, summary_path


def build_claude_rows(rows: list[Candidate]) -> list[dict[str, str | float | None]]:
    """Return one earliest official TOB announcement per ticker for Claude Code.

    `tob_announcement_date` means the official IR first-release date gathered as
    `ir_release_date`, not the TOB tender-offer start date.
    """
    selected: dict[str, Candidate] = {}
    for row in rows:
        if row.confidence == "unresolved" or not row.ir_release_date:
            continue
        current = selected.get(row.ticker)
        if current is None or str(row.ir_release_date) < str(current.ir_release_date):
            selected[row.ticker] = row

    out: list[dict[str, str | float | None]] = []
    for ticker in sorted(selected):
        row = selected[ticker]
        out.append(
            {
                "ticker": row.ticker,
                "tob_announcement_date": row.ir_release_date,
                "tob_price": row.tob_price,
                "market": row.market,
            }
        )
    return out


def main() -> int:
    args = parse_args()
    client = bq_client()
    targets = load_targets(client, args.limit)
    bq_candidates = load_bq_tdnet_candidates(client, targets)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    rows: list[Candidate] = []
    for idx, target in enumerate(targets, 1):
        key = (target.ticker, _date_str(target.delisting_date))
        entries, err = fetch_irbank_entries(target.ticker, session)
        cand = choose_candidate(target, entries, "WEB_IRBANK_TDNET") if entries else None
        if not cand:
            cand = bq_candidates.get(key)
        if cand:
            rows.append(cand)
        else:
            rows.append(unresolved_candidate(target, err or "no_official_tdnet_candidate_found"))
        if idx % 25 == 0:
            print(f"processed {idx}/{len(targets)}", flush=True)
        time.sleep(args.sleep)

    csv_path, jsonl_path, claude_csv_path, summary_path = write_outputs(Path(args.out_dir), rows)
    print(f"csv={csv_path}")
    print(f"jsonl={jsonl_path}")
    print(f"claude_csv={claude_csv_path}")
    print(f"summary={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Screen earnings cascade opportunities from association_pairs.csv.

Finds pairs where:
  - Leader has already reported earnings AND stock surged (大幅高)
  - Follower has NOT yet reported earnings
  → Follower is a buy candidate before their earnings announcement.

Usage:
  PYTHONUTF8=1 python scripts/screen_earnings_cascade.py [--min-return 3.0] [--season-start 2026-04-20]
"""

import argparse
import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog
from google.cloud import bigquery
from google.oauth2 import service_account

logger = structlog.get_logger()

JST = ZoneInfo("Asia/Tokyo")
KEY_FILE = Path("keys/gcp-service-account.json")
PROJECT_ID = "gmailpj-357912"
ASSOCIATION_CSV = Path("data/master/association_pairs.csv")

_REPORTED_SQL = """
SELECT TICKER, DISCLOSED_DATE, LATEST_TYPE, FY_END
FROM `gmailpj-357912.STOCK.V_LATEST_DISCLOSURE`
WHERE TICKER IN UNNEST(@tickers)
  AND DISCLOSED_DATE >= @season_start
  AND LATEST_TYPE IS NOT NULL
"""

_SCHEDULED_SQL = """
SELECT TICKER, MIN(DISCLOSURE_DATE) AS next_scheduled_date
FROM `gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR`
WHERE RECORD_TYPE = 'S'
  AND CATEGORY = 'R'
  AND DISCLOSURE_DATE >= @today
  AND TICKER IN UNNEST(@tickers)
GROUP BY TICKER
"""

_PRICE_REACTION_SQL = """
WITH earnings_dates AS (
  SELECT
    TICKER,
    DISCLOSED_DATE AS disc_date
  FROM `gmailpj-357912.STOCK.V_LATEST_DISCLOSURE`
  WHERE TICKER IN UNNEST(@tickers)
    AND DISCLOSED_DATE >= @season_start
    AND LATEST_TYPE IS NOT NULL
),
price_window AS (
  SELECT
    p.TICKER,
    p.DATE,
    p.ADJ_CLOSE,
    e.disc_date,
    ROW_NUMBER() OVER (PARTITION BY p.TICKER ORDER BY p.DATE DESC) AS rn_desc,
    LAG(p.ADJ_CLOSE) OVER (PARTITION BY p.TICKER ORDER BY p.DATE) AS prev_close
  FROM `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS` p
  JOIN earnings_dates e ON p.TICKER = e.TICKER
  WHERE p.DATE BETWEEN DATE_SUB(e.disc_date, INTERVAL 5 DAY)
                    AND DATE_ADD(e.disc_date, INTERVAL 5 DAY)
)
SELECT
  TICKER AS ticker,
  disc_date,
  -- pre-earnings close (last trading day before or on disc_date)
  MAX(IF(DATE <= disc_date, ADJ_CLOSE, NULL)) AS close_on_disc,
  -- post-earnings close (first trading day after disc_date)
  MIN(IF(DATE > disc_date, ADJ_CLOSE, NULL)) AS close_after_disc,
  -- pre-earnings close (last trading day before disc_date)
  MAX(IF(DATE < disc_date, ADJ_CLOSE, NULL)) AS close_before_disc,
  -- latest close
  MAX(ADJ_CLOSE) AS latest_close
FROM price_window
GROUP BY TICKER, disc_date
"""


def _load_pairs() -> list[dict]:
    """Load association pairs CSV."""
    with ASSOCIATION_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _get_bq_client() -> bigquery.Client:
    """Create authenticated BQ client."""
    creds = service_account.Credentials.from_service_account_file(str(KEY_FILE))
    return bigquery.Client(project=PROJECT_ID, credentials=creds)


def _query_bq(client: bigquery.Client, sql: str, params: list) -> list[dict]:
    """Run parameterized BQ query and return list of dicts."""
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    result = client.query(sql, job_config=job_config).result()
    return [dict(row) for row in result]


def _is_jp_listed(code: str) -> bool:
    """Check if code is JP-listed."""
    import re
    return bool(re.match(r"^\d{3,4}[A-Z]?$", code))


def main() -> None:
    """Run earnings cascade screening."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-return", type=float, default=3.0,
                        help="Minimum leader earnings reaction (%%)")
    parser.add_argument("--season-start", type=str, default="2026-04-20",
                        help="Earnings season start date (YYYY-MM-DD)")
    args = parser.parse_args()

    today = datetime.now(tz=JST).date()
    season_start = date.fromisoformat(args.season_start)
    min_ret = args.min_return / 100.0

    pairs = _load_pairs()
    logger.info("loaded pairs", count=len(pairs))

    jp_leaders = {p["leader_code"] for p in pairs if _is_jp_listed(p["leader_code"])}
    jp_followers = {p["follower_code"] for p in pairs if _is_jp_listed(p["follower_code"])}
    all_jp = sorted(jp_leaders | jp_followers)
    logger.info("JP tickers", leaders=len(jp_leaders), followers=len(jp_followers), total=len(all_jp))

    client = _get_bq_client()

    # --- Query 1: Reported check via fin_summary (authoritative) ---
    reported_params = [
        bigquery.ArrayQueryParameter("tickers", "STRING", all_jp),
        bigquery.ScalarQueryParameter("season_start", "DATE", season_start),
    ]
    reported_rows = _query_bq(client, _REPORTED_SQL, reported_params)
    logger.info("fin_summary reported", rows=len(reported_rows))

    reported: dict[str, dict] = {}
    for r in reported_rows:
        reported[r["TICKER"]] = {
            "disc_date": r["DISCLOSED_DATE"],
            "quarter": r["LATEST_TYPE"],
        }

    # --- Query 1b: Scheduled dates for unreported (informational) ---
    unreported_jp = [t for t in all_jp if t not in reported]
    not_yet: dict[str, dict] = {}
    if unreported_jp:
        sched_params = [
            bigquery.ArrayQueryParameter("tickers", "STRING", unreported_jp),
            bigquery.ScalarQueryParameter("today", "DATE", today),
        ]
        sched_rows = _query_bq(client, _SCHEDULED_SQL, sched_params)
        for r in sched_rows:
            not_yet[r["TICKER"]] = {"next_date": r["next_scheduled_date"]}
    for t in unreported_jp:
        if t not in not_yet:
            not_yet[t] = {"next_date": None}

    logger.info("reported leaders", count=len([t for t in reported if t in jp_leaders]))
    logger.info("unreported followers", count=len([t for t in not_yet if t in jp_followers]))

    # --- Query 2: Price reaction for reported leaders ---
    reported_leaders = [t for t in reported if t in jp_leaders]
    if not reported_leaders:
        logger.warning("no reported leaders found")
        return

    price_params = [
        bigquery.ArrayQueryParameter("tickers", "STRING", reported_leaders),
        bigquery.ScalarQueryParameter("season_start", "DATE", season_start),
    ]
    price_rows = _query_bq(client, _PRICE_REACTION_SQL, price_params)
    logger.info("price reactions", rows=len(price_rows))

    leader_reactions: dict[str, dict] = {}
    for r in price_rows:
        tk = r["ticker"]
        before = r.get("close_before_disc")
        after = r.get("close_after_disc")
        if before and after and before > 0:
            ret = (after - before) / before
            leader_reactions[tk] = {
                "disc_date": r["disc_date"],
                "return": ret,
                "close_before": before,
                "close_after": after,
            }

    surged = {tk: v for tk, v in leader_reactions.items() if v["return"] >= min_ret}
    logger.info("surged leaders", count=len(surged), threshold=f"{args.min_return}%")

    # --- Filter pairs ---
    candidates: list[dict] = []
    for p in pairs:
        lc = p["leader_code"]
        fc = p["follower_code"]

        if lc not in surged:
            continue
        if fc not in not_yet and fc in reported:
            continue

        lr = surged[lc]
        fc_info = not_yet.get(fc, {})

        candidates.append({
            "leader_code": lc,
            "leader_name": p["leader_name"],
            "leader_disc_date": str(lr["disc_date"]),
            "leader_return_pct": round(lr["return"] * 100, 1),
            "follower_code": fc,
            "follower_name": p["follower_name"],
            "follower_next_date": str(fc_info.get("next_date", "未定")),
            "pair_type": p["pair_type"],
            "relationship": p["relationship"],
            "dependency_pct": p["dependency_pct"],
        })

    candidates.sort(key=lambda x: (-x["leader_return_pct"], x["follower_code"]))

    # --- Output ---
    logger.info("candidates", count=len(candidates))

    if not candidates:
        print("No candidates found.")
        return

    out_path = Path("data/csv/earnings_cascade_screen.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_fields = list(candidates[0].keys())
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields)
        writer.writeheader()
        writer.writerows(candidates)

    print(f"\n{'='*80}")
    print(f"Earnings Cascade Screen ({today})")
    print(f"Min leader return: {args.min_return}%  |  Season: {season_start}~")
    print(f"Candidates: {len(candidates)}")
    print(f"{'='*80}\n")

    seen_followers: set[str] = set()
    for c in candidates:
        fc = c["follower_code"]
        if fc in seen_followers:
            continue
        seen_followers.add(fc)
        print(
            f"  {c['follower_name']:16s}({c['follower_code']}) "
            f"← {c['leader_name']}({c['leader_code']}) "
            f"+{c['leader_return_pct']}% ({c['leader_disc_date']}) "
            f"| 次決算: {c['follower_next_date']} "
            f"| {c['pair_type']} | {c['relationship'][:40]}"
        )

    print(f"\n→ {out_path}")


if __name__ == "__main__":
    main()

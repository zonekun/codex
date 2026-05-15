"""Build association_pairs.csv from supply_chain.csv.

Converts supply chain facts into tradeable signal pairs for the
earnings cascade strategy (013).

Phase 1 — Direct pairs:
  EDINET: customer好決算 → supplier好決算 (revenue dependency)
  LLM:    demand-driver好決算 → JP supplier好決算

Phase 2 — Sibling pairs (derived):
  Shared demand driver → co-movement among followers.
  e.g. NVDA drives イビデン & アドバンテスト
  → イビデン好決算 predicts アドバンテスト好決算 (both directions)
"""

import argparse
import csv
import re
from collections import defaultdict
from itertools import combinations
from pathlib import Path

import structlog

logger = structlog.get_logger()

SUPPLY_CHAIN_CSV = Path("data/master/supply_chain.csv")
ASSOCIATION_CSV = Path("data/master/association_pairs.csv")

FIELDS = [
    "pair_id",
    "leader_code",
    "leader_name",
    "follower_code",
    "follower_name",
    "pair_type",
    "relationship",
    "dependency_pct",
    "via_code",
    "via_name",
    "source",
]

_JP_RE = re.compile(r"^\d{3,4}[A-Z]?$")
_FOREIGN_ALPHA_RE = re.compile(r"^[A-Z]{1,5}$")
_FOREIGN_EXCH_RE = re.compile(r"^\d+\.[A-Z]{2,3}$")

_NON_TRADEABLE = {"NONLISTED", "PRIVATE_FOREIGN"}


def _is_tradeable(code: str) -> bool:
    """Return True if code represents a tradeable security."""
    if code in _NON_TRADEABLE or code.startswith("GOV_") or "_SUB" in code:
        return False
    if _JP_RE.match(code) or _FOREIGN_ALPHA_RE.match(code) or _FOREIGN_EXCH_RE.match(code):
        return True
    return False


def _is_jp_listed(code: str) -> bool:
    """Return True if code is a JP-listed ticker."""
    return bool(_JP_RE.match(code))


def _clean_name(row: dict, role: str) -> str:
    """Pick the cleanest name available for leader/follower.

    Args:
        row: A supply_chain.csv row dict.
        role: 'leader' or 'follower' — determines which columns to check.
    """
    if role == "leader_edinet":
        mn = row.get("matched_stock_name", "")
        if mn and mn not in ("非上場", "非上場（海外）"):
            return mn
        return row["customer_name"]
    if role == "follower_edinet":
        return row["supplier_name"]
    if role == "leader_llm":
        return row["supplier_name"]
    if role == "follower_llm":
        mn = row.get("matched_stock_name", "")
        return mn if mn else row["customer_name"]
    return ""


def _build_direct_pairs(sc_rows: list[dict]) -> tuple[list[dict], set[tuple[str, str]]]:
    """Phase 1: Convert supply chain facts into direct association pairs."""
    pairs: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for row in sc_rows:
        src = row["source"]

        if src == "edinet_yuho_2025":
            leader_code = row["customer_code"]
            leader_name = _clean_name(row, "leader_edinet")
            follower_code = row["supplier_code"]
            follower_name = _clean_name(row, "follower_edinet")
            dep = float(row.get("revenue_pct") or 0)
            rel = f"{follower_name}の売上{dep}%が{leader_name}に依存"
        elif src == "llm_web_survey":
            leader_code = row["supplier_code"]
            leader_name = _clean_name(row, "leader_llm")
            follower_code = row["customer_code"]
            follower_name = _clean_name(row, "follower_llm")
            dep = float(row.get("revenue_pct") or 0)
            rel = row.get("relationship", "")
        else:
            continue

        if not _is_tradeable(leader_code) or not _is_tradeable(follower_code):
            continue
        if leader_code == follower_code:
            continue

        key = (leader_code, follower_code)
        if key in seen:
            existing = next(p for p in pairs if (p["leader_code"], p["follower_code"]) == key)
            if src == "llm_web_survey" and row.get("relationship"):
                existing["relationship"] = row["relationship"]
            continue
        seen.add(key)

        pairs.append({
            "pair_id": f"D_{leader_code}_{follower_code}",
            "leader_code": leader_code,
            "leader_name": leader_name,
            "follower_code": follower_code,
            "follower_name": follower_name,
            "pair_type": "direct",
            "relationship": rel,
            "dependency_pct": dep,
            "via_code": "",
            "via_name": "",
            "source": src,
        })

    return pairs, seen


def _build_sibling_pairs(
    direct_pairs: list[dict],
    seen: set[tuple[str, str]],
) -> list[dict]:
    """Phase 2: Derive sibling pairs from shared demand drivers."""
    leader_to_followers: dict[str, list[dict]] = defaultdict(list)
    for p in direct_pairs:
        leader_to_followers[p["leader_code"]].append(p)

    siblings: list[dict] = []
    for leader_code, followers in leader_to_followers.items():
        jp_followers = [f for f in followers if _is_jp_listed(f["follower_code"])]
        if len(jp_followers) < 2:
            continue

        leader_name = jp_followers[0]["leader_name"]

        for f1, f2 in combinations(jp_followers, 2):
            for a, b in [(f1, f2), (f2, f1)]:
                key = (a["follower_code"], b["follower_code"])
                if key in seen:
                    continue
                seen.add(key)

                siblings.append({
                    "pair_id": f"S_{a['follower_code']}_{b['follower_code']}_via{leader_code}",
                    "leader_code": a["follower_code"],
                    "leader_name": a["follower_name"],
                    "follower_code": b["follower_code"],
                    "follower_name": b["follower_name"],
                    "pair_type": "sibling",
                    "relationship": f"共通需要ドライバー: {leader_name}({leader_code})",
                    "dependency_pct": 0,
                    "via_code": leader_code,
                    "via_name": leader_name,
                    "source": "derived_sibling",
                })

    return siblings


def main() -> None:
    """Build association pairs CSV."""
    parser = argparse.ArgumentParser(description="Build association_pairs.csv")
    parser.add_argument("--no-siblings", action="store_true", help="Skip sibling pair derivation")
    parser.add_argument("--dry-run", action="store_true", help="Print stats without writing CSV")
    args = parser.parse_args()

    with SUPPLY_CHAIN_CSV.open(encoding="utf-8") as f:
        sc_rows = list(csv.DictReader(f))
    logger.info("supply_chain loaded", rows=len(sc_rows))

    direct_pairs, seen = _build_direct_pairs(sc_rows)
    logger.info("direct pairs", count=len(direct_pairs))

    sibling_pairs: list[dict] = []
    if not args.no_siblings:
        sibling_pairs = _build_sibling_pairs(direct_pairs, seen)
        logger.info("sibling pairs", count=len(sibling_pairs))

    all_pairs = direct_pairs + sibling_pairs
    logger.info("total association pairs", count=len(all_pairs))

    jp_follower = [p for p in all_pairs if _is_jp_listed(p["follower_code"])]
    logger.info("JP-listed followers", count=len(jp_follower))

    if args.dry_run:
        logger.info("dry-run mode, not writing CSV")
        return

    with ASSOCIATION_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(all_pairs)

    logger.info("written", path=str(ASSOCIATION_CSV), rows=len(all_pairs))


if __name__ == "__main__":
    main()

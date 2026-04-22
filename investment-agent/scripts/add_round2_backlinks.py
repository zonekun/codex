"""Round 2 フォローアッププラン MD への相互リンクを関連ファイル先頭に追加。

冪等: 既に同じバックリンクが含まれていればスキップ。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(r"G:\マイドライブ\claude\investment-agent")
PLAN_REL_FROM_LOGS = "../../docs/plans/20260418_monthly_bc_round2_followup.md"
PLAN_REL_FROM_KNOW = "../../plans/20260418_monthly_bc_round2_followup.md"
MARKER = "20260418_monthly_bc_round2_followup"

TARGETS: list[tuple[str, str]] = [
    # data/logs/
    ("data/logs/24_list.md", PLAN_REL_FROM_LOGS),
    ("data/logs/regression_10_investigation.md", PLAN_REL_FROM_LOGS),
    ("data/logs/bc_compare_diff_analysis.md", PLAN_REL_FROM_LOGS),
    ("data/logs/7127_7918_report.md", PLAN_REL_FROM_LOGS),
    ("data/logs/dl_46_investigation.md", PLAN_REL_FROM_LOGS),
    ("data/logs/3349_investigation.md", PLAN_REL_FROM_LOGS),
    ("data/logs/p1_fixes_applied.md", PLAN_REL_FROM_LOGS),
    ("data/logs/p3_fixes_applied.md", PLAN_REL_FROM_LOGS),
    ("data/logs/p4_bc_reverse_investigation.md", PLAN_REL_FROM_LOGS),
    ("data/logs/p2_bc_reverse_investigation.md", PLAN_REL_FROM_LOGS),
    ("data/logs/p2_fix_proposals.md", PLAN_REL_FROM_LOGS),
    ("data/logs/source_rename_log.md", PLAN_REL_FROM_LOGS),
    # docs/knowledges/
    ("docs/knowledges/tools/042_monthly_disclosure_master.md", PLAN_REL_FROM_KNOW),
]


def add_backlink(path: Path, rel_link: str) -> str:
    if not path.exists():
        return f"SKIP (missing): {path}"
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return f"SKIP (already): {path}"
    line = f"> 関連プラン: [20260418 月次BC突合 Round 2 フォローアップ]({rel_link})\n\n"
    path.write_text(line + text, encoding="utf-8")
    return f"OK: {path}"


def main() -> None:
    for rel, link in TARGETS:
        print(add_backlink(ROOT / rel, link))


if __name__ == "__main__":
    main()

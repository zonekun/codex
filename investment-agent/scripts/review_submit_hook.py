# -*- coding: utf-8 -*-
"""レビュー提出時のAgent起動リマインダ.

Claude Code PostToolUse hook (Write|Edit) から呼ばれる。
docs/reviews/ 配下にレビューMDが作成された場合、
レビュワーAgent起動を促す。

事故背景: review 018 / 100 — 提出MD作成後にmd-reviewer Agent起動が脱落する再発パターン。
意志依存型（ルール追記）では防げなかったため構造的強制としてフック化。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath


REVIEW_DIR_PATTERNS = [
    "docs/reviews/",
    "docs\\reviews\\",
]

REVIEW_FILE_RE = re.compile(r"\d{3}_(mr|cr|so)_.*\.md$")


def _get_tool_info_from_stdin() -> tuple[str, str]:
    """stdin の hook JSON から tool_name, file_path を取得."""
    if sys.stdin.isatty():
        return "", ""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return "", ""
        data = json.loads(raw)
        tool_name = data.get("tool_name", "")
        file_path = data.get("tool_input", {}).get("file_path", "")
        return tool_name, file_path
    except (json.JSONDecodeError, AttributeError, TypeError):
        return "", ""


def _is_review_file(file_path: str) -> bool:
    """docs/reviews/ 配下のレビューMDか判定."""
    normalized = file_path.replace("\\", "/")
    if "docs/reviews/" not in normalized:
        return False
    filename = PurePosixPath(normalized).name
    return bool(REVIEW_FILE_RE.match(filename))


def main() -> None:
    """メインエントリポイント."""
    tool_name, file_path = _get_tool_info_from_stdin()
    if not file_path:
        return

    if not _is_review_file(file_path):
        return

    filename = Path(file_path).name
    prefix_match = re.match(r"\d{3}_(mr|cr|so)_", filename)
    if not prefix_match:
        return

    reviewer_type = prefix_match.group(1)
    reviewer_names = {
        "mr": "md-reviewer",
        "cr": "code-reviewer",
        "so": "structure-optimizer",
    }
    reviewer = reviewer_names.get(reviewer_type, "reviewer")

    print(
        f"[レビュー提出チェック] docs/reviews/ にレビューMD ({filename}) を検出。"
        f"{reviewer} Agent を起動しましたか？ "
        f"097ガイド §1-3: 提出MD作成→Agent起動はアトミック。中断禁止。",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()

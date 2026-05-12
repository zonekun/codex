# -*- coding: utf-8 -*-
"""新規スクリプト作成時の規約チェックリマインダ.

Claude Code PostToolUse hook (Write) から呼ばれる。
scripts/ 配下に新規 .py が作成された場合、004 チェックリスト確認を促す。

事故背景: review 095 — save_backlog_record.py 初版で10件の規約違反混入。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


def _get_file_path_from_stdin() -> str | None:
    """stdin の hook JSON から file_path を取得."""
    if sys.stdin.isatty():
        return None
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return None
        data = json.loads(raw)
        return data.get("tool_input", {}).get("file_path", "")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None


def _is_git_untracked(path: str) -> bool:
    """git status でファイルが untracked（新規）か判定."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", path],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(PROJECT_ROOT),
        )
        return result.stdout.strip().startswith("??") or result.stdout.strip().startswith("A")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def main() -> None:
    """メインエントリポイント."""
    file_path = _get_file_path_from_stdin()
    if not file_path:
        return

    p = Path(file_path)

    if p.suffix != ".py":
        return

    try:
        if not p.resolve().is_relative_to(SCRIPTS_DIR.resolve()):
            return
    except (ValueError, OSError):
        return

    if p.name.startswith("tmp_"):
        return

    if not _is_git_untracked(file_path):
        return

    print(
        "[規約チェック] scripts/ 新規 .py 検出。"
        "004 §新規バッチジョブ作成時チェックリスト を確認せよ "
        "(docs/knowledges/tools/004_coding_conventions.md)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()

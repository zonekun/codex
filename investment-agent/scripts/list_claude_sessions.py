#!/usr/bin/env python3
"""Claude Code クラッシュリカバリ用セッション一覧表示スクリプト.

`C:\\tmp\\claude_logs\\<session_id>\\` 以下に保存された全セッションを
更新時刻順（最新順）に列挙し、復元候補を選択しやすい形で表示する。

表示項目（各セッション）:
  - session_id
  - 最終更新時刻（JST）
  - 経過時間
  - conversation.log の行数 / ツール呼び出し回数
  - 最初のユーザープロンプト（冒頭要約用）
  - 直近のユーザープロンプト（作業再開の目印）

使い方:
  PYTHONUTF8=1 python scripts/list_claude_sessions.py
  PYTHONUTF8=1 python scripts/list_claude_sessions.py --limit 10
"""

from __future__ import annotations

import argparse
import platform
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=+9), "JST")

if platform.system() == "Windows":
    LOG_BASE = Path("C:/tmp/claude_logs")
else:
    LOG_BASE = Path("/tmp/claude_logs")

HEADER_RE = re.compile(
    r"^=== (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) JST \[(User|Claude)\] ===\s*$"
)
PROMPT_PREVIEW_CHARS = 80
LABEL_MAX_CHARS = 40


@dataclass
class SessionInfo:
    session_id: str
    dir_path: Path
    last_mtime: datetime
    conv_exists: bool
    tool_exists: bool
    console_exists: bool
    user_prompt_count: int
    tool_call_count: int
    first_user_prompt: str
    last_user_prompt: str
    last_user_prompt_ts: str
    label: str


def _read_label(session_dir: Path) -> str:
    """セッションディレクトリから label.txt を読み取る."""
    label_path = session_dir / "label.txt"
    try:
        return label_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_user_prompts(conv_path: Path) -> tuple[list[tuple[str, str]], int, int]:
    """conversation.log から User エントリを抽出する.

    Returns:
        (user_entries, user_count, claude_count)
        user_entries: [(timestamp, prompt_text), ...]
    """
    text = _read_text(conv_path)
    if not text:
        return [], 0, 0
    lines = text.splitlines()
    entries: list[tuple[str, str, list[str]]] = []
    current: tuple[str, str, list[str]] | None = None
    for line in lines:
        m = HEADER_RE.match(line)
        if m:
            if current is not None:
                entries.append(current)
            current = (m.group(1), m.group(2), [])
        elif current is not None:
            current[2].append(line)
    if current is not None:
        entries.append(current)

    user_entries: list[tuple[str, str]] = []
    user_count = 0
    claude_count = 0
    for ts, role, body in entries:
        if role == "User":
            user_count += 1
            body_text = "\n".join(body).strip()
            user_entries.append((ts, body_text))
        elif role == "Claude":
            claude_count += 1
    return user_entries, user_count, claude_count


def _count_tool_calls(tool_path: Path) -> int:
    text = _read_text(tool_path)
    if not text:
        return 0
    return sum(1 for line in text.splitlines() if line.strip())


def _latest_mtime(d: Path) -> datetime:
    latest = 0.0
    for f in d.iterdir():
        if f.is_file():
            mt = f.stat().st_mtime
            if mt > latest:
                latest = mt
    if latest == 0.0:
        latest = d.stat().st_mtime
    return datetime.fromtimestamp(latest, tz=JST)


def _preview(text: str, length: int = PROMPT_PREVIEW_CHARS) -> str:
    single = " ".join(text.split())
    if len(single) <= length:
        return single
    return single[:length] + "..."


def _format_elapsed(dt: datetime, now: datetime) -> str:
    delta = now - dt
    sec = int(delta.total_seconds())
    if sec < 0:
        return "未来?"
    if sec < 60:
        return f"{sec}秒前"
    if sec < 3600:
        return f"{sec // 60}分前"
    if sec < 86400:
        h = sec // 3600
        m = (sec % 3600) // 60
        return f"{h}時間{m}分前" if m else f"{h}時間前"
    d = sec // 86400
    h = (sec % 86400) // 3600
    return f"{d}日{h}時間前" if h else f"{d}日前"


def collect_sessions() -> list[SessionInfo]:
    if not LOG_BASE.exists():
        return []
    sessions: list[SessionInfo] = []
    for d in LOG_BASE.iterdir():
        if not d.is_dir():
            continue
        conv = d / "conversation.log"
        tool = d / "tool_trace.log"
        console = d / "console.log"
        user_entries, user_count, _claude_count = _parse_user_prompts(conv)
        tool_count = _count_tool_calls(tool)
        try:
            latest_dt = _latest_mtime(d)
        except (OSError, ValueError):
            continue

        first_prompt = user_entries[0][1] if user_entries else ""
        last_prompt = user_entries[-1][1] if user_entries else ""
        last_prompt_ts = user_entries[-1][0] if user_entries else ""

        label = _read_label(d)
        if not label and first_prompt:
            label = _preview(first_prompt, LABEL_MAX_CHARS)

        sessions.append(
            SessionInfo(
                session_id=d.name,
                dir_path=d,
                last_mtime=latest_dt,
                conv_exists=conv.exists(),
                tool_exists=tool.exists(),
                console_exists=console.exists(),
                user_prompt_count=user_count,
                tool_call_count=tool_count,
                first_user_prompt=_preview(first_prompt),
                last_user_prompt=_preview(last_prompt),
                last_user_prompt_ts=last_prompt_ts,
                label=label,
            )
        )
    sessions.sort(key=lambda s: s.last_mtime, reverse=True)
    return sessions


def render(
    sessions: list[SessionInfo],
    limit: int | None = None,
) -> str:
    if not sessions:
        return f"セッションが見つかりません: {LOG_BASE}"

    now = datetime.now(JST)

    if limit is not None:
        sessions = sessions[:limit]

    out: list[str] = []
    out.append("# Claude Code セッション一覧（最新順）")
    out.append(f"場所: {LOG_BASE}")
    out.append(f"セッション数: {len(sessions)}")
    out.append("")

    for i, s in enumerate(sessions, 1):
        mtime_str = s.last_mtime.strftime("%Y-%m-%d %H:%M:%S JST")
        elapsed = _format_elapsed(s.last_mtime, now)
        files = []
        if s.conv_exists:
            files.append("conversation")
        if s.tool_exists:
            files.append("tool_trace")
        if s.console_exists:
            files.append("console")
        files_str = ", ".join(files) if files else "(なし)"

        label_display = f" 「{s.label}」" if s.label else ""
        out.append(f"[{i}]{label_display}  (id: {s.session_id})")
        out.append(f"    最終更新   : {mtime_str}  ({elapsed})")
        out.append(f"    User発話   : {s.user_prompt_count}件 / Tool呼出 : {s.tool_call_count}件")
        out.append(f"    ログ種別   : {files_str}")
        if s.first_user_prompt:
            out.append(f"    初回prompt : {s.first_user_prompt}")
        if s.last_user_prompt and s.last_user_prompt != s.first_user_prompt:
            ts_tail = f" ({s.last_user_prompt_ts})" if s.last_user_prompt_ts else ""
            out.append(f"    直近prompt{ts_tail}: {s.last_user_prompt}")
        out.append(f"    パス       : {s.dir_path}")
        out.append("")

    out.append("選択方法:")
    out.append("  上記リストから復元したいセッションの番号（または session_id）をユーザーが指示する。")
    out.append("  指示を受けたら、そのセッションの conversation.log / tool_trace.log を読んで状況把握に移る。")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Claude Code セッション一覧表示")
    parser.add_argument("--limit", type=int, default=None, help="表示件数上限")
    args = parser.parse_args()

    sessions = collect_sessions()
    print(render(sessions, limit=args.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Claude Code 会話ログ記録スクリプト.

hooks (settings.local.json) から呼び出される:
  - UserPromptSubmit (--event=prompt)  : ユーザープロンプトを記録 + 古エントリ削除
  - Stop            (--event=stop)    : Claudeの最終レスポンスを記録
  - PostToolUse     (--event=tool)    : ツール呼び出しを記録（自律作業の追跡用）

ログファイル（セッション別ディレクトリに保存）:
  C:\\tmp\\claude_logs\\<session_id>\\conversation.log  : ユーザー ↔ Claude の対話ログ（24時間保持）
  C:\\tmp\\claude_logs\\<session_id>\\tool_trace.log    : ツール呼び出し追跡ログ（24時間保持）
  C:\\tmp\\claude_logs\\<session_id>\\console.log       : Bash出力ログ（24時間保持）

共有ファイル:
  C:\\tmp\\claude_logs\\debug.log                      : 本スクリプト自身のデバッグログ
  data/logs/active_jobs.md                            : Cloud Run ジョブ状態（自動追記）

セッション分離:
  --session-id=$PPID で親プロセスIDを受け取り、セッション別ディレクトリにログを分離。
  複数コンソールで同時作業しても混在しない。

クラッシュ後の再開方法:
  「クラッシュした。再開モード発動」と伝えると
  Claude が最新セッションのログを読み込んで状況を把握する。
"""

import json
import platform
import re
import shutil
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ==========================================
# 設定
# ==========================================

JST = timezone(timedelta(hours=+9), "JST")

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

# ログ保管ベースディレクトリ（ローカル、Google Drive外）
if platform.system() == "Windows":
    LOG_BASE = Path("C:/tmp/claude_logs")
else:
    LOG_BASE = Path("/tmp/claude_logs")

# 共有ファイル
DEBUG_LOG = LOG_BASE / "debug.log"
ACTIVE_JOBS = PROJECT_ROOT / "data" / "logs" / "active_jobs.md"

# 保持期間（時間）
RETENTION_HOURS = 24          # conversation.log, tool_trace.log
CONSOLE_RETENTION_HOURS = 24  # console.log
SESSION_CLEANUP_HOURS = 24    # 古いセッションディレクトリの削除

# Bash 出力の最大保存文字数
CONSOLE_OUTPUT_MAX_CHARS = 800

# PostToolUse でログ対象とするツール名
# （Read/WebFetch/WebSearch は多すぎるため除外）
TOOL_LOG_INCLUDE = {
    "Bash", "Edit", "Write", "NotebookEdit",
    "Agent", "Task",
}
# Bash でもスキップするコマンドプレフィックス（読み取り系）
BASH_SKIP_PREFIXES = ("cat ", "head ", "tail ", "ls ", "echo ", "grep ")

# ログエントリのヘッダーパターン
HEADER_RE = re.compile(r"^=== (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) JST \[")

# gcloud run jobs execute 検出パターン
GCLOUD_EXECUTE_RE = re.compile(
    r"gcloud(?:\.cmd)?\s+run\s+jobs\s+execute\s+([\w-]+)"
)
# gcloud 応答から実行IDを抽出
EXECUTION_ID_RE = re.compile(r"\[([\w-]+-[a-z0-9]{5})\]")


# ==========================================
# セッション管理
# ==========================================

def get_session_dir(session_id: str) -> Path:
    """セッションIDからログディレクトリパスを取得する."""
    return LOG_BASE / session_id


def get_log_paths(session_id: str) -> tuple[Path, Path, Path]:
    """セッション別のログファイルパスを返す."""
    d = get_session_dir(session_id)
    return (
        d / "conversation.log",
        d / "tool_trace.log",
        d / "console.log",
    )


def resolve_session_id(data: dict) -> str:
    """hook stdin JSON の session_id を取得する.

    Claude Code は全イベント（UserPromptSubmit/Stop/PostToolUse）で
    session_id (UUID) を stdin JSON に含む。
    フォールバック: CLI 引数 --session-id=<id>、最終手段は 'default'。
    """
    sid = data.get("session_id", "")
    if sid:
        return sid
    for arg in sys.argv:
        if arg.startswith("--session-id="):
            return arg.split("=", 1)[1]
    return "default"


# ==========================================
# ユーティリティ
# ==========================================

def now_str() -> str:
    """現在時刻をJST文字列で返す."""
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


def write_debug(msg: str) -> None:
    """デバッグログに書き込む（このスクリプト自身のエラー追跡用）."""
    try:
        DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"{now_str()} {msg}\n")
    except Exception:
        pass


def extract_text(content) -> str:
    """メッセージ content からテキスト部分のみを抽出する."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


# ==========================================
# クリーンアップ
# ==========================================

def _cleanup_log_by_hours(log_path: Path, hours: int) -> None:
    """指定時間を超えるエントリをログファイルから削除する."""
    if not log_path.exists():
        return
    content = log_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines(keepends=True)
    cutoff = datetime.now(JST) - timedelta(hours=hours)
    cutoff_naive = cutoff.replace(tzinfo=None)

    entry_starts: list[tuple[int, datetime | None]] = []
    for idx, line in enumerate(lines):
        m = HEADER_RE.match(line)
        if m:
            try:
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                ts = None
            entry_starts.append((idx, ts))

    if not entry_starts:
        return

    keep_lines: set[int] = set()
    for i, (start_idx, ts) in enumerate(entry_starts):
        end_idx = entry_starts[i + 1][0] if i + 1 < len(entry_starts) else len(lines)
        if ts is None or ts >= cutoff_naive:
            keep_lines.update(range(start_idx, end_idx))

    new_lines = [line for idx, line in enumerate(lines) if idx in keep_lines]
    log_path.write_text("".join(new_lines), encoding="utf-8")


def cleanup_conversation(log_path: Path) -> None:
    """conversation.log の古いエントリを削除する."""
    _cleanup_log_by_hours(log_path, RETENTION_HOURS)


def cleanup_tool_log(tool_log: Path) -> None:
    """tool_trace.log の古いエントリを削除する."""
    _cleanup_log_by_hours(tool_log, RETENTION_HOURS)


def cleanup_console_log(console_log: Path) -> None:
    """console.log の古いエントリを削除する."""
    _cleanup_log_by_hours(console_log, CONSOLE_RETENTION_HOURS)


def cleanup_old_sessions() -> None:
    """古いセッションディレクトリを削除する."""
    if not LOG_BASE.exists():
        return
    cutoff = datetime.now(JST) - timedelta(hours=SESSION_CLEANUP_HOURS)
    for session_dir in LOG_BASE.iterdir():
        if not session_dir.is_dir():
            continue
        # セッションディレクトリ内の最新ファイルの更新時刻をチェック
        try:
            latest_mtime = max(
                f.stat().st_mtime for f in session_dir.iterdir() if f.is_file()
            )
            latest_dt = datetime.fromtimestamp(latest_mtime, tz=JST)
            if latest_dt < cutoff:
                shutil.rmtree(session_dir, ignore_errors=True)
        except (ValueError, OSError):
            # 空ディレクトリやアクセスエラー
            shutil.rmtree(session_dir, ignore_errors=True)


# ==========================================
# セッションラベル
# ==========================================

LABEL_MAX_CHARS = 40


def _make_label(prompt: str) -> str:
    """プロンプトから短いセッションラベルを生成する."""
    single = " ".join(prompt.split())
    if len(single) <= LABEL_MAX_CHARS:
        return single
    return single[:LABEL_MAX_CHARS] + "…"


def save_session_label(session_id: str, prompt: str) -> None:
    """セッションの初回プロンプトからラベルを保存する.

    label.txt が既に存在する場合は上書きしない（初回のみ）。
    """
    label_path = get_session_dir(session_id) / "label.txt"
    if label_path.exists():
        return
    label_path.parent.mkdir(parents=True, exist_ok=True)
    label_path.write_text(_make_label(prompt), encoding="utf-8")


# ==========================================
# ログ書き込み
# ==========================================

def append_log(log_path: Path, role: str, text: str) -> None:
    """会話ログにエントリを追記する."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = now_str()
    header = f"=== {ts} JST [{role}] ===\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(header)
        f.write(text.rstrip("\n") + "\n\n")


def append_tool_log(tool_log: Path, line: str) -> None:
    """ツール追跡ログに1行追記する."""
    tool_log.parent.mkdir(parents=True, exist_ok=True)
    with open(tool_log, "a", encoding="utf-8") as f:
        f.write(f"{now_str()} {line}\n")


def append_console_log(console_log: Path, cmd_summary: str, output: str) -> None:
    """コンソール出力ログにエントリを追記する."""
    console_log.parent.mkdir(parents=True, exist_ok=True)
    ts = now_str()
    # 長い出力はトリム
    if len(output) > CONSOLE_OUTPUT_MAX_CHARS:
        output = output[:CONSOLE_OUTPUT_MAX_CHARS] + f"\n... (truncated, total {len(output)} chars)"
    with open(console_log, "a", encoding="utf-8") as f:
        f.write(f"=== {ts} JST [Bash] ===\n")
        f.write(f"CMD: {cmd_summary[:200]}\n")
        f.write(output.rstrip("\n") + "\n\n")


# ==========================================
# active_jobs.md 自動更新
# ==========================================

def auto_add_active_job(cmd: str, response: str) -> None:
    """gcloud run jobs execute を検出して active_jobs.md に自動追記する."""
    match = GCLOUD_EXECUTE_RE.search(cmd)
    if not match:
        return
    job_name = match.group(1)

    # 実行IDを応答から抽出
    exec_id = "-"
    exec_match = EXECUTION_ID_RE.search(response)
    if exec_match:
        exec_id = exec_match.group(1)

    # 引数を抽出（内容列用）
    args_match = re.search(r"--\s*args\s*=?\s*['\"]?([^'\"]+)", cmd)
    args_desc = args_match.group(1).strip()[:50] if args_match else "(デフォルト)"

    ts = now_str()

    # active_jobs.md に追記
    if not ACTIVE_JOBS.exists():
        ACTIVE_JOBS.parent.mkdir(parents=True, exist_ok=True)
        ACTIVE_JOBS.write_text(
            "# アクティブジョブ管理\n\n## 実行中\n\n"
            "| ジョブ名 | 実行ID | 実行開始 | 内容 | 状態 |\n"
            "|---------|--------|--------|------|------|\n",
            encoding="utf-8",
        )

    content = ACTIVE_JOBS.read_text(encoding="utf-8")

    # 「## 実行中」テーブルの末尾（次の ## の直前）に行を挿入
    new_row = f"| {job_name} | {exec_id} | {ts} JST | {args_desc} | 🔄 実行中 |\n"

    # テーブル末尾を探す: 「## 実行中」セクション内の最後の | 行の後
    lines = content.split("\n")
    insert_idx = None
    in_running_section = False
    for i, line in enumerate(lines):
        if line.strip().startswith("## 実行中"):
            in_running_section = True
            continue
        if in_running_section and line.strip().startswith("##"):
            # 次のセクション開始 → この直前に挿入
            insert_idx = i
            break
        if in_running_section and line.strip().startswith("|"):
            insert_idx = i + 1  # テーブル行の後

    if insert_idx is not None:
        lines.insert(insert_idx, new_row.rstrip("\n"))
        ACTIVE_JOBS.write_text("\n".join(lines), encoding="utf-8")
    else:
        # フォールバック: ファイル末尾に追記
        with open(ACTIVE_JOBS, "a", encoding="utf-8") as f:
            f.write(new_row)

    write_debug(f"[auto_job] Added: {job_name} ({exec_id})")


# ==========================================
# Stop イベント処理
# ==========================================

def handle_stop(data: dict, log_path: Path) -> None:
    """Stop フック: Claude の最終テキスト応答を記録する."""
    # 新仕様: last_assistant_message が直接渡される
    last_msg = data.get("last_assistant_message", "")
    if last_msg:
        append_log(log_path, "Claude", last_msg.strip())
        return

    # フォールバック: transcript_path からファイルを読む
    transcript_path = data.get("transcript_path", "")
    if transcript_path:
        import json as _json
        try:
            with open(transcript_path, encoding="utf-8") as f:
                transcript = _json.load(f)
            for msg in reversed(transcript):
                if msg.get("role") == "assistant":
                    text = extract_text(msg.get("content", "")).strip()
                    if text:
                        append_log(log_path, "Claude", text)
                    return
        except Exception as e:
            write_debug(f"[stop] transcript_path 読み込み失敗: {e}")

    write_debug(f"[stop] 応答テキスト取得失敗。dataのキー: {list(data.keys())}")


# ==========================================
# PostToolUse イベント処理
# ==========================================

def handle_tool(data: dict, tool_log: Path, console_log: Path) -> None:
    """PostToolUse フック: 重要なツール呼び出しを tool_trace.log に記録する."""
    tool_name = data.get("tool_name", "")

    if tool_name not in TOOL_LOG_INCLUDE:
        return

    tool_input = data.get("tool_input") or {}

    if tool_name == "Bash":
        cmd = str(tool_input.get("command", "")).strip()
        # 読み取り系コマンドはスキップ
        if any(cmd.startswith(p) for p in BASH_SKIP_PREFIXES):
            return
        # 長いコマンドは先頭120文字
        summary = cmd[:120].replace("\n", " ")
        append_tool_log(tool_log, f"[Bash] {summary}")
        # コンソール出力を console.log に保存
        output = data.get("tool_response", "") or ""
        if output and isinstance(output, str):
            cleanup_console_log(console_log)
            append_console_log(console_log, summary, output)
        # gcloud run jobs execute を検出 → active_jobs.md 自動追記
        try:
            auto_add_active_job(cmd, output if isinstance(output, str) else "")
        except Exception as e:
            write_debug(f"[auto_job] Error: {e}")

    elif tool_name in ("Edit", "Write", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        append_tool_log(tool_log, f"[{tool_name}] {path}")

    elif tool_name == "Agent":
        desc = tool_input.get("description", "")
        append_tool_log(tool_log, f"[Agent] {desc[:80]}")

    cleanup_tool_log(tool_log)


# ==========================================
# メイン
# ==========================================

def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else ""

    try:
        data = json.load(sys.stdin)
    except Exception as e:
        write_debug(f"[{event}] JSON parse error: {e}")
        sys.exit(0)

    session_id = resolve_session_id(data)
    log_path, tool_log, console_log = get_log_paths(session_id)

    try:
        if event == "--event=prompt":
            cleanup_conversation(log_path)
            cleanup_old_sessions()
            prompt = data.get("prompt", "").strip()
            if prompt:
                append_log(log_path, "User", prompt)
                append_tool_log(tool_log, f"[User] {prompt[:80]}")
                save_session_label(session_id, prompt)

        elif event == "--event=stop":
            handle_stop(data, log_path)

        elif event == "--event=tool":
            handle_tool(data, tool_log, console_log)

    except Exception as e:
        write_debug(f"[{event}] 処理中に例外: {e}\n{traceback.format_exc()}")

    print("{}", end="")
    sys.exit(0)


if __name__ == "__main__":
    main()

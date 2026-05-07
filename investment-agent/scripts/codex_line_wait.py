"""Codex-only ntfy bidirectional wait wrapper.

This wrapper deliberately keeps Codex operational safeguards outside
``notify.py`` because Claude Code also uses the shared notify module.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from notify import send_ntfy, wait_ntfy_reply  # noqa: E402


DEFAULT_STATE_PATH = Path(r"C:\tmp\codex_line_wait\active_gpt_wait.json")


def _jst_now() -> str:
    return datetime.now(tz=ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    return f'"{pid}"' in result.stdout


def _load_state(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {"status": "corrupt", "path": str(path)}


def _write_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    last_error: OSError | None = None
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.1)
    if last_error is not None:
        raise last_error


def _read_message(args: argparse.Namespace) -> str:
    if args.message_file:
        return Path(args.message_file).read_text(encoding="utf-8")
    if args.message_env:
        message = os.environ.get(args.message_env)
        if message is None:
            raise SystemExit(f"environment variable not set: {args.message_env}")
        return message
    raise SystemExit("--message-file or --message-env is required")


def _default_reply_path(args: argparse.Namespace, msg_id: str) -> str:
    if args.reply_file:
        return str(Path(args.reply_file))
    if args.stdout_log:
        return str(Path(args.stdout_log).with_suffix(".reply.txt"))
    return str(DEFAULT_STATE_PATH.with_name(f"reply_{msg_id}.txt"))


def _send_ntfy_with_id(message: str, title: str, priority: str, tags: str) -> tuple[str, int]:
    msg_id = send_ntfy(
        message,
        title=title,
        tags=tags,
        priority=priority,
        with_id=True,
    )
    if msg_id is None:
        raise RuntimeError("send_ntfy did not return a message id")
    return msg_id, 200


def cmd_status(args: argparse.Namespace) -> int:
    state_path = Path(args.state_path)
    state = _load_state(state_path)
    if state is None:
        print(f"[codex-line] no state path={state_path}", flush=True)
        return 1
    pid = state.get("pid")
    alive = _is_pid_alive(int(pid)) if isinstance(pid, int) else False
    state["pid_alive"] = alive
    reply_path = state.get("reply_text_path")
    if isinstance(reply_path, str) and Path(reply_path).exists():
        state["reply_text"] = Path(reply_path).read_text(encoding="utf-8")
    print(json.dumps(state, ensure_ascii=False, indent=2), flush=True)
    return 0


def cmd_clear_state(args: argparse.Namespace) -> int:
    state_path = Path(args.state_path)
    state_path.unlink(missing_ok=True)
    print(f"[codex-line] cleared state path={state_path}", flush=True)
    return 0


def cmd_mark_processed(args: argparse.Namespace) -> int:
    state_path = Path(args.state_path)
    state = _load_state(state_path)
    if state is None:
        print(f"[codex-line] no state path={state_path}", flush=True)
        return 1
    state["processed"] = True
    state["processed_at_jst"] = _jst_now()
    state["updated_at_jst"] = _jst_now()
    _write_state(state_path, state)
    print(f"[codex-line] marked processed state={state_path}", flush=True)
    return 0


def cmd_send_wait(args: argparse.Namespace) -> int:
    state_path = Path(args.state_path)
    active = _load_state(state_path)
    if active and not args.replace:
        if active.get("status") == "replied" and active.get("processed") is not True:
            print(
                f"[codex-line] unprocessed reply exists state={state_path}; "
                "run status, handle reply, then mark-processed",
                flush=True,
            )
            return 4
        pid = active.get("pid")
        if isinstance(pid, int) and _is_pid_alive(pid):
            print(
                f"[codex-line] active wait exists pid={pid} state={state_path}; "
                "use --replace only after explicitly abandoning it",
                flush=True,
            )
            return 3

    message = _read_message(args)
    started_at = _jst_now()
    since_ts = int(time.time())

    try:
        msg_id, http_status = _send_ntfy_with_id(message, args.title, args.priority, args.tags)
    except Exception as exc:
        failed = {
            "status": "send_failed",
            "pid": os.getpid(),
            "started_at_jst": started_at,
            "updated_at_jst": _jst_now(),
            "error": repr(exc),
            "stdout_log": args.stdout_log,
            "stderr_log": args.stderr_log,
        }
        _write_state(state_path, failed)
        print(f"[codex-line] send_failed error={exc!r}", flush=True)
        return 2

    state = {
        "status": "waiting",
        "processed": False,
        "pid": os.getpid(),
        "msg_id": msg_id,
        "title": args.title,
        "timeout_seconds": args.timeout,
        "started_at_jst": started_at,
        "updated_at_jst": _jst_now(),
        "stdout_log": args.stdout_log,
        "stderr_log": args.stderr_log,
        "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
        "http_status": http_status,
    }
    _write_state(state_path, state)
    print(f"[codex-line] sent http={http_status} id={msg_id} state={state_path}", flush=True)

    reply = wait_ntfy_reply(
        msg_id,
        timeout=args.timeout,
        since_ts=since_ts,
        own_body_rest=message,
    )
    if reply is None:
        state["status"] = "timeout"
        state["updated_at_jst"] = _jst_now()
        _write_state(state_path, state)
        print(f"[codex-line] timeout id={msg_id}", flush=True)
        return 2

    state["status"] = "replied"
    state["updated_at_jst"] = _jst_now()
    state["reply_sha256"] = hashlib.sha256(reply.encode("utf-8")).hexdigest()
    reply_path = _default_reply_path(args, msg_id)
    Path(reply_path).write_text(reply, encoding="utf-8")
    state["reply_text_path"] = reply_path
    state["processed"] = False
    _write_state(state_path, state)
    print(f"[codex-line] reply id={msg_id}", flush=True)
    print(reply, flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex-only LINE/ntfy wait wrapper")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_send = sub.add_parser("send-wait")
    p_send.add_argument("--message-file")
    p_send.add_argument("--message-env")
    p_send.add_argument("--title", default="GPT")
    p_send.add_argument("--timeout", type=float, default=10800.0)
    p_send.add_argument("--priority", choices=["low", "default", "high", "urgent"], default="urgent")
    p_send.add_argument("--tags", default="question")
    p_send.add_argument("--stdout-log", default="")
    p_send.add_argument("--stderr-log", default="")
    p_send.add_argument("--reply-file")
    p_send.add_argument("--replace", action="store_true")
    p_send.set_defaults(func=cmd_send_wait)

    p_status = sub.add_parser("status")
    p_status.set_defaults(func=cmd_status)

    p_clear = sub.add_parser("clear-state")
    p_clear.set_defaults(func=cmd_clear_state)

    p_processed = sub.add_parser("mark-processed")
    p_processed.set_defaults(func=cmd_mark_processed)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

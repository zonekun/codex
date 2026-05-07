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
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

from notify import _NTFY_BASE_URL, _gen_unique_msg_id, _load_ntfy_topic, wait_ntfy_reply  # noqa: E402


DEFAULT_STATE_PATH = Path(r"C:\tmp\codex_line_wait\active_gpt_wait.json")


def _jst_now() -> str:
    return datetime.now(tz=ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")


def _is_pid_alive(pid: int) -> bool:
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
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "corrupt", "path": str(path)}


def _write_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _read_message(args: argparse.Namespace) -> str:
    if args.message_file:
        return Path(args.message_file).read_text(encoding="utf-8")
    if args.message_env:
        return os.environ[args.message_env]
    raise SystemExit("--message-file or --message-env is required")


def _send_ntfy_with_id(message: str, title: str, priority: str, tags: str) -> tuple[str, int]:
    topic = _load_ntfy_topic()
    msg_id = _gen_unique_msg_id(topic)
    body = f"{msg_id}\n{message}"
    payload = json.dumps(
        {
            "topic": topic,
            "title": title,
            "message": body,
            "tags": [tags],
            "priority": 3 if priority == "default" else {"low": 2, "high": 4, "urgent": 5}.get(priority, 3),
            "click": "",
            "actions": [],
        }
    ).encode("utf-8")
    req = urllib.request.Request(_NTFY_BASE_URL, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Sticky", "yes")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return msg_id, int(resp.status)


def cmd_status(args: argparse.Namespace) -> int:
    state_path = Path(args.state_path)
    state = _load_state(state_path)
    if state is None:
        print(f"[codex-line] no state path={state_path}", flush=True)
        return 1
    pid = state.get("pid")
    alive = _is_pid_alive(int(pid)) if isinstance(pid, int) else False
    state["pid_alive"] = alive
    print(json.dumps(state, ensure_ascii=False, indent=2), flush=True)
    return 0


def cmd_clear_state(args: argparse.Namespace) -> int:
    state_path = Path(args.state_path)
    state_path.unlink(missing_ok=True)
    print(f"[codex-line] cleared state path={state_path}", flush=True)
    return 0


def cmd_send_wait(args: argparse.Namespace) -> int:
    state_path = Path(args.state_path)
    active = _load_state(state_path)
    if active and not args.replace:
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
    p_send.add_argument("--replace", action="store_true")
    p_send.set_defaults(func=cmd_send_wait)

    p_status = sub.add_parser("status")
    p_status.set_defaults(func=cmd_status)

    p_clear = sub.add_parser("clear-state")
    p_clear.set_defaults(func=cmd_clear_state)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

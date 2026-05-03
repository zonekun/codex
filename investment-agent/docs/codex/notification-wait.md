# Codex Notification Wait Procedure

This file is the Codex-side source of truth for LINE, ntfy, `notify.py`, and notification-based user reply waits.

## Trigger Wording

Use this procedure when user wording or task context may involve notification delivery, waiting for a reply, or running `scripts\notify.py`.

Triggers include:

- `LINE`, `Line`, `line`, `ライン`
- `双方向`, `会話`, `対話`, `返信待ち`, `待機`, `待つ`
- `通知`, `ntfy`, `notify`, `notify.py`
- `timeout`, `タイムアウト`, `12時間`, or another wait duration

Do not require the exact phrase `LINE双方向会話モード`.

## Command Form

For LINE bidirectional mode, use:

```powershell
python scripts\notify.py ntfy "<message>" --wait --timeout <seconds>
```

Do not replace this with a one-way notification unless the user explicitly disables reply waiting.

## Timeout Alignment

The command has an internal timeout in seconds. The shell tool has a separate timeout in milliseconds. Both must be aligned.

Before running the command:

1. Identify the intended wait duration in seconds.
2. Convert it to milliseconds: `timeout_ms = seconds * 1000`.
3. Set the shell command timeout to at least that value.
4. Do not use short defaults such as `timeout_ms=120000` for long waits.
5. Do not report that the requested wait is active unless both the command timeout and shell timeout are consistent.

For a 12-hour wait:

- command timeout: `--timeout 43200`
- shell timeout: `timeout_ms=43200000`

## If The Shell Cannot Guarantee The Wait

If the execution environment rejects the requested shell timeout or may have an unknown lower maximum, state the limitation before relying on the wait.

Use an explicit fallback such as:

- send the notification with a shorter stated wait
- record the checkpoint in `data/logs/codex_active_context.md`
- continue only when the user replies in chat or explicitly instructs a non-waiting workflow

Do not silently claim a 12-hour wait is running when the outer shell command stopped early.

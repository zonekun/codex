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

## Initial Bidirectional Message

When the user says that bidirectional LINE conversation mode is starting or enabled, send one initial `ntfy --wait` message immediately so the user has a message to reply to. This initial message is required even when there is no substantive question yet.

If the local `notify.py ntfy` implementation does not support a documented sender flag, identify Codex/GPT in the notification title or message body and keep the wait behavior intact.

## Reply Loop

While bidirectional LINE conversation mode is active, treat a received LINE/ntfy reply as an active conversation turn. Send a concrete reply back with `ntfy --wait` unless the user explicitly says no reply is needed or disables bidirectional mode. Do not leave a received reply unanswered on the assumption that no question was asked.

## Background Wait Handoff

If `notify.py ntfy --wait` is launched in a background process, Codex must treat the redirected stdout log as the handoff boundary for the next conversation turn.

Before launching any new background wait or sending any LINE/ntfy message:

1. Inspect the newest active Codex/GPT wait stdout log.
2. Inspect the matching stderr log if stdout is empty or the send status is unclear.
3. Check whether a Codex/GPT wait process is still active.
4. Do not send a new message until any received reply in the active log has been processed.

After launching a background wait:

1. Record the process id, stdout log path, stderr log path, and timeout seconds in the user-visible response or working notes.
2. Before answering any later chat message, sending another LINE message, or reporting that no reply has arrived, inspect the latest active wait log.
3. If the log contains `[ntfy] リプライ受信`, read the reply text immediately and treat it as the newest user instruction.
4. After processing that reply, start the next `ntfy --wait` unless the user explicitly disables bidirectional mode or says no reply is needed.
5. Do not rely on the background process ending as an automatic notification to Codex; the log must be checked explicitly.

This check is mandatory even when the user also sends a normal chat message. A chat message saying the LINE reply was missed is itself a trigger to inspect the latest wait log first.

## Duplicate Send Prevention

Never resend a LINE/ntfy message only because the redirected stdout log is still empty immediately after launch. An empty stdout log can mean the process is still waiting for a reply.

Before treating a send as failed and retrying, all of these facts must be verified:

1. The wait process has exited.
2. The stderr log has been inspected.
3. The stdout log does not contain a successful send id.
4. The exit status or stderr confirms failure.

If a Codex/GPT wait process is still active, do not start another Codex/GPT wait for a follow-up or status report. Process the active wait first, or explicitly record why it is being abandoned before launching a replacement.

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

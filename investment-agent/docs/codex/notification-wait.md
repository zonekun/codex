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

For Codex-side LINE bidirectional mode, do not call `scripts\notify.py ntfy --wait` directly. Use the Codex-only wrapper in the foreground so the shell tool returns the reply directly to the active Codex turn.

```powershell
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$waitDir = "data\logs\codex_line_wait"
New-Item -ItemType Directory -Force -Path $waitDir | Out-Null
$msg = Join-Path $waitDir "line_wait_$ts.message.txt"
Set-Content -Path $msg -Encoding UTF8 -Value "<message>"
C:\venvs\investment-agent\Scripts\python.exe -u scripts\codex_line_wait.py send-wait `
  --message-file $msg `
  --timeout <seconds> `
  --title GPT
```

Do not replace this with a one-way notification unless the user explicitly disables reply waiting.

The foreground tool call is mandatory for Codex. Background `Start-Process` waits can record replies, but they cannot wake the active Codex turn. That is the Codex-specific failure mode that caused replies to be "received" in files while remaining unhandled.

## Initial Bidirectional Message

When the user says that bidirectional LINE conversation mode is starting or enabled, send one initial `ntfy --wait` message immediately so the user has a message to reply to. This initial message is required even when there is no substantive question yet.

If the local `notify.py ntfy` implementation does not support a documented sender flag, identify Codex/GPT in the notification title or message body and keep the wait behavior intact.

## Reply Loop

While bidirectional LINE conversation mode is active, treat a received LINE/ntfy reply as an active conversation turn. Send a concrete reply back with `ntfy --wait` unless the user explicitly says no reply is needed or disables bidirectional mode. Do not leave a received reply unanswered on the assumption that no question was asked.

## Foreground Reply Handoff

Codex LINE mode must use a foreground wait. The shell output is the handoff boundary for the next conversation turn.

Before launching any new foreground wait or sending any LINE/ntfy message:

1. Run `python scripts\codex_line_wait.py status` or inspect `data\logs\codex_line_wait\active_gpt_wait.json`.
2. If status is `replied`, read `reply_text` from `status` output or `reply_text_path`, process it as the newest user instruction, then run `python scripts\codex_line_wait.py mark-processed`.
3. Do not send a new message until any received reply in the active state has been processed.

After launching a foreground wait:

1. Keep the tool call in the foreground until it returns a reply or timeout.
2. Treat the returned reply text as the newest user instruction.
3. Run `python scripts\codex_line_wait.py mark-processed` after handling that reply.
4. After marking the reply processed, start the next foreground `send-wait` unless the user explicitly disables bidirectional mode or says no reply is needed.

This check is mandatory even when the user also sends a normal chat message. A chat message saying the LINE reply was missed is itself a trigger to inspect the latest wait log first.

## Duplicate Send Prevention

Never resend a LINE/ntfy message only because a foreground wait has not returned yet. A running foreground wait can mean the process is still waiting for a reply.

Before treating a send as failed and retrying, all of these facts must be verified:

1. The wait process has exited.
2. The shell output and exit status have been inspected.
3. The output does not contain a successful send id.
4. The exit status confirms failure.

If a Codex/GPT wait process is still active, do not start another Codex/GPT wait for a follow-up or status report. Let the foreground wait return first, or explicitly record why it is being abandoned before launching a replacement.

`scripts\codex_line_wait.py send-wait` must refuse to launch when `active_gpt_wait.json` contains `status: replied` and `processed` is not `true`. Treat that refusal as a guardrail, not an error to bypass.

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

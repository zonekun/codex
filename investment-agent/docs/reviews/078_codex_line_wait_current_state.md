# 078: Codex LINE wait current-state code review

## Reviewed State

- Date: 2026-05-07 JST
- Branch: `codex/integration`
- Latest relevant commits:
  - `434883b codex: add dedicated line wait wrapper`
  - `a992712 codex: force utf8 line wait logs`
  - `5521d51 codex: block sends until line replies are processed`
- Runtime evidence:
  - `C:\tmp\codex_line_wait\active_gpt_wait.json`
  - `status: replied`
  - `processed: false`
  - `msg_id: 594`
  - `reply_text: 回答せよ`

## Findings

### P0-1. Background wait records replies but cannot wake Codex

`scripts/codex_line_wait.py` correctly writes `status: replied` and `reply_text_path` when a LINE reply arrives. However, nothing in Codex is triggered by that state transition. The Python process exits, but Codex does not receive a callback, task notification, or scheduled wake-up.

This means the implementation can receive the reply and still leave it unhandled until the user sends another normal chat message. That is exactly the observed failure pattern: the reply exists in state, but Codex has not acted on it.

Affected code:

- `scripts/codex_line_wait.py:199-222` waits and writes reply state, then exits.
- `docs/codex/notification-wait.md:70-77` still describes a manual "inspect later" workflow, not an automatic wake-up mechanism.

Expected behavior for LINE conversation mode is that a LINE reply becomes the next active conversation turn without requiring the user to complain in chat. Current Codex implementation does not satisfy that contract.

### P0-2. The design copied a Claude Code background-notification assumption that Codex does not have

The documentation says not to rely on background process completion automatically notifying Codex, but the overall loop still depends on Codex checking the state later. Claude Code has a task-notification flow; this Codex session does not.

Affected code/docs:

- `docs/codex/notification-wait.md:73-77` requires later inspection, but no process enforces that inspection.
- `scripts/codex_line_wait.py:142-160` blocks only the next send. It does not force handling of an already received reply.

The `processed=false` guard is useful as a safety rail, but it only fires when another `send-wait` is attempted. It does not make Codex answer the user.

### P1-1. State file is only one-slot and can obscure historical reply processing

`active_gpt_wait.json` stores only the current/latest wait. Reply text is written to a per-log reply file, but the active state is overwritten on the next send. If an operator bypasses the guard with `--replace` or clears state, the active view no longer shows pending context.

Affected code:

- `scripts/codex_line_wait.py:31`
- `scripts/codex_line_wait.py:178-196`
- `scripts/codex_line_wait.py:212-219`

A durable append-only event log would make review and recovery easier.

### P1-2. The wrapper imports private functions from shared `notify.py`

The Codex wrapper imports `_NTFY_BASE_URL`, `_gen_unique_msg_id`, `_load_ntfy_topic`, and `wait_ntfy_reply` from `notify.py`. That avoids modifying the shared module, but it couples Codex behavior to private internals.

Affected code:

- `scripts/codex_line_wait.py:28`

If `notify.py` changes internals for Claude Code, Codex can regress without a public contract changing.

### P2-1. Documentation still has stale wording from the old direct notify flow

The Codex doc now mandates `codex_line_wait.py`, but the background handoff section still starts with "If `notify.py ntfy --wait` is launched...". That wording is stale and can send future agents back to the wrong primitive.

Affected docs:

- `docs/codex/notification-wait.md:58-60`

## Recommended Fix Direction

Do not keep adding checks around the same background-wait model. The core model is wrong for Codex.

Use one of these designs:

1. Foreground blocking wait for LINE conversation turns in Codex, so the tool call returns the reply directly to the active assistant turn.
2. External supervisor that can actually wake/invoke Codex when `active_gpt_wait.json` transitions to `replied`.
3. Declare Codex LINE mode as screen-assisted only: replies are stored and must be processed on the next chat turn, with no claim of automatic bidirectional conversation.

Option 1 is the smallest honest fix inside the current Codex environment. Option 2 is stronger but requires an integration outside this repository. Option 3 is operationally honest but does not meet the original LINE conversation requirement.


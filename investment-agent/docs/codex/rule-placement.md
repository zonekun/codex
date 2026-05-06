# Codex Rule Placement

This file defines where Codex-side operating rules belong.

## AGENTS.md

`AGENTS.md` is a startup routing index only.

Do not put detailed workflow steps, long command recipes, dependency group lists, review procedures, cleanup procedures, notification procedures, or sync procedures in `AGENTS.md`.

Use `AGENTS.md` only to point to the correct detailed document.

## Rule Locations

- General Codex operating principles: `docs/codex/operation-knowledge.md`
- Rule placement policy: this file
- Display and table formatting: `docs/codex/display-rules.md`
- uv environment, dependency extras, and module intake: `docs/codex/uv-setup.md`
- Long-running jobs and active context: `docs/codex/long-running.md`
- LINE / ntfy / notification reply waits: `docs/codex/notification-wait.md`
- Claude Code <-> Codex message board and board rules: `docs/codex-to-claude-handoff.md`
- Claude Code Markdown sync and Codex artifact protection: `docs/claude-md-sync.md`
- Parallel work policy: `docs/codex/parallel-operation-policy.md`
- Codex code review procedure: `skills/codex-code-reviewer.md`
- Individual plans and investigations: `docs/plans/*.md`
- Review results and review requests: `docs/reviews/*.md`

## Git Protection Model

Codex-specific Markdown is Git-managed, but it must be insulated from Claude Code-side bulk reflection.

Authoritative Codex metadata branch:

```text
codex/meta
```

Daily work and Claude Code reflection branch:

```text
codex/integration
```

Rules:

- Store durable Codex operational docs under `docs/codex/**`.
- Keep `docs/codex/**` present on `codex/integration` so Codex can read it during normal work.
- Also keep `docs/codex/**` and `AGENTS.md` on `codex/meta` as a restore source.
- Claude Code-side bulk reflection targets `codex/integration`, not `codex/meta`.
- If Codex docs are overwritten or deleted on `codex/integration`, restore them from `codex/meta`:

```powershell
git checkout codex/meta -- AGENTS.md docs/codex
```

`docs/codex/**` must also be protected in `scripts/sync_claude_md.py` as `CODEX_PROTECTED`.

## Adding Rules

When adding a durable rule:

1. Choose the specific document that owns the topic.
2. Add the detailed rule there.
3. Add or update only a short pointer in `AGENTS.md` if startup routing would otherwise miss it.

If a rule spans multiple topics, keep the authoritative text in one file and link to it from the others.

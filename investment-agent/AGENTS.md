# AGENTS.md

This file is the repository-local routing index for Codex.

## Startup Instruction Discovery Rule

At the start of work in this repository, read the local instruction entry points before task-specific exploration.

1. Read this `AGENTS.md`.
2. Read `CLAUDE.md` and use its task-specific required-file index as the first routing table.
3. Read `docs/codex/operation-knowledge.md` for Codex-side common principles and topic routing.
4. Read the topic-specific Codex doc listed below when the task touches that area.
5. If the task involves a specific workflow, tool, script, pipeline, notebook, batch job, notification flow, sync flow, or review flow, read the relevant runbook pointed to by `CLAUDE.md`, `docs/codex/operation-knowledge.md`, or `docs/knowledges/INDEX.md`.

Do not skip `CLAUDE.md` or the relevant local runbook because a direct search might find matching code.

If Codex-protected docs or helper scripts are changed on `codex/integration`, update and push `codex/meta` in the same work session. Do not leave `codex/meta` stale.

## Codex Rule Placement

Keep `AGENTS.md` as a small pointer file. Do not add detailed workflow steps, command recipes, dependency lists, review procedures, cleanup procedures, notification procedures, or sync procedures here.

When a new rule is needed:

- Put general Codex operational principles in `docs/codex/operation-knowledge.md`.
- Put rule-placement policy in `docs/codex/rule-placement.md`.
- Put display behavior in `docs/codex/display-rules.md`.
- Put uv / dependency / extras rules in `docs/codex/uv-setup.md`.
- Put long-running job and monitoring rules in `docs/codex/long-running.md`.
- Put LINE / notification wait rules in `docs/codex/notification-wait.md`.
- Put Claude Code mirror/sync rules in `docs/claude-md-sync.md`.
- Put parallel work policy in `docs/codex/parallel-operation-policy.md`.
- Put bidirectional Claude Code <-> Codex messages and board rules in `docs/codex-to-claude-handoff.md`.
- Put code review procedure in `skills/codex-code-reviewer.md`.

If a task needs a new detailed procedure, create or update an appropriate file under `docs/codex/`, `docs/plans/*.md`, or a skill/runbook file, then point to it from the relevant index instead of expanding this file.

## High-Frequency Pointers

- Codex branch/path ownership: `docs/codex/operation-knowledge.md`
- Rule placement policy: `docs/codex/rule-placement.md`
- Long-running task and active context rules: `docs/codex/long-running.md`
- LINE / notification wait procedure: `docs/codex/notification-wait.md`
- Codex Python / uv environment sharing rule: `docs/codex/uv-setup.md`
- Codex display/table formatting: `docs/codex/display-rules.md`
- Claude Code <-> Codex handoff board: `docs/codex-to-claude-handoff.md`
- Claude Code Markdown sync and Codex artifact protection: `docs/claude-md-sync.md`
- Codex code review runbook: `skills/codex-code-reviewer.md`

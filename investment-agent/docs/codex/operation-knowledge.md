# Codex Operation Knowledge

作成日: 2026-04-22
対象プロジェクト: `investment-agent`

This file is the Codex-side operational index and common principles document.
Detailed procedures live in topic-specific files under `docs/codex/`.

## Purpose

Codex assists Claude Code in this workspace.

The operational source of truth remains the Claude Code side unless a task is explicitly delegated to Codex. Codex should investigate, implement, verify, and hand off work in a form Claude Code can intake.

## Common Principles

- Treat this repository as a collection of investment data collection, analysis, monitoring, and operation scripts rather than a single small app.
- Use `CLAUDE.md` as the primary task-specific routing table.
- Do not ignore local runbooks when they exist.
- Keep Codex-specific rules outside `CLAUDE.md` and `docs/knowledges/` unless the user explicitly asks to change Claude Code-side operation.
- For code implementation, use the Codex-side working tree by default:
  - repository: `C:\Users\zonekun\Documents\codex\investment-agent`
  - branch: `codex/integration`
- Do not directly edit the Claude Code-side working tree `C:\gdrive\claude\investment-agent` unless the user explicitly asks for direct Claude-side edits.
- Prefer `C:\gdrive\claude\investment-agent` over Japanese Google Drive paths when referring to Claude Code-side files.
- Use UTF-8 assumptions for Python and file IO: set `PYTHONUTF8=1` for script execution and use `encoding="utf-8"` in code.
- Show and judge times in JST.
- If a local text file appears mojibake in PowerShell, re-read with explicit encoding such as `-Encoding UTF8` or `-Encoding Default`.
- If Codex-protected docs or helper scripts are changed on `codex/integration`, update and push `codex/meta` in the same work session. Do not leave `codex/meta` stale.
- When operating on multiple Git worktrees or branches, every Git command that should affect a secondary worktree must use `git -C <absolute-worktree-path> ...`. Do not rely on the shell's current directory or mix main-worktree and secondary-worktree Git commands in one chained command.

## Required Topic Pointers

- Rule placement and how to keep `AGENTS.md` thin: `docs/codex/rule-placement.md`
- Codex display rules: `docs/codex/display-rules.md`
- Codex Python / uv environment sharing rule: `docs/codex/uv-setup.md`
- Codex Python code quality practices: `docs/codex/python-practices.md`
- Long-running jobs, active context, monitoring, and execution-change rules: `docs/codex/long-running.md`
- LINE / ntfy / notification reply waits: `docs/codex/notification-wait.md`
- Claude Code <-> Codex message board and board rules: `C:\gdrive\claude\investment-agent\docs\codex-to-claude-handoff.md`
- Claude Markdown sync and Codex artifact protection: `docs/claude-md-sync.md`
- Parallel work policy: `docs/codex/parallel-operation-policy.md`
- Segment structure earnings analysis procedure: `docs/codex/segment-structure-codex-procedure.md`
- Codex code review runbook: `skills/codex-code-reviewer.md`

## Startup And Workflow Routing

At startup, use:

1. `AGENTS.md`
2. `CLAUDE.md`
3. this file
4. the topic-specific document listed above
5. `docs/knowledges/INDEX.md` or the runbook referenced by `CLAUDE.md` for workflow-specific details

If the user says a notebook/book has already been run, first look in `CLAUDE.md` and referenced knowledge docs for where outputs are saved.

## Main Operational Boundaries

- Codex handles investigation, implementation, verification, documentation, and handoff.
- Claude Code-side production operation decisions remain Claude Code-owned unless explicitly delegated.
- `docs/knowledges/` is treated as Claude Code -> Codex synchronized knowledge. Codex should not directly update it for Codex-only behavior.
- If `docs/knowledges/` needs a change, write a handoff note for Claude Code or ask the user how to reflect it.
- Any operational rule change should be documented in the appropriate Codex doc and, when relevant, announced through `C:\gdrive\claude\investment-agent\docs\codex-to-claude-handoff.md`.

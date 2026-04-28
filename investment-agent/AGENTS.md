# AGENTS.md

This file defines repository-local working rules for Codex.

## Startup Instruction Discovery Rule

At the start of work in this repository, check the cloned Claude/Codex instruction files before task-specific exploration.

1. Read this `AGENTS.md`.
2. Read `CLAUDE.md` and use its task-specific required-file index as the first routing table.
3. For Codex-side operational differences, check `docs/codex-operation-knowledge.md` when relevant.
4. For Claude/Codex sync questions, check `docs/claude-md-sync.md` and `scripts/sync_claude_md.py` before using ad-hoc file searches.
5. If the user says a notebook/book has already been run, first look in `CLAUDE.md` and the referenced knowledge docs for where outputs are saved.

Do not skip `CLAUDE.md` just because a direct `rg` search might find matching scripts.

## Codex Branch Ownership Rule

When the user asks Codex to implement code for this project, use the Codex-side
Git working tree and branch by default:

- Repository: `C:\Users\zonekun\Documents\codex\investment-agent`
- Branch: `codex/integration`

Do not directly edit the Claude Code-side working tree
`C:\gdrive\claude\investment-agent` or `G:\マイドライブ\claude\investment-agent`
for code implementation unless the user explicitly asks for direct Claude-side
edits. Claude Code-side docs, plans, BigQuery table changes, and production
operation decisions are handled by Claude Code unless explicitly delegated to
Codex.

When writing `docs/codex-to-claude-handoff.md`, state the repository path,
branch, and whether the Claude Code-side files were directly edited.

## Codex Display Rule

For Codex-specific display rules, use `docs/codex-display-rules.md`. Keep these
rules out of `CLAUDE.md` and `docs/knowledges/` unless the user explicitly asks
to change Claude Code-side operation.

When showing tabular lists in Codex, default to fixed-width fenced code blocks
instead of Markdown tables. Markdown tables are not the default in Codex because
they often become hard to read with Japanese text, long names, or mixed-width
columns.

## Long-running Task Rule

When handling long-running jobs, monitoring, batch processing, or data processing, always follow these rules.

1. At the start of work, create or update `data/logs/codex_active_context.md`.
2. Record the monitoring targets, commands, log paths, success conditions, and next check items.
3. Before switching to another task, update the same file.
4. Do not treat chat history as the source of truth for work state; use this file instead.
5. When resuming monitoring, read this file first.

## Execution Change Rule

When there is an existing tool, script, or documented procedure for a task, and you decide not to use it, explain that before proceeding.

1. State why the existing method is not being used.
2. State what alternative method will be used instead.
3. State what the alternative will change, create, delete, or leave behind.
4. If the switch affects later operations, manifests, baselines, or follow-up workflow, say so explicitly.
5. Do not silently replace an expected workflow with an ad-hoc one.

## Notes

- This rule applies even within the same session.
- If the active context is no longer relevant, explicitly mark it as closed in `data/logs/codex_active_context.md`.
- Prefer short, factual updates. Keep the file current rather than exhaustive.
- When reading Japanese or other non-ASCII text files from PowerShell, do not trust the default encoding if the output is mojibake. Re-read the file with an explicit likely encoding, such as `-Encoding UTF8` or `-Encoding Default` / CP932 depending on the file, and state which encoding was used.
- Do not confuse handoff files: `docs/handoff.md` is the Claude Code side terminal-to-terminal handoff board. For messages from Codex to Claude Code, use `docs/codex-to-claude-handoff.md`.
- Treat the following user phrases as aliases for `docs/codex-to-claude-handoff.md`: `クロード伝言メモ`, `Claude伝言メモ`, `claude伝言メモ`, `クロードへの伝言`, `Claude Code伝言`, `Claude Code handoff`, `CodexからClaudeへの伝言`, `codex-to-claude handoff`, and similar wording that means a memo from Codex to Claude Code.

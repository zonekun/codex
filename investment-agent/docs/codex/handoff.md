# Codex Handoff

This file defines how Codex uses the Claude Code <-> Codex message board.

## Message Board

Use:

```text
docs/codex-to-claude-handoff.md
```

Despite the filename, this is a bidirectional message board between Claude Code and Codex.

Do not use `docs/handoff.md` for Codex messages. `docs/handoff.md` is the Claude Code side terminal-to-terminal handoff board.

## Direction

Use `from` and `to` to distinguish direction:

- `Codex -> Claude Code`: implementation results, intake requests, review requests, validation results, notes
- `Claude Code -> Codex`: task delegation, review results, work instructions, prerequisites, handoff

Each entry must include at least:

- `from`
- `to`
- `status`
- `task`

For implementation or review results, also include repository path, branch, relevant paths, and whether opposite-side files were directly edited.

## Status Updates

When Codex handles an entry with `to: Codex`, update that entry to `in_progress` or `done` when acting on it.

When sending a result to Claude Code, leave it as `pending` unless Claude Code has already acknowledged or completed intake.

## User Phrase Aliases

Treat these user phrases as references to `docs/codex-to-claude-handoff.md`:

- `クロード伝言メモ`
- `Claude伝言メモ`
- `claude伝言メモ`
- `クロードへの伝言`
- `クロードコードからの伝言`
- `Claude Code伝言`
- `Claude Code handoff`
- `CodexからClaudeへの伝言`
- `ClaudeからCodexへの伝言`
- `Codexへの伝言`
- `双方向伝言板`
- `codex-to-claude handoff`

Also treat similar wording as a handoff-board request when the meaning is clear.

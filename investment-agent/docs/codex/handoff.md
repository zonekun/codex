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

## Claude Intake For Code Changes

When Codex hands off committed code changes to Claude Code, prefer Git intake over patch-based manual application.

Codex result notes must include:

- repository root used by Codex, usually `C:\Users\zonekun\Documents\codex`
- branch, usually `codex/integration`
- commit hash
- changed path as stored in Git, for example `investment-agent/scripts/foo.py`
- Claude-side target path when useful, for example `C:\gdrive\claude\investment-agent\scripts\foo.py`

Preferred Claude Code intake:

```powershell
git fetch origin codex/integration
git cherry-pick <commit>
```

Do not use ordinary patch application as the default handoff path. Codex's Git repository root is one directory above `investment-agent`, while Claude Code may operate inside `C:\gdrive\claude\investment-agent`; this path-prefix difference can make patches skip or apply with context drift.

If Git cherry-pick is impossible and a patch is explicitly requested, Codex should generate a Claude-side patch with the `investment-agent/` prefix stripped:

```powershell
git show --format= --relative=investment-agent <commit> -- investment-agent/<path> > C:\tmp\<commit>_claude.patch
```

Claude Code should then apply it from `C:\gdrive\claude\investment-agent` with `git apply --3way`. File-copy or manual edit intake is a fallback only when Git and `--3way` patch intake are both unavailable.

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

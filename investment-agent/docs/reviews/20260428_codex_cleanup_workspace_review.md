# Codex workspace cleanup review request

Date: 2026-04-28
Target: `C:\Users\zonekun\Documents\codex\cleanup_codex_workspace.py`

## Incident

`gcp` MCP failed to start with:

```text
MCP client for `gcp` failed to start: MCP startup failed: 指定されたパスが見つかりません。 (os error 3)
```

The configured interpreter was:

```text
C:\Users\zonekun\Documents\codex\investment-agent\.venv-codex\Scripts\python.exe
```

`.venv-codex` had existed during the Codex migration and later disappeared.
`cleanup_codex_workspace.py` listed `investment-agent/.venv-codex` as a deletion
candidate, so the cleanup tool could remove the runtime used by MCP and local
Codex workflows.

## Review focus

Check whether the cleanup tool can delete anything that should be preserved.
Specifically review all candidates passed to `remove_path()` and verify their
expected lifecycle.

## Defect fixed before review

The following paths were removed from normal cleanup candidates:

- `investment-agent/.venv-codex`
- `investment-agent/.venv`
- `investment-agent/venv`
- `investment-agent/env`
- `investment-agent/data/cache` (later confirmed disposable)
- `investment-agent/data/csv/uki_predictor` (later confirmed disposable)

An explicit protected-path guard was added. The script now refuses to delete:

- `.env`
- `.claude`
- `.mcp.json`
- `.venv-codex`
- `.venv`
- `venv`
- `env`
- `keys/`
- `data/logs`
- `data/master`

Additional hardening after review:

- `tmp_*` / `tmp-*` project-root candidates must be untracked and ignored by Git.
- Recursive deletion preflights the tree and refuses symlinks, protected paths,
  and paths resolving outside the workspace.

## Reviewer checklist

- Confirm the remaining default cleanup candidates are truly disposable.
- Confirm ignored but operationally important paths are protected, not merely absent from the candidate list.
- Confirm future additions to cleanup candidates pass through `is_protected_path()`.
- Confirm `tmp_*` / `tmp-*` project-root deletion is acceptable for this workspace.
- Confirm `--execute` cannot delete runtime paths even if a candidate is accidentally added later.

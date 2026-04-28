# Codex Workspace Cleanup

This is the Codex-side cleanup entry point for `C:\Users\zonekun\Documents\codex`.
It is intentionally stored at the Codex workspace root, outside the
`investment-agent` Claude Code project clone, so Claude-side project syncs do not
overwrite it.

## Usage

```powershell
# dry-run
python .\cleanup_codex_workspace.py

# keep workspace under 1GB
python .\cleanup_codex_workspace.py --target-gb 1.0 --execute
```

## Cleanup Order

The script deletes only ignored, locally generated paths first:

- `investment-agent/.uv-cache`
- virtualenv/cache/build output directories
- `tmp_*` and `tmp-*` project-root items
- `__pycache__`

The script must not delete runtime, credential, MCP, or project state paths.
Protected paths include `.claude`, `.env`, `.mcp.json`, `keys/`, `.venv-codex`,
`.venv`, `venv`, `env`, `data/logs`, and `data/master`. `data/cache` and
`data/csv/uki_predictor` are disposable local outputs, but they are not part of
the default cleanup set unless explicitly added as candidates.
Project-root `tmp_*` / `tmp-*` candidates must be both ignored and untracked by
Git before they can be deleted. Recursive deletion refuses symlink or outside
workspace paths.

The original Claude Code cleanup tool remains at
`investment-agent/scripts/cleanup_disk.py` and should be left Claude-owned.

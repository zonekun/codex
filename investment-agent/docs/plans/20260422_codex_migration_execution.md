# Codex Migration Execution Log

実施日: 2026-04-22  
対象プロジェクト: `investment-agent`

## 目的
`docs/codex-migration-estimate.md` のチェックリストを Codex 側で順に消化し、実施結果と残タスクを記録する。

## 実施結果

### 1. ワークスペース確認
- 確認済み
- `C:\Users\zonekun\Documents\codex\investment-agent` に主要ディレクトリ一式が存在
- `.claude/`, `scripts/`, `src/`, `tests/`, `docs/`, `keys/`, `.env`, `.mcp.json` を確認

### 2. Claude 固有設定の切り分け
- 確認済み
- `.claude/settings.local.json`
- `.claude/scheduled_tasks.lock`
- `settings.local.json` は Claude 固有の permissions / hooks / mcpServers を含む
- `scheduled_tasks.lock` は Claude 側のローカル運用ファイルであり、Codex 常用設定には含めない

### 3. Codex 向け運用ドキュメント確認
- 確認済み
- `docs/codex-operation-knowledge.md`
- `docs/codex-parallel-operation-policy.md`
- `docs/claude-code-intake-checklist.md`
- `docs/git-bootstrap-notes.md`

### 4. MCP 移行
- 実施済み
- `.mcp.json` の `gcp` server スクリプトパスを Codex ワークスペース側へ修正
  - `C:\Users\zonekun\Documents\codex\investment-agent\scripts\gcp_mcp_server.py`
- `.mcp.json` の `gcp` server Python を Codex 専用 venv へ修正
  - `C:\Users\zonekun\Documents\codex\investment-agent\.venv-codex\Scripts\python.exe`
- `.mcp.json` は JSON として正常

### 5. Python 環境の分離
- 実施済み
- 既存 `C:\venvs\investment-agent` は触らない方針を維持
- Codex 専用の `.venv-codex` / `.uv-cache` / `.uv-python` をプロジェクト直下に分離
- `scripts/setup_codex_uv.ps1` を追加・更新
- `docs/codex-uv-setup.md` を追加
- `scripts/smoke_codex_migration.ps1` を追加

### 6. uv 利用確認
- 実施済み
- `setup_codex_uv.ps1` により `.venv-codex` の再構築を確認
- `UV_PROJECT_ENVIRONMENT`, `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, `PYTHONUTF8` を Codex 用に固定して実行
- `Python 3.12.10` で起動確認

### 7. 主要コマンドの smoke
- 実施済み
- `scripts/smoke_codex_migration.ps1` を実行
- 以下 5 スクリプトで `--help` を確認
  - `scripts/download_monthly.py`
  - `scripts/tdnet_load_parallel.py`
  - `scripts/update_monthly_adapters.py`
  - `scripts/extract_monthly_data.py`
  - `scripts/monitor_backfill.py`
- `pytest.exe` は未導入のため pytest smoke はスキップ

### 8. Git 運用確認
- 実施済み
- Git ルートは `C:\Users\zonekun\Documents\codex`
- 現在ブランチは `codex/integration`
- `origin` は `https://github.com/zonekun/codex.git`
- 初回 commit / push 済み
  - commit: `a2d87f1 Initial codex workspace import`
  - upstream: `origin/codex/integration`
- `scripts/switch_codex_branch.ps1` は `codex/integration` 上で正常終了
- 未初回コミット状態の `codex/integration` でも失敗しないよう、`switch_codex_branch.ps1` と `bootstrap_codex_workspace.ps1` の branch 確認を修正
- 秘密情報系は `.gitignore` で除外確認
  - `.env`
  - `keys/`
  - `.venv-codex/`
  - `.uv-cache/`
  - `.uv-python/`
  - `.claude/settings.local.json`
  - `.claude/scheduled_tasks.lock`
- `git add --dry-run investment-agent` は Codex sandbox では `.git/index.lock` 権限拒否だったため、通常 PowerShell 側で実施

## 現在の結論
Codex 移行で必要だった以下は完了。

- Codex 用ワークスペースの確認
- Claude 固有設定の切り分け
- Codex 用運用ドキュメントの整理
- `.mcp.json` の Codex 側固定パス修正
- Codex 専用 uv / venv 分離
- Python 3.12.10 での起動確認
- 主要 5 スクリプトの smoke
- Git `origin` / `codex/integration` 確認
- 初回 commit / push 完了
- branch 切替補助スクリプトの unborn branch 対応

現時点で Codex 側の通常作業は継続可能。

## 残タスク
1. Claude Code 側で Git 取り込みを開始する場合は、`origin` と `codex/integration` を基準に pull / checkout 手順を合わせる
2. `pytest` は今回対象外。必要になった時点で `uv sync --extra dev` を実施する

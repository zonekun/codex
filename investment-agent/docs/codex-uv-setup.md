# Codex 専用 uv 環境

作成日: 2026-04-22  
対象プロジェクト: `investment-agent`

## 目的

Claude Code 側で使っている `C:\venvs\investment-agent` を壊さず、Codex 専用の uv 実行環境を分離する。

## 方針

- Claude Code 用 venv は触らない
- Codex 用 venv はワークスペース直下に置く
- uv cache / managed Python 置き場も Codex 用に分離する

## 配置

- venv: `C:\Users\zonekun\Documents\codex\investment-agent\.venv-codex`
- uv cache: `C:\Users\zonekun\Documents\codex\investment-agent\.uv-cache`
- uv managed python: `C:\Users\zonekun\Documents\codex\investment-agent\.uv-python`

## セットアップ

```powershell
cd C:\Users\zonekun\Documents\codex\investment-agent
powershell -ExecutionPolicy Bypass -File .\scripts\setup_codex_uv.ps1
```

このスクリプトは次を行う。

- Python 3.12 実体を候補パスから探索
- Codex 専用 `.venv-codex` を作成
- `uv sync` で依存同期
- `python.exe --version` で疎通確認

## セッション時の前提

Codex で `uv` を使うときは、少なくとも次を同一セッションに設定する。

```powershell
$env:UV_PROJECT_ENVIRONMENT='C:\Users\zonekun\Documents\codex\investment-agent\.venv-codex'
$env:UV_CACHE_DIR='C:\Users\zonekun\Documents\codex\investment-agent\.uv-cache'
$env:UV_PYTHON_INSTALL_DIR='C:\Users\zonekun\Documents\codex\investment-agent\.uv-python'
$env:PYTHONUTF8='1'
```

## smoke 確認

専用 uv が作成できたら、移行 smoke は次で一括実行する。

```powershell
cd C:\Users\zonekun\Documents\codex\investment-agent
powershell -ExecutionPolicy Bypass -File .\scripts\smoke_codex_migration.ps1
```

このスクリプトは次を確認する。

- `.venv-codex\Scripts\python.exe --version`
- 主要 5 スクリプトの `--help`
- `pytest.exe` が存在する場合は `pytest -x --tb=short tests`

## 補足

- `.venv-codex/`, `.uv-cache/`, `.uv-python/` は `.gitignore` で除外する
- Claude Code 用の `setup_machine.ps1` は Codex では使わない

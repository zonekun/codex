# Codex uv / Python Environment

作成日: 2026-04-22
更新日: 2026-05-16
対象プロジェクト: `investment-agent`

## 目的

Codex 側で Python / uv 環境を重複保持しない。ディスク使用量を増やさないため、Codex は Claude Code 側で管理されている uv / venv を共有して使う。

## 方針

- Codex 専用 venv、uv cache、uv managed Python は作成しない。
- Codex 側に `.venv-codex/`、`.uv-cache/`、`.uv-python/` を新設しない。
- 既存の Codex 専用環境が残っていても、新規セットアップや復旧手順では使わない。
- Python 実行は Claude Code 側の既存環境を使う。
- 依存関係の追加・更新は Claude Code 側の `pyproject.toml` / `uv.lock` / venv 管理に従う。Codex 側だけの依存定義を増やさない。

## 既定 Python

Codex で Python を実行するときは、次の順で既存 interpreter を使う。

1. `C:\venvs\investment-agent\Scripts\python.exe`
2. 存在しない場合だけ、作業を止めてユーザーへ確認する。

`C:\venvs\investment-agent` は Claude Code 側で使う共有 venv の既定パスであり、Codex が独自に作り直したり削除したりしない。

## セッション時の前提

PowerShell で Python を実行するときは UTF-8 を有効にし、直接 interpreter パスを指定する。

```powershell
$env:PYTHONUTF8='1'
& 'C:\venvs\investment-agent\Scripts\python.exe' scripts\some_task.py
```

`C:\venvs\investment-agent` が存在しない場合も、Claude Code 側ワークツリー内の旧 `.venv` へ戻さず、作業を止めてユーザーへ確認する。

Codex セッションで次の変数を Codex 側ローカルパスへ向けて設定しない。

```powershell
$env:UV_PROJECT_ENVIRONMENT='C:\Users\zonekun\Documents\codex\investment-agent\.venv-codex'
$env:UV_CACHE_DIR='C:\Users\zonekun\Documents\codex\investment-agent\.uv-cache'
$env:UV_PYTHON_INSTALL_DIR='C:\Users\zonekun\Documents\codex\investment-agent\.uv-python'
```

## uv を使う場合

Codex が `uv` を使う必要がある場合も、Codex 専用環境を作らず Claude Code 側の既存 venv を参照する。

```powershell
$env:PYTHONUTF8='1'
$env:UV_PROJECT_ENVIRONMENT='C:\venvs\investment-agent'
uv run python --version
```

`C:\venvs\investment-agent` が存在しない場合は、旧 `C:\gdrive\claude\investment-agent\.venv` へ fallback せず、作業を止めてユーザーへ確認する。

Codex は `uv sync`、`uv lock`、`uv python install` など依存関係や interpreter を変更するコマンドを安易に実行しない。必要な場合は、対象が Claude Code 側の共有環境であること、変更理由、影響範囲を確認してから実行する。

## 依存変更

Codex 側だけの `requirements-codex-min.txt` や Codex 専用 optional extras を新設しない。新しい Python 依存が必要な場合は、次のどちらかで扱う。

- 一時的な調査なら、既存共有 venv で足りる範囲に作業を調整する。
- 永続的に必要なら、Claude Code 側の依存管理へ反映するための handoff を作るか、ユーザーの明示指示を受けて共有依存として更新する。

## smoke 確認

Python 実行前の軽い確認は、共有 venv の存在と interpreter の疎通だけでよい。

```powershell
$env:PYTHONUTF8='1'
& 'C:\venvs\investment-agent\Scripts\python.exe' --version
```

旧 `C:\gdrive\claude\investment-agent\.venv\Scripts\python.exe` は smoke 確認の fallback に使わない。

## 廃止

次の Codex 専用環境運用は廃止する。

- `.venv-codex/`
- `.uv-cache/`
- `.uv-python/`
- `requirements-codex-min.txt` を正本にする運用
- `scripts/setup_codex_uv.ps1`
- `scripts/setup_codex_uv_min.ps1`

これらは新規作成・復旧・保護対象にしない。既存ディレクトリの削除は、実体パスを確認し、ユーザーの意図が削除である場合だけ行う。

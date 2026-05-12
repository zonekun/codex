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
- `uv sync --extra codex-light` で日常作業用の軽量依存を同期
- `python.exe --version` で疎通確認

## セッション時の前提

Codex で `uv` を使うときは、少なくとも次を同一セッションに設定する。

```powershell
$env:UV_PROJECT_ENVIRONMENT='C:\Users\zonekun\Documents\codex\investment-agent\.venv-codex'
$env:UV_CACHE_DIR='C:\Users\zonekun\Documents\codex\investment-agent\.uv-cache'
$env:UV_PYTHON_INSTALL_DIR='C:\Users\zonekun\Documents\codex\investment-agent\.uv-python'
$env:PYTHONUTF8='1'
```

## optional dependency groups

`pyproject.toml` は Codex の容量肥大化を避けるため、重い依存を optional extras に分離している。
作業前に必要な group だけを `uv sync` で入れる。

```powershell
# 日常作業
uv sync --extra codex-light

# ブラウザ操作
uv sync --extra codex-light --extra browser

# PDF抽出
uv sync --extra codex-light --extra pdf

# GCP heavy / Vertex AI / pyarrow
uv sync --extra codex-light --extra gcp-heavy

# 分析
uv sync --extra codex-light --extra analysis

# numba / shap を使う重い分析
uv sync --extra codex-light --extra analysis --extra analysis-accelerated
```

重い作業が終わったら、可能な範囲で軽量状態へ戻す。

```powershell
uv sync --extra codex-light
```

`--all-extras` は通常使わない。全機能検証など、全依存が必要な場合だけ明示的に使う。

## Claude Code 側から依存変更を取り込む時

Claude Code 側で新規モジュールが追加され、`pyproject.toml` / `uv.lock` を Codex 側へ取り込む時は、
その依存をそのまま `codex-light` へ入れない。まず用途に応じて optional dependency group へ分類する。

分類の目安:

- 軽い日常作業・テスト・BQ/GCS確認: `codex-light`
- ブラウザ操作: `browser`
- 通常分析: `analysis`
- `numba` / `shap` など重い高速分析: `analysis-accelerated`
- PDF/表抽出: `pdf`
- Vertex AI / pyarrow / db-dtypes / Cloud Run など重いGCP: `gcp-heavy`
- Anthropic / OpenAI / Gemini / MCP / LangChain系: `llm`
- yfinance / J-Quants / YouTube / Twitter / arxiv / ddgs / Excel系: `data-sources`
- FastAPI / Streamlit / Plotly / scheduler: `dashboard-api`

取り込み後の基本手順:

```powershell
cd C:\Users\zonekun\Documents\codex\investment-agent
$env:UV_PROJECT_ENVIRONMENT='.venv-codex'
uv lock
uv sync --extra codex-light
```

作業に必要な group がある場合だけ追加する。

```powershell
uv sync --extra codex-light --extra pdf
uv sync --extra codex-light --extra browser
uv sync --extra codex-light --extra analysis --extra analysis-accelerated
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
- `.venv-codex/` は cleanup 対象外。通常の容量削減では削除しない

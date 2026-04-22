# コマンド集

## Git Bash で実行

```bash
# プロジェクトに移動
cd /c/gdrive/claude/investment-agent

# パッケージ管理
uv add <package>
uv sync

# スクリプト実行（PYTHONUTF8=1 必須）
PYTHONUTF8=1 uv run python scripts/<script>.py

# テスト
uv run pytest -x --tb=short
uv run pytest tests/test_idea/ -v

# リンター・型チェック
uv run ruff check src/
uv run ruff format src/
uv run mypy src/

# 開発サーバー
uv run python -m src.core.agent
uv run streamlit run dashboard/app.py

# アイディア手動投入
uv run python -m src.idea.sources.manual --input "逆日歩狙いの買方投資"

# GCS同期
bash scripts/sync_push.sh   # 送り出し（.env / keys/ / data/logs/）
bash scripts/sync_pull.sh   # 受け取り
```

## PowerShell で実行

```powershell
# Claude Code インストール/更新
irm https://claude.ai/install.ps1 | iex
claude update

# Claude Code 起動
cd C:\gdrive\claude\investment-agent
claude

# 環境変数永続化
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-xxxxx", [EnvironmentVariableTarget]::User)
```

## 新端末セットアップ（1回のみ）

```powershell
cd C:\gdrive\claude\investment-agent
.\setup_machine.ps1
```

`UV_PROJECT_ENVIRONMENT=C:\venvs\investment-agent` を永続化 → venv作成 → `uv sync`。
実行後ターミナルを再起動すること。

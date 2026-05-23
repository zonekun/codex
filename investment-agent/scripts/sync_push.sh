#!/bin/bash
# sync_push.sh
# 非gitファイルを GCS にプッシュする（Linux/Windows 共通）
# 使い方: bash scripts/sync_push.sh
#
# GCS 同期対象（gitで管理できないもののみ）:
#   .env                       APIキー類
#   keys/gcp-service-account.json  GCP認証キー
#   (data/logs/ は廃止 → ローカル C:\tmp\claude_logs\ に移行)
#   claude-memory/             Claude Codeのメモリファイル（~/.claude/projects/.../memory/）
#
# 【重要】git管理済みのファイル（scripts/, src/, CLAUDE.md 等）は絶対にGCSに上げない。
# 【重要】gsutil rsync でプロジェクト全体を GCS にコピーしてはいけない。

set -euo pipefail

BUCKET="gs://stock_data_1930932/config/investment-agent"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== sync push: local → GCS ==="
echo "From: $PROJECT_ROOT"
echo "To:   $BUCKET"
echo ""

# .env
if [ -f "$PROJECT_ROOT/.env" ]; then
    gcloud storage cp "$PROJECT_ROOT/.env" "$BUCKET/.env" && echo "[OK] .env" || echo "[NG] .env"
else
    echo "[SKIP] .env not found"
fi

# keys/gcp-service-account.json
if [ -f "$PROJECT_ROOT/keys/gcp-service-account.json" ]; then
    gcloud storage cp "$PROJECT_ROOT/keys/gcp-service-account.json" \
        "$BUCKET/keys/gcp-service-account.json" \
        && echo "[OK] keys/gcp-service-account.json" \
        || echo "[NG] keys/gcp-service-account.json"
else
    echo "[SKIP] keys/gcp-service-account.json not found"
fi

# data/logs/ → ローカル保管に移行（C:\tmp\claude_logs\）。GCS同期不要

# claude-memory/ (Claude Code メモリ: ~/.claude/projects/.../memory/)
# Windows と Linux VM で autoMemoryDirectory に合わせた固定パスを使う。
# find ベースは不使用（別プロジェクトの memory/ を拾うリスクがある）。
# 除外ファイル: line_conversation_mode.md（端末固有のLINE会話状態。他端末に同期不要）
MEMORY_DIR=""
if [ -d "/c/Users/zonekun/.claude/projects/G---------claude/memory" ]; then
    # Windows (Git Bash)
    MEMORY_DIR="/c/Users/zonekun/.claude/projects/G---------claude/memory"
else
    # Linux VM: ~/.claude/settings.json の autoMemoryDirectory と同じパスを使う
    MEMORY_DIR="$HOME/.claude/projects/G---------claude/memory"
fi

if [ -n "$MEMORY_DIR" ] && [ -d "$MEMORY_DIR" ]; then
    gcloud storage rsync -r \
        --exclude="line_conversation_mode\.md" \
        "$MEMORY_DIR/" "$BUCKET/claude-memory/" \
        && echo "[OK] claude-memory/" \
        || echo "[NG] claude-memory/"
else
    echo "[SKIP] claude-memory/ not found: $MEMORY_DIR"
fi

echo ""
echo "=== push complete ==="
echo ""
echo "受取側で以下を実行:"
echo "  git pull origin master"
echo "  bash scripts/sync_pull.sh"

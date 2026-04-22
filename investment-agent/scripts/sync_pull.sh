#!/bin/bash
# sync_pull.sh
# GCS から非gitファイルをプルする（Linux/Windows 共通）
# 使い方: bash scripts/sync_pull.sh
#
# 取得対象:
#   .env                           APIキー類
#   keys/gcp-service-account.json  GCP認証キー
#   (data/logs/ は廃止 → ローカル C:\tmp\claude_logs\ に移行)
#   claude-memory/                 Claude Codeのメモリファイル

set -euo pipefail

BUCKET="gs://stock_data_1930932/config/investment-agent"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== sync pull: GCS → local ==="
echo "From: $BUCKET"
echo "To:   $PROJECT_ROOT"
echo ""

# .env
gcloud storage cp "$BUCKET/.env" "$PROJECT_ROOT/.env" \
    && echo "[OK] .env" \
    || echo "[SKIP] .env not found in GCS"

# keys/gcp-service-account.json
mkdir -p "$PROJECT_ROOT/keys"
gcloud storage cp "$BUCKET/keys/gcp-service-account.json" \
    "$PROJECT_ROOT/keys/gcp-service-account.json" \
    && echo "[OK] keys/gcp-service-account.json" \
    || echo "[SKIP] keys/gcp-service-account.json not found in GCS"

# data/logs/ → ローカル保管に移行（C:\tmp\claude_logs\）。GCS同期不要

# claude-memory/ (Claude Code メモリ: ~/.claude/projects/.../memory/)
MEMORY_DIR=""
if [ -d "/c/Users/zonekun/.claude/projects/G---------claude/memory" ]; then
    MEMORY_DIR="/c/Users/zonekun/.claude/projects/G---------claude/memory"
elif [ -d "$HOME/.claude/projects" ]; then
    MEMORY_DIR=$(find "$HOME/.claude/projects" -type d -name "memory" 2>/dev/null | head -1)
fi

if [ -n "$MEMORY_DIR" ]; then
    mkdir -p "$MEMORY_DIR"
    gcloud storage rsync -r "$BUCKET/claude-memory/" "$MEMORY_DIR/" \
        && echo "[OK] claude-memory/" \
        || echo "[SKIP] claude-memory/ not found in GCS"
else
    echo "[SKIP] claude-memory/ destination not found"
fi

# uv sync (git pull で pyproject.toml が更新された場合に依存を同期)
if command -v uv &>/dev/null; then
    echo ""
    echo "--- uv sync ---"
    (cd "$PROJECT_ROOT" && uv sync) \
        && echo "[OK] uv sync" \
        || echo "[WARN] uv sync failed"
fi

echo ""
echo "=== pull complete ==="

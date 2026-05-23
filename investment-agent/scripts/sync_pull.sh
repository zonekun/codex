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
# Windows と Linux VM で autoMemoryDirectory に合わせた固定パスを使う。
# 除外ファイル: line_conversation_mode.md（端末固有のLINE会話状態。上書きしない）
MEMORY_DIR=""
if [ -d "/c/Users/zonekun/.claude/projects/G---------claude/memory" ] || \
   [ "$(uname -o 2>/dev/null)" = "Msys" ] || [ -n "$WINDIR" ]; then
    # Windows (Git Bash)
    MEMORY_DIR="/c/Users/zonekun/.claude/projects/G---------claude/memory"
else
    # Linux VM: ~/.claude/settings.json の autoMemoryDirectory と同じパスを使う
    MEMORY_DIR="$HOME/.claude/projects/G---------claude/memory"
fi

mkdir -p "$MEMORY_DIR"
gcloud storage rsync -r \
    --exclude="line_conversation_mode\.md" \
    "$BUCKET/claude-memory/" "$MEMORY_DIR/" \
    && echo "[OK] claude-memory/" \
    || echo "[SKIP] claude-memory/ not found in GCS"

# Linux VM のみ: ~/.claude/settings.json に autoMemoryDirectory を自動設定
# これがないと Claude Code が別パス（プロジェクトハッシュ由来）に書き続けて同期が無意味になる
if [ "$(uname -s)" = "Linux" ]; then
    SETTINGS="$HOME/.claude/settings.json"
    if command -v jq &>/dev/null; then
        if [ -f "$SETTINGS" ]; then
            TMP=$(mktemp)
            jq --arg d "$MEMORY_DIR" '.autoMemoryDirectory = $d' "$SETTINGS" > "$TMP" \
                && mv "$TMP" "$SETTINGS" \
                && echo "[OK] settings.json autoMemoryDirectory → $MEMORY_DIR"
        else
            mkdir -p "$HOME/.claude"
            printf '{"autoMemoryDirectory":"%s"}\n' "$MEMORY_DIR" > "$SETTINGS"
            echo "[OK] settings.json 新規作成 autoMemoryDirectory → $MEMORY_DIR"
        fi
    else
        echo "[WARN] jq が見つかりません。以下を手動で ~/.claude/settings.json に追加してください:"
        echo "       \"autoMemoryDirectory\": \"$MEMORY_DIR\""
    fi
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

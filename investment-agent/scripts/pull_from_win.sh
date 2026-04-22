#!/bin/bash
# pull_from_win.sh
# Win環境から最新版取得（git pull + GCS secrets pull）
# 使い方: bash scripts/pull_from_win.sh
#
# 前提: Windows側で push_to_linux.sh が実行済みであること

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== Win環境から最新版取得 ==="
echo ""

echo "[1/2] git pull..."
git pull
echo ""

echo "[2/2] secrets pull (GCS → VM)..."
bash "$PROJECT_ROOT/scripts/sync_secrets_pull.sh"
echo ""

echo "=== 完了 ==="

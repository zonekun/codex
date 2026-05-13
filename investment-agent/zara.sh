#!/usr/bin/env bash
# ザラ場ツール一発起動ラッパー（uv run 経由）
cd /home/zonekun/project/claude/investment-agent
PYTHONUTF8=1 uv run python /home/zonekun/.local/bin/zara.py "$@"

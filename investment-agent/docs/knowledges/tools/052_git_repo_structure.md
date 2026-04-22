# GitHubリポジトリ構造

**カテゴリ**: tools
**作成日**: 2026-03-21
**ステータス**: 有効

## 概要

`zonekun/claude` リポジトリの構造とWindows・Linux VM上のパス対応。

## リポジトリ構造

```
zonekun/claude (repo root)
├── investment-agent/   ← 投資AIエージェントプロジェクト（このCLAUDE.mdが属する）
├── stock-skills/       ← 別プロジェクト（独立）
├── main.py
├── pyproject.toml
└── uv.lock
```

> **重要**: `investment-agent` はリポジトリそのものではなく、リポジトリ内のサブディレクトリ。

## パス対応

| 環境 | git repo root | investment-agent |
|------|--------------|-----------------|
| Windows (Git Bash) | `/g/マイドライブ/claude/` | `/g/マイドライブ/claude/investment-agent/` |
| Linux VM (zonekun) | `~/project/claude/` | `~/project/claude/investment-agent/` |

## git操作

```bash
# gitコマンドはrepo root（investment-agentの1つ上）で実行
cd /g/マイドライブ/claude   # Windows
cd ~/project/claude         # VM

git push origin master
git pull
```

## VM初回cloneコマンド

```bash
gh repo clone zonekun/claude ~/project/claude
```

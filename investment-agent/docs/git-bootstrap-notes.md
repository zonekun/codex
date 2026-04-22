# Git Bootstrap Notes

作成日: 2026-04-22  
対象プロジェクト: `investment-agent`

## 現状
- Codex 側の Git ルートは `C:\Users\zonekun\Documents\codex`
- Codex プロジェクトは `C:\Users\zonekun\Documents\codex\investment-agent`
- 現在の作業ブランチは `codex/integration`
- `origin` は `https://github.com/zonekun/codex.git`
- 初回 commit / push 済み
  - commit: `a2d87f1 Initial codex workspace import`
  - upstream: `origin/codex/integration`

## 除外方針
初回 commit / push 前に、以下が Git 対象外であることを確認する。

- `.env`
- `keys/`
- `.venv/`
- `.venv-codex/`
- `.uv-cache/`
- `.uv-python/`
- `.claude/settings.local.json`
- `.claude/scheduled_tasks.lock`
- `data/cache/`
- `data/logs/`
- `.pytest_cache/`

## 初回 commit 手順
初回 commit は実施済み。再作成が必要な場合のみ、必ず dry-run で対象を確認してから実行する。

```powershell
cd C:\Users\zonekun\Documents\codex
git status --short --ignored
git add --dry-run investment-agent
```

対象に問題がなければ commit する。

```powershell
git add investment-agent
git commit -m "Initial codex workspace import"
git push -u origin codex/integration
```

## ブランチ切替
`codex/integration` へ切り替える場合は次を使う。

```powershell
cd C:\Users\zonekun\Documents\codex\investment-agent
powershell -ExecutionPolicy Bypass -File .\scripts\switch_codex_branch.ps1 `
  -RepoPath "C:\Users\zonekun\Documents\codex" `
  -BranchName "codex/integration"
```

このスクリプトは、初回 commit 前の unborn branch 状態でも、既に `codex/integration` 上なら何もしない。

## Claude Code 側への取り込み
Claude Code 側で Git 連携を始める場合は、同じ `origin` と `codex/integration` を基準にする。

```powershell
git remote add origin https://github.com/zonekun/codex.git
git fetch origin
git checkout codex/integration
```

既存作業ツリーがある場合は、上書きや mirror 同期の前に差分を必ず確認する。

# Claude Code 側 取り込みチェックリスト

作成日: 2026-04-22  
対象プロジェクト: `investment-agent`

## 1. 目的

本書は、Codex 側で作成された変更を Claude Code 側へ安全に取り込むための確認手順を定める。

## 2. 取り込み前提

- 本運用の正系は Claude Code 側
- Codex 側の変更は `codex/integration` または `codex/*` ブランチに載る
- Claude Code 側でレビューし、必要な変更のみ本流へ取り込む

## 3. ブランチ確認

- 取り込み対象ブランチ名を確認する
- `main` 相当へ直接変更していないことを確認する
- 取り込み対象以外の不要差分が混ざっていないことを確認する

## 3-1. 取り込み方式

Codex 側でコミット済みのコード変更は、通常のパッチ適用ではなく Git で取り込む。

Codex 側の標準構成:

- repository root: `C:\Users\zonekun\Documents\codex`
- project path: `C:\Users\zonekun\Documents\codex\investment-agent`
- branch: `codex/integration`
- Git 上の変更パス例: `investment-agent/scripts/foo.py`
- Claude Code 側の対応パス例: `C:\gdrive\claude\investment-agent\scripts\foo.py`

推奨手順:

```powershell
git fetch origin codex/integration
git cherry-pick <commit>
```

通常の patch 適用をデフォルトにしない。Codex 側の Git repository root は `investment-agent` の1階層上で、Claude Code 側は `C:\gdrive\claude\investment-agent` 直下で作業することがある。この prefix 差により、patch がコンテキスト不一致で skip されたり、手動適用が必要になったりする。

Git cherry-pick が使えず、明示的に patch が必要な場合のみ、Codex 側で `investment-agent/` prefix を落とした Claude Code 側用 patch を生成する。

```powershell
git show --format= --relative=investment-agent <commit> -- investment-agent/<path> > C:\tmp\<commit>_claude.patch
```

Claude Code 側では `C:\gdrive\claude\investment-agent` から `git apply --3way` で適用する。ファイルコピーや手動編集での取り込みは、Git 取り込みと `--3way` patch の両方が使えない場合の最終手段にする。

## 4. 差分確認

- 変更ファイル一覧を確認する
- コード変更と設定変更を分けて把握する
- `.env`、`.mcp.json`、`keys/`、認証まわりの変更有無を確認する
- スクリプト変更が運用系か、一時調査系かを判別する
- ドキュメント変更に運用ルールの改定が含まれているか確認する

## 5. 運用影響確認

- `CLAUDE.md` の前提を壊していないか確認する
- パス前提が Claude Code 側で破綻しないか確認する
- Windows 前提、UTF-8 前提、既存実行手順に反しないか確認する
- GCP、MCP、ブラウザ操作、定期運用への影響有無を確認する

## 6. Secrets と認証確認

- `.env` の変更がある場合は意図を確認する
- `keys/` 配下の追加、削除、更新を確認する
- GCP サービスアカウントや API キーの参照先が変わっていないか確認する
- 認証情報の欠落や上書き事故がないか確認する

## 7. 主要設定確認

- `.mcp.json` の変更有無を確認する
- `pyproject.toml` と `uv.lock` の整合を確認する
- 実行パスや環境変数前提が変わっていれば、その影響を確認する

## 8. 動作確認

- 必要に応じて `pytest` を実行する
- smoke 的に主要経路が通るか確認する
- まず確認候補とする主要スクリプト:
  - `download_monthly.py`
  - `tdnet_load_parallel.py`
  - `update_monthly_adapters.py`
  - `extract_monthly_data.py`
  - `monitor_backfill.py`
- 失敗時は、移行影響か既存不具合かを切り分ける

## 9. 取り込み判断

- 影響範囲が明確
- 認証や secrets の扱いに問題がない
- 主要設定が破綻していない
- 必要なテストまたは確認が済んでいる
- Claude Code 側で継続運用可能

上記を満たした変更のみ本流へ取り込む。

## 10. 取り込み後確認

- 本流反映後の差分を再確認する
- 必要に応じて主要スクリプトを再実行する
- MCP 接続や認証前提に問題がないか最終確認する
- 取り込みに伴う追加対応があれば `docs/codex-to-claude-handoff.md` などに残す

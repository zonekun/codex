# Claude Code 片方向取り込み Sync

作成日: 2026-04-22

## 既定手順以外は禁止

この文書で許可する同期は次の 2 種類だけ。

1. MD 同期: `scripts/sync_claude_md.py` を使う。
2. ソースコード同期: Git 上で `codex/integration` のソースコード対象ツリーを Claude Code 側の最新へ単純上書きする。

上記以外の方法を使ってはならない。独自スクリプト、手作業コピー、ディレクトリ単位ミラー、履歴統合、merge、rebase、部分的な思いつき差分適用は禁止する。

## 厳禁: ROBOCOPY

このリポジトリ作業では `ROBOCOPY` を使ってはならない。`robocopy /MIR` に限らず、dry-run、差分確認、単純コピー、同期、削除確認、検証目的でも禁止する。

Claude Code から Codex への同期は必ず本書の既定手順だけを使う。ディレクトリ単位ミラーや汎用コピーコマンドは `docs/codex/**`、`docs/claude-md-sync.md`、`docs/codex-to-claude-handoff.md`、`AGENTS.md`、Codex 専用スクリプトなどの保護対象を削除・上書きする危険がある。

## 1. MD 同期

Markdown と secrets / API keys は専用ツールで同期する。既定の確認・取り込み方法は次の通り。

```powershell
$env:PYTHONUTF8='1'
python .\scripts\sync_claude_md.py
python .\scripts\sync_claude_md.py --apply
```

secrets / API keys は GCS 正本から取り込む。

```powershell
$env:PYTHONUTF8='1'
python .\scripts\sync_codex_secrets.py
python .\scripts\sync_codex_secrets.py --apply
```

MD 同期で Git の checkout / restore / merge / rebase / コピーコマンドを使ってはならない。`CONFLICT` は自動解決せず、内容確認してから個別対応する。

## 2. ソースコード同期

Claude Code 側のソースコード最新版を Codex へ取り込む場合は、`codex/integration` ブランチ上の対象ツリーを Git 上で Claude Code 側の最新内容へ単純に置き換える。履歴の統合、merge、rebase、コピーコマンドによるミラー、凝った差分適用は不要。

既定手順:

```powershell
git fetch https://github.com/zonekun/claude.git master:refs/remotes/claude/master
git restore --source=refs/remotes/claude/master -- investment-agent
git restore --source=HEAD -- investment-agent/AGENTS.md investment-agent/docs/claude-md-sync.md investment-agent/docs/codex-to-claude-handoff.md investment-agent/docs/codex investment-agent/scripts/setup_codex_uv.ps1 investment-agent/scripts/sync_claude_md.py investment-agent/scripts/sync_codex_secrets.py
git diff --stat -- investment-agent
git add -A -- investment-agent
git commit -m "chore: mirror claude investment-agent source"
git push origin codex/integration
```

目的は Codex ブランチの内容を Claude Code 側最新へ合わせることであり、Claude Code 側の履歴構造を Codex 側へ持ち込むことではない。Codex オリジナル MD と Codex 専用スクリプトだけは、上記 `git restore --source=HEAD -- ...` で必ず戻す。

## 目的

Claude Code 側で継続編集・管理されるファイルを、Codex 側へ安全に取り込むための手順。

Claude Code 側には同期用の仕組みを追加しない。Codex 側だけで前回取り込み時点の hash や GCS 世代情報を管理する。

## 対象カテゴリ

| カテゴリ | 正系 | Codex側ツール | 状態管理 |
|---|---|---|---|
| Markdown | Claude Code workspace | `scripts/sync_claude_md.py` | `data/logs/claude_md_sync_manifest.json` |
| secrets / API keys | GCS `gs://stock_data_1930932/config/investment-agent/` | `scripts/sync_codex_secrets.py` | `data/logs/codex_secrets_sync_manifest.json` |

## Markdown 対象

既定対象:

- `CLAUDE.md`
- `data_catalog.md`
- `docs/**/*.md`
- `skills/**/*.md`

ただし Codex 移植・並走運用のために Codex 側だけで持つ文書は既定除外する。

- `docs/claude-code-intake-checklist.md`
- `docs/claude-md-sync.md`
- `docs/codex/**`
- `docs/git-bootstrap-notes.md`
- `docs/plans/*codex*.md`

同期状態は Codex 側の `data/logs/claude_md_sync_manifest.json` に保存する。このファイルは `.gitignore` の `data/logs/` 配下なのでローカル状態として扱う。

## 初回

Codex 側が現在の Claude Code 側と一致している状態で baseline を作る。

```powershell
$env:PYTHONUTF8='1'
python .\scripts\sync_claude_md.py --init-baseline
```

`Skipped` が出たファイルは、すでに Claude Code 側と Codex 側で内容が違う。取り込み前に個別確認する。

## 通常確認

```powershell
$env:PYTHONUTF8='1'
python .\scripts\sync_claude_md.py
```

分類:

- `SAFE_IMPORT`: Claude Code 側だけ変更。自動取り込み可
- `CODEX_ONLY`: Codex 側だけ変更。触らない
- `CONFLICT`: Claude Code 側と Codex 側の両方が変更。手動確認
- `CLAUDE_DELETED`: Claude Code 側で削除。既定では削除しない
- `BASELINE_MISSING_SAME`: 両側が同じ内容だが baseline 未記録。manifest のみ更新可

## 安全取り込み

```powershell
$env:PYTHONUTF8='1'
python .\scripts\sync_claude_md.py --apply
git diff --stat
git diff -- CLAUDE.md data_catalog.md docs skills
```

`--apply` は `SAFE_IMPORT` と baseline 更新だけを適用する。`CONFLICT` はコピーしない。

Claude Code 側の削除も反映する場合だけ、内容確認後に `--delete` を付ける。

```powershell
python .\scripts\sync_claude_md.py --apply --delete
```

## 禁止: Mirror mode

`scripts/sync_claude_md.py --mode mirror` は既定手順ではないため使用禁止。dry-run 目的でも使わない。

MD 同期は `scripts/sync_claude_md.py` の通常確認と `--apply` のみを使う。ソースコード同期は上記「2. ソースコード同期」の Git 手順だけを使う。

## 原則

- Claude Code 側または GCS は正系だが、Codex 側から勝手に書き込まない
- Codex 側の MD 変更を上書きしない
- MD 取り込み前は必ず `scripts/sync_claude_md.py` の通常確認を実行する
- `CONFLICT` は人間が diff を見て判断する
- 取り込み後は Codex 側で必要に応じて commit する

## Secrets / API Keys

Claude Code 既存の `scripts/sync_pull.sh` / `scripts/sync_push.sh` と同じ GCS バケットを参照する。ただし Codex 側は pull 専用にする。

既定対象:

- `.env`
- `keys/gcp-service-account.json`

初回 baseline:

```powershell
$env:PYTHONUTF8='1'
python .\scripts\sync_codex_secrets.py --init-baseline
```

通常確認:

```powershell
$env:PYTHONUTF8='1'
python .\scripts\sync_codex_secrets.py
```

安全取り込み:

```powershell
python .\scripts\sync_codex_secrets.py --apply
```

分類:

- `SAFE_IMPORT`: GCS 側だけ変更、またはローカル欠落。自動取り込み可
- `CODEX_ONLY`: Codex ローカルだけ変更。触らない
- `CONFLICT`: GCS と Codex ローカルの両方が変更。手動確認
- `SOURCE_MISSING`: GCS に対象がない
- `BASELINE_MISSING`: baseline 未作成

注意:

- secret 値は表示しない
- manifest には GCS generation/metageneration と Codex ローカル SHA-256 だけを保存する
- Claude Code workspace と GCS には書き込まない

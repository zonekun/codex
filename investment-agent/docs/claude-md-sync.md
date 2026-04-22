# Claude Code 片方向取り込み Sync

作成日: 2026-04-22

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
- `docs/codex-*.md`
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

## 原則

- Claude Code 側または GCS は正系だが、Codex 側から勝手に書き込まない
- Codex 側の MD 変更を上書きしない
- 取り込み前は必ず dry-run
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

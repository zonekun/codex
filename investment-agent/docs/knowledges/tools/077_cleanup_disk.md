# 077: ディスク容量クリーンアップツール (cleanup_disk.py)

## 目的

プロジェクト配下（`data/logs/`）と Claude Code のユーザーホーム配下（`C:\Users\zonekun\.claude\`）
で膨らみがちなログ・キャッシュ・旧プロジェクトセッションを、dry-run 前提で安全にクリーンアップする。

## 使い方

```bash
# 候補表示のみ（削除しない）
PYTHONUTF8=1 python scripts/cleanup_disk.py

# 実削除
PYTHONUTF8=1 python scripts/cleanup_disk.py --execute

# 保持期間を変える（デフォルト: logs/claude 30日, mem 90日）
PYTHONUTF8=1 python scripts/cleanup_disk.py --logs-days 14 --claude-days 14 --mem-days 60

# 片方だけに限定
PYTHONUTF8=1 python scripts/cleanup_disk.py --skip-claude
PYTHONUTF8=1 python scripts/cleanup_disk.py --skip-logs
PYTHONUTF8=1 python scripts/cleanup_disk.py --skip-mem

# claude-mem DB の VACUUM（--execute と併用）
PYTHONUTF8=1 python scripts/cleanup_disk.py --skip-logs --skip-claude --mem-vacuum --execute
```

## スコープ

| 区分 | 対象 | 条件 |
|------|------|------|
| `data/logs/` | `build_*.log` | N 日以上前（デフォルト 30 日） |
| `data/logs/` | `*コピー*`, `* - Copy*`, `*.tmp` | 常に |
| `data/logs/` | 空ファイル | 常に |
| `data/logs/` | `active_jobs.md` | **常に除外**（実運用ファイル） |
| `~/.claude/` | `debug/` `telemetry/` `file-history/` `shell-snapshots/` `paste-cache/` `cache/` 配下 | ファイル mtime が N 日以上前 |
| `~/.claude/projects/<enc_path>/` | プロジェクト単位 | 内部ファイルの最新 mtime が N 日以上前 |
| `claude-mem` logs/ | 日次ログファイル | N 日以上前（デフォルト 90 日） |
| `claude-mem` trash/ | ソフト削除データ | **常に全削除** |
| `claude-mem` backups/ | DBバックアップ | N 日以上前（デフォルト 90 日） |
| `claude-mem` DB | observations / session_summaries / user_prompts | created_at が N 日以上前 |
| `claude-mem` DB | VACUUM + WAL checkpoint | `--mem-vacuum` 指定時のみ |

## `~/.claude/` キャッシュ系の保持判断

30日超のファイルは基本的にすべて削除して問題ない。Claude Code が必要に応じて再生成する。

| ディレクトリ | 用途 | 削除の影響 |
|------|------|---------|
| `telemetry/` | 未送信のテレメトリイベント（Anthropicへの利用統計キュー） | 再送が諦められるだけで機能影響なし |
| `debug/` | Claude Code 自体のクラッシュダンプ・デバッグセッション | Anthropicにバグ報告する時だけ使う。通常は不要 |
| `file-history/` | Claude が編集したファイルのローカル履歴 | gitと重複。Claudeの「編集を戻す」機能用だが実使用は稀 |
| `shell-snapshots/` | セッションごとの shell 環境スナップショット | セッション終了後は再利用されない |
| `paste-cache/` | 画像・ファイル貼付けキャッシュ | エフェメラル |
| `cache/` | 各種内部キャッシュ | 自動再生成 |

**残すべきもの**（cleanup対象外）: `projects/<アクティブなセッション>/`, `settings.json`, `plugins/`, `plans/`, `tasks/`, `todos/`, `sessions/`, `backups/`, `.credentials.json`, `mcp-needs-auth-cache.json`, `stats-cache.json`。スクリプトは `CLAUDE_CACHE_DIRS` にリストアップした6ディレクトリのみを対象にしているので、それ以外は触らない。

## 落とし穴

- **`~/.claude/projects/` 配下のディレクトリ名は不可逆エンコード**: パス区切りと `:` が `-` に
  置換されるため、例えば `G:\マイドライブ\claude\investment-agent` は
  `G---------claude-investment-agent`（日本語 `マイドライブ` が `---------` に化ける）となる。
  「ディレクトリ名からパス復元 → `Path.exists()` で存在確認」は日本語パスで誤爆するため、
  **必ず mtime ベースで stale 判定する**。
- `active_jobs.md` を誤って消さないこと（Cloud Run Job 実行状態の単一真実）。
- 実行前に必ず dry-run で候補件数・サイズを目視確認する。

## 実例（2026-04-16 クリーンアップ）

- 削除済み: 旧 Dropbox パスの `.claude/projects/C--Users-zonekun-Dropbox-claude-investment-agent`
  セッションログ（100MB）, `data/csv/vwap_signals.csv` (306MB), ルート散乱ファイル群,
  `data/` 直下の調査用一時 JSON/CSV, `data/logs/` の古いビルドログ・コピー CSV・空ファイル,
  `scripts/.ipynb_checkpoints/`。
- `cleanup_disk.py` 初回 dry-run で `.claude/` 配下 80.6MB が候補化（30日保持）。

## 関連

- Claude Code hooks logger 設定: `docs/knowledges/tools/017_claude_code_hooks_logger.md`
- `active_jobs.md` 運用: `docs/knowledges/tools/033_check_jobs.md`
- claude-mem プラグイン: `docs/knowledges/tools/082_claude_mem.md`

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

# Git GC（dry-run: .git サイズ表示 + .gitignore チェック）
PYTHONUTF8=1 python scripts/cleanup_disk.py --git-gc

# Git GC 実行
PYTHONUTF8=1 python scripts/cleanup_disk.py --git-gc --execute

# Git GC 対象ディレクトリを変更（デフォルト: C:\gdrive\claude, ~/.claude）
PYTHONUTF8=1 python scripts/cleanup_disk.py --git-gc --git-targets C:\other\path --execute

# C:\tmp 台帳チェック（期限切れエントリを表示）
PYTHONUTF8=1 python scripts/cleanup_disk.py --skip-logs --skip-claude --skip-mem

# C:\tmp 台帳チェック + 実削除 + 台帳から行削除
PYTHONUTF8=1 python scripts/cleanup_disk.py --skip-logs --skip-claude --skip-mem --execute

# 全部入り（ファイル削除 + Git GC + DB VACUUM）
PYTHONUTF8=1 python scripts/cleanup_disk.py --git-gc --mem-vacuum --execute
```

## スコープ

| 区分 | 対象 | 条件 |
|------|------|------|
| `data/logs/` | `build_*.log` | N 日以上前（デフォルト 30 日） |
| `data/logs/` | `*コピー*`, `* - Copy*`, `*.tmp` | 常に |
| `data/logs/` | 空ファイル | 常に |
| `data/logs/` | `active_jobs.md` | **常に除外**（実運用ファイル） |
| `~/.claude/` | `debug/` `telemetry/` `file-history/` `shell-snapshots/` `paste-cache/` `cache/` 配下 | ファイル mtime が N 日以上前 |
| `~/.claude/projects/<enc_path>/` | 個別ファイル単位 | ファイル mtime が N 日以上前（アクティブプロジェクト含む） |
| `claude-mem` logs/ | 日次ログファイル | N 日以上前（デフォルト 90 日） |
| `claude-mem` trash/ | ソフト削除データ | **常に全削除** |
| `claude-mem` backups/ | DBバックアップ | N 日以上前（デフォルト 90 日） |
| `C:\tmp\` | 103 台帳の期限列が `YYYY-MM-DD` かつ当日以前のエントリ | 期限日 ≤ 今日 → 実体削除 + 台帳行削除 |
| `claude-mem` DB | observations / session_summaries / user_prompts | created_at が N 日以上前 |
| `claude-mem` DB | VACUUM + WAL checkpoint | `--mem-vacuum` 指定時のみ |
| Git GC | 対象ディレクトリ配下の `.git/` | `--git-gc` 指定時。`git gc --aggressive --prune=now` |

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

**残すべきもの**（cleanup対象外）: `settings.json`, `plugins/`, `plans/`, `tasks/`, `todos/`, `sessions/`, `backups/`, `.credentials.json`, `mcp-needs-auth-cache.json`, `stats-cache.json`。スクリプトは `CLAUDE_CACHE_DIRS` にリストアップした6ディレクトリ + `projects/` のみを対象にしているので、それ以外は触らない。

## `~/.claude/projects/` の仕様

- **個別ファイル単位で mtime 判定**。アクティブプロジェクトであっても N 日超のファイルは削除候補
- プロジェクトディレクトリ自体は削除しない（古いファイルのみ除去）
- `--claude-days` で保持日数を指定（デフォルト 30 日）

## Git GC の仕様

- `--git-gc` 指定時に実行。デフォルト対象: `C:\gdrive\claude\` と `~/.claude\` の直下リポジトリ（1階層のみ探索。2階層以上の入れ子は `--git-targets` で個別指定）
- `--git-targets` で対象ディレクトリを変更可能
- 各リポジトリで `git gc --aggressive --prune=now` を実行（不要オブジェクト・古い reflog・未参照ブロブを圧縮削除）
- GC 前後の `.git/` サイズを表示
- `.git/index.lock` または `.git/gc.pid` が存在するリポジトリはスキップ（別プロセスが操作中）
- `.gitignore` に `node_modules/`, `.venv/`, `__pycache__/` が含まれていなければ警告（glob構文 `**/node_modules` 等も認識）
- ジャンクション/シンボリックリンクは `resolve()` で正規化し、同一リポジトリの二重実行を防止
- タイムアウト: リポジトリあたり 5 分。`--aggressive` は大規模リポジトリで超過する場合がある（その場合は個別に `git gc` を手動実行）

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

# Claude Code フック — 会話ログ自動記録システム

**カテゴリ**: tools
**作成日**: 2026-03-02
**更新日**: 2026-04-10
**ステータス**: 有効
**関連ファイル**: `scripts/claude_logger.py`, `scripts/check_jobs.py`, `.claude/settings.local.json`

## 概要

Claude Code のフック機能を使って、ユーザー ↔ Claude の対話とツール呼び出しを
自動的にログファイルへ記録する。クラッシュ後のセッション再開に使う。

**v2 (2026-04-10)** で以下を改修:
- セッション別ログ分離（`--session-id=$PPID`）— 複数コンソール同時作業に対応
- ログ保管先をローカル（`C:\tmp\claude_logs\`）に移行 — Google Drive同期遅延を解消
- 保持期間を短縮（conversation/tool_trace: 6時間、console: 12時間）
- `gcloud run jobs execute` 検出で active_jobs.md に自動追記
- `check_jobs.py --update-active` で active_jobs.md の🔄エントリを自動更新

**v3 (2026-04-24)** — セッション分離の根本修正:
- `$PPID` は MSYS2 上で常に 1（init プロセス）→ 全セッションが同一ディレクトリに混在する致命バグ
- hook stdin JSON の `session_id`（UUID）でセッション分離するように変更。`--session-id=$PPID` を hook command から削除
- セッション識別ラベル（`label.txt`）を導入 — 初回プロンプトから40文字で自動生成
- `list_claude_sessions.py` でラベル付き一覧表示
- CLAUDE.md クラッシュリカバリ手順を改訂（「1件なら省略」削除、ラベル使用義務追加）

---

## ログファイル一覧

| ファイル | 内容 | 保持上限 |
|---------|------|---------|
| `C:\tmp\claude_logs\<session_id>\conversation.log` | ユーザーのプロンプト + Claude の応答テキスト | 6 時間 |
| `C:\tmp\claude_logs\<session_id>\tool_trace.log` | ツール呼び出し追跡（Bash/Edit/Write/Agent） | 6 時間 |
| `C:\tmp\claude_logs\<session_id>\console.log` | Bash コマンド出力（先頭800文字） | 12 時間 |
| `C:\tmp\claude_logs\debug.log` | claude_logger.py 自身のエラーログ（共有） | 追記のみ（手動削除） |
| `data/logs/active_jobs.md` | Cloud Run ジョブ状態（共有、自動追記） | 手動管理 |

### セッション分離の仕組み

- Claude Code が hook stdin JSON に含める `session_id`（UUID）でセッションを分離
- 各セッションのログは `C:\tmp\claude_logs\<UUID>\` に分離保存
- 各セッションディレクトリに `label.txt`（初回プロンプトから自動生成）を保持
- 24時間以上更新のない古いセッションディレクトリは自動削除
- **サブエージェント（Agent ツール）は親と異なる session_id を持つ**。hook stdin に `agent_id` キーが含まれる場合はサブエージェント。リカバリ時にサブエージェントのセッションが表示されても通常は復元不要（User発話0件で判別可能）

> **廃止**: v2 で使用していた `--session-id=$PPID` は MSYS2 上で `$PPID=1` 固定のため v3 で廃止。CLI 引数 `--session-id` は互換性のためフォールバックとして残存

### conversation.log フォーマット

```
=== 2026-03-04 08:01:36 JST [User] ===
ユーザーのメッセージ

=== 2026-03-04 08:02:15 JST [Claude] ===
Claude の応答テキスト

```

### tool_trace.log フォーマット

```
2026-03-04 08:02:10 [User] irbank.net URL提供、資料一覧確認依頼
2026-03-04 08:02:11 [Bash] gcloud run jobs executions list --job=edinet-download ...
2026-03-04 08:05:30 [Write] scripts/irbank_tdnet_download.py
2026-03-04 09:10:00 [Bash] gcloud builds submit --tag gcr.io/...
2026-03-04 09:45:22 [Agent] irbank.net 資料一覧確認
```

---

## クラッシュ後の再開方法

クラッシュ後に新しいセッションを開始したら:

> **「クラッシュした。再開モード発動」**

Claude は以下を実行:

1. 最新セッションのログを特定して読む:
   ```bash
   ls -lt C:/tmp/claude_logs/ | head -5
   ```
   - `conversation.log` — 対話履歴
   - `tool_trace.log` — **自律作業の行動記録**（最重要）
2. active_jobs.md を自動更新 + gcloudで最新状況を確認:
   ```bash
   PYTHONUTF8=1 python scripts/check_jobs.py --update-active
   PYTHONUTF8=1 python scripts/check_jobs.py --limit 5
   ```
3. 状況をユーザーに報告し、指示があるまで前セッションの続きを勝手に実行しない

> **注意**: conversation.log の「最後のユーザーメッセージ」が必ずしも「最後の作業」では
> ない。ユーザーが席を外した後に Claude が2〜3時間自律作業を続けた場合、
> tool_trace.log にその記録が残っている。

---

## active_jobs.md 自動更新

### 自動追記（PostToolUse フック）

`gcloud run jobs execute <job-name>` を検出すると、active_jobs.md の「## 実行中」テーブルに🔄エントリを自動追記する。

- ジョブ名: コマンドから抽出
- 実行ID: gcloud応答から抽出（取得できない場合は `-`）
- 引数: `--args` から抽出

### 自動ステータス更新（check_jobs.py）

```bash
PYTHONUTF8=1 python scripts/check_jobs.py --update-active
```

- active_jobs.md の 🔄 エントリを gcloud 結果で照合
- 完了(✅) / 失敗(❌) / キャンセル(⛔) に自動更新
- 完了時刻も自動付記

---

## フック設定（`.claude/settings.local.json`）

```json
"hooks": {
  "UserPromptSubmit": [{
    "hooks": [{ "type": "command",
      "command": "PATH=$HOME/.local/bin:$PATH PYTHONUTF8=1 uv run python scripts/claude_logger.py --event=prompt",
      "timeout": 30 }]
  }],
  "Stop": [{
    "hooks": [{ "type": "command",
      "command": "PATH=$HOME/.local/bin:$PATH PYTHONUTF8=1 uv run python scripts/claude_logger.py --event=stop",
      "timeout": 30 }]
  }],
  "PostToolUse": [{
    "hooks": [{ "type": "command",
      "command": "PATH=$HOME/.local/bin:$PATH PYTHONUTF8=1 uv run python scripts/claude_logger.py --event=tool",
      "timeout": 10 }]
  }]
}
```

> session_id は hook stdin JSON から取得するため、CLI 引数 `--session-id` は不要（v3 で廃止）

---

## `claude_logger.py` の動作

| フックイベント | 処理内容 |
|-------------|---------|
| `UserPromptSubmit` | 6時間超エントリを削除 → conversation.log にユーザーメッセージを追記 → tool_trace.log にもマーカーを追記 → 古いセッションディレクトリを削除 |
| `Stop` | last_assistant_message からテキストを抽出して conversation.log に追記。失敗時は debug.log に記録 |
| `PostToolUse` | Bash/Edit/Write/Agent の呼び出しを tool_trace.log に追記。`gcloud run jobs execute` 検出時は active_jobs.md に自動追記 |

### PostToolUse でログ対象のツール

| ツール | 記録内容 |
|-------|---------|
| Bash | コマンド先頭120文字（cat/head/tail/ls/echo/grep 系はスキップ）+ console.logに出力保存 |
| Edit | 編集ファイルパス |
| Write | 書き込みファイルパス |
| NotebookEdit | ノートブックパス |
| Agent | description 先頭80文字 |
| Read / WebFetch / WebSearch / Glob / Grep | **除外**（量が多すぎるため） |

---

## Claude Code フック仕様メモ

| 項目 | 内容 |
|------|------|
| 設定場所 | `.claude/settings.local.json` の `"hooks"` キー |
| 入力 | JSON を stdin で受け取る |
| `UserPromptSubmit` の stdin | `{"prompt": "...", "transcript": [...], "session_id": "..."}` |
| `Stop` の stdin | `{"last_assistant_message": "...", "transcript_path": "...", "session_id": "..."}` |
| `PostToolUse` の stdin | `{"tool_name": "...", "tool_input": {...}, "tool_response": ..., "session_id": "..."}` |
| 出力 | stdout に JSON を出力（空なら `{}`）|
| 終了コード | 必ず `0`。非 0 は Claude の動作を止める可能性あり |

---

## 落とし穴 — `cd` で作業ディレクトリを変えるとフックが壊れる

フックコマンドは `scripts/claude_logger.py` を**相対パス**で指定している（上記 settings.local.json 参照）。Bash ツールの cwd はコマンド間で永続するため、一度でも `cd サブディレクトリ` したまま以降のBashを走らせると、PostToolUse / Stop フックがその相対パスを解決できず毎回失敗する。

```
C:\...\Python312\python.exe: can't open file
'G:\\マイドライブ\\claude\\investment-agent\\meta\\monthly\\scripts\\claude_logger.py':
[Errno 2] No such file or directory
```

**対策**:

- Bash ツールで `cd` を使わない。絶対パス（`G:/マイドライブ/claude/investment-agent/...`）で操作する
- やむを得ず `cd` する場合は **サブシェル囲い** が最も安全: `(cd X && ...)` — 外側のcwdは変わらない
- 同一コマンド内で戻す方法もある: `cd X && ... && cd -`（ただし途中で失敗すると戻らない）
- Python スクリプト側でのパス操作は `pathlib.Path` の絶対パス指定にする（`cd` 不要）
- 事故後のリカバリ: `cd "G:/マイドライブ/claude/investment-agent"` を単独で実行して cwd を戻す（以降のフック呼び出しは復旧する）

---

## 根拠・出典

- 2026-03-02: Claude Code クラッシュ（Bun OOM）後の記憶喪失問題を解決するために実装
- 2026-03-04: PostToolUse フック追加。自律作業（ユーザー不在時）の行動記録を補完
- 2026-04-10: v2改修 — セッション分離・ローカル保管・保持期間短縮・active_jobs.md自動更新
- 2026-04-11: 「落とし穴 — `cd` で作業ディレクトリを変えるとフックが壊れる」追記
- 2026-04-16: ディスククリーンアップ作業中に同事象再発。対策に**サブシェル囲い** `(cd X && ...)` を追記

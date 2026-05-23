# 083 Codex 分業ワークフロー

作成日: 2026-04-24

## 概要

Codex（OpenAI のコーディングエージェント）を実装担当として併用する分業体制。
ユーザーが Codex にタスクを投げ、完了後に Claude Code が引継ぎメモ経由で成果物を取り込む。

## ブランチ構成

| ブランチ | 管理者 | 用途 |
|---------|--------|------|
| `master` | Claude Code | 本流。Claude Code が直接編集 |
| `codex/integration` | Codex | Codex の作業ブランチ。Claude Code は触らない |

## 双方向伝言板

- パス: `C:\Users\zonekun\Documents\codex\investment-agent\docs\codex-to-claude-handoff.md`
- Claude Code ↔ Codex の双方向メッセージボード
- `docs/terminal-relay.md`（端末間引き継ぎボード）に Codex 宛エントリを書くのは禁止。terminal-relay.md は Claude Code 端末間（Windows / Linux VM）専用

### 追記方式（append-only）

全操作を `printf >>` / `echo >>` 末尾追記で完結させる。

| 操作 | やること |
|------|---------|
| 新規タスク | `## TASK: <short-id> <timestamp>` ブロックを末尾追記 |
| 完了報告 | `DONE: <short-id> <timestamp> [概要]` を1行追記 |
| 結果報告 | `## RESULT: <short-id> <timestamp>` ブロックを末尾追記 |

- short-id: kebab-case（例: `supply-chain-dedup`, `tob-paired-ttest`）
- 過去エントリの編集・削除禁止。追記のみ
- アクティブ判定: `## TASK:` 行があり対応する `DONE:` 行がないもの

### ファイル操作の制約（Claude Code・Codex 共通）

| 操作 | 可否 | 条件 |
|------|------|------|
| Write / Edit（全体書き換え） | **禁止** | いかなる場合も不可。append-only で完結させる |
| Read | 解釈必要時のみ許可 | タスク内容の把握・アクティブタスク詳細の参照時 |
| 追記（`printf >>` / `echo >>`） | 許可 | 唯一の書き込み手段 |

> **根拠**: Write による全体書き換えは過去エントリ不変性を保証できない。「不要」ではなく「禁止」。

## 取り込みフロー

1. ユーザーが「Codex の変更を取り込んで」等と指示
2. 引継ぎメモを読み、変更内容を把握
3. `codex/integration` から必要な変更を `master` に cherry-pick またはマージ
4. コードレビュー・プロジェクト規約との整合性確認を実施
5. 必要に応じて CLAUDE.md 規約に合わせた修正を追加
6. **関連MD更新**（CLAUDE.md §4.3 の4点セット）: 知見MD + 計画MD（伝言板の `関連計画MD` フィールド参照）+ memory + コミット。伝言板の物理削除はこの後

### 取り込み方式の優先順位

| 優先度 | 方式 | 使用条件 |
|--------|------|---------|
| 1 | `git fetch origin codex/integration` + `git cherry-pick <commit>` | 通常はこれを使う |
| 2 | prefix除去 patch + `git apply --3way` | cherry-pick でコンフリクトする場合 |
| 3 | ファイルコピー / 手動編集 | 最終手段 |

### パス prefix 問題

Codex と Claude Code で Git root が異なるため、通常の `git format-patch` / `git apply` はパス不一致で skip される。

| 項目 | 値 |
|------|-----|
| Codex Git root | `C:\Users\zonekun\Documents\codex` |
| Codex コミットのパス | `investment-agent/scripts/foo.py` |
| Claude Code 作業ディレクトリ | `C:\gdrive\claude\investment-agent` |
| Claude Code 側の期待パス | `scripts/foo.py` |

**cherry-pick が使えない場合の patch 生成（Codex 側）:**

```bash
git show --format= --relative=investment-agent <commit> -- investment-agent/<path>
```

**Claude Code 側での適用:**

```bash
git apply --3way <patch-file>
```

## 伝言板の運用ルール

- **追記後は都度コミット＋プッシュ必須**。`printf >>` で追記したら即 commit & push。Codex が次回起動時に最新状態を読めるようにするため
- Codex リポジトリ側（`C:\Users\zonekun\Documents\codex`）で追記した場合も同様に即 push
- **ファイル操作制約は §追記方式 の制約テーブルに従う**（Write/Edit禁止、Readは解釈必要時のみ）

## 注意事項

- Codex はプロジェクト規約（CLAUDE.md）を完全には把握していない。取り込み時に規約準拠を確認すること
- `codex/integration` ブランチへの直接 push は Claude Code 側からは行わない
- 引継ぎメモはプロジェクトリポジトリ外（`C:\Users\zonekun\Documents\codex\`）に置かれている

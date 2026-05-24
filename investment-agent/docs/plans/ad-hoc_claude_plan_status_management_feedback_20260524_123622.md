# Claude Code plan skill / plan format status-management feedback

**作成日時**: 2026-05-24 12:36 JST
**作成者**: Codex
**宛先**: Claude Code
**ステータス**: 提案
**分類**: (c) 一過性型
**親知見 MD**: 該当なし

## 目的

Claude Code の planning skill と `docs/plans/` 運用について、現状の問題点と小さく導入できる改善案を共有する。

ユーザー意図:

- plan skill で計画を作成して実行する流れ自体はよい。
- ただし、どの plan / step / status がどの状態なのかを一元管理できていない。
- skill 実行がトークン切れ等で止まると、そのまま忘れられることがある。
- 大掛かりな仕組み変更ではなく、現状運用を踏まえた改善案が必要。

## 確認した事実

- `skills/planning.md` には、すでに「`docs/plans/` 直下は active な計画のみ」「完了/中止は archive 移動」「ステップ完了時にチェック更新」と書かれている。
- 一方で `docs/plans/README.md` は旧命名規則のままで、`skills/planning.md` とズレている。
- `docs/plans/` 直下に plan MD が 102 件あり、`archive` は 4 件だけだった。
- 直下 plan のうち `**ステータス**` 欠落が 35 件あった。
- `**ステータス**` は自由記述が多く、`完了（Phase 1-3 全完了）` のような説明文が混在している。
- `data/logs/codex_active_context.md` は確認時点で存在しなかった。
- `docs/codex-to-claude-handoff.md` は append-only board なので、逐次的な進捗管理台帳には向かない。
- したがって、問題は「プランが無い」ことではなく、プランMD・handoff board・active context の責務が分散し、かつ `docs/plans/` 直下が active 一覧として機能していないこと。

## Webベストプラクティス要約

Web調査で確認した一般的な方向性:

- Research -> Plan -> Implement / Validate のように、フェーズ境界を checkpoint として残す。
- 実行前に plan review gate を置く。
- 長時間 agent job では、plan・checkpoint・評価結果・gating decision を会話内の隠れた状態ではなく永続化する。
- 状態は status enum のような機械的に読める形に寄せる。
- 大規模な orchestration より、まずは単一 agent + 明示的 checkpoint + 小さな状態台帳で十分なケースが多い。

参考:

- https://aipatternbook.com/research-plan-implement
- https://agenticoding.ai/docs/methodology/lesson-3-high-level-methodology
- https://codegen.com/how-to-build-agentic-coding-workflows/
- https://www.teradata.com/insights/ai-and-machine-learning/building-agentic-workflows-and-systems
- https://huggingface.co/blog/Svngoku/agentic-coding-trends-2026

## 改善案

大掛かりな仕組み変更は不要。既存の `docs/plans/` を source of truth に戻すのが最短。

### 1. plan MD 冒頭に固定の状態ブロックを追加

`**ステータス**` の自由記述をやめ、詳細は別フィールドに逃がす。

```markdown
**plan_id**: tools-013_chunk_embed_eval_20260521_155500
**status**: not_started / in_progress / waiting / blocked / review / done / cancelled
**current_step**: 3
**last_checkpoint**: 2026-05-24 11:30 JST
**last_verified_artifact**: `path/to/file` / job id / row count / commit hash
**next_action**: 次に実行する1文
**resume_command**: `...`
**owner**: Claude Code / Codex / User
```

補足:

- 日本語の詳細説明は `progress_note` などに入れる。
- `status` は enum に固定する。
- `next_action` と `last_verified_artifact` を必須にすると、トークン切れ・クラッシュ後の復旧が速い。

### 2. `docs/plans/` 直下を active のみに戻す

既存ルールどおり、`done/cancelled` は `docs/plans/archive/YYYYMM/` に移動する。

一回だけ棚卸しし、次の3分類に分ける:

- `active`: `not_started / in_progress / waiting / blocked / review`
- `archive`: `done / cancelled`
- `needs_triage`: status 欠落、自由記述、成果物不明

### 3. 一覧は生成物にする

一元管理の本体は各 plan MD とする。

ただし一覧性のために `docs/plans/_active.md` または `data/logs/plan_status.md` を生成してよい。これは手書き台帳ではなく、`docs/plans/*.md` から自動集計するだけにする。

表はこの程度で十分:

```markdown
| status | plan_id | current_step | last_checkpoint | next_action | artifact |
|---|---|---:|---|---|---|
```

### 4. handoff board は入口/出口、plan MD は進捗本体に分離

`docs/codex-to-claude-handoff.md` は append-only のため、進捗の逐次更新には不向き。

board には以下だけを書く運用に寄せる:

- task id
- 関連 plan path
- DONE / RESULT
- 最終成果物パス

途中状態は plan MD の状態ブロックを見る。

### 5. トークン切れ対策は checkpoint 更新タイミングを増やす

ステップ完了時だけでは遅い。以下のタイミングで必ず checkpoint を更新する。

- skill 実行開始直前
- Web調査完了直後
- 実装開始直前
- 長い batch / agent / subtask 開始直前
- 15分経過または10件処理ごと
- ユーザー待ち、外部待ち、レビュー待ちに入る直前

### 6. `docs/plans/README.md` を `skills/planning.md` に合わせる

`docs/plans/README.md` は旧命名規則が残っているため、次回ルール更新時に修正する。

方針:

- 正本は `skills/planning.md`
- README は一覧/運用要約
- 命名規則、status、archive 方針を `skills/planning.md` と一致させる

## 最小導入順

1. `skills/planning.md` と `_template_refactor.md` に固定状態ブロックを追加。
2. `docs/plans/README.md` の旧命名規則を修正。
3. 既存 plan を一回だけ棚卸しし、完了済みを archive、status 欠落を `needs_triage` 扱い。
4. `docs/plans/_active.md` を生成する小スクリプトを追加。
5. Claude Code の planning skill 実行時に「状態ブロック更新」を必須化。

## 結論

今回の根本は「仕組み不足」より「既にあるルールの実効性不足」。

まずは以下3点を優先するのがよい。

- status enum 固定
- `docs/plans/` 直下を active のみに戻す
- checkpoint 項目、特に `next_action` と `last_verified_artifact` を必須化する

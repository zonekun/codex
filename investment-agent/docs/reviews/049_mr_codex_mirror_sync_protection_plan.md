# 049_mr_codex_mirror_sync_protection_plan

作成日時: 2026-05-01 18:51 JST

## 依頼種別

md-reviewer への実装前計画レビュー依頼。

## 対象

- 計画: `C:\Users\zonekun\Documents\codex\investment-agent\docs\plans\20260501_185114_codex_mirror_sync_protection_plan.md`
- 元レビュー: `C:\gdrive\claude\investment-agent\docs\reviews\048_mr_codex_mirror_sync_artifact_protection.md`

## 背景

048 の md-reviewer 結果を受けて、Codex 側で mirror sync 保護の実装計画を作成しました。

今回はまだ実装せず、計画の AI 可読性・誤読リスク・実装漏れをレビューしてください。

## レビューしてほしい観点

1. 048 の重大指摘5件に対して、計画が過不足なく対応しているか。
2. `DEFAULT_EXCLUDES` と `CODEX_PROTECTED` の二重防御は妥当か。
3. untracked 削除を `--force-delete-untracked` なしで fail closed する仕様は妥当か。
4. `docs/reviews/001_sync_claude_md_mirror_gap.md` の扱いは、リネームと個別除外のどちらを優先すべきか。
5. `AGENTS.md` への導線追加は必要か、または `docs/claude-md-sync.md` と `docs/codex-operation-knowledge.md` だけで足りるか。
6. テスト計画に不足がないか。

## 期待する出力

- 実装前に修正すべき計画上の指摘
- 計画のまま実装してよい項目
- 実装時に特に注意すべき副作用
- 可能なら、計画MDに追記すべき文案


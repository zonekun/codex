# コードレビュー提出: structure.json QA計画 Phase B チェッカー設計

## 対象

`docs/plans/20260501_202600_structure_json_quality_assurance.md` — Phase B（Step 4〜Step 6）の更新部分

## 変更内容

Phase AをCodexが実施し、誤り率13.3%（CONDITIONAL）の結果が出た。この結果を受けてPhase Bを以下の通り更新:

1. Phase A実施結果セクションを追加（誤り率、4社の詳細、パターン分布）
2. Step 4（チェッカー設計）をPhase A実測の3パターンに基づき具体化:
   - チェック1: 偽陽性検出（E-1〜E-4）— 最優先、Phase Aで50%
   - チェック2: 単位バリデーション（D-1〜D-2）
   - チェック3: メトリクス網羅性（A-1〜A-3）
   - チェック4: 汎用品質チェック（G-1〜G-3）
3. Step 4.5（回帰テスト）を追加: チェッカーをPhase Aの30社で検証してから全社適用
4. Step 5のフラグ企業数の想定範囲を具体化（38〜153社）
5. Step 6にfix_log.csv記録を追加（Phase C'のプロンプト改善素材）

## レビュー依頼

パターン1として、以下の観点でレビューしてください:

1. **チェッカー設計の網羅性**: Phase Aの4パターンがすべてカバーされているか。検出漏れの余地はないか
2. **チェッカーの実現可能性**: structure.jsonのメタデータのみで判定する前提は正しいか。E-4のpdfplumber再スキャンは適切か
3. **偽陽性リスク**: correct企業を誤ってフラグする可能性はないか。特にA-1/A-2のヒューリスティクス
4. **試行錯誤の罠**: Step 4→4.5→5→6のフローが一方向で収束するか。ループに入る余地はないか
5. **088計画との整合**: 1社ずつ丁寧に、パターン化禁止の方針とチェッカー（自動スキャン）は矛盾しないか

## 関連ファイル

- `C:\tmp\structure_qa\results.csv` — Phase A結果
- `docs/plans/tools-089-1_order_backlog_extraction_20260430_200000.md` — 親計画

---

## レビュー追記: 2026-05-02 13:18 JST — code-reviewer

→ `docs/reviews/056_cr_structure_qa_phase_b_checker.md`

# docs / scripts 大掃除プラン

**作成日時**: 2026-04-25 06:20 JST
**基準 commit**: `fe769e7`
**対象ファイル**: `docs/knowledges/` (113 MD, 13,800行), `docs/plans/` (36 MD, 9,269行), `scripts/` (312 .py, 91,177行), `data/csv/` (バックアップ等)
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: 肥大化したMDファイルの圧縮と不要スクリプトの削除。月次パイプラインのダウンロード・エクストラクトのエラー対応に有益な情報は保持し、一過性のセッション記録・調査メモ・実験コードを除去する

**スコープ外**:
- 本番スクリプト (`extract_monthly_data.py`, `download_monthly.py` 等) のリファクタリング
- adapter JSON の変更
- GCS / BQ データの操作

---

## 前提サマリ

- 過去の大掃除: なし（初回）
- 現状: docs/knowledges 113ファイル中10本が400行超（合計6,491行）。scripts/ に一時スクリプト約120本（_bg 49本, investigate 31本, poc 12本, *_copy 5本）。docs/plans 36本中完了済み多数
- 実機検証: 不要（ファイル削除・編集のみ）
- 関連 incident: なし

---

## 優先度の定義

- **P0**: 削除しても安全な一時ファイル・バックアップ（即実行可）
- **P1**: 知見MDの圧縮（将来のエラー対応に影響するため慎重に）
- **P2**: 完了済みプランの整理・アーカイブ

---

## P0-1. 不要スクリプトの削除 🚨

**症状**: scripts/ に312ファイル・91,177行。約120本は一過性の調査・実験・バックグラウンド処理で、完了後も残存。新規開発時にノイズとなり、grep結果を汚染する

**該当**: 以下のカテゴリ

### 削除対象A: _bg.py（バックグラウンド一過性処理）— 49本

agent_bc セッションや月次修正セッションで生成された1回限りの処理スクリプト。知見は042-1に集約済み。

```
scripts/*_bg.py                    (49本, 6,042行)
scripts/monthly_bc_repair/archive/*.py      (6本, 780行)
scripts/monthly_bc_repair/*_bg.py           (4本, ~400行)
```

**除外（残す）**: `scripts/monthly_bc_repair/` のコアツール6本（inspect_ticker.py, apply_adapter_patch.py, snapshot_adapter.py, reextract_and_compare.py, list_ng_queue.py, log_progress.py）+ `scripts/monthly_bc_repair/apply_23ng_d1_d4.py`（一過性だが削除対象Aに明示追加）

> **レビュー指摘反映**: `investigate_ticker_generic_bg.py` は `--ticker` 引数で任意銘柄の adapter/records/PDF を一括ダンプする汎用ツール。`monthly_bc_repair/inspect_ticker.py` と機能重複を確認し、重複なら削除可、なければ除外リストに追加すること

### 削除対象B: investigate_*.py（調査完了済み）— 31本

NG銘柄の個別調査用。調査結果は042-1知見・adapterのbc_ignore理由に反映済み。

```
scripts/investigate_*.py           (31本, 4,480行)
```

**除外（残す）**: `scripts/investigate_monthly_ng.py`（汎用調査ツール、070知見から参照）

### 削除対象C: *_copy.py / *コピー*.py（テスト用コピー）— 5本

Gemma4 PoC比較テスト時のファイルコピー。

```
scripts/gemma4_*_copy.py           (4本, ~1,600行)
scripts/update_conse_rakuten - コピー.py  (1本)
```

### 削除対象D: poc_*.py（実験完了済み）— 12本

分析実験スクリプト。結果はdocs/knowledges/analysis/ に知見として記録済み。

```
scripts/poc_*.py                   (12本, ~8,400行)
```

**除外（残す）**: `scripts/poc_gemma4_comparison.py`（CLAUDE.mdから参照、Gemma4 PoC比較の参照実装）

> **レビュー指摘反映**: `poc_gemma4_tpu_monthly_test.py` は `078_gemma4_operation.md:35` から参照されている。削除する場合は078の該当行も同時に削除すること（P1-1の圧縮作業に組み込む）

### 削除対象E: シェルスクリプト（一過性バッチ）— 4本

バックフィル・リカバリ用の一過性シェル。

```
scripts/backfill_2023_efg.sh
scripts/catchup_20260420.sh
scripts/chain_batch05_11_to_recovery.sh
scripts/recover_batch05_11_and_gap.sh
```

### 削除対象F: テストスクリプト（git未追跡・削除済み）

git statusで `D` マークのファイルがあった場合はそのまま削除確定。

### 削除対象G: data/csv バックアップ — 2本

```
data/csv/bc_monthly_kpi.csv.bak_20260417_124227  (12M)
data/csv/bc_monthly_kpi.csv.bak_20260419_183929  (12M)
```

### 削除対象H: apply_batch_fixes_round3.py — 1本

月次adapter一括修正の過去スクリプト。1,056行。修正パターンは042-1に集約済み。

**修正方針**: `git rm` で一括削除。削除前にリスト化してdry-run確認

**呼び出し側への波及**: なし（全て独立スクリプト。他スクリプトからimportされていない）

**検証**: `git rm --dry-run` で削除対象を確認 → import依存チェック（grep "from.*{script}" / "import.*{script}"）→ 実削除

**ロールバック**: `git revert <commit>` で完全復元可能

---

## P1-1. 知見MD圧縮 — 400行超の9ファイル ⚠️

**症状**: docs/knowledges/tools/ の9ファイルが400行超（合計6,491行）。セッション実績・過去調査ログ・一過性のデバッグ記録が蓄積し、本来の「恒久ルール + エラー対応手順」が埋もれている

**該当**: 以下9ファイル

| ファイル | 現行行数 | 目標行数 | 圧縮方針 |
|---------|---------|---------|----------|
| 042_monthly_disclosure_master.md | 1308 | ~600 | 一致率推移・完了済みTODO・作業タイムラインを削除。アーキテクチャ・adapter仕様・エラー対応フロー・`_extract_pdf_by_row`実装経緯(L1055-1072)・未解決NG銘柄の対処方針を残す |
| 013_tdnet_load.md | 1161 | ~600 | 旧アーキ詳細(L504〜: 5フェーズETL・旧実行コマンド)を削除。Cloud Run Jobリソース設計根拠(L147-172)・バックフィル投入時間帯(L304-315)・OOM対策・Phase I事故テーブル(L372-383)を残す |
| 059_earnings_model_eda.md | 771 | ~400 | ノートブック構成・スコアリング仕様・GCS保存ルールを残す。反省会ログは因子追加/廃止の契機となった行のみ残し、類似外れ例は削除 |
| 078_gemma4_operation.md | 583 | ~250 | TPU起動手順・Gemini Batch API仕様・コスト計算式を残す。PoC比較結果・実測値テーブルを圧縮 |
| 005_cloudrun_job_deploy.md | 507 | ~250 | デプロイコマンド・Dockerfile・環境変数を残す。過去デプロイ履歴を削除 |
| 042-1_bc_match_agent.md | 482 | ~350 | パターンA-Jカタログ・バッチ分類アプローチ・安全制約を残す。セッション実績の銘柄個別記録を圧縮 |
| 057_extract_adapter_feedback_backlog.md | 440 | ~200 | 一致率推移サマリ・未解決パターンを残す。過去セッションの銘柄別修正ログを削除 |
| 080_workflows_runbook.md | 431 | ~200 | Workflows YAML仕様・parallel設計・retry設定・トラブルシュート手順を残す。個別実行ログを削除 |
| 071_xbrl_to_jquants.md | 409 | ~200 | 変換仕様・逆引きテーブル・BQスキーマを残す。検証結果の全銘柄テーブルを圧縮 |

**圧縮の原則**:

1. **残すもの**: 恒久ルール、エラー対応フロー（特にDL/Extract失敗時）、コマンド集、設定値、仕様定義、パターンカタログ
2. **削除するもの**: セッション実績テーブル（日付+銘柄の組み合わせ）、過去のincident詳細経緯、デバッグ途中のメモ、一過性の数値（バックフィル件数等）
3. **判定基準**: 「3ヶ月後に別セッションで同種のエラーが起きたとき、この情報は役立つか？」— Yesなら残す、Noなら削除

**修正方針**: 1ファイルずつ Edit で圧縮。削除予定セクションをまず列挙し、`<!-- REMOVED: セクション名 -->` コメントに置換してから最終確認で完全削除

**呼び出し側への波及**: CLAUDE.md の知見ファイル一覧のdescriptionが変わる場合は同期更新

**検証**: 圧縮後に以下を確認
- CLAUDE.mdからの参照リンクが全て有効
- 各ファイルの目次（## 見出し）が論理的に完結
- エラー対応手順が完全に残っている

**ロールバック**: `git revert <commit>` で復元可能

---

## P1-2. docs/plans/ 完了済みプランの整理 ⚠️

**症状**: 36ファイル・9,269行。3月以前の完了済みプランが多数残存

**該当**: 以下は**削除対象**（完了済み・知見に反映済み）

| ファイル | 行数 | 理由 |
|---------|------|------|
| 20260324_*.md 〜 20260403_*.md | - | 3月の完了プラン |
| 20260404_monthly_pipeline_ng73.md | 58 | 完了済み |
| 20260405_ng84_investigation.md | 732 | NG84社調査完了。結果は042-1に反映 |
| 20260406_ng78_cloudrun_6a6b.md | 163 | 完了済み |
| 20260407_zaraba_watch_tdnet_xbrl.md | 85 | 完了済み |
| 20260408_adapter_fix_and_gemini3.md | 159 | 完了済み |
| 20260410_gemini_extraction_failure_analysis.md | 223 | 完了済み。知見に反映 |
| 20260413_zaraba_factor_sync.md | 94 | 完了済み |
| 20260418_monthly_bc_round2_followup.md | 426 | BC Round 2完了。結果は042-1に反映 |
| 20260419_bc_match_agent.md | - | 042-1の初期仕様書。知見に統合済み |
| 20260420_220000_session_log.md | 272 | 純粋なセッション作業記録 |
| 20260420_223000_monthly_adapter_master_plan.md | 355 | adapter修正完了 |
| 20260421_093823_monthly_pdf_strategy_integration.md | 385 | PDF戦略。062知見に反映済み |
| 20260421_094200_pdf_extraction_improvement.md | 325 | PDF改善完了。062知見に反映 |
| 20260421_094500_document_ai_poc_todo.md | 135 | PoC保留→実質完了 |
| 20260424_212000_docs_scripts_cleanup.md | 322 | 本プランの前版（取って代わり） |

**残すもの**:
- `20260424_200200_extract_adapter_quality.md` — 今回のPhase実績記録（参照価値あり）
- `20260417_091112_tdnet_load_ai_split.md` — AI分離アーキ（まだ統合テスト未完）
- `20260421_063341_tdnet_load_code_review.md` — コードレビュー結果（参照価値あり）
- `20260425_001000_tdnet_2024_gap_backfill.md` — 未着手バックフィル計画（参照価値あり）
- `20260425_062000_docs_scripts_cleanup.md` — 本プラン
- `_template_refactor.md` — テンプレート

**修正方針**: `git rm` で削除。知見への反映漏れがないか削除前に1ファイルずつ確認

**検証**: 削除後にCLAUDE.md・知見ファイルからの参照が壊れていないかgrep確認

**ロールバック**: `git revert`

---

## P2-1. data/csv/uki_predictor/ の巨大ファイル（715MB）

**症状**: UKI予測モデルの中間データが715MB。git追跡されていないが、ローカルディスク消費が大きい

**該当**:
```
data/csv/uki_predictor/tech_features_v2.parquet   (235M)
data/csv/uki_predictor/uki_test_result.csv         (211M)
data/csv/uki_predictor/uki_test_result_v2.csv      (145M)
data/csv/uki_predictor/tech_features.parquet       (120M)
```

**修正方針**: BQまたはGCSに退避済みか確認 → 退避済みならローカル削除。未退避なら退避後に削除

**検証**: `analysis/005_uki_predictor_high_low_20d.md` の参照パスを確認

---

## 対応アンチパターン

本プランはコード改修を含まないため、004/T-x/G-x への該当なし。全項目がファイル削除・ドキュメント編集のみ。

---

## レビュー指摘反映（2026-04-25 06:40 JST）

code-reviewer 品質評価: **B**。以下6点を反映済み:
1. 013_tdnet_load.md 圧縮目標 ~400→~600 に緩和（OOM設計根拠・バックフィル時間帯・事故テーブルは恒久情報）
2. investigate_ticker_generic_bg.py は inspect_ticker.py との重複確認後に判断
3. P1-2 削除対象を具体ファイル名16本に展開、残すものに2本追加
4. poc_gemma4_tpu_monthly_test.py 削除時は 078 の参照行も同時削除
5. 042 圧縮で `_extract_pdf_by_row` 実装経緯(L1055-1072)は残す
6. 059 反省会ログは因子追加/廃止の契機行のみ残す

---

## 検証戦略

1. **P0-1 smoke test**: `git rm --dry-run` でリスト化 → `grep -r "from.*{script}\|import.*{script}" scripts/` で依存チェック → 依存ゼロ確認後に実削除
2. **P1-1 圧縮検証**: 圧縮後の各MDをcat → エラー対応手順セクションが完全に残っていることを目視確認 → CLAUDE.mdからのリンク有効性チェック
3. **P1-2 削除前チェック**: 各プランの「結果」セクションが知見MDに反映済みか確認
4. **回収手順**: 全て `git revert` で復元可能

---

## 実行結果（2026-04-25 完了）

| Phase | コミット | 内容 |
|-------|---------|------|
| P0-1 | `98c50b1` | スクリプト101本削除（17,474行） + 078参照行修正 |
| P1-1 | `b7e70de` | 知見MD 9本圧縮（6,491→3,243行, -50%） |
| P1-2 | `a9e3a96` | 完了済みプラン14本削除（3,622行） |
| P2-1 | — | uki_predictor/ 715MB削除（untracked, コミット不要） |

**合計削減**: コード17,474行 + ドキュメント7,055行 = 24,529行削減 + 715MBディスク解放

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/tools/042-1_bc_match_agent.md`（主要参照先）
- 知見 MD: `docs/knowledges/tools/042_monthly_disclosure_master.md`（圧縮対象）
- フォーマット正本: `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット

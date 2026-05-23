# 作業計画: memory ディレクトリ整理実行（SO+CR統合）

**作成日時**: 2026-05-16 22:00 JST
**ステータス**: 完了（2026-05-17）
**分類**: (c) 一過性型
**親知見 MD**: 該当なし
**関連アイディアID**: -

---

## 目的

`C:\Users\zonekun\.claude\projects\G---------claude\memory\` 配下の82ファイルについて、189/190/192 の3レビュー統合分析に基づき、削除・CLAUDE.md昇格・知見MD移管・適正残留の4区分で整理する。

---

## 背景・動機

- **レビュー出典**: `docs/reviews/189_so_memory_restructure.md`（SO）、`docs/reviews/190_cr_memory_review.md`（CR）、`docs/reviews/192_so_memory_plan_final.md`（最終確認）
- **192 返却判定**: QF-2（CLAUDE.md行数）のみ見送り。他11件全採用
- **189 返却**: QF-1/SR-3の `adapter_fix_backlog`・`gcs_adapter_audit` 2件に異議あり（CR(190)の根拠不足指摘が正しい）

---

## 事前確認（必須・作業前に完了。未完了時はPhase開始禁止）

**P0 未完了時ルール**: P0-A/B/C のいずれかが完了できない場合、対応する Phase 1 対象をその回の削除リストから外し、別セッションで実施。削除前には必ず対象ファイルを `C:\tmp\memory_backup\` にコピーしてからファイル削除（不可逆操作のバッファ）。

- [x] **P0-0**: `New-Item -ItemType Directory -Force C:\tmp\memory_backup\` を実行してバックアップ先を作成（作業開始前に1回のみ）
- [x] **P0-A**: 14 ticker を 057 の §E に移記済み。削除OK
- [x] **P0-B**: 本文「完了: 2026-05-06 22:50, 66件」を確認。MEMORY.md記載51件は不正確なインデックス。削除OK
- [x] **P0-C**: earnings_model → GCS URIが059 line 350に確認済み→削除OK。ng84 → 教訓を057 §Fに移記済み→削除OK

---

## 作業ステップ

### Phase 1: 即時削除＋ MEMORY.md 索引エントリ同時削除

**ファイル削除と MEMORY.md 索引エントリ削除をアトミックに処理する（CR重大#1）**

| ファイル | 削除理由 |
|--------|---------|
| `project_next_session_todo.md` | 本文に「完了後削除すること」と明記。49日後も残存 |
| `project_twitter_url_pending.md` | 読み取り完了・保存先確認済み（完了）|
| `project_tdnet_embedding_cost_reduction.md` | 単価誤認の無効化案件。完了済み |
| `project_tdnet_gemma3_benchmark.md` | Gemma4本番確定で意義消失。190でも反論なし |
| `project_deferred_session_defects_log.md` | 知見MD反映完了済みと推定 |
| `project_bc_kpi_download.md` | 37日前。知見069移管済み。再開の具体的次アクション無し |
| `feedback_rule_with_change.md` | CLAUDE.md §4.3と完全一致。補記不要で削除のみ |
| `project_adapter_fix_backlog.md` | P0-A 完了後に追加 |
| `project_deferred_gcs_adapter_audit.md` | P0-B 完了後に追加 |
| `project_ng84_investigation_progress.md` | P0-C 完了後に追加（条件付き）|
| `project_earnings_model.md` | P0-C 完了後に追加（059突合後）|

- [x] P0-A/B/C 完了を確認してから当該行を削除リストへ追加
- [x] 各ファイルを `C:\tmp\memory_backup\` にコピー後に削除
- [x] ファイル削除と MEMORY.md 索引エントリ削除をセットで実施
- [x] MEMORY.md 孤立リンク確認（0件）

### Phase 2: CLAUDE.md 補記後削除（4件）

| ファイル | 追記先 | 内容 |
|--------|-------|------|
| `feedback_python_execution.md` | §6 確認のみ | PYTHONUTF8=1 は115行目に既記載確認済み → 追記不要。確認のみ → 削除 |
| `feedback_wait_for_go.md` | §4.5 確認 | MR-120拡張の補記が必要か確認後削除（190指摘でPhase 1→Phase 2に移動）|
| `feedback_git_bash.md` | §6 | 内容 Read して差分のみ追記 |
| `feedback_local_download_dir.md` | §6 | 「検証用ダウンロードはCドライブ」1行追記後削除 |

- [x] 各ファイルを Read してCLAUDE.md 既存記述と差分を確認
- [x] 差分のみ追記（git_bash→OS行にmkdir-p追記、local_download_dir→ダウンロード先1行追記）
- [x] CLAUDE.md 追記後、memory ファイル削除 + MEMORY.md索引エントリ削除
- [x] MEMORY.md 孤立リンク確認（0件）

### Phase 3: What NOT to save 対象を知見MD移記後削除

**Phase 3 実施前に適正残留 feedback 系を What NOT to save 基準（Code patterns/conventions/architecture）でスキャンし、追加移管対象があれば本Phaseに加える**

| ファイル | 移記先知見MD |
|--------|------------|
| `feedback_bq_reserved_words.md` | `docs/knowledges/api/002_bigquery.md` |
| `feedback_jupyterlab_graphs.md` | `docs/knowledges/tools/004_coding_conventions.md` |
| `feedback_cross_platform_commands.md` | `docs/knowledges/tools/004_coding_conventions.md` |
| `feedback_cloudrun_local_llm.md` | Read して陳腐化度合いを確認。obsolete なら削除、有効なら005または004に移記 |
| `feedback_jquants_v2_only.md` | api/ 配下の該当知見MD |
| `feedback_pdf_processing_strategy.md` | `013_tdnet_load.md` §PDF処理戦略 |

- [x] 各ファイルを Read して移記内容確認
- [x] What NOT to save スキャン実施（追加対象なし）
- [x] 移記: bq_reserved_words→002追記、cross_platform→004追記、pdf_processing→013追記。jupyterlab/jquants/cloudrun_local_llm は既記載or陳腐化で追記スキップ
- [x] memory ファイル削除 + MEMORY.md索引エントリ削除
- [x] MEMORY.md 孤立リンク確認（0件）

### Phase 4: 知見MD移管（reference系、3件）

| ファイル | 移管先 | CLAUDE.md §10 索引追加 |
|--------|-------|----------------------|
| `reference_claude_json_backup.md` | 新規 `docs/knowledges/tools/claudecode_recovery.md` | **必須**（緊急復旧のため高頻度参照テーブルへ）|
| `reference_claude_high_vm.md` | `046_gcp_mcp_server.md` or 新規VMスペックMD | 要判断 |
| `reference_claude_high_vm_ssh.md` | 同上（VMスペック+SSHコマンドは同一MD）| 要判断 |

- [x] `reference_claude_json_backup.md` を Read して内容確認
- [x] `docs/knowledges/tools/claudecode_recovery.md` を新規作成
- [x] CLAUDE.md §10 に `claudecode_recovery.md` エントリ追加
- [x] VM参照ファイル: 051_windows_linux_vm_guide.md に完全カバー済み → 追記不要・削除のみ
- [x] memory ファイル削除 + MEMORY.md索引エントリ削除
- [x] MEMORY.md 孤立リンク確認（0件）

### Phase 5: 運用ルール整備

- [x] CLAUDE.md §4.3 に feedbackライフサイクルルール1行追記
- [x] `MAINTENANCE.md` 作成（3ヶ月ごとの棚卸し手順）

---

## 成果物

- 整理後の memory ディレクトリ（削除・移管実施済み）
- 孤立リンクなしの MEMORY.md
- 更新済み CLAUDE.md（§4.3補記 + §10索引追加）
- 新規 `docs/knowledges/tools/claudecode_recovery.md`
- 更新済み各知見MD（002/004/013等）
- `MAINTENANCE.md`（memory GCルール）

---

## 完了条件

1. Phase 1〜5 の全チェックボックスが `[x]`
2. MEMORY.md 内に削除済みファイルへの孤立リンクが存在しない
3. CLAUDE.md §10 に `claudecode_recovery.md` エントリが追加されている
4. P0-A/B/C の確認結果が `C:\tmp\memory_backup\` に記録されている

---

## 見積もり

- 想定所要時間: 2〜3時間（Phase 1: 40分、Phase 2: 30分、Phase 3: 45分、Phase 4: 30分、Phase 5: 15分）
- 難易度: 低〜中
- ロールバック: memory ファイルは git管理外のため削除は不可逆。`C:\tmp\memory_backup\` コピーが唯一の保険

---

## レビュー履歴

- 2026-05-16: `docs/reviews/192_so_memory_plan_final.md`（structure-optimizer）→ 返却済み（QF-2のみ見送り・他全採用）

---

## 振り返り（作業後に記入）

- 実際の所要時間: 約3時間（セッション圧縮をまたいで実施）
- うまくいった点: P0確認でCRの指摘（14 ticker・66vs51件）が正しく、SOより深い検証が機能した。孤立リンク確認をGrep明示で徹底できた
- 改善点: jupyterlab_graphs/jquants_v2のように「既記載」で追記スキップになるケースは、事前にGrepで確認してから計画に含めると工数削減できる
- 得られた知見: feedbackメモリの蓄積原因は「CLAUDE.md gap-fill device」化。lifecycle rule（反映→即削除）を §4.3 に入れたことで再発防止できる。3ヶ月棚卸しサイクルをMAINTENANCE.mdに記録

---

## レビュー追記: 2026-05-16 22:30 JST — code-reviewer

→ `docs/reviews/194_cr_memory_plan_impl.md`

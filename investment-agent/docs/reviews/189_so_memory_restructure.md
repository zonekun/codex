# 構造最適化: memory ディレクトリ保管先切り分け

- 日時: 2026-05-16
- 対象: `C:\Users\zonekun\.claude\projects\G---------claude\memory\` (82ファイル)
- 分析軸: memory残留 / CLAUDE.md昇格 / 知見MD移管 / 削除
- スキル: structure-optimizer

---

## サマリー

- 総ファイル数: 82
- feedback系: 約46件
- project系: 約29件
- reference系: 3件
- 特殊系: 1件 (line_conversation_mode.md)
- MEMORY.md (索引): 1件

| 分類 | 件数 | 割合 |
|------|------|------|
| memory残留 (適正) | 25 | 30% |
| 即時削除 (stale) | 18 | 22% |
| 知見MD移管 | 12 | 15% |
| CLAUDE.md昇格候補 | 5 | 6% |
| 陳腐化要確認 | 22 | 27% |

**根本問題**: feedback系は「CLAUDE.mdに記載すべきルール」の複製・補足として増殖している。project系は「完了後も削除されない」慢性化が起きている。memory自体のGC（ガベージコレクション）サイクルが機能していない。

---

## Quick Fix（削除 or 即時整理）

### QF-1: 完了済みプロジェクトの即時削除 — 影響度: 低

以下は「完了」が明記されており、次アクションが「別セッションで」以上に具体性がないため削除対象:

| ファイル | 完了根拠 |
|---------|---------|
| `project_deferred_gcs_adapter_audit.md` | 「完了: 2026-05-06 22:50」明記 |
| `project_twitter_url_pending.md` | 「読み取り完了」「保存先確認済み」— タイトルが「pending」だが完了している |
| `project_tdnet_embedding_cost_reduction.md` | 「単価誤認で無効化」— 完了ではなく「不要になった」 |
| `project_ng84_investigation_progress.md` | 調査完了・分類確定。修正作業は別プロジェクト扱いで知見化済み |
| `project_tdnet_gemma3_benchmark.md` | Gemma4本番アーキが確定し、gemma3ベンチは意義消失。知見は使われていない |
| `project_next_session_todo.md` | 49日前作成。「完了したら削除」と本文に記載あり。未削除 |
| `project_bc_kpi_download.md` | 37日前。停止中だが再開の具体的次アクション無し。知見 069 に移管済み |

**処置**: 7ファイル削除。ユーザーに確認後、削除を実行する。

### QF-2: MEMORY.md に「完了」マーク済みだがファイルが残っているケース — 影響度: 低

MEMORY.md内の以下エントリには取り消し線 (~~) があるが、ファイル本体が残っている可能性:

- ~~決算反応モデル 新因子候補~~: `project_earnings_model.md` は 48日前に作成。今後は `059_earnings_model_eda.md` に委譲済みとMEMORY.mdに記載。ファイルは削除候補。

### QF-3: 「project系」をフラット管理している設計問題 — 影響度: 低

MEMORY.mdのproject欄に「feedback」型のものが混入している:
- `feedback_yoy_offset_not_needed.md` がproject欄に掲載されている。feedback欄に移動または削除する。

---

## Scoped Refactor（移管提案）

### SR-1: CLAUDE.md高頻度参照テーブルへの昇格候補 — 影響度: 中

以下のfeedbackは「毎タスクで必要・迷走防止効果が高い・現在CLAUDE.mdに明記がない or 埋もれている」もの。現在はmemory経由でのみ到達可能（中到達可能性）。

| ファイル | 昇格理由 | 推奨掲載先 |
|---------|---------|-----------|
| `feedback_python_execution.md` | venv外インタープリタ起動によるModuleNotFoundErrorが繰り返し発生。CLAUDE.md §6 実行環境に記載あるが「uv run」のみで `PYTHONUTF8=1` が抜けている | CLAUDE.md §6 既存行に補記 |
| `feedback_git_bash.md` | `python3` 禁止・パス表記の注意がCLAUDE.md §6に記載ありで重複。**ただし「mkdir -pが効かない」は未掲載** | CLAUDE.md §6 に1行補記後、memory削除 |
| `feedback_local_download_dir.md` | Cドライブ使用ルール。CLAUDE.md §6「その他の規約」に委譲されているが、具体的パスが未掲載。GDriveに誤保存する事故防止に高効果 | CLAUDE.md §6 に1行追加後、memory削除 |

**昇格不要（CLAUDE.md §4.5マルチターン待機に既記載）**:
- `feedback_wait_for_go.md`: CLAUDE.md §4.5に明文化済み。memoryとCLAUDE.mdで重複。memory削除候補。

### SR-2: docs/knowledges/ へ移管すべきfeedback — 影響度: 中

「別端末でも必要か?」がYesで、かつ詳細手順・設計判断を含むもの:

| ファイル | 移管先 | 理由 |
|---------|-------|------|
| `feedback_pdf_processing_strategy.md` | `docs/knowledges/tools/013_tdnet_load.md` §PDF処理戦略 または 新規 `docs/knowledges/tools/013-1_pdf_processing.md` | PyMuPDF/pdfplumber/OCRmyPDFの使い分けは開発者判断基準。プロジェクトの取り決め |
| `feedback_cloudrun_local_llm.md` | `docs/knowledges/tools/074_vertex_ai_cost_analysis.md` §ローカルLLM判断基準 | GPU単価・コスト閾値はデータとして知見MD向き。47日前で陳腐化リスク |
| `feedback_monthly_categorization.md` | `docs/knowledges/tools/013_tdnet_load.md` §分類責務 | tdnet_load_parallel.pyの設計思想。コード規約に近い |
| `feedback_adapter_rebuild_snapshot.md` | `docs/knowledges/tools/057_extract_adapter_feedback_backlog.md` または `056_compare_monthly_buffett.md` | GCSスナップショット手順は知見MD向き |
| `feedback_cross_platform_commands.md` | `docs/knowledges/tools/004_coding_conventions.md` §クロスプラットフォーム | Windowsと Linux VM両対応の規約。55日前でpython3→python置換パターンはfeedback_python_executionと重複 |
| `feedback_jquants_v2_only.md` | `docs/knowledges/api/` 配下の新規 or 既存ファイル | APIバージョン制約は外部仕様。変更されると自動的に陳腐化するため知見MDで管理 |

### SR-3: project系の知見MD移管と削除 — 影響度: 中

大規模プロジェクトの詳細が project_*.md に滞留している。完了後も削除されず「作業ログ」化している:

| ファイル | 状態 | 処置 |
|---------|------|------|
| `project_tdnet_backfill.md` | 2017-2022全完了と記載。「残」チェックボックスが混在（task-timeout戻し/Geminiスキップ恒久化判断/Schedulerは未確認） | 残タスクのみ `project_tdnet_load_ai_split.md` に統合してから削除 |
| `project_tdnet_load_ai_split.md` | Phase I完遂。残タスク(1)〜(5)あり | 残タスクが具体的なため保留。ただしタスク完了後は速やかに削除する義務を記載 |
| `project_xbrl_to_jquants.md` | 本番変換完了。次アクションはBS項目追加・iXBRL予想値(優先度低) | アーキテクチャ詳細・技術メモは知見MDへ。TODO 2-4は計画MDに。memory保持は現状ポインタのみで十分 |
| `project_gemma4_production_architecture.md` | 確定済み。詳細は知見MD(074-1)参照と記載 | 知見MDに詳細ありならmemoryは1行ポインタで十分。現在の14行は過剰 |
| `project_ng84_investigation_progress.md` | 調査完了。C:\tmpのGCSバックアップ・修正着手が「次のアクション」だが39日前 | 完了フェーズと判断し削除。未着手修正は別プロジェクトMDまたは知見バックログに |
| `project_adapter_fix_backlog.md` | 45日前。BC突合が88.7%→100%に改善済み(project_monthly_pipeline_statusより) | staleな詳細データ。月次パイプラインが進展しており、内容の大半が陳腐化 |
| `project_monthly_pipeline_status.md` | 21日前。Phase 1-3完了・NG=0。bc_ignore 202件が次Phase | 現在進行中のため保留。ただし「月次パイプライン進捗(04-25)」と `project_monthly_deploy_20260425.md` が重複 |

### SR-4: reference系の整理 — 影響度: 低

| ファイル | 状態 | 処置 |
|---------|------|------|
| `reference_claude_high_vm.md` | スペック情報。「SSHコマンドは051知見MD参照」と正しく委譲している | 保留。ただし014行のスペック表は知見MD移管後に削除可 |
| `reference_claude_high_vm_ssh.md` | SSH接続コマンドの即実行用。コマンドをmemoryに持つパターン(過去事故原因) | `feedback_memory_usage.md` が「正本は知見MD、memoryにはポインタだけ」と明記。このファイル自体が違反。要検討 |
| `reference_claude_json_backup.md` | 破損時の復旧手順。詳細な手順MDとして適切な情報量 | 知見MD移管推奨。`docs/knowledges/tools/` 配下に `claude_code_recovery.md` 等で保管すべき |

---

## Rebuild（memory運用ルール自体の改善）

### RD-1: feedbackが増える根本原因と予防策 — 影響度: 高

**根本原因の分析**:

1. **feedback = 「CLAUDE.mdに書き損じたルール」の補完装置になっている**
   - CLAUDE.md §4「基本原則」に書くべきことが、事故→feedback→memoryという回り道で保管されている
   - 例: `feedback_wait_for_go.md` の内容はCLAUDE.md §4.5に既に存在。二重管理
   - 例: `feedback_python_execution.md` はCLAUDE.md §6に`uv run`が記載されているが、`PYTHONUTF8=1`が抜けていたため補完

2. **feedbackのライフサイクル管理が未定義**
   - CLAUDE.md §4.3「永続化・記録」に「メモリ保存は厳禁」と書いてあるが、feedbackは例外扱いされている
   - 一度feedbackになったものがCLAUDE.mdに反映されても、memory側が削除されない
   - 結果: CLAUDE.md ↔ memory 重複が慢性化

3. **project系の「完了後削除」が機能していない**
   - `project_next_session_todo.md` 本文には「完了したら本ファイルを削除すること」と書かれているが、49日後も残存
   - 完了トリガーがAIの判断に委ねられており、構造的に削除が走らない

**予防的ルール案（CLAUDE.md §4.3への追記候補）**:

```
feedbackのライフサイクル:
- 新規feedbackはCLAUDE.md §4〜7 の該当ルールへ反映後、memoryから削除する
- 既存feedbackが「CLAUDE.mdに記載済み」と判定されたら即時削除
- projectメモリは「次のアクション」が未定義になった時点で削除。完了宣言時は必ず削除

セッション末尾チェックリスト（3ヶ月に1回程度）:
1. 完了済みproject系 → 削除
2. CLAUDE.md重複feedback → 削除  
3. 30日以上更新のないproject → stale確認
```

### RD-2: memory量爆発の構造的問題 — 影響度: 高

**現状の問題**:
- Claude Codeは全memoryをコンテキストに読み込む（system-reminderとして注入）
- 82ファイル×平均20行 = 約1,640行のコンテキスト消費
- MEMORY.md自体は80行で索引として機能しているが、ファイル実体の大半がコンテキスト外
- 「到達可能性」の観点で、個別ファイルへの到達は「MEMORY.mdを読んでからリンクを辿る」という2ステップが必要

**AIの迷走リスク**:
- project系の詳細（project_tdnet_load_ai_split.md の100行など）がmemory内にあると、AIが古い状態を参照して誤った判断をするリスクがある（system-reminderの陳腐化警告が示す通り）
- 「62日前」の feedback_git_bash.md が現役として認識される

**改善方向**:

memoryを「索引 + 最小ポインタ」に絞り込む三層構造:

```
Layer 1: MEMORY.md (索引)
  - feedback: 1行サマリー + 知見MDポインタ（feedbackは知見MDに昇格後削除）
  - project: 1行ステータス + プランMDポインタ（詳細はプランMDに）
  - reference: ポインタのみ（詳細は知見MDに）

Layer 2: docs/knowledges/ (恒常知識)
  - フィードバックから昇格したルール
  - API仕様・設計判断の詳細

Layer 3: docs/plans/ (揮発的作業状態)
  - プロジェクト進捗の詳細
  - 完了後は memory から削除、プランMD自体は保管継続
```

### RD-3: line_conversation_mode.md の特殊性 — 影響度: 中

`line_conversation_mode.md` は唯一「リアルタイム状態」を保管するmemory。

現在の内容:
```
active: false
timeout: 10800
deactivated_at: 2026-05-14T19:40:00+09:00
```

これは「別端末でも必要」なためmemoryに保管する正当性がある。CLAUDE.md §5「LINE会話モード」に「memory `line_conversation_mode.md` を必ずチェック」と明記されており、到達可能性は「高（CLAUDE.md経由）」。現状維持が正しい。

ただし timeout: 10800 は `feedback_line_timeout_from_memory.md` の「ハードコードせずmemory参照」ルールで参照されるが、このfeedbackは知見MD 068 に移管した上で、line_conversation_mode.md の中に timeout コメントとして統合できる。

---

## 導線検証

移管・削除対象の到達可能性評価（移管先の導線確認）:

| 対象ファイル | 現在の読み取り到達可能性 | 移管後の書き込みトリガー | 移管先の到達可能性 | 必要なアクション |
|------------|----------------------|----------------------|-----------------|----------------|
| `feedback_python_execution.md` | 中（MEMORY.md経由） | CLAUDE.md §6更新時 | 高（CLAUDE.md常駐） | CLAUDE.md §6に `PYTHONUTF8=1` を明記してmemory削除 |
| `feedback_wait_for_go.md` | 中（MEMORY.md経由） | CLAUDE.md §4.5更新時 | 高（CLAUDE.md §4.5に既記載） | CLAUDE.md確認後memory削除 |
| `feedback_cross_platform_commands.md` | 中（MEMORY.md経由） | 004規約更新時 | 中（INDEX.md→004規約） | 004_coding_conventions.mdに追記後memory削除 |
| `feedback_jquants_v2_only.md` | 中（MEMORY.md経由） | API仕様変更時 | 中（INDEX.md→api/） | api/配下の知見MDに追記後memory削除 |
| `project_adapter_fix_backlog.md` | 中（MEMORY.md経由） | 月次パイプライン改善時 | 中（INDEX.md→056/057知見MD） | 057バックログMDに統合後memory削除 |
| `reference_claude_json_backup.md` | 中（MEMORY.md経由） | Claude Code破損時 | 低（索引未掲載） | 知見MD作成後 CLAUDE.md §10 索引に追加が必要 |
| `project_next_session_todo.md` | 中（MEMORY.md経由） | 完了時（未実行） | 不到達（削除対象） | 確認後削除 |

**重要所見**: `reference_claude_json_backup.md` は「Claude Code破損」という緊急時対応のため、知見MD移管後もCLAUDE.md §10の索引に「Claude Code復旧」等のキーワードで登録しないと到達可能性が「低」になり、緊急時に参照できない。移管時は必ず索引登録とセットにすること。

---

## 推奨実行順序

### Phase 1: 即時削除（確認後）

1. `project_next_session_todo.md` — 本文に「完了後削除」指示あり
2. `project_deferred_gcs_adapter_audit.md` — 完了明記
3. `project_twitter_url_pending.md` — 完了（ファイル名が誤解を招く）
4. `project_tdnet_embedding_cost_reduction.md` — 不要になった案件
5. `project_tdnet_gemma3_benchmark.md` — Gemma4確定で意義消失
6. `project_earnings_model.md` — MEMORY.mdで取り消し線あり、059知見MDに委譲済み
7. `feedback_wait_for_go.md` — CLAUDE.md §4.5に重複記載

### Phase 2: CLAUDE.md補記後にmemory削除

8. `feedback_python_execution.md` → CLAUDE.md §6の `uv run python` 行に `PYTHONUTF8=1` を追記後削除
9. `feedback_git_bash.md` → CLAUDE.md §6 に `mkdir -p` の注意を追記後削除（`python3`禁止は既記載）
10. `feedback_local_download_dir.md` → CLAUDE.md §6 に「検証用ダウンロードはCドライブ」1行追記後削除

### Phase 3: 知見MD移管（次セッション以降）

11. `feedback_cross_platform_commands.md` → `004_coding_conventions.md` §クロスプラットフォーム
12. `feedback_jquants_v2_only.md` → api/ 配下の知見MD
13. `feedback_pdf_processing_strategy.md` → `013_tdnet_load.md` §PDF処理戦略
14. `feedback_cloudrun_local_llm.md` → `074_vertex_ai_cost_analysis.md`
15. `reference_claude_json_backup.md` → 新規 `docs/knowledges/tools/claudecode_recovery.md` + CLAUDE.md §10索引追加

### Phase 4: 要確認（ユーザー判断）

16. `project_ng84_investigation_progress.md` — C:\tmp GCSバックアップの要否確認後
17. `project_adapter_fix_backlog.md` — 057バックログMD内容確認後
18. `project_tdnet_backfill.md` — 残タスク(Scheduler化等)のステータス確認後
19. `reference_claude_high_vm_ssh.md` — 「コマンドをmemoryに持つ」パターン議論

---

## 付記: memory残留として適正なもの（変更不要）

以下は「セッション跨ぎの行動ルール」として正当性がある:

**feedback系（適正・残留）**: `feedback_claudemd_index_first.md`, `feedback_bq_query_cost.md`, `feedback_bq_query_count.md`, `feedback_check_timestamps.md`, `feedback_cancel_and_resubmit.md`, `feedback_verify_before_completion_report.md`, `feedback_no_shortcut_investigation.md`, `feedback_data_catalog_first.md`, `feedback_memory_usage.md`, `feedback_recall_check_md.md`, `feedback_rule_with_change.md`, `feedback_proactive_plan_md_update.md`, `feedback_gcp_md_sync.md`, `feedback_ntfy_foreground_only.md`, `feedback_backfill_metrics_auto.md`, `feedback_screen_line_dual_output.md`, `feedback_bq_access_minimize.md`, `feedback_review_scope.md`, `feedback_codex_review_trust.md`, `feedback_no_false_positive_assumption.md`, `feedback_table_format.md`, `feedback_no_write_for_copy.md`

**project系（残留・アクティブ）**: `project_tob_ml_feature_cleanup.md`, `project_yutai_v2_plan.md`, `project_tdnet_dl_prev_day.md` (デプロイ残あり), `project_deferred_session_defects_log.md`, `project_next_task_008_tob.md`, `project_bq_consensus_restructure.md`, `project_2019_backfill_handoff.md` (2022年投入残あり)

**特殊**: `line_conversation_mode.md` (リアルタイム状態・適正)

---

## 返却 2026-05-16

- QF-1 (`project_adapter_fix_backlog.md` 行): [異議あり] CR(190)にて14 ticker残タスクが存在すると判明。「陳腐化」断定は根拠不足。P0-A事前確認（057突合）に変更
- QF-1 (`project_deferred_gcs_adapter_audit.md` 行): [異議あり] CR(190)にてファイル本文66件とMEMORY.md記述51件の食い違いが判明。「完了確認済み」断定は根拠不足。P0-B事前確認に変更
- QF-2: [採用]
- QF-3: [採用]
- SR-1 (`feedback_wait_for_go.md` のPhase 1即時削除推奨): [見送り] CR(190)にてMR-120拡張の補記確認が必要と判明。Phase 2（確認後削除）に変更
- SR-1 (昇格候補 残3件): [採用]
- SR-2 (`feedback_cloudrun_local_llm.md` の074移管先指定): [見送り] 192 SR-1にて「Readして移管 or 削除を判断」に変更
- SR-2 (残): [採用]
- SR-3 (`project_adapter_fix_backlog.md` の陳腐化断定): [異議あり] QF-1と同一理由。P0-A確認後削除に変更
- SR-3 (残): [採用]
- SR-4: [採用]
- RD-1: [採用]
- RD-2: [採用（Phase 5のMAINTENANCE.mdとセットで実施）]
- RD-3: [採用]

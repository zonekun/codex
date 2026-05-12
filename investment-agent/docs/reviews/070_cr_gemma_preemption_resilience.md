# コードレビュー: Gemma TPU プリエンプション耐性改善プラン

- 日時: 2026-05-05 07:37 JST
- 対象: `docs/plans/tools-013_gemma_preemption_resilience_20260504_212000.md`
- パターン: 2 (改修) — `_template_refactor.md` フォーマット準拠のため
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: Spot TPU プリエンプションによる Gemma 推論バックフィルの手動介入を削減するため、(1) cross-execution checkpoint 引継ぎ機構（`resume_run_id`）の追加と (2) retry 上限の 3→7 増加を提案
- 品質評価: **A** — 実運用事故（2021-H1 で ~13h ロス）に基づく具体的な改修提案。before/after・波及箇所・検証戦略が網羅的。重大な設計上の穴が1点あるが全体として高品質
- 主要リスク:
  1. `resume_run_id` で旧 checkpoint をコピーした直後、`gcs_download_current` が同一ファイルを二重読みする構造上の冗長性
  2. `gemma_tpu_runner.sh` が `RESUME_RUN_ID` を worker に渡す具体的な修正コードが不足
  3. retry 7回 × backoff で最大 ~2.5h の retry 待機が発生し、Workflows `connector_params.timeout: 14400` (4h) に対してマージンが薄い

---

## 【改修プラン評価】

### フォーマット適合性チェック

- [x] 冒頭に対象ファイルの基準 commit hash が書かれているか — **OK**: `commit 0d04fd8`
- [x] 前提サマリで過去修正と残件数が明示されているか — **OK**: 既存 retry/checkpoint の説明あり
- [x] 優先度の定義（P0/P1/P2 昇格基準）が冒頭にあるか — **OK**
- [x] 各項目が 7 フィールド（症状/該当/根本原因/修正方針/呼び出し側波及/検証/ロールバック）を揃えているか — **OK**（P1-2 はデプロイ手順のため一部省略だが妥当）
- [x] 修正方針に before/after の両方が書かれているか — **OK**
- [x] 呼び出し側への波及が該当行リストで明示されているか — **OK**: L28, L48-L53, L199, L235, L29 を具体的に列挙
- [x] 「既に〜がある」系の前提記述を実コードと照合し、食い違いが無いか — **下記で詳細検証**
- [x] アンチパターン対応表（plan ID → 004/T/G）が末尾にあるか — **OK**
- [x] 検証戦略が smoke / dev / prod / 回収手順の 4 段を網羅しているか — **OK**
- [x] ロールバック手順が書かれているか — **OK**
- [x] 読みづらさ・デッドコードだけで P0 に置かれている項目が無いか — **OK**（P0 は実害ベース）
- [x] 関連 commit・知見 MD・incident ログへのリンクがあるか — **OK**: 5件のリンクあり

**フォーマット違反**: なし

### 妥当性

**独立仮説**: 問題の本質は「Workflows execution 境界を跨ぐと GCS checkpoint パスが変わるため推論結果が引き継がれない」こと。execution ID をパスに使う設計自体は合理的（同一 execution 内の retry で checkpoint が効く）だが、execution FAILED → 手動再投入のケースを想定していなかった。

**方向性照合**: プランの `resume_run_id` アプローチは、パス設計を変えずに「旧パスから新パスへのコピー」で解決するもの。根本原因（パスが execution ID に紐付く）に対して正しい方向。パス設計自体を変える（例: date_range ベースのパスにする）代替案もあり得るが、既存の checkpoint・resume 機構との互換を維持する点で `resume_run_id` 方式が妥当。

**対症療法パターン検出**: 該当なし。checkpoint コピーは真因に対する正攻法。

### 副作用・デグレードチェック

- [x] **通常実行への影響**: `RESUME_RUN_ID` が空文字の場合は if ブロックをスキップし、既存の `gcs_download_current` に到達する。後方互換性あり
- [x] **state.json の整合性**: resume 時に旧 execution の `gemma_CURRENT.jsonl` のみコピーし、`state.json` は新 execution のものを使う設計。state.json の `docs` リストが旧 execution と異なる場合（例: date_range が変更された場合）、旧 checkpoint の doc_id が新 state.json に存在しないケースが生じるが、done_ids でスキップされるだけで実害なし
- [x] **`_SUCCESS` マーカーの誤引継ぎ**: resume 時に `_SUCCESS` はコピー対象外（`gemma_CURRENT.jsonl` のみ）。正常
- [x] **既存の `gcs_download_current()` との二重読み**: P0-1 after コードで、RESUME_RUN_ID コピー後に `gcs_push_current()` で新パスに push → 直後に `gcs_download_current()` が新パスから再度 DL。実害はない（同一ファイルを読むだけ）が冗長。→ 改善提案 #1

### 抜け漏れ（類似観点での横展開含む）

- [x] **`state.json` の cross-execution 引継ぎは不要か**: 確認済み。`state.json` は ai-prepare ジョブが生成するため、新 execution でも同一 date_range なら同じ内容が生成される。引継ぎ不要は正しい
- [x] **Workflows `connector_params.timeout` との整合**: P1-1 で retry を 7 に増やすと、最悪ケースで全 retry が backoff 上限 (900s) に達した場合の合計待機時間が増大する。ただし retry はジョブ実行も含むため、timeout 14400s (4h) 内に 8 回試行するのは十分可能 → 問題なし
- [ ] **`gemma_tpu_runner.sh` の RESUME_RUN_ID passthrough 修正コード**: プランが L235 と L29 に言及しているが、before/after のコード片が shell script 部分のみ省略されている。runner.sh の WORKER_CMD ブロック (L231-L240) で `export RESUME_RUN_ID='${RESUME_RUN_ID}'` を追加する必要があるが、具体的な before/after が示されていない → 重大な指摘 #1
- [ ] **`cloudbuild.tdnet-gemma-runner.yaml` の存在確認**: P1-2 でデプロイコマンドが記載されているが、このファイルが実在するかプランからは確認不能 → 確認できなかった事項に記載

### 新規リスク

- **GCS コスト**: `resume_run_id` 使用時に `gcloud storage cp` が追加で 1 回発生するが、`gemma_CURRENT.jsonl` は数十 MB 程度のため無視可能
- **誤った `resume_run_id` 指定**: ユーザーが無関係な execution ID を指定した場合、他の date_range の checkpoint を読み込み、done_ids が誤マッチして本来処理すべき doc をスキップする可能性 → 重大な指摘 #2
- **retry 7 回の課金影響**: 各 retry で TPU 作成→削除が走る（runner.sh の EXIT trap）。8 回 × TPU 起動コスト (~$3-5/回) は許容範囲だが認識しておくべき

---

## 【重大な指摘】（即修正）

### #1 `gemma_tpu_runner.sh` の before/after コード欠落

- 箇所: プラン P0-1 呼び出し側波及の `scripts/gemma_tpu_runner.sh:L235` および `L29`
- 事象: プランは runner.sh への修正を「L235 に `export RESUME_RUN_ID` 追加」「L29 に `: "${RESUME_RUN_ID:-}"` 追加」と文章で述べているが、before/after のコード片が示されていない。他の修正箇所（workflow YAML、worker.py）はすべて before/after が記載されており、runner.sh のみ欠落
- トリガー: 実装者がプランを見て runner.sh を修正する際、正確な挿入位置と既存コードの文脈を確認するのに追加調査が必要になる
- 影響: 実装遅延リスク。致命的ではないが、プランの完全性が損なわれている
- 根拠: プランのフォーマット要件「修正方針に before/after の両方が書かれているか」に対する部分違反
- 推奨対応: runner.sh L231-L240 の WORKER_CMD ブロック内に `export RESUME_RUN_ID='${RESUME_RUN_ID}'` を追加する before/after、および L29 付近の env 宣言部に `: "${RESUME_RUN_ID:-}"` を追加する before/after を記載する

### #2 誤った `resume_run_id` による doc_id 誤スキップ

- 箇所: プラン P0-1 after コード `gemma_tpu_worker.py` — resume ロジック
- 事象: `resume_run_id` に無関係な execution の ID（例: 別の date_range で実行した execution）を誤って指定した場合、その execution の `gemma_CURRENT.jsonl` から読み込んだ doc_id が done_ids に入り、新 execution で本来処理すべき doc がスキップされる。doc_id はグローバルにユニークなため「たまたま同じ doc_id があった」場合のみ発火するが、同一 ticker の再実行などで十分起こり得る
- トリガー: ユーザーが `resume_run_id` に誤った execution ID を手動入力
- 影響: 推論結果の欠損（一部 doc が未処理のまま _SUCCESS になる）
- 根拠: `done_ids` は doc_id の集合であり、date_range の整合性チェックがない。旧 checkpoint の `run_id` と新 execution の `state.json` の `run_id` が一致するか検証するロジックがない
- 推奨対応: resume 時に旧 checkpoint の先頭数行から run_id を抽出し、state.json との date_range/doc_id 範囲の整合性を簡易チェック（完全一致でなく重複率閾値）するか、少なくとも `state.json` の `run_id` と `resume_run_id` の不一致を WARNING ログ出力して操作者に注意喚起する

### #3 Workflow YAML の `resume_run_id` 入力抽出の位置

- 箇所: プラン P0-1 after コード `ai_processing_flow.yaml` — `extract_resume_run_id` ステップ
- 事象: プランは `extract_input の後に追加` と記載しているが、実際の YAML には `extract_input` → `extract_recent_only` → `extract_ticker_range` → `resolve_date_range` → ... と続く。`extract_resume_run_id` の正確な挿入位置（どのステップの後か）が曖昧。`extract_input` の直後なのか、`extract_ticker_range` の後なのかで、YAML のステップ順序が変わる
- トリガー: 実装者がステップ挿入位置を誤る
- 影響: Workflows YAML のステップ順序は意味を持つ（前のステップで assign した変数を後のステップで参照）。`resume_run_id` は他ステップと依存関係がないため順序問題は低いが、一貫性のため位置を明確にすべき
- 根拠: 実際の YAML L47-L66 には `extract_input`、`extract_recent_only`、`extract_ticker_range` の 3 つの extract ステップが連続しており、プランはこの構造を明示していない
- 推奨対応: 挿入位置を「`extract_ticker_range` の直後（L66 の後）」のように行番号で明示する

---

## 【改善提案】（可読性・保守性）

### #1 resume 後の二重ダウンロード回避

- 箇所: プラン P0-1 after コード `gemma_tpu_worker.py` — Step 2a + 2b
- 現状: RESUME_RUN_ID 指定時に (a) 旧パスから LOCAL_OUT にコピー → (b) `gcs_push_current()` で新パスに push → (c) `gcs_download_current()` で新パスから再度 DL。(b)→(c) は同一ファイルの往復で冗長
- 提案: RESUME_RUN_ID 指定時は `gcs_push_current()` のみ行い、`gcs_download_current()` は RESUME_RUN_ID の有無にかかわらず実行する構造に統一する（現行コードとの差分最小化）。あるいは、resume 成功時は `gcs_download_current()` をスキップし、直接 `LOCAL_OUT` から `done_ids` を構築する

### #2 P1-1 の backoff 計算の明示

- 箇所: プラン P1-1 の修正方針
- 現状: `max_retries: 7` + `initial_delay: 60` + `max_delay: 900` + `multiplier: 2` のパラメータ変更が記載されているが、実際の backoff シーケンス（60s, 120s, 240s, 480s, 900s, 900s, 900s）と合計待機時間（~3600s = 1h）が明示されていない
- 提案: retry 回数と backoff の全シーケンスを表形式で記載し、最悪ケースの合計時間と `connector_params.timeout: 14400` とのマージンを示すと、レビュー時の判断が容易になる

### #3 P1-2 デプロイ手順の知見 MD 参照

- 箇所: プラン P1-2
- 現状: `gcloud builds submit --config cloudbuild/cloudbuild.tdnet-gemma-runner.yaml` のコマンドが直書き
- 提案: CLAUDE.md §Cloud Build の規約「`docs/knowledges/` 内の該当ジョブのドキュメントからコマンドをコピーして使う。手打ちで組み立て禁止」に従い、参照元の知見 MD パスを明記する

---

## 【確認できなかった事項】

- `cloudbuild/cloudbuild.tdnet-gemma-runner.yaml` の実在と内容（プラン P1-2 のデプロイコマンドが正しいか）
- Workflows の `retry` 設定で `predicate` が未指定の場合のデフォルト動作（全エラーで retry するのか、特定の HTTP status のみか）。Cloud Run Job が TPU プリエンプション以外の理由（例: OOM, コード bug）で FAILED になった場合にも 7 回 retry してしまう可能性
- `gemma_CURRENT.jsonl` のファイルサイズの実績値（14,671 docs × 1行あたり ~500B と仮定して ~7MB 程度だが、text フィールドが含まれる場合はもっと大きい可能性）。cross-execution copy の所要時間に影響
- Workflows `retry` の `max_retries: 7` が Workflows サービス側の上限を超えていないか（ドキュメント上の明示的な上限の確認が必要）

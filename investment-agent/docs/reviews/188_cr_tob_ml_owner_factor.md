# コードレビュー: TOB MLモデル改善 — TOP10株主TYPE付与 + オーナー色因子新設

- 日時: 2026-05-16 23:00 JST
- 対象: `docs/plans/analysis-007_tob_ml_prediction_20260516_220000.md`
- パターン: 4 (新規開発計画レビュー)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: TOP10株主名へのTYPE分類を行い、信託口スキップ後の「実質筆頭株主」やオーナー色スコアをTOB予測モデルの新因子として追加する計画
- 品質評価: **A** — 問題定義・技術選定・段階的検証がよく設計されており、既存システムとの接合も明確。いくつかの抜け漏れと技術的リスクを指摘するが全体として実装可能性は高い
- 主要リスク:
  1. ASSET_MGMT vs PRIVATE_CORP 境界の曖昧さが因子精度に影響するが、計画で許容範囲と明記されている
  2. BQ SHAREHOLDER_COMPOSITION スキーマ変更（6カラム追加）がデータカタログ・既存クエリに波及
  3. Claude Code バッチ判定（Step 2）のトークン消費量・所要時間の見積もりが未記載

---

## 【パターン4のみ: 新規計画評価】

### 技術選定の妥当性

**[検証済み]** 技術選定は妥当。

- **ルールベース分類 + Claude Code フォールバック**: 56,160名のうちルールでカバー可能な ~26,000名（信託銀行+外国カストディ+金融機関+上場企業+個人名パターン）をルールで処理し、残り ~30,000名をClaude Code で判定するアプローチは合理的。Gemini ではなく Claude Code を使う選択は CLAUDE.md §6「Gemini API: 明示指示なし→使用禁止」に適合
- **Random Forest 維持**: 既存モデル（22変数 RF）に4変数を追加する増分的改修であり、アルゴリズム変更なしで効果検証可能。過剰設計がない
- **BQ レベルでの派生カラム算出**: TOP10_NAMES_JSON から毎回 Python で計算するのではなく、BQ カラムとして永続化する判断は正しい（train_rf.py の _load_shareholders() が SELECT で直接取得できる）
- **有名個人投資家因子**: 仮説としてはユニークだが、「効果なければ戻す」判断基準が明記されており defensiveに設計されている

**懸念**: Claude Code の 200件/バッチ判定について、1バッチあたりのトークン数・レート制限・全体所要時間の見積もりがない。30,000名 / 200 = 150バッチ。会話内で処理する前提であるが、コンテキスト消費への影響が不明

### 既存システムとの統合

- [x] **train_rf.py との接合**: Step 6 で `_load_shareholders()` に新カラム5つ追加 + `build_feature_matrix()` に因子4つ追加が明記。CONTINUOUS_FEATURES / BINARY_FEATURES への割り当ても適切（owner_count → CONTINUOUS, real_top_is_individual → BINARY）
- [x] **screen_tob.py との接合**: Step 8 で根拠表示にオーナー色因子追加が計画済み
- [x] **BQ SHAREHOLDER_COMPOSITION スキーマ**: 6カラム追加が明記（REAL_TOP_NAME, REAL_TOP_RATIO, REAL_TOP_TYPE, OWNER_COUNT_IN_TOP10, OWNER_RATIO_IN_TOP10, HAS_FAMOUS_INVESTOR）
- [x] **apply_shareholder_listing_flag.py パターン流用**: 正規化マッチ + AI判定のパターンは既存実績あり。classify_shareholder_names.py はこの成功パターンを踏襲
- [ ] **キャッシュ整合**: `C:\tmp\tob_prediction\shareholders.csv` が train_rf.py のキャッシュとして存在。新カラム追加後は `--refresh` が必須だが、Step 6 で明記済み
- [ ] **data_catalog 更新**: 計画に **データカタログ更新ステップが未記載**。6カラム追加は `docs/data_catalog/bq_shareholder_composition.md` の更新が必要

### リスク・コスト

- **GCP課金**: BQ UPDATE 37,657行 × 1回 = DML操作としては軽微（数十MB）。コストは無視可能レベル
- **Claude Code トークン**: 150バッチ × (入力200名 + プロンプト + 出力JSON) = 推定1.5-3Mトークン。見積もり明記を推奨
- **処理時間**: Phase 1 が 2-3時間（ルール詰め + Claude判定）は Claude Code のレート制限次第で楽観的。150バッチを逐次処理すると API制限で 1-2時間追加の可能性あり
- **撤退基準**: ROC-AUC が旧モデル (0.752) を下回った場合、因子選択を再検討する旨が明記されている。十分
- **ディスク**: CSV (`shareholder_name_types.csv`, `famous_investors.csv`) は `C:\tmp\` に保存。git 管理外で問題なし

### 抜け漏れ

- [ ] **データカタログ更新**: `docs/data_catalog/bq_shareholder_composition.md` に新6カラムを追加するステップが Phase 4 に含まれていない。知見MD更新（007_tob_ml_prediction.md）は含まれているが、カタログは別ファイル
- [ ] **TOP10_NAMES_JSON の type フィールド追加**: Step 4 で JSON 構造を `{name, ratio}` → `{name, ratio, type}` に変更するが、既存の TOP10_NAMES_JSON を参照している他のコードへの影響確認が未記載。`fetch_shareholder_composition.py` が新規行を挿入する際に type フィールドをどう扱うかの方針が必要
- [ ] **年次更新パイプラインとの統合**: `fetch_shareholder_composition.py` が毎年新レコードを追加する際、新規レコードの株主名TYPE付与をどう行うか（毎回 classify を再実行？ マッピングCSVとLEFT JOINで自動付与？）の運用フローが未定義
- [ ] **SMOTENC cat_indices 更新**: 新因子のうち `real_top_is_individual` と `has_famous_investor` が BINARY_FEATURES に追加される。`resample()` 内の SMOTENC の `categorical_features=cat_indices` は `range(n_cont, n_cont + len(BINARY_FEATURES))` で自動計算されるため実装上は問題ないが、Step 6 に明示なし
- [ ] **memory 更新**: `project_tob_ml_feature_cleanup.md` の更新が Step 8 に含まれているが、新因子名（26変数）の列挙が必要

### 目的・スコープの明確性

- 目的は明確: 「MBO/オーナー型TOBの捕捉力を改善する」
- 非スコープは暗黙的だが、計画の範囲内で十分理解可能（例: アルゴリズム変更は含まない、TOP_SHAREHOLDER_NAME の名寄せ改善は含まない）
- 完了条件が5つ明記されており、いずれも具体的

### 段階的検証計画

- **Phase 1 Step 3**: 信頼度検証（各TYPE 10件ずつランダムサンプル + 3銘柄の期待値確認） -- 十分
- **Phase 2**: BQ更新は dry-run → 10件確認 → 全件展開のパターン -- CLAUDE.md §4.4 に準拠
- **Phase 3 Step 7**: Walk-Forward再評価 + SHAP分析 + 個別銘柄ランキング確認 -- 包括的
- **smoke → dev → prod の区分**: 明示的にはないが、Phase 1-3 が段階的検証の実質的なステージになっている。本運用への反映はモデル再学習結果次第

### 完了条件の検証可能性

5条件すべて検証可能:
1. 「56,160名の全ユニーク株主名にTYPEが付与」→ BQ COUNT WHERE type IS NULL = 0 で検証可能
2. 「3銘柄で期待値一致」→ 具体値（OWNER_COUNT=6 等）が明記
3. 「ROC-AUC >= 0.752」→ Walk-Forward出力で確認
4. 「久光・マンダムが Top15% 以内」→ predictions CSV のランキングで確認
5. 「知見MD・memory 更新済み」→ ファイル差分で確認

### データカタログ整合

- `STOCK.SHAREHOLDER_COMPOSITION`: カタログ (`docs/data_catalog/bq_shareholder_composition.md`) に存在。ただし新規追加予定の6カラムはまだカタログにない -- 計画にカタログ更新ステップを追加すべき
- `STOCK.STOCK_CODE_LIST`: カタログに存在（LISTED_CORP 判定に使用）
- `shareholder_name_types.csv`: ローカル一時データ (c層)。git管理不要で data_catalog 追記不要
- `famous_investors.csv`: 同上

---

## 【重大な指摘】（即修正）

### #1 年次更新時のTYPE付与パイプライン未定義

- 箇所: 計画全体（Phase 4 相当の継続運用設計が不在）
- 事象: `fetch_shareholder_composition.py` が毎年新レコードを BQ に INSERT するが、新株主名に対する TYPE 分類の実行タイミング・方法が定義されていない
- トリガー: 2027年以降の有報提出シーズン（6-7月）に新レコードが追加された時
- 影響: 新年度レコードの `OWNER_COUNT_IN_TOP10` 等が NULL のまま残り、モデル入力に欠損が発生。median imputation で吸収されるが、新規上場銘柄や経営交代で新たに現れる株主名は未分類のまま
- 根拠: Step 4 で一時テーブル JOIN 方式の一括 UPDATE を行うが、これはバックフィル用のワンショット処理。定常運用フローへの組み込みが未設計
- 推奨対応: **[方向性]** Phase 4 に「年次更新時の運用」ステップを追加。選択肢: (A) `fetch_shareholder_composition.py` 完了後に `classify_shareholder_names.py` を自動実行する Cloud Workflows ステップ追加、(B) マッピングCSV を BQ テーブル化し LEFT JOIN で自動付与（未知名は UNCLASSIFIED → 定期バッチで判定）

### #2 データカタログ更新ステップの欠落

- 箇所: Phase 4 Step 8（ドキュメント更新）
- 事象: BQ に6カラムを追加するが、`docs/data_catalog/bq_shareholder_composition.md` の更新が計画に含まれていない
- トリガー: Phase 2 Step 5 完了後
- 影響: データカタログとBQ実スキーマの乖離。他の開発者（または将来のClaude Codeセッション）がカタログを参照した際に新カラムの存在を認識できない
- 根拠: CLAUDE.md §3「テーブル名・カラム名・ファイル名は推測ファースト厳禁。最初に data_catalog.md でテーブル名を確認し、スキーマは docs/data_catalog/*.md を Read する」
- 推奨対応: **[検証済み]** Step 8 のチェックリストに「`docs/data_catalog/bq_shareholder_composition.md` に REAL_TOP_NAME, REAL_TOP_RATIO, REAL_TOP_TYPE, OWNER_COUNT_IN_TOP10, OWNER_RATIO_IN_TOP10, HAS_FAMOUS_INVESTOR の6カラムを追記」を追加

### #3 TOP10_NAMES_JSON 構造変更と既存パイプラインの非互換

- 箇所: Phase 2 Step 4（TOP10_NAMES_JSON に type フィールド追加）
- 事象: Step 4 で既存の `{name, ratio}` を `{name, ratio, type}` に変更。しかし `fetch_shareholder_composition.py` は XBRL パース時に `{name, ratio}` で INSERT する。次回バックフィルで type が欠落したレコードが混入する
- トリガー: Phase 2 完了後の次回 `fetch_shareholder_composition.py` 実行時
- 影響: 新規レコードの TOP10_NAMES_JSON に type フィールドがなく、Python 側で `entry.get("type")` が None を返す。派生カラム算出ロジックが正しく動かない可能性
- 根拠: `fetch_shareholder_composition.py` の既存実装は `{name, ratio}` のみ出力。計画では同スクリプトの改修に触れていない
- 推奨対応: **[方向性]** 二択: (A) `fetch_shareholder_composition.py` を改修して INSERT 時に name→type マッピングを付与する、(B) type フィールドは TOP10_NAMES_JSON に持たせず、派生カラム算出時に別テーブル（name→type マッピング）と JOIN で解決する。(B) の方が既存パイプラインへの影響が少ない

---

## 【改善提案】（可読性・保守性）

### #1 Claude Code 判定のトークン消費見積もり追加

- 箇所: Phase 1 Step 2（見積もりセクション）
- 現状: 「200件ずつ読み込み → TYPE判定結果をCSV追記」とあるが、150バッチ × トークン消費の見積もりがない
- 提案: リスク・注意事項セクションに「Claude Code 判定: 推定150バッチ、1バッチあたり入力~2K tokens + 出力~1K tokens = 合計~450K tokens。セッション上限考慮で複数セッション分割の可能性あり」を追記

### #2 INDIVIDUAL_RULE の精度リスク明示

- 箇所: Phase 1 Step 1 の INDIVIDUAL_RULE 定義
- 現状: 「法人接尾辞なし + 全角文字のみ + 空白含む2-8文字（日本人名パターン）（推定 ~15,000名）」
- 提案: 外国人名（例: `JPMORGAN ASSET MANAGEMENT` のような短い名前、または漢字を含む外国人名）が誤分類されるリスクを明示し、Step 3 の信頼度検証で INDIVIDUAL_RULE の false positive 率を重点チェック項目に追加

### #3 has_famous_investor 閾値の事前仮説記載

- 箇所: Phase 2 Step 5a
- 現状: 「閾値はEDAで決定（候補: 5社/10社/20社）」
- 提案: 背景セクションに五味大輔(118社)、内藤征吾(204社)の出現数が記載されているが、5社閾値だと数百名がhitし因子が薄まる可能性あり。「閾値ごとの該当人数をEDAし、10-20名程度に絞れる閾値を採用」等の方針を先に記載しておくと実装時の判断が早い

---

## 【確認できなかった事項】

- `fetch_shareholder_composition.py` の TOP10_NAMES_JSON 生成部分の実装詳細（Read対象が多数のため本レビューでは割愛。Step 4 実装時に確認推奨）
- BQ の TOP10_NAMES_JSON に NULL が含まれる行の具体数（計画では「少数」と記載されているが、data_catalog には NULLABLE と記載のみ）
- `screen_tob.py` の根拠表示ロジックの詳細（新因子追加時の優先順位変更がどの程度の改修量か）
- 年次更新で新たに出現する株主名の推定件数/年（マッピングCSVのメンテナンス頻度に影響）

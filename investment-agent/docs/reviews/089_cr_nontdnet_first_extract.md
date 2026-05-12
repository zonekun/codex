# コードレビュー: 非TDnet未実行9社の月次データ初回抽出計画

- 日時: 2026-05-06 22:30 JST
- 対象: `docs/plans/ad-hoc_nontdnet_first_extract_20260506_220300.md`
- パターン: 4 (新規計画)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: 非TDnetソースの月次開示銘柄9社に対し、adapter整備・extract実行・error-autofixを一括実行し月次パイプラインの網羅率100%を目指す一過性計画
- 品質評価: **C** — 対象銘柄の現状調査が不十分で、計画の前提が実データと複数箇所で乖離している。そのまま実行すると空振りや意図しない挙動が発生する
- 主要リスク:
  1. 9社中7社が既に `excluded: true`（inactive_reason あり）で BC月次データが数年前に停止済み。`--since 2026` で抽出しても該当データが存在しない可能性が高い
  2. 計画の `_excluded` 設定指示と実adapter上の `excluded`（アンダースコアなし）キー名が食い違い、除外処理が正しく機能しない
  3. Phase 2「method設定なし」3社・Phase 3「adapter生成必要」5社とも、実際には extract_adapter.json が既に存在しており、計画の前提と乖離

## 【パターン4のみ: 新規計画評価】

### 技術選定の妥当性

技術選定自体は適切。`extract_monthly_data.py --tickers --since 2026 --no-batch` のローカル実行、`build_monthly_extractor.py` によるadapter生成、`/monthly-error-autofix` によるエラー対応は、既存パイプラインの標準的な使い方であり過不足ない。

### 既存システムとの統合

- [x] `extract_monthly_data.py` の `--tickers`, `--since`, `--no-batch` フラグ: 存在確認済み
- [x] `build_monthly_extractor.py` の `--tickers` フラグ: 存在確認済み
- [ ] **月次adapter除外フラグの整合性**: extract_monthly_data.py L3319 は `adapter.get("_excluded")` をチェックするが、既存adapter8社は `"excluded": true`（アンダースコアなし）で記録されている。Phase 1 の 7612 除外で `_excluded: true` を設定しても、同時に既存の `excluded: true` が残る8社の adapter は `_excluded` が未設定のため除外されない。ただし Phase 2-3 の7社は `fields: []`（空）なので抽出結果が0件になるだけで破壊的事象にはならない
- [ ] **monthly_adapter_index.csv との連動**: Phase 1 で 7612 を `category=excluded` にする計画があるが、Phase 2-3 の8社は index 上 `category=active` のまま。整合性の観点から、inactive な銘柄の index category も確認が必要

### リスク・コスト

- **空振りリスク（高）**: 8社中7社に `inactive_reason: "BC月次データ 20XX-XX 以降停止（管理対象外判断 2026-04-17）"` が設定されている。`--since 2026` で抽出しても GCS docs に 2026年のファイルが存在しなければ抽出件数 0。Gemini APIコスト・処理時間は発生しない代わりに、計画の完了条件「9社全て records 存在」が達成不可能になる
- **Gemini APIコスト**: Phase 3 の `build_monthly_extractor.py` 実行でGemini呼び出しが発生。5社分で微額だがCLAUDE.md §Gemini API制約に注意（ユーザー許可前提）
- **撤退基準**: 未定義。「何社成功すれば十分か」「inactive銘柄をどう扱うか」の判断基準がない

### 抜け漏れ

- [ ] **GCS adapter同期（Phase 2-3）**: 既存adapterを修正する場合、ローカル→GCS同期が必要だが Phase 2-3 のステップに明示されていない。Phase 5 で「全adapter の GCS同期確認」はあるが、Phase 2-3 の extract 実行前にGCS上のadapterが更新されている必要がある（extract_monthly_data.py はGCSからadapterを読むのか、ローカルから読むのかの明確化が必要）
- [ ] **`excluded: true` フラグの解除判断**: Phase 2-3 対象の8社は `excluded: true` + `inactive_reason` が設定済み。extract 実行前にこれらを解除するステップが計画に含まれていない
- [ ] **`fields: []` の adapter に対する extract の挙動**: 2652, 2751, 2914, 6191, 9878 の5社は `fields: []`（空配列）。Phase 3 で `build_monthly_extractor.py` を実行して再生成するとあるが、`manual_override: true` が設定されているため自動再生成がブロックされる可能性がある

### 目的・スコープの明確性

- 目的「月次パイプラインの網羅率を100%にする」は明確
- ただし「100%」の定義が曖昧: BC月次データが停止済み（inactive）の銘柄も「100%」に含めるのか。含めるなら、数年前にデータ停止した銘柄を今抽出する意義の説明が必要。含めないなら、対象は実質 7612（除外）+ 6425（即実行）= 2社のみになる

### 段階的検証計画

Phase 1-5 の段階分けは適切。ただし各Phase内の成功基準が不明確:
- Phase 2-3 の extract 後、「何件抽出されれば成功か」の基準がない
- Phase 4 の `/monthly-error-autofix` 起動判断: 「失敗・スキップがあれば」とあるが、inactive銘柄の0件抽出はエラーではなくスキップ。これを error-autofix に回すと無駄な調査ループに入る

### 完了条件の検証可能性

完了条件「9社全てについて GCS monthly/record/{ticker}/monthly_records.json が存在する、または正当な理由で除外されている」は検証可能だが、以下の問題:
- inactive 銘柄7社は records が生成されない可能性が高い → 全社「正当な理由で除外」で完了条件を満たすことになり、実質的に除外処理しかしない計画になる
- 「extract 失敗が0社」: 0件抽出（該当ファイルなし）は「失敗」に含まれるかの定義がない

### データカタログ整合

対象データは GCS `monthly/` 配下で data_catalog.md に定義済み。新規テーブル・新規データソースの追加はないため問題なし。

---

## 【重大な指摘】（即修正）

### #1 対象9社の現状と計画の前提が大幅に乖離

- 箇所: `docs/plans/ad-hoc_nontdnet_first_extract_20260506_220300.md:26-36`（対象9社テーブル）
- 事象: 計画テーブルの「必要作業」「method」「GCS docs」が実際の adapter 状態と一致しない
- トリガー: 計画をそのまま実行した場合
- 影響: Phase 2-3 のステップが無駄になる、または予期しない挙動
- 根拠: 実 adapter を Read した結果:
  - **Phase 2「method設定なし」3社**: 2433, 3927, 6264 は全て extract_adapter.json が既に存在し、`excluded: true` + `inactive_reason` が設定済み。2433 は `fields: [1]`（テレビ1項目のみ）、3927 は `fields: [2]`、6264 は `fields: [2]`。「method設定→実行」ではなく「excluded 解除→fields 妥当性確認→実行」が正しいフロー
  - **Phase 3「adapter生成必要（GCS docs 0）」5社**: 2652, 2751, 2914, 6191, 9878 は全て extract_adapter.json が既に存在する（`fields: []` で実質空だが）。`excluded: true` + `manual_override: true`。`build_monthly_extractor.py` で再生成しようとすると `manual_override` でブロックされる可能性がある
  - **全8社に `inactive_reason`**: BC月次データ停止時期が 2019-12（JT）〜 2024-07（テンポスHD）。`--since 2026` で抽出可能な docs が GCS に存在するか未確認
- 推奨対応: 計画実行前に以下の事前調査を追加ステップとして挿入する:
  1. 各社の GCS `monthly/docs/{ticker}/` に 2026年以降のファイルが存在するか確認
  2. inactive_reason の停止時期を確認し、「本当に抽出すべき銘柄」を再選定する
  3. 実質的に有効な対象が 7612（除外）+ 6425（即実行可能）の2社のみであれば、計画スコープを縮小する

### #2 除外フラグのキー名不整合: `excluded` vs `_excluded`

- 箇所: `docs/plans/ad-hoc_nontdnet_first_extract_20260506_220300.md:43`（Phase 1 Step 1）
- 事象: 計画は `_excluded: true, _excluded_reason: "7616の旧コード重複"` の設定を指示しているが、042 スキーマ定義の `_excluded`（アンダースコア付き）と、既存 adapter 群で使われている `excluded`（アンダースコアなし）が混在している
- トリガー: 7612 に `_excluded: true` を設定 → extract_monthly_data.py L3319 が正しくスキップする（コードは `_excluded` をチェック）。一方、Phase 2-3 の8社は `excluded: true`（アンダースコアなし）なのでスキップされない
- 影響: Phase 2-3 の8社は excluded フラグが効かず、空の fields で extract が走る（結果は0件で破壊的ではないが意図と不一致）
- 根拠: `scripts/extract_monthly_data.py:3319` — `adapter.get("_excluded")` でアンダースコア付きのみチェック。既存 adapter 8社は `"excluded": true` で記録
- 推奨対応: 2つの選択肢: (a) 既存 adapter 群の `excluded` を `_excluded` に統一するマイグレーション、または (b) extract_monthly_data.py で `excluded` もフォールバックチェックする。前者が042スキーマ定義に準拠するため推奨

### #3 計画テーブルの社数不整合: タイトル「9社」vs テーブル10行

- 箇所: `docs/plans/ad-hoc_nontdnet_first_extract_20260506_220300.md:1,26-36`
- 事象: タイトルと目的に「9社」とあるが、対象テーブルは #1〜#10 の10行。#1 の7612を除外対象として引くと9社だが、テーブル自体が10行あるため読み手が混乱する
- 影響: 軽微。数え間違いではなく7612を含めた全対象を一覧にしていると推測されるが、明示的な説明がない
- 推奨対応: テーブル冒頭に「10社のうち7612は除外のみ → 実質 extract 対象は9社」等の注記を追加

---

## 【改善提案】（可読性・保守性）

### #1 Phase 0（事前調査）の追加

- 箇所: 作業ステップ全体
- 現状: Phase 1 からいきなり除外・実行に入るが、対象銘柄の実データ状態が未確認
- 提案: Phase 0 として「各社の GCS docs 存在確認 + inactive_reason の妥当性確認 + 対象銘柄の最終選定」を追加。特に `--since 2026` でヒットするファイルの有無を事前に確認することで、空振りを防げる

### #2 inactive 銘柄の扱い方針の明記

- 箇所: 前提セクション
- 現状: 計画は9社全てを extract 対象として扱うが、7社が inactive
- 提案: 「inactive_reason 付き銘柄は (a) 除外して完了とする / (b) inactive を解除して強制抽出する / (c) GCS docs の 2026年データ有無で判断する」のいずれかの方針を明記する

### #3 除外処理の手順を 042 の論理削除手順に準拠させる

- 箇所: Phase 1 Step 1（7612除外）
- 現状: `_excluded: true` + `_excluded_reason` の設定のみ記載
- 提案: 042 §論理削除の4ステップ（①adapter追記 → ②GCS同期 → ③index更新 → ④followup MD追記）に準拠した記載にする。特に `_excluded_at`（ISO 8601）の設定が計画から欠落している

---

## 【確認できなかった事項】

- 各社の GCS `monthly/docs/{ticker}/` に実際に何年のファイルが存在するかは GCS を直接確認しないと判定不能。inactive_reason の停止時期はあくまで BC側の停止であり、企業が IR で月次データを公開し続けている可能性はゼロではない
- `build_monthly_extractor.py` が `manual_override: true` の adapter に対してどう振る舞うか（上書きブロックか無視か）はコードを精読しないと断定不能
- `extract_monthly_data.py` が adapter をローカルから読むのか GCS から読むのかの確認（`--tickers` 指定時の adapter 取得元）

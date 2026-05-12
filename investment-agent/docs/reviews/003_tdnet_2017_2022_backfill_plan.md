# MD AI可読性レビュー: TDnet 2017-2022 バックフィル計画

- 日時: 2026-04-27 14:30 JST
- 対象: `docs/plans/20260427_140000_tdnet_2017_2022_backfill.md`
- パターン: 1 (作成 MD レビュー)
- レビュアー: Claude (md-reviewer skill)
- 出力先: `docs/reviews/003_tdnet_2017_2022_backfill_plan.md`

---

## 【サマリー】

- レビュー対象の要約: TDnet 2017-2022年の全量バックフィル計画。BQ Load + AI処理（Gemma TPU + Embedding、Gemini Flash Batchスキップ）のロードプラン・コード改修・コスト見積を含む。
- AI可読性評価: **A** — 構造・命令が明確で既存プラン（2024/2025 gap）と一貫性がある。コピペ実行可能なコマンドが揃っている
- 誤読リスク評価: **B** — コード改修説明の技術的不正確さ、Q並列に関する矛盾、docs推定の根拠不足が実行時の判断ミスを招くリスクあり
- 主要リスク:
  - `_merge_gemini_juchu` の動作説明が不正確（Geminiスキップ時のsub_categories状態を誤記）
  - Q並列可否が注意事項と2024/2025プラン実績で矛盾。AIがどちらに従うか不明確
  - 推定docs数の根拠が線形補間のみで、GCS実データによる検証手順が欠落

---

## 【Markdown 品質評価】

### Accuracy / 正確性

- **`_merge_gemini_juchu` の動作説明が不正確**: L108「Gemini 未実行時は `doc.sub_categories`（Gemini 結果格納先）が空のまま」は誤り。実コード（`tdnet_load_parallel.py:1853`）で `_apply_gemma_results` が `doc.sub_categories = sorted(final_sub)` を設定するため、Gemini スキップ時も Gemma 結果が入っている。結果的にマージは正しく動作するが、説明が誤っている
- コスト見積の算出根拠は妥当（$4/hr × 24h + $24 O/H = $120 TPU、Embedding $0.025/1M chars × 636M chars = $16）
- コマンド形式は 2024/2025 プランと一貫しており正確

### Completeness / 完全性

- **GCS docs 実数カウント手順が欠落**: 推定 ~318K は線形補間だが、GCS `list_blobs("tdnet/")` + index CSV の実件数カウント手順がない。実行開始前にバッチごとの正確な件数が不明だと、OOM境界の 15K/batch 判定ができない
- **stall 検知の記述がない**: CLAUDE.md「stall 検知義務」は長時間ジョブ監視時の必須要件。monitor_backfill.py の stall 検知機能への参照が欠落
- **YAML テンプレートが H1 のみ**: H2 用テンプレートの記載がない（H1 と同一パターンだが明示が望ましい）
- **2016年との重複チェック手順がない**: 2016年は BQ に存在するが irbank index CSV は 2016 も含む。Load で 2016 の blob を読んだ場合の dedup 動作確認の記述がない（実際は FILE_NAME dedup で安全だが、プランに未記載）

### Relevance / 関連性

- 問題の概要、方針、コスト見積がコンパクトにまとまっており、ノイズが少ない
- 2024/2025 プランとの比較表（コスト）が効果的

### Actionability / 実行可能性

- コマンドのコピペ実行性は高い（年の差し替えだけで全年対応可能な設計）
- 「古い年から順に実行」の順序指定が明確
- 完了後チェック SQL が提供されている

---

## 【AI 誤読リスク】

1. **L108「空のまま」**: AI がこの説明を信じて「Gemini スキップ時は sub_categories が空になる」と理解し、別のコード改修で空前提のロジックを書く可能性がある
2. **L60-62 「Q 並列は TPU 競合のため非推奨」vs 2024/2025 プラン実績「Q2と並列」**: AI が monitor_backfill.py YAML を作成する際、Q を並列 step にすべきか sequential にすべきか判断が分かれる。013_tdnet_load.md:276 にも「3ヶ月/回」とあるが並列可否は明示されていない
3. **推定 docs の「~48,000」等**: AI が「推定値なので正確」と解釈し、OOM 判定（15K/batch）を推定値で行う可能性。実測前に安全判断すべきでない

---

## 【MD 構成リスク】

- コード改修セクションとロードプランセクションの独立性が高く、読みやすい構成
- 注意事項が末尾にまとまっており、実行時に参照しやすい
- 軽微: 「Year 2018-2022（同一パターン）」が年ごとのコマンドを省略しており、YAML テンプレートへの誘導が暗黙。AI がこのセクションだけ読んでコマンドを手組みする可能性は低いが、明示的な「→ monitor_backfill.py YAML で実行」への誘導があるとより安全

---

## 【指示優先順位・文脈境界】

- 013_tdnet_load.md のバックフィル運用規約（3ヶ月単位、task-timeout、投入時間帯）と整合している
- CLAUDE.md の「破壊的操作 dry-run 先行」→ スモークテスト手順が記載されており準拠
- 既存の 2024/2025 gap プランとフォーマットが一貫

---

## 【重大な指摘】（即修正）

### #1 `_merge_gemini_juchu` の動作説明が技術的に不正確

- 箇所: `docs/plans/20260427_140000_tdnet_2017_2022_backfill.md:108`
- 問題: 「Gemini 未実行時は `doc.sub_categories`（Gemini 結果格納先）が空のまま → マージ関数は Gemma 結果をそのまま `doc.sub_categories` にセット」は不正確。`_apply_gemma_results`（L1853）が Gemini 実行前に `doc.sub_categories = sorted(final_sub)` を設定するため空ではない
- AI の誤読パターン: AI がこの説明を根拠に「Gemini スキップ時は sub_categories が初期値（空リスト）」と判断し、下流の改修で空前提ロジックを組む
- トリガー: ai-finalize の改修・デバッグ時
- 影響: 下流コード改修で sub_categories が空であることを期待した分岐を書く → 実際は Gemma 結果が入っている → 予期しない分岐
- 根拠: `scripts/tdnet_load_parallel.py:1853` — `doc.sub_categories = sorted(final_sub)` + L1854 `doc.sub_categories_gemma = sorted(final_sub)`
- 推奨対応: L108 を以下に修正:「Gemini 未実行時は `doc.sub_categories` に Gemma 結果が残ったまま（`_apply_gemma_results` で設定済み）→ マージ関数は `sub_categories_gemma`（バックアップ）から復元し、Gemini の受注判定差分は空なので実質 no-op。結果として Gemma 結果がそのまま確定する」
- MD 修正だけで足りるか: 足りる

### #2 Q 並列可否の矛盾

- 箇所: `docs/plans/20260427_140000_tdnet_2017_2022_backfill.md:60,338`
- 問題: L60「Q 並列は TPU 競合のため非推奨」とあるが、2024/2025 プランの monitor_backfill.py では Q1/Q2 を並列投入して成功している（2024プラン L57-58「Q2と並列」）。013_tdnet_load.md の YAML テンプレートも sequential steps で書かれているが、monitor_backfill.py が実際に並列 step をサポートしている可能性がある
- AI の誤読パターン: AI が「非推奨」を「禁止」と読み、monitor_backfill YAML を sequential のみで構成 → 実行時間が倍増
- トリガー: YAML 作成時、またはユーザーから「もっと速くできないか」と聞かれた時
- 影響: 不要なシーケンシャル実行で全体スケジュールが 6-12日 → 12-24日に膨張、またはユーザーが直接並列投入して TPU 競合発生
- 根拠: 2024 gap プラン実績では Q 並列成功。ただし同一 TPU リージョンで 2 WF 同時投入すると 2 台目の TPU 作成が失敗する可能性あり（retry で救われている可能性）
- 推奨対応: L60 を「Q 並列は 同一 H 内（Q1/Q2 or Q3/Q4）のみ可。H 跨ぎの並列（Q2 と Q3 同時など）は非推奨。monitor_backfill.py の retry が TPU 競合を吸収する」に修正。根拠: 2024/2025 実績
- MD 修正だけで足りるか: 足りる

---

## 【改善提案】（中優先度）

### #1 推定 docs 数の実測手順を追加

- 箇所: `docs/plans/20260427_140000_tdnet_2017_2022_backfill.md:26-36`
- 現状: 線形補間による推定のみ。根拠列が空行多数
- 提案: 「実行前の確認」セクションに以下を追加: `gsutil ls gs://stock_data_1930932/tdnet/index_YYYYMM*.csv | wc -l` で年別 index 件数を確認。または BQ/GCS の blob count で実数を算出する手順。OOM 判定（15K/Q）に使うため精度が必要
- 期待効果: 実行時に「推定 ~48K だが実際は 65K」といった想定外を防止

### #2 stall 検知への参照を追加

- 箇所: プラン全体
- 現状: stall 検知に関する記述がない
- 提案: 注意事項セクションに「stall 検知: CLAUDE.md「stall 検知義務」に基づき、monitor_backfill.py + ScheduleWakeup で進捗メトリクス不変を監視すること。詳細は 013-2_monitor_backfill.md」を追加
- 期待効果: 2026-04-19 の 15h stuck 再発防止

### #3 YAML テンプレートに H2 も明示

- 箇所: `docs/plans/20260427_140000_tdnet_2017_2022_backfill.md:215-242`
- 現状: H1 のみテンプレート提示。H2 は「同一パターン」で省略
- 提案: H2 テンプレートも 1 例追加（date_from/date_to の差し替え箇所が明確になる）
- 期待効果: AI がテンプレートをコピー修正する際の差し替え漏れ防止

---

## 【ソースコード・仕組み側への波及】

- 対象: `scripts/tdnet_load_parallel.py:2287-2294`
- 理由: コード改修そのものは単純だが、Gemini スキップの判定条件 `date_to < "20230101"` は文字列比較であり、`date_to` が環境変数から来るため、フォーマットが YYYYMMDD でないケース（空文字、None、ISO形式）でサイレントに誤判定する可能性がある
- 推奨対応: スモークテスト（デプロイ手順 Step 3）で date_to の値がログに出力されることを確認。既に `logger.log(f"... date_to={date_to} ...")` が改修後コードに含まれているので、ログ確認で十分
- 検証方法: スモークテスト実行後のログで「Gemini Flash Batch: date_to=20221231 < 20230101 → スキップ」を目視確認

---

## 【修正文案】

```markdown
# before (L108)
3. **`_merge_gemini_juchu` は常に実行**: Gemini 未実行時は `doc.sub_categories`（Gemini 結果格納先）が空のまま → マージ関数は Gemma 結果をそのまま `doc.sub_categories` にセット。正常動作

# after
3. **`_merge_gemini_juchu` は常に実行**: Gemini 未実行時も `doc.sub_categories` には `_apply_gemma_results` で設定済みの Gemma 結果が残っている（空ではない）。マージ関数は `sub_categories_gemma`（バックアップ）を基準に受注判定の差分を適用するが、Gemini が動いていないため差分はゼロ → Gemma 結果がそのまま確定。正常動作
```

```markdown
# before (L60)
- **AI 処理**: 3ヶ月単位（Q1-Q4）で Workflows 投入、Q 並列は TPU 競合のため非推奨

# after
- **AI 処理**: 3ヶ月単位（Q1-Q4）で Workflows 投入。同一 H 内の Q 並列（Q1/Q2 or Q3/Q4）は monitor_backfill.py の TPU retry で吸収可能（2024/2025 実績あり）。H 跨ぎの並列は非推奨
```

---

## 【確認できなかった事項】

- GCS 上の 2017-2022 年の実際の blob 数（推定値の検証）— `gsutil` 実行が必要
- monitor_backfill.py が Workflows を並列投入した際の TPU 作成 retry の具体的挙動（コード閲読のみ、実行未確認）
- 2016年データが irbank 経由で入った際のパイプライン構成（現行 ai-finalize 改修が 2016 年データに遡及影響しないかの確認）

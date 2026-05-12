# コードレビュー: _AMBIGUOUS_SUBCATEGORY / Purpose 2 コード削除（再提出 v2）

- 日時: 2026-04-30 23:50 JST
- 対象: `scripts/tdnet_load_parallel.py`（未コミット差分、base = `135d5b8`）
- 提出 MD: `docs/reviews/036_cr_ambiguous_subcategory_removal_v2.md`
- パターン: 2 (改修)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: `_AMBIGUOUS_SUBCATEGORY` / `_MONTHLY_SUB_CATEGORIES` の2変数を削除し、`_NEEDS_GEMINI_ANALYSIS` にリテラル展開。3関数から sub_categories への "月次開示" 追加ロジック（Purpose 2）を全削除。6577 ベストワンドットコムの MAIN_CATEGORY 補正を `_TICKER_SPECIFIC_MONTHLY` に追加。
- 品質評価: **A** — 削除は意図通りの最小スコープで完結しており、CR-033 の2件の重大指摘（downstream SUB_CATEGORIES 参照 / recovery 残存）の前者は BQ データ修正+コード補正で根本対処済み。
- 主要リスク:
  1. `013_tdnet_load.md` L320 に `_MONTHLY_SUB_CATEGORIES` の記述が残存（ドキュメント不整合）
  2. `tdnet_load_recovery.py` L444-468 に同一ロジックが残存（CR-033 #2 と同じ。提出 MD で「別途対応」と明記済み）
  3. `tools-006_tdnet_category_classification_20260430_210000.md` L74 の `_MONTHLY_SUB_CATEGORIES` 参照が削除後コードと不整合

---

## 【パターン2: 改修プラン評価】

提出 MD は正式なプラン MD テンプレート（7フィールド構成）ではなく、レビュー提出用の変更説明書として書かれている。フォーマット適合性チェックは該当しないため省略する。

### 妥当性

**適切**。CR-033 重大指摘 #1 の根本原因は「downstream が SUB_CATEGORIES の "月次開示" に依存している」ことだったが、ユーザー判断として「downstream の OR EXISTS(SUB_CATEGORIES) はコード側の誤り」と確定。この判断のもと:

1. BQ 上の実データ（6577 の 15 行）を MAIN_CATEGORY '月次開示' に UPDATE 済み
2. 今後の load で同銘柄が再度誤分類されないよう `_TICKER_SPECIFIC_MONTHLY` に追加

Purpose 2 の削除は設計意図（MAIN_CATEGORY のみでフィルタ）に合致しており、対症療法ではなく根本対処。

### 副作用・デグレードチェック

- [x] **`_NEEDS_GEMINI_ANALYSIS` の集合等価性**: Before: `_AMBIGUOUS_OVERWRITE | _MONTHLY_SUB_CATEGORIES | _NEEDS_SUB_CATEGORIES` = `{"その他（未分類）", "業績予想", "大型受注・契約", "業績の重要な先行指標", "受注高/受注残高", "決算短信", "決算説明資料"}`。After: `_AMBIGUOUS_OVERWRITE | _NEEDS_SUB_CATEGORIES | {"業績予想", "大型受注・契約", "業績の重要な先行指標", "受注高/受注残高"}` = 同一集合。**等価。漏れ・重複なし。**
- [x] **`_AMBIGUOUS_OVERWRITE` ロジックへの非影響**: `_AMBIGUOUS_OVERWRITE` 変数自体は変更なし（L237 に `{"その他（未分類）"}` が残存）。参照箇所 L1042, L1100, L1806, L1825, L1827 もすべて diff に含まれず無変更。**影響なし。**
- [x] **`_phase3_poll_and_apply` の MAIN_CATEGORY 上書きロジック**: L1042-1047 の `_AMBIGUOUS_OVERWRITE` チェックは維持されている。削除されたのは L1053-1057 の `_MONTHLY_SUB_CATEGORIES` チェックのみ。**正常系の月次上書きは保持。**
- [x] **`_phase3_poll_and_apply_legacy` の同様のロジック**: L1100 の `_AMBIGUOUS_OVERWRITE` チェックは維持。削除は L1108-1111 の `_MONTHLY_SUB_CATEGORIES` チェックのみ。**同上。**
- [x] **`_apply_gemma_results` の MAIN/SUB 決定**: L1825-1830 の MAIN 決定（`_AMBIGUOUS_OVERWRITE` ルール）は無変更。L1832-1835 の SUB 決定から `_MONTHLY_SUB_CATEGORIES` チェックが削除され、Gemma 結果のみを採用する形に単純化。**意図通り。**
- [x] **6577 `_TICKER_SPECIFIC_MONTHLY` 追加**: `_correct_category_by_title()` は MAIN_CATEGORY を "月次開示" に上書きする関数（L312-316 の docstring）。6577 のタイトルパターン "月間予約受注額" はこの関数の `_TICKER_SPECIFIC_MONTHLY` 辞書にマッチし、`_MONTHLY_DOC_PATTERN` では拾えない銘柄固有表記を補完する。既存の 3086 パターンと同じ仕組み。**矛盾なし。**

### 抜け漏れ（類似観点での横展開含む）

- [x] **`tdnet_load_parallel.py` 内の `_MONTHLY_SUB_CATEGORIES` / `_AMBIGUOUS_SUBCATEGORY` 残存参照**: grep 確認済み、0件。**クリーン。**
- [ ] **`tdnet_load_recovery.py` L444-468**: 同一の `_AMBIGUOUS_SUBCATEGORY` / `_MONTHLY_SUB_CATEGORIES` ロジックが残存。提出 MD で「別途対応予定」と明記されているためブロッカーではないが、parallel と recovery の出力が分岐する状態が一時的に発生する。
- [ ] **`013_tdnet_load.md` L320**: 「`_MONTHLY_SUB_CATEGORIES` ルール（SUB に月次開示追加）を適用」との記述が残存。コード削除後は不正確になる。
- [ ] **`tools-006_tdnet_category_classification_20260430_210000.md` L74**: `_MONTHLY_SUB_CATEGORIES` への参照が残る。これはプラン MD であり正本ではないが、次セッション担当者が参照した際に削除済み変数名を見て混乱する可能性がある。

### 新規リスク

- **recovery との出力分岐（一時的）**: parallel から sub_categories "月次開示" 追加が消えるが、recovery 側は残存。recovery 経由でリカバリ処理された文書のみ sub_categories に "月次開示" が付く非対称状態が発生する。ただしユーザー判断として「SUB_CATEGORIES の月次開示は不要」が確定しているため、recovery 側も削除が正しい方向。**一時的な非対称であり、downstream が SUB_CATEGORIES を参照しない前提（ユーザー確認済み）のもとでは実害なし。**

---

## 【重大な指摘】（即修正）

なし。

差分は提出 MD の記載通り6箇所に限定されており、余分な削除・意図しない変更は検出されなかった。`_NEEDS_GEMINI_ANALYSIS` の集合等価性は手動展開で確認済み。`_AMBIGUOUS_OVERWRITE` ロジックは完全に無変更。

---

## 【改善提案】（可読性・保守性）

### #1 013_tdnet_load.md の `_MONTHLY_SUB_CATEGORIES` 記述を更新

- 箇所: `docs/knowledges/tools/013_tdnet_load.md:320`
- 現状: 「`_AMBIGUOUS_OVERWRITE` ... + `_MONTHLY_SUB_CATEGORIES` ルール（SUB に月次開示追加）を適用」と記載されているが、コード側では `_MONTHLY_SUB_CATEGORIES` は削除済み
- 提案: L320 の MAIN_CATEGORY 決定の説明から `_MONTHLY_SUB_CATEGORIES` ルール部分を削除し、「`_AMBIGUOUS_OVERWRITE` ({"その他（未分類）"} かつ is_monthly=True のとき月次開示へ上書き) を適用」のみに修正する。SUB 決定は「Gemma 出力をそのまま採用」に簡略化

### #2 `_NEEDS_GEMINI_ANALYSIS` のインラインリテラルにコメントを付与

- 箇所: `scripts/tdnet_load_parallel.py:240-242`
- 現状: `_AMBIGUOUS_OVERWRITE | _NEEDS_SUB_CATEGORIES | {"業績予想", "大型受注・契約", "業績の重要な先行指標", "受注高/受注残高"}` のインラインセットに名前がない
- 提案: 旧変数名 `_MONTHLY_SUB_CATEGORIES` の痕跡を残す意味で、インラインセットの直前にコメント `# 旧 _MONTHLY_SUB_CATEGORIES 相当（Gemini 分析トリガーとしてのみ使用）` を追加する。これにより将来の読者が「なぜこの4カテゴリがセットに入っているのか」を理解しやすくなる

---

## 【確認できなかった事項】

- **BQ UPDATE の実施結果**: 提出 MD で「6577 の 15 行を UPDATE 済み」と記載されているが、BQ クエリの実行は本レビューのスコープ外（分析ツール使用禁止）。ユーザー報告を信頼する
- **downstream スクリプトの SUB_CATEGORIES "月次開示" 非依存の網羅的確認**: ユーザー判断として「MAIN_CATEGORY のみが正しい設計」と確定しているが、全 downstream スクリプト（`build_monthly_extractor.py` 等）が SUB_CATEGORIES "月次開示" を参照しなくても正しく動作するかは実行確認が必要。CR-033 で 6+ スクリプトが SUB_CATEGORIES を参照していた事実があるため、BQ データ修正（MAIN_CATEGORY → '月次開示'）により downstream が正しくフィルタできるようになったかの実行確認は推奨

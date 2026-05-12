# コードレビュー: _AMBIGUOUS_SUBCATEGORY / Purpose 2 dead code 削除

- 日時: 2026-04-30 22:30 JST
- 対象: `scripts/tdnet_load_parallel.py` コミット `9230b77`
- パターン: 2 (改修 — コミット差分レビュー)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: `_AMBIGUOUS_SUBCATEGORY` / `_MONTHLY_SUB_CATEGORIES` を削除し、sub_categories への "月次開示" 追加ロジック（Purpose 2）を5箇所から除去。`_NEEDS_GEMINI_ANALYSIS` のインライン化で Purpose 1（Gemini分析トリガー）は維持。
- 品質評価: **B** — インライン化の値は正確で、対象ファイル内の変更は整合的。ただし downstream 影響の前提に重大な誤りがあり、横展開漏れ・知見MD未更新がある。
- 主要リスク:
  1. **`sub_categories` に "月次開示" が入らなくなることで、downstream BQ クエリ（6+ スクリプト）が一部文書を検出できなくなる可能性** — 提出MDの「downstream は SUB_CATEGORIES を参照しない」は不正確
  2. `tdnet_load_recovery.py` に同一ロジック（L445-468）が残存し、parallel と recovery で挙動が分岐
  3. `docs/knowledges/tools/013_tdnet_load.md` L320-321 が `_MONTHLY_SUB_CATEGORIES` ルールを現行として記述したまま未更新

## 【パターン2: 改修プラン評価】

提出 MD (`docs/reviews/033_cr_ambiguous_subcategory_removal.md` の元版) はプラン MD テンプレートではなくレビュー依頼書形式のため、パターン2の改修プランフォーマット適合性チェックは一部のみ適用。

### 妥当性

提出MDの論拠は「downstream `get_tdnet_docs` は MAIN_CATEGORY のみでフィルタし SUB_CATEGORIES を参照しない」である。

**この前提は不正確。** `build_monthly_extractor.py` 内の `get_tdnet_docs()` (L252) および同ファイル内の2つの BQ クエリ (L897, L920) は:

```sql
OR EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) AS sc WHERE sc = '月次開示')
```

で `SUB_CATEGORIES` を明示的に参照している。さらに以下のスクリプトも同様のクエリパターンを使用:

- `scripts/verify_monthly_buffett.py` L187
- `scripts/fix_monthly_category_bq.py` L56, 72, 90, 103, 116
- `scripts/check_monthly_coverage_gaps.py` L81
- `scripts/recheck_d1_category.py` L67
- `scripts/debug_extract.py` L45
- `scripts/diagnose_zero_companies.py` L69

`data_catalog.md` L994-1003 にも「月次開示文書を検索する際は `MAIN_CATEGORY = '月次開示'` だけでなく `SUB_CATEGORIES` にも必ず含める」と明記されている。

**ただし、削除された Purpose 2 が実際に影響を及ぼす条件は限定的である。** 削除されたロジックは:

> `doc.main_category in _MONTHLY_SUB_CATEGORIES` (= "業績予想", "大型受注・契約", "業績の重要な先行指標", "受注高/受注残高") かつ `is_monthly=True` のとき、`sub_categories` に "月次開示" を追加

しかし:
1. `_correct_category_by_title()` (L310-327) が上流で月次パターンを検出し `main_category` を "月次開示" に補正済み
2. Gemini Batch/Gemma の `is_monthly` 判定が True の場合、`_AMBIGUOUS_OVERWRITE` (= "その他（未分類）") については `main_category` を "月次開示" に上書きするロジックは存続
3. `_MONTHLY_SUB_CATEGORIES` に入る値（"業績予想" 等）が `main_category` として残る場合は、タイトルパターンもGeminiも月次と判定しなかったケース

**結論**: 上流補正を経てなお `main_category` が "業績予想" 等のまま残り、かつ `is_monthly=True` となるケースは実質的に稀だが、**ゼロであるという検証は提出MDに無い。** BQ 実データでの件数確認が必要。

### 副作用・デグレードチェック

- [x] **`_NEEDS_GEMINI_ANALYSIS` の等価性**: Before: `{"その他（未分類）"} | {"業績予想", "大型受注・契約", "業績の重要な先行指標"} | {"受注高/受注残高"} | {"決算短信", "決算説明資料"}` = 7要素。After: 同一7要素をインラインで記述。**等価。問題なし。**
- [x] **`_AMBIGUOUS_OVERWRITE` / `_NEEDS_SUB_CATEGORIES` は未変更**: 両定数および参照箇所は変更なし。OK。
- [ ] **既存 BQ データの `SUB_CATEGORIES` に "月次開示" が入っている行の扱い**: 既にバックフィル済みデータには Purpose 2 で "月次開示" が書き込まれた行が存在する可能性がある。今後の新規ロードでは入らなくなるため、同一カテゴリの文書で新旧で `SUB_CATEGORIES` の内容が異なる非対称が生じうる。

### 抜け漏れ（類似観点での横展開含む）

- [ ] **`tdnet_load_recovery.py` L444-468**: `_AMBIGUOUS_SUBCATEGORY` / `_MONTHLY_SUB_CATEGORIES` の同一ロジックが残存。parallel 側で dead code と判断して削除するなら、recovery 側も同期すべき。
- [ ] **`docs/knowledges/tools/013_tdnet_load.md` L320-321**: 「`_MONTHLY_SUB_CATEGORIES` ルール（SUB に月次開示追加）を適用」と現行ドキュメントに記載されたまま。コード変更と知見MDが不整合。
- [ ] **`docs/plans/tools-006_tdnet_category_classification_20260430_210000.md` L60, 74-75, 77-82**: `_AMBIGUOUS_SUBCATEGORY` の残留問題として議論されていた内容が、このコミットで解消されたことの反映が未実施。

### 新規リスク

- **BQ データ非対称**: 過去バックフィルデータ（Purpose 2 有効時にロード）と今後の新規データ（Purpose 2 削除後にロード）で、同一カテゴリ・同一 `is_monthly=True` の文書の `SUB_CATEGORIES` が異なる。downstream クエリの結果が新旧で非対称になるリスク（ただし上記「妥当性」節の通り、影響件数が実質ゼロの可能性もある）。

---

## 【重大な指摘】（即修正）

### #1 downstream SUB_CATEGORIES 参照の前提誤り

- 箇所: 提出MD L16 / コミットメッセージ
- 事象: 「downstream get_tdnet_docs は SUB_CATEGORIES を参照しない」と記載しているが、`build_monthly_extractor.py` L252, L897, L920 を含む 6+ スクリプトが `SUB_CATEGORIES` に "月次開示" が入っている行を BQ クエリで検索している。`data_catalog.md` L994-1003 にも両方を検索するルールが明記されている。
- トリガー: `main_category` が "業績予想" / "大型受注・契約" / "業績の重要な先行指標" / "受注高/受注残高" のまま残り、かつ `is_monthly=True` と判定される文書が存在する場合
- 影響: 該当文書が `build_monthly_extractor.py` 等の月次開示検索クエリから脱落する（MAIN_CATEGORY でもヒットしないため）
- 根拠: `build_monthly_extractor.py:252` の SQL `OR EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) AS sc WHERE sc = '月次開示')`。同パターンが6スクリプト以上で使用
- 推奨対応: デプロイ前に BQ で以下のクエリを実行し、該当行が 0 件であることを確認する:
  ```sql
  SELECT COUNT(*) FROM `<project>.<dataset>.TDNET_DOCUMENTS_ENHANCED`
  WHERE MAIN_CATEGORY IN ('業績予想', '大型受注・契約', '業績の重要な先行指標', '受注高/受注残高')
    AND EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) AS sc WHERE sc = '月次開示')
  ```
  0 件なら dead code 確定。1 件以上なら、それらの行が今後のロードで SUB_CATEGORIES から "月次開示" が消える影響を評価する必要がある。

### #2 `tdnet_load_recovery.py` の同一ロジック残存

- 箇所: `scripts/tdnet_load_recovery.py:445-468`
- 事象: `_AMBIGUOUS_SUBCATEGORY` / `_MONTHLY_SUB_CATEGORIES` の定義と Purpose 2 ロジック（L464-468: `sub_categories.append("月次開示")`）がそのまま残っている。recovery スクリプト経由でリカバリ処理を実行した場合、parallel とは異なり "月次開示" が `sub_categories` に追加される。
- トリガー: `tdnet_load_recovery.py` を使ったリカバリ実行時
- 影響: parallel と recovery で同一文書の BQ 出力が異なる（recovery 側のみ `SUB_CATEGORIES` に "月次開示" が入る）
- 根拠: `tdnet_load_recovery.py:464-468` で `_MONTHLY_SUB_CATEGORIES` チェック＋ `sub_categories.append("月次開示")` が現存
- 推奨対応: dead code と判断するなら recovery 側も同期削除。判断保留なら parallel 側の削除も保留すべき。

---

## 【改善提案】（可読性・保守性）

### #1 知見MD `013_tdnet_load.md` の更新

- 箇所: `docs/knowledges/tools/013_tdnet_load.md:320-321`
- 現状: 「`_MONTHLY_SUB_CATEGORIES` ルール（SUB に月次開示追加）を適用」と記載されているが、コードからは既に削除済み。
- 提案: L320 の MAIN_CATEGORY 決定行から `_MONTHLY_SUB_CATEGORIES` 参照を削除。L321 の SUB_CATEGORIES 決定行から「旧ロジックの月次開示追加を適用」を削除。

### #2 インライン化セットへのコメント追加

- 箇所: `scripts/tdnet_load_parallel.py:240-242`
- 現状: `_NEEDS_GEMINI_ANALYSIS` のインラインセットリテラル `{"業績予想", "大型受注・契約", "業績の重要な先行指標", "受注高/受注残高"}` に、これらが何であるか（旧 `_MONTHLY_SUB_CATEGORIES` の要素）のコメントがない。
- 提案: セットリテラルの直前に `# 旧 _MONTHLY_SUB_CATEGORIES の要素（月次性カテゴリ）` 等のコメントを追加し、由来を明示する。

### #3 `tools-006` プランMDの状態反映

- 箇所: `docs/plans/tools-006_tdnet_category_classification_20260430_210000.md:60, 77-82`
- 現状: `_AMBIGUOUS_SUBCATEGORY` の残留問題（Step 4: L176-184）が未解決として記載されているが、このコミットでセット自体が削除されたため解消済み。
- 提案: Step 4 に「9230b77 で `_AMBIGUOUS_SUBCATEGORY` セット自体を削除済み。残留問題は解消」と追記。

---

## 【確認できなかった事項】

- **BQ 実データにおける Purpose 2 適用実績**: `main_category` が `_MONTHLY_SUB_CATEGORIES` の要素のまま残り、かつ `is_monthly=True` で、`SUB_CATEGORIES` に "月次開示" が実際に書き込まれた BQ 行が何件存在するかは、BQ クエリを実行しないと確認できない。0 件であれば dead code 確定、1 件以上であれば既存データとの非対称リスクおよび downstream 検索脱落リスクが実在する。
- **Gemma/Gemini が `sub_categories` に "月次開示" を自律的に出力する頻度**: Gemini プロンプト（L840-861）は `is_monthly` と `sub_categories` を独立に判定させる構成。`is_monthly=True` のとき `sub_categories` にも "月次開示" を含める傾向があるかは、AI 出力の統計的分析が必要。
- **2018年バックフィル実行中のデータ**: 現在実行中のバックフィルが旧コード（Purpose 2 あり）で処理されているか新コード（Purpose 2 なし）で処理されているかは、デプロイ状況に依存し確認不能。

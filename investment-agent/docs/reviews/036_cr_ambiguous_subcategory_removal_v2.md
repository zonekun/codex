# CR-036: _AMBIGUOUS_SUBCATEGORY / Purpose 2 コード削除（再提出）

## レビュー対象

- **対象ファイル**: `scripts/tdnet_load_parallel.py`
- **変更種別**: refactor（dead code 削除） + fix（6577 ティッカー補正追加）
- **前回レビュー**: CR-033 で重大指摘2件 → revert 済み → 根本原因対処後の再提出

## 前回レビュー（CR-033）からの変更点

### CR-033 重大指摘 #1: downstream SUB_CATEGORIES 参照の前提誤り

**ユーザー判断**: downstream の `OR EXISTS(... SUB_CATEGORIES ...)` はコード側の誤り。設計意図は `get_tdnet_docs` L403 コメント通り「MAIN_CATEGORY のみでフィルタ、SUB_CATEGORIES は使わない」が正。よって Purpose 2 削除は正当。

**実データ検証結果**: BQ で `MAIN_CATEGORY IN ('業績予想','大型受注・契約','業績の重要な先行指標','受注高/受注残高') AND SUB_CATEGORIES に '月次開示'` を検索 → **15行（13ドキュメント）、全て 6577（ベストワンドットコム）の「月間予約受注額について」**。他銘柄は 0件。

**対処済み**:
1. BQ UPDATE: 6577 の 15行の MAIN_CATEGORY を '大型受注・契約' → '月次開示' に修正済み
2. コード: `_TICKER_SPECIFIC_MONTHLY` に `"6577": re.compile(r"月間予約受注額")` を追加。今後の load で自動的に MAIN_CATEGORY='月次開示' に補正される

### CR-033 重大指摘 #2: `tdnet_load_recovery.py` の同一ロジック残存

**今回スコープ外**: recovery 側は別途対応予定。本レビューは parallel 側のみ。

## 変更箇所（6箇所）

### 削除（Purpose 2 コード除去、前回と同一）
1. **定義セクション** (L237-247): `_AMBIGUOUS_SUBCATEGORY`, `_MONTHLY_SUB_CATEGORIES` 削除。値は `_NEEDS_GEMINI_ANALYSIS` にインライン化
2. **`_phase3_poll_and_apply`** (L1053-1057): `_MONTHLY_SUB_CATEGORIES` チェック＆"月次開示" sub追加ブロック削除
3. **`_phase3_poll_and_apply_legacy`** (L1112-1114): 同上
4. **`_apply_gemma_results` 本体** (L1851-1852): `_MONTHLY_SUB_CATEGORIES` チェック＆"月次開示" sub追加削除
5. **`_apply_gemma_results` docstring** (L1822-1823): `_MONTHLY_SUB_CATEGORIES` 参照記述削除

### 追加（6577 ピンポイント補正）
6. **`_TICKER_SPECIFIC_MONTHLY`** (L310): `"6577": re.compile(r"月間予約受注額")` 追加

## レビュー観点（ユーザー指示）

**余分な箇所を削っていないかの確認が主目的。**

- Purpose 1（`_NEEDS_GEMINI_ANALYSIS` Gemini分析トリガー）のインライン化で値の漏れ・重複がないか
- Purpose 2 以外のロジック（`_AMBIGUOUS_OVERWRITE` による MAIN_CATEGORY 上書き等）が影響を受けていないか
- 6577 の `_TICKER_SPECIFIC_MONTHLY` 追加が既存の補正ロジックと矛盾しないか
- grep で `_MONTHLY_SUB_CATEGORIES` / `_AMBIGUOUS_SUBCATEGORY` の残存参照が 0件であることは確認済み

---

## レビュー追記: 2026-04-30 23:50 JST — code-reviewer

→ `docs/reviews/037_cr_ambiguous_subcategory_removal_v2_review.md`

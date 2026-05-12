# コードレビュー: CONSENSUS CSV出力の FY_CURRENT 判定 off-by-one バグ

- 日時: 2026-05-12 07:34 JST
- 対象: `scripts/lib_conse_csv_from_view.py`, `scripts/export_consensus_csv.py`
- パターン: 3 (ad-hoc)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: FY開示済み銘柄において、C案 PERIOD_REL 判定の `fy > latest_fy` フィルタが当期FYコンセンサスを除外し、来期FYが FY_CURRENT に繰り上がるバグの修正方針レビュー
- 品質評価: **B** -- バグの根本原因分析は正確で、提案方針は概ね妥当だが、既にプロジェクト内で正しく動作している実績パターン（`_derive_current_fy` 関数）との整合性が検討されていない
- 主要リスク:
  1. 提案方針は正しいが、既存の実績パターン（zaraba/predict の `_derive_current_fy`）と異なるアプローチを取っており、保守コスト増のリスクがある
  2. QUICK-only 銘柄（FYレコードのみ、四半期データなし）で `ticker_q_fy` が空になるフォールバックパスに同一バグが残存する
  3. `export_consensus_csv.py` に Dropbox API トークンがハードコードされている（本バグとは無関係だが既存のセキュリティリスク）

---

## 【バグ分析: 独立仮説の検証】

### 根本原因の独立推定

バグの本質は、`fin_summary` の `LATEST_FY`（最新 **発表済み** FY末YYYYMM）と、コンセンサスの FY 値の意味的不一致にある。

- `fin_summary` で `TYPE_OF_CURRENT_PERIOD = 'FY'` の最新レコードが `CURRENT_FISCAL_YEAR_END_DATE = 2026-03-31` → `LATEST_FY = "202603"`
- これは「202603期のFY決算が **開示済み** 」を意味する
- コンセンサスの `FY = "202603"` は「202603期のFY予想値」
- FY開示後もコンセンサス予想値は VIEW に残る（IFISの当期FY値など）
- `fy > latest_fy` は「まだ開示されていないFY期の予想」を取得する意図だが、**FY開示済み = latest_fy == コンセンサスのFY** のケースで当期FYを取りこぼす

この分析は依頼内容の根本原因分析と一致する。**バグの特定は正確である。**

### 既存コードとの比較

同一プロジェクト内に、同じ問題を **既に正しく解決している** 実装が2箇所ある:

**1. `scripts/zaraba_earnings.py:861-879` / `scripts/earnings_model/predict.py:144-161` の `_derive_current_fy()`:**

```python
def _derive_current_fy(prev_disc_type, prev_disc_fy_end):
    if prev_disc_type == "FY":
        # FY発表済み → current_fy = 翌年度FY
        y = int(fy_yyyymm[:4])
        m = fy_yyyymm[4:6]
        return f"{y + 1}{m}"
    return fy_yyyymm  # 1Q/2Q/3Q → 同FY
```

このアプローチは `fin_summary` の `TYPE_OF_CURRENT_PERIOD` を使って「当期が何か」を正確に導出する。FY開示済みなら翌年度に繰り上げ、そうでなければ同年度。その後の判定は `fy == current_fy` → CURRENT、`fy > current_fy` → NEXT と明快。

**2. `scripts/zaraba_earnings.py:896-940` の `_consensus_to_prior_fields()`:**

上記の `current_fy` を使い、`fy == current_fy` で CURRENT を判定（equality）。

**バグのある2ファイルはこの `_derive_current_fy` パターンを使っていない。** 代わりに `fin_summary` から `LATEST_FY` を直接取得し、strict greater-than で比較している。

---

## 【重大な指摘】（即修正）

### #1 FY開示済み銘柄で当期FYコンセンサスが消失

- 箇所: `scripts/lib_conse_csv_from_view.py:92-93`
- 事象: `fy > latest_fy` の strict greater-than により、`fy == latest_fy` のレコード（= FY開示済みだがコンセンサスとして有効な当期FY値）が除外される。結果として来期FY値が FY_CURRENT に繰り上がり、FY_NEXT が空になる
- トリガー: `fin_summary` で `TYPE_OF_CURRENT_PERIOD = 'FY'` が存在する銘柄（= 本決算開示済み銘柄）。依頼にある ticker 1720 のケースが典型
- 影響: CSV出力の FY_CURRENT / FY_NEXT が1期ずつずれる。Dropbox経由で外部共有されるCSVのため、下流の投資判断に直接影響する
- 根拠: line 93 の `if fy > latest_fy` で `"202603" > "202603"` → `False`。15900 が除外され、19150 のみが `future_fys[0]` に入る
- 推奨対応: **[方向性]** 下記の2つの選択肢がある。選択肢Bを推奨する

  **選択肢A（提案方針: 四半期FYベース判定）**: 1Q/2Q/3Qレコードの FY を `ticker_q_fy` として保持し、`fy == q_fy` → FY_CURRENT とする。QUICK-only銘柄は fin_summary フォールバック。

  **選択肢B（既存実績パターン踏襲: `_derive_current_fy` 方式）**: `zaraba_earnings.py` / `predict.py` に既に実装されている `_derive_current_fy()` 関数を共通ライブラリとして切り出し、lib_conse_csv_from_view.py でも使う。`fin_summary` から `TYPE_OF_CURRENT_PERIOD` と `CURRENT_FISCAL_YEAR_END_DATE` の両方を取得し、FY開示済みなら翌年度に繰り上げた `current_fy` を導出。判定は `fy == current_fy` → CURRENT、`fy > current_fy` の最小 → NEXT。

  選択肢Bの優位性:
  - プロジェクト内で2箇所で実証済みの実績パターン（zaraba/predict）
  - 判定ロジックが1箇所に統一され、保守コストが下がる
  - QUICK-only 銘柄でも fin_summary ベースで正しく動作する（フォールバック分岐不要）
  - `TYPE_OF_CURRENT_PERIOD` を使うことで「FY開示済みか否か」を明示的に判定できる

### #2 export_consensus_csv.py に同一バグ

- 箇所: `scripts/export_consensus_csv.py:74`
- 事象: `row["FY"] > latest_fy` で #1 と全く同じ off-by-one が発生
- トリガー: #1 と同一
- 影響: Dropbox にアップロードされる CONSENSUS_latest.csv の 4Q/NEXT 列が1期ずれる
- 根拠: line 74 の条件式が lib_conse_csv_from_view.py:93 と同一パターン
- 推奨対応: **[方向性]** #1 の修正と同時に適用。export_consensus_csv.py は lib_conse_csv_from_view.py とは独立した実装（pandas ベース）だが、FY判定ロジックは共通化すべき

### #3 `_fetch_latest_fy_end` が `TYPE_OF_CURRENT_PERIOD` を返さない

- 箇所: `scripts/lib_conse_csv_from_view.py:24-34`
- 事象: `_fetch_latest_fy_end()` は `LATEST_FY`（YYYYMM文字列）のみ返す。`TYPE_OF_CURRENT_PERIOD` を返さないため、FY開示済みか否かの判定材料が不足しており、#1 のバグの構造的原因となっている
- トリガー: 設計上の制約（BQクエリが情報不足）
- 影響: FY判定ロジックの正確性が担保できない
- 根拠: SQL が `WHERE TYPE_OF_CURRENT_PERIOD = 'FY'` でフィルタしつつ `MAX(CURRENT_FISCAL_YEAR_END_DATE)` のみ返すため、返却値は常に「最新FY開示済みのFY末日」。これだけでは「現在の最新開示がFYか四半期か」が分からない
- 推奨対応: **[方向性]** `_fetch_latest_fy_end` を改修し、`TYPE_OF_CURRENT_PERIOD` フィルタを除去して銘柄ごとの最新開示の `TYPE_OF_CURRENT_PERIOD` と `CURRENT_FISCAL_YEAR_END_DATE` の両方を返すか、または `_derive_current_fy` パターンを導入する

---

## 【改善提案】（可読性・保守性）

### #1 FY判定ロジックの共通化

- 箇所: `scripts/lib_conse_csv_from_view.py`, `scripts/export_consensus_csv.py`, `scripts/zaraba_earnings.py:861-879`, `scripts/earnings_model/predict.py:144-161`
- 現状: `_derive_current_fy()` が zaraba_earnings.py と predict.py に全く同じコードとして重複している。lib_conse_csv_from_view.py と export_consensus_csv.py は別のアプローチ（`LATEST_FY` strict greater-than）を使っている。合計3種類の FY判定ロジックが存在
- 提案: `_derive_current_fy()` を共通ユーティリティ（例: `scripts/lib_consensus_utils.py`）に切り出し、4ファイルすべてから参照する。BQクエリも `TYPE_OF_CURRENT_PERIOD` を含む共通クエリを1箇所に定義する

### #2 export_consensus_csv.py のセキュリティ

- 箇所: `scripts/export_consensus_csv.py:26-28`
- 現状: Dropbox の `DBX_APP_KEY`, `DBX_APP_SECRET`, `DBX_REFRESH_TOKEN` がソースコードにハードコードされている
- 提案: `.env` ファイルまたは環境変数に移動する。CLAUDE.md のコーディング規約「設定値: ハードコーディング禁止」および「APIキーは.envファイルで管理し、絶対にコミットしない」に違反している。ただし本件は既存コードであり、本バグ修正のスコープ外

---

## 【提案方針への評価】

### 提案方針（四半期FYベース判定）の妥当性

提案方針はバグの根本原因に対処しており、**方向性は正しい**。しかし以下の点で懸念がある:

1. **QUICK-only 銘柄のフォールバック**: QUICK は FY レコードのみ（1Q/2Q/3Q なし）のため、`ticker_q_fy` が空になる。フォールバックで `fy > latest_fy`（fin_summary ベース）を使うと、QUICK-only 銘柄では**同一バグが残存する**。提案方針のフォールバックパスにも `>` ではなく `>=` または `_derive_current_fy` が必要

2. **「`>=` への単純変更ではダメな理由」の検証**: 依頼で言及されている「`>=` ではダメ」について検証した。結論: **`>=` への単純変更は確かにダメである**。理由は、`latest_fy` が「最新FY **開示済み** のFY末日」であるため:
   - FY未開示の銘柄（1Q/2Q/3Q開示済み）: `latest_fy` は前年度のFY末日。当期FYの `fy > latest_fy` は成立し正常動作。`>=` にしても同じ結果（前年度FYコンセンサスが残っていなければ）
   - FY開示済みの銘柄: `latest_fy = "202603"`。`fy >= "202603"` にすると当期FY(15900)は含まれるが、**前年度FYのレコードが VIEW に残っていた場合にそれも CURRENT に含まれてしまう可能性**がある... と思ったが、VIEW は最新 DATAAT のみなので実質的に前年度FYレコードが残る状況は稀
   - しかし本質的な問題は、**`latest_fy` の意味が「最新開示済みFY」であり「当期FY」ではない**こと。FY開示済み銘柄の場合、latest_fy と当期FYが一致するが、来期FYは `latest_fy + 1年`。`>=` にすると「開示済みFY = 当期FY」の関係が崩れた時に壊れる

   **`>=` が実用上は機能する可能性はあるが、意味論的に正しくない**。`_derive_current_fy` パターンのように「当期FYとは何か」を明示的に導出するのが正しいアプローチ

3. **既存実績パターンとの不整合**: zaraba/predict は `_derive_current_fy` → `fy == current_fy` パターンで動作実績がある。提案方針は「四半期FYからの逆引き」という別アプローチであり、プロジェクト内のFY判定ロジックが3種類に増える（バグの2種 + 正常の1種 → 正常の2種）。保守性の観点から `_derive_current_fy` パターンへの統一を推奨

### 副作用・デグレードチェック

- [x] FY未開示銘柄（1Q/2Q/3Q最新）: 提案方針で `ticker_q_fy` が正しく取得され、正常動作。デグレードなし
- [x] QUICK-only 銘柄: フォールバックパスに `fy > latest_fy` が残るため、**同一バグが残存するリスクあり**（重大指摘 #1 参照）
- [x] 複数FY年度のレコードがある銘柄: 提案方針は `fy == q_fy` と `fy > q_fy の最小` で正しく分類。問題なし
- [x] CSV出力の既存フォーマット: 6列構成（TICKER, 1Q_CURRENT, 2Q_CURRENT, 3Q_CURRENT, FY_CURRENT, FY_NEXT）は変更なし。デグレードなし

### 抜け漏れ（横展開チェック）

- [x] `scripts/zaraba_earnings.py:930-936` の `_consensus_to_prior_fields()`: `fy == current_fy` / `fy > current_fy` パターンで正しく実装済み。**バグなし**
- [x] `scripts/earnings_model/predict.py:206-211` の `_build_cons_map_from_df()`: `fy == cfy` / `fy > cfy` パターンで正しく実装済み。**バグなし**
- [x] `update_conse_ifis.py` / `update_conse_quick.py` は lib_conse_csv_from_view.py の `export_consensus_csv()` を呼ぶだけで、独自のFY判定ロジックを持たない。修正は自動的に波及する

### 新規リスク

- 提案方針を採用した場合、QUICK-only銘柄のフォールバックパスで `fin_summary` ベースの判定が残る。このパスの `>` を `>=` に変更するか、`_derive_current_fy` パターンを導入しないと、QUICK-only銘柄で同一バグが残存する

---

## 【確認できなかった事項】

- `V_CONSENSUS_MERGED` VIEW にFY開示後のレコードがどの程度残存するか（IFISの当期FY値は開示後も残るが、QUICKの当期FY値の挙動は実データを確認する必要がある）
- FY開示直後のタイミングで、コンセンサスの当期FY値がソース側（IFIS/QUICK）から消える場合、本バグの影響は一時的かもしれない。ただし修正の必要性は変わらない
- QUICK-only銘柄が現在何社あるか（フォールバックパスの影響範囲の定量評価には実データ確認が必要）

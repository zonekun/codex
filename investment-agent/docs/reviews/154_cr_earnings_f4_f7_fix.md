# コードレビュー: 決算反応モデル F4/F7因子バグ修正

- 日時: 2026-05-12 22:20 JST
- 対象: `docs/plans/earnings-model-fix_20260512_220000.md`
- パターン: 2 (改修)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: F4コンセンサス乖離のNaN問題（2395）と、F7成長加速/減速baselineの比較軸不整合（7951）を修正するプラン
- 品質評価: **B** — 症状と根本原因の分析は的確。P0-1の修正方針が複数原因のif分岐で確定していない点、P1-1のafter案にエッジケース考慮不足がある
- 主要リスク:
  1. P0-1の根本原因が未確定のまま複数フォールバック案を列挙 — 診断BQクエリを先に実行すべき
  2. P1-1のbaseline集約で不完全FY（4Q未満）を除外するが、3月決算以外の変則決算期で集約ミスの可能性
  3. backfill全期間への影響範囲が未定量

## 【改修プラン評価】

### 妥当性

**P0-1（F4コンセンサスNaN）**: プランは3つの原因候補を列挙しているが、**最も可能性の高い原因は明記されていない**。独自推定: `bq_consensus.md` の記載「QUICK: FYのみ・当期/来期/再来期」「IFIS: 当期のみ」から、2395がQUICKカバレッジ外の場合、IFISは当期のみのため**NEXT期のコンセンサスデータ自体がCONSENSUSテーブルに存在しない**。これが最有力原因。プランのフォールバック案（CURRENTキーで再検索）はこの場合でもデータ不在のため機能しない。**方向性に問題あり**: データが無い銘柄のフォールバック先を設計する必要がある（V_CONSENSUS_MERGED VIEW利用 or predict単日モードでの別クエリ追加）。

**P1-1（F7 baseline不整合）**: 根本原因の分析は正確。4Q単独OP YoY vs 通期OP成長率の比較は論理的に誤り。修正方針（四半期集約→通期OP YoY）は正しい方向。

### 副作用・デグレードチェック

- [x] `predict.py:L619` の `dff = dfq_asof[dfq_asof["QUARTER"] == "4Q"]` は baseline 以外にも `median_5y_op_map`（L634-640）で使用 — P1-1の修正で `dff` 変数を消す場合、`median_5y_op_map` 計算が壊れる。プランはこの依存を記載していない
- [x] P1-1のafter案で `set(grp["QUARTER"].tolist()) >= {"1Q", "2Q", "3Q", "4Q"}` は**非3月決算の中間期開示で "FY" QUARTERが混在する場合を考慮していない**。`v_fin_summary_actual_for_q_on_q` が 1Q/2Q/3Q/4Q のみ格納するかは未確認
- [x] P1-1で `grp["OPERATING_PROFIT"].sum()` は NaN を含む場合 `skipna=True`（デフォルト）で合計するが、1四半期でもNaNがあると通期OPが過小になる。`if pd.notna(total)` チェックだけでは不十分

### 抜け漏れ（類似観点での横展開含む）

- [ ] **F3 YoY OP（`earnings_model_core.py:L68-74`）もP1-1と同根**: F3の `yoy_op` は `qoq_map` から取得（`predict.py:L590-597`）。これは**四半期単独OP**のYoY。FY期の場合、4Q単独のYoYがF3スコアに使われている。7951ヤマハの `YoY OP +1209%` はまさにこれ。F7だけ直してF3を放置すると不整合が残る
- [ ] **`is_low_base` 判定（`predict.py:L750-754`）**: 5年中央値OPとの比較だが、これも4Q単独OPベースの `median_5y_op_map` を使用。通期ベースに揃えるべきか要検討
- [ ] **backfill時のCONSENSUSデータ取得**: `predict.py:L393-396` は `STOCK.CONSENSUS` テーブルを直接クエリ。`bq_consensus.md` §VIEW不使用 に「backfillはas-ofクエリのためVIEW不使用」と記載。P0-1でVIEW利用に変更する場合、backfillモードとpredictモードで異なるデータソースになる — 整合性リスク
- [ ] **知見MD更新**: 修正後に `059_earnings_model_eda.md` の因子説明を更新する必要がある（F7の定義変更を反映）

### 新規リスク

- P1-1: baseline計算のパフォーマンス劣化。現行は `QUARTER == "4Q"` フィルタ後のシンプルなループ。修正後は全四半期のgroupby+集約になり、銘柄数×年数分のiterが増える。backfill（数千銘柄×数百日）での所要時間増大を検証すべき
- P0-1: QUICK未カバー銘柄にV_CONSENSUS_MERGEDを使う場合、as-of時点でのVIEWデータ再現性（backfillで過去日を処理する際、VIEWは最新データのみ返す）

---

## 【重大な指摘】（即修正）

### #1 P1-1 after案が `median_5y_op_map` の `dff` 変数を破壊する可能性

- 箇所: `scripts/earnings_model/predict.py:L619,L634-640`
- 事象: プランのafter案はL618-632を置き換えるが、L634の `median_5y_op_map` 計算も同じ `dff = dfq_asof[dfq_asof["QUARTER"] == "4Q"]` を参照。プランは `dff` をそのまま残すか削除するか明示していない
- トリガー: P1-1の修正を適用した場合
- 影響: `median_5y_op_map` が空辞書になり、`is_low_base` 判定が全銘柄でFalseになる
- 根拠: `predict.py:L619` で `dff` を定義し、L620-632のbaseline計算とL634-640の5年中央値計算の両方が参照
- 推奨対応: **[検証済み]** `dff` 変数はbaseline計算セクション外に残し、baseline計算のみを通期集約に変更する。`dff` を削除・上書きしない

### #2 P0-1 フォールバック案（CURRENTキー再検索）が無効

- 箇所: `docs/plans/earnings-model-fix_20260512_220000.md` P0-1修正方針
- 事象: プランのフォールバック案は `cons_map.get((tk, "FY", "CURRENT"), {})` で再検索するが、QUICKカバレッジ外かつIFIS当期のみの場合、CURRENTキーにも翌期NET_PROFITは存在しない
- トリガー: 2395のようなQUICK未カバー銘柄のFY開示
- 影響: フォールバックが空辞書を返し、NaNのまま変わらない
- 根拠: `bq_consensus.md` L24「IFIS: 当期のみ」、L25「QUICK: FYのみ。当期・来期・再来期」
- 推奨対応: **[方向性]** 診断BQクエリでデータ有無を先に確認。QUICK未カバーが原因なら: (a) predict単日モードではV_CONSENSUS_MERGED VIEWからNET_PROFITを追加取得するパスを設ける (b) backfillモードではCONSENSUSテーブル1-passからQUICKデータが無い銘柄をログに記録し、影響範囲を把握する

### #3 F3 YoY OPも同根の比較軸不整合（横展開漏れ）

- 箇所: `scripts/earnings_model/predict.py:L590-597`, `earnings_model_core.py:L68-74`
- 事象: F3の `yoy_op` はFY期でも4Q単独OPのYoYを使用。7951ヤマハの `YoY OP +1209%` はこの計算結果。F7のbaselineを通期に揃えてもF3が4Q単独のままでは不整合
- トリガー: 4Q単独OPが極端に小さい/大きい銘柄のFY開示
- 影響: F3スコアが異常なYoY値（+1209%等）で発火し、実態と乖離した+1点が加算される
- 根拠: `predict.py:L570-597` で `cur_standalone_op` を計算し4Q単独OPのYoYを算出。FY期では通期OPのYoYを使うべき
- 推奨対応: **[方向性]** FY期のF3 yoy_opを通期OPベースに変更する。ただしF3は全四半期（1Q/2Q/3Q/FY）で使われるため、FY期のみ分岐する設計が必要。スコープに含めるか次回対応とするかはユーザー判断

---

## 【改善提案】（可読性・保守性）

### #1 P1-1 after案の四半期完全性チェック

- 箇所: `docs/plans/earnings-model-fix_20260512_220000.md` P1-1修正方針
- 現状: `set(grp["QUARTER"].tolist()) >= {"1Q", "2Q", "3Q", "4Q"}` で4四半期揃いを判定
- 提案: NaN値を含む四半期もカウントされるため、`grp.dropna(subset=["OPERATING_PROFIT"])` 後に4四半期チェックするか、sum時に `min_count=4` を使う

### #2 診断ステップの明確化

- 箇所: `docs/plans/earnings-model-fix_20260512_220000.md` P0-1
- 現状: Phase 1（診断）とPhase 2（修正）を分けているが、Phase 2が3分岐のまま
- 提案: P0-1は診断BQクエリを実施してから修正方針を確定する2段階アプローチとし、プランを診断結果で更新してから実装に入る

---

## 【確認できなかった事項】

- `STOCK.CONSENSUS` テーブルにおける2395のQUICKデータ有無（BQクエリ未実行）
- `v_fin_summary_actual_for_q_on_q` ビューのQUARTERカラムに "FY" が含まれるかどうか（ビュー定義未確認）
- backfill全期間でのF7スコア変化の定量的影響（実行して確認が必要）
- P1-1の通期集約によるパフォーマンス劣化の程度

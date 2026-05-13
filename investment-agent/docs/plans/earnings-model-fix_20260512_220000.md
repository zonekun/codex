# 決算反応モデル F4/F7因子バグ修正（2395コンセNaN + 7951 baseline不整合）

**作成日時**: 2026-05-12 22:00 JST
**ステータス**: 実装完了（smoke test済み、backfill検証未）
**対象ファイル**: `scripts/earnings_model/predict.py`（1386行, commit 41cba16 時点）、`scripts/earnings_model/earnings_model_core.py`（250行）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: 2026-05-11反省会で発覚した2件のバグを修正する。(1) FY期のコンセンサス乖離F4が一部銘柄でNaNになる問題、(2) F7成長加速/減速の baseline が4Q単独YoYを使用しており通期成長率と比較軸が不整合な問題。スコープ外: F3低ベースYoY抑制（別タスク）、TOWAの半導体テーマ過熱検知（新因子設計が必要）。

---

## 前提サマリ

- 発端: 2026-05-11 決算反省会（Codex実施、伝言板経由）
- 影響銘柄:
  - **2395 新日本科学**: score +4 / 予測UP / 実績DOWN(-11.5%) — F4 consensus_deviation, np_consensus_deviation ともにNaN。BQ V_CONSENSUS_MERGED上はFY202703 NET_PROFIT=5011百万円あり。会社予想NP=35億→コンセ比-30%が本来拾えるべきネガ因子。
  - **7951 ヤマハ**: score +3 / 予測UP / 実績DOWN(-7.6%) — F7「成長加速 (翌期+30% vs baseline-35%)」が発火。翌期+30%は通期OP成長率（292.74億→380億）だが、baseline-35%は4Q単独OP YoYの過去中央値。比較軸が不整合でユーザー指摘。
- 実機検証の有無: 予測JSONは本番GCS保存済み、反省会CSVあり
- 関連ファイル: `C:\tmp\earnings_review\summary_20260511.md`, `C:\tmp\earnings_review\earnings_review_20260511.csv`

---

## 優先度の定義

- **P0**: 日次予測の精度に直接影響し、-30%コンセ未達がスコアに反映されないデータ欠損バグ
- **P1**: F7の比較軸不整合。スコア±1なので影響は限定的だが論理的に誤り

---

## 指摘項目

### P0-1. F4コンセンサス乖離がFY銘柄で取得できないケースがある 🚨

**症状**: 2395 新日本科学のFY開示でconsensus_deviation / np_consensus_deviationがともにNaN。BQにはFY202703のNET_PROFIT=5011百万円が存在する。

**該当**: `scripts/earnings_model/predict.py:L164-L223` (`_build_cons_map_from_df`) + `L708-L723` (F4計算)

```python:L708-L723
if cur_per == "FY":
    cons_next = cons_map.get((tk, "FY", "NEXT"), {})
    cons_next_ord = cons_next.get("ORD_PROFIT")
    cons_next_np = cons_next.get("NET_PROFIT")
```

**根本原因**: FY開示時のF4ルックアップキーの不整合。

- `_derive_current_fy` は直前開示の Q から current_fy を決定。2395 の直前開示は3Q（FY202603）→ current_fy = "202603"
- cons_map のキー: FY202703 のデータは `("2395", "FY", "NEXT")` で格納される（"202703" > "202603"）
- F4コードは `cons_map.get((tk, "FY", "NEXT"), {})` でルックアップ → ここまでは正しい

**したがって cons_map 構築側に原因がある可能性が高い**:

1. **CONSENSUSテーブル内のQUARTER列**: 2395のFY202703コンセンサスは QUARTER="FY" で格納されているか？ → CONSENSUSテーブルのQUARTER値がcons_mapキーの第2要素と一致する必要
2. **QUICK/IFISカバレッジ**: QUICKは`FY`のみ・5項目。IFISは`1Q/2Q/3Q/FY`だがORD_PROFITのみで「当期のみ」。2395がIFISのみカバーの場合、NEXT期のNET_PROFITは取得不可（IFIS=当期のみ）
3. **df_prev_disc 未収録**: `_prev_from`（date_min - 18ヶ月）以前にしか開示がない場合、current_fy_map に載らない

**修正方針**:

Phase 1（診断）: BQクエリで2395のCONSENSUSテーブルのデータ有無を確認

```sql
SELECT * FROM `gmailpj-357912.STOCK.CONSENSUS`
WHERE TICKER = '2395' AND FY LIKE '202703%'
ORDER BY DATAAT DESC LIMIT 5
```

Phase 2（修正 — 原因別）:

- **原因がQUICK未カバー**: `_build_cons_map_from_df` をFY期のみV_CONSENSUS_MERGED対応に変更するか、predict単日モード用にVIEWからデータ取得するパスを追加
- **原因がcurrent_fy_map欠損**: `_prev_from` の遡り期間を拡大（18ヶ月→36ヶ月）、またはdf_fin（当日開示データ）からcurrent_fyを直接導出するフォールバック追加
- **原因がQUARTER値不一致**: cons_mapキーの正規化を追加

```python
# after (フォールバック案: FY期でcons_next空の場合、df_fin行から直接current_fyを導出してCURRENTキーで再検索)
if cur_per == "FY":
    cons_next = cons_map.get((tk, "FY", "NEXT"), {})
    # フォールバック: NEXT が空なら current_fy 直接導出して再検索
    if not cons_next:
        _fy_end = row.get("CurFYEndDt") or row.get("CurFYEd")
        if _fy_end:
            _fy_end_str = str(_fy_end).replace("-", "")[:6]
            # FY開示 → 翌FYのコンセンサスは current_fy と同じFYYYMM
            _next_fy = f"{int(_fy_end_str[:4]) + 1}{_fy_end_str[4:6]}"
            cons_next = cons_map.get((tk, "FY", "CURRENT"), {})
            # CURRENT キーの FY が _next_fy と一致するか検証
```

**呼び出し側への波及**:
- `earnings_model_core.py:L76-L101` — 波及なし（compute_score は row 辞書を受け取るだけ）
- `review_report.py` — 波及なし（consensus_deviation をレポート表示するが計算には関与しない）
- `show_prediction.py` — 波及なし（同上）

**検証**:
1. BQ診断クエリで2395のCONSENSUS/V_CONSENSUS_MERGEDデータを確認
2. 修正後、`predict.py predict --date 20260511` を実行し2395のconsensus_deviation ≈ -0.30を確認
3. backfill `--from 20260511 --to 20260512` で2395以外の既存銘柄のF4値に退行がないことを確認

**ロールバック**: コミット revert で復旧可能。GCS prediction JSONは日付別保存のため、revert後に再予測すれば上書き。

---

### P1-1. F7 baseline_yoy_op が4Q単独OPのYoYで計算されており通期成長率との比較が不整合 ⚠️

**症状**: 7951 ヤマハ FY開示。F7が「成長加速 (翌期+30% vs baseline-35%)」で+1点。翌期+30%は通期OP（292.74億→380億）の成長率だが、baseline-35%は4Q単独OP YoYの過去中央値。4Q単独OPは前年3.72億→当年48.71億で季節性・低ベースの影響が大きく、通期成長率と直接比較すべきでない。

**該当**: `scripts/earnings_model/predict.py:L618-L632` (`baseline_yoy_op_map` 計算)

```python:L618-L632
# ── baseline YoY OP ──
dff = dfq_asof[dfq_asof["QUARTER"] == "4Q"]   # ← 4Q単独OP
baseline_yoy_op_map: dict[str, float] = {}
for tk in tickers:
    tk_fy = dff[dff["tk"] == tk].sort_values("CURRENT_FISCAL_YEAR_START_DATE", ascending=False)
    if len(tk_fy) < 3:
        continue
    ops = tk_fy["OPERATING_PROFIT"].tolist()     # ← 4Q単独OPのリスト
    yoy_list: list[float] = []
    for j in range(len(ops) - 1):
        c, p = ops[j], ops[j + 1]
        if pd.notna(c) and pd.notna(p) and p != 0:
            yoy_list.append(float((c - p) / abs(p)))
    if yoy_list:
        baseline_yoy_op_map[tk] = float(pd.Series(yoy_list).median())
```

**比較先**: `predict.py:L740-L742`
```python
# next_year_op_change は通期OP成長率
if cur_per == "FY" and pd.notna(nx_fop) and pd.notna(op) and op != 0:
    next_year_op_change = float((nx_fop - op) / abs(op))
```

**根本原因**: baseline は4Q単独OP (v_fin_summary_actual_for_q_on_q の QUARTER="4Q") から計算しているが、next_year_op_change は通期（FY累計）OP から計算。比較軸が不一致。

**修正方針**: baseline を通期OP YoYから計算する。`dfq_asof` の全四半期を集約して通期OPを算出し、そのYoYの中央値を baseline とする。

```python
# before
dff = dfq_asof[dfq_asof["QUARTER"] == "4Q"]
baseline_yoy_op_map: dict[str, float] = {}
for tk in tickers:
    tk_fy = dff[dff["tk"] == tk].sort_values(...)
    ...
    ops = tk_fy["OPERATING_PROFIT"].tolist()
    ...

# after
baseline_yoy_op_map: dict[str, float] = {}
for tk in tickers:
    tk_all = dfq_asof[dfq_asof["tk"] == tk]
    fy_ops: dict[str, float] = {}
    for fy_start, grp in tk_all.groupby("CURRENT_FISCAL_YEAR_START_DATE"):
        if set(grp["QUARTER"].tolist()) >= {"1Q", "2Q", "3Q", "4Q"}:
            total = grp["OPERATING_PROFIT"].sum()
            if pd.notna(total):
                fy_ops[str(fy_start)] = float(total)
    sorted_fys = sorted(fy_ops.items(), reverse=True)
    if len(sorted_fys) < 3:
        continue
    yoy_list: list[float] = []
    for j in range(len(sorted_fys) - 1):
        c, p = sorted_fys[j][1], sorted_fys[j + 1][1]
        if p != 0:
            yoy_list.append((c - p) / abs(p))
    if yoy_list:
        baseline_yoy_op_map[tk] = float(pd.Series(yoy_list).median())
```

**呼び出し側への波及**:
- `earnings_model_core.py:L128-L140` — 波及なし（`baseline_yoy_op` のキー名は変わらない）
- `predict.py:L806` — 波及なし（出力辞書に `baseline_yoy_op` を格納する箇所、キー名不変）

**検証**:
1. 修正後、7951ヤマハのbaseline_yoy_opが通期OPベースの値になることを確認（4Q単独の-35%ではなく、通期OPのYoY中央値）
2. backfill `--from 20260501 --to 20260512` で全銘柄のF7スコア変化を集計し、方向一致率への影響を確認
3. 精度集計 `accuracy` で全期間のF7因子ICが改善（または悪化しない）ことを確認

**ロールバック**: コミット revert で復旧可能。

---

## 対応アンチパターン

| plan ID | 004 | T-x | G-x |
|---|---|---|---|
| P0-1 | 該当なし | — | — |
| P1-1 | 該当なし | — | — |

> データパイプラインのキーマッチングバグとメトリクス比較軸の不整合。004のカテゴリには直接該当しないが、「推測ファースト禁止」（データ属性はBQ等で裏取り）に反していた面あり。

---

## 検証戦略

1. **smoke test**: P0-1は `predict.py predict --date 20260511` で2395のconsensus_deviation ≠ NaN を確認。P1-1は7951のbaseline_yoy_opが通期ベースの値に変わることを確認。
2. **dev 実機**: `predict.py backfill --from 20260501 --to 20260512` (12日分) でF4/F7のスコア変化集計。想定BQスキャン量 < 5GB。
3. **本番適用判断基準**: (a) 2395のF4スコアが-3〜-5に下がること (b) backfill全体の方向一致率が悪化しないこと (c) F7でbaseline表示が通期ベースに変わること
4. **回収手順**: revert commit → predict.py backfill で該当期間を再生成。GCS prediction JSONは上書きモード。

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/tools/059_earnings_model_eda.md`（親知見）
- データカタログ: `docs/data_catalog/bq_consensus.md`（CONSENSUS/V_CONSENSUS_MERGEDスキーマ）
- Codex反省会結果: `C:\tmp\earnings_review\summary_20260511.md`
- 伝言板: `C:\Users\zonekun\Documents\codex\investment-agent\docs\codex-to-claude-handoff.md`（2026-05-12エントリ）

---

## 提出前セルフチェック（必須）

- [x] 冒頭に基準 commit hash があるか
- [x] 全項目が 7 フィールド（症状/該当/根本原因/修正方針/呼び出し側波及/検証/ロールバック）を揃えているか
- [x] 修正方針に before/after の両方があるか
- [x] 呼び出し側への波及が行番号リストで明示されているか
- [x] 対応アンチパターン表が末尾にあるか
- [x] 検証戦略が smoke / dev / 本番適用判断基準 / 回収手順の 4 段を網羅しているか
- [x] ロールバック手順があるか
- [x] 「既に〜がある」系の前提を実コードで Read 確認したか

---

## レビュー追記: 2026-05-12 22:20 JST — code-reviewer

→ `docs/reviews/154_cr_earnings_f4_f7_fix.md`

---

## 実装メモ: 2026-05-12 23:22 JST

### 実施内容（レビュー指摘を踏まえた修正）

1. **BQ TVF `F_CONSENSUS_MERGED_ASOF(date)`** 作成 — V_CONSENSUS_MERGED同等ロジック + `DATAAT <= as_of_date`。アドホック・単日クエリ用。
2. **P0-1 修正（3箇所）**:
   - `fetch_shared_data` L393: SOURCE列をクエリに追加
   - `_build_cons_map_from_df` L190-206: `drop_duplicates` → QUICK/IFIS別dedup + outer merge（V_CONSENSUS_MERGED同等）
   - `_process_single_day` L532-538: `df_prev_disc` の同一日dedup順序を修正（FY予想修正を後回しにし3Q実績を優先）
3. **P1-1 修正（1箇所）**:
   - `baseline_yoy_op_map` L629-649: 4Q単独OP → 全四半期groupby集約 → 通期OP YoY中央値。`dff`変数は`median_5y_op_map`用に残存。NaN安全: `dropna(subset=["OPERATING_PROFIT"])` 後に4Q揃いチェック。

### 根本原因（プラン記載の3候補とは別）

P0-1の真の原因: **`df_prev_disc` の同一日dedup順序**。2395は2026-02-06に3Q実績とFY予想修正を同時開示。FY行が先に取られると `_derive_current_fy("FY", ...)` が current_fy を202703に進めてしまい、CONSENSUS FY202703が CURRENT 扱い → F4はNEXTを探す → 空 → NaN。

### smoke test 結果

| 銘柄 | 項目 | 修正前 | 修正後 |
|------|------|--------|--------|
| 2395 | consensus_deviation | NaN | -0.116 |
| 2395 | np_consensus_deviation | NaN | -0.302 |
| 2395 | score / prediction | +4 / UP | -1 / NEUTRAL |
| 7951 | baseline_yoy_op | -0.35 | -0.17 |
| 全体 | distribution | UP79/N67/D34 | UP78/N68/D34 |

### 残タスク

- [ ] backfill `--from 20260501 --to 20260512` で全銘柄F4/F7変化を集計
- [ ] accuracy 全期間でF4/F7因子ICが悪化しないことを確認
- [ ] backfill用as-of VIEW（BQ TVF）のbackfillパス統合は別タスク（現在はpandas側QUICK優先マージで対応）

---

## レビュー追記: 2026-05-13 00:15 JST — code-reviewer

→ `docs/reviews/159_cr_earnings_f4_f7_implementation.md`

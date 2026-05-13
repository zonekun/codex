# 決算反応モデル: 反省会20260512フィードバック改善

**作成日時**: 2026-05-13 16:51 JST
**ステータス**: 完了（backfill未実施）
**分類**: (b) 継続改修型
**親知見 MD**: `docs/knowledges/tools/059_earnings_model_eda.md`
**対象ファイル**: `scripts/earnings_model/predict.py`（1406行）、`scripts/earnings_model/earnings_model_core.py`（250行）、commit f3a3414 時点
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: 反省会20260512のフィードバック7件（Codex調査付き）に基づく因子バグ修正・改善

---

## 起点データ

ユーザーフィードバック JSONL: `C:\tmp\earnings_review\claude_feedback_20260512.jsonl`（7 records）
Codex引継ぎ: `C:\Users\zonekun\Documents\codex\investment-agent\docs\codex-to-claude-handoff.md`

---

## 前提サマリ

- 過去修正: f3a3414 で F7 baseline_yoy_op を通期OP集約に修正済み（2026-05-12）
- 残存: P0×1件（median_5y_opバグ）、P1×2件（低利益率ガード・F5分離）、P2×1件（ログ記録）
- 実機検証の有無: 6644 因子分解は調査済み（`next_year_eps_change=-0.164` がmax()で無視されていた）

---

## 優先度の定義

- **P0**: 低ベース補正の判定に直結するバグ。修正なしだとF5/F7の低ベース補正が不正な値で発火
- **P1**: 次回 backfill 再構築前に消化。精度改善に直結
- **P2**: 記録。知見MDへの反省会ログ追記

---

## 指摘項目

### P0-1. `median_5y_op` が4Q単独OPで計算されている 🚨

**症状**: 5449 大阪製鐵でCodex調査により判明。`median_5y_op = 12.42億`は4Q単独OPの中央値。通期OPの5年中央値は約53.28億。この結果、低ベース判定（`is_low_base`）と F5 の比較対象が不正な値になり、`next_year_op_change = +77.1%` と表示されるが実態は通期中央値比で大幅下振れ。

**該当**: `scripts/earnings_model/predict.py:L633,L654-L660`

```python:L633
dff = dfq_asof[dfq_asof["QUARTER"] == "4Q"]
```

```python:L654-L660
# ── 5年中央値OP ──
median_5y_op_map: dict[str, float] = {}
for tk in tickers:
    tk_fy = dff[dff["tk"] == tk].sort_values("CURRENT_FISCAL_YEAR_START_DATE", ascending=False)
    ops_5y = tk_fy["OPERATING_PROFIT"].dropna().tolist()[:5]
    if len(ops_5y) >= 3:
        median_5y_op_map[tk] = float(np.median(ops_5y))
```

**根本原因**: L633 で `dff = dfq_asof[dfq_asof["QUARTER"] == "4Q"]` にフィルタした後、L657 で `dff` を使って5年中央値OPを計算。`v_fin_summary_actual_for_q_on_q` の `OPERATING_PROFIT` は四半期単独値のため、4Qフィルタ後は4Q単独OPのみが対象。`baseline_yoy_op` は同コミットで通期OP集約に修正されたが、`median_5y_op` は修正漏れ。

**修正方針**: `baseline_yoy_op` ループで構築済みの通期OP(`fy_ops`)を再利用して `median_5y_op` を計算する。

**呼び出し側への波及**:
- `predict.py:L770-L777` — `is_low_base` 判定と `next_year_op_change` の差替え。ロジック変更不要だが、`_median_5y_op` の値が変わることで発火条件が変化
- `earnings_model_core.py` — 変更不要（受け取った値を使うのみ）

**検証**:
1. 5449 大阪製鐵で修正前後の `median_5y_op` 値を比較（期待: 4Q単独12.42億 → 通期53.28億前後）
2. `is_low_base` の発火有無が変わることを確認
3. `show_prediction.py 20260512 5449` で因子分解を確認

**ロールバック**: predict.py の該当ブロックを revert

---

### ~~P1-1. F3 `yoy_op` をFY期のみ通期OPベースに変更~~ → **取り下げ**

ユーザー判断: F3 `yoy_op` は「四半期単独OPの前年同期比」で全Q統一。FY期だけ通期に変えると因子の意味が変わってしまう。7030の+1394%問題はP1-2（低利益率ガード）側で対処。

---

### P1-2. 低利益率企業の事前検知と OP系%因子ガード

**症状**: 7030 スプリックス（+1394%）、7918 ヴィア・HD（+137%/+541%）で、分母（前年同期4Q単独OP）が極小のために%が暴れる。

**設計方針**: 極端YoY%を事後的に閾値で弾くのではなく、**上流で「低利益率/赤字企業」を事前検知**する。

- P0-1修正後の `median_5y_op`（通期OP中央値）が一定以下 → `is_low_profit` フラグ
- フラグが立った企業では OP系%因子（F3 YoY OP / F7 成長加速 / F13 QoQ OP急変 等）を無効化
- 閾値はEDAで `median_5y_op` の分布とフラグ対象銘柄の方向一致率を確認して決定

**検証**: backfill 再構築後に `is_low_profit` 対象銘柄リストと精度への影響を確認

---

### P1-3. F5 をOP/EPS別因子に分離（F5a/F5b）

**症状**: 6644 大崎電気工業で `next_year_eps_change = -16.4%` が取得できていたにもかかわらず、F5の `max(OP成長率, EPS成長率)` ロジックで OP+24% が勝ち、EPS減益が完全にマスクされた。score +5 UP予測 → 実績 -18.3% DOWN の大外し。

**設計方針**: F5を分離し、EPS優先の優先関係を設ける。

- **F5b（EPS成長率）が主因子**: 単独でスコア付与（閾値は現行F5と同じ ±10%）
- **F5a（OP成長率）は従因子**: F5bと同符号の場合のみ有効。F5bと矛盾する（符号が逆）場合はF5aを無効化
- 市場はEPSをより重視する。OP+でもEPS-ならダメ、EPS+でOP-なら市場はEPSを見て反応する

**6644 修正後の期待動作**:
- F5b: EPS -16.4% → -2点
- F5a: OP +24.1% → F5bと矛盾（EPS-なのにOP+）→ **無効化（0点）**
- 現行との差: +2 → -2（4点差。score +5 → +1 に下がり、NEUTRAL予測でDOWN実績との乖離が縮小）

**該当**: `scripts/earnings_model/earnings_model_core.py:L103-L122`（F5スコアリング）、`scripts/earnings_model/predict.py:L760-L777`（データ準備）

**検証**: 6644 で修正前後の因子分解を比較。backfill で F5a/F5b 分離の精度影響を確認

---

### P2-1. 反省会ログへの記録（7件全件）

059知見MDの反省会ログセクションに 20260512 分のログを追記（score/予測/実績の実値埋め含む）。
全修正完了後に最終状態で記録する。

---

## 検証戦略

1. **smoke test**: `show_prediction.py 20260512 5449 7030 6644 7918` で修正前後の因子分解を比較
2. **backfill 再構築**: `predict.py backfill` で全期間再スコアリング → accuracy で精度変化を確認
3. **本番適用判断基準**: smoke test + backfill accuracy が修正前以上で適用
4. **回収手順**: predict.py / earnings_model_core.py の該当ブロックを git revert

---

## 実行順序

1. [x] P0-1: `median_5y_op` を通期OP集約に修正（`predict.py`）
2. [x] smoke test: 5449 で因子分解確認（median_5y_op 修正前後比較）
3. [x] P1-2: `is_low_profit` フラグ実装 + OP系%因子ガード（`predict.py` + `earnings_model_core.py`）
4. [x] P1-3: F5 を F5a(OP)/F5b(EPS) に分離、EPS優先ロジック（`earnings_model_core.py`）
5. [x] smoke test: 6644/7030/7918 で因子分解確認
6. [ ] backfill 再構築（全期間）— 別セッションで実施
7. [x] P2-1: 059知見MD反省会ログ追記 + 因子改善TODOテーブル更新 + 因子クイックリファレンス更新
8. [x] Codex引継ぎファイルのステータスを `done` に更新

---

## 関連ドキュメント

- 親知見: `docs/knowledges/tools/059_earnings_model_eda.md`
- Codex引継ぎ: `C:\Users\zonekun\Documents\codex\investment-agent\docs\codex-to-claude-handoff.md` §2026-05-13
- フィードバック JSONL: `C:\tmp\earnings_review\claude_feedback_20260512.jsonl`
- commit f3a3414: F7 baseline_yoy_op 通期OP集約修正

---

## レビュー追記: 2026-05-13 17:30 JST — code-reviewer

→ `docs/reviews/167_cr_earnings_median5y_f5_lowprofit.md`

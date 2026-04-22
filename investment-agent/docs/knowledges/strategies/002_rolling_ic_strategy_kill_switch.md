# ローリングIC戦略棄却（Rolling IC Strategy Kill Switch）

**カテゴリ**: strategies
**作成日**: 2026-04-02
**ステータス**: NEW
**アイデアソース**: 論文 SIG-FIN-036（河村・久保・中川, 2026）— LLM戦略自動生成実験中にClaude Haiku 4.5が提案した手法
**関連ファイル**:
- `docs/references/web/20260402_sig_fin_036_llm_strategy_feedback.md`

## 概要

任意のアルファシグナルの予測力（IC: Information Coefficient）をローリング窓で監視し、予測力が閾値を下回ったら戦略を停止するメタ手法。

「マーケット環境が悪い」ではなく「このシグナルがもう効いていない」という判断を自動化する。

## IC（Information Coefficient）

シグナルと翌日リターンの銘柄横断Spearman相関:

```
IC_t = SpearmanCorr(signal_t, return_{t+1})   ← 日付tにおける全銘柄横断
```

- IC > 0: シグナルが当たっている
- IC ≈ 0: 予測力なし
- 実務目安: IC平均 0.03〜0.05 で「使えるアルファ」

## 手法

### 基本: バイナリ棄却

```python
rolling_ic = daily_ic.rolling(window=60).mean()

if rolling_ic < threshold:  # e.g. 0.0
    weight = 0  # 戦略停止
else:
    weight = raw_signal
```

### 発展: 確信度加重（Confidence Weighting）

ON/OFFではなくICの大きさに応じてウェイトを連続調整:

```python
confidence = np.clip(rolling_ic / ic_cap, 0, 1)
final_weight = raw_signal * confidence
```

## 用途

- 単一戦略の自動停止（アルファの寿命管理）
- 複数戦略アンサンブル: N個の戦略を走らせ、IC閾値以上のものだけ稼働
- 過剰適合の検出: 訓練期間IC高・OOS期間IC崩壊 → 過学習

## 検証すべきパラメータ

- ローリング窓の長さ（20日? 60日? 120日?）
- 棄却閾値（0.0? 0.01? 0.02?）
- 確信度加重の ic_cap 値
- 棄却後の再開条件（ヒステリシス幅）

## 使用データ

特定のデータソースに依存しない。任意のシグナル + `STOCK.STOCK_PRICE` のリターンで計算可能。

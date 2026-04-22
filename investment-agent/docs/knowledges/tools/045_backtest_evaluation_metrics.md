# バックテスト・モデル評価指標

**カテゴリ**: tools
**作成日**: 2026-03-15
**ステータス**: 有効
**関連ファイル**: `src/backtest/metrics.py`, `src/analysis/statistics.py`（実装予定）
**出典**: Prediction Market Trading Bot (Chapter 3 — Predict & Execute)

## 概要

バックテストの合否判定・モデルの予測精度評価に使う指標の数式と実装方針。

---

## 1. Brier Score（モデルキャリブレーション評価）

```
BS = (1/n) · Σ(pᵢ - oᵢ)²

pᵢ = モデルの予測確率
oᵢ = 実際の結果（0 or 1）
```

- **値が小さいほど良い**（完璧なモデル = 0）
- 用途: 予測モデル（XGBoost 等）の確率キャリブレーション確認
- 合否基準: ランダム予測（BS=0.25）を下回ること

```python
def brier_score(p_pred: np.ndarray, y_true: np.ndarray) -> float:
    return float(np.mean((p_pred - y_true) ** 2))
```

---

## 2. VaR / 日次損失上限（リスク管理）

```
VaR(95%) = μ - 1.645 · σ

μ = 日次リターンの平均
σ = 日次リターンの標準偏差
```

- 95% 信頼水準での最大日次損失を推定
- 新規ポジション追加時に VaR が日次上限を超える場合はブロック
- 推奨上限: 資産の 2%（`config/settings.yaml` で設定）

```python
def var_95(returns: np.ndarray) -> float:
    """95% VaR（正規分布近似）。損失方向が正の値で返る。"""
    return float(-(returns.mean() - 1.645 * returns.std()))
```

---

## 3. Max Drawdown（MDD）

```
MDD = (Peak - Trough) / Peak
```

- MDD > 8% でバックテスト FAIL、新規トレードをブロック
- 本プロジェクトの `config/settings.yaml` では `max_drawdown: 0.20`（要見直し）

```python
def max_drawdown(equity_curve: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity_curve)
    return float(np.max((peak - equity_curve) / peak))
```

---

## 4. Profit Factor

```
PF = gross_profit / gross_loss
```

- PF > 1.5 を健全なボットの目安とする
- Sharpe > 2.0 と合わせてバックテスト PASS 判定に使う

```python
def profit_factor(returns: np.ndarray) -> float:
    gains = returns[returns > 0].sum()
    losses = abs(returns[returns < 0].sum())
    return float(gains / losses) if losses > 0 else float("inf")
```

---

## 5. Bayes Update（ニュースシグナルによる確率更新）

```
P(H|E) = P(E|H) · P(H) / P(E)

H = 株価上昇仮説
E = 観測されたニュース・シグナル
```

- NLP 感情スコア等の各シグナルで事前確率を逐次更新
- 将来的にニュース・開示書類をシグナルとして取り込む際に使用

---

## バックテスト PASS 基準（推奨）

| 指標 | 合格ライン |
|------|-----------|
| Sharpe Ratio | > 1.0（理想 > 2.0） |
| Max Drawdown | < 20% |
| Profit Factor | > 1.5 |
| Win Rate | > 50% |
| Brier Score | < 0.25（ランダム以下） |
| VaR(95%) 日次 | < 資産の 2% |

---

## 6. バックテスト前の指標スクリーニング（オーバーフィット防止）

**出典**: [@aiba_algorithm (2026-03-23)](https://x.com/aiba_algorithm/status/2032981545011859559?s=20)

> バックテストの9割はオーバーフィットで死んでる説
>
> 原因の1つ：指標自体に予測力があるか先に確認してないから。
>
> やった方がいいこと：
> ・将来リターン × 指標の散布図を描く
> ・回帰係数・相関係数を確認
> ・有意でない指標は即切り
>
> この前処理を挟むだけで「たまたまフィットしただけ」の戦略をかなり弾ける。
> バックテストって最適化の自由度が高い分、オーバーフィットしやすいんよな。散布図で先に機械的に確認はした方がいい。

### バックテスト前スクリーニング手順（推奨）

バックテスト本体に入る**前**に、以下を必須ステップとして実施する。

```
1. 指標 × 将来Nday リターン の散布図を描く（目視で外れ値・関係性を確認）
2. Spearman 相関係数を計算（非線形・外れ値に頑健。線形の Pearson と併用）
3. p値 < 0.05 かつ |相関係数| が実用的な大きさであることを確認
4. 複数指標を同時にスクリーニングする場合は Bonferroni 補正を適用
   （有意水準 α' = 0.05 / 検定本数）
5. この検証はバックテスト用の In-Sample 期間の「前半」のみで実施
   後半はスクリーニング後の Out-of-Sample 確認用に温存する
6. 有意でない指標はバックテスト本体に持ち込まない（即脱落）
```

### 注意点・落とし穴

| 落とし穴 | 対処 |
|---------|------|
| 散布図は線形関係しか直感的に確認できない | Spearman 相関 + 分位別平均も確認 |
| 多指標を探索すると「たまたま有意」が生まれる | Bonferroni 補正 or FDR 制御 |
| スクリーニング自体がサンプル内オーバーフィット | スクリーニング期間を In-Sample 前半に限定 |
| 過去の相関が将来も維持される保証はない | OOS 期間でも指標予測力を再確認 |
| 経済的直感のない指標はたまたま有意でも脆い | なぜ効くのか仮説を必ず言語化してから採用 |

---

## 根拠・出典

- Prediction Market Trading Bot, Chapter 3 — Predict & Execute（Core formulas セクション）
- Sharpe・MDD は `docs/knowledges/tools/039_backtest_daily_pnl_model.md` と整合
- バックテスト前スクリーニング: [@aiba_algorithm](https://x.com/aiba_algorithm/status/2032981545011859559?s=20)（2026-03-23）
  - 生テキスト: `docs/references/tweets/20260323_aiba_algorithm_backtest_overfit.md`

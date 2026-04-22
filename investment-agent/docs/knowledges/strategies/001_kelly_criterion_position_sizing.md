# Kelly Criterion によるポジションサイジング

**カテゴリ**: strategies
**作成日**: 2026-03-15
**ステータス**: 有効
**関連ファイル**: `src/trading/risk.py`（実装予定）
**出典**: Prediction Market Trading Bot (Chapter 3 — Predict & Execute)

## 概要

バックテスト PASS 後のデモトレード・本運用において、1銘柄あたりの投資比率を
Kelly Criterion で算出する。フラクショナル Kelly で分散を低減する。

## 数式

### Full Kelly
```
f* = (p·b - q) / b

p = モデル予測勝率
b = 勝ち時のリターン（decimal odds - 1）
q = 1 - p（負け確率）
```

### Fractional Kelly（推奨）
```
f = α · f*,   α ∈ [0.25, 0.5]
```
α = 0.25〜0.5 を使うことで推定誤差による過大投資を防ぐ。

### 運用上限（config/settings.yaml と連動）
```
size <= kelly(f, bankroll)
exposure + bet <= max_exposure       # 例: ポートフォリオの30%
VaR(95%) <= daily_loss_limit         # VaR = μ - 1.645·σ
```

## 実装方針

```python
def kelly_fraction(p_win: float, b_odds: float, alpha: float = 0.25) -> float:
    """Kelly Criterion でポジション比率を算出する。

    Args:
        p_win: モデル予測勝率 (0〜1)
        b_odds: 勝ち時のリターン倍率 (decimal odds - 1)
        alpha: フラクショナルKelly係数 (0.25〜0.5 推奨)

    Returns:
        bankroll に対する投資比率 (0〜1)
    """
    q = 1 - p_win
    f_star = (p_win * b_odds - q) / b_odds
    return max(0.0, alpha * f_star)  # 負値はゼロに丸める
```

## 発注前 4条件チェック（必須）

発注直前にすべての条件をスクリプトで検証する。Claude に判断させない。

```
1. edge = p_model - p_market > 0.04     # エッジが閾値を超えるか
2. size <= kelly(f, bankroll)            # Kelly 上限内か
3. exposure + bet <= max_exposure        # 総エクスポージャー上限内か
4. VaR(95%) within daily limit          # 日次損失上限内か
```

→ **4条件すべて PASS のときのみ発注する。**

## パラメータ設定例（config/settings.yaml）

```yaml
trading:
  kelly_alpha: 0.25          # フラクショナルKelly係数
  min_edge: 0.04             # 最小エッジ閾値
  max_exposure_pct: 0.30     # ポートフォリオに対する最大エクスポージャー
  var_daily_limit_pct: 0.02  # 日次VaR上限（資産の2%）
```

## 根拠・出典

- Prediction Market Trading Bot, Chapter 3 — Predict & Execute
- バックテスト実績（同論文の90日シミュレーション）: Win Rate 68.4%, Sharpe 2.14, MaxDD -4.2%, PF 1.84
- フラクショナルKelly の有効性: 推定誤差に対してフルKellyはオーバーベットになりやすく、0.25〜0.5 倍が実用的とされる

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

## バックテスト評価: 5次元スコアリング

**出典**: [evaluate_backtest.py](../../references/backtest-expert/evaluate_backtest.py)（tradermonty/backtest-expert, MIT License）

閾値ベースの Yes/No 判定ではなく、5次元 × 20点 = 100点満点でスコアリングする。段階的な評価により「ギリギリ通るが怪しい」ケースも適切に警告できる。

### 5次元スコアリング

| 次元 | 配点 | 評価内容 |
|------|------|---------|
| Sample Size | 20 | トレード数 |
| Expectancy | 20 | 1トレードあたり期待値（勝率 × 平均勝ち vs 敗率 × 平均負け） |
| Risk Management | 20 | MDD（0-12点）+ Profit Factor（0-8点） |
| Robustness | 20 | テスト期間（0-15点）+ パラメータ数（0-5点） |
| Execution Realism | 20 | スリッページ/フリクションテスト実施有無（実施=20 / 未実施=0） |

### 各次元のスコアリング詳細

**Sample Size**:
- <30: 0点（統計的に無意味）
- 30-99: 8-14点（線形補間）
- 100-199: 15-19点（線形補間）
- 200+: 20点

**Expectancy**:
- 期待値 = 勝率 × 平均勝ち% - 敗率 × 平均負け%
- ≤0: 0点
- 0-0.5%: 5-10点
- 0.5-1.5%: 10-18点
- ≥1.5%: 20点

**Risk Management**:
- MDD <20%: 12点 / 20-50%: 線形に0点へ / ≥50%: 全体を0点に上書き（壊滅的）
- PF <1.0: 0点 / 1.0-3.0: 線形0-8点 / ≥3.0: 8点

**Robustness**:
- テスト期間: <5年=0 / 5-9年=5-14（線形） / 10年+=15
- パラメータ数: ≤4=5 / 5-6=3 / 7=1 / 8+=0

### 判定基準

| スコア | 判定 | アクション |
|-------|------|-----------|
| ≥70 | **Deploy** | デモトレードへ移行 |
| 40-69 | **Refine** | 弱い次元を特定し改善を検討 |
| <40 | **Abandon** | 棄却。失敗記録テンプレートで知見化 |

### Red Flag（自動検出）

以下のいずれかに該当する場合、スコアに関わらず警告を発する:

| Red Flag | 重大度 | 条件 |
|----------|-------|------|
| 小サンプル | high | トレード数 < 30 |
| スリッページ未テスト | high | slippage_tested = false |
| 壊滅的ドローダウン | high | MDD > 50% |
| 過最適化 | medium | パラメータ数 ≥ 7 |
| テスト期間不足 | medium | テスト期間 < 5年 |
| 負の期待値 | high | expectancy < 0 |
| 結果が良すぎる | medium | 勝率 > 90% かつ MDD < 5% |

### 適用例: 014 FY弱気ガイダンスBT（MC≥1兆, 5日hold）

| 次元 | 入力 | スコア |
|------|------|--------|
| Sample Size | N=21 | **0** (< 30) |
| Expectancy | WR=61.9%, avg_win≈4.3%, avg_loss≈2.7% | **10** |
| Risk Management | MDD=13%, PF≈1.8 | **15** |
| Robustness | 4年, パラメータ2個 | **5** |
| Execution Realism | スリッページ 2bps テスト済み | **20** |
| **合計** | | **50 → Refine** |
| Red Flag | small_sample (N=21 < 30) | 🔴 high |

旧基準（閾値 Yes/No）では PASS 判定だったが、5次元スコアリングでは **Refine** + **Red Flag** となり、サンプル不足の懸念が明示される。

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

## 7. バックテストのデータ取得原則

### BQクエリは最小回数で全データ取得 → pandas加工

パラメータ探索（保有日数・フィルタ閾値など）をBQ呼び出しのループで回すのは禁止。
1回のBQクエリで必要な全データを取得し、以降はpandasで加工・フィルタする。

**悪い例（014 FY弱気ガイダンスBTで実際に発生）:**
```
# 67回BQクエリ実行（本来3回で済んだ）
for hold in [3, 5, 10, 20]:
    for mc in [100億, 500億, 1000億, 3000億, 1兆]:
        for roa in [None, 0.03, 0.08]:
            fetch_exit_prices(client, filtered_trades)   # ← 毎回BQ
            fetch_company_profiles(client, tickers)       # ← 毎回同じ結果
```

**正しいパターン:**
```python
# 1. 全トレード × 最大保有日数分の価格を1回で取得
all_prices = fetch_all_exit_prices(client, all_trades, max_hold=20)

# 2. 企業プロファイルを1回で取得
profiles = fetch_company_profiles(client, all_tickers)

# 3. 以降はpandasでフィルタ・集計
for hold in [3, 5, 10, 20]:
    for mc_threshold in [...]:
        filtered = prices_df[prices_df["rn"] == hold]
        filtered = filtered[filtered["MARKET_CAP"] >= mc_threshold]
        metrics = compute_metrics(filtered)
```

### 設計チェックリスト

| チェック項目 | 対処 |
|-------------|------|
| 同一テーブルを異なる条件で複数回クエリしていないか | UNIONまたは広めの条件で1回取得 |
| パラメータスイープのループ内にBQ呼び出しがないか | ループ外で全件DL→ループ内はpandas |
| 不変のマスタ（YF_STOCK_INFO等）を毎回取得していないか | 1回取得してdict/dfで保持 |
| 保有日数を変えるたびにSTOCK_PRICEを再取得していないか | 最大保有日数分を1回で取得しrn列で切り替え |

### コスト目安

BQクエリ1回あたりのスキャン量はテーブルサイズに依存する。STOCK_PRICE（全銘柄×全日）は数GBあり、67回呼べば数百GBスキャンになる。無料枠（1TB/月）を1セッションで大幅消費するリスクがある。

---

## 根拠・出典

- Prediction Market Trading Bot, Chapter 3 — Predict & Execute（Core formulas セクション）
- Sharpe・MDD は `docs/knowledges/tools/039_backtest_daily_pnl_model.md` と整合
- バックテスト前スクリーニング: [@aiba_algorithm](https://x.com/aiba_algorithm/status/2032981545011859559?s=20)（2026-03-23）
  - 生テキスト: `docs/references/tweets/20260323_aiba_algorithm_backtest_overfit.md`

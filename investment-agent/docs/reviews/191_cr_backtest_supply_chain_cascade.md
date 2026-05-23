# コードレビュー: backtest_supply_chain_cascade.py

- 日時: 2026-05-16 14:30 JST
- 対象: `scripts/backtest_supply_chain_cascade.py`
- パターン: 1 (新規)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: サプライチェーン連想ペア（leader決算→followerドリフト仮説）の決算カスケードバックテスト。12条件（entry×exit×direction）を一括テストし比較表出力
- 品質評価: **B** — ロジック構造は明快だが、MDD計算に根本的な問題があり、先読みバイアスの疑義箇所と BQ SQL インジェクションリスクがある
- 主要リスク:
  1. MDD がポジション単位 return の cumsum で計算されており、時系列上の資産推移を反映していない（同日に複数ポジションが重複する場合に不正確）
  2. aggressive モード (T+0 entry) で leader 決算日当日の follower 始値でエントリーするが、leader 決算発表が引け後の場合に先読みバイアスが発生
  3. f-string による SQL 組立で SQL injection リスク（入力CSV改竄時）

## 【重大な指摘】（即修正）

### #1 MDD計算が時系列equity curveを反映していない

- 箇所: `scripts/backtest_supply_chain_cascade.py:392-395`
- 事象: `np.cumsum(net_rets)` でトレードをリスト順（生成順）に積み上げているが、複数トレードが時間的に重複して走っている場合（同日に複数ペアのポジションが存在）、この cumsum は実際のポートフォリオ equity curve を表さない。また、生成順がエントリー日の時系列順になっている保証もない（pairs のループ順×earnings日順で生成されるため、pair Aの2024年トレードの後に pair Bの2020年トレードが来る）
- トリガー: 複数ペアが存在する時点で常に発生。736ペアのバックテストでは全条件で不正確
- 影響: MDD が過小/過大に評価される。特に生成順がランダムに近いため、結果に意味がない
- 根拠: L392 `equity = np.cumsum(net_rets)` は trades リストの挿入順で累積。`generate_trades` は pairs ループの内側で earnings 日順だが、pair 間の時系列は考慮されていない
- 推奨対応: **[方向性]** trades を `entry_date` でソートしてから cumsum するか、日次ベースの equity curve（各日のアクティブポジションの P&L を合算）を構築して MDD を計算する。前者は最低限の修正、後者はより正確

### #2 aggressive モード (T+0) の先読みバイアス

- 箇所: `scripts/backtest_supply_chain_cascade.py:271-272`
- 事象: `entry_mode == "aggressive"` の場合、`entry_date = leader_earn_date` となり、follower の当日始値 (`OPEN`) でエントリーする。しかし leader の決算発表が当日引け後（15:30以降）であれば、当日始値は発表前の価格であり、「leader決算を見てfollowerに入る」という仮説と矛盾する。当日終値は leader 決算発表の影響を反映するとは限らない（引け後発表の場合は翌日から反映）
- トリガー: leader 決算発表が引け後（日本では決算の大半が引け後発表）のケース
- 影響: aggressive モードの結果が「leader決算を見てからfollowerに入る」戦略を正しく表現していない。実質的にはleader決算と無関係のタイミングでエントリーしている可能性が高い
- 根拠: `fin_summary` の `DISCLOSED_TIME` を確認していない。`compute_leader_return` は close-to-close で計算するが、引け後発表の leader return は翌営業日に反映される
- 推奨対応: **[方向性]** aggressive モードは「leader 決算発表時刻が場中（例: 11:30-15:00）の場合のみ同日エントリー」とするか、aggressive を「T+0 引け後シグナル → T+1 始値エントリー」（= conservative と同義）に再定義する。あるいは DISCLOSED_TIME で場中/引け後を分岐するフィルタを追加する

### #3 f-string SQL 組立による SQL injection リスク

- 箇所: `scripts/backtest_supply_chain_cascade.py:123, 156`
- 事象: `ticker_list = ", ".join(f"'{t}'" for t in tickers)` で生成した文字列を f-string SQL に埋め込んでいる。`date_from` / `date_to` はパラメータ化されているが、ticker は文字列連結
- トリガー: `association_pairs.csv` に `'; DROP TABLE ...--` のようなティッカーが含まれた場合。CSVは `data/master/` にありgit管理されているため現実的リスクは低いが、規約違反
- 影響: BQ SQL injection（BQは DDL を直接実行できないためテーブル削除リスクは低いが、意図しないデータ取得は可能）
- 根拠: `004_coding_conventions.md` タグ `bug:sql-injection`、`002_bigquery.md` で推奨されるパラメータ化パターンに違反
- 推奨対応: **[検証済み]** BQ の `ARRAY` パラメータを使用する: `bigquery.ArrayQueryParameter("tickers", "STRING", list(tickers))` + `WHERE LOCAL_CODE IN UNNEST(@tickers)`。プロジェクト内の他スクリプトで実績パターンあり

### #4 leader_return の計算が DISCLOSED_TIME を無視

- 箇所: `scripts/backtest_supply_chain_cascade.py:207-222`
- 事象: `compute_leader_return` は `earn_date` の close-to-close return を計算するが、引け後発表の場合、当日の株価変動は leader 決算とは無関係。真の "earnings reaction" は翌営業日の始値-前日終値（= overnight gap）に集中する
- トリガー: leader 決算が引け後（15:30以降）に発表された全ケース。日本の決算の7-8割が該当
- 影響: leader_return が決算反応を正確に捕捉できていない。方向判定（long/short）が不正確になり、backtest 全体の有効性が損なわれる
- 根拠: `fin_summary` テーブルには `DISCLOSED_TIME` カラムが存在する（data_catalog確認済み）が、本スクリプトでは取得・使用していない
- 推奨対応: **[方向性]** (A) `DISCLOSED_TIME` を取得し、引け後発表の場合は翌営業日の close-to-close（または open-to-close）を leader_return とする。(B) conservative モードでのみ翌日 leader_return を使用し、結果を比較する

### #5 Sharpe 比の年率化が不正確

- 箇所: `scripts/backtest_supply_chain_cascade.py:380-383`
- 事象: `trades_per_year = 252 / avg_hold_days` で年間トレード数を推定し、`mean/std * sqrt(trades_per_year)` で年率化しているが、これは「各トレードが独立かつ同一分布」「ポジションは逐次的（並行しない）」を仮定している。実際は同時に複数ポジションが走り、トレード間に相関がある
- トリガー: 常に
- 影響: Sharpe が過大評価される傾向。特にポジションが大量に同時オープンしている場合、独立性仮定が大きく崩れる
- 根拠: 736ペア×複数決算で数千〜1万超のトレードが生成され、多くが同時期にアクティブ
- 推奨対応: **[方向性]** 日次リターンの時系列（日次ポートフォリオ P&L / invested capital）を構築し、その mean/std で Sharpe を計算する方がより正確。初版としての簡易計算は許容できるが、結果の解釈時に注意が必要

## 【改善提案】（可読性・保守性）

### #1 トレード生成順序の保証がない

- 箇所: `scripts/backtest_supply_chain_cascade.py:236-359`
- 現状: `generate_trades` は pairs リスト順 × leader_earn_dates 順でトレードを生成するが、pair 間の時系列順序は保証されない。`compute_metrics` や CSV 出力でトレードの時系列分析が必要になった場合、ソートが前提条件
- 提案: `generate_trades` の返り値を `entry_date` でソートして返す（1行追加: `trades.sort(key=lambda t: t.entry_date)`）

### #2 best_trades 選定ロジックが非効率

- 箇所: `scripts/backtest_supply_chain_cascade.py:545`
- 現状: `compute_metrics(best_trades, "").sharpe_annual` をループ反復ごとに再計算している。既に `result.sharpe_annual` が計算済みなのに、best_trades に対して再度 `compute_metrics` を呼ぶのは冗長
- 提案: `best_sharpe` 変数を保持し、`if result.sharpe_annual > best_sharpe` で比較する

### #3 型ヒント docstring の型注釈が戻り値型と不一致

- 箇所: `scripts/backtest_supply_chain_cascade.py:148-150`
- 現状: `fetch_stock_prices` の docstring は `Returns dict: ticker -> date -> {OPEN, HIGH, LOW, CLOSE, VOLUME}` だが、実際は `OPEN` と `CLOSE` のみ取得。戻り値型は `dict[str, dict[date, dict[str, int]]]`
- 提案: docstring を実際の取得カラムに合わせて修正

### #4 hold_days_count の計算が O(N) ループ

- 箇所: `scripts/backtest_supply_chain_cascade.py:313-315`
- 現状: `sum(1 for td in trading_days if entry_date < td <= exit_date)` で全 trading_days をスキャンする。trading_days が ~1500日（6年分）× 数千トレードで性能影響
- 提案: `bisect` で entry/exit のインデックスを求めて差分を取る（既に `next_trading_day` で bisect を使っているので自然）

### #5 print() 使用（structlog 規約違反）

- 箇所: `scripts/backtest_supply_chain_cascade.py:406-419, 423-436, 561-571`
- 現状: 結果出力に `print()` を使用。CLAUDE.md §7 では「print禁止。structlogを使用」と規定
- 提案: テーブル出力は人間可読性のために print が適切なケースではあるが、規約上は structlog 経由が正しい。`log.info("results", table=...)` とするか、`--quiet` フラグで制御する設計を検討

### #6 backtest_design.md 設計原則への準拠度

- 箇所: スクリプト全体
- 現状: 以下の設計原則項目が未実装/未検討
  - **§3-1 Walk-Forward**: パラメータ最適化は無いため不要だが、OOS/IS の分離検証なし
  - **§3-3 時間的ロバスト性**: 年度別分析は実装済み（`print_yearly_breakdown`）で合格
  - **§3-4 レジーム分析**: 未実装（ボラティリティ/トレンド別の成績分解なし）
  - **§3-5 層別分析**: pair_type 別は実装済みだが、時価総額・dependency_pct による層別なし
  - **§5 実行可能性**: conservative (T+1) は「前日引け後シグナル→翌朝寄成」パターンに合致し合格
  - **§4.4 3段階報告**: gross / net / after_tax の3段階を実装済みで合格
- 提案: 初版としてはレジーム分析と追加層別（dependency_pct 閾値別）を次フェーズで実装

## 【修正例】（必要な箇所のみ）

#### #1（MDD）に対する修正案

```python
# before: scripts/backtest_supply_chain_cascade.py:392-395
equity = np.cumsum(net_rets)
running_max = np.maximum.accumulate(equity)
drawdowns = equity - running_max
result.max_drawdown = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0.0

# after: トレードをentry_date順にソートしてからcumsum
sorted_trades = sorted(trades, key=lambda t: t.entry_date)
sorted_rets = [t.net_ret for t in sorted_trades]
equity = np.cumsum(sorted_rets)
running_max = np.maximum.accumulate(equity)
drawdowns = equity - running_max
result.max_drawdown = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0.0
```

#### #3（SQL injection）に対する修正案

```python
# before: scripts/backtest_supply_chain_cascade.py:123-130
ticker_list = ", ".join(f"'{t}'" for t in tickers)
sql = f"""
SELECT DISTINCT LOCAL_CODE AS TICKER, DISCLOSED_DATE
FROM `gmailpj-357912.STOCK.fin_summary`
WHERE LOCAL_CODE IN ({ticker_list})
  ...
"""

# after: パラメータ化
sql = """
SELECT DISTINCT LOCAL_CODE AS TICKER, DISCLOSED_DATE
FROM `gmailpj-357912.STOCK.fin_summary`
WHERE LOCAL_CODE IN UNNEST(@tickers)
  AND DISCLOSED_DATE BETWEEN @date_from AND @date_to
  AND TYPE_OF_DOCUMENT LIKE '%FinancialStatements%'
ORDER BY LOCAL_CODE, DISCLOSED_DATE
"""
job_config = bigquery.QueryJobConfig(
    query_parameters=[
        bigquery.ArrayQueryParameter("tickers", "STRING", list(tickers)),
        bigquery.ScalarQueryParameter("date_from", "DATE", date_from),
        bigquery.ScalarQueryParameter("date_to", "DATE", date_to),
    ]
)
```

## 【確認できなかった事項】

- `fin_summary` の `DISCLOSED_TIME` の実データ分布（引け後vs場中の比率）。引け後が大多数であれば #2/#4 の影響は甚大
- `association_pairs.csv` の `pair_type` カラムの値域（`direct` 以外に何があるか）。pair_type 別分析の有効性に関わる
- STOCK_PRICE テーブルのサバイバーシップバイアス（上場廃止銘柄が含まれているか）。backtest_design.md §2-3 で要求されるが、yfinance 経由データの範囲は未確認
- `next_trading_day` / `prev_trading_day` で使う trading_days が全銘柄の union であるため、個別銘柄が売買停止中の日も trading day として扱われる。薄い銘柄の売買停止日にエントリー/エグジットを試みている可能性

---

## 返却 2026-05-16

- #1 MDD計算: [採用]
- #2 aggressive先読みバイアス: [採用]
- #3 SQL injection: [採用]
- #4 leader_return DISCLOSED_TIME: [異議あり] 方向性は正しいが知識不足。東証は2024-11-05に引け時間を15:00→15:30に変更（クロージングオークション導入）。固定閾値15:00ではなく日付で分岐が必要。修正済み。
- #5 Sharpe年率化: [採用]
- 改善#1〜#6: [採用]

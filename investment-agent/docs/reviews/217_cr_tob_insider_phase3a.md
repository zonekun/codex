# 217 code-review: TOBインサイダースクリーナー Phase 3-A 実装

## レビュー対象ファイル

- `scripts/tob_prediction/screen_tob_insider.py`（差分: dormant_factor / 生値保持 / `add_cross_section_scores()` 新設）
- `scripts/tob_prediction/backtest_tob_insider.py`（差分: v1/v2 比較ロジック / DETECTION_WINDOWS 拡張）

直近コミット: `856afc34` (`feat: 015 Phase 3-A — クロスセクションrank + dormant_factor + 10-15日窓`)

## レビューパターン

**パターン: 新規ロジック（数式・スコア設計）のコードレビュー**

数値計算の正しさ、pandas 操作の妥当性、エッジケース処理（NaN/ゼロ除算/データ不足）に重点を置いてレビューしてください。

## 事象・背景

Phase 2 バックテストで以下の問題が判明:
- `dormant_days=0` の銘柄でスコアが完全に0になる（半数の TOB ターゲット）
- 自分の過去との時系列比較のみのため、「永続的に静止している銘柄」を検出不可

Phase 3-A で次の3点を実装:

### 1. dormant_factor 化（B案 採用）
- 変更前: `dormant_bonus = √(dormant_days/120)` で 0 を許容
- 変更後: `dormant_factor = 0.5 + 0.5 × √(dormant_days/120)` で最低 0.5 を保証
- 既存の `dormancy` は変更せず（dormant_bonus を残置、影響範囲を限定）
- 新規 `dormancy_score_v2` で dormant_factor を使用

### 2. クロスセクション rank
- `compute_ticker_scores` に 生値（`bb_width`, `vol_level`, `range_rank_120d`, `dormant_factor`）を出力追加
- `compute_all_scores` から `add_cross_section_scores()` を新設して呼ぶ
- 日付ごとに `groupby("DATE").rank(pct=True)` で全銘柄横断 rank
- α=0.5 で時系列rank と加重平均
- 銘柄数 50 未満の日は cross-section rank を NaN（信頼性確保）

### 3. 検出窓拡張
- `DETECTION_WINDOWS = [10, 15, 30, 60, 90]`（10/15日は学術的「黄金時間」）
- `SCORE_VARIANTS = ["momentum_score", "momentum_score_v2"]` で v1/v2 比較

## 補足情報

### バックテスト結果（v1 vs v2）
- 30日窓・閾値0.005: 41.8% → 81.8% (+40pts)
- 10日窓・閾値0.005: 23.4% → 68.3% (+45pts)

### 既知の制約・スコープ限定
- バックテストでは TOB銘柄群のみで cross-section rank を取っており、本番運用（screen_tob_insider.py の通常スクリーニング）の挙動とは異なる。本来は全銘柄バックテストが必要（Phase 3-A 残課題）
- AR/CAR は別ターンに切り出し済み（データソース確認が必要）
- `compute_ticker_scores` は既存の v1 スコア計算は維持。v2 は `add_cross_section_scores` 後にのみ計算される

### 重点的に見てほしい点
1. `add_cross_section_scores` の数式・NaN 処理が妥当か（特に `fillna` のフォールバック）
2. `compute_ticker_scores` のフィールド追加がレギュレーション（dtype, length）を満たしているか
3. backtest の run_backtest が v1/v2 両対応に変わった際、結果テーブル列の不在で落ちる箇所がないか
4. `daily_count < min_universe` の NaN マスキングが意図通り（小規模日の cross-section rank 非適用）
5. パフォーマンス: 全銘柄 × 全日付の `groupby("DATE").rank` が現実的な時間で完了するか

### スコープ外（指摘不要）
- 既存 v1 スコア計算ロジック（変更なし）
- BQ 接続/credentials 部分（変更なし）
- AR/CAR・全銘柄バックテストは別ターン

---

## レビュー結果: 2026-05-20 19:30 JST — code-reviewer

- 対象: コミット `856afc34` の差分（`screen_tob_insider.py` / `backtest_tob_insider.py`）
- パターン: 2（既存コード改修）
- レビュアー: Claude (code-reviewer runbook)

### 【サマリー】

- 変更の要約: `compute_ticker_scores` に生値4列を追加し、`add_cross_section_scores()` で日付横断 rank と v2 スコアを付加。バックテストは `tob_map.items()` ベースのループに再構成し v1/v2 を併走比較。
- 品質評価: **B** — 数式の方向性と NaN ガードは概ね妥当だが、`vol_level` を raw volume で cross-section rank している点と、`bb_combined.fillna()` の非対称フォールバックに設計意図の取りこぼしが残る。
- 主要リスク:
  - **Major #1**: `vol_level = ADJ_VOLUME` の raw 値をクロスセクション rank しており、流動性 (時価総額) バイアスが入る。
  - **Major #2**: 時系列 rank が NaN のとき (warm-up 期間) は cs_rank があっても fallback でゼロ化される。「永続静止銘柄を CS で拾う」設計意図と矛盾しないか要確認。
  - **Minor #3**: `daily_count` は bb_width NaN 行も含むため `min_universe=50` 判定が緩い (有効標本ベースでない)。

### 【改修プラン評価】

本コミットは Phase 2 → Phase 3-A の改善実装で、`docs/plans/analysis-015_tob_insider_screener_20260519_194557.md` の Phase 3-A セクションに沿っている。改修プラン MD は refactor テンプレートではなく開発計画フォーマットなのでフォーマット適合性チェックは省略（パターン 4 寄りの実装レビューとして扱う）。

#### 妥当性
- dormant_factor 化は dormant_days=0 ゼロ問題に直接対処しており方向性 OK。
- クロスセクション rank 導入も「永続静止銘柄の検出」という症状に対しては妥当な仮説。
- ただし下記 #2 (非対称 fillna) と #1 (raw volume) のため、**cs_rank の効き方は設計意図より弱まる/歪む可能性**がある。バックテスト v2 +40pts の改善は cs_rank だけでなく dormant_factor 化と dormancy_v2 の式変更の合算効果である点に注意（cs_rank 単独効果は別途切り分けが必要）。

#### 副作用・デグレードチェック
- [x] **v1 スコアは触っていない**: `dormancy_score`, `momentum_score` などの既存列は変更なし。後段の `screen()` も `momentum_score` 参照のままで非破壊。**OK**。
- [x] **`compute_ticker_scores` の戻り値スキーマ拡張**: 旧呼び出し元（`backtest_tob_insider.py`, `screen()` 内, `run_smoke_test`）はいずれも `result[列名]` 形式でアクセスしており、列数増加は問題なし。**OK**。
- [x] **キャッシュ互換性**: `fetch_ohlcv` / `fetch_ohlcv_for_tob` の parquet は OHLCV のみで、スコア列は含まないため互換性問題なし。**OK**。
- [ ] **下流コード**: `screen_tob_insider.py` は `momentum_score` で screening、CSV 出力に v2 列は含まれない（display_cols 未更新）。**意図的なら問題なし**だが、v2 を運用に乗せる際は出力列・しきい値・運用ドキュメントの追従が必要。

#### 抜け漏れ
- [ ] 015 知見 MD への v2 スコア仕様反映（dormant_factor, dormancy_score_v2, momentum_score_v2 の定義式）。submission MD の説明レベルに留まる場合、知見 MD の更新指示を提案。
- [ ] `range_rank_120d` だけ cs_rank が無い（time-series only）。submission MD §3-A.2 の「生値追加」一覧には含めているが、`add_cross_section_scores` 内で `range_rank` の CS 版は計算していない。**意図的なら OK**（range_60/sma は既に正規化済みなので CS の追加情報量は少ない）。明示記載がほしい。

#### 新規リスク
- パフォーマンス: `groupby("DATE").rank(pct=True)` は ~1.1M 行 × 280 グループで pandas 既定実装が O(n log n)。手元見積もりで数〜十数秒オーダー。問題化リスクは低いが、バックテストの「TOB 銘柄群内 CS rank」ではグループサイズが極小 (< 50) の日が大半となり、コードコメントに従えば全行 NaN マスクされる → v2 スコアが大量に 0/NaN になる懸念（後述 #2 参照）。

### 【重大な指摘】

#### #1 Major — `vol_level` が raw volume のため cross-section rank に時価総額バイアスが入る

- 箇所: `scripts/tob_prediction/screen_tob_insider.py:248`, `304`
- 事象: `result["vol_level"] = v.values` (= `ADJ_VOLUME`) を日付横断で `groupby("DATE")["vol_level"].rank(pct=True)` している。raw volume はティッカー間で 2-3 桁の桁差があるため、大型株が常に上位 (1-rank が低い)、小型株が常に下位 (1-rank が高い) となる。
- トリガー: 全銘柄スクリーニング (`screen_tob_insider.py` 本番運用) で cs_rank を有効化したとき。バックテストは TOB 銘柄群内なので顕在化しにくい。
- 影響: `vol_combined = α·(1-vol_rank_120d) + (1-α)·(1-vol_cs_rank)` の cs 項が事実上「小型株度」を測ることになり、`dormancy_score_v2` が小型株偏重になる。本番運用で TOB ターゲットを拾えても、それは「静止小型株」を均一に高得点化しているだけの可能性がある（偽陽性増の懸念）。
- 根拠: `compute_ticker_scores` 内で `bb_width = (4·std)/sma` は per-ticker 正規化済みだが、`v` (= `ADJ_VOLUME`) は absolute 値のまま `result["vol_level"]` に格納されている (`screen_tob_insider.py:248`)。
- 推奨対応 [方向性]: `vol_level` を per-ticker 正規化した値で cs_rank する。代替案:
  - (a) `vol_level = v / v.rolling(120).median()` 等の相対値で渡す
  - (b) `vol_rank_120d` 自体を cs_rank する（rank of rank）
  - (c) `vol_level = ADJ_VOLUME * ADJ_CLOSE`（売買代金）に変えて cs_rank
  
  どれが Phase 3-A の意図に合うかは仮説次第。設計意図を明示してから採用案を選定すること。バックテスト v2 +40pts のうち、cs_rank 単独効果が小さければ raw volume バイアスを直しても結果は大きく変わらない可能性も。

#### #2 Major — `bb_combined.fillna(...)` が時系列 rank 不在時に cross-section rank も捨てる

- 箇所: `scripts/tob_prediction/screen_tob_insider.py:312-319`
- 事象:
  ```python
  bb_combined = (alpha * (1.0 - bb_width_rank)
                 + (1.0 - alpha) * (1.0 - bb_width_cs_rank))
                .fillna((1.0 - bb_width_rank).fillna(0.0))
  ```
  `bb_width_rank` (時系列 120 日 rolling rank) が NaN の場合、`alpha*NaN + (1-α)*(1-cs_rank) = NaN`。続く `.fillna((1-bb_width_rank).fillna(0.0))` は `bb_width_rank` が NaN なので **0.0** にフォールバックする。つまり **時系列 rank が無い行では cs_rank の情報も完全に捨てられる**。
- トリガー: ① 各銘柄の最初 119 日 (DORMANCY_WINDOW warm-up)。② バックテストで TOB 直前 600 日のうち最初 120 日。③ 上場後 120 日未満の新規上場銘柄。
- 影響: submission MD・015 知見 MD で「永続的に静止している銘柄を cross-section で拾う」と謳っている設計意図と矛盾しうる。「永続静止」かつ「自分の時系列が短い」銘柄（新興上場銘柄など）の v2 スコアが 0 になる。
- 根拠: pandas の Series 加算は片方が NaN なら結果 NaN。`bb_width_rank` が `min_periods=DORMANCY_WINDOW (120)` で集計されているため warm-up 中は NaN（`screen_tob_insider.py:187`）。fillna の引数 `(1.0 - bb_width_rank).fillna(0.0)` も同じ NaN を 0 に潰すだけで、cs_rank を活用していない。
- 推奨対応 [方向性]: 「両方ある場合は加重平均、片方しか無ければ存在する方を使う」のが自然。例えば次の優先順位:
  ```
  1. 両方有り → α*(1-ts) + (1-α)*(1-cs)
  2. ts のみ有り → (1-ts)
  3. cs のみ有り → (1-cs)
  4. 両方 NaN → 0
  ```
  実装は `bb_combined = bb_combined.where(bb_combined.notna(), (1-bb_width_rank).fillna(1-bb_width_cs_rank).fillna(0.0))` などで実現可能。Phase 3-A の効果検証としては「ts のみ」「cs のみ」「両方」のバックテストを切り分けて報告するのが望ましい。

#### #3 Minor — `daily_count` 判定が「TICKER 数」で「有効 bb_width 数」ではない

- 箇所: `scripts/tob_prediction/screen_tob_insider.py:300-306`
- 事象: `daily_count = all_scored.groupby("DATE")["TICKER"].transform("count")` は TICKER 列の非 NULL 数なので、`bb_width` が NaN（warm-up 中）の行も 1 とカウントされる。「銘柄数 50 以上」判定は通るが、実際に rank に寄与する有効値は遥かに少ない、というケースが warm-up 期間や履歴薄銘柄の混在時に発生する。
- トリガー: 履歴の浅い銘柄（IPO 直後）が大量に含まれる日、または `compute_ticker_scores` の MIN_DATA_DAYS=180 は通るが 120 日 rolling 未完了の銘柄が多数のとき。
- 影響: cs_rank の信頼性ガードが緩く効く。少数の有効値で `rank(pct=True)` が極端な値を出す可能性。バックテストでは TOB 銘柄群（高々数百）なので顕在化しないが、本番全銘柄運用で warm-up 域では発生し得る。
- 根拠: `groupby.transform("count")` は 非 NULL カウント。TICKER 列はスクリプト全体で常に非 NULL（`screen_tob_insider.py:106-107` で astype(str)）。bb_width は NaN を取り得る。
- 推奨対応 [方向性]: `daily_count = all_scored.groupby("DATE")["bb_width"].transform("count")` のように、cs_rank する列の非 NULL 数で判定する。両列（bb_width, vol_level）の有効数を別々に計算するか、min(両者)で取る。

#### #4 Minor — `for ticker in tob_map` ループで `all_scored["TICKER"] == ticker` の O(N) スキャンを反復

- 箇所: `scripts/tob_prediction/backtest_tob_insider.py:167-168`
- 事象: TOB ticker 数 × all_scored 行数の boolean mask を毎回新規生成。TOB 銘柄数を 200、all_scored を 100k 行とすると 200 × 100k = 2,000 万比較。`groupby("TICKER")` で 1 パスにすれば 10〜30 倍速。
- トリガー: バックテスト全実行。
- 影響: 体感数秒〜十数秒のオーバーヘッド。submission MD でも「パフォーマンス測定が必要なら実行コマンドの提案だけで構いません」とあるため、Critical ではない。
- 推奨対応 [方向性]: `for ticker, ticker_scored in all_scored.groupby("TICKER")` で回し、`tob_map.get(ticker)` で TOB 日を取る。あるいは `all_scored = all_scored.set_index("TICKER")` 後に `loc[ticker]` でアクセス。

#### #5 Minor — `pd.Timedelta(days=window)` で「営業日」ではなく「カレンダー日」を引いている（pre-existing だが拡張で挙動が変わる）

- 箇所: `scripts/tob_prediction/backtest_tob_insider.py:173-178`
- 事象: `w_end = tob_date - 1day`, `w_start = tob_date - N day`（カレンダー日）。週末・祝日が含まれると実検出窓の取引日は N - (週末数) - (祝日数) となる。30 日窓 → 約 20 営業日、90 日窓 → 約 60 営業日。
- トリガー: 新規追加された 10 日窓と 15 日窓。10 日窓は実営業日 6〜7 日、15 日窓は実営業日 10〜11 日となり、submission MD で言及されている「学術的黄金時間 (10-15 営業日)」と差が出る可能性。
- 影響: バックテスト結果の解釈が「カレンダー日窓」ベースになっており、論文等の「営業日窓」と直接比較できない。本番運用の検出窓設計にも影響。
- 根拠: `pd.Timedelta(days=window)` は wall-clock 日数。
- 推奨対応 [方向性]: 営業日窓に切り替える場合は `pd.bdate_range` または `tob_date - pd.tseries.offsets.BDay(window)` を使う。あるいは「カレンダー日窓」であることを 015 知見 MD・submission MD に明記。

### 【改善提案】

#### #6 `range_rank_120d` だけ cs_rank が未実装 — 意図か未対応か明示
- 箇所: `scripts/tob_prediction/screen_tob_insider.py:320`
- 現状: `range_complement = (1.0 - all_scored["range_rank_120d"]).fillna(0.0)` で time-series rank のみ使用。submission MD では cs_rank の生値リストに `range_rank_120d` が含まれていたため、未実装か意図的かが曖昧。
- 提案: 意図的なら docstring か知見 MD に「range は per-ticker 正規化済みのため cs_rank は省略」と明記する。実装するなら `range_60.values` を保持して cs_rank する。

#### #7 警告ログ: cs_rank が全行 NaN になった日数の可視化
- 箇所: `scripts/tob_prediction/screen_tob_insider.py:303-306` 後
- 現状: `daily_count < min_universe` で NaN マスクされた日数が分からない。バックテスト時に「cs_rank が全く効かない日」が大半である可能性が見えない。
- 提案: `bb_cs.isna().mean()` などを `log.info` で出す。バックテストの TOB 銘柄群実行では「ほぼ全行 NaN マスク」となる想定なので、submission MD で「バックテストでは TOB 銘柄群のみで CS rank」と注記した実態がログでも追える。

#### #8 SCORE_VARIANTS 列を一箇所で管理
- 箇所: `scripts/tob_prediction/backtest_tob_insider.py:48`, `137`, `200-204`
- 現状: `momentum_score` / `momentum_score_v2` がコード内に分散ハードコード。
- 提案: バリアントの追加時に変更箇所が増える。`SCORE_VARIANTS` のループで row 構築を回すか、`max_*` 列の自動展開にする。今は v1/v2 だけなので Critical ではない。

#### #9 `compute_all_scores` の戻り値 docstring を更新
- 箇所: `scripts/tob_prediction/screen_tob_insider.py:254-262`
- 現状: docstring に v2 列追加について言及がない。
- 提案: `Returns:` 節に `add_cross_section_scores` を経由して `dormancy_score_v2`, `momentum_score_v2`, `bb_width_cs_rank`, `vol_cs_rank` を含む旨を追記。

### 【パフォーマンス測定の提案】

submission MD の重点 #5「全銘柄 × 全日付の `groupby("DATE").rank` が現実的な時間で完了するか」について、実行は不要との指示なのでコマンドのみ提案:

```bash
# 全銘柄スクリーニング (本番運用と同等。約 4000 銘柄 × 280 営業日)
PYTHONUTF8=1 python -X importtime scripts/tob_prediction/screen_tob_insider.py --date 2026-05-19 2>&1 | tail -30
```

`compute_all_scores_done` ログから `pd.concat` 完了 ～ `add_cross_section_scores` 完了までの時間差を観測。1.1M 行・280 グループの `groupby.rank` で 10 秒以内が目安。30 秒超なら `transform` + Categorical の検討。

### 【確認できなかった事項】

- バックテスト v2 +40pts の改善寄与分解（dormant_factor 単独 / dormancy_v2 式変更単独 / cs_rank 単独）。submission MD §補足にも未記載。`add_cross_section_scores` を呼ばずに dormant_factor だけ適用した中間 v1.5 を測れば cs_rank の純効果が分かる（依頼次第）。
- 「TOB 銘柄群内 CS rank」がバックテストでどの程度 NaN マスクされたか。バックテストで TOB 銘柄群 (~200) が同一日に揃うことは稀で、`daily_count >= 50` が満たされる日付がどれだけあるかは実行ログを見ないと不明。submission MD でも「本来は全銘柄バックテストが必要」と認識済み。



---

## 返却 2026-05-20

- #1 [採用]: vol_level を per-ticker 120日 median 正規化に変更 (`v / v.rolling(120).median()`)
- #2 [採用]: `_combine_ts_cs()` 関数で「両方→加重 / ts のみ / cs のみ / 両方NaN→0」の優先順位を実装
- #3 [採用]: `daily_count` を `bb_width` の非NULL数ベースに変更
- #4 [採用]: backtest を `all_scored.groupby("TICKER")` の 1パスに変更
- #5 [採用]: `pd.tseries.offsets.BDay(window)` で営業日換算に変更（10日窓=実営業日10日に統一）
- #6 [採用]: `add_cross_section_scores` docstring に「range_rank は per-ticker 正規化済みのため CS 省略」と明記
- #7 [採用]: `cs_rank_nan_ratio` を `log.info` で出力（bb=12.1
---

## 返却 2026-05-20

- #1 [採用]: vol_level を per-ticker 120日 median 正規化に変更 (`v / v.rolling(120).median()`)
- #2 [採用]: `_combine_ts_cs()` 関数で「両方→加重 / ts のみ / cs のみ / 両方NaN→0」の優先順位を実装
- #3 [採用]: `daily_count` を `bb_width` の非NULL数ベースに変更
- #4 [採用]: backtest を `all_scored.groupby("TICKER")` の 1パスに変更
- #5 [採用]: `pd.tseries.offsets.BDay(window)` で営業日換算に変更
- #6 [採用]: `add_cross_section_scores` docstring に「range_rank は per-ticker 正規化済みのため CS 省略」と明記
- #7 [採用]: `cs_rank_nan_ratio` を `log.info` で出力（bb=12.1%, vol=34.8%）
- #8 [採用]: backtest の row 構築を `SCORE_VARIANTS` ループで一元化
- #9 [採用]: `compute_all_scores` docstring に v2 列の追加を明記

**再バックテスト結果**: 30日窓・閾値0.005 で v1 50.6%(BDay化で +8.8pts) / v2 85.7%(+3.9pts)。両方改善。

**残課題**:
- 全銘柄バックテスト（偽陽性率測定 + v2 cs_rank の純効果切り分け）
- v1.5 (dormant_factor のみ) 中間バックテストで cs_rank 単独効果の分解
- AR/CAR は別ターン

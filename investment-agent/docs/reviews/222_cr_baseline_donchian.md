# 222: Donchian Breakout ベースラインスクリーナー新規実装レビュー

**提出日**: 2026-05-21
**レビューパターン**: 1（新規実装の品質確認）
**提出者**: メインエージェント

---

## レビュー対象

- `scripts/tob_prediction/baseline_donchian.py`（新規・178行）

## 事象・背景

修正プラン (`docs/plans/analysis-015_insider_pattern_mismatch_20260521_205454.md`) の **Phase 0-3** として、Phase 0-2 (Bollinger Squeeze) と並ぶ第 2 のベースラインを実装。

Phase 0-2 を 2026-03-21〜05-21 の 40 営業日で実行した結果、hit 5 件のうち 4 件が**下落トレンド中の戻り**（4073 ジィ・シィ企画型）で偽陽性、目標パターン 8141 新光商事（3/20 急騰）も拾えなかった。**「BB 上抜け = 絶対値判定」では下落 bounce を排除できない**問題に対し、**Donchian 60 日高値ブレイク = 新高値更新**で「過去 60 日のレジスタンスを破った」事実をベースに偽陽性を除外する設計。

## 実装内容（要点）

Donchian "4-week rule" の 60 日拡張 + Keown & Pinkerton 1981 の abnormal volume 概念:

1. **静止**: 過去 60 日のレンジ (高値-安値)/SMA が直近 120 営業日 percentile **下位 50%** に滞在（ヨコヨコ確認）
2. **発火**: **前日まで close ≤ 過去 60 営業日高値**、かつ **当日 close > 過去 60 営業日高値**（cross-up 新高値更新）
3. **出来高**: 当日 ADJ_VOLUME が **20 日平均の 2 倍以上**

積スコア・独自重み付け一切なし。Phase 0-2 の cross-up 形式を踏襲。

## レビュー観点（特に確認してほしい点）

1. **学術論文準拠の正しさ**: Donchian の 4-week rule を 60 日に拡張した妥当性、レンジ percentile 下位 50% の閾値設定
2. **計算ロジックの正しさ**: `c.shift(1).rolling(60).max()` の look-ahead 防止、cross-up 判定（前日 close ≤ 前日の Donchian 高値）の境界処理
3. **AND 条件評価**: 3 条件すべて満たす日が正しく hit になっているか、`fillna(False)` の挙動
4. **コーディング規約遵守** (`004_coding_conventions.md`): 型ヒント・docstring・structlog・JST・encoding 明示
5. **Phase 0-2 (baseline_bb_squeeze.py) との設計一貫性**: 関数構造・出力フォーマット・空 hits 時の処理

## 補足情報

- 関連プラン: `docs/plans/analysis-015_insider_pattern_mismatch_20260521_205454.md` Phase 0-3
- Phase 0-2 と同じ `screen_tob_insider.fetch_ohlcv` を sys.path 経由で import（レビュー 221 改善-4 で「Donchian 完了後に共通 lib 化検討」と判定済み → 本レビューでも本 PR スコープ外）
- 摘出目標: 8141 新光商事 (2026-03-20 ブレイク)。本実装で hit すべき。Phase 0-4 のバックテストで必達条件
- 偽陽性除外目標: 4073 ジィ・シィ企画型（下落トレンド中の戻り）。Donchian 新高値条件で自動排除されるはず

## スコープ限定

- 共通 lib 化（`fetch_ohlcv` 等の切り出し）は Phase 0-3 完了後の判断事項。本レビュー対象外
- バックテスト性能評価は Phase 0-4 で別途実施
- レビューは**コード品質と論文準拠性の確認**に絞る

---

## レビュー結果

- 日時: 2026-05-21 23:00 JST
- 対象: `scripts/tob_prediction/baseline_donchian.py`（新規 194 行）
- パターン: 1（新規実装）
- レビュアー: Claude (code-reviewer runbook, インライン実行 — 本環境では Agent ツール利用不可のため。`004-1` 追記は補足節に素材のみ記載)

### 【サマリー】

- 変更の要約: Donchian 4-week rule を 60 日に拡張した「過去 60 日 close 最高値の cross-up ブレイク」+「過去 60 日レンジ percentile 下位 50%（ヨコヨコ確認）」+「出来高 20 日平均の 2 倍以上」の AND 3 条件で銘柄を二値抽出するベースラインスクリーナーを新規実装。
- 品質評価: **B+** — レビュー 221 の重大-1（空 hits KeyError）、改善-1（cross-up 化）、改善-2（タイポ）、改善-3（FETCH_DAYS=400）は全て本実装に踏襲され同種バグの再発なし。look-ahead 防止・cross-up 境界・fillna 挙動も正しい。型ヒント・docstring・structlog・JST・encoding 明示も 004 規約遵守。**ただし「ヨコヨコ判定」を当日含む `range_pct` で行っており、Phase 0-2 が `squeeze_streak.shift(1)` で「前日まで静止」を明示していたのと比較すると当日のレンジ情報を判定に混ぜている。**さらに **Donchian の伝統的定義「過去 N 日の HIGH 最高値」ではなく「過去 N 日の close 最高値」を採用**しており（プラン MD と整合のため仕様判断だが）、docstring の「過去 60 営業日の高値」という表現が誤解を招く。
- 主要リスク:
  1. ヨコヨコ判定 (`range_pct`) が当日 HIGH/LOW を含んでいる — Phase 0-2 「前日まで静止」と非対称（改善-1）
  2. Donchian 高値が close ベースであり、ドキュメントの「高値」表記と乖離する（改善-2）
  3. hit 銘柄数が極端に多い日（例: 全市場上昇局面で出来高 2 倍 + 新高値多発）の挙動が未検証 — `--top-n 100` で切られるが出来高比降順以外の判定情報が失われる（改善-3、軽微）

---

### 【重大な指摘】（即修正）

**該当なし** — クラッシュ・データ欠損・誤検出を直接引き起こす欠陥は検出されなかった。レビュー 221 重大-1 (空 hits KeyError) は L176-180 の `if hits.empty:` 早期 return で適切に対応されている。Phase 0-2 で発見されたパターンの再発はない。

---

### 【改善提案】（可読性・保守性）

#### #1 「ヨコヨコだった条件」が当日のレンジを含む — Phase 0-2 との設計非対称

- 箇所: `scripts/tob_prediction/baseline_donchian.py:94-96`、`:109`
- 現状: `range_pct = range_60.rolling(120).rank(pct=True)` の `range_60` は当日の HIGH/LOW を含む 60 日窓で計算され、`cond_quiet = range_pct < 0.50` は**当日のレンジ percentile** をヨコヨコ判定に使う。一方 Phase 0-2 では改善-1 採用後に `cond_squeeze = squeeze_streak.shift(1) >= 20` で**前日まで** Squeeze 連続を明示的に検証している。本実装でも「過去 60 日のレンジが下位 50%（ヨコヨコ**だった**条件）」（プラン MD L96）の「だった」を文字通り解釈するなら、`range_pct.shift(1) < 0.50` を使うべき。
- 影響: 当日の急騰で HIGH が伸びると range_60 が当日寄与で拡大し、`range_pct` が瞬間的に上位に押し上げられる可能性がある。すると「**発火日に限って** ヨコヨコ条件を満たさない」逆挙動になり、ブレイク日が hit から漏れるエッジケースが理論上起こり得る。実データでは 120 日 window の中で 1 日の HIGH 寄与が percentile を 50% 跨ぐほど押し上げるケースは稀だが、本来検出したい「強い発火日」ほど該当しやすい構造的弱点。
- 提案: `cond_quiet = range_pct.shift(1) < RANGE_PCT_THRESHOLD` に変更し、Phase 0-2 の「前日まで静止」設計と揃える。あるいは現状実装のまま「当日含むスナップショット判定」と明示する仕様コメントを L108 周辺に追加し、Phase 0-4 のバックテストで `shift(1)` 有無の hit 件数差を計測してから採否決定する。本指摘は仕様判断の要素もあるが、Phase 0-2 との一貫性の観点では shift 派を推奨。

#### #2 Donchian 高値が close ベース — docstring の「高値」が紛らわしい

- 箇所: `scripts/tob_prediction/baseline_donchian.py:6-12`、`:10`、`:24`、`:82-84`
- 現状: L82 `max_n_prev = c.shift(1).rolling(60).max()` は close 系列の最大値であり、Donchian の伝統的定義「過去 N 日の HIGH の最大値」とは異なる。プラン MD L94「当日終値が過去 60 日の高値を更新」とは整合（プランも close 側を想定）するが、docstring L8 「**過去 60 営業日の高値**」、L10 「**前日まで close <= 過去 60 営業日の高値**」「**当日 close > 過去 60 営業日の高値**」という表記は OHLC の HIGH を指すと誤読される。
- 影響: 本実装では「**過去 60 日中の最高値 close** を当日 close が上抜けた」が成立条件。一方、伝統的 Donchian 4-week rule（HIGH ベース）では「ヒゲで一瞬付けた過去 N 日 HIGH を当日終値が更新」を捉える。close ベースの方が一般に**条件が緩い**（過去のヒゲを参照しないため、終値ブレイク判定がより容易に発火）。本実装はこの仕様で OK だが、ドキュメント表現の不一致は将来の改修・他者レビュー時に「実装バグ?」と誤解される。
- 提案: docstring L8/L10/L24 を「**過去 60 営業日の close 最高値**」または「**過去 60 営業日の終値高値**」と明記し、Donchian の伝統的 HIGH ベースとの違いを 1 行で注記する。あるいは HIGH ベースに変更する（その場合 L82 を `h.shift(1).rolling(60).max()` に変更し、cond_breakout も `c > max_high_60` のままで良い — 「終値が過去 60 日 HIGH を上抜け」は伝統的 Donchian 解釈の中で許容）。**プラン MD と実装の整合性は崩したくないため、docstring 修正のみで充分。**

#### #3 `--top-n` 超過時のソート基準が `vol_ratio_20d` のみ — Donchian 強度情報が欠落

- 箇所: `scripts/tob_prediction/baseline_donchian.py:150-152`
- 現状: hit 銘柄が `--top-n` を超えた場合、`vol_ratio_20d` 降順で先頭 N 件のみ採用。Donchian の核心情報である「**新高値の超過幅** (`(close - donchian_high_60d) / donchian_high_60d`)」が考慮されない。出来高で勝るが新高値超過幅が小さい銘柄が上位に来て、本来優先したい「大きく上抜けた銘柄」が切り捨てられる。
- 影響: 通常時 hit 件数 < 100 なら問題ないが、上昇相場で hit 100 件超になると採用銘柄構成が変わる。Phase 0-4 バックテスト時に上限到達日の評価が出来高比のみで決まり、Donchian らしさを評価できない。
- 提案: 超過幅列 `breakout_excess = (c - max_n_prev) / max_n_prev` を計算し、出力列および出力フォーマットに含める。ソート基準は (a) 出来高比 × 超過幅 の合成、または (b) 出来高比降順は維持しつつ display で超過幅も並記、の 2 択。**Phase 0-2 との一貫性を優先するなら出来高比降順は維持し、補助列追加のみ**で良い。Phase 0-4 でソート基準の影響を比較するときに役立つ。

#### #4 `range_60` の HIGH/LOW 採用と Donchian 高値の close 採用が非対称

- 箇所: `scripts/tob_prediction/baseline_donchian.py:90-93` (range_60), `:82` (max_n_prev)
- 現状: `range_60 = (h.rolling(60).max() - lo.rolling(60).min()) / sma` は HIGH/LOW を使い、`max_n_prev` は close を使う。同じスクリプト内で OHLC の使い分けが説明されていない。
- 影響: 機能的には問題ないが、なぜ片方は HIGH/LOW で、もう片方は close かが不明瞭。コードリーダー（将来の改修者・Phase 0-4 担当者）が「実装ミスでは?」と疑念を持つ可能性がある。
- 提案: コメントで使い分けの意図を明示。例: 「`range_60` は実体レンジを HIGH-LOW で測る方がノイズに強い。一方 cross-up 判定は close を使う方が引け値ブレイクのシグナル性を保てる」のような根拠を 1 行追加。

#### #5 共通 lib 化（改善-4 続報）

- 箇所: `scripts/tob_prediction/baseline_donchian.py:41-44`
- 現状: `sys.path.insert(0, ...) + from screen_tob_insider import fetch_ohlcv` の relative import を Phase 0-2 と同じパターンで再採用。レビュー 221 改善-4 で「Donchian 完了後に共通 lib 化検討」と判定されており、本 PR スコープ外として継続。
- 提案: Phase 0-3 完了 → Phase 0-4 着手前のタイミングで `scripts/tob_prediction/lib_ohlcv.py` 切り出しを実施。`baseline_bb_squeeze.py` と `baseline_donchian.py` の両方をリファクタリングする mini プランを別建てするのが整理しやすい。本 PR では現状 OK。

---

### 【レビュー観点 1〜5 の判定】

| # | 観点 | 判定 | コメント |
|---|------|------|---------|
| 1 | 学術論文準拠の正しさ（4-week → 60 日拡張、レンジ percentile 下位 50%） | OK（仕様判断あり） | Donchian の 60 日化はユーザー指示の拡張で論文厳密性より実用性優先。レンジ percentile 下位 50% は厳密な学術定義ではないが、ヨコヨコ判定のヒューリスティクスとして合理的。プラン MD L92-97 と完全整合。ただし**伝統的 Donchian は HIGH ベース**である点をドキュメントで明示すべき（改善-2）。 |
| 2 | 計算ロジック（look-ahead 防止、cross-up 境界） | OK | `c.shift(1).rolling(60).max()` で当日除外（当日 - 1 日 ～ 当日 - 60 日 の close 最高値）。`max_n_prev.shift(1)` で前日値取得。cross-up 判定 `(c > max_n_prev) & (c.shift(1) <= max_n_prev_prev)` は数学的に正しく、Phase 0-2 改善-1 採用パターンと同型。タイ値 (`==`) で発火しないのは Donchian の "exceeds" 解釈と整合。 |
| 3 | AND 条件評価、fillna(False) 挙動 | OK | warm-up 期間（先頭 ~60 営業日）は `max_n_prev` / `range_pct` / `vol_ma` のいずれかが NaN。pandas の比較演算子は NaN 入力で False を返すため、`cond_quiet & cond_breakout & cond_volume` は warm-up 期間で安全に False になる。`fillna(False)` は実は冗長（比較結果が既に False）だが、防御的記述として残すのは OK。 |
| 4 | 004 規約遵守 | OK | 型ヒント (`pd.DataFrame`, `int`, `str` -> 戻り値型も Google docstring 内で明示)、Google スタイル docstring、structlog、`encoding="utf-8-sig"` (CSV出力)、`JST = timezone(timedelta(hours=+9), "JST")`、`datetime.now(tz=JST)`。全て準拠。`PYTHONUTF8=1` を Usage 記載。`TICKER` は str 型で fetch_ohlcv から受領（004 §TICKER 文字列型）。 |
| 5 | Phase 0-2 (baseline_bb_squeeze.py) との設計一貫性 | 部分一貫 | 関数構造（`evaluate_ticker` / `screen` / `main`）、出力フォーマット (`baseline_<rule>_YYYYMMDD.csv`)、空 hits 時の早期 return、FETCH_DAYS=400、`fetch_ohlcv` 共有、Usage 例の構成、display_cols のスタイル — いずれも一貫。**唯一の非対称は「前日まで静止」の shift 有無**: Phase 0-2 は `squeeze_streak.shift(1)` で前日参照、本実装は `range_pct` を当日参照（改善-1）。論文準拠性・偽陽性除外性能には微小な差しかないが、設計一貫性の観点では揃える方が望ましい。 |

---

### 【Phase 0-2 重大-1〜改善-3 の再発確認】

| Phase 0-2 指摘 | 本実装での該当箇所 | 再発有無 |
|----------------|--------------------|---------|
| 重大-1: 空 hits 時 `hits[display_cols]` KeyError | L176-180 `if hits.empty:` 早期 return | **再発なし**（Phase 0-2 採用パターン踏襲） |
| 改善-1: cross-up 化（前日 close ≤ Upper BB, 当日 close > Upper BB） | L111 `cond_breakout = (c > max_n_prev) & (c.shift(1) <= max_n_prev_prev)` | **再発なし**（cross-up 採用済み） |
| 改善-2: docstring タイポ「ならから DataFrame」 | L70 `データ不足なら空 DataFrame` | **再発なし** |
| 改善-3: FETCH_DAYS=300 → 400 | L57 `FETCH_DAYS = 400` | **再発なし** |
| 改善-4: 共通 lib 化（見送り） | L41-44 sys.path 経由 import 維持 | 同じ仕様で踏襲（改善-5 として継続） |
| 改善-5: hit 列を debug 用に出力（見送り） | display_cols に hit 含まず | 同じ仕様で踏襲 |

→ **Phase 0-2 のレビュー成果が本実装に確実に反映されており、同種バグの再発は確認されなかった。**

---

### 【確認できなかった事項】

- Phase 0-4 のバックテスト結果（lift, TPR/FPR, 8141 新光商事の 2026-03 hit 有無）。プラン MD の必達条件「8141 を 2026-03 ブレイク日 ±5 営業日以内に hit」が満たされるかは実データを走らせないと不明。本レビューは「コードが正しく動くか」「Phase 0-2 との一貫性」に限定。
- 改善-1 の `range_pct.shift(1)` 有無による hit 件数差。本指摘は仕様判断要素を含むため、Phase 0-4 で smoke 計測してから採否を決めることを推奨。
- 偽陽性除外目標 4073 ジィ・シィ企画型（下落トレンド中の戻り）が Donchian 60 日 close 新高値条件で本当に除外されるか。下落トレンド中でも短期的に 60 日 close 最高値を超えるケース（戻り高値が 60 日内最大）は理論上あり得る。プラン MD L164「長期 SMA 上判定で除外」は本実装には未組込（プラン MD では Phase 0-3 の Donchian ルール定義に長期 SMA 条件が含まれていないため意図的な分離）。
- Donchian 原著（Richard Donchian の 4-week rule, 1934 起源）の primary source が `docs/references/` に未取り込み（プラン Phase 0-1 で取り込み予定）。本レビューは「60 日拡張・close ベース」がプラン MD と整合という前提で評価した。

---

### 【補足: 004-1 追記について】

レビュー 221 と同様、本環境では `Agent` ツールが利用不可のため 004-1 への追記をスキップ。Agent ツール復帰後にサブエージェント経由で以下を追記すること（カタログタグは Phase 0-2 で導入済みのものを使用）:

参考追記候補（次回サブエージェント起動時の素材）:
- `[2026-05-21] code:logic-spec-gap | scripts/tob_prediction/baseline_donchian.py:94-96,109 | range_pct を当日含む値で評価しており Phase 0-2 の前日参照と非対称（仕様判断）`
- `[2026-05-21] code:doc-impl-mismatch | scripts/tob_prediction/baseline_donchian.py:6-12,82-84 | docstring「過去 60 営業日の高値」が実装の close 最高値と用語不一致`
- `[2026-05-21] code:sort-key-narrow | scripts/tob_prediction/baseline_donchian.py:150-152 | --top-n 切り捨て時に vol_ratio_20d のみソートで Donchian 超過幅情報が消える（軽微）`

---

## 返却 2026-05-21

- 改善-1: [採用] `range_pct.shift(1)` で前日参照に変更（Phase 0-2 一貫性 + 「だった条件」の文字通り解釈）
- 改善-2: [採用] docstring を「過去 60 営業日 close 最高値」と明記、伝統的 Donchian (HIGH ベース) との違いも注記
- 改善-3: [採用] `breakout_excess = (c - max_n_prev) / max_n_prev` 列追加、display_cols にも組み込み
- 改善-4: [採用] range_60 (HIGH/LOW) と max_n_prev (close) の OHLC 使い分け意図をコメントで明示
- 改善-5: [見送り: Phase 0-3 完了後の別 mini プラン] `lib_ohlcv.py` 共通切り出しは bb_squeeze と donchian の 2 本揃ったタイミングで別途リファクタプラン化

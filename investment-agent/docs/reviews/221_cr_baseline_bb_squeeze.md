# 221: Bollinger Squeeze ベースラインスクリーナー新規実装レビュー

**提出日**: 2026-05-21
**レビューパターン**: 1（新規実装の品質確認）
**提出者**: メインエージェント

---

## レビュー対象

- `scripts/tob_prediction/baseline_bb_squeeze.py`（新規・185行）

## 事象・背景

インサイダー検知スクリーナー (`screen_tob_insider.py`, v1〜v3) で 2026-05-21 Top100 を統計検証した結果、本来の目的「ヨコヨコ→出来高急増→価格上放れ」のパターン適合銘柄がほぼ皆無（全条件 AND = 0/100）であることが判明。

原因として「独自設計の積スコアが中庸銘柄を優遇する構造」「改善のたびに lift がぶれる（v2 で逆効果）」が特定された。

修正プラン (`docs/plans/analysis-015_insider_pattern_mismatch_20260521_205454.md`) の **Phase 0-2** として、学術論文・古典の記述に忠実な単純ルールを「パクって」ベースラインとして実装するもの。本スクリプトはその第1弾。

## 実装内容（要点）

Bollinger 2001（"Bollinger on Bollinger Bands"）の Squeeze ルール + Keown & Pinkerton 1981 の abnormal volume 概念を組合せた **AND 条件のみの二値判定**:

1. **静止**: BB幅 (4σ/sma) の直近 120 営業日 percentile が **下位 10%** に **過去 20 営業日連続** で滞在
2. **発火**: 当日終値が **Upper Bollinger Band を上抜け** (close > sma + 2σ)
3. **出来高**: 当日 ADJ_VOLUME が **20 日平均の 2 倍以上**

積スコア・独自重み付け一切なし。3条件全て True の銘柄のみ抽出し、超過時のみ `vol_ratio_20d` 降順で `--top-n` に絞る。

## レビュー観点（特に確認してほしい点）

1. **学術論文準拠の正しさ**: Bollinger Squeeze の定義（BB幅 percentile 下位 10% / 20日連続滞在）が論文に忠実か。閾値・期間設定の妥当性
2. **計算ロジックの正しさ**: BB幅 = 4σ/sma、Upper BB = sma + 2σ、rolling percentile の look-ahead 防止
3. **AND 条件評価の実装**: `latest snapshot` を取得後に hit 判定する流れ、`_consecutive_true()` の挙動
4. **コーディング規約遵守** (`004_coding_conventions.md`): 型ヒント・docstring・structlog 利用・encoding 明示・JST タイムゾーン明示
5. **既存スクリプトとの整合**: `screen_tob_insider.fetch_ohlcv` を再利用しているが、循環依存の懸念はないか

## 補足情報

- 関連プラン: `docs/plans/analysis-015_insider_pattern_mismatch_20260521_205454.md`
- 親知見MD: `docs/knowledges/analysis/015_tob_insider_screener.md`
- 参考文献ToC: `docs/references/README.md` #28〜#30
- 「オリジナル色ではなく学術論文をパクる」（ユーザー指示 2026-05-21）
- Phase 0-3（Donchian breakout）も後続で別スクリプトとして実装予定（本レビュー対象外）

## スコープ限定

- 既存 `screen_tob_insider.py` の改修は対象外（Phase A 以降）
- バックテスト検証は Phase 0-4 で別途実施するため、性能評価は本レビュー対象外
- レビューは**コード品質と論文準拠性の確認**に絞る

---

## レビュー結果

- 日時: 2026-05-21 22:00 JST
- 対象: `scripts/tob_prediction/baseline_bb_squeeze.py`（新規 185 行）
- パターン: 1（新規実装）
- レビュアー: Claude (code-reviewer runbook, インライン実行 — 本環境では Agent ツール利用不可のため。`004-1` 追記はスキップ)

### 【サマリー】

- 変更の要約: Bollinger 2001 の Squeeze ルール（BB幅 120日 percentile 下位 10% × 20 日連続）＋ Upper BB 上抜け ＋ 出来高 2 倍の AND 3 条件で銘柄を二値抽出するベースラインスクリーナーを新規実装。
- 品質評価: **B** — 計算ロジック（BB幅、SMA、std、`_consecutive_true`、look-ahead 防止）は数学的に正しく、論文記述にも忠実。型ヒント・docstring・structlog・JST・encoding 明示など 004 規約は遵守。ただし**空 hits 時に `hits[display_cols]` で KeyError が出る経路**があり、ユーザーが該当ゼロ日に実行すると即落ちる。修正は数行で済む。
- 主要リスク:
  1. 空 DataFrame に対する `hits[display_cols]` で `KeyError`（main 関数、L171/L177）
  2. 「Upper BB 上抜け」が **新規ブレイク**ではなく **継続的に超過中の銘柄も hit** にする（仕様判断による許容範囲だが、提出 MD の「発火」表現とは乖離）
  3. `evaluate_ticker` で `pd.DataFrame()` を 1 件ずつ append → 末尾で `pd.concat` する形式は OK だが、空 part の append が増えるとログ可視性が落ちる（軽微）

---

### 【重大な指摘】（即修正）

#### #1 hits 空のとき `hits[display_cols]` で KeyError → スクリプト即死

- 箇所: `scripts/tob_prediction/baseline_bb_squeeze.py:171`、`:177`
- 事象: `screen()` が「該当銘柄ゼロ」または「OHLCV データなし」の場合、L130 `return pd.DataFrame()` で**カラム情報を持たない空 DataFrame** を返す。main 側で `hits[display_cols].to_csv(out_path, ...)` を実行すると、`display_cols` に列が存在しないため `KeyError: "['rank', 'TICKER', 'DATE', ...] not in index"` が発生。さらに L177 の `hits[display_cols].head(20).to_string(index=False)` でも同じく KeyError。
- トリガー: ①休場日 / 全銘柄 OHLCV が取得できなかった日、②全銘柄が AND 3 条件を満たさなかった日（実運用では十分起こり得る — そもそも本ベースラインは厳格な AND ルールで「該当ゼロ日」を許容する設計）
- 影響: スクリーナー実行が**例外で異常終了**。CSV すら出力されず、structlog の `done` も呼ばれず、ユーザーから見ると「何が起きたか分からないクラッシュ」になる。Cloud Run Job 化した場合は exit code 1 が返り、上流 Workflow が誤検知連鎖する（004 §A-1 アンチパターン）。
- 根拠: L130 `return pd.DataFrame()` はカラム指定なしの空 DataFrame。L143-148 の通常パスでは `hits` がカラムを持つが、L130 経路だけ素の空 DF。L171/L177 の `hits[display_cols]` はその両方を区別せず参照する。
- 推奨対応: **[検証済み]** main 側で `if hits.empty:` 分岐を `hits[display_cols].to_csv` の **前** に置き、空時は「該当銘柄なし」メッセージのみ出力して CSV 出力をスキップする。あるいは `screen()` 側で `pd.DataFrame(columns=[...])` のように display_cols を含む空 DF を返すよう統一する。後者の方が main 側ロジック簡潔。

  ```python
  # screen() L128-130 around
  if not evaluated_parts:
      log.warning("no_data")
      return pd.DataFrame(columns=["rank", "TICKER", "DATE", "STOCK_NAME",
                                   "ADJ_CLOSE", "upper_bb", "bb_width",
                                   "bb_width_pct_120d", "squeeze_streak_days",
                                   "vol_ratio_20d", "hit"])
  ```

  または main 側で:

  ```python
  # main() L171 around
  if hits.empty:
      print(f"\n=== BB Squeeze ベースライン {target_date} (hits=0) ===")
      print("該当銘柄なし")
      log.info("done", output=None, hits=0)
      return
  hits[display_cols].to_csv(out_path, index=False, encoding="utf-8-sig")
  ```

---

### 【改善提案】（可読性・保守性）

#### #1 「Upper BB 上抜け」が新規ブレイクではなく継続超過も含む

- 箇所: `baseline_bb_squeeze.py:107` `cond_breakout = c > upper_bb`
- 現状: Bollinger 2001 §"Squeeze" の典型例示は「Squeeze 期間 → Upper Band を**新たに**上抜けて release する」イメージだが、本実装は当日 close が Upper BB を上回ってさえいれば hit。つまり「20 日連続 Squeeze 中だが終値が既に 3 日連続で Upper BB の上にいる銘柄」も hit する。前日まで Squeeze 内 → 当日 Upper BB 上抜け、という"発火日"を捉える意図と乖離。
- 提案: 「**前日 close ≤ Upper BB かつ 当日 close > Upper BB**」のクロスオーバー条件にする方が論文の Squeeze release に忠実。あるいは「Squeeze streak ≥ 20 が**今日初めて**崩れる日」を発火日とする実装にする。なお本指摘は仕様判断の問題であり、現状実装も「Squeeze 中に上抜け中」というシグナルとしては妥当。提出MDの「**上抜け**」「**発火**」表現と論文記述のニュアンス揃えるかは Phase 0-4 のバックテスト結果を見て判断するのが現実的。

#### #2 docstring タイポ「データ不足ならから DataFrame」

- 箇所: `baseline_bb_squeeze.py:75`
- 現状: `Returns: 条件評価列を追加した DataFrame。データ不足ならから DataFrame。`
- 提案: 「空 DataFrame」に修正。

#### #3 FETCH_DAYS=300 カレンダー日のマージンが薄い

- 箇所: `baseline_bb_squeeze.py:54`、`:116`
- 現状: 300 カレンダー日 ≒ 205 営業日。BB_PCT_WINDOW=120 + sma 20 日先頭 NaN ＝ 計 138 営業日の warm-up が必要。残り 65 営業日強しか SQUEEZE_STREAK 判定に使えない。MIN_DATA_DAYS=180 銘柄では Squeeze streak >= 20 を満たす最古日が warm-up 終了 + 19 日後（≒ 158 日目）になり、目的の「target_date 1 日分の判定」には足りるが、Phase 0-4 のバックテスト時に過去複数日を遡って評価する場合は不足する可能性がある。
- 提案: バックテスト用途には `FETCH_DAYS=400`（既存 `screen_tob_insider.FETCH_DAYS` と統一）に増やす方が無難。ベースラインのみであれば現状で OK。コメントに「snapshot 評価専用。バックテスト用途では 400 以上に上げること」と明記すると後続混乱を防げる。

#### #4 sys.path 経由の relative import（既存パターン踏襲だが脆い）

- 箇所: `baseline_bb_squeeze.py:39-42`
- 現状: `sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "tob_prediction"))` で `screen_tob_insider` を import。
- 提案: 既存スクリプトのパターン踏襲なので循環依存はないが、Phase 0-3 で `baseline_donchian.py` も同じ `fetch_ohlcv` を import するなら、共通の `lib_ohlcv.py`（004 §scripts/ 共通ライブラリ命名規則）に切り出した方が将来的に安全。現時点では本 PR スコープ外で OK。

#### #5 出力ファイル名・列構成のドキュメント化

- 箇所: `baseline_bb_squeeze.py:166-171`
- 現状: 出力列に `bb_width` を含めず（result には保持されているが display_cols に無い）、`upper_bb` `bb_width_pct_120d` `squeeze_streak_days` `vol_ratio_20d` の 4 つを表示。
- 提案: ユーザー目視確認用の MD 推奨カラムと整合を取ること。Phase 0-4 でバックテスト比較する際、`hit` 列も出力しておくと「閾値ぎりぎりで落ちた銘柄」のデバッグが楽になる（現状 hit=True しか出ない設計なので冗長だが、debug ログ列としてあると便利）。

---

### 【レビュー観点 1〜5 の判定】

| # | 観点 | 判定 | コメント |
|---|------|------|---------|
| 1 | 学術論文準拠の正しさ | OK | BB幅 = 4σ/sma（= (Upper−Lower)/sma）は Bollinger 2001 の Bandwidth 定義。120日 percentile / 20日連続 / 出来高 2倍はいずれも論文・古典の閾値レンジ内。プラン MD と整合。 |
| 2 | 計算ロジック | OK | rolling の `min_periods=W` で先頭欠損を NaN にして look-ahead 防止。`rolling(W).rank(pct=True)` は当日を含む過去 W 日のウィンドウで順位計算 = look-ahead なし。`sma.replace(0.0, np.nan)` のゼロ除算防御も適切。NaN は `NaN < threshold = False` で安全に偽になる。 |
| 3 | AND 条件評価・`_consecutive_true` | OK | `_consecutive_true` は cumsum - cumsum_at_reset 方式で正しく動作（手動トレース: [T,T,F,T,T,T,F,T] → [1,2,0,1,2,3,0,1]）。`latest = ... .groupby("TICKER").last()` で target_date 以前の最新行を抜くロジックも正しい。DATE が `YYYY-MM-DD` 文字列のため辞書順 = 日付順で sort 結果も一致。 |
| 4 | 004 規約遵守 | OK | 型ヒント、docstring（Google スタイル）、structlog 利用、`encoding="utf-8-sig"`（既存 `screen_tob_insider.py:650` と整合）、`JST = timezone(timedelta(hours=+9), "JST")` の標準パターン、`datetime.now(tz=JST).date()`。全て準拠。CSV 外部共有時は CLAUDE.md §6 で `shift_jis` 推奨だが、既存 tob_prediction 系スクリプトが `utf-8-sig` 統一なので踏襲で OK。 |
| 5 | 既存スクリプトとの整合 | OK | `screen_tob_insider.fetch_ohlcv` を再利用、循環依存なし（screen_tob_insider は baseline_bb_squeeze を import しない）。parquet キャッシュ (`C:/tmp/tob_insider_screener/ohlcv_*.parquet`) も共有されるためコスト効率良し。 |

---

### 【確認できなかった事項】

- Phase 0-4 のバックテスト結果（lift, TPR/FPR）は実行ファイル `backtest_full_universe.py` に渡してみないと不明。本レビューは「コードが正しく動くか」に限定。
- 「該当ゼロ日」の頻度: 厳格 AND 3 条件で日次何件 hit するかは実データを走らせないと不明。本指摘 #1（空 DataFrame KeyError）の発火頻度に直結するため、Phase 0-4 で smoke 確認することを推奨。
- Bollinger 2001 書籍そのものは `docs/references/` に未取り込み（プラン Phase 0-1 で取り込み予定）。本レビューは提出 MD §レビュー観点 1 の閾値（120日 percentile 下位 10% / 20日連続 / 出来高 2倍）が論文記述に忠実という前提で評価した。閾値の出典確認は Phase 0-1 完了後に再検証可能。

---

### 【補足: 004-1 追記について】

本来 code-reviewer.md §不備蓄積ログへの追記 に従い `docs/knowledges/tools/004-1_code_review_findings_log.md` への 1 行追記が必須だが、本環境では `Agent` ツールが利用不可（ToolSearch でも TaskCreate しか取得できず、サブエージェント分離ができない）。004-1 §書き込み権限は「Agent ツールでサブエージェントとして起動された reviewer のみ」と明示しているため、メインエージェントによるインライン追記は規約違反となる。よって本タスクでは 004-1 追記をスキップ。Agent ツール復帰後にサブエージェント経由で追記すること。

参考追記候補（次回サブエージェント起動時の素材）:
- `[2026-05-21] code:empty-df-keyerror | scripts/tob_prediction/baseline_bb_squeeze.py:171 | hits 空時に hits[display_cols] で KeyError → スクリプト異常終了`
- `[2026-05-21] code:logic-spec-gap | scripts/tob_prediction/baseline_bb_squeeze.py:107 | Upper BB 上抜けが新規ブレイクでなく継続超過も含む（仕様判断）`

---

## 返却 2026-05-21

- 重大-1: [採用] main 側に `if hits.empty:` 早期 return を追加し KeyError を回避
- 改善-1: [採用] Bollinger 2001 の Squeeze release event に忠実なクロスオーバー実装に変更（前日まで Squeeze 連続 + 当日 cross-up）
- 改善-2: [採用] docstring タイポ修正「ならから DataFrame」→「なら空 DataFrame」
- 改善-3: [採用] FETCH_DAYS=300 → 400（既存 screen_tob_insider.py と統一・バックテスト用途も想定）
- 改善-4: [見送り: Phase 0-3 完了後に判断] Donchian でも同 import するため、2 本揃ったタイミングで共通 lib 化を検討
- 改善-5: [見送り: 現状 hit=True しか出力されない設計のため冗長]

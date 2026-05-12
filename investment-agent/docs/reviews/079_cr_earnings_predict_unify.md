# コードレビュー: 決算反応予測モデル CLI統一プラン

- 日時: 2026-05-05 19:20 JST
- 対象: `docs/plans/tools-059_earnings_predict_unify_20260505_191300.md`
- パターン: 4 (新規計画)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: 2つの実装（Colabノートブック+バッチスクリプト）をCLI 1本に統合し、CONSENSUS v3対応+EPS因子追加を同時に取り込む計画
- 品質評価: **B** — 全体構造は堅実だが、CONSENSUS v3の当期/来期判定ロジック設計が未詳細、backfill時のデータソース切替問題の影響範囲が過小評価
- 主要リスク:
  1. `_derive_current_fy()` の入力データが predict.py の文脈で正しく取得できる保証が設計に含まれていない
  2. as-of TVF が IFIS 旧データ（SOURCE='RAKU'）を返さない期間のバックフィル結果が旧バッチと比較不能になり、検証戦略が成立しない
  3. F4b EPS 乖離の「大きい方を採用」ルールが core.py の既存 `compute_score()` インターフェースと整合しない

## 【パターン4のみ: 新規計画評価】

### 技術選定の妥当性

- **CLI化（argparse + サブコマンド）**: 適切。Colabの起動コストを排除し、ザラ場ツールや将来のスケジューラ連携も容易。過剰設計ではない
- **バッチ側アーキ（共通データ1-pass + per-date処理）の採用**: 妥当。BQクエリ回数最小化の原則にも合致
- **TDnetフォールバック3段の維持**: 妥当。BQのみのバッチは過去データには十分だが、当日予測では未投入データがあるため必要
- **earnings_model_core.py の維持**: 適切。F4b追加もここに集約するのは一貫している

### 既存システムとの統合

- [x] `earnings_model_core.py`: 既存維持。F4b追加で `compute_score()` の入力 Series に新キー (`cons_eps`, `jq_eps` 等) が必要になる点が設計に暗示されているが、具体的なキー名・入力契約が明示されていない
- [x] `zaraba_earnings.py`: `_derive_current_fy()` を「移植」とあるが、この関数は `prev_disc_type` と `prev_disc_fy_end` を引数に取る。predict.py でこれらを取得するには J-Quants `get_fin_summary` の前回行（=BQ `fin_summary` から取得）が必要。バッチの `df_prev` に相当する前処理が作業ステップ3に暗示されているが、単日モードでの BQ クエリ追加の要否が未記載
- [x] `fn_consensus_merged_asof` TVF: backfill 用に使用。VIEW は predict 当日用。TVF の出力カラム（TICKER, FY, QUARTER, REVENUE, OP_PROFIT, ORD_PROFIT, NET_PROFIT, EPS, AS_OF_DATE）とプランの SELECT 文が一致していることを確認した
- [x] GCS パス (`gs://stock_data_1930932/earnings_model/`): 既存と同一パス構造を維持。既存 prediction/actual JSON との互換性が確保される
- [ ] **`data_catalog.md`**: CONSENSUS v3 スキーマは更新済みだが、predict.py が新規ファイルとして追加される際に data_catalog への追記計画が欠落

### リスク・コスト

- **BQ課金**: TVF の as-of 参照は FULL SCAN に近い（PARTITION BY なし）。backfill で日付ごとに TVF を呼ぶ設計は「BQクエリ回数を最小化」の原則に反する。プランの設計では backfill 時も TVF を使うか、バッチの共通データ 1-pass を維持するか曖昧。現状バッチは `WHERE DATAAT <= '{hy(DATE_MAX_PREDICT)}'` で全期間を一括取得し pandas で as-of フィルタする方式であり、こちらが BQ 課金的に有利
- **J-Quants API**: backfill で過去日の `get_fin_summary(date_yyyymmdd=...)` を連続呼び出し。既存バッチと同リスク。日間レート制限 (12回/秒) 内に収まるが、プランに throttle 設計がない
- **処理時間見積もり**: 欠落。現行バッチ19日で全処理何分かの実績値がないと、backfill 30日以上の実行時間を見積もれない
- **撤退基準**: 「git revert で即回収可能」は適切。ただし旧ノートブックを Phase 4 で削除する前に、predict.py が同等精度であることの定量的合格基準が未定義

### 抜け漏れ

- [ ] **backfill の CONSENSUS データ取得方式が未決定**: プランの設計セクションでは TVF（`fn_consensus_merged_asof(DATE '{predict_date}')`) を backfill 用と記載。しかし作業ステップ Phase 2 の item 7 は「バッチ版の共通データ1-pass + per-date処理」と記載。この2つは矛盾する。TVF を日付ごとに呼ぶなら 1-pass ではない。1-pass なら TVF は不要で、全データ取得+pandas as-of フィルタになる
- [ ] **IFIS と QUICK の SOURCE 混在対応**: TVF/VIEW は QUICK 優先・IFIS 補完のマージ済み結果を返す。しかし旧バッチは `SOURCE='RAKU'` でフィルタし ORD_PROFIT のみ使用。新プランでマージ済み結果（EPS等含む）に切り替わると、IFIS 銘柄は EPS=NULL。F4b の「silent skip」は記載されているが、F4a の ORD_PROFIT 値自体が QUICK/IFIS で異なる可能性（観測日のラグ等）が考慮されていない
- [ ] **`cons_map` の新キー構造が `compute_score()` と整合しない**: プランの cons_map 新設計は `(ticker, quarter, "CURRENT"|"NEXT") -> {ORD_PROFIT: ..., EPS: ...}` の dict。しかし `compute_score()` は `row["consensus_deviation"]` (float) を受け取る設計。F4b 追加で `row["eps_consensus_deviation"]` のような新キーが必要になるが、core.py の入力インターフェース変更が Phase 3 作業ステップに記載されていない。PRED_COLUMNS への追加も必要
- [ ] **Colab からのインポート経路維持**: Phase 4 でノートブック削除後、Colab から predict.py を呼ぶユースケースは考慮されているか。§維持するファイルに predict.py のみあり、Colab で直接実行する手段がなくなる（CLI がローカル想定）。これは意図的かもしれないが明示されていない
- [ ] **`059_earnings_model_eda.md` の関連ファイルリスト更新**: 知見 MD の frontmatter `関連ファイル` から旧2ファイルを削除し predict.py を追加する必要あり
- [ ] **ザラ場ツール連携**: `zaraba_earnings.py` の `_derive_current_fy()` は `prev_disc_type` / `prev_disc_fy_end` を要求する。ザラ場ツールでは prior_data.json にこれらが保存されているが、predict.py では J-Quants 当日の fin_summary からは**当日開示のQ情報**しか得られない。前回開示情報は BQ `fin_summary` テーブルから別途取得する必要がある（バッチの `df_prev` に相当）。プランの `_derive_current_fy() 移植` は関数自体のコピーだけでなく、入力データ取得パイプラインの設計が必要

### 目的・スコープの明確性

- 目的は明確: 「2ファイル→1 CLI」「v3対応」「EPS因子追加」の3つが1-2文で言い切れている
- **非スコープの明示が不足**: 以下が In/Out のどちらか不明
  - 既存 GCS JSON の互換性（新 predict.py で保存する JSON と旧ノートブックの JSON でスキーマが同一か）
  - accuracy サブコマンドの可視化（旧ノートブックにある matplotlib チャートは CLI に含まれるのか）

### 段階的検証計画

- Phase 1-4 の分割は論理的に妥当
- ただし **Phase 3（EPS因子）は Phase 1-2 の検証完了を待って着手すべき** という依存関係が明示されていない。Phase 1-2 で旧バッチとの突合がパスしない段階で F4b を追加すると、差分の原因特定が困難になる
- 検証戦略の §1（predict 1日実行で既存GCS結果と突合）は堅実。ただし「スコア・因子一致確認」の一致基準（完全一致 vs 許容差）が未定義。CONSENSUS v3 切替により ORD_PROFIT 値自体が微妙に異なる可能性がある

### 完了条件の検証可能性

- 検証戦略 §1-3 は具体的で検証可能
- ただし「accuracy_summary の方向一致率が同一 or 改善」は、CONSENSUS ソース変更による系統的バイアスの可能性を考えると、「改善」の定義（何%以上の向上で合格か）が必要
- §4 の「git revert で即回収」は回収手順として十分

### データカタログ整合

- `STOCK.CONSENSUS` の v3 スキーマは `data_catalog.md` に更新済み（tools-022プランで確認）
- `V_CONSENSUS_MERGED` VIEW および `fn_consensus_merged_asof` TVF も data_catalog に記載済み
- `STOCK.STOCK_PRICE_JQUANTS`, `STOCK.fin_summary`, `STOCK.v_fin_summary_actual_for_q_on_q` 等の既存テーブルは data_catalog に存在
- **不足**: predict.py が出力する GCS JSON のスキーマ（PRED_COLUMNS + EPS 関連追加列）について data_catalog 更新の計画がない

---

## 【重大な指摘】（即修正）

### #1 backfill 時の CONSENSUS 取得方式が設計内で矛盾

- 箇所: プランMD L89-93（設計§CONSENSUS v3対応）vs L129-130（作業ステップ Phase 2 item 7）
- 事象: 設計セクションでは backfill 用に TVF `fn_consensus_merged_asof(DATE '{predict_date}')` を日付ごとに呼ぶと記載。一方 Phase 2 item 7 では「バッチ版の共通データ1-pass + per-date処理」を採用すると記載。この2つは排他的
- トリガー: 実装開始時に方式を選択する段階で判断に迷い、不整合な実装が生まれる
- 影響: TVF 方式なら BQ 課金が日数分膨張（backfill 30日なら TVF を30回呼び出し）。1-pass 方式なら全 CONSENSUS データを1回取得し pandas 側で as-of フィルタする必要があるが、そのロジック設計が欠落
- 根拠: TVF SQL は `fn_consensus_merged_asof(DATE '{predict_date}')` で1日分のスナップショットを返す設計。1-pass なら `WHERE DATAAT <= '{max_date}'` + pandas groupby で as-of 再現が必要
- 推奨対応: backfill は**既存バッチの 1-pass + pandas as-of 方式を維持**し、TVF は predict 単日用のみに限定する旨を設計に明記。cons_map 構築ロジックの pandas 実装（`DATAAT <= predict_date` で最新行を取る）を設計に追加

### #2 `_derive_current_fy()` の入力取得パイプラインが設計に含まれていない

- 箇所: プランMD L96（当期/来期判定）、L122-123（作業ステップ item 3）
- 事象: `_derive_current_fy()` は `prev_disc_type`（前回開示のQ種別）と `prev_disc_fy_end`（前回開示のFY末日）を必要とする。predict 当日用では「直前の fin_summary 開示」を BQ から取得する必要があるが、この BQ クエリ設計がプランに存在しない
- トリガー: predict 単日実行時、当日の J-Quants fin_summary だけでは前回開示情報がない
- 影響: `_derive_current_fy()` が None を返し、cons_map から CURRENT/NEXT の判定ができず、F4（コンセンサス乖離）が全銘柄で silent skip になる
- 根拠: 既存バッチ `batch_rerun_predict.py:L196-210` では `df_prev`（BQ fin_summary, `DISCLOSED_DATE < predict_date`）を取得し、銘柄ごとの最新行から `TYPE_OF_CURRENT_PERIOD` と `CURRENT_FISCAL_YEAR_START_DATE` を使っている。ただし既存バッチは `_derive_current_fy()` を使わず、旧 TARGET='CURRENT'/'NEXT' をそのまま使用していた
- 推奨対応: predict.py の共通データ取得フェーズに「前回開示の Q/FY 末日を BQ fin_summary から取得」するステップを明記。backfill の場合は 1-pass で取得した df_prev を流用

### #3 F4b (EPS乖離) の core.py インターフェース設計が未定義

- 箇所: プランMD L100-114（EPS コンセンサス因子 F4拡張）、L135（作業ステップ Phase 3 item 9）
- 事象: F4b を `earnings_model_core.py` の `compute_score()` に追加する計画だが、現在の `compute_score()` は `row["consensus_deviation"]` (float, F4a 由来) を受け取る設計。F4b を追加するには (a) 呼び出し元が `eps_consensus_deviation` を row に追加する、(b) core.py 側で max(F4a, F4b) を取る方式にする — のどちらかだが、プランの擬似コード（L108-112）は core.py 外で max 選択する想定に見える一方、作業ステップ item 9 は core.py に F4b 追加と記載
- トリガー: 実装時に F4b を core.py 内に置くか外に置くかで判断が分かれる
- 影響: core.py 内に置く場合、row に `eps_consensus_deviation` キーが必要。PRED_COLUMNS に追加も必要。core.py 外で max 選択する場合、既存の `consensus_deviation` を上書きして渡すことになり、GCS 保存時にどちらの乖離値が記録されたか判別不能
- 根拠: 現行 PRED_COLUMNS に `consensus_deviation` と `f4_source` が含まれる。F4b 追加時に `eps_consensus_deviation` と `f4b_source` を追加するか、既存列を流用するかで GCS JSON の互換性が変わる
- 推奨対応: (a) core.py の `compute_score()` 入力仕様に `eps_consensus_deviation` (Optional[float]) を追加、(b) core.py 内で `max(cd, eps_cd)` 選択してスコア付与、(c) PRED_COLUMNS に `eps_consensus_deviation` を追加、(d) `f4_source` の値に `"EPS_..."` 系を追加 — を設計に明記

---

## 【改善提案】（可読性・保守性）

### #1 Phase 間の依存関係を明示する

- 箇所: プランMD L118-143（作業ステップ全体）
- 現状: Phase 1-4 が列挙されているが、Phase 3 は Phase 1-2 の検証完了を前提とすることが暗黙
- 提案: 各 Phase の冒頭に前提条件（ゲート）を追記。例: 「Phase 3 着手条件: Phase 2 の backfill 結果が旧バッチ精度と ±2pp 以内」

### #2 cons_map の新構造から `compute_score()` 入力への変換レイヤーを設計に含める

- 箇所: プランMD L145-155（CONSENSUS v3 の cons_map 構造）
- 現状: cons_map が nested dict に変わるが、`compute_score()` は flat な row を受け取る。変換レイヤー（cons_map → row keys への展開）がどこに位置するか不明
- 提案: predict.py の特徴量構築ステップ内に「cons_map[tk] -> consensus_deviation (F4a), eps_consensus_deviation (F4b)」の変換ロジックの位置付けを明記

### #3 既存 GCS JSON との互換性ポリシーを明示する

- 箇所: プランMD（全体）
- 現状: accuracy サブコマンドが全期間の actual を集約する際、旧ノートブック出力の JSON と新 predict.py 出力の JSON が同じスキーマである必要があるが、互換性ポリシーが未記載
- 提案: 「既存 GCS prediction/actual JSON のスキーマは維持。新規カラム（eps_consensus_deviation 等）は追加のみ（破壊的変更なし）」を明記

### #4 matplotlib 可視化の扱いを明示

- 箇所: プランMD L39（ロジック比較テーブル「可視化: matplotlib」）
- 現状: 旧ノートブックの精度チャート可視化が predict.py に含まれるかどうか不明
- 提案: accuracy サブコマンドに `--plot` オプションで JupyterLab ノートブック出力にリダイレクトする方式か、CLI では JSON のみ出力し可視化は別途 ipynb で行うかを明記

---

## 【確認できなかった事項】

- `fn_consensus_merged_asof` TVF の実行コスト（FULL SCAN か PARTITION PRUNE が効くか）。TVF 内部で QUALIFY を使っており、DATAAT にパーティションがなければ毎回フルスキャン
- QUICK コンセンサスの DATAAT 最古日。TVF で 2026年4月以前の as-of を指定した場合、QUICK=NULL で IFIS のみが返ることの確認（プランのリスク§に記載はあるが、影響範囲の具体的な銘柄数が不明）
- `get_fin_summary(date_yyyymmdd=...)` の API レスポンスに `CurFYStartDt` / `CurFYSt` のどちらが含まれるか（バッチコード L371 で both をフォールバックしているが、API バージョン依存の可能性）
- accuracy サブコマンドが既存 `accuracy_summary.json` と同一パスに上書きするか、別ファイルにするかの設計意図

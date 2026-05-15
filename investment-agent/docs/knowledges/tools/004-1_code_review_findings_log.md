# コードレビュー不備 蓄積ログ

**カテゴリ**: tools
**作成日**: 2026-04-21
**ステータス**: 有効（追記中）
**適用範囲**: code-reviewer スキル（`skills/code-reviewer.md`）、md-reviewer スキル（`skills/md-reviewer.md`）、および ad-hoc レビュー（スキル非経由を含む）で検出した不備の時系列蓄積

## 書き込み権限

本ファイルへのエントリ追記は **`Agent` ツールでサブエージェントとして起動された code-reviewer または md-reviewer のみ** が行える。

**「起動している」の定義**: メインエージェントがスキルファイルを Read してインラインで手順を実行することは「起動」に該当しない。`Agent` ツールの呼び出し境界で物理的に分離されたサブエージェントのみが「起動している」状態。

**メインエージェント（提出元）は本ファイルを Edit しない。** 不備を発見した場合は reviewer サブエージェントを `Agent` ツールで起動し、サブエージェントに追記させること。

**追記手段**: `Read offset=113 limit=20` で蓄積エントリ先頭を部分読み → `Edit` で最新日付見出し直下に 1 行挿入。**全件 Read は禁止**（トークン効率化）。苦情受付（既存行削除）時のみ従来通り Read + Edit 許可。

## 目的

code-reviewer / md-reviewer がレビューで検出した「プラン不備」「コード不備」「MD可読性不備」を**時系列で蓄積**し、**傾向が見えた段階で** `004_coding_conventions.md` のルール追加 or `skills/code-reviewer.md` / `skills/md-reviewer.md` のチェック項目追加に繋げる。

**運用原則**: 一件ごとの都度対策は禁止。傾向分析の結果として対策を打つ（過剰反応・ルールインフレ防止）。

## 記録フォーマット

各エントリは以下の 1 行:

```
- [YYYY-MM-DD] <tag> | <対象プラン/コード> | <症状の 1 行要約>
```

- `<tag>`: 下記タグカタログから選ぶ（該当がなければカタログに新規追加。ただし既存タグで意味的にカバーできる場合は新規追加しない — タグ爆発を避ける）
- `<対象プラン/コード>`: `docs/plans/<ファイル名>` or `scripts/<ファイル名>:L<行番号>`
- `<症状の 1 行要約>`: 80 文字以内、固有名詞を含める

### 追記位置ルール

- 「蓄積エントリ（新しい順）」直下に日付降順で `### YYYY-MM-DD` 見出しを配置
- 同日のエントリは同一見出し配下に追記（既存見出しがあれば再作成しない）
- 同日内の順序は追記順（末尾追加）

## タグカタログ

### format 系（プラン MD 書式違反）
- `format:commit-hash-missing` — 基準 commit hash が冒頭に無い
- `format:line-number-drift` — 行番号が実コードと乖離（commit 指定無しの結果）
- `format:caller-ref-vague` — 呼び出し側波及が曖昧（該当行リストになっていない）
- `format:antipattern-map-missing` — アンチパターン対応表が末尾に無い
- `format:rollback-missing` — ロールバック手順が欠落
- `format:verification-thin` — 検証戦略が薄い（smoke/dev/prod/回収手順が揃っていない）
- `format:before-after-missing` — 修正方針が after のみで before 対比なし

### content 系（プラン内容の不備）
- `content:unverified-assumption` — 「既に〜がある」系の前提が実コードと食い違い
- `content:priority-inflation` — 読みづらさ・デッドコードだけで P0 に置いている
- `content:missing-downstream` — 呼び出し側への波及の記述漏れ
- `content:data-loss-path` — データロスト経路（silent drop / orphan row）の見落とし
- `content:silent-exception` — 例外握り潰しによる検知不能の見落とし
- `content:orphan-resource` — ジョブ/バッチの orphan（中断時のリソース放置）の見落とし
- `content:similar-bug-uncovered` — 同種バグが他箇所にあるのに横展開していない
- `content:regression-risk-missed` — 修正による regression リスクの記述漏れ
- `content:numeric-inconsistency` — プラン内の数値（件数・比率・見積）が計算と合わない
- `content:architecture-platform-mismatch` — 設計モデルが実行プラットフォームの機能制約と不適合（ランタイム契約を履行不能）
- `content:missing-precondition` — 技法・ツール・コマンドの実行前提条件（入力ファイル・環境・状態）の記載漏れ

### bug 系（コード側の実バグ、プラン未指摘）
- `bug:sql-injection` — f-string SQL 組立・パラメタライズ未実施
- `bug:race-condition` — 並行性バグ
- `bug:resource-leak` — ファイル/接続/ディスクのリーク
- `bug:error-swallowing` — 広義 except で例外を無言で捨てる
- `bug:type-mismatch` — 型の齟齬（None/空/NaN/union 型の扱いミス）
- `bug:partition-prune-loss` — BQ partition prune が効かない SQL
- `bug:ipynb-codegen-escape` — ipynb をプログラム的に生成する際のエスケープ不足（改行・引用符等）
- `bug:aggregation-key-ignored` — 集約キー（QUARTER/FY/SOURCE等）を持つ DataFrame から `iloc[0]` / `first()` / dict comprehension で代表値を取る前にソート・一致確認・tie-breaker が無く、CSV/SQL の並び順依存で誤った行が選ばれる

### md 系（MD AI可読性不備 — md-reviewer スキル由来）
- `md:ambiguous-scope` — 対象範囲・非対象範囲が曖昧
- `md:ambiguous-action` — 修正・反映・整理・確認などの操作内容が曖昧
- `md:overwrite-risk` — 追記・更新・上書きの区別が曖昧
- `md:stale-context` — 古い情報・過去ログ・却下済み案が現行方針に見える
- `md:priority-conflict` — 複数 MD 間で指示優先順位が不明
- `md:reference-vs-rule` — 参考情報と遵守ルールが混在
- `md:example-vs-exhaustive` — 例示と網羅リストが区別されていない
- `md:missing-output-contract` — 出力先・出力形式・完了報告が曖昧
- `md:missing-stop-condition` — 不明時・失敗時・中断時の扱いがない
- `md:missing-source-verification` — 実コードや既存 MD との照合が必要な前提が未確認
- `md:tool-boundary-risk` — AI が許可されていないツールや操作を実行する余地がある
- `md:context-expansion-risk` — AI が対象外ファイル・対象外文脈まで作業を広げる余地がある
- `md:review-recommendation-unsafe` — レビュー推奨自体が上位ルール違反・副作用未検証・正本帰属違反で新規事故を誘発
- `md:discoverability` — MDへの導線が欠落・不十分でAIが到達できない（索引未登録・別名マッピング漏れ・ルックアップ順不備）
- `md:review-quality-low` — レビュー自体の品質不足（対症療法を見抜けない・根本原因分析が浅い・アーキテクチャ選択の妥当性評価が弱い等）

### behavior 系（AI行動違反 — md-reviewer パターン4 由来）
- `behavior:verbatim-copy-violation` — 「そのまま移植」「コピペせよ」等の明示指示に反して独自改変を実施
- `behavior:instruction-escalation-ignored` — ユーザーが指示を強化（繰り返し・表現変更）しても行動が変わらない
- `behavior:unauthorized-tech-change` — ユーザー承認なしに技術スタック（ライブラリ・フレームワーク）を変更
- `behavior:unverified-claim` — 確認可能な事実を確認せずに推測・断定で発言（CLAUDE.md §推測禁止ルール違反）
- `behavior:index-first-violation` — CLAUDE.md §10 高頻度参照テーブル・INDEX.md を確認せずに直接 Grep/Glob で探索した（索引ファースト原則違反）
- `behavior:narrow-scope-prevention` — 再発防止策が目の前の個別事象に限定され、同種パターンの他対象への適用を検討していない

### reviewer 系（レビュアー自身の不備 — 苦情受付窓口由来）
- `reviewer:false-positive` — 実際には問題のない箇所を問題と指摘した
- `reviewer:misread` — コード/MDを誤読して的外れな指摘をした
- `reviewer:scope-overreach` — レビュースコープ外の事象を指摘した
- `reviewer:stale-premise` — 古い前提・廃止済みルールに基づいて指摘した
- `reviewer:unsafe-recommendation` — 推奨対応自体が技術的欠陥を含み、採用すると新規バグ・データロストを引き起こす
- `reviewer:false-negative` — 検出すべき問題を見落とした（レビュー観点として明示されていたにも関わらず未検出）

### plan 系（新規計画 MD の内容不備 — code-reviewer パターン4 由来）
- `plan:scope-unclear` — 計画の目的・スコープが曖昧
- `plan:tech-overengineered` — 技術選定が目的に対して過剰
- `plan:no-phased-validation` — 段階的検証計画（smoke/dev/prod）が欠落
- `plan:catalog-mismatch` — data_catalog.md との不整合
- `plan:missing-field` — テンプレート必須フィールド（7フィールド・アンチパターン表等）の欠落

（該当タグ無ければカタログに追加してから使う。ただし既存タグで意味的にカバーできる場合は新規追加しない — タグ爆発を避ける）

## 蓄積エントリ（新しい順）

### 2026-05-14

- [2026-05-14] plan:scope-unclear | docs/plans/tools-backtest_skill_rebuild_20260514_201840.md Step3-3 | [CR-177] 045からの「移管」方針が045側更新手順欠落+非スコープ宣言と矛盾。二重管理リスク
- [2026-05-14] content:missing-downstream | docs/plans/tools-backtest_skill_rebuild_20260514_201840.md 構成案§7 | [CR-177] failed_tests.md Case Study Framework（失敗記録テンプレート）の統合先が未定義。BACKTEST_FAIL記録手順が脱落
- [2026-05-14] content:missing-precondition | docs/plans/tools-backtest_skill_rebuild_20260514_201840.md Step3-1 | [CR-177] Regime Analysis（VIX→日経VI、Fed→日銀）のローカライズ方針が未記載
- [2026-05-14] content:numeric-inconsistency | docs/plans/tools-backtest_skill_rebuild_20260514_201840.md Step2§5 | [CR-177] 現行スキル合格基準(Sharpe>=0.5)と045基準(Sharpe>1.0)の統一判断が未記載
- [2026-05-14] plan:no-phased-validation | docs/plans/tools-backtest_skill_rebuild_20260514_201840.md 全体 | [CR-177] smoke test（/backtest-design起動確認）ステップが欠落
- [2026-05-14] bug:logic-error | scripts/fy_conservative_guidance_screener.py:69-86 | [CR-176] ACTUAL_METRICとINITIAL_FORECAST_METRICで異なる利益指標（operating_profit vs ordinary_profit等）が混在し得るがclassify_rowで種別一致を検証していない
- [2026-05-14] content:convention-violation | scripts/fy_conservative_guidance_screener.py:872 | [CR-176] main()が常にreturn 0。BQ失敗時もexit 0でA-1アンチパターン違反
- [2026-05-14] bug:logic-error | scripts/fy_conservative_guidance_screener.py:87-94 | [CR-176] REVISION_FORECAST_METRICのCOALESCEがFORECAST_*（当期修正）をNEXT_YEAR_FORECAST_*（翌期修正）より優先。翌期リビジョン検索のJOINと不整合
- [2026-05-14] bug:logic-error | scripts/fy_conservative_guidance_screener.py:34-55 | [CR-176] STOCK_CODE_LISTのTICKER重複排除なし。複数行存在時にinitial_eventsがファンアウト
- [2026-05-14] md:ambiguous-action | docs/knowledges/tools/083_codex_collaboration.md L47 | [MR-175] 取り込み方式「ファイルコピー」が方式名のみで実行手段（Copy-Item/cp）未記載。Read+Writeコピーを許容し887行分トークン浪費
- [2026-05-14] md:stale-context | docs/data_catalog/bq_fin_summary.md L285,L388 | [MR-170] 是正措置でL268-269は修正されたがL285「毎日21:00」・L388「更新（毎日21:00）」が残存。同一ファイル内でスケジュール矛盾
- [2026-05-14] md:missing-stop-condition | docs/knowledges/tools/005_cloudrun_job_deploy.md §⑦ | [MR-170] Scheduler作成手順にドキュメント更新チェックポイント不在。data_catalog同期漏れの構造的原因
- [2026-05-14] md:missing-stop-condition | docs/knowledges/tools/093_monitoring_obligation.md L1-6 | [MR-174] 「監視の定義」が冒頭に不在。手順列挙のみで「ツール設定=監視」の誤認パターンを助長（MR-068→080→174 同根3回目）
- [2026-05-14] md:missing-source-verification | docs/knowledges/tools/093_monitoring_obligation.md 全体 | [MR-173] Monitorスクリプト完了判定条件の推奨テンプレート不在。AIが推測で条件式を構築し未検証で投入（3回目の同種事故: 068→080→173）
- [2026-05-14] bug:logic-error | scripts/edinet_load_parallel.py:556-561 | [CR-172] ticker_from指定時のGCS prefix絞り込みが1銘柄のみスキャン。ticker_from+1以降が全欠落
- [2026-05-14] bug:logic-error | scripts/edinet_load_parallel.py:582-595 | [CR-172] blob名に日付がない場合に日付フィルタがバイパスされ全期間blobが混入
- [2026-05-14] perf:memory-leak | scripts/edinet_load_parallel.py:627-637 | [CR-172] Phase1後にdoc.textがメモリに残り続け512Mi OOMの主因になり得る
- [2026-05-14] perf:unnecessary-init | scripts/edinet_load_parallel.py:1030 | [CR-172] backfillモードで不要なgenai_client初期化（メモリ浪費+権限エラーリスク）
- [2026-05-14] bug:race-condition | scripts/edinet_load_parallel.py:931-948 | [CR-172] DELETE→Load Jobの非原子性。Load失敗時に既存データ喪失
- [2026-05-14] content:convention-violation | scripts/edinet_load_parallel.py:172,482,1198 | [CR-172] print()直接使用。CLAUDE.md §7 structlog規約違反
- [2026-05-14] md:missing-stop-condition | docs/knowledges/tools/005_cloudrun_job_deploy.md 全体 | [MR-171] .gcloudignoreメンテナンス手順・ビルドコンテキスト確認が完全欠落。AIがデプロイ手順に従っても肥大化を検知不能
- [2026-05-14] md:missing-source-verification | docs/knowledges/tools/005_cloudrun_job_deploy.md §⑩ | [MR-171] よくある罠テーブルにビルドコンテキスト肥大化パターンが未登録

### 2026-05-13

- [2026-05-13] content:unverified-assumption | docs/plans/tools-012_edinet_load_refactor P0-3 | [CR-170] custom_id方式を提案するがVertex AI Embedding Batchでの透過が未検証。TDnetはdefaultdict(list)方式で解決済み
- [2026-05-13] plan:no-phased-validation | docs/plans/tools-012_edinet_load_refactor P0-1 | [CR-170] Load Job化後の冪等性設計（重複INSERT防止）が未記載。再実行時に重複行が発生する
- [2026-05-13] content:missing-downstream | docs/plans/tools-012_edinet_load_refactor P0-2 | [CR-170] numpy導入するがDockerfileへの依存追加が計画に含まれていない
- [2026-05-13] bug:error-swallowing | scripts/edinet_load_parallel.py:714-724,790-801 | [CR-170] Embedding結果取得のexcept continue がsilentに失敗を握り潰し。errorsカウンターに合流せずexit 0
- [2026-05-13] content:missing-downstream | scripts/earnings_model/earnings_model_core.py (グループキャップ設計) | [CR-169] キャップ発火時にreasonsとscoreが乖離。キャップ適用のreason行追加が設計に含まれていない
- [2026-05-13] content:regression-risk-missed | scripts/earnings_model/earnings_model_core.py:130-135 | [CR-169] F5翌期予想非開示ペナルティ(-1)を_guidance_groupに蓄積する設計。現時点は安全だが将来グループ拡張時に相殺リスク
- [2026-05-13] plan:ambiguous-scope | docs/plans/tools-059_hanseikai_split_20260513_200110.md P0-1 L72-103 | [CR-168] Bash echo追記のエンコーディング・原子性リスク未記載。Git Bash限定/ヘッダー一括echo化の指針なし
- [2026-05-13] plan:missing-field | docs/plans/tools-059_hanseikai_split_20260513_200110.md P0-1 L112-113 | [CR-168] CLAUDE.md索引更新方針が「任意」のまま。不要の根拠（手順は059本体に残るため）が未記載
- [2026-05-13] content:regression-risk-missed | scripts/earnings_model/earnings_model_core.py:113-128 | [CR-167] F5a/F5b分離でスコア上限が旧+2/-2から最大+4/-4に拡大。プランに影響分析なし
- [2026-05-13] content:missing-downstream | scripts/earnings_model/show_prediction.py:112-137 | [CR-167] F5b(EPS)導入でnext_year_eps_changeが主因子になったがshow_prediction FIELD_ORDERに未追加
- [2026-05-13] plan:missing-field | docs/plans/tools-059_earnings_model_eda_20260513_165133.md P1-2 | [CR-167] 7フィールド中4フィールド欠落（該当/根本原因/呼び出し側波及/ロールバック）
- [2026-05-13] format:antipattern-map-missing | docs/plans/tools-059_earnings_model_eda_20260513_165133.md | [CR-167] アンチパターン対応表セクション欠落
- [2026-05-13] code:copypaste | scripts/extract_monthly_data.py:3700-3720,3871-3891,4074-4094 | [CR-001] Gemini Cloud Run/ローカル分岐ロジックが3箇所コピペ。ヘルパー関数抽出で保守性改善可
- [2026-05-13] code:copypaste | scripts/extract_monthly_data.py:3495-3500 vs 3700,3871,4074 | [CR-001] TDnetパスはphase_extract.__dict__キャッシュ、非TDnet3パスは毎ticker新規Client生成。初期化パターン不統一
- [2026-05-13] content:ambiguous-scope | skills/monthly-error-autofix.md:292 + プランP0-1(a) | [CR-166] ガードレール#6と新#8の適用スコープ（本番ループ vs ローカルループ）が文面上区別不能。AIが#6を先に発動し#8が縮退するリスク
- [2026-05-13] content:similar-bug-uncovered | skills/monthly-error-autofix.md:145-171 + プランP0-1(a) | [CR-165] BC値逆引き(a)とStep 3C bc_ignore判定フローの逆引き作業が重複。棲み分け指針なし
- [2026-05-13] content:missing-precondition | プランP0-1(b) reconcile_bc_key_from_compare.py | [CR-165] compare CSV未生成時にreconcileスクリプト実行不能。前提条件の記載漏れ
- [2026-05-13] plan:missing-field | docs/plans/tools-042_skill_bc_reverse_lookup_20260513_164554.md | [CR-165] テンプレ7フィールド中4フィールド欠落（根本原因/呼び出し側波及/検証/ロールバック）。アンチパターン対応表も欠落
- [2026-05-13] content:similar-bug-uncovered | scripts/extract_monthly_data.py:3617-3631 | [CR-164] regex PDFフォールバック修正で_has_row_regex=Falseのアダプターは依然BQ full_textに落ちる。設計意図の明示欠如
- [2026-05-13] md:stale-context | docs/knowledges/tools/042-1_monthly_error_fix_patterns.md:L412 | [CR-164] E5-1参照ソースL3611が修正後の行番号とずれ。知見MD更新漏れ
- [2026-05-13] md:discoverability | docs/reviews/NNN_mr_*.md（P-001事例テーブルから） | [SO-162] P-001事例テーブルに個別MRレビューへの参照列が欠如。パターンDBから深層分析への到達不能
- [2026-05-13] md:ambiguous-scope | docs/knowledges/tools/004_coding_conventions.md:L511-513 | [SO-162] 事故パターンDB・MRレビュー・004-1蓄積ログの3者の責務境界が未明文化。情報分散の設計意図が暗黙
- [2026-05-13] md:missing-stop-condition | docs/knowledges/tools/004_coding_conventions.md:L536 | [SO-162] P-001防止策メタルールに自信度バイアスによる構造的限界の注記なし。フローチャートの実効性限界がAIに不明示
- [2026-05-13] md:discoverability | docs/knowledges/tools/083_codex_collaboration.md:L31-37 | [MR-161] 083取り込みフローにCLAUDE.md §4.3「プランMD更新」へのポインタ欠如。取り込み完了時にプランMD更新に到達不能
- [2026-05-13] md:missing-output-contract | docs/knowledges/tools/083_codex_collaboration.md:L25-28 | [MR-161] 伝言板テンプレの必須フィールドに「関連計画MD:」欠如。P-002防止策1が未インプリ
- [2026-05-13] bug:resource-leak | scripts/zaraba_earnings.py:1880-1883 | [CR-160] F10 PDF fetchがThreadPoolExecutor外のメインスレッドで同期実行。決算集中日に複数自社株買い開示で最大10秒×N件のポーリング遅延
- [2026-05-13] content:missing-downstream | scripts/zaraba_earnings.py:1223,1543 | [CR-160] poller._session（private属性）を外部から直接参照。ポーラーリファクタリング時にAttributeError
- [2026-05-13] behavior:index-first-violation | CLAUDE.md:SS3,SS4.1 / memory:feedback_data_catalog_first.md | [MR-160] BQ TVF新規作成時にdata_catalog.md・BQ既存リソースを確認せず重複TVF作成。確認ルール3件が「参照時」トリガーで「作成時」に未発火
- [2026-05-13] md:stale-context | docs/knowledges/tools/022_consensus_load.md:L21 | [MR-160] 旧TVF fn_consensus_merged_asof がDROP済みだがL21に参照残存。新TVF F_CONSENSUS_MERGED_ASOF への更新漏れ
- [2026-05-13] content:similar-bug-uncovered | scripts/earnings_model/predict.py:L604-611 | [CR-159] F3 yoy_opがFY期でも4Q単独OP YoY使用。F7 baseline通期化とF3の比較軸不整合が残存。154_cr#3と同根
- [2026-05-13] content:missing-downstream | docs/plans/earnings-model-fix_20260512_220000.md:残タスク | [CR-159] 059知見MDの因子定義更新（F7 baseline定義変更）が残タスクに未記載
- [2026-05-13] content:unverified-assumption | docs/plans/tools-004_code_review_findings_log_20260513_105502.md:L63 | [CR-163] echo>>はEOF追記だが004-1末尾は傾向分析セクション(L715-741)。蓄積エントリ外に書き込まれファイル構造破壊
- [2026-05-13] content:missing-downstream | docs/plans/tools-004_code_review_findings_log_20260513_105502.md:L30-36 | [CR-163] md-reviewer苦情受付(既存行削除+不備記録)のRead/Edit例外がStep2に未記載
- [2026-05-13] plan:no-phased-validation | docs/plans/md-reviewer-quality-fix_20260513_003000.md:106-112 | [CR-158] Step 2.5「4軸速断」の判定品質に保証なし。Step 2出力から4軸を正確に速断できる根拠がない。Step 3本評価を分岐条件に使う設計への変更を推奨
- [2026-05-13] content:regression-risk-missed | docs/plans/md-reviewer-quality-fix_20260513_003000.md:63-68 | [CR-158] MD非原因モードP2/4出力が「行動問題です」で打ち切り、md-reviewerが提供すべき価値（MD群の構造・導線分析）が欠落。形を変えた空振りリスク
- [2026-05-13] format:before-after-missing | docs/plans/md-reviewer-quality-fix_20260513_003000.md:102-112,130-170 | [CR-158] P0-1/P0-2のbefore記述が省略。afterのみ記載で「現行スキルMD参照」と注記

### 2026-05-12

- [2026-05-12] md:missing-source-verification | docs/reviews/155_ir_agent_hang_response_failure.md:60-64 | [MR-157] 同日のCLAUDE.md再編成(357→193行,commit 11f8985)の影響検討が完全欠落。環境要因の仮説なし → [採用・インプリ済み: CLAUDE.md §4.2 トリガー語彙復旧]
- [2026-05-12] reviewer:scope-overreach | docs/reviews/157_mr_agent_hang_incident_report_v2.md | [MR-157苦情] 重大指摘#1(統一テーマ分析)・#3(思考回路再構成)・改善提案全3件が文書品質向上に終始し行動変容に繋がらない。155_irは記録として機能すれば十分という観点を欠いた
- [2026-05-12] reviewer:scope-overreach | docs/reviews/156_mr_agent_hang_incident_report.md | [MR-157苦情] 前回MR-156が100知見MD導線・085追記先・PostToolUse hook等、対象外スコープ（サブエージェントハング）の指摘に偏り、事故核心（ユーザー無視）を外した
- [2026-05-12] md:missing-stop-condition | docs/reviews/155_ir_agent_hang_response_failure.md:68-73 | [MR-156] 再発防止策3/4件が「既存原則で十分」で閉じ、既存原則が機能しなかった構造的理由の分析なし。behavior:unverified-claim同型5回以上再発の意志依存型対策
- [2026-05-12] md:ambiguous-action | docs/reviews/155_ir_agent_hang_response_failure.md:73 | [MR-156] 再発防止策#3「085に追記検討」の「検討」が曖昧。追記するか否かの判断基準・具体的追記内容なし
- [2026-05-12] md:ambiguous-scope | docs/reviews/155_ir_agent_hang_response_failure.md:24-58 | [MR-156] 対応失敗5件がフラット列挙で共通行動パターン（確認前断定/知見MD参照省略/圧縮後喪失）への統合分析なし。因果連鎖も不明示
- [2026-05-12] md:missing-output-contract | docs/reviews/155_ir_agent_hang_response_failure.md:24-58 | [MR-156] AIの思考回路再構成（なぜその行動を正しいと判断したか）が全件欠落。事象と原因の間の因果ギャップが埋まっていない
- [2026-05-12] md:reference-vs-rule | docs/reviews/155_ir_agent_hang_response_failure.md:62 | [MR-156] 100知見MDへのポインタが再発防止策#1のみ。根本原因セクションで言及されるが明示的参照リンクなし
- [2026-05-12] behavior:index-first-violation | CLAUDE.md:178, 023_powershell_menu.md:L7-8 | [MR-153] PSメニューパス探索でCLAUDE.md §10テーブル・023知見MD未参照。hook警告も無視しGrep直行。MR-066/117/123に続く4度目の索引ファースト違反
- [2026-05-12] bug:type-mismatch | scripts/earnings_model/predict.py:L389,L216-219 | [CR-152] BQ CONSENSUSクエリとcons_mapビルダーがNET_PROFITを取得・格納せずF4b(np_consensus_deviation)が全銘柄で常時None
- [2026-05-12] content:similar-bug-uncovered | scripts/earnings_model/review_report.py:L217-229 | [CR-152] split_reasons()が「純利コンセ乖離」プレフィックスを未認識。正の純利コンセ乖離もNEG側に分類
- [2026-05-12] md:stale-context | docs/knowledges/tools/059_earnings_model_eda.md:L132 | [CR-152] 因子改善TODO #3「OPフォールバック」がステータス「実装済」のまま。本改修で廃止されているが未反映
- [2026-05-12] md:ambiguous-scope | docs/plans/20260512_175047_refactor_plan_lifecycle_tracking.md:L148 | [MR-151] P0-3の呼び出し側波及にskills/planning.md「計画の更新」節(L351-356)との整合未言及。改修プラン完了手順の二重定義リスク
- [2026-05-12] md:ambiguous-action | docs/plans/20260512_175047_refactor_plan_lifecycle_tracking.md:L109 | [MR-151] 実装後チェック「関連知見MDを更新したか」の「更新」の具体的内容が曖昧
- [2026-05-12] md:missing-output-contract | docs/plans/_template_refactor.md | [MR-150] 改修プランテンプレートにステータスフィールドと実装記録セクションが欠如。実装完了を記録する構造がない
- [2026-05-12] behavior:narrow-scope-prevention | docs/plans/20260508_174200_compare_monthly_index_excluded.md | [MR-150] 実装完了（3723fb4）後にプランMD未更新。CLAUDE.md §4.3「プランMD更新」義務違反
- [2026-05-12] md:missing-stop-condition | skills/planning.md:L169-274 | [MR-150] 改修プランフォーマット節に実装後の更新義務・完了記録フォーマットの記述なし
- [2026-05-12] content:numeric-inconsistency | scripts/zaraba_earnings.py:L1005,L1850 | [CR-149] F4cは全QでORD_PROFIT比較だがprepareサマリーはFY時OP_PROFIT表示。表示と実処理の乖離
- [2026-05-12] content:silent-exception | scripts/zaraba_earnings.py:L1880-1911 | [CR-149] F4n翌期NP非開示時にサイレントスキップ。F4の「翌期予想非開示」ペナルティとの非対称
- [2026-05-12] bug:sql-injection | scripts/xbrl_lookup.py:L87 | [CR-149] f-string SQL組立（ticker[:4]をLIKEに直接埋め込み）。ローカル専用CLIだが規約違反
- [2026-05-12] content:similar-bug-uncovered | scripts/zaraba_earnings.py:L1744-1752,L1934-1942,L1961-1968 | [CR-149] ODP→OPフォールバックが3因子(F4/F7g/F12)で個別インライン実装。片方の修正漏れリスク
- [2026-05-12] bug:aggregation-key-ignored | scripts/zaraba_earnings.py:L699-701 | [CR-148] FY時fin.iloc[1]が3Qである保証なし。TYPE_OF_CURRENT_PERIOD未検証でprev_cumulative_opに誤値格納の可能性
- [2026-05-12] reviewer:false-negative | docs/reviews/146_cr_claude_md_restructure.md | [CR-146苦情] §5「クラッシュ復旧」ラベルが085の実スコープ（+コンパクト後復元）より狭く導線切れ。ポインタ存在は確認したがラベルのスコープ一致は未検証
- [2026-05-12] format:before-after-missing | CLAUDE.md / 004_coding_conventions.md | [CR-146] 旧CLAUDE.md §コーディング規約の2項目（外部API try/except+tenacity、設定値ハードコーディング禁止）が移動先なく消失。プランで「既にある」としたが004に不在
- [2026-05-12] md:ambiguous-scope | CLAUDE.md:45-50 | [CR-146] 旧§4.1の「因果説明にも適用」の明示文言が圧縮で消失。原則文でカバーするが明示性低下
- [2026-05-12] bug:type-mismatch | scripts/lib_conse_csv_from_view.py:L93 | [CR-145] FY開示済み銘柄で `fy > latest_fy` が当期FYコンセンサスを除外。来期FYが FY_CURRENT に繰り上がる off-by-one
- [2026-05-12] md:ambiguous-scope | docs/plans/20260512_072134_claude_md_restructure.md:28-29 | [MR-145] 「原則のみ残す」の判定基準未定義。実行AIが境界を恣意的に解釈し防止力のあるルールを過剰削除するリスク
- [2026-05-12] md:missing-stop-condition | docs/plans/20260512_072134_claude_md_restructure.md:131-132,336-340 | [MR-145] LINE会話モード068移動で圧縮後復旧トリガー（memory必ずチェック+active:trueなら自発再開）が失われるリスク。ポインタだけでは不十分
- [2026-05-12] md:ambiguous-action | docs/plans/20260512_072134_claude_md_restructure.md:311 | [MR-145] Step2「CLAUDE.md書き直し」が1チェックボックス。357行→200行の全面書き直しが段階分割なし
- [2026-05-12] md:missing-output-contract | docs/plans/20260512_072134_claude_md_restructure.md:87-99 | [MR-145] Step0スキルMD改訂が方向性のみで具体的修正文案なし。実行AIの解釈余地が大きい
- [2026-05-12] md:missing-source-verification | docs/plans/20260512_072134_claude_md_restructure.md:294-307 | [MR-145] MR事故番号除去の事前検証なし。知見MD側に全事故番号が存在するか未確認
- [2026-05-12] md:overwrite-risk | docs/plans/20260512_072134_claude_md_restructure.md:109-115,150-163 | [MR-145] CLAUDE.md追加許可基準がStep0とStep2§1に重複定義。正本が4箇所に分散する設計
- [2026-05-12] content:similar-bug-uncovered | scripts/export_consensus_csv.py:L74 | [CR-145] 同一の `fy > latest_fy` off-by-one バグが export_consensus_csv.py にも存在
- [2026-05-12] content:similar-bug-uncovered | scripts/lib_conse_csv_from_view.py:L24-34 | [CR-145] _fetch_latest_fy_end が TYPE_OF_CURRENT_PERIOD を返さず、FY開示済み判定の情報不足が構造的原因
- [2026-05-12] content:missing-downstream | scripts/lib_conse_csv_from_view.py | [CR-145] zaraba/predictの実績パターン(_derive_current_fy)との統一が未検討。プロジェクト内FY判定ロジックが3種類に分散
- [2026-05-12] behavior:narrow-scope-prevention | 022_consensus_load.md / bq_consensus.md / bq_fin_summary.md | [MR-147] コンセンサスFY判定改修+VIEW新規作成後、関連3MD更新漏れ。MR-041と同一ドメインの4度目の同一パターン再発
- [2026-05-12] md:missing-source-verification | CLAUDE.md §知見ファイル整合義務 | [MR-147] 意志依存型ルール（知見ファイル整合義務）が4回連続で機能せず。構造的強制（hook/チェックリスト）の欠如が根本原因

### 2026-05-09

- [2026-05-09] md:stale-context | docs/knowledges/tools/059_earnings_model_eda.md:187,192,220,312,563 | [MR-144] 削除済みファイル(batch_rerun_predict.py, earnings_model_predict.ipynb)への参照が5箇所残存。commit d732c70でCLI統合後にMD未更新
- [2026-05-09] md:missing-source-verification | docs/knowledges/tools/059_earnings_model_eda.md:685 | [MR-144] 「F4コンセンサスは現在一時除外中」が虚偽。core.py L76-101でF4は完全稼働中
- [2026-05-09] md:stale-context | docs/knowledges/tools/059_earnings_model_eda.md:690 | [MR-144] 「5因子スコアリング」が古い。現行16因子。精度数値も2026-04-04時点のもの
- [2026-05-09] content:numeric-inconsistency | docs/knowledges/tools/059_earnings_model_eda.md:180 | [MR-144] PRED_COLUMNS「34列」は実際35列。fy_achievement追加後の列数更新漏れ
- [2026-05-09] md:stale-context | docs/knowledges/tools/059_earnings_model_eda.md:714-724 | [MR-144] モデル改善候補テーブルの「未着手」項目が因子改善TODOでは「実装済」。2テーブル間のステータス不整合
- [2026-05-09] md:reference-vs-rule | docs/knowledges/tools/059_earnings_model_eda.md:267-515 | [MR-144] 反省会ログ(約250行)の未実装設計案が現行仕様セクションと混在。検討中の構想を現行ルールと区別する明示マーカーなし
- [2026-05-09] behavior:unverified-claim | セッション内行動 | [MR-142] 5/8を木曜・5/9を営業日と推測で断定。python/dateコマンドで即確認可能な曜日を確認せず虚偽発言。MR-035/073/119に続く同型4回目
- [2026-05-09] behavior:narrow-scope-prevention | docs/reviews/142_mr_weekday_false_claim.md | [MR-142] 事故報告MDに再発防止策セクションが完全欠落。事象・原因・影響の記録で閉じており行動変容への接続なし
- [2026-05-09] md:missing-source-verification | CLAUDE.md:333 | [MR-142] 推測禁止ルールの確認手段例示に日付属性（曜日・祝日・営業日）が欠落。AIが確認対象として認知しない構造的隙間
- [2026-05-09] behavior:narrow-scope-prevention | docs/reviews/139_mr_unauthorized_qf_execution.md | [MR-139] 再発防止策が完全に欠落。P-001テーブル追記のみで閉じており、メタルール適用判定の盲点（承認スコープ拡大解釈）が未分析
- [2026-05-09] reviewer:stale-premise | 140_mr 重大指摘#2 | [SO-141苦情再審] CLAUDE.md L343「5件目以降はルール追加禁止」方針確定後に、CLAUDE.md適用判定拡張を推奨。方針確認不足
- [2026-05-09] reviewer:stale-premise | 140_mr 重大指摘#3 | [SO-141苦情再審] 同上。CLAUDE.md §毎タスク適用に新ルール追加を推奨。既存メタルールでカバー済みかつ方針違反
- [2026-05-09] content:missing-downstream | docs/plans/tools-098_yutai_scraper_20260509_165014.md | [CR-138] 会社名フィールドの取得源が未定義。v1コードにname抽出なし、JSONL仕様とExcel列仕様に齟齬
- [2026-05-09] plan:no-phased-validation | docs/plans/tools-098_yutai_scraper_20260509_165014.md | [CR-138] 全銘柄走査の実行時間(4-5h)が見積もりに含まれず、セッションタイムアウト対策も未設計
- [2026-05-09] content:missing-downstream | docs/plans/tools-098_yutai_scraper_20260509_165014.md | [CR-138] kenri_kakutei→月番号パース仕様が未定義。随時優待のシート振り分けも未決定
- [2026-05-09] md:priority-conflict | CLAUDE.md:286,296,208,334,340 | [SO-138] 4件系譜の個別ルールがメタルール(§指示の字義優先)とフラットに並列。親子関係が不明示で表層が異なる変種に発火しない構造的欠陥
- [2026-05-09] md:reference-vs-rule | CLAUDE.md:340 | [SO-138] §指示の字義優先が抽象原則のまま。「手段Xを手段Yに置き換えようとしている」等の具体的適用判定基準なし
- [2026-05-09] behavior:narrow-scope-prevention | docs/reviews/137_mr_web_research_skip_retrial.md | [SO-138] md-reviewer再審が「5件目でhook検討」と先送り。ルール増殖モデルの限界をアーキテクチャ転換で対処すべき相互作用問題
- [2026-05-09] behavior:narrow-scope-prevention | docs/reviews/134_mr_web_research_skip.md | [MR-134再審] 再発防止策・系譜セクションがロールバック後も未復旧。事故記録が不完全なまま放置
- [2026-05-09] md:stale-context | CLAUDE.md:208 | [MR-134再審] §ステップ完了検証義務の系譜参照が「3件目」で止まり、MR-136（4件目）が未反映
- [2026-05-09] md:discoverability | CLAUDE.md:39 | [MR-136] コンテキスト圧縮後の復旧義務がPython関数名のみでCLIフラグ導線なし。068知見MD参照が暗黙で--wait漏れ
- [2026-05-09] md:missing-source-verification | 068_line_ntfy_push.md:82-87 | [MR-136] --timeoutは--wait併用時のみ有効という共依存が暗黙。--timeout指定で待機すると誤解
- [2026-05-09] behavior:narrow-scope-prevention | docs/reviews/136_mr_ntfy_wait_flag_miss.md:64-68 | [MR-136] 再発防止策4点が全て意志依存型。MR-071/131/134/136で4連続同根パターン再発に対し構造的強制ゼロ
- [2026-05-09] behavior:instruction-escalation-ignored | セッション内行動 | [MR-136] ユーザー3回の異常報告を外部帰責で無視。--wait漏れは068参照で即判明。確証バイアスで知見MD参照を省略
- [2026-05-09] behavior:unverified-claim | セッション内行動 | [MR-129] GCS保管済みresults.csvをローカル不在のみで「残っていない」と誤報告。知見MD(066)にGCSパス明記済みだが索引ファースト未実施
- [2026-05-09] behavior:narrow-scope-prevention | docs/reviews/129_mr_zaraba_results_gcs_miss.md:49-53 | [MR-130] 索引ファースト3回目違反の再発防止が「既存ルールのカバー範囲内」で閉じ、フック突破の構造分析なし
- [2026-05-09] md:ambiguous-scope | CLAUDE.md:163 | [MR-130] データ取得フォールバック順序が「取得」に限定。「所在確認・有無判断」への適用が曖昧で事故原因
- [2026-05-09] md:discoverability | docs/knowledges/tools/066_zaraba_tool.md:176-184 | [MR-130] ローカルキャッシュ構造セクションにGCS永続化先への明示的クロスリファレンスなし
- [2026-05-09] md:ambiguous-scope | CLAUDE.md:284 | [MR-131] 手動処理指示（1件ずつ・君が成形せよ等）に対するPythonコード化禁止ルールが不在。MR-071改良バイアスの変種
- [2026-05-09] md:ambiguous-scope | CLAUDE.md:331 | [MR-131] §指示の字義優先の「技術的判断」が手段選択（コード vs 手動）を射程に含むことが明示されず、AIがルール適用外と解釈
- [2026-05-09] behavior:instruction-escalation-ignored | セッション内行動 | [MR-131] 「1件1件丁寧に」を複数回念押しされてもPythonスクリプト化を繰り返す。MR-071系の改良バイアス3件目
- [2026-05-09] behavior:narrow-scope-prevention | docs/reviews/134_mr_web_research_skip.md | [MR-134] 事故報告に再発防止策ゼロ。事象・原因・影響の記録で閉じ、CLAUDE.mdへの構造的対策が欠落
- [2026-05-09] md:ambiguous-scope | CLAUDE.md:201-207 | [MR-134] §作業計画の管理にプラン実行時のステップ完了検証義務が不在。作成段階の品質管理のみで実行段階が空白
- [2026-05-09] behavior:instruction-escalation-ignored | セッション内行動 | [MR-134] プランに「AI知見+Web調査」と明記しながらWeb調査を省略し完了報告。MR-071系4件目（省略変種）
- [2026-05-09] md:missing-source-verification | docs/knowledges/analysis/012_monthly_disclosure_earnings_screening.md:143-150 | [MR-134] 予測力仮説セクションの出自（Web調査 vs AI推論）が未ラベルで区別不能

### 2026-05-08

- [2026-05-08] content:regression-risk-missed | docs/plans/20260508_230500_zaraba_6557_bugfix.md:P0-4 | [CR-128] F2(ガイダンス修正)とF15(通期着地)がFYで同時発火する二重加点リスクの明示的許容/排除が未記載
- [2026-05-08] content:unverified-assumption | docs/plans/20260508_230500_zaraba_6557_bugfix.md:P0-4,L216 | [CR-128] F15分母effective_forecast_opがBQ prior由来の古い値のとき乖離率過大リスクの注記なし
- [2026-05-08] content:similar-bug-uncovered | docs/knowledges/tools/066_zaraba_tool.md:L125-137 | [CR-128] F4c/F7g/F11/F12/F14/F15が因子テーブルに未掲載（CR-126 F-8残存+F15追加）
- [2026-05-08] md:stale-context | docs/plans/20260508_230500_zaraba_6557_bugfix.md:116-165,380-382 | [MR-127] P0-3のBLOCKER未解決修正方針が残存。ユーザー決定（通期=営業/四半期=経常）が未反映でAIが全面OP_PROFIT統一と誤解釈
- [2026-05-08] md:ambiguous-scope | docs/plans/20260508_230500_zaraba_6557_bugfix.md:339 | [MR-127] 整合性マトリクスがQ別利益種別分岐を反映せず「営業vs営業」と記載。方針決定後の実装と矛盾
- [2026-05-08] md:missing-source-verification | docs/plans/20260508_230500_zaraba_6557_bugfix.md:232-268 | [MR-127] P1-3がP0-3方針変更に連動する旨の注記が欠落。独立実装で表示/スコアリング不整合リスク
- [2026-05-08] content:unverified-assumption | docs/plans/ad-hoc_data_catalog_split_20260508_225000.md:49 | [MR-124] catalog.py append_entry()を「実害なし」と断定。インデックス化後のファイル末尾追記がテーブル構文外に漏れる
- [2026-05-08] md:ambiguous-scope | docs/plans/ad-hoc_data_catalog_split_20260508_225000.md:91-93 | [MR-124] CLAUDE.md書き換え範囲がL161付近のみ。L159/L165/L226-227の更新が作業ステップに未含
- [2026-05-08] content:missing-downstream | docs/plans/ad-hoc_data_catalog_split_20260508_225000.md:89-93 | [MR-124] INDEX.md L72/L180のdata_catalog.md参照の更新がPhase 3に未含
- [2026-05-08] md:missing-source-verification | docs/plans/ad-hoc_data_catalog_split_20260508_225000.md:125 | [MR-124] 65ファイルの参照元棚卸しが「grepで確認」の注記のみ。作業ステップにチェックボックス化されていない
- [2026-05-08] md:missing-output-contract | docs/plans/ad-hoc_data_catalog_split_20260508_225000.md:57-78 | [MR-124] 19個の個別ファイルのヘッダテンプレート未定義。書式統一・戻りリンクの保証なし
- [2026-05-08] md:ambiguous-scope | docs/plans/ad-hoc_data_catalog_split_20260508_225000.md:82,97,106,113 | [MR-124] 目標行数が「~100行」「~100行以下」「120行以下」と3表現混在。検証基準不明確
- [2026-05-08] content:missing-downstream | docs/plans/ad-hoc_data_catalog_split_20260508_225000.md:49 | [CR-125] load_catalog()の戻り値変化（全文→INDEX120行）に対する判断が欠落。discovery.py将来実装時の罠
- [2026-05-08] content:numeric-inconsistency | docs/plans/ad-hoc_data_catalog_split_20260508_225000.md:157 | [CR-125] 参照元67ファイルのリンク更新作業量が30-40分見積もりに未含。実際は50-70分
- [2026-05-08] behavior:unverified-claim | CLAUDE.md:161,165 | [MR-123] BQクエリ発行時にdata_catalog.md未参照。プロジェクトID・カラム名を推測で使用し2回エラー（再発: MR-117）
- [2026-05-08] behavior:narrow-scope-prevention | CLAUDE.md:341-344 | [MR-122] 再発防止策策定時のスコープ自己検証義務がCLAUDE.mdに不在。メインエージェントが対症療法的fixを知見MDに永続化する行動パターン
- [2026-05-08] md:ambiguous-scope | CLAUDE.md:328 | [MR-122] 「新規規約の導入」の適用対象が記法・マーク・分類基準・命名規則に限定。運用ルール・再発防止策が射程外で発火しない
- [2026-05-08] md:ambiguous-scope | 023_powershell_menu.md:62-73 | [MR-121] 専用ハンドラ同期義務がザラ場限定。IsPredict等の他ハンドラに適用されずPS1更新漏れが再発する
- [2026-05-08] md:missing-stop-condition | 023_powershell_menu.md:177-189 | [MR-121] メニュー項目追加方法が通常項目のみ。専用ハンドラ（IsXxx）付き項目の追加手順が未文書化
- [2026-05-08] md:missing-source-verification | 023_powershell_menu.md:69 | [MR-121] zara.pyにreviewサブコマンド欠落。同期義務ルール記載時に既存の同期状態を検証していない
- [2026-05-08] md:example-vs-exhaustive | CLAUDE.md:328 | [MR-120] マルチターン入力待機ルールの未完了シグナル例示が2例のみで判定基準が未定義。「続き」を作業GOと誤認
- [2026-05-08] md:missing-source-verification | docs/reviews/120_mr_premature_action_tsuzuki.md | [MR-120] 既存memory feedback_wait_for_go.md（GO明示待ち）との関連が事故報告MDに未記載
- [2026-05-08] md:ambiguous-scope | docs/reviews/118_mr_test_script_placement_violation.md:33-37 | [MR-119] 再発防止を「追加対策不要」で閉じているが、CLAUDE.md L290のトリガー条件盲点（scripts/以外への配置時に発火しない）が本事故の根本原因
- [2026-05-08] md:missing-source-verification | docs/reviews/118_mr_test_script_placement_violation.md:27-29 | [MR-119] 根本原因が「ルールを読まなかった」で止まり、トリガー導線の構造的欠陥（L290の条件範囲）に踏み込んでいない
- [2026-05-08] md:missing-source-verification | docs/knowledges/tools/004_coding_conventions.md:372-430 | [MR-119] tmp_*/lib_*の配置ルールは明文化済みだがtest_*の配置ルールが未明文化。暗黙の前提
- [2026-05-08] md:ambiguous-scope | CLAUDE.md:290 | [MR-119] 新規スクリプト必読ルールのトリガーが「scripts/配下に書く場合」に限定。scripts/以外に書こうとした場合は発火しない
- [2026-05-08] behavior:unverified-claim | docs/reviews/118_mr_false_column_shift_explanation.md | [MR-118] PDF列構造の因果説明を検証なしに捏造（2736列ズレ説）。MR-035/073に続く同型3回目の再発
- [2026-05-08] md:ambiguous-scope | CLAUDE.md:321 | [MR-118] 推測禁止ルールが中間仮説・因果推論に適用されるか曖昧。MR-073推奨の拡張が未実施のまま再発
- [2026-05-08] md:missing-source-verification | docs/reviews/118_mr_false_column_shift_explanation.md:36-38 | [MR-118] 再発防止策がPDF表構造固有の対症療法。過去事故系譜（MR-035/073）への参照なし
- [2026-05-08] md:stale-context | docs/knowledges/tools/066_zaraba_tool.md:280-330 | [MR-116] 解決済み落とし穴5件（約120行）が現行仕様と同列H3で並存。AIが未解決と誤認して不要な回避策を実装するリスク
- [2026-05-08] md:stale-context | docs/knowledges/tools/066_zaraba_tool.md:459-487 | [MR-116] 反省会ログ内の未対応TODO2件と修正済みチェックマーク混在。完了/未完了の取り違えリスク
- [2026-05-08] md:reference-vs-rule | docs/knowledges/tools/066_zaraba_tool.md:368-427 | [MR-116] XBRL context確定仕様10行が経緯・検証ログ60行に埋没。071との二重管理も未解消
- [2026-05-08] md:ambiguous-scope | docs/knowledges/tools/066_zaraba_tool.md:7-12,477-478,488-493 | [MR-116] 未完了TODO/課題が3箇所に分散。冒頭のみ参照するAIが残課題を見落とす
- [2026-05-08] md:stale-context | docs/knowledges/tools/066_zaraba_tool.md:258-278 | [MR-116] 設計方針2セクション（メモリロード/二層構成）が実質一層運用で過剰。yanoshin低レイテンシ値がAIの回帰提案を誘発
- [2026-05-08] md:missing-output-contract | docs/reviews/115_mr_066_zaraba_restructure.md | [MR-116] 整理プランに目標構成（H2見出し一覧・推定行数）が未定義。実施AIの完了判断基準が不明
- [2026-05-08] md:ambiguous-scope | data_catalog.md:26,361 | [MR-117] STOCK_CODE_LISTの一覧説明に「銘柄名」欠落。AIが会社名取得テーブルとして認識不能
- [2026-05-08] md:ambiguous-scope | CLAUDE.md:160 | [MR-117] BQ SQL発行前ルールのトリガーが狭く「データの所在不明時」にdata_catalog.md参照が発火しない
- [2026-05-08] md:missing-source-verification | CLAUDE.md:211-235 | [MR-117] 高頻度参照テーブルに「銘柄属性・会社名取得」→data_catalog.mdの導線が不在
- [2026-05-08] md:stale-context | docs/knowledges/tools/066_zaraba_tool.md:249-257 | [MR-116] J-Quants移行経緯9行が冒頭ステータス「移行完了」と重複。整理方向性に含まれていない
- [2026-05-08] md:stale-context | docs/knowledges/tools/066_zaraba_tool.md:353-366 | [MR-116] XBRL予想値タグ修正履歴14行。確定仕様は071が正本で066に残す必要なし。整理方向性に含まれていない
- [2026-05-08] content:silent-exception | docs/plans/20260508_174200_compare_monthly_index_excluded.md | [CR-114] _load_excluded_tickers()のexcept Exception: return set()がCSVスキーマ変更時にフィルタ無効化を検知不能にする
- [2026-05-08] content:missing-downstream | docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md | [CR-113] predict.py L444のbeta_20dパスに対応する定数が未定義。L908等は定数化指示があるがL444は単純書き換えのみ
- [2026-05-08] content:missing-downstream | docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md | [CR-112] predict.py L908/1078/1079/1251の文字列リテラルGCSパスがGCS_*定数経由でなく、定数変更だけでは追従しない
- [2026-05-08] content:missing-downstream | docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md | [CR-112] beta-calc Cloud Run Job再デプロイが計画に含まれず、日次Jobが旧パスに書き続けるリスク
- [2026-05-08] content:regression-risk-missed | docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md | [CR-112] beta_20dパス移行タイミングで日次Job（18:30 JST）との競合リスクが未記載
- [2026-05-08] content:missing-downstream | docs/plans/20260508_230500_zaraba_6557_bugfix.md:148-154 | [CR-126] P0-3 F4c OP_PROFIT切替で1Q/2Q/3QがIFIS由来(OP_PROFIT=NULL)のため常時不発火になる。データソース特性の確認漏れ
- [2026-05-08] md:missing-source-verification | docs/plans/20260508_230500_zaraba_6557_bugfix.md:157 | [CR-126] cumulative_opとOrdinaryProfitが両方「累計値」である根拠がプランに未記載。期間スコープ変化なしの検証が暗黙
- [2026-05-08] md:stale-context | docs/knowledges/tools/066_zaraba_tool.md:125-137,161-164 | [CR-126] F4c/F7g/F12/F14が実装済みだが因子テーブルに未掲載。「未実装」記載が実態と乖離
- [2026-05-08] plan:catalog-mismatch | docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md | [CR-112] 新規GCSパスzaraba_scoring_results/のdata_catalog.mdエントリ追加が計画に欠落
- [2026-05-08] content:similar-bug-uncovered | docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md | [CR-112] 059知見MD記載のmodels/reports/サブフォルダのリネーム検討・非スコープ明記がない
- [2026-05-08] md:missing-source-verification | docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md:54-68 | [MR-112] Part3ソースコード参照リストに.ipynb_checkpoints+059MDコードブロック内パスの漏れ。「等」で範囲不確定
- [2026-05-08] md:ambiguous-scope | docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md:40-51 | [MR-112] features/リネーム対象だがmodels/reports/が対象外の理由未記載。AIが計画外リネームを実行するリスク
- [2026-05-08] md:ambiguous-action | docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md:24-25 | [MR-112] Q列追加のQ値取得元（prior_data.json/scored_results/XBRLのどれか）が未記載
- [2026-05-08] md:missing-stop-condition | docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md:98-104 | [MR-112] Part2-Part3間の実行順序依存が未定義。zaraba_scoring_results命名規則の不整合も未説明
- [2026-05-08] format:rollback-missing | docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md:98-104 | [MR-112] Part3破壊的操作のロールバック手順が欠落
- [2026-05-08] plan:catalog-mismatch | docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md:63 | [MR-112] Part2新規zaraba_scoring_results/のdata_catalog.md追記が計画ステップに欠落
- [2026-05-08] md:missing-output-contract | docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md:75 | [MR-112] commit粒度（Part別分割か一括か）が未定義
- [2026-05-09] md:missing-stop-condition | skills/md-reviewer.md:319-377 | [MR-133] Step 8に出力内部整合性チェック（指摘統合・指摘-修正文案クロスチェック・技術的正確性）が欠落。苦情3件（MR-110水増し/CR-084unsafe推奨/MR-075重複）の構造的原因
- [2026-05-09] md:context-expansion-risk | skills/md-reviewer.md:52-61 | [MR-133] §3が「深化・汎用化」方向のみで「統合・削減」方向の制御がない。根本対策で解消される派生指摘の独立計上を抑止できない
- [2026-05-09] md:missing-source-verification | skills/md-reviewer.md:319-321, skills/code-reviewer.md:228-230 | [MR-133] 推奨対応に技術的提案を含める場合の正確性検証義務が両スキルMDに未定義。CR-084のBQトランザクション誤推奨の構造的原因
- [2026-05-08] md:ambiguous-scope | CLAUDE.md:207 | [MR-110] 高頻度参照テーブルの「一致」判定基準が未定義。完全一致/部分一致/セマンティックのいずれか不明
- [2026-05-08] reviewer:false-positive | docs/reviews/110_mr_code_reviewer_routing_miss.md #1,#3,#4 | [MR-110 苦情] 根本原因を#2（セマンティックマッチ未許可）で正しく特定しながら、推奨対応を#1・#3・#4のルールベース列挙（タスク列にユーザー語彙バリエーション追記）に分散。#2修正で#1・#3・#4は不要となる対症療法だった **[対処済み 2026-05-09 MR-133: §3統合の次元+8e(i)指摘統合チェック追加]**
- [2026-05-08] content:unverified-assumption | docs/plans/20260508_125300_monthly_autofix_bc_ignore_knowhow.md:77 | [CR-109] _bc_ignore_reasonフィールド名が042スキーマ定義のbc_ignore_reason（アンダースコアなし）と不一致
- [2026-05-08] content:similar-bug-uncovered | docs/plans/20260508_125300_monthly_autofix_bc_ignore_knowhow.md:137,153,165 | [CR-109] E6一致キーが既存E1/E3パターンと広範重複。grep照合で誤マッチ誘発
- [2026-05-08] content:missing-downstream | docs/plans/20260508_125300_monthly_autofix_bc_ignore_knowhow.md | [CR-109] Step 3A L94,L136-141の「パターンDB不要」「やらないこと」リストがE6追加後も未修正。禁止文言との矛盾
- [2026-05-08] content:missing-downstream | docs/plans/20260508_125300_monthly_autofix_bc_ignore_knowhow.md | [CR-109] Step 3A修復サイクルL109でGCS docs=0件時のStep 3Cへの遷移パスが未定義
- [2026-05-08] bug:race-condition | workflows/ai_processing_flow.yaml:86-100 | [CR-107] resolve_date_range の MIN(SUBMISSION_DATE)+DATE_TRUNC(MONTH) が月跨ぎで当日分をスキップ。4月pending残存→5月分275行が処理されず
- [2026-05-08] content:orphan-resource | workflows/tdnet_daily_pipeline.yaml:57-65 | [CR-107] trigger_ai 失敗後の pending 自動回収メカニズム不在。週次(土7:00)まで5-6日滞留
- [2026-05-08] content:similar-bug-uncovered | workflows/ai_processing_flow.yaml:93-96 | [CR-107] recent_only=true の14日ガードが DATE_TRUNC(MONTH) で月単位に丸められ、意図した14日制限が形骸化
- [2026-05-08] md:missing-stop-condition | docs/knowledges/tools/013_tdnet_load.md:§バックフィル運用 | [CR-107] 決算特別スケジュール中のバックフィル投入制約（日次パイプラインとの競合）が未明文化
- [2026-05-08] md:ambiguous-scope | docs/knowledges/tools/013_tdnet_load.md:94 | [MR-108] 「バックフィル安全」がrecent_only日付ガードのみ指すがCloud Run Job競合も含むと誤読される
- [2026-05-08] md:missing-stop-condition | docs/knowledges/tools/013_tdnet_load.md:562-564 | [MR-108] resolve_date_rangeの月単位選択で複数月pending共存時に当日分が後回しになるエッジケース未記載
- [2026-05-08] md:missing-stop-condition | docs/knowledges/tools/013_tdnet_load.md:11-86 | [MR-108] 決算特別スケジュール中のバックフィル実行制約（20:03 JST競合回避）が未記載
- [2026-05-08] md:discoverability | CLAUDE.md:211-235 | [MR-108] 高頻度参照テーブルに「バックフィル投入」エントリ不在。バックフィル投入時に013§バックフィル運用+§共存制約への導線が欠落
- [2026-05-08] md:stale-context | docs/knowledges/tools/013_tdnet_load.md:81 | [MR-108] 運用実績2026-05-07が成功面のみ記載。同日のtrigger_aiタイムアウト+5/7分未処理インシデントが未記録

### 2026-05-07

- [2026-05-07] md:discoverability | CLAUDE.md:211-232 | [MR-106] analysis/カテゴリが高頻度参照テーブルに0件。093(決算じっくり分析)を含む16ファイルが速引き不可
- [2026-05-07] md:ambiguous-scope | CLAUDE.md:207 | [MR-106] ルックアップ手順が「一覧表示」系質問に未対応。部分一致でINDEX.mdフォールバック不発火
- [2026-05-07] md:ambiguous-scope | INDEX.md:195 | [MR-106] 導線チェックリスト項目3「週1回以上参照」が新設ファイルに適用不能。過去実績ゼロで常にスキップ
- [2026-05-07] md:missing-output-contract | skills/monthly-error-autofix.md:99-100 | [MR-105] Step 3A PDF取得・テキスト抽出のコマンド例が欠落。自律実行AIが手段選択で試行錯誤しトークン浪費
- [2026-05-07] md:ambiguous-action | skills/monthly-error-autofix.md:101,156 | [MR-105] Step 3A-4 Editの修正対象パスとStep 5-1の正式パス保存の関係が不明。パスフロー未定義
- [2026-05-07] md:ambiguous-scope | skills/monthly-error-autofix.md:85-87 | [MR-105] E1-E6の個別症状定義がスキルMD内に不在。Step 0で042-1ロード不要と指示しつつE系分類を要求する矛盾
- [2026-05-07] md:missing-output-contract | skills/monthly-error-autofix.md:176 | [MR-105] Step 7 LINE通知に --sender ATP --task 引数の記載なし。CLAUDE.md §ntfy送信ルール違反リスク
- [2026-05-07] content:missing-downstream | docs/plans/tools-042_monthly_adapter_field_source_20260507_222800.md | [CR-104] extract_monthly_data.py L3336-3362のstructure-adapter突合が source:"original" メトリクスで偽警告。影響分析テーブルで「不要」判定
- [2026-05-07] content:missing-downstream | docs/plans/tools-042_monthly_adapter_field_source_20260507_222800.md | [CR-104] build_monthly_extractor.py L214-218のparse_structure_metrics()が source:"original" をGeminiプロンプトに混入。影響分析テーブルで「不要」判定
- [2026-05-07] format:rollback-missing | docs/plans/tools-042_monthly_adapter_field_source_20260507_222800.md | [CR-104] 470ファイル一括マイグレーション前のGCSバックアップ手順が欠落。撤退基準も未定義
- [2026-05-07] content:data-loss-path | CLAUDE.md:79-86 | [MR-103] AI手動反復処理の逐次永続化義務がCLAUDE.mdに不在。151社adapter修正で約30社分のPDF調査がEdit/Write 0件のままクラッシュし全消失
- [2026-05-07] md:missing-stop-condition | docs/plans/20260507_134400_monthly_adapter_reconciliation.md | [MR-103] Codex委譲失敗時のフォールバック手順（AI手動処理サイクル定義）が未記載。急遽引き受けた際に処理サイクル未定義のまま着手
- [2026-05-07] md:discoverability | memory/feedback_no_shortcut_investigation.md:16 | [MR-103] 「調査結果は都度ファイルに保存」がNG調査固有文脈のためadapter修正タスクで発火せず。CLAUDE.md汎用ルールへの昇格が必要
- [2026-05-07] content:data-loss-path | scripts/earnings_model/predict.py:1042-1052 | [CR-102] cmd_todayがcmd_answer内sys.exit(1)で即死。predict成功+GCS保存済みなのにanswer未完了の片方成功状態
- [2026-05-07] bug:type-mismatch | scripts/earnings_model/predict.py:1077-1081 | [CR-102] backfill --fromのみ指定で--toがNone時にif not条件でGCS全期間リビルド発動。片方指定ガードなし
- [2026-05-07] bug:sql-injection | scripts/earnings_model/predict.py:1033-1035 | [CR-102] _get_prev_business_dayのSQL f-string組立。C-1違反（既存パターン横展開記録）
- [2026-05-07] md:ambiguous-scope | codex-to-claude-handoff.md:44-47 | [MR-101] non-tdnet(html_table)のDLコマンドが欠落。2792処理時に手段不明で処理漏れリスク
- [2026-05-07] md:missing-output-contract | codex-to-claude-handoff.md:85 | [MR-101] build_logカラム定義が再作業/差戻し/元151社の3箇所で不一致。confirmed_pdf新設・notes改名の説明なし
- [2026-05-07] md:ambiguous-action | codex-to-claude-handoff.md:66-76 | [MR-101] extraction_method 5条件チェック結果の格納先不明。既存値を変更するのか確認のみかが未記載
- [2026-05-07] behavior:unverified-claim | docs/reviews/100_mr_reviewer_launch_delay_pattern.md:55,58 | [MR-100] 提出MDが「高頻度参照テーブルにレビュー提出の行がない」と主張するがCLAUDE.md:247に存在。推測禁止ルール違反
- [2026-05-07] md:discoverability | CLAUDE.md:247 | [MR-100] L247「レビュー提出・返却・苦情」キーワードがAIの認知ラベル「レビューMD作成」にマッチせず097への導線が機能しない
- [2026-05-07] md:missing-stop-condition | 097_review_submission_guide.md:30-34 | [MR-100] アトミック制約が§1-3に埋没。§1冒頭にサマリーなく、AI が§1-1で作成に着手すると§1-3に到達しない
- [2026-05-07] md:review-quality-low | docs/reviews/018_forgot_to_run_reviewer.md:220-221 | [MR-100] 018レビューがPostToolUseフックを「過剰」と見送り。意志依存型MD修正のみ推奨した結果、同一事故が再発
- [2026-05-07] md:missing-stop-condition | codex-to-claude-handoff.md:445-449 | [MR-099] description「含めるべき情報」が推奨表現で合格基準未定義。key名テンプレート展開で全51社同一文面が生成された
- [2026-05-07] md:example-vs-exhaustive | codex-to-claude-handoff.md:437-443 | [MR-099] gemini description正例1件のみ・NG例ゼロ。keyコピペが禁止事項に該当しない抜け穴
- [2026-05-07] md:ambiguous-action | codex-to-claude-handoff.md:402 | [MR-099] extraction_method統計傾向（gemini 4/regex 1）が個別判断省略の口実に。51社全gemini・extraction_notes全社同一理由
- [2026-05-07] content:regression-risk-missed | scripts/extract_monthly_data.py:3350-3363 | [CR-098] structure.json metrics完全カバー要求が正常アダプタをadapter_no_fieldsでスキップ。adapterはmetricsの部分集合が正常設計なのに完全一致を強制
- [2026-05-07] bug:resource-leak | scripts/extract_monthly_data.py:3330-3335 | [CR-098] fields=[]チェックだけならGCS不要なのに全ticker(~250社)でstructure.json GCS読み込み。50-125秒の不要レイテンシ追加
- [2026-05-07] md:missing-source-verification | C:\tmp\codex_delegation_draft_51companies.md:147 | [MR-097] HTMLソースのextraction_method記述が実コードと矛盾。「regex/geminiとは独立」は誤りで、コードは明示的にextraction_methodを参照
- [2026-05-07] md:missing-stop-condition | C:\tmp\codex_delegation_draft_51companies.md:127-143 | [MR-097] gemini description overfit禁止ルール（042 L277）が欠落。月固定・サンプル値入りdescriptionを量産するリスク
- [2026-05-07] md:missing-output-contract | C:\tmp\codex_delegation_draft_51companies.md:149-172 | [MR-097] 出力フォーマットにbc_keyが欠落。key名がBC名と不一致時に突合全NGの導線
- [2026-05-07] md:ambiguous-action | C:\tmp\codex_delegation_draft_51companies.md:19-73 | [MR-097] 51社中45社のcompany_nameが?のまま。名前の特定方法が未指示
- [2026-05-07] md:ambiguous-action | C:\tmp\codex_delegation_draft_51companies.md:104 | [MR-097] regex条件4「pdfplumberで同一行に出る」の確認方法がCodexに提供されていない
- [2026-05-07] content:architecture-platform-mismatch | scripts/codex_line_wait.py:199-222 | [CR-078] BGプロセスモデルがCodex環境と不適合。返信到着後にCodexを自動起動する手段がなくLINE会話モード契約を構造的に履行不能
- [2026-05-07] content:similar-bug-uncovered | scripts/codex_line_wait.py:83-102 | [CR-078] notify.pyのsend_ntfy(with_id=True)と微妙に異なる独自HTTP送信ロジック。title生成・URL構造でカップリング乖離
- [2026-05-07] bug:race-condition | scripts/codex_line_wait.py:64 | [CR-078] WindowsでPath.replace()が非atomic。status/send-wait同時実行でOSError→状態ファイル書込み失敗
- [2026-05-07] bug:type-mismatch | scripts/codex_line_wait.py:70 | [CR-078] os.environ[args.message_env]がKeyError送出。try外のため状態ファイルにsend_failedが記録されない
- [2026-05-07] bug:race-condition | scripts/codex_line_wait.py:39-45 | [CR-078] _is_pid_aliveがPID再利用で偽陽性。長時間タイムアウト後にデッドロック誘発
- [2026-05-07] md:priority-conflict | docs/knowledges/tools/068_line_ntfy_push.md:194,200 | [MR-096] --task必須ルールがCLAUDE.md L341と068 L200で矛盾（必須 vs 省略可）。068見出しも--senderのみで--task未含
- [2026-05-07] md:discoverability | docs/knowledges/tools/004_coding_conventions.md:294-317 | [MR-095] 新規バッチチェックリストがCLAUDE.md§コーディング規約4項目(print禁止/structlog/GCP認証/docstring)を含まず。004だけ見て規約準拠完了と誤判断
- [2026-05-07] md:missing-stop-condition | CLAUDE.md:231 | [MR-095] 高頻度参照テーブル「スクリプト新規作成→004」の導線はあるがWrite前にReadを強制する行動命令が無い。意志依存型で構造的強制なし
- [2026-05-07] behavior:instruction-escalation-ignored | scripts/save_backlog_record.py | [MR-095] 新規スクリプト初版で10件規約違反。CLAUDE.md高頻度参照テーブル+004チェックリストの両方を参照せずWrite実行
- [2026-05-07] plan:scope-unclear | docs/plans/refactor_extract_fields_validation_20260507_115942.md:P1-1 | [CR-094] 042-1パターンDB追記のタイミング（同一commit or 別タスク）が不明確。monthly-error-autofixが未知error_typeを受ける空白期間リスク
- [2026-05-07] content:missing-downstream | docs/plans/refactor_extract_fields_validation_20260507_115942.md:L4189 | [CR-094] CLIサマリーログにadapter_no_fieldsカウント表示がなく60社の可視化が error log JSON 直接参照のみ
- [2026-05-07] content:unverified-assumption | docs/plans/tools-089-2_adapter_completed_metrics_cleanup_20260507_073346.md:37-41 | [CR-091] structure.jsonのversions[].metricsをトップレベルmetricsと混同した修正ロジック記述。スキーマ不一致で全社スキップ or 破損リスク
- [2026-05-07] content:missing-downstream | docs/plans/tools-089-2_adapter_completed_metrics_cleanup_20260507_073346.md:55-61 | [CR-091] GCS削除順序矛盾：extract_adapter削除後にstructure.json旧版残存の中間状態。49社の二重アップロード曖昧さ
- [2026-05-07] content:similar-bug-uncovered | docs/plans/tools-089-2_adapter_completed_metrics_cleanup_20260507_073346.md | [CR-091] data/quarterly_adapters/（2628件）がプランの修正対象から漏れ。旧形式アダプタのcompletedメトリクス残存リスク
- [2026-05-07] content:silent-exception | scripts/save_backlog_record.py:159-166 | [CR-093] errors>0でもsys.exit(1)なし。常にexit 0で終了。004 A-1直接違反
- [2026-05-07] bug:type-mismatch | scripts/save_backlog_record.py:45-49 | [CR-093] find_latest_period()がUnicodeコードポイント文字列比較。日本語期間("2026年3月期")とASCII("2025-4Q")が混在すると最新期を誤判定
- [2026-05-07] bug:error-swallowing | scripts/save_backlog_record.py:15,20-23 | [CR-093] STRUCTURE_DIR・GCSキーパスが相対パスハードコード。E-1違反。Cloud Run実行時にFileNotFoundError
- [2026-05-07] content:data-loss-path | scripts/save_backlog_record.py:77 | [CR-093] adapter_versionが常に空文字。再抽出時のバージョン追跡不能。知見MDのレコード仕様（"2026-03"形式）と乖離
- [2026-05-07] content:regression-risk-missed | scripts/save_backlog_record.py:140-155 | [CR-093] dry-run時にGCSの既存records.jsonを読まないため、upsert衝突検出が動作せずdry-runと本番の挙動が非対称

### 2026-05-06

- [2026-05-06] md:ambiguous-action | docs/knowledges/tools/042_monthly_disclosure_master.md:42-50 | [MR-087] 本運用パイプラインテーブルに実行コマンド列なし。AIがextract_monthly_data.pyをデフォルト30社で実行し206社未処理
- [2026-05-06] md:missing-output-contract | docs/knowledges/tools/042_monthly_disclosure_master.md:157-163 | [MR-087] 実行コマンド例セクションにextract_monthly_data.py --allが未掲載。全社実行フラグへの導線ゼロ
- [2026-05-06] md:stale-context | docs/knowledges/tools/042_monthly_disclosure_master.md:48 | [MR-087] 本運用開始済みなのに実行頻度「検討中」表記が残存。テスト段階と誤認→サンプル実行を正当化
- [2026-05-06] bug:error-swallowing | scripts/validate_monthly_first_run.py:63 | [CR-086] bare except Exceptionで全例外をNone化。GCS認証期限切れ・ネットワーク障害が「ファイルなし」と区別不能
- [2026-05-06] bug:type-mismatch | scripts/validate_monthly_first_run.py:230-231 | [CR-086] float()変換にtry/exceptなし。JSON由来の非数値フィールドでValueError→プロセス中断
- [2026-05-06] content:silent-exception | scripts/validate_monthly_first_run.py:249-289 | [CR-086] main()が常にexit 0。アラート164件検出でも上流から成否判定不可。004 A-1違反
- [2026-05-06] content:data-loss-path | scripts/tmp_reextract_failed_docs.py:566-593 | [CR-085] トランザクション内DELETE後に同テーブルからINNER JOINするためREAD COMMITTEDでJOIN結果0行→INSERT 0件でデータ全件ロスト
- [2026-05-06] content:missing-downstream | scripts/tmp_reextract_failed_docs.py:572-583 | [CR-085] INSERT行にFILER_NAME/DISCLOSURE_TIME/PAGE_COUNT等が欠落。元データのメタ消失
- [2026-05-06] bug:resource-leak | scripts/tmp_reextract_failed_docs.py:338-357 | [CR-085] _poll_batch_jobにdeadlineなし。Batch APIハング時に永久ループ。004 D-1違反
- [2026-05-06] bug:sql-injection | scripts/tmp_reextract_failed_docs.py:307 | [CR-084] doc_idをf-string直接埋め込みでDELETE SQL構築。004 C-1違反
- [2026-05-06] reviewer:unsafe-recommendation | docs/reviews/084_cr_reextract_failed_docs.md #2 | [CR-084] 推奨対応でBQトランザクションDELETE→INSERTを有効選択肢として提示。READ COMMITTEDで先行DELETEが後続JOINから可視→INSERT 0行→データロスト。MERGE方式のみ推奨すべきだった **[対処済み 2026-05-09 MR-133: 8e(iii)技術的正確性チェック+確度表示義務追加]**
- [2026-05-06] content:missing-downstream | scripts/tmp_reextract_failed_docs.py:328-344 | [CR-084] INSERT行にCHUNK_TEXT/SUB_CATEGORIES/EMBEDDING欠落。AI判定済みDOCの情報消失
- [2026-05-06] bug:type-mismatch | scripts/tmp_reextract_failed_docs.py:233,236 | [CR-084] new_text_lengthが成功時len(text)=文字数、失敗時_content_length(text)=トークン数で尺度不整合
- [2026-05-06] content:data-loss-path | scripts/tmp_reextract_failed_docs.py:343 | [CR-084] AI_STATUS='pending'固定でMAIN_CATEGORYは旧値保持。テキスト変更後の中間状態で下流クエリ漏れ
- [2026-05-06] content:similar-bug-uncovered | docs/plans/tools-013_tdnet_load_20260506_112815.md P0-1 | [CR-083] pdfminer結果にも[PAGE N]マーカーが含まれるがL626/L1638の閾値判定が修正対象から漏れ
- [2026-05-06] content:missing-downstream | docs/plans/tools-013_tdnet_load_20260506_112815.md P0-1 | [CR-083] 呼び出し側波及リストにL626/L1638が未記載（pdfminerフォールバック受入判定）
- [2026-05-06] format:caller-ref-vague | docs/plans/tools-013_tdnet_load_20260506_112815.md P0-1 | [CR-083] 修正対象「4箇所」だが実際は6箇所（pdfminer判定2箇所漏れ）

### 2026-05-05

- [2026-05-05] content:data-loss-path | docs/plans/20260505_210400_042_md_slim_down.md:54 | [CR-082] 英語PDF対応5ステップ修復手順が042-1のE3-4移行で情報量ミスマッチ。autofix Layer1即答時にgemini_custom_prompt必須項目欠落
- [2026-05-05] content:regression-risk-missed | docs/plans/20260505_210400_042_md_slim_down.md:70-71 | [CR-082] ThreadPoolExecutor罠・Geminiプロンプト改修はコード設計根拠。1行ルール化でコード改修時に同バグ再発リスク
- [2026-05-05] content:missing-downstream | docs/plans/20260505_210400_042_md_slim_down.md:40 | [CR-082] デバッグ知見5点の移行先未定義。042-1のパターンDB構造に横断的ランタイム知見が入らない
- [2026-05-05] md:missing-stop-condition | docs/plans/20260505_193700_monthly_error_autofix_skill.md:248 | [MR-081] Layer1→2遷移の「一致」判定基準が未定義。部分一致で既知修正を誤適用するリスク
- [2026-05-05] md:ambiguous-scope | docs/plans/20260505_193700_monthly_error_autofix_skill.md:288-298 | [MR-081] Step2分類コード(D1-E6)とLayer2調査戦略テーブルのキーが1:1対応せずAIが戦略選択に迷う
- [2026-05-05] md:missing-output-contract | docs/plans/20260505_193700_monthly_error_autofix_skill.md:222-228 | [MR-081] パターンDB蓄積の一致キー・書式テンプレートが未定義。将来のLayer1検索品質が担保されない
- [2026-05-05] md:tool-boundary-risk | docs/plans/20260505_193700_monthly_error_autofix_skill.md:129,141 | [MR-081] Gemini API使用がCLAUDE.md制約と衝突。スキルMDでの明示的許可が欠落
- [2026-05-05] md:priority-conflict | docs/plans/20260505_193700_monthly_error_autofix_skill.md:23,299 | [MR-081] 「30分以内完了」目標と「1社ずつ丁寧に」原則の優先順位未定義
- [2026-05-05] plan:no-phased-validation | docs/plans/tools-059_earnings_predict_unify_20260505_191300.md | [CR-079] backfill時CONSENSUS取得方式が設計内で矛盾（TVF日別呼出 vs 1-pass共通データ）。実装判断が分岐
- [2026-05-05] content:unverified-assumption | docs/plans/tools-059_earnings_predict_unify_20260505_191300.md:L96,L122 | [CR-079] _derive_current_fy()移植と記載するが入力(prev_disc_type/prev_disc_fy_end)の取得パイプライン設計が欠落。実行時F4全銘柄skip
- [2026-05-05] content:missing-downstream | docs/plans/tools-059_earnings_predict_unify_20260505_191300.md:L100-114 | [CR-079] F4b EPS乖離追加のcore.pyインターフェース変更(入力キー追加/PRED_COLUMNS追加)が作業ステップに未記載
- [2026-05-05] bug:aggregation-key-ignored | scripts/zaraba_earnings.py:920 | [CR-078] _consensus_to_prior_fieldsが1Q/2Q/3Q行をFY無関係にby_qに格納。複数FY共存時にイテレーション順依存で不定値（v2 iloc[0]バグと同構造）
- [2026-05-05] content:similar-bug-uncovered | scripts/zaraba_earnings.py:854-872 | [CR-078] _derive_current_fyが決算期変更企業（年数十社）でy+1同月を返し実際のFYと不一致。コンセ乖離スコアがsilent skip
- [2026-05-05] content:similar-bug-uncovered | scripts/update_conse_quick.py | [CR-077] export_consensus_csv()未呼び出し。IFIS/RAKUは完了後にmerged CSV出力するがQUICKのみ欠落。下流ツールへの伝搬断絶
- [2026-05-05] content:data-loss-path | scripts/update_conse_quick.py:241-251 | [CR-077] _to_int/_to_floatがValueErrorでクラッシュ→process_tickerのexceptで握り潰し。想定外HTML値で銘柄データが無言欠落
- [2026-05-05] bug:error-swallowing | scripts/update_conse_quick.py:274-282 | [CR-077] insert_bq()失敗時にlog.warningのみで続行。失敗カウント未追跡・終了コード未反映。BQ欠落が検知不能
- [2026-05-05] content:similar-bug-uncovered | scripts/lib_conse_csv_from_view.py:33 | [CR-077] V_CONSENSUS_MERGED VIEWが旧PROFIT列参照。新スキーマ(REVENUE/OP_PROFIT等)適用後にIFIS/RAKUのCSV出力が全壊
- [2026-05-05] behavior:unverified-claim | セッション内行動 | [MR-076] C案「BQスキーマは変えない」を自ら提案→了承後にALTER TABLE ADD COLUMNを実装。自己発言の制約を確認せず矛盾実装を3ターン推進
- [2026-05-05] behavior:instruction-escalation-ignored | セッション内行動 | [MR-076] ユーザーから2回「C案はスキーマ変更不要では？」と指摘されても矛盾に気付かず、3回目で初めて理解。了承済み方針=指示の認識不足
- [2026-05-05] md:missing-source-verification | docs/knowledges/tools/097_review_submission_guide.md:18 | [MR-041] 採番ルール「最大番号+1」が3スキル正本の「空いている最若番を001から連番」と不一致
- [2026-05-05] md:ambiguous-scope | docs/knowledges/tools/097_review_submission_guide.md:28 | [MR-041] 「レビューパターン（code-reviewerのみ）」がmd-reviewerのパターン指定を不要と誤読させる
- [2026-05-05] md:discoverability | docs/knowledges/tools/097_review_submission_guide.md | [MR-041] INDEX.md未登録。到達可能性が低く統合効果が得られない
- [2026-05-05] md:context-expansion-risk | docs/plans/tools-022_consensus_load_20260505_125600.md:70,77 | [MR-075] 同一P0ラベルの2つのH3見出しがAIに「一括実行単位」と誤認され、ユーザーの「ここまで実行」範囲限定を無視してスクリプト改修に着手
- [2026-05-05] reviewer:false-positive | docs/reviews/075_mr_scope_overreach_consensus_restructure.md | [MR-075 苦情] 重大指摘#1（CLAUDE.md独立ルール追加）が修正文案#3（L332改訂で字義優先を常時適用化）と実質重複。独立した重大指摘として成立しない冗長提案 **[対処済み 2026-05-09 MR-133: 8e(ii)指摘-修正文案クロスチェック追加]**
- [2026-05-05] behavior:unverified-claim | セッション内行動 | [MR-073] 「前回成功時はEdgeが既に動いていた可能性が高い」等4件の根拠なし推測。元ネタRead・HTMLダンプ解析で確認可能な事実を確認せず断定
- [2026-05-05] behavior:instruction-escalation-ignored | セッション内行動 | [MR-073] ユーザーが「毎回ログインしている」と推測を否定した後も同系統の推測（「キャッシュ済みセッション」）を再提示。否定フィードバックへの非応答
- [2026-05-05] md:ambiguous-scope | CLAUDE.md:324 | [MR-073] 推測禁止ルール「推測で発言するな」がデバッグ時の中間仮説を適用スコープに含むか不明確。AIがデバッグ仮説を「発言」から除外解釈する余地
- [2026-05-05] bug:resource-leak | scripts/extract_monthly_data.py:571-584 | [CR-072] _poll_monthly_batch に deadline がなく while True 無限ループ（D-1違反）。バッチジョブ stuck で永久ハング
- [2026-05-05] content:data-loss-path | scripts/extract_monthly_data.py:3318-3348 | [CR-072] overwrite_past_months + batch_mode で regex overwrite が Gemini バッチ結果と重複蓄積。submission_date 偶然依存でどちらが残るか不定
- [2026-05-05] bug:error-swallowing | scripts/extract_monthly_data.py:3832-3872 | [CR-072] バッチジョブ全件失敗時に deferred 全社が skip 扱い。errors 未加算で exit 0（A-1/A-7違反）
- [2026-05-05] behavior:verbatim-copy-violation | セッション内行動 | [MR-071] 「OrderForMABatch.pyのログインをそのまま移植せよ」を4回連続無視。Selenium→Playwright変更、bot検知対策省略、fill()差替え等6箇所の独自改変で松井証券口座ロック
- [2026-05-05] behavior:instruction-escalation-ignored | セッション内行動 | [MR-071] 「そのまま」→「独自ゼロ」→「コピペせよ」と3段階エスカレーションしても行動不変。改良バイアスが指示強度を上回った
- [2026-05-05] behavior:unauthorized-tech-change | セッション内行動 | [MR-071] 認証系コード(松井証券ログイン)でSelenium→Playwrightを無断変更。bot検知対策欠落・fill()によるイベント差異が認証失敗の直接原因
- [2026-05-05] format:before-after-missing | docs/plans/tools-013_gemma_preemption_resilience_20260504_212000.md P0-1 | [CR-070] gemma_tpu_runner.sh のRESUME_RUN_ID passthrough修正がbefore/afterコード片なし。workflow/workerは記載あるのにshellのみ欠落
- [2026-05-05] content:data-loss-path | docs/plans/tools-013_gemma_preemption_resilience_20260504_212000.md P0-1 | [CR-070] 誤ったresume_run_idで別date_rangeのcheckpointを読むとdoc_idが誤スキップされ推論欠損。整合性チェック不在
- [2026-05-05] content:missing-downstream | docs/plans/tools-013_gemma_preemption_resilience_20260504_212000.md P0-1 | [CR-070] extract_resume_run_idステップの正確な挿入位置（extract_ticker_rangeの後か）が未特定。既存3つのextractステップ構造を未記載

### 2026-05-04

- [2026-05-04] md:missing-stop-condition | CLAUDE.md:44 | [MR-069] §監視する/見張るの定義がプロセス起動のみ。起動後の動作検証義務が不在で「起動=監視完了」と誤読される
- [2026-05-04] md:ambiguous-action | CLAUDE.md:46 + 084:25 | [MR-069] BGスクリプト丸投げ監視の限界が未記載。ScheduleWakeup併用が「最終防衛線」=任意扱いで必須化されていない
- [2026-05-04] md:missing-stop-condition | CLAUDE.md全体 | [MR-069] コンテキスト圧縮後のBGプロセス棚卸しルール不在。001レビュー対策(PIDロック)はmonitor_backfill.py専用でad-hocスクリプトに適用外
- [2026-05-04] md:missing-source-verification | 068_mr_backfill_monitor_false_claim.md:17 | [MR-069] backfill_monitor.sh(ad-hoc)とmonitor_backfill.py(PIDロック付き正規ツール)の区別が不明確。再発防止策の焦点がずれるリスク
- [2026-05-04] md:tool-boundary-risk | 013-2_monitor_backfill.md全体 | [MR-069] ad-hoc監視スクリプト作成禁止ルール不在。PIDロック等の安全機構がバイパスされる

### 2026-05-03

- [2026-05-03] md:stale-context | 087_backfill_execution_metrics.md:155,186 | [MR-064] セクション配置が時系列破壊（2020-H1がL155、2018-H2がL186）。事後復元の追記順が年代順を崩した
- [2026-05-03] content:numeric-inconsistency | 087_backfill_execution_metrics.md:159,171 | [MR-064] load docs数(29,814)とBQ DISTINCT FILE_NAME(29,813)に1件差。注記なし
- [2026-05-03] md:stale-context | 087_backfill_execution_metrics.md:266-273 | [MR-064] 見積もりサマリ集計テーブルが「2019-2022推定~82h」のまま。2019全完了+2020-H1完了を未反映
- [2026-05-03] md:review-quality-low | CLAUDE.md:258-263 + memory feedback 3件 | [MR-064] 同根事故4連続再発(041/054/060/064)。毎回意志依存型ルール追加で対策し全て失敗。構造的強制への escalation なし
- [2026-05-03] md:ambiguous-scope | skills/md-reviewer.md:66-68 | [MR-065] §3「対処療法」定義が手段次元のみ。スコープ次元（個別fix vs 汎用ルール化）が欠落し、個別技術fixで§3クリアと判断される
- [2026-05-03] md:missing-stop-condition | skills/md-reviewer.md:337-341 | [MR-065] Step 8b「CLAUDE.md不十分→改訂推奨」に発火条件（チェック質問）がなく、常に「違反なし」で通過
- [2026-05-03] md:ambiguous-scope | skills/md-reviewer.md:136-143 | [MR-065] パターン2/4必須評価項目に「対策スコープ判定」がなく、個別fixで再発防止を完了させる構造
- [2026-05-03] md:review-quality-low | skills/md-reviewer.md全体 | [MR-065] Grep「抽象|汎用|一般化」0件。問題の抽象度を上げるステップが不在。004-1のreview-quality-low 3件蓄積の構造的原因
- [2026-05-03] md:priority-conflict | skills/md-reviewer.md:514-517 vs 337-341 | [MR-065] §やらないこと「ルール追加は役目でない」がStep 8b「CLAUDE.md改訂推奨」を暗黙的に抑制するリスク
- [2026-05-03] md:discoverability | CLAUDE.md §高頻度参照テーブル / 042_monthly_disclosure_master.md | [MR-066] 「アダプタ保管パス」質問で042/089 MDを最初にReadせず5回以上ユーザー誘導。索引ファースト原則違反+関連MD横断調査の自発性欠如
- [2026-05-03] review:quality | docs/reviews/066_mr_adapter_path_investigation_failure.md | [MR-066] 再発防止策が既存ルール（feedback_claudemd_index_first.md）の反復に留まり差し戻し。意志依存型ルール違反に対する構造的強制策の不在が根本問題
- [2026-05-03] content:similar-bug-uncovered | earnings_model_predict.ipynb Cell 5 | [CR-067] Q_MAP/CUM_PREV_Q/PREV_Q_MAP がCell 3のcore import後にCell 5でローカル再定義。core版をシャドウイングし将来の定数変更時にサイレント乖離
- [2026-05-03] bug:type-mismatch | batch_rerun_predict.py:581-587, notebook Cell 5 | [CR-067] F5低ベース判定時にnext_year_op_changeは5年中央値に差替わるがnext_year_eps_changeは未差替え。EPS経由で低ベースフィルタが迂回される
- [2026-05-03] content:regression-risk-missed | earnings_model_core.py:103 | [CR-067] F5のmax(op,eps)で両方負の場合、減点方向でリスクが緩和される設計。加点/減点で集約関数の使い分け未検討

### 2026-05-02

- [2026-05-02] content:unverified-assumption | docs/plans/strategies-004_faber_timing_model_20260502_232018.md:30-36 | [CR-063] Excel個別シート(SP500/CRB/GBOND)前提だがBB_債券履歴_new.xlsxはLISTシートに列インデックスベースで統合。convert_bond_history.pyと不一致
- [2026-05-02] plan:scope-unclear | docs/plans/strategies-004_faber_timing_model_20260502_232018.md:38 | [CR-063] GBOND判定方式(利回り反転 vs TLT代替)が未確定のままPhase1完了条件に未反映。5資産中1資産の根幹ロジック不定
- [2026-05-02] plan:no-phased-validation | docs/plans/strategies-004_faber_timing_model_20260502_232018.md:116-118 | [CR-063] Phase1/2完了条件が「出力される」「通知が届く」と曖昧。具体的検証ケース(既知日付の手計算一致等)なし
- [2026-05-02] plan:catalog-mismatch | docs/plans/strategies-004_faber_timing_model_20260502_232018.md | [CR-063] EFA/VNQ(yfinance)のdata_catalog.md登録計画が欠落。Phase3 BQテーブルの追記計画もなし
- [2026-05-02] content:similar-bug-uncovered | docs/plans/strategies-004_faber_timing_model_20260502_232018.md:91-92 | [CR-063] yf.download()のデフォルト期間(1mo)では200日SMA算出に必要な10ヶ月データが不足。period指定の欠落
- [2026-05-02] md:discoverability | CLAUDE.md / docs/reviews/027_structure_optimization.md RD-1 | [SO-062/自己事故] 557e15fでCLAUDE.md 3フェーズ圧縮時に`### 外部リファレンス管理（docs/references/）`セクションを丸ごと削除。INDEX.mdに移動したが高頻度参照テーブルに代替導線を残さず、5/2に「リファレンス取り込み」指示でdocs/references/の存在を認知できない記憶喪失が発生。到達可能性「高(自動ロード)→中(能動的参照)」降格のリスク評価が圧縮推奨プロセスに不在
- [2026-05-02] md:missing-output-contract | docs/reviews/060_mr_weekly_scheduler_md_update_omission.md:37-45 | [MR-061/自己不備] 再発防止策4ステップの永続化先（記載先ファイル・セクション）が未定義。042教訓違反。MR-055で同一パターン指摘済みだが再現
- [2026-05-02] md:missing-source-verification | docs/reviews/060_mr_weekly_scheduler_md_update_omission.md:50 | [MR-061/自己不備] 041→054→060の3連続再発の系統分析が欠落。「MR-054: 同根」1行のみで過去対策の失敗原因が未分析
- [2026-05-02] md:review-quality-low | docs/reviews/060_mr_weekly_scheduler_md_update_omission.md | [md-reviewer自己不備] 同一根本原因(GCP/コード変更後のMD更新漏れ)が041→054→060で3度再発。毎回「手動チェックリスト追加」を対策提示し続け、対策の有効性を検証せず同じ種類の対症療法を繰り返した。レビュープロセスとして対策の実効性フィードバックループが欠如
- [2026-05-02] md:review-quality-low | docs/reviews/058, 059 | [自己課題] v1(CR-058)でPowerShell固定という対症療法を「適切」と評価し根本原因(Bashパス表記)を見抜けず。v2(CR-059)でもBGデフォルト化の方向性を先行提案できず。3サイクル要しユーザーから「全員的外れ」指摘。課題: (a)対症療法vs根本原因の判断力 (b)FG/BGアーキテクチャ選択の妥当性評価
- [2026-05-02] md:review-recommendation-unsafe | 20260502_152924_line_mode_recurrence_prevention.md v1全体 | [CR-059/自己課題] v1レビュー(CR-058)で根本原因(Bashパス表記)ではなく対症療法(PowerShell切替)の妥当性を検証。ユーザーから「全員的外れ」と指摘。根本原因vs対症療法の判断が甘かった
- [2026-05-02] format:antipattern-map-missing | 20260502_152924_line_mode_recurrence_prevention.md v2 | [CR-059] アンチパターン対応表(plan ID→004/T/G)が末尾にない(v1から未改善)
- [2026-05-02] content:regression-risk-missed | 20260502_152924_line_mode_recurrence_prevention.md v2 P1-1 | [CR-059] feedbackメモリ更新後の内容がBash/PowerShell2分岐でシェル選択判断基準なし
- [2026-05-02] content:similar-bug-uncovered | 20260502_152924_line_mode_recurrence_prevention.md P0-1 | [CR-058] §①/§②のCLI例(L46-50,L80-86)がBash形式のまま残存。LINE会話モード中に参照してBash実行→C:\パス解決失敗の再発リスク
- [2026-05-02] content:regression-risk-missed | 20260502_152924_line_mode_recurrence_prevention.md P1-1 | [CR-058] 新規feedback作成が既存feedback_ntfy_foreground_only.md L10「Bashで」と矛盾。feedback更新の計画スコープ漏れ
- [2026-05-02] format:antipattern-map-missing | 20260502_152924_line_mode_recurrence_prevention.md | [CR-058] アンチパターン対応表(plan ID→004/T/G)が末尾にない
- [2026-05-02] content:unverified-assumption | 20260501_202600_structure_json_quality_assurance.md:152 | [CR-056] E-4チェックがstructure.jsonの`page_keywords`参照するが同フィールドはextract_adapter.jsonにのみ存在。実装不能
- [2026-05-02] content:unverified-assumption | 20260501_202600_structure_json_quality_assurance.md:165 | [CR-056] A-3の`tdnet_documents.category`は文書カテゴリ（受注高/受注残高等）であり業種コードではない。比較母集団が不正確
- [2026-05-02] content:regression-risk-missed | 20260501_202600_structure_json_quality_assurance.md:150 | [CR-056] E-2キーワードリストが受注関連メトリクス名の全バリエーションをカバーしない。correct企業の偽陽性リスク
- [2026-05-02] md:ambiguous-action | 20260501_202600_structure_json_quality_assurance.md:195 | [CR-056] Step 6「残りに展開」が1社ずつ修正継続かパターン一括適用か曖昧。088計画「パターン化禁止」と解釈次第で抵触

### 2026-05-01

- [2026-05-01] md:missing-output-contract | docs/reviews/054_mr_backfill_md_update_omission.md:18-24 | [MR-055] 再発防止4項目チェックリストの永続化先（記載先ファイル・セクション）が不在。042教訓違反
- [2026-05-01] md:missing-source-verification | docs/reviews/054_mr_backfill_md_update_omission.md:12-15 | [MR-055] 先行事故041（コンセMD更新漏れ）との関連分析が欠落。同種再発の系統分析なし
- [2026-05-01] md:missing-source-verification | docs/reviews/054_mr_backfill_md_update_omission.md:12-15 | [MR-055] プランMD後片付けチェックリスト（L419-425）に更新義務記載済みだった事実への言及欠落
- [2026-05-01] md:missing-output-contract | 20260501_202600_structure_json_quality_assurance.md:136-139 | [MR-053] Phase B Step 6のGCS上書きにdry-run/確認ステップ不在。CLAUDE.md破壊的操作ルール非準拠
- [2026-05-01] md:missing-source-verification | 20260501_202600_structure_json_quality_assurance.md:75-78 | [MR-053] サンプリング対象リストのソート順未指定。BQ結果順序非決定的でrandom.seed再現性が無効
- [2026-05-01] md:ambiguous-action | 20260501_202600_structure_json_quality_assurance.md:82-98,173 | [MR-053] Step 2手順にpdfplumber併用ステップ不在だがリスク対策で「pdfplumber併用」記載。手順と対策の不整合
- [2026-05-01] md:missing-output-contract | 20260501_202600_structure_json_quality_assurance.md:94 | [MR-053] results.csvのスキーマ（カラム名・型）が未定義。AI独自設計の余地
- [2026-05-01] md:ambiguous-action | 20260501_202600_structure_json_quality_assurance.md:133 | [MR-053] 「概ね一致」の定量基準不在。チェッカー精度検証の判定が曖昧
- [2026-05-01] md:missing-stop-condition | 20260501_202600_structure_json_quality_assurance.md:147 | [MR-053] Phase B-Cループ2回超過時の「検討」に判断基準なし。AIが自律的に3回目を開始する余地
- [2026-05-01] md:ambiguous-action | CLAUDE.md:71 | [MR-052] 復旧義務「モード継続」が状態宣言のみで行動指示（ファーストトリガーsend_ntfy_and_wait自発送信）を含まない。/clear後に双方向ループ再開不能でデッドロック
- [2026-05-01] md:missing-stop-condition | CLAUDE.md:20付近 | [MR-052] §起動時メニューにLINE会話モードactive時の例外なし。メニュー表示とファーストトリガー送信の優先順位が不明でスマホユーザーに到達しない
- [2026-05-01] md:ambiguous-scope | 20260501_185114_codex_mirror_sync_protection_plan.md:38-39 | [MR-050] DEFAULT_EXCLUDESへのdocs/reviews/*codex*追加がsource側にも適用されClaude Code側の*codex*ファイル収集を阻害
- [2026-05-01] md:missing-source-verification | 20260501_185114_codex_mirror_sync_protection_plan.md:108 | [MR-050] 001_sync_claude_md_mirror_gapリネーム先の採番衝突リスク未検討(001_monitor_backfill既存)
- [2026-05-01] md:missing-output-contract | 20260501_185114_codex_mirror_sync_protection_plan.md:43 | [MR-050] summarize_rows()のordered_statusesへのCODEX_PROTECTED追加が計画本文に記載漏れ
- [2026-05-01] md:ambiguous-action | 20260501_185114_codex_mirror_sync_protection_plan.md:118-131 | [MR-050] テスト計画にsource側*codex*ファイル収集確認と--force-delete-untracked正常系テストが欠落
- [2026-05-01] md:ambiguous-action | 20260501_185114_codex_mirror_sync_protection_plan.md:58-60 | [MR-050] untracked判定のgit依存実装方法・フォールバック方針が未定義。現行スクリプトはgit非依存
- [2026-05-01] md:ambiguous-scope | scripts/sync_claude_md.py:31-37 | [MR-048] DEFAULT_EXCLUDESにdocs/reviews/*codex*が不在。Codex独自レビューMDがmirror modeで削除対象になる
- [2026-05-01] md:missing-stop-condition | scripts/sync_claude_md.py:209-224 | [MR-048] plan_mirror()がdest-onlyファイルを無差別にDELETE分類。Codex独自成果物か共有残骸かを区別しない
- [2026-05-01] md:missing-output-contract | docs/claude-md-sync.md:72-79 | [MR-048] mirror mode説明に保護対象・注意事項・確認チェックリストが不在。AIが全dest-only削除を正常動作と判断
- [2026-05-01] md:ambiguous-scope | docs/codex-operation-knowledge.md:105-108 | [MR-048] docs/knowledges/の一方通行性は明記するがdocs/reviews/の双方向性に触れない。AIがdocs/配下全体に一方通行を一般化
- [2026-05-01] md:missing-stop-condition | scripts/sync_claude_md.py:285-306 | [MR-048] apply_mirror_rows()がuntracked/trackedを区別せず削除。untracked削除は不可逆だがフェイルセーフなし
- [2026-05-01] md:discoverability | 068_line_ntfy_push.md:223-249 | [MR-047] CLAUDE.md追記でカバー可だが068落とし穴セクション単体での混信安全保証(L206)への導線は依然不在（多層防御として任意改善）
- [2026-05-01] md:missing-stop-condition | CLAUDE.md:77-78 | [MR-046] BG残存send_ntfy_and_wait時の回復手順が不在。AIが「send_ntfy_and_waitを呼べない」と誤判断しsend_ntfyにダウングレード
- [2026-05-01] md:priority-conflict | CLAUDE.md:69 | [MR-046] send_ntfy禁止ルールに「技術的理由含め例外なし」が非明示。AIが「技術的にやむを得ない」と自己正当化しフォールバック
- [2026-05-01] md:discoverability | 068_line_ntfy_push.md:206,223-249 | [MR-046] 並行実行の混信安全保証(L206)が注意事項セクションに孤立。落とし穴セクション(L223)からの参照なしでAIが到達不能
- [2026-05-01] md:ambiguous-scope | docs/handoff.md:1-4 | [MR-045] 「端末間引き継ぎボード」にClaude Code端末間専用の排他制約が不在。to:Codexが構文的に有効でCodex宛伝言が誤記載
- [2026-05-01] md:missing-output-contract | docs/knowledges/tools/083_codex_collaboration.md:17-35 | [MR-045] Claude Code→Codex方向の伝言チャネルが未定義。Codex→Claude方向のみでAIが逆方向の送り先を特定不能
- [2026-05-01] md:discoverability | CLAUDE.md:13 | [MR-045] Codex引継ぎチェックが受け取り専用(Read行動のみ)。Codexへの送り出し導線がCLAUDE.mdに不在でhandoff.mdにフォールバック
- [2026-05-01] content:regression-risk-missed | docs/plans/20260501_060000_verify_unit_mixed_fix.md Step5:L137-138 | [CR-044] 「円安」false positive の「運用影響は軽微」判断がStep6結果に依存するのにStep5段階で結論。still-complex 104社における定量評価が欠落
- [2026-05-01] content:similar-bug-uncovered | docs/plans/20260501_060000_verify_unit_mixed_fix.md Step3a:L79-83 | [CR-044] Step3aのラベル一意性判定が regex 抽出可否の十分条件でない（結合セル・複数段ヘッダ・期別横並びで regex 不向き）。Step3bの手動検証で補完する設計だが自動判定結果の過大評価リスク
- [2026-05-01] content:missing-downstream | docs/plans/20260501_060000_verify_unit_mixed_fix.md Step3:L68-99 | [CR-044] 98社extract_adapter.jsonのnotesフィールド（Geminiがgemini_vision選択の理由）を事前確認するステップが欠落。理由分類によりPDF DL対象を絞り込みGCSコスト削減可能

### 2026-04-30

- [2026-04-30] md:ambiguous-scope | CLAUDE.md:87-91 | [MR-039] §時刻表示ルールが外部ソース変換のみ想定。AI自身がMDに日時を手書きする際の正確性検証義務が適用範囲外
- [2026-04-30] md:missing-source-verification | docs/plans/tools-089-1_order_backlog_extraction_20260430_200000.md:4,142,152 | [MR-039] 更新日時 `2026-05-01 02:30 (JST)` がコミット実時刻 `2026-04-30 13:30:12 +0900` および作業時間帯(4/30 20-23時)と不整合。UTC値にJSTラベル付与の疑い
- [2026-04-30] bug:aggregation-key-ignored | scripts/zaraba_earnings.py:781,786 | [033] `_build_prior_data` の CURRENT/NEXT コンセが `iloc[0]` で QUARTER/FY 無視 → CSV 並び順で 1Q が選ばれ FY 開示で +320.5% 誤検出（1878 大東建託 4/30 11:30）
- [2026-04-30] bug:aggregation-key-ignored | scripts/zaraba_earnings.py:523-532 | [033] `_refresh_prior_consensus` が dict comprehension で TICKER→PROFIT 単純マップ化 → 同 TICKER の複数 QUARTER/FY 行を silent overwrite。`--data consensus` 経路でも同型バグ
- [2026-04-30] content:missing-downstream | docs/plans/tools-066_zaraba_consensus_quarter_match_20260430_153500.md:849 | [033] スキーマ変更（consensus_profit → consensus_profit_by_q）の波及で `_print_prepare_summary` の表示列が常に空欄になる修正コードがプランに未提示
- [2026-04-30] content:regression-risk-missed | docs/plans/tools-066_zaraba_consensus_quarter_match_20260430_153500.md:267-279 | [033] P1-1 旧キー検出時 `cons_profit=None` 早期 return が「初回適用日に F4c が全銘柄で一斉沈黙」する検知漏れリスクの記述漏れ。スキーマバージョン or 起動時 abort の検討無し
- [2026-04-30] bug:aggregation-key-ignored | docs/plans/tools-066_zaraba_consensus_quarter_match_20260430_153500.md:100,219-221 | [033] NEXT 修正案 `sort_values("FY", ascending=False).iloc[0]` で同 FY 複数 broker 時の tie-breaker 不在。SOURCE_USED 別行で `iloc[0]` が pandas stable sort + 元並び依存
- [2026-04-30] content:numeric-inconsistency | docs/plans/tools-066_zaraba_consensus_quarter_match_20260430_153500.md:246-250 | [033] `_refresh_prior_consensus` の `updated` カウンタ意味変更（銘柄単位→キー×銘柄で最大4倍）。「条件追加」コメントで曖昧、運用ログとの非互換誘発
- [2026-04-30] md:stale-context | docs/knowledges/tools/013_tdnet_load.md:320 | [CR-037] _MONTHLY_SUB_CATEGORIESルール記述が削除後コードと不整合のまま残存（CR-033指摘と同一箇所、未修正）
- [2026-04-30] content:unverified-assumption | docs/reviews/033_cr_ambiguous_subcategory_removal.md / 9230b77 | [CR-033] 「downstream get_tdnet_docs は SUB_CATEGORIES を参照しない」前提が不正確。build_monthly_extractor.py 含む6+スクリプトが SUB_CATEGORIES で月次開示を検索
- [2026-04-30] content:similar-bug-uncovered | scripts/tdnet_load_recovery.py:445-468 | [CR-033] _AMBIGUOUS_SUBCATEGORY/_MONTHLY_SUB_CATEGORIES の同一ロジックが recovery 側に残存。parallel と recovery で BQ 出力が分岐
- [2026-04-30] content:missing-downstream | docs/knowledges/tools/013_tdnet_load.md:320-321 | [CR-033] _MONTHLY_SUB_CATEGORIES ルール記述が削除後コードと不整合のまま残存
- [2026-04-30] md:discoverability | skills/structure-optimizer.md:50-56 | [SO-032] 到達可能性テーブルが「読み取り」方向のみ定義。「書き込みトリガー到達可能性」(AI が更新義務を認知する経路)が欠落。030で追加した定義自体の考慮漏れ
- [2026-04-30] md:discoverability | skills/structure-optimizer.md:82-85 | [SO-032] 導線検証テーブル(出力テンプレート)が読み取り/書き込みを区別せず単一「到達可能性」列。新規セクション追加提案時に書き込みトリガー検証が漏れる構造
- [2026-04-30] md:missing-source-verification | docs/reviews/030_structure_optimizer_self_analysis.md:287-291 | [SO-032] 030導線検証でINDEX.mdを「高(CLAUDE.md§知見管理から参照)」と評価したが読み取り到達のみ検証。書き込みトリガー(CLAUDE.md→INDEX.md更新義務)の検証が不在→031で検出
- [2026-04-30] md:missing-source-verification | CLAUDE.md:262 | [MR-035] 「推測で発言するな」ルールが事前学習知識からの想起を「推測」に含むか不明確。BQ結果にFILER_NAME=ベストワンドットがあるのに記憶で「ベイカレント」と報告
- [2026-04-30] md:ambiguous-scope | CLAUDE.md:262 | [MR-035] 「データ属性はBQ・CSV等で確認」の例示が抽象的。ティッカー→企業名対応のような具体パターンが不在で確認義務が発火しない

### 2026-04-29

- [2026-04-29] md:ambiguous-scope | CLAUDE.md:213 | [031] INDEX.md更新義務が「知見ファイル追加・更新時」限定でスキル追加・ファイル移動・リネームをカバーしない → **修正済み**(2026-04-30)
- [2026-04-29] md:discoverability | CLAUDE.md:193-213 | [031] INDEX.md §導線チェックリスト(6項目)への導線がCLAUDE.mdに不在。チェックリスト到達率が低い → **修正済み**(2026-04-30)
- [2026-04-29] md:ambiguous-scope | docs/knowledges/INDEX.md:1 | [031] 冒頭更新注記が「知見ファイル」限定。スキル索引・参照構造・導線チェックリスト更新の対象外 → **修正済み**(2026-04-30)
- [2026-04-29] md:missing-output-contract | docs/knowledges/INDEX.md:152-165 | [031] MD参照構造テーブルに更新トリガー注記なし。他セクションには更新義務記述あり → **修正済み**(2026-04-30)
- [2026-04-29] md:priority-conflict | CLAUDE.md:213 + INDEX.md:148 | [031] 更新義務がCLAUDE.md(知見限定)とINDEX.md(スキル限定)に分散。AI統合解釈不可 → **修正済み**(2026-04-30)
- [2026-04-29] content:similar-bug-uncovered | ad-hoc_code_reviewer_review_trail.md 修正2/修正4 | [029] code-reviewer L19,25 / md-reviewer L19の起動方式セクションにcr_/mr_なしファイル名が残存。修正スコープ漏れ
- [2026-04-29] md:missing-output-contract | ad-hoc_code_reviewer_review_trail.md 修正5 | [029] md-reviewer Pattern 2ポインタテンプレートがプランに未定義。code-reviewer側(修正1)は定義済みだが対称性欠落
- [2026-04-29] format:caller-ref-vague | ad-hoc_code_reviewer_review_trail.md 全体 | [029] 起動方式セクション等への波及が行番号リストで明示されていない
- [2026-04-29] md:context-expansion-risk | CLAUDE.md:329-452 | [026] 知見索引テーブル173行(全体31%)がルックアップデータでありながら毎セッション全量読込。方針・制約と異質な性質が混在
- [2026-04-29] md:priority-conflict | CLAUDE.md:306,310,392,508,557,558 | [026] data_catalog.md参照ルールが5-6箇所に分散記述。表現差からAIが「場面別ルール」と誤解するリスク
- [2026-04-29] md:context-expansion-risk | CLAUDE.md:55-87 | [026] stall検知義務の実装詳細(メトリクス名・閾値・bashコード例33行)がCLAUDE.mdの抽象度を逸脱。知見ファイルレベルの粒度
- [2026-04-29] md:context-expansion-risk | CLAUDE.md:221-271 | [026] クラッシュ復旧手順の全プロセス(51行)がインライン記述。月数回の低頻度タスクで毎セッショントークン消費
- [2026-04-29] md:context-expansion-risk | CLAUDE.md:169-218 | [026] 端末間作業移管の手順詳細(50行、git stash例・NG/OK bash例含む)がインライン記述
- [2026-04-29] md:priority-conflict | CLAUDE.md:116-119,509 | [026] JST統一ルールが「時刻表示ルール」と「コーディング規約」の2箇所に独立記述。正本不明
- [2026-04-29] md:review-recommendation-unsafe | skills/md-reviewer.md:319-323 | [SO-025] Step 8cがクロスルール競合を検出できない。020推奨のL107追加がL100と競合し021事故を誘発したがStep 8cのスコープ外
- [2026-04-29] md:missing-stop-condition | skills/md-reviewer.md:317-323 | [SO-025] Step 8cの副作用検証が事故パターン依存(007→009の単体副作用)で検証軸(単体/クロスルール/状態依存)として体系化されていない
- [2026-04-29] md:missing-source-verification | skills/md-reviewer.md:149-170 | [SO-025] Step 1の過去レビュー追跡がmd-reviewer自身への改善推奨の運用結果追跡に不十分。010是正→024再浮上のフィードバック未検出
- [2026-04-29] md:priority-conflict | CLAUDE.md:92-108 + 068:129-248 | [SO-024] 6件LINE事故の統合分析: CLAUDE.mdとO68§③の正本帰属が未定義で責務分担不明。AIがどちらを優先するか文脈依存
- [2026-04-29] md:ambiguous-scope | CLAUDE.md:107 | [SO-024] L107コンテキストクリアハンドリングが1行に4ルール圧縮(メモリ保存+案内+リマインド+BG例外)。AIが後半例外句を読み落とすリスク
- [2026-04-29] md:priority-conflict | 068:238,248 + CLAUDE.md:103 | [SO-024] 021是正(BG完了通知読む義務)と023是正(BG実行自体禁止)がレイヤー不一致で並列記述。AIが「BGでも通知読めばOK」と誤学習するリスク
- [2026-04-29] md:missing-stop-condition | CLAUDE.md:103 | LINE会話モード中のsend_ntfy_and_waitフォアグラウンド必須制約が不在。実行方法（FG/BG）の規定なくAIが068のBG推奨に従い会話ループ断絶
- [2026-04-29] md:ambiguous-action | 068_line_ntfy_push.md:222 | 見出し「フォアグラウンド実行によるセッションブロック」がFG=問題と誘導。LINE会話モード中はFG必須だが逆方向に誘導
- [2026-04-29] md:ambiguous-scope | 068_line_ntfy_push.md:234 | 「OK: バックグラウンド実行」が無条件OK。LINE会話モード中はNG。条件分岐不在
- [2026-04-29] md:priority-conflict | skills/structure-optimizer.md 全体 | トークン削減を第1目的に掲げるスキルが自身333行(~4,000トークン)を消費。生み出す削減効果との投入対効果が構造的に疑わしい
- [2026-04-29] md:priority-conflict | skills/structure-optimizer.md:66-88,264-281 | 優先順位の定義が「ペルソナの優先順位」と「判断基準」に分散し正本不明。Step 4テーブルとも自己重複
- [2026-04-29] md:missing-output-contract | skills/structure-optimizer.md 全体 | 不備蓄積ログ(004-1)への追記手順が完全欠落。code-reviewer/md-reviewerは必須プロセスとして定義済みだがstructure-optimizerのみ断絶
- [2026-04-29] md:ambiguous-action | skills/structure-optimizer.md:332 | 「再生成はしない」が相互作用問題(L122-124)の新規発見を禁じるか許容するか判断不能
- [2026-04-29] md:missing-source-verification | skills/structure-optimizer.md:144-152 | Step 5トークン概算に方法論不在。推論のみで数値算出不能だが数値テンプレートを要求し「推測で断定しない」(L62)と自己矛盾
- [2026-04-29] md:context-expansion-risk | skills/structure-optimizer.md:11-16,44-49,325-333 | 既存2スキルとの重複記述~120行。3スキル合計1,286行に膨張し正本分散リスク
- [2026-04-29] md:ambiguous-scope | CLAUDE.md:107 | L107「新タスクの処理を開始しない」の「新タスク」定義不在。バックグラウンドタスク完了通知（task-notification）を含むか曖昧で過剰停止を誘発
- [2026-04-29] md:missing-stop-condition | 068_line_ntfy_push.md:228-236 | バックグラウンドsend_ntfy_and_wait完了後の処理義務（出力ファイル読み→返信抽出）が欠落。「通知が届く」で説明終了
- [2026-04-29] md:priority-conflict | CLAUDE.md:100,107 | L100非同期サブタスクフロー(完了→報告)とL107コンテキストクリア待機(処理停止)の競合時優先順位が未定義
- [2026-04-29] md:missing-stop-condition | CLAUDE.md:92-107,534-555 | 「コンテキストクリア」指示のAI行動手順がCLAUDE.md全体に不在。L106は否定例としてのみ言及。3回指示されて3回無視
- [2026-04-29] md:missing-stop-condition | CLAUDE.md:98 | LINE会話モード同期ループに「ユーザー操作待ち」中断メカニズム不在。/clear案内後もループが回り次メッセージを新タスクとして処理
- [2026-04-29] md:ambiguous-action | CLAUDE.md:106 | 「コンテキストクリア」がモード終了の否定例としてのみ記述。肯定的行動定義（クリア手順の案内・完了待ち）が不在
- [2026-04-29] md:ambiguous-scope | CLAUDE.md:322 | 「新しいタスクを受けたら」が質問・探索依頼を包含するか不明確。索引テーブルの用途が「タスク実行前の必読指定」に限定的に読める
- [2026-04-29] md:ambiguous-scope | feedback_claudemd_index_first.md:7 | 「ツール系タスク実行前に」が適用条件の全てで情報探索・質問応答が適用外に読める。TOB索引スキップの直接原因
- [2026-04-29] md:missing-stop-condition | skills/md-reviewer.md:17-31 | メインエージェント側手順のステップ1(提出MD作成)→ステップ2(Agent起動)間にアトミック制約なし。割り込み(LINE返信等)でステップ2が脱落
- [2026-04-29] md:missing-source-verification | skills/md-reviewer.md:13,19 | 「提出MDを作成しただけではmd-reviewerは起動しない」の否定形因果説明が不在。AI が提出MD作成=レビュー依頼完了と誤認
- [2026-04-29] md:missing-stop-condition | CLAUDE.md:98 | ライン会話モード動作フローが同期パターンのみ。Agent起動等の非同期サブタスク起動→中間報告→完了待ち→最終報告のフロー未定義
- [2026-04-29] md:ambiguous-action | CLAUDE.md:92-105 | ライン会話モード中「〜を待て」型LINE短文指示の解釈ガイドライン不在。能動的(自分で起動して待つ)か受動的(外部を待つ)かの判断基準なし
- [2026-04-29] content:unverified-assumption | docs/plans/20260428_220000_predict_zaraba_parity.md P1-1 | MARKET_CAPを「USD建て」と記載するが実際はJPY。zaraba_earnings.py:794に「円単位→億円」と明記。為替換算追加で全銘柄にペナルティ誤発火リスク
- [2026-04-29] content:similar-bug-uncovered | docs/plans/20260428_220000_predict_zaraba_parity.md P1-3 | notebookとbatch_rerunでCONSENSUS SOURCEフィルタ不一致（notebook=全SOURCE, batch_rerun=RAKUのみ）。IFIS銘柄でP1-3修正の挙動が割れる
- [2026-04-29] content:missing-downstream | docs/plans/20260428_220000_predict_zaraba_parity.md P1-1/P1-4 | batch_rerun fetch_shared_data()へのYF_STOCK_INFOクエリ追加でas-of精度（DATE_PAIRS幅13日 vs 週次スナップショット）の設計未記載
- [2026-04-29] content:missing-downstream | docs/plans/20260428_220000_predict_zaraba_parity.md P1-1/P1-4 | batch_rerun pred_recordsカラムリスト(L721-729)にmarket_cap_oku追加が未記載（has_stock_splitは前回指摘済み）
- [2026-04-29] md:ambiguous-scope | CLAUDE.md:94,99 | ライン会話モード「報告・質問は」が確認応答・待機宣言を含まず、AI が「了解です」をsend_ntfy_and_wait対象外と判断しコンソールのみ出力
- [2026-04-29] md:missing-stop-condition | CLAUDE.md:92-103 | ライン会話モードにコンソール出力禁止ルール不在。send_ntfy_and_wait未使用時にコンソールがフォールバック出力先として残る
- [2026-04-29] md:missing-output-contract | CLAUDE.md:92-103 | ライン会話モードに返信受信後のループフロー(受信→処理→再送信)不在。068知見に記載あるがCLAUDE.md正本に欠落

### 2026-04-28

- [2026-04-28] content:unverified-assumption | docs/plans/20260428_220000_predict_zaraba_parity.md P1-4 | STOCK_PRICE_JQUANTSにPERカラムがあると前提するが実在しない。正しくはYF_STOCK_INFO.FORWARD_PE
- [2026-04-28] content:unverified-assumption | docs/plans/20260428_220000_predict_zaraba_parity.md P1-1 | J-Quants get_fin_summaryのShOutFYを前提するがEDAではBQ fin_summaryから取得。APIレスポンスに含まれない可能性
- [2026-04-28] content:unverified-assumption | docs/plans/20260428_220000_predict_zaraba_parity.md P1-3 | CONSENSUS.PROFITの利益段階（経常/営業/純利益）未検証のままOdP→OPフォールバック。IFRS企業で系統的下方バイアスリスク
- [2026-04-28] content:missing-downstream | docs/plans/20260428_220000_predict_zaraba_parity.md P0-2 | batch_rerun pred_recordsカラムリスト(L721-729)にhas_stock_split追加が未記載
- [2026-04-28] content:missing-downstream | docs/plans/20260428_220000_predict_zaraba_parity.md P1-1 | batch_rerun fetch_shared_dataにmarket_cap取得BQクエリ追加が未記載
- [2026-04-28] content:similar-bug-uncovered | docs/plans/20260428_220000_predict_zaraba_parity.md P0-1 | EDAノートブックは株式併合もMAIN_CATEGORY='株式分割・併合'で検知するがpredictはDOC_TITLE LIKE '%株式分割%'のみ。併合時のper-share指標歪みが未検知
- [2026-04-28] format:antipattern-map-missing | docs/plans/20260428_220000_predict_zaraba_parity.md | アンチパターン対応表(plan ID→004/T/G)が末尾にない
- [2026-04-28] format:rollback-missing | docs/plans/20260428_220000_predict_zaraba_parity.md P0-2/P1-2/P1-3/P1-4 | 個別ロールバック手順が欠落(P0-1の「コミットrevert」への参照なし)
- [2026-04-28] md:ambiguous-scope | docs/reviews/015_zaraba_428_md_update_plan.md:67-68 | 更新5「F5の発火条件」が066ではF4（翌期見通し）を指す。066/059の因子番号不整合で誤更新リスク
- [2026-04-28] md:ambiguous-action | docs/reviews/015_zaraba_428_md_update_plan.md:37-43 | 更新2「技術仕様セクションへの反映」の追記先が066内3箇所に分裂し一意に特定不能
- [2026-04-28] md:missing-source-verification | docs/reviews/015_zaraba_428_md_update_plan.md 全体 | 066スコアリング因子テーブル(line 99-113)への翌期非開示cap>=3000億フィルタ更新が漏れ
- [2026-04-28] md:priority-conflict | 066_zaraba_tool.md:99-113 / 059_earnings_model_eda.md:50-64 | 同一スコアリングロジックの因子番号が066(F4=翌期見通し)と059(F5=翌期見通し)で不整合
- [2026-04-28] content:missing-downstream | zara.py:72-73 | watch日付デフォルト化がランチャーに未反映。zaraba_earnings.py改修(91933e1)の下流波及漏れ
- [2026-04-28] content:similar-bug-uncovered | zara.py:61-85 / zaraba_earnings.py | zara.pyラッパー全体がzaraba_earnings.pyのインターフェース変更に追従する仕組みがなく、prepare/catchup/watchの各引数変更時に同型バグ再発リスク
- [2026-04-28] md:ambiguous-scope | CLAUDE.md:538 | 「推測で発言するな」ルールの例示がデータ属性（ticker/上場廃止/データ所属先）に偏り、完了報告前の実態確認パターンが適用範囲外に見える
- [2026-04-28] md:missing-stop-condition | CLAUDE.md:531-548 | 注意事項に「完了報告の裏取り義務」が不在。対応表に「済み」と書く前のRead/git log確認ルールが欠落しAI虚偽完了報告を許容
- [2026-04-28] content:similar-bug-uncovered | scripts/zaraba_tdnet_poller.py:509-524 | NET_SALES営業収入加算ロジックにTDNET_CURRENT_PATTERNSの累計優先フィルタが未適用（本体ループL478-484の横展開漏れ）
- [2026-04-28] content:similar-bug-uncovered | scripts/xbrl_to_jquants/extract_pipeline.py:164-173 | CURRENT_DURATION_CONTEXTSにCurrentQuarterDuration含有でvalid_entries[0]順序依存バグ（zaraba_tdnet_pollerと同型）
- [2026-04-28] md:missing-source-verification | skills/code-reviewer.md:182 | Step 3「プロジェクト固有規約との整合」が004のみ参照しCLAUDE.mdコーディング規約セクション(structlog/GCP認証/JST等)との照合が暗黙的。推奨がCLAUDE.md横断ルールに違反するリスク
- [2026-04-28] content:regression-risk-missed | skills/code-reviewer.md:184-186 | パターン1/3のStep 4に推奨対応の副作用自問がなく、推奨修正案が新たなバグ・CLAUDE.md規約違反を生むリスクの検証導線が不在
- [2026-04-28] md:missing-source-verification | skills/code-reviewer.md:164 | パターン2 Step 1で過去事故事例は閲読するが「同プランへの前回レビュー推奨の実施結果」追跡が含まれず事後検証ループが不在
- [2026-04-28] md:review-recommendation-unsafe | skills/md-reviewer.md:258-271 | Step 6に「情報の正本帰属(Single Source of Truth)」観点が欠落。007でmemoryにコマンド追加を推奨する誤りの構造的原因
- [2026-04-28] md:review-recommendation-unsafe | skills/md-reviewer.md:147-284 | レビュー推奨の上位ルール(CLAUDE.md)整合性検証ステップがプロセスに不在。007推奨がCLAUDE.md L535に違反したが検出されず
- [2026-04-28] md:review-recommendation-unsafe | skills/md-reviewer.md:124-131 | パターン2「6.新規リスク」がテンプレートフィールドのみでプロセスStep 1-7に対応ステップなし。007の方法A追加が009事故を誘発したが副作用未検出
- [2026-04-28] md:review-recommendation-unsafe | skills/md-reviewer.md 全体 | レビュー推奨の事後検証(推奨実施後に新規事故が発生していないか確認)の仕組みが不在。007→修正→009新規事故の連鎖を検出不能
- [2026-04-28] md:priority-conflict | docs/reviews/007_vm_ssh_iap_misread.md:111-119 | 重大指摘#1がmemoryにコマンド追加を推奨→CLAUDE.md L535「memoryはポインタのみ」ルールに違反。正解はコマンド削除+知見MDポインタ化
- [2026-04-28] md:missing-source-verification | docs/reviews/007_vm_ssh_iap_misread.md:102 | 再発防止でmemory修正を推奨する際にCLAUDE.mdのmemory制約・既存feedback(memory_usage)との照合が抜けた
- [2026-04-28] md:ambiguous-scope | 051_windows_linux_vm_guide.md:74-113 | 「Windows→Linux VM」セクションのStep 1/2(送り出し側)/Step 3(受け取り側)が連番構成で操作主体の境界不明確。AIが全Step実行と解釈
- [2026-04-28] md:context-expansion-risk | 051_windows_linux_vm_guide.md:95 | 007で追加した方法A「Claude Codeはこちらを使う」注記がStep 3を送り出し側AIの操作と誤認させる導線を生成
- [2026-04-28] md:missing-source-verification | 051_windows_linux_vm_guide.md:88 | Step 2が存在しないスクリプトpush_to_linux.shを参照(正しくはsync_push.sh)
- [2026-04-28] md:missing-source-verification | 051_windows_linux_vm_guide.md:7 | 関連ファイル欄のsync_secrets_push/pull.shが存在しない(.ps1版のみ存在)
- [2026-04-28] md:stale-context | 066_zaraba_tool.md:24,32,72-75 | 概要・サブコマンド表・データソース節がJ-Quantsを現行記載。watch/catchupとも TDnet+XBRL移行済みだが未更新
- [2026-04-28] md:ambiguous-scope | 066_zaraba_tool.md:217 | 「既知の問題: watchコマンドが機能しない」がwatch限定見出し。J-Quantsリアルタイム不可はcatchupにも影響するが横展開なし
- [2026-04-28] md:stale-context | 066_zaraba_tool.md:384 | 将来TODOに「【ブロッカー】watch TDnet改造」が完了済みのまま残存。AIが未実装と誤認
- [2026-04-28] md:missing-stop-condition | CLAUDE.md 注意事項セクション | マルチターン入力待機ルール不在。「続きあり」「追加情報あり」等の未完了シグナル検知→待機の仕組みがプロジェクト全体ルールに欠落
- [2026-04-28] md:missing-stop-condition | skills/md-reviewer.md:534-543 | 「呼び出し時にエージェントが最初にやること」に入力完了確認ステップなし。手順が入力完了を暗黙前提とした線形フロー
- [2026-04-28] md:missing-stop-condition | skills/md-reviewer.md:18-22 | メインエージェント側手順にユーザー入力完了確認責務が欠落。「すべて書き出す」は情報の網羅性であり入力完了の確認ではない
- [2026-04-28] md:ambiguous-scope | 066_zaraba_tool.md 全体 | catchupの実装詳細セクションが不在。サブコマンド表1行のみでデータソース・スコアリング有無・results結合ロジックが不明
- [2026-04-28] md:missing-stop-condition | 051_windows_linux_vm_guide.md:64-70 | 「VMにはGoogle Driveがない」「自動同期手段はgit+GCSのみ」の否定情報が不在。AIがgdriveパスからDrive同期を推論
- [2026-04-28] md:stale-context | zaraba_earnings.py:2-12 | docstringが「J-Quants APIでポーリング」のまま。066と一致しAIの旧情報確信を二重強化
- [2026-04-28] content:similar-bug-uncovered | docs/plans/20260428_zaraba_scoring_bugs.md / scripts/zaraba_earnings.py:L1655 | F12(PEG)がrec.get("ForEPS")を参照するがTDNET_TAG_MAPにFORECAST_EPSなく_xbrl_to_jquants_recでもForEPSを設定しない。TDnet移行後F12は恒久dead factor
- [2026-04-28] content:unverified-assumption | docs/plans/20260428_zaraba_scoring_bugs.md P0-1 | 仮説1「VMデプロイ漏れ」と仮説2「IFRS context不一致」を列挙するがL478-484のcumulフィルタは"YearDuration"部分一致なので大半のIFRS contextにもマッチする。真の原因候補として「XBRL抽出自体は成功しcumulative_op=FY累計値だがNxFOP側の値が異常」を未検証
- [2026-04-28] content:missing-downstream | docs/plans/20260428_zaraba_scoring_bugs.md P1-2 | F4c(L1602-1613)がactual_odpにOrdinaryProfit(経常利益)を使うがIFRS企業はOrdinaryIncomeタグなし→ORDINARY_PROFITがNone。コンセ比較がIFRS全銘柄で不発だがプランはOPフォールバック案のみでタグ追加の横展開なし
- [2026-04-28] content:similar-bug-uncovered | docs/plans/20260428_zaraba_scoring_bugs.md | _guidance_vs_consensus(L1385-1392)がNxFOP×consensus_profit_nextを比較するがconsensus_profitの定義（経常利益or営業利益or純利益）がV_CONSENSUS_MERGEDのPROFIT列依存で不明確。F4cのactual_odp(経常利益)とも整合未検証
- [2026-04-28] bug:type-mismatch | scripts/zaraba_earnings.py:L1371-1374 / zaraba_tdnet_poller.py:L487-492 | _xbrl_to_jquants_recがraw_extract.get("FORECAST_OP",{}).get("value")で値取得するがextract_tdnet_plはEPS以外をint(float())変換済み。rec["FOP"]等はint型だが_to_num()経由でfloat変換されるため実害は軽微だがForEPS欠落の方が影響大
- [2026-04-28] content:regression-risk-missed | docs/plans/20260428_zaraba_scoring_bugs.md P2-1 | 時価総額フィルタ追加案がp.get("market_cap")を参照するが実コードのキー名はmarket_cap_oku(L800,L1694)。プラン内のサンプルコードが実装時にキー名不一致バグを誘発

### 2026-04-27

- [2026-04-27] md:stale-context | 059_earnings_model_eda.md:226-228,234 | GCSパス表記が旧形式(actual_YYYYMMDD.json)のまま残存→AIがACTUAL_DATEで検索し0件見逃し
- [2026-04-27] md:missing-source-verification | earnings_model_predict.ipynb cell-11:L1193 | 精度集計セルのコメント/dedupロジックが旧命名前提。新命名ではdedupキーがSAVE_DATEに変化し意図と乖離の疑い
- [2026-04-27] md:ambiguous-action | 059_earnings_model_eda.md:236-239 | actual検索候補3つに優先順位なし。SAVE_DATE不明時にどれを最初に試すか判断不能
- [2026-04-27] md:tool-boundary-risk | 004-1_code_review_findings_log.md / md-reviewer.md | 提出元がmd-reviewerスキルを自分でインライン実行し004-1に直接追記を試行。2026-04-26の5件対策（テキスト警告追加）が再発を防げず
- [2026-04-27] md:ambiguous-scope | 004-1_code_review_findings_log.md:10 / md-reviewer.md:463 | 「スキルが起動している」の定義が不在。Readしただけで起動と自認可能
- [2026-04-27] md:missing-source-verification | CLAUDE.md タスク別必読テーブル / 034_data_load_jobs.md | Cloud Scheduler pause時にlocation=asia-northeast1を推測指定。正解us-west1は034に記載済みだが必読導線なし
- [2026-04-27] md:missing-source-verification | 20260427_140000_tdnet_2017_2022_backfill.md:108 | _merge_gemini_juchu動作説明が不正確（Geminiスキップ時sub_categoriesが「空」と記載→実際はGemma結果入り）
- [2026-04-27] md:ambiguous-action | 20260427_140000_tdnet_2017_2022_backfill.md:60,338 | Q並列可否が「非推奨」と2024/2025実績「並列成功」で矛盾。AIがYAML構成を判断不能
- [2026-04-27] md:missing-stop-condition | 20260427_140000_tdnet_2017_2022_backfill.md | stall検知義務（CLAUDE.md必須要件）への参照が欠落。長時間WF stuck時の検知手順なし
- [2026-04-27] content:unverified-assumption | 20260427_140000_tdnet_2017_2022_backfill.md:26-36 | 推定docs数が線形補間のみで実データ（GCS blob count/index CSV）による検証手順なし。OOM 15K/batch判定に不十分
- [2026-04-27] md:missing-output-contract | 013_tdnet_load.md:405-440 / 2024年・2025年バックフィルプラン | バックフィル完了時の伝播先（013/active_jobs/memory）が後片付けチェックリストに網羅されておらず4箇所中3箇所が未更新で放置
- [2026-04-27] md:stale-context | 013_tdnet_load.md:427 | 2025年バックフィル「待ち」が全完了後も残存。インライン状態に最終確認日が無く古い状態と区別不能
- [2026-04-27] content:missing-downstream | 20260425_001000/091000_gap_backfill.md §後片付け | monitor_backfill.py経由実行ではPostToolUseフック不発火でactive_jobs.md自動追記されない構造的欠陥が未認識

### 2026-04-26

- [2026-04-26] content:regression-risk-missed | docs/plans/20260426_095900_backfill_status_tracking.md P0-1/P1-1 | 「手動ステップの記憶依存が根本原因」と診断しながら対策が新しい手動ルール追加（check_backfill_status.py手動実行+CLAUDE.mdルール追記）で同型再発
- [2026-04-26] content:similar-bug-uncovered | docs/plans/20260426_095900_backfill_status_tracking.md | キャンセル後のBQ状態確認フローが未設計（事故の直接トリガーだが対策に反映なし）
- [2026-04-26] format:verification-thin | docs/plans/20260426_095900_backfill_status_tracking.md | 検証戦略のdev実機がsmoke testと実質同一内容、4段網羅していない
- [2026-04-26] content:unverified-assumption | docs/plans/20260426_072156_cloudbuild_auto_jobs_update.md P0-1 | `images:`ディレクティブがsteps完了後にpushするため、step2のjobs updateが旧digestを解決する実行順序問題
- [2026-04-26] bug:error-swallowing | docs/plans/20260426_072156_cloudbuild_auto_jobs_update.md P0-1 | step2 bashスクリプトにset -eがなく、forループ内のjobs update失敗が無視され部分更新
- [2026-04-26] content:similar-bug-uncovered | docs/plans/20260426_072156_cloudbuild_auto_jobs_update.md | 42本中40本の他cloudbuild YAMLも同じ手動jobs update漏れ問題を抱えるが横展開未言及
- [2026-04-26] format:verification-thin | docs/plans/20260426_072156_cloudbuild_auto_jobs_update.md | 検証戦略がsmoke→本番の2段のみ、dev環境ステップ欠落
- [2026-04-26] md:missing-output-contract | 004-1_code_review_findings_log.md | 追記手順（日付見出しルール・追記位置）が004-1自身に書かれておらず日付見出し重複・順序崩壊リスク
- [2026-04-26] md:ambiguous-action | 004-1_code_review_findings_log.md §傾向分析 | 傾向分析の実行主体が不明でAIがエントリ追記時に自律的に分析着手するリスク
- [2026-04-26] md:missing-stop-condition | 004-1_code_review_findings_log.md §タグカタログ | タグ新規追加が無制限でタグ爆発→傾向分析トリガー永久不発火リスク
- [2026-04-26] md:ambiguous-scope | 004-1_code_review_findings_log.md L6 | 適用範囲がスキル経由に限定して読めad-hocレビュー不備が記録漏れ
- [2026-04-26] md:missing-output-contract | 004-1_code_review_findings_log.md §対策履歴 | 対策履歴の記録フォーマットが未定義で記載粒度がばらつく
- [2026-04-26] md:missing-stop-condition | 004-1_code_review_findings_log.md §蓄積エントリ | ファイル長大化時の古いエントリアーカイブ・切り出し基準が未定義
- [2026-04-26] md:tool-boundary-risk | CLAUDE.md L347 | 索引エントリがreviewerスキル経由せず直接004-1を参照→提出元が自分で追記する導線
- [2026-04-26] md:ambiguous-scope | 004-1_code_review_findings_log.md L6 | 「検出した不備の蓄積」が書き込み権限の制約に読めず誰でも書ける状態
- [2026-04-26] md:ambiguous-action | code-reviewer.md/md-reviewer.md | 「スキル経由でなくても追記」の主語が曖昧で通常作業AIが自分で追記と解釈するリスク
- [2026-04-26] md:tool-boundary-risk | 004-1_code_review_findings_log.md §記録フォーマット | 記録フォーマットが前提条件なく自己完結で書き込み権限制御が構造的に効かない
- [2026-04-26] md:context-expansion-risk | CLAUDE.md L348 | 依頼者に004-1を読ませる導線が「ついでに書いておこう」を誘発

### 2026-04-25

- [2026-04-25] content:missing-downstream | docs/plans/20260425_165500_aiplatform_to_genai_migration.md P0-2 | Dockerfile.download-monthly が対象から完全に欠落。移行後 Cloud Run で google-genai 未インストールクラッシュ
- [2026-04-25] content:unverified-assumption | docs/plans/20260425_165500_aiplatform_to_genai_migration.md §Embedding | Embedding モデル名が text-embedding-005 だが実コードは text-embedding-004
- [2026-04-25] content:similar-bug-uncovered | docs/plans/20260425_165500_aiplatform_to_genai_migration.md P0-1/P0-4/P2-2 | Part.from_data()→types.Part.from_bytes() の API差異が 3 スクリプトで未言及
- [2026-04-25] content:missing-downstream | docs/plans/20260425_165500_aiplatform_to_genai_migration.md P1-3 | aws_mcp_search_colab.py の Content/Part/system_instruction 移行が「共通パターン」範囲外だが未記述
- [2026-04-25] content:missing-downstream | docs/plans/20260425_165500_aiplatform_to_genai_migration.md P1-3 | 知見MD 029_aws_mcp_servers.md:L158-166 のコード例が旧SDK のまま放置される
- [2026-04-25] format:caller-ref-vague | docs/plans/20260425_165500_aiplatform_to_genai_migration.md 全項目 | 「呼び出し側への波及」フィールドが全項目で欠落（テンプレ7フィールド中2フィールド欠落）
- [2026-04-25] format:antipattern-map-missing | docs/plans/20260425_165500_aiplatform_to_genai_migration.md | アンチパターン対応表が末尾にない
- [2026-04-25] bug:ipynb-codegen-escape | scripts/tob_prediction/shap_analysis.ipynb:Cell23 | ipynbをPython json.dumpで生成時、f-string内の`\n`がリテラル改行に展開されSyntaxError。sourceフィールドはchar配列のため`\\n`にエスケープ必要。初回修正でstr.replaceしたが`list()`で再文字配列化したため修正が無効化（2回発生）
- [2026-04-25] bug:race-condition | scripts/tob_prediction/train_rf.py:StandardScaler | StandardScalerがX_trainをin-place上書き→Optuna CV内でvalidation foldにtrain foldのscaler漏れ（data leakage）
- [2026-04-25] content:unverified-assumption | scripts/tob_prediction/train_rf.py:shares_est | shares_est=equity/bpsは自己株式控除後BPSを使うため発行済株式数を過小推定する可能性
- [2026-04-25] bug:type-mismatch | scripts/tob_prediction/shap_analysis.ipynb:Setup | Colab環境で `from google.colab import auth` がトップレベル実行されローカル環境のauth変数が未定義→NameError。setup_runtime()でラップし返り値で渡す必要あり
- [2026-04-25] bug:type-mismatch | scripts/tob_prediction/shap_analysis.ipynb:DataLoad | セル単独再実行時にRUNTIME等のグローバル変数が未定義→NameError。globals()ガードとauth is Noneチェックが必要
- [2026-04-25] bug:error-swallowing | scripts/tob_prediction/shap_analysis.ipynb:SMOTENC | TomekLinks後にminority_countが1以下になるとSMOTENC k_neighbors=0でクラッシュ。minority_count >= 2 ガードが必要
- [2026-04-25] bug:type-mismatch | scripts/tob_prediction/shap_analysis.ipynb:SHAP | shap.TreeExplainer.shap_values()の戻り値がSHAPバージョンによりlist/ndarray(3D)/Explanationオブジェクト。list判定のみではExplanation型でAttributeError
- [2026-04-25] bug:type-mismatch | scripts/tob_prediction/shap_analysis.ipynb:Activist | アクティビスト有無のmask適用時に該当0件だとnp.mean()が空配列でwarning。safe_mean()ヘルパーが必要

### 2026-04-24

- [2026-04-24] content:missing-downstream | docs/plans/20260424_212000_docs_scripts_cleanup.md B-3 | poc_gemma4_phaseD_report.py削除でorchestrator.sh:343の呼び出し先が消失
- [2026-04-24] content:numeric-inconsistency | docs/plans/20260424_212000_docs_scripts_cleanup.md | 「32本中17本孤立」だが実態は33本（ng73_analysis_detail.md未カウント）
- [2026-04-24] content:similar-bug-uncovered | docs/plans/20260424_212000_docs_scripts_cleanup.md B-4 | schedule_backfill_2024.shとcleanup_backfill_schedulers.shがペアだが片方のみ削除
- [2026-04-24] content:similar-bug-uncovered | docs/plans/20260424_200200_extract_adapter_quality.md Phase1 | 042-1 Pattern J（スキャンPDF診断フロー）が新規adapter作成手順に未組込
- [2026-04-24] content:numeric-inconsistency | docs/plans/20260424_200200_extract_adapter_quality.md Phase2 | NG上位20社合計169件を「~180件=60%」と過大記載（実55.6%）
- [2026-04-24] format:rollback-missing | docs/plans/20260424_200200_extract_adapter_quality.md Phase4 | GCS一括アップロード前のスナップショット・ロールバック手順が未定義
- [2026-04-24] content:similar-bug-uncovered | docs/plans/20260424_200200_extract_adapter_quality.md 全体 | 042-1 §再発防止TODO #2（non-tdnet doc_title_pattern緩和）がスコープ外で未対処
- [2026-04-24] format:verification-thin | docs/plans/20260424_200200_extract_adapter_quality.md Phase4 | 検証戦略がsmoke/dev/prod/回収手順の4段を網羅していない
- [2026-04-24] format:antipattern-map-missing | docs/plans/20260424_200200_extract_adapter_quality.md | アンチパターン対応表（042-1パターンA-J対応）が末尾にない
- [2026-04-24] content:unverified-assumption | docs/plans/tools-017_claude_code_hooks_logger_20260424_114759.md P0-1 | 知見ファイル017に hook stdin の session_id 含有が既記載だが診断フェーズを設計（知見ファイル読み飛ばし）
- [2026-04-24] content:missing-downstream | docs/plans/tools-017_claude_code_hooks_logger_20260424_114759.md P0-1 | CLAUDE.md L199/L234 と知見017 L33-37/L113-131 への波及が未記載
- [2026-04-24] format:caller-ref-vague | docs/plans/tools-017_claude_code_hooks_logger_20260424_114759.md P0-2/P1-1 | 「呼び出し側への波及」「ロールバック」フィールドが欠落
- [2026-04-24] content:unverified-assumption | CLAUDE.md:L228 | 「復元候補セッション数: N」がスクリプト出力の「セッション数: N」と不一致

### 2026-04-21

- [2026-04-21] content:unverified-assumption | docs/plans/20260421_063341_tdnet_load_code_review.md P0-3 | 「既に except ValueError がある」誤認、実コードで parse_tdnet_filename 呼び出しは裸
- [2026-04-21] content:data-loss-path | docs/plans/20260421_063341_tdnet_load_code_review.md | ai-finalize で text 無し doc の pending_* 行 DELETE によるデータロスト経路未指摘
- [2026-04-21] content:orphan-resource | docs/plans/20260421_063341_tdnet_load_code_review.md | phase_gemini_tanshin_batch の orphan Gemini batch + 例外握り潰し未指摘
- [2026-04-21] content:similar-bug-uncovered | docs/plans/20260421_063341_tdnet_load_code_review.md | P1-3 orphan cancel が phase_gemini_tanshin_batch 経由にも必要な件の横展開漏れ
- [2026-04-21] format:line-number-drift | docs/plans/20260421_063341_tdnet_load_code_review.md | plan と実コードで最大 +27 行のドリフト（基準 commit 未指定が原因）
- [2026-04-21] content:priority-inflation | docs/plans/20260421_063341_tdnet_load_code_review.md P0-6 | 読みづらさ（errors=1 デッドコード）のみで P0
- [2026-04-21] bug:partition-prune-loss | docs/plans/20260421_063341_tdnet_load_code_review.md P0-5 | parametrize 化後に DATE 型指定が漏れると partition prune が無効化するリスクが検証に含まれていない
- [2026-04-21] bug:error-swallowing | scripts/tdnet_load_parallel.py:1322-1323 | Embedding parse 失敗が silent continue（プラン P1-9 でリストにも無し）
- [2026-04-21] bug:error-swallowing | scripts/tdnet_load_parallel.py:779-780 | Vision OCR 個別 doc パース失敗が silent（プラン P1-9 リスト漏れ）
- [2026-04-21] bug:resource-leak | scripts/tdnet_load_parallel.py:680 | `_download_batch_results` が blob 全量メモリロード（004 C-2 違反、プラン未指摘）

## 傾向分析

**本セクションはユーザーの明示的な指示があった場合にのみ実行する。エントリ追記時に自動発火しない。**

ファイルが長大化した場合は `offset`/`limit` で全エントリを読むこと（直近偏重を避ける）。100件を超えたらアーカイブ分割を検討。

### トリガー条件

- **単一タグが 3 件以上** 蓄積した時点で分析着手
- **月次で全タグの頻度集計**（毎月 1 日、前月分を集計）
- 重大（bug:sql-injection / bug:race-condition 等）は 1 件でも即分析

### 分析手順

1. 該当タグのエントリを一覧化し、**共通原因**を抽出
2. 共通原因が「プラン作成時の見落とし」なら → `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット にルール追加（定義正本）
3. 共通原因が「レビュー観点の不足」なら → `skills/code-reviewer.md` のチェックリストに項目追加
4. 共通原因が「コード設計の繰り返しミス」なら → `004` のアンチパターン集（A-x / B-x など）に新項目追加
5. 分析結果と対策を**本ファイル末尾「対策履歴」に追記**（どのエントリを根拠にどのルール追加をしたか紐付け）

### 分析時の禁止事項

- **1 件だけのエントリを根拠にルールを追加しない**（過剰反応）
- **タグ分類を細かくしすぎない**（1 件 1 タグ状態は傾向が見えない）
- **対策でルールを増やす時は既存ルールの整理も同時に**（ルールインフレ回避）

## 対策履歴

傾向分析の結果として実施した 004 / スキル md 改訂をここに記録する。形式:

```
- [YYYY-MM-DD] <根拠タグ×N件> → <対策先ファイル> | <対策内容の1行要約>
```

### 2026-05-02: 初回傾向分析 + 対策実施

**分析対象**: 2026-04-21〜2026-05-02 の全蓄積エントリ（100件超）
**分析方法**: structure-optimizer 3軸分析（トークン効率・AI判断安定性・到達可能性）
**根拠レビュー**: 011, 030, 042, 057, 058, 059 の自己指摘記録

#### 検出した傾向と実施した対策

- [2026-05-02] `md:review-quality-low`×3 + `md:review-recommendation-unsafe`×5 → `skills/code-reviewer.md` | **SR-1**: パターン2妥当性チェックを「独立仮説→方向性照合→対症療法パターン検出」の3ステップに構造化。プランの方向性を無批判に受け入れてフォーマット・副作用チェックに終始する問題に対処（CR-058/059で3サイクル要した事故が根拠）
- [2026-05-02] `format:antipattern-map-missing`×7 + `format:verification-thin`×3 + `format:rollback-missing`×2 + `format:caller-ref-vague`×3 → `docs/plans/_template_refactor.md`, `skills/planning.md`, `CLAUDE.md` | **SR-3**: テンプレート末尾に「提出前セルフチェック」8項目を追加。planning.md の「セルフチェックは書かない」方針を撤回し作成者側の事前チェック義務を追加。CLAUDE.md §作業計画の管理にセルフチェック義務のポインタ追加。導線: CLAUDE.md→テンプレ末尾→チェック実行→レビュー提出
- [2026-05-02] 011改善提案#1〜#3 + 042メタ教訓 → `skills/code-reviewer.md` | **QF-1〜4**: 確認の結果、4件とも提案後に既に取り込み済みだった（Step 3 CLAUDE.md参照/Step 4副作用自問/Step 1前回推奨追跡/記載先明記義務）
- [2026-05-02] md-reviewer 自己レビュー傾向分析（010/025/013/031/035/039/045/053/054/055/060/061）→ `skills/md-reviewer.md` | **QF-1**: パターン4（運用事故—自己行動不備記録）を§起動パターンに正式定義。060/061で暗黙使用されていたパターンを明文化。判定・出力先・必須評価項目を定義
- [2026-05-02] 同上 | **QF-3**: Step 8c を3段階→4段階に拡張。(iv)再発防止策の実効性: 意志依存型の対策を検出し、構造的強制の代替案（hook/validator/テンプレート埋込/CLAUDE.mdトリガー導線）の併記を義務化。041→054→060の3連続再発パターン（T1）が根拠
- [2026-05-02] 同上 | **最優先指針 item 3 追加**: 「対処療法的な提案で終わらせない」を§最優先指針に新設。検出品質（item 2: 表面的な添削で終わらせない）に加え、提案品質の指針を生成段階に配置。QF-3（8c-iv）と合わせて予防+検出の2層化
- [2026-05-02] 同上 | **SR-1**: 出力テンプレートを155行→約70行に圧縮（650行→564行、86行削減）。プレースホルダー記述を除去しセクション名+必須フィールド一覧に簡素化

#### 今後の分析スコープ

**上記対策のカットオフ: 2026-05-02**。次回の傾向分析は 2026-05-03 以降の蓄積エントリを対象とする。2026-05-02 以前のエントリは対策済みとして再分析しない（同一タグが再発した場合は対策の効果不足として新規分析対象）。

# コードレビュー: STOCK.CONSENSUS テーブル再構成（QUICK 5項目対応）

- 日時: 2026-05-05 13:04 JST
- 対象: `docs/plans/tools-022_consensus_load_20260505_125600.md`
- パターン: 2 (改修)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: BQ STOCK.CONSENSUS テーブルを DROP→新スキーマで CREATE し、PROFIT→ORD_PROFIT リネーム+4列追加（REVENUE, OP_PROFIT, NET_PROFIT, EPS）、TARGET列廃止（FY列で年度識別）、RAKUソース廃止→IFIS+QUICKの2ソース構成に変更。VIEW・TVF・下流スクリプト5本の改修を含む
- 品質評価: **B** — スコープ特定と影響ファイル一覧は丁寧だが、改修テンプレート準拠率が低く、下流スクリプト改修の具体的方針が欠落。「TARGET廃止→FY判定」の変換ロジックが未設計のまま P1 に後回しされている
- 主要リスク:
  1. TARGET廃止後の「当期/来期」判定ロジックが全下流スクリプトで未設計であり、FY列だけでは判定不能な場面がある
  2. batch_rerun_predict.py の cons_map キー構造が (TICKER, QUARTER, TARGET) → (TICKER, QUARTER, FY) に変わる際の as-of マージ互換性が未検討
  3. zaraba_earnings.py の _consensus_to_prior_fields() が TARGET="CURRENT"/"NEXT" でフィルタしており、書き換え方針が未記載

## 【パターン2: 改修プラン評価】

### フォーマット適合性チェック

本プランは `_template_refactor.md` フォーマット（P0/P1 + 7フィールド構成）ではなく、汎用テンプレート形式（目的/背景/作業ステップ構成）で書かれている。ユーザー指定によりパターン2として実施するが、以下のフォーマット違反を検出:

- [x] 冒頭に対象ファイルの基準 commit hash が書かれているか → **書かれている**（`cd9fb99`）
- [ ] 前提サマリで過去修正と残件数が明示されているか → **なし**。改修テンプレートの「前提サマリ」節が不在
- [ ] 優先度の定義（P0/P1/P2 昇格基準）が冒頭にあるか → **なし**。P0/P1 は作業ステップ内で使われているが、定義は明示されていない
- [ ] 各項目が「症状 / 該当 / 根本原因 / 修正方針 / 呼び出し側波及 / 検証 / ロールバック」7フィールドを揃えているか → **揃っていない**。汎用テンプレート形式で作業ステップのみ
- [ ] 修正方針に before/after の両方が書かれているか → **なし**
- [x] 呼び出し側への波及が該当行リストで明示されているか → **明示されている**（影響ファイル一覧に行番号・変更概要あり）
- [ ] 「既に〜がある」系の前提記述を実コードと照合し、食い違いが無いか → **下記で検証**
- [ ] アンチパターン対応表（plan ID → 004/T/G）が末尾にあるか → **なし**
- [ ] 検証戦略が smoke / dev / prod / 回収手順の 4 段を網羅しているか → **部分的**。SQL単体・書き込みsmoke・読み取りsmokeはあるが prod 段階がない
- [x] ロールバック手順が書かれているか → **書かれている**（回収手順に記載）
- [x] 読みづらさ・デッドコードだけで P0 に置かれている項目が無いか → **問題なし**
- [ ] 関連 commit・知見 MD・incident ログへのリンクがあるか → **部分的**。知見MDリンクはあるがcommitリンクはない

**フォーマット違反まとめ**: 汎用テンプレートで記述されているため改修テンプレートの 7 フィールド構成を満たさない。ただしユーザー指定なので許容範囲として内容レビューに進む。

### 妥当性

プランの方向性（QUICK 5項目対応のためのスキーマ拡張、RAKU廃止、TARGET→FY方式変更）は合理的。真因は「現行テーブルが経常利益のみの単一指標構造であり、QUICK 5項目を格納できない」であり、スキーマ再構成はその真因に対処している。

ただし **TARGET列廃止** の設計判断について重要な問題がある:

TARGET列は「当期 (CURRENT) / 来期 (NEXT)」のセマンティクスを持つ。FY列（YYYYMM）はあくまで決算期の値であり、「その銘柄にとって当期か来期か」を判定するには **別途、銘柄ごとの現在期を知る必要がある**。プランにはこの判定ロジックの設計が記載されていない。

### 副作用・デグレードチェック

- [ ] **batch_rerun_predict.py の cons_map 互換性**: 現行は `cons_map[(TICKER, QUARTER, TARGET)]` で `"CURRENT"` / `"NEXT"` をキーに使っている（`batch_rerun_predict.py:L324,L327`）。TARGET廃止後は `(TICKER, QUARTER, FY)` に変わるが、FY値から CURRENT/NEXT を判定するために銘柄ごとの決算期情報が必要。この変換ロジックが `compute_features()` 内の as-of コンセンサス参照（L319-L329）全体に波及する
- [ ] **zaraba_earnings.py の prior_data.json 互換性**: `_consensus_to_prior_fields()` は `TARGET=="CURRENT"` / `TARGET=="NEXT"` でフィルタリングして prior_data.json に書き込む（`zaraba_earnings.py:L866,L878`）。prior_data.json のスキーマ（`consensus_profit_by_q`, `consensus_profit_next`, `consensus_profit_next_fy`）はそのまま使えるが、VIEW/TVF が TARGET 列を返さなくなるため、FY値と銘柄の決算期を比較して CURRENT/NEXT を自力判定する必要がある
- [ ] **V_CONSENSUS_MERGED VIEW 消費者のカラム参照**: `_load_or_fetch_consensus()` (`zaraba_earnings.py:L482`) は `SELECT ... PROFIT, TARGET, SOURCE_USED` を発行し、`_refresh_prior_consensus()` (`zaraba_earnings.py:L519`) で `"TARGET" not in df_conse.columns` をガード条件にしている。TARGET 列が消えるとここが always-true になり全更新がスキップされる。**これはサイレント障害を引き起こす**
- [ ] **export_consensus_csv.py のピボット**: `QUARTER == "FY" and TARGET == "NEXT"` でピボット列 "NEXT" を構築している（`export_consensus_csv.py:L52`）。TARGET 廃止後の代替判定が必要
- [ ] **lib_conse_csv_from_view.py の TARGET 分岐**: L44 で `row["TARGET"]` を参照し、L50-55 で CURRENT/NEXT を分岐している。TARGET 列がなくなると KeyError でクラッシュする

### 抜け漏れ（類似観点での横展開含む）

- [ ] **VIEW の SELECT 列 SOURCE_USED の扱い**: 現行 V_CONSENSUS_MERGED は IFIS 優先・RAKU 補完で SOURCE_USED 列（どちらのソースが使われたか）を返している。新 VIEW では QUICK 優先・IFIS 補完になるが、SOURCE_USED 列の定義・ロジックがプランに未記載
- [ ] **TVF fn_consensus_merged_asof の RAKU → QUICK 移行**: 現行 TVF SQL (`scripts/sql/create_fn_consensus_merged_asof.sql`) は `SOURCE = 'RAKU'` と `SOURCE = 'IFIS'` で CTE を分けている（L4, L21）。これを QUICK + IFIS に書き換える必要があるが、新 TVF SQL の設計がプランにない
- [ ] **batch_rerun_predict.py の SOURCE='RAKU' ハードコード**: L186 で `AND SOURCE = 'RAKU'` を直接指定している。VIEW/TVF を使わず CONSENSUS テーブルを直接参照しているため、SOURCE='QUICK' への変更が必要
- [ ] **QUARTER の差異**: IFIS は 1Q/2Q/3Q/FY の 4 QUARTER を返すが、QUICK は「FY のみ」とプランに記載されている（設計§ソース別データ特性）。これは VIEW マージ時に QUARTER='1Q'〜'3Q' が IFIS 単独ソースとなることを意味するが、この挙動がプランで明文化されていない
- [ ] **data_catalog.md の VIEW/TVF スキーマ定義更新**: プランの P1 に含まれているが、VIEW の新カラム（REVENUE, OP_PROFIT, NET_PROFIT, EPS, SOURCE_USED）の定義が記載されていない
- [ ] **update_conse_quick.py の存在**: 095_consensus_quick.md に `scripts/update_conse_quick.py` が言及されているが、プランの影響ファイル一覧にこのスクリプトがない。QUICK データの BQ 書き込みスクリプトの改修（CSV → BQ INSERT への切替）もプランに含まれるべき
- [ ] **Dropbox CSV 出力パス**: `export_consensus_csv.py` は Dropbox にアップロードしているが（L28: `DBX_UPLOAD_PATH`）、新スキーマ対応で CSV フォーマットが変わる（5項目追加の反映可否）。下流で CSV を消費するツールへの影響が未調査
- [ ] **prior_data.json のスキーマ**: zaraba_earnings.py の ConsensusFields TypedDict（L850-854）は `consensus_profit_by_q`, `consensus_profit_unit`, `consensus_profit_next`, `consensus_profit_next_fy` の 4 フィールド。新スキーマで ORD_PROFIT 以外の項目（REVENUE, OP_PROFIT, NET_PROFIT, EPS）を prior_data.json にも追加するか、ORD_PROFIT のみを継続するかの設計判断が未記載

### 新規リスク

- **データ欠損リスク**: DROP → CREATE で既存データを破棄するとプランに記載されているが、「移行不要」の判断根拠がない。過去の DATAAT 別時系列データは batch_rerun_predict.py の as-of 参照で使われている。DROP 後にこのスクリプトを過去日で再実行すると CONSENSUS データが存在しない状態になる
- **移行期の空テーブル問題**: DROP → CREATE 後、IFIS/QUICK の書き込みスクリプトが実行されるまで CONSENSUS テーブルは空。その間に zaraba_earnings.py が実行されるとコンセンサスなしで prior_data.json が生成される
- **QUICK の QUARTER='FY' のみ制約**: QUICK が FY のみなのに対し、既存下流スクリプトは 1Q/2Q/3Q のコンセンサスも参照している（zaraba_earnings.py の consensus_profit_by_q はQUARTER別dict）。RAKU 廃止後、1Q〜3Q は IFIS 単独となるが、IFIS は CURRENT のみ。つまり **1Q〜3Q の NEXT コンセンサスはどのソースからも取得できなくなる**。現行は RAKU が NEXT の 1Q〜3Q も提供していたため、これはデグレード

---

## 【重大な指摘】（即修正）

### #1 TARGET廃止後の当期/来期判定ロジックが全く設計されていない

- 箇所: プラン全体、特に P1 読み取り側スクリプト改修（ステップ 7-11）
- 事象: プランは「TARGET廃止→FY列で当期/来期を判定するロジックに変更」と記載しているが、その判定ロジック自体が設計されていない。FY 列には `"202603"` のような値が入るが、これが「当期」か「来期」かを判定するには銘柄ごとの現在の決算期（= 直近の FY 開始日 or 前回決算の FY 値）が必要
- トリガー: 全下流スクリプトの改修時に判定ロジックが必要になるが、プランにガイダンスがないため各スクリプトで独自実装される可能性がある
- 影響: 判定ロジックの不統一 → スクリプト間でコンセンサス値の解釈が異なる → 予測モデルの品質劣化
- 根拠: `zaraba_earnings.py:L866` `conse_one_ticker["TARGET"] == "CURRENT"`, `batch_rerun_predict.py:L186` `TARGET IN ('CURRENT', 'NEXT')`, `lib_conse_csv_from_view.py:L50-55` の TARGET 分岐が全て FY 値による判定に置き換わるが、判定基準が未定義
- 推奨対応: VIEW / TVF 側で FY 値と銘柄マスタの決算期を比較して CURRENT/NEXT 相当の派生列を生成するか、あるいは TARGET 列を「読み取り側が書き込む」のではなく「VIEW/TVF が算出する」設計にする。どちらを採用するかをプランに明記すべき

### #2 zaraba_earnings.py の TARGET ガード条件がサイレント障害を引き起こす

- 箇所: `scripts/zaraba_earnings.py:L519`
- 事象: `_refresh_prior_consensus()` 内の `if df_conse.empty or "TARGET" not in df_conse.columns:` というガード条件は、TARGET 列が VIEW から消えた後に always-true となり、コンセンサス更新が全てスキップされる。warning ログは出るが、prior_data.json のコンセンサス値が永久に古いまま固定される
- トリガー: VIEW から TARGET 列が削除された後、zaraba_earnings.py の `--data consensus` を実行した場合
- 影響: ザラバツールの prior_data.json 内コンセンサスが更新されず、決算乖離率の算出が古い値で行われる。結果として予測精度が劣化するがエラーにならないため検知が遅れる
- 根拠: `zaraba_earnings.py:L519` の条件文そのもの
- 推奨対応: プランの影響箇所一覧にこの行を追加し、TARGET 列の代わりに何を検査するかを明記する（例: `"FY" not in df_conse.columns`）

### #3 batch_rerun_predict.py の SOURCE='RAKU' ハードコードが移行後にデータ取得ゼロになる

- 箇所: `scripts/earnings_model/batch_rerun_predict.py:L186`
- 事象: `AND SOURCE = 'RAKU'` が直接 SQL にハードコードされている。RAKU ソース廃止後、このクエリは 0 行を返す。cons_map が空になり、全銘柄の consensus_deviation が None になる
- トリガー: RAKU ソースのデータが CONSENSUS テーブルから消えた後（DROP→CREATE で即座に発生）
- 影響: 決算反応モデルの F4（コンセンサス乖離）因子が全銘柄で無効化。予測精度のサイレント劣化
- 根拠: `batch_rerun_predict.py:L181-188` の SQL リテラル
- 推奨対応: `SOURCE = 'RAKU'` を削除し、TVF `fn_consensus_merged_asof` を使った as-of 参照に切り替える。プランにこの移行を P0 に格上げして記載すべき（モデルの品質に直結する）

### #4 DROP→CREATE による過去データ喪失が batch_rerun_predict.py の as-of 参照を破壊する

- 箇所: プラン §テーブル移行方式 「DROP → CREATE（既存データ破棄、移行不要）」
- 事象: batch_rerun_predict.py は `DATAAT <= predict_date` で過去のコンセンサスを as-of 参照している（L181-188）。DROP 後はテーブルが空のため、過去日の再実行が不可能になる
- トリガー: テーブル DROP 後に batch_rerun_predict.py を過去日（例: 20260401〜20260428）で再実行した場合
- 影響: 過去の accuracy_summary が再計算不能。決算反応モデルの精度評価の再現性が失われる
- 根拠: `batch_rerun_predict.py:L180-188` の as-of クエリが DATAAT ベースで全期間を参照
- 推奨対応: 既存データのバックアップ方針を記載する（GCS エクスポート or 別テーブルにコピー）。あるいは batch_rerun_predict.py が GCS 上の prediction JSON を読み直す形で過去データ参照を回避できるかを検討

### #5 RAKU廃止後に 1Q〜3Q NEXT コンセンサスが取得不能になる

- 箇所: プラン §ソース別データ特性
- 事象: QUICK は QUARTER='FY' のみ、IFIS は TARGET='CURRENT' のみ。現行 RAKU は 1Q〜3Q の CURRENT/NEXT を両方提供していた。RAKU 廃止後、1Q〜3Q の NEXT コンセンサスはどのソースからも取得できなくなる
- トリガー: RAKU 廃止後のあらゆる実行
- 影響: zaraba_earnings.py の `consensus_profit_next` が 1Q〜3Q 開示銘柄で常に None になる（現行は RAKU 経由で値がある）。ただし実運用上、コンセンサスの NEXT は主に FY で参照されるため影響は限定的
- 根拠: プラン §ソース別データ特性テーブルと、`zaraba_earnings.py:L878` の NEXT フィルタ
- 推奨対応: この影響が許容範囲かをプランに明記する（RAKU の 1Q〜3Q NEXT は実際に使われていたか、使用頻度を調べる）

---

## 【改善提案】（可読性・保守性）

### #1 VIEW の新カラム設計をプランに記載する

- 箇所: プラン §設計 → VIEW マージルール
- 現状: `V_CONSENSUS_MERGED: QUICK優先、IFIS補完。TARGET列なし。` の1行のみ。新 VIEW の SELECT リストが不明
- 提案: VIEW が返すカラム一覧（DATAAT, TICKER, FY, QUARTER, ORD_PROFIT, REVENUE, OP_PROFIT, NET_PROFIT, EPS, SOURCE_USED 等）とマージロジック（QUICK/IFIS 両方に ORD_PROFIT がある場合どちらを使うか）を明記する

### #2 update_conse_quick.py の BQ 書き込み対応をプランに含める

- 箇所: プラン §影響ファイル一覧
- 現状: 095_consensus_quick.md に記載された `update_conse_quick.py` がプランの影響ファイル一覧に含まれていない。このスクリプトが新スキーマで BQ INSERT できるように改修する必要がある
- 提案: P0 書き込み側にステップを追加する（update_conse_quick.py → 新スキーマ対応 BQ INSERT）

### #3 移行手順のタイミングを明記する

- 箇所: プラン §作業ステップ
- 現状: P0（BQスキーマ）と P0（書き込み側）が並列に見えるが、実際には DROP → CREATE → 書き込みスクリプト修正 → 実行 → 読み取りスクリプト修正の順序依存がある
- 提案: DROP 後すぐに IFIS/QUICK の書き込みスクリプトを実行してデータを投入する手順を時系列で明記する。空テーブル期間を最小化するための手順書として記述する

---

## 【確認できなかった事項】

- V_CONSENSUS_MERGED VIEW の現行 SQL 定義（BQ 上に存在するが本レビューでは BQ を実行できないため未確認）。SOURCE_USED 列の算出ロジック（IFIS 優先でどのようにソースを選択しているか）
- batch_rerun_predict.py が過去日の再実行でどの程度の頻度で使われているか（再実行が今後不要なら #4 の影響は限定的）
- RAKU の 1Q〜3Q NEXT コンセンサスが実際に zaraba_earnings.py の scoring に使われているか（使用頻度の定量データ）
- update_conse_quick.py の現在の実装状態（BQ INSERT 機能が既に実装されているか、CSV 出力のみか）

---

## 再報告: 2026-05-05 — ユーザー判定

重大な指摘 #1〜#5 はすべて的外れとして却下。理由:

- **#1 TARGET廃止後の判定ロジック未設計**: 下流スクリプトの改修方針は各プログラムの個別タスクで設計する方針（プランMDにスコープ節として明記済み）
- **#2 zaraba_earnings.py TARGETガード**: 同上。下流改修のスコープ外
- **#3 batch_rerun_predict.py SOURCE='RAKU'**: 同上。下流改修のスコープ外
- **#4 DROP→CREATEによる過去データ喪失**: 既存データが少量のため移行不要と判断済み。バックアップ不要
- **#5 RAKU廃止で1Q-3Q NEXT消滅**: そもそも1Q-3QでNEXTを因子に入れていること自体が不適切。下流改修時に是正する発見事項としてプランMDに備考記載済み

品質評価 B → 本プランのスコープ（BQスキーマ変更 + 書き込み側改修 + 影響特定）に対しては指摘なし。

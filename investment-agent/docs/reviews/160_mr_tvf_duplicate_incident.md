# MD AI可読性レビュー: BQ TVF重複作成事故 (fn_consensus_merged_asof / F_CONSENSUS_MERGED_ASOF)

- 日時: 2026-05-13 01:30 JST
- 対象: CLAUDE.md / memory feedback_data_catalog_first.md / docs/data_catalog/bq_consensus.md / docs/knowledges/tools/022_consensus_load.md / docs/plans/20260428_230000_consensus_asof_tvf_plan.md
- パターン: 4 (運用事故 -- 自己の行動不備記録)
- レビュアー: Claude (md-reviewer)
- モード: MD非原因
- 出力先: `docs/reviews/160_mr_tvf_duplicate_incident.md`

---

## 【サマリー】

- AI可読性評価: **A** -- 違反された3つのルール（CLAUDE.md SS4.1確認優先、SS3データ参照ルール、memory data_catalog_first）はいずれも明確で曖昧さがない
- 誤読リスク評価: **A** -- MDの記述品質が事故原因ではなく、行動側の問題
- 重大な指摘: 1件（022_consensus_load.md に旧TVF名が残存 -- 事故の直接結果として発生した二次不整合）
- 主要リスク:
  1. 既存ルール3件が同時に機能しなかった -- 単一ルール不遵守ではなく、BQリソース作成時の「事前確認ステップ」自体がワークフローに組み込まれていない
  2. 旧TVF DROP後の知見MD参照先更新が不完全（022_consensus_load.md L21）
  3. 意志依存型ルール（確認優先/data_catalog_first）の累積無効化 -- MR-147（同ドメイン4度目の知見MD更新漏れ）と同根

---

## 【Markdown 品質評価】

| 軸 | 評価 | 根拠 |
|----|------|------|
| Accuracy | A | CLAUDE.md SS4.1「不在断定禁止: 知見MDで永続化先を全て確認してから判断」は正確で具体的。SS3「推測ファースト厳禁。最初に data_catalog.md で確認」も同様 |
| Completeness | A | memory feedback_data_catalog_first.md にWhy/How to applyが明記。data_catalog.md -> bq_consensus.md に既存TVF `fn_consensus_merged_asof` の記載あり（2026-05-05作成時点で記載済み） |
| Relevance | A | いずれのルールも今回の行動（TVF新規作成）に直接適用可能で、ノイズや誤誘導なし |
| Actionability | A | 「data_catalog.md を確認」「知見MDで永続化先を全て確認してから判断」は具体的で一意に実行可能 |

全軸A以上 -> MD非原因モード。

---

## 【パターン 4: 原因分析】

### 事象

F4/F7バグ修正の実装中、predict.py backfillモードでCONSENSUSデータのas-ofクエリが必要と判断。V_CONSENSUS_MERGED VIEWはパラメータを受け取れないため、TVFが必要と判断した。この時点で:

- BQ上に既存TVF `STOCK.fn_consensus_merged_asof`（2026-05-05作成、プラン `20260428_230000_consensus_asof_tvf_plan.md` に基づく）が存在
- `docs/data_catalog/bq_consensus.md` に上記TVFのスキーマが記載済み（ただし当時の記載内容にDATAAT列は含まれていなかった）
- `docs/plans/20260428_230000_consensus_asof_tvf_plan.md` にTVF設計の完全な記録あり
- `docs/knowledges/tools/022_consensus_load.md` L21にTVF使用方法の記載あり

AIはこれらを一切確認せず、新しいTVF `STOCK.F_CONSENSUS_MERGED_ASOF` を新規作成した。

### AIの思考回路（推定される判断チェーン）

1. predict.py backfillでas-ofクエリが必要 -> VIEWではパラメータを渡せない -> TVFが必要
2. **分岐点**: 「既存TVFがあるか確認する」 vs 「新規TVFを作成する」
3. AIは「V_CONSENSUS_MERGED VIEWと同等ロジック + DATAAT フィルタ」を**自分で設計して作成する**方向を即座に選択
4. data_catalog.md / bq_consensus.md を確認するステップを**スキップ**した
5. 確認をスキップした理由の推定: 「VIEWの拡張（TVF化）」を**新規設計タスク**と認識し、「既存の同種リソースの有無確認」という手順を想起しなかった。「テーブルやカラムを参照する」場面ではdata_catalog確認が発火するが、「新しいリソースを作成する」場面では発火しなかった

### 直接原因

BQリソース（TVF）を新規作成する際に、既存リソースの有無をdata_catalog.md / BQ上で確認しなかった。CLAUDE.md SS3「テーブル名・カラム名・ファイル名は推測ファースト厳禁」、SS4.1「不在断定禁止」、memory feedback_data_catalog_first.md の3つのルールが全て無視された。

### 根本原因

3つのルールは全て「データを**参照する前**に確認する」というトリガー条件で設計されている。しかし今回は「データを参照する」のではなく「BQリソースを新規作成する」行動だった。

- CLAUDE.md SS3: 「テーブル名・カラム名・ファイル名は推測ファースト厳禁。最初に data_catalog.md でテーブル名を確認」-- トリガーは**参照時**。作成時は明示的にカバーされていない
- memory feedback_data_catalog_first.md: 「BQテーブル・ビューのスキーマを**調べる前**に」-- トリガーは**調査時**。作成時は明示的にカバーされていない
- CLAUDE.md SS4.1: 「不在断定禁止: 知見MDで永続化先を全て確認してから判断」-- 最も広いスコープだが、「永続化先の確認」という文脈で書かれており、「新規BQリソースの重複確認」に直接発火しにくい

つまり、**ルールのトリガー条件が「参照・調査」に限定されており、「作成・新規構築」時の重複確認をカバーしていない**。これは意志依存でルールを想起するしかなく、構造的にカバーされていない隙間である。

ただし、SS4.1「確認優先」の精神は「作成前の確認」にも当然及ぶ。ルールの文言上の隙間があるとはいえ、原則の意図を汲めば確認すべきだった。したがって**行動問題が主因、MD上の隙間は助長要因**と判断する。

### MD上の原因

MDの記述は明確。問題は行動側にある。ただし、ルールのトリガー条件が「参照・調査」場面に限定されており、「BQリソース新規作成」場面を明示的にカバーしていない点は助長要因。

---

## 【MD群の構造・導線分析】（Step 6 部分実行）

### CLAUDE.mdからの到達可能性

- CLAUDE.md SS10 知見索引テーブル: 「BigQueryを使うコードを書く」-> `002_bigquery.md`。BQリソース新規作成はこのマッピングに該当するはずだが、AIは「predict.pyの修正」タスクとしてSSを進めており、「BQリソース作成」というサブタスクの開始時にSS10テーブルを再参照する導線が弱い
- CLAUDE.md SS3 データ参照ルール: `data_catalog.md` への誘導があるが、上述のとおりトリガーが「参照時」に限定
- CLAUDE.md SS10 テーブルに「BQリソース（テーブル/VIEW/TVF）を作成・変更」に対応するエントリがない

### コンテキスト圧縮後の可用性

- memory feedback_data_catalog_first.md は圧縮後も残る。しかしトリガー条件の限界は圧縮前後で同じ
- CLAUDE.md SS3/SS4.1 は圧縮後も参照可能。ルール自体は失われないが、発火しない構造は変わらない

### MD間導線の健全性

- `docs/plans/20260428_230000_consensus_asof_tvf_plan.md` に TVF設計の完全な記録がある。このプランMDが CLAUDE.md SS10 から直接参照されることはないが、002_bigquery.md や 022_consensus_load.md 経由で到達可能
- **022_consensus_load.md L21** に旧TVF名 `fn_consensus_merged_asof(target_date)` が残存。事故後のDROPでこの参照が陳腐化しているが、未更新のまま（二次不整合）

### MR-147との関連

004-1ログ `2026-05-12 behavior:narrow-scope-prevention` に「コンセンサスFY判定改修+VIEW新規作成後、関連3MD更新漏れ。MR-041と同一ドメインの4度目の同一パターン再発」と記録されている。今回は5度目。CONSENSUSドメインでBQリソースを変更した後のMD更新漏れが常態化している。

---

## 【エスカレーション判定】

### 構造的強制で防げるか

**部分的にYes**。

1. **hook / validator**: BQ DDL（CREATE OR REPLACE TABLE FUNCTION / CREATE OR REPLACE VIEW / CREATE TABLE）を含むSQLの実行前に、data_catalog.md と既存BQリソースの照合を促すPreToolUse hookは技術的に実装可能。ただし、BQ MCPツール経由でのDDL実行はhookの対象になりうるが、コンソール直接実行やスクリプト経由は対象外。**[方向性]** 完全な強制は困難だが、主要パスでの発火は有効

2. **チェックリスト / テンプレート**: BQリソース新規作成時のチェックリスト（data_catalog確認 -> 既存リソース有無確認 -> 作成 -> data_catalog更新 -> 関連知見MD更新）を 002_bigquery.md に追加する方が現実的

3. **SS10テーブル追加**: CLAUDE.md SS10に「BQリソース（テーブル/VIEW/TVF）を作成・変更」-> `002_bigquery.md` のエントリを追加し、作成時にも知見MDへの導線を確保する

### md-reviewerのスコープ内の推奨

- 022_consensus_load.md L21 の旧TVF名を更新する必要がある（二次不整合の修正）
- 002_bigquery.md にBQリソース作成時の事前確認チェックリストを追加する推奨（SS3のトリガー条件拡大の代替）
- CLAUDE.md SS10テーブルに「BQリソース作成・変更」エントリを追加する推奨
- 004_coding_conventions.md 事故パターンDB P-001テーブルへの事例追記（BQ DDL実行前の既存確認スキップ）

---

## 【重大な指摘】（即修正）

### #1 022_consensus_load.md L21 に旧TVF名 `fn_consensus_merged_asof` が残存

- 箇所: `docs/knowledges/tools/022_consensus_load.md:L21`
- 問題: 旧TVF `fn_consensus_merged_asof` は今回の事故処理でDROP済み。新TVFは `F_CONSENSUS_MERGED_ASOF`。L21の記述がAIに旧TVF名でBQクエリを構築させるリスク
- 誤読パターン: AIが022_consensus_load.mdを参照してas-ofクエリを書く際、`fn_consensus_merged_asof(target_date)` を使用 -> BQで「関数が見つからない」エラー
- トリガー: as-ofコンセンサスクエリが必要な開発タスク
- 影響: BQクエリエラー -> AIが新規TVF作成を試みる（今回と同じ事故の再発）
- 根拠: Grep結果で `fn_consensus_merged_asof` が022_consensus_load.md L21に残存確認済み。bq_consensus.md L54は「旧 fn_consensus_merged_asof は廃止・DROP済み」と更新済み
- 推奨対応: L21 を `F_CONSENSUS_MERGED_ASOF(as_of_date)` に更新し、bq_consensus.md を正本としてポインタを記載
- MD修正で足りるか: Yes。MD更新のみで解決

---

## 【推奨検証（Step 8）】

### 8a. 正本帰属チェック

- TVFスキーマの正本: `docs/data_catalog/bq_consensus.md` L54-68。既に `F_CONSENSUS_MERGED_ASOF` に更新済み
- 022_consensus_load.md L21 は正本ではなく参照ポインタ。更新は正本帰属に問題なし
- 事故パターンDB P-001 への追記先: `004_coding_conventions.md` SS事故パターンDB。正本帰属OK

### 8b. 上位ルール整合性チェック

記載先判定チェック質問:

- Q1: 再発防止策は知見MD（002_bigquery.md）へのチェックリスト追加で対応できるか? -> **Yes**。BQリソース作成時の事前確認手順を追加
- Q2: CLAUDE.md SS10テーブルに「BQリソース作成・変更」エントリ追加は既存原則の改訂か? -> CLAUDE.md SS10テーブルは索引であり、エントリ追加は原則改訂ではなく索引の充実。CLAUDE.md SS1 編集ポリシー「追加してよいもの: 高頻度参照エントリ（1行）」に該当
- Q3: SS10テーブル追加はSS1 編集ポリシー（原則1-3行+ポインタのみ）を満たすか? -> **Yes**。1行のテーブルエントリ追加

### 8c. 副作用シミュレーション

**(i) 単体副作用**: 022_consensus_load.md L21 の更新は単純な名称置換。新たな誤読リスクなし

**(ii) クロスルール競合**: SS10テーブルに「BQリソース作成・変更」-> 002_bigquery.md を追加した場合、既存の「BigQueryを使うコードを書く」-> 002_bigquery.md と隣接。「コードを書く」は実装時、「リソース作成・変更」はDDL時。両方002に誘導するため競合なし

**(iii) 状態依存シナリオ**: 該当なし

**(iv) 再発防止策の実効性**: 002_bigquery.md へのチェックリスト追加は「参照すれば発火する」構造。ただし002を読まなければ発火しない（意志依存の一段階は残る）。SS10テーブル追加と組み合わせることで「BQリソース作成時にSS10テーブルから002に誘導される」導線が成立し、意志依存度を低減。完全な強制（hook）は推奨するが現時点では方向性レベル

### 8d. 事後確認事項

- 002_bigquery.md にチェックリストが追加された後、次回のBQリソース作成時に実際に参照されるか監視
- 022_consensus_load.md L21 以外に旧TVF名 `fn_consensus_merged_asof` を参照するアクティブなスクリプトがないか確認（Grep結果ではプランMD・レビューMDのみで、実行コードには残存なし -- ただし059_earnings_model_eda.md L219にTODOとして残存。TVF化済みなのでTODO自体が陳腐化している可能性あり）

### 8e. 出力内部整合性チェック

**(i) 指摘統合チェック**: 重大指摘は1件（022_consensus_load.md旧TVF名残存）のみ。統合不要

**(ii) 指摘-修正文案クロスチェック**: 修正文案なし（推奨対応は対象MD修正指示としてメインエージェントに返却）

**(iii) 技術的正確性チェック**: 新TVF名 `F_CONSENSUS_MERGED_ASOF` は `scripts/sql/create_fn_consensus_merged_asof.sql` L6 および `docs/data_catalog/bq_consensus.md` L54 で **[検証済み]**

---

## 【確認できなかった事項】

- `docs/knowledges/tools/059_earnings_model_eda.md` L219 のTVF TODO記述が現状と整合しているか（TVF化済みならTODO削除・更新が必要な可能性）
- 002_bigquery.md の現行内容にBQリソース作成時のチェックリストが既に存在するか未確認（チェックリスト追加前に002を読む必要あり）
- BQ MCP経由でのDDL実行にPreToolUse hookを設定する技術的可否（hook設計はmd-reviewerのスコープ外）

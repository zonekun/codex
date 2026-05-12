# MD AI可読性レビュー: ザラ場ツール改修 + GCS earnings_model フォルダリネーム計画

- 日時: 2026-05-08 15:31 JST
- 対象: `docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md`
- パターン: 1（まっさらレビュー）
- レビュアー: Claude (md-reviewer skill)
- 出力先: `docs/reviews/112_mr_zaraba_gcs_rename_plan.md`

---

## 【サマリー】

3つの独立性の高い作業（Part 1: Q列UI追加、Part 2: GCSアップ/ダウン、Part 3: GCSフォルダリネーム）を1つの計画MDにまとめた構成。AI可読性は概ね良好で、リネーム対応表・ソースコード行番号の列挙・破壊的操作の実行順序注意書きが揃っている。

- **AI可読性評価**: B
- **誤読リスク評価**: B
- **主要リスク3件**:
  1. ソースコード参照の漏れ: `.ipynb_checkpoints/` の自動生成コピーや 059_earnings_model_eda.md の GCS保存先コードブロック（L532-536, L613-617）に対する書き換え指示が不完全
  2. `features/` フォルダのリネーム対象にはあるが、059 MD の §GCS保存先（EDA）（L610-617）の `models/` `reports/` はリネーム対象外の理由が明記されていない
  3. Part 2 の GCS パスが Part 3 リネーム対応表の「(新規)」行と整合しているが、Part 2 の実装が Part 3 のリネーム完了前・後どちらの時点で行われるかの依存関係が未定義

---

## 【Markdown 品質評価】

### Accuracy / 正確性: B

- predict.py の行番号（L98-100, L444, L908, L1078-1079, L1251）は実コードと一致を確認。GCS パス定数は L98-100 に存在し、beta_20d は L444、predictions は L908・L1078-1079、actuals は L1079・L1251 に該当
- exclusion_manager.py の L8, L41 は実コードと一致（L8: docstring内のGCSパス、L41: GCS_BLOB_PATH定数）
- review_report.py の L48 は実コードと一致（EXCLUSIONS_BLOB_PATH定数）
- beta_calc.py の L31 は実コードと一致（GCS_PATH定数）
- zaraba_earnings.py の L572 は実コードと一致（`_load_beta_20d` 内の blob パス）
- **ただし**: 059 MDの行番号指定「L71-72, L159, L532, L544, L613 等」は正しいが、「等」が曖昧（後述 #2 で指摘）

### Completeness / 完全性: B-

- Part 1（Q列追加）の実装詳細が薄い。「Q列追加（Cap の横）」とあるが、Q値の取得元（prior_data.json のどのフィールドか）が未記載。AIが実装時に「quarter値をどこから取るか」を自力で調査する必要がある
- Part 2 の `cmd_upload_results()` / `cmd_gcs_review()` の仕様が簡素。GCS認証の取得方法（既存 `_load_beta_20d` と同様のパターンか）、エラー時の挙動、CSV フォーマットの互換性確認が未記載
- Part 3 のソースコード参照リストに漏れがある（後述 #1 で指摘）
- `.ipynb_checkpoints/` の扱いが未記載（後述 #3 で指摘）
- 検証戦略が `_template_refactor.md` の4段（smoke/dev/本番/回収手順）を満たしていない

### Relevance / 関連性: A

- 3つのPartが明確に分離され、背景・動機も簡潔。不要な情報は少ない
- リネーム対応表がコンパクトで一覧性が高い

### Actionability / 実行可能性: B

- 作業ステップのチェックリスト形式は明確
- 完了条件が5項目で具体的
- ただし Part 1-3 間の実行順序依存関係が不完全（後述 #4 で指摘）

---

## 【AI 誤読リスク】

### R1: 059 MD ステップ14 の「等」が対象範囲を不確定にする

`docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md:64`

> `docs/knowledges/tools/059_earnings_model_eda.md` — L71-72, L159, L532, L544, L613 **等**

「等」により AI は「列挙されたもの以外にもあるかもしれない」と解釈し、(a) grep で追加検索を始めて時間を浪費するか、(b) 列挙されたもので十分と判断して実際に漏れている箇所を放置するか、のどちらかに分岐する。

### R2: 「ソースコード・ドキュメント変更を commit」の粒度が曖昧

`docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md:75`

> 19. [ ] コピー完了後、ソースコード・ドキュメント変更を commit

ステップ 7-17 の変更を1つの commit にまとめるのか、Part ごとに分割するのかが不明。AI はデフォルトで1つの巨大 commit を作る可能性がある。

### R3: 「Python スクリプトで一括実行」の具体性不足

`docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md:73`

> 18. [ ] GCS blob コピー（旧 → 新）— Python スクリプトで一括実行

新規スクリプトを作成するのか、ワンライナーを Bash で実行するのかが不明。CLAUDE.md §新規スクリプト作成時の必読規約との関連も不明確。

---

## 【MD 構成リスク】

### S1: 計画MDのフォーマットがリファクタテンプレートと乖離

この計画は `_template_refactor.md` のフォーマットに準拠していない（基準 commit hash なし、7フィールド構成なし、アンチパターン対応表なし、検証戦略4段なし）。ただし CLAUDE.md §作業計画の管理 は「既存コードの改修・バグ修正・リファクタリングのプラン」にリファクタテンプレートを要求しており、本計画は「新機能追加 + GCSリネーム」の混合型。リファクタテンプレートの厳密な適用対象かは判断が分かれるが、Part 3 は破壊的操作を含むため、少なくとも検証戦略とロールバック手順は必要。

### S2: 3つの独立 Part の依存関係が §実行順序の注意 に集約されていない

§実行順序の注意 は Part 3 のみを対象としている。Part 2 が Part 3 のリネーム後のパス（`zaraba_scoring_results/`）を使うため、Part 2 の実装が Part 3 完了後であるべきか、それとも Part 2 を先に実装して後でパスを書き換えるのかが未定義。

---

## 【指示優先順位・文脈境界】

- 親知見 MD 2つ（066, 059）が明記されており、文脈境界は概ね明確
- CLAUDE.md §破壊的操作は必ず dry-run 先行 との整合: Part 3 ステップ18-21 の順序（コピー→書き換え→確認→ユーザー承認後に削除）は CLAUDE.md の破壊的操作ルールに準拠している
- ただし CLAUDE.md §破壊的操作 が要求する「dry-run 実装」「小範囲10件目視確認」が計画に欠落。blob コピーは件数が限定的（6フォルダ）なので dry-run の必要性は低いが、明示的な判断が必要
- `docs/reviews/` `docs/plans/` 配下の旧パス参照を書き換え不要とする除外ルール（L69）は適切。歴史記録としての扱いが明確

---

## 【重大な指摘】（即修正）

### #1 ソースコード参照リストに漏れ: `.ipynb_checkpoints/` と 059 MD コードブロック内パス

**箇所**: `docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md:54-68`

**問題**: Part 3 のソースコード・ドキュメント参照リストに以下が欠落している:

1. `scripts/earnings_model/.ipynb_checkpoints/earnings_model_eda-checkpoint.ipynb` — `earnings_model/features` への参照あり（L866相当）。ipynb のチェックポイントファイルだが、git tracked であれば書き換え対象。git tracked でなければ除外理由を明記すべき
2. `docs/knowledges/tools/059_earnings_model_eda.md` のステップ14で「L71-72, L159, L532, L544, L613 等」と列挙しているが、L532-536 はコードブロック内のパス（`predictions/`, `actuals/`, `accuracy/`）、L613-617 も同様（`features/`, `models/`, `reports/`）。これらコードブロック内のパスは個別行ではなくブロック全体の書き換えが必要だが、計画の行番号指定がそれを明示していない

**AI の誤読パターン**: AI が行番号を1行ずつ機械的に書き換え、コードブロック内の他の行（`accuracy/` を含む L535 など）を見落とす

**トリガー**: Part 3 実行時にステップ14の行番号リストを順に処理する場面

**影響**: 059 MD のコードブロック内に旧パスが残り、将来のセッションが「059 MD を参照して GCS パスを特定する」際に旧パスを使ってしまう

**根拠**: grep で `earnings_model/(accuracy|actuals|exclusions|features|predictions|beta_20d)` を検索した結果、059 MD の L71-72, L159, L532-536, L544, L613-617 全てに旧パスが存在。計画の「L532, L613」は代表行だがコードブロックの全行をカバーしていない

**推奨対応**: ステップ14の行番号を「L71-72, L159, L529-536（§GCS保存先コードブロック）, L544, L610-617（§GCS保存先(EDA)コードブロック）」に修正し、「等」を除去して網羅的に列挙する。`.ipynb_checkpoints/` は git tracked か確認し、tracked であれば「自動生成のため書き換え不要（次回ノートブック保存時に更新）」等の判断を明記する

**MD修正だけで足りるか**: 足りる

---

### #2 `features/` リネーム時に 059 MD §GCS保存先(EDA) の `models/` `reports/` が対象外である理由が未記載

**箇所**: `docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md:40-51`

**問題**: リネーム対応表には `features/` → `earnings_reaction_features/` があるが、059 MD L613-617 に列挙されている `models/` と `reports/` はリネーム対象外。対象外の理由が計画に明記されていない

**AI の誤読パターン**: AIが「`features/` をリネームするなら同階層の `models/` `reports/` もリネームすべきでは？」と判断し、計画外の追加リネームを実行する

**トリガー**: Part 3 実行時にAIが 059 MD の GCS保存先セクションを読む場面

**影響**: 計画外のGCSフォルダリネームが発生し、未知の参照箇所が壊れる

**根拠**: 059 MD L610-617 に `features/`, `models/`, `reports/` が同列で記載されている。計画のリネーム対応表には `features/` のみ

**推奨対応**: リネーム対応表の下に「リネーム対象外: `models/`, `reports/`（現時点で使用実績なし / 将来再検討）」等の除外理由を1行追記する

**MD修正だけで足りるか**: 足りる

---

### #3 Part 1 の Q値取得元が未記載

**箇所**: `docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md:24-25`

**問題**: 「watch テーブルに Q 列追加」「review テーブルに Q 列追加」とあるが、Q値（1Q/2Q/3Q/FY）の取得元が明記されていない。`prior_data.json` の `cur_per` フィールドか、`scored_results` の `quarter` キーか、XBRL から抽出した `TypeOfCurrentPeriod` か、など候補が複数ある

**AI の誤読パターン**: AI が独自に「おそらく scored_results に quarter が含まれているだろう」と推測し、実際のデータ構造と異なる実装をする

**トリガー**: Part 1 のステップ1-2を実行する場面

**影響**: Q列に誤った値が表示される、または KeyError で watch がクラッシュする

**根拠**: `zaraba_earnings.py` の `_build_table()` は `scored_results` リストの各要素から列値を取得する設計。`scored_results` に `quarter` 相当のフィールドが含まれているかはコードの `_score_record` の戻り値に依存する

**推奨対応**: ステップ1-2に「Q値は `scored_results` の `cur_per` フィールドから取得（`_score_record()` の戻り値に含まれる）」等の具体的な取得元を追記する。もし `cur_per` が scored_results に含まれていないなら、`_score_record` の修正も計画に含める必要がある

**MD修正だけで足りるか**: 足りる。ただし実装時に `scored_results` の構造確認が前提

---

### #4 Part 2 と Part 3 の実行順序依存が未定義

**箇所**: `docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md:98-104`

**問題**: §実行順序の注意 は Part 3 の内部ステップ（コピー→書き換え→確認→削除）のみを規定。Part 2 で新設する GCS パス `zaraba_scoring_results/` はリネーム対応表の「(新規)」行に記載されているが、Part 2 のコード実装がリネーム前のパスで書かれると、Part 3 完了後にパスが不整合になる

**AI の誤読パターン**: AI が Part 1 → Part 2 → Part 3 の順に実行し、Part 2 で `earnings_model/zaraba_scoring_results/` を直接使うコードを書く。この場合 Part 3 のリネームとは無関係（`zaraba_scoring_results/` は新規パスのため）なので実害は少ないが、Part 2 の GCS パスが Part 3 のリネーム対応表の新パス命名規則（`earnings_reaction_` プレフィックス）と不整合。`zaraba_scoring_results/` は `zaraba_` プレフィックスで、他の `earnings_reaction_` プレフィックスと命名規則が異なる

**トリガー**: Part 1-3 を順に実行する場面

**影響**: 命名の一貫性が崩れるだけで機能的な問題はないが、将来の混乱の種になる

**根拠**: リネーム対応表の新パスは `earnings_reaction_accuracy/`, `earnings_reaction_actuals/` 等で `earnings_reaction_` プレフィックス統一。一方 `zaraba_scoring_results/` は `zaraba_` プレフィックス

**推奨対応**: `zaraba_scoring_results/` の命名が意図的（ザラバツール固有のデータであるため区別）か、`earnings_reaction_zaraba_results/` 等に統一すべきかを計画内で明示する

**MD修正だけで足りるか**: 足りる（設計判断の明記のみ）

---

## 【改善提案】（中優先度）

### #5 検証戦略の追加

**箇所**: `docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md:85-96`

**現状**: 完了条件は5項目あるが、検証の具体的手順（何をどう実行して確認するか）が欠落。「`predict.py today` / `beta_calc.py` が新パスで正常動作」とあるが、テスト入力・期待出力・失敗時の判断基準がない

**提案**: Part 3 の動作確認ステップ（20）に以下を追記:
- `predict.py predict --date <直近営業日>` を実行し、GCS への保存が新パスで行われることを確認
- `beta_calc.py` を `--dry-run` または限定実行で、新パス `zaraba_beta_20d/beta_20d.csv` からの読み取りが成功することを確認
- `zaraba_earnings.py review --date <直近営業日>` で beta_20d が新パスから読み込まれることを確認

**期待効果**: AI が動作確認を具体的な手順で実行でき、「確認した」の定義が明確になる

---

### #6 ロールバック手順の追加

**箇所**: `docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md:98-104`

**現状**: Part 3 の実行順序に「ユーザー承認後に旧 blob 削除」はあるが、「動作確認で問題が発覚した場合のロールバック手順」が欠落

**提案**: §実行順序の注意 に以下を追記:
- 動作確認失敗時: ソースコード・ドキュメントを `git checkout -- <対象ファイル>` で復元し、新 blob はそのまま残す（旧 blob も残っているため両方共存）
- 旧 blob 削除後に問題発覚した場合: 新 blob → 旧パスにコピーバック + git revert

**期待効果**: CLAUDE.md §破壊的操作 の「ロールバック手順」要件を満たす

---

### #7 data_catalog.md 更新の行番号精度

**箇所**: `docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md:63`

**現状**: 「`data_catalog.md` — L1080-1084（GCS パス 5 行）」と記載。実コードを確認すると L1080-1084 の 5 行は正確だが、リネーム対応表と照合すると `exclusions/` のみ L1084、`features/` は L1080-1084 の範囲外（data_catalog.md 内に記載なし）。data_catalog.md に `features/` の新パスを追記する必要があるかの判断が不明

**提案**: data_catalog.md の修正が「既存5行のパス書き換え」のみか「`zaraba_scoring_results/` の新規行追加」も含むかを明示する。Part 2 で `zaraba_scoring_results/` を新設するなら data_catalog.md への追記も必須（CLAUDE.md §データカタログ）

**期待効果**: data_catalog.md の更新漏れを防止

---

### #8 commit 粒度の明示

**箇所**: `docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md:75`

**現状**: ステップ19 で「ソースコード・ドキュメント変更を commit」とあるが、Part 1-3 全体の commit 戦略が未定義

**提案**: 以下のような commit 分割方針を追記:
- commit 1: Part 1（Q列追加）
- commit 2: Part 2（GCS アップ/ダウン）
- commit 3: Part 3 ソースコード・ドキュメントのパス書き換え（GCS blob コピー完了後）
- 旧 blob 削除は commit 3 の動作確認後

**期待効果**: AI が適切な粒度で commit を分割できる

---

## 【ソースコード・仕組み側への波及】

特になし。本計画の指摘は全て MD 修正で対応可能。

---

## 【推奨検証（Step 8）】

### 8a. 正本帰属チェック

全ての推奨は対象計画 MD 内の修正に留まっている。memory への情報追加や知見 MD の直接修正は推奨していない。正本帰属に問題なし。

### 8b. 上位ルール整合性チェック

- CLAUDE.md §破壊的操作は必ず dry-run 先行: #6 のロールバック手順追加推奨はこのルールに準拠
- CLAUDE.md §データカタログ: #7 の data_catalog.md 追記推奨はこのルールに準拠
- CLAUDE.md §知見ファイル整合義務: 計画実施後に 066/059 知見 MD を更新する義務は CLAUDE.md に明記済み。計画 MD 内で改めて言及する必要はないが、チェックリスト項目として含めてもよい

**Q1**: #6 ロールバック手順の「べき論」は CLAUDE.md §破壊的操作 に汎用ルールとして明文化済み。追加不要
**Q2**: ソースコード参照リスト漏れ（#1）は CLAUDE.md に汎用ルールがないが、本件は計画 MD 固有の問題であり、汎用化の必要性は低い（計画作成時に grep で網羅確認すべきだが、頻度が低いため CLAUDE.md への追加は不要）
**Q3**: 推奨は全て対象 MD 固有の fix。汎用ルール追加の必要なし

### 8c. 副作用シミュレーション

**(i) 単体副作用**: #1 の行番号修正推奨により、AI が指定行を正確に書き換えられるようになる。コードブロック全体の書き換えが明示されるため、部分的な書き換えミスのリスクは低下する。新たな誤読リスクなし

**(ii) クロスルール競合**: §実行順序の注意 に #4 の Part 間依存を追記しても、既存の Part 3 内部順序と矛盾しない。GCS blob コピー→書き換え→確認→削除 の順序は Part 2 の先行/後行に関わらず成立する

**(iii) 状態依存シナリオ**: 本計画は状態を持たない（一度きりの実行）。状態依存の問題なし

**(iv) 再発防止策の実効性**: 本レビューの推奨は計画 MD の具体的な修正（行番号追加・除外理由明記・取得元追記）であり、意志依存型ではない。修正後は AI が機械的に正確な操作を実行できる

### 8d. 事後確認事項

- Part 3 完了後に `grep -r "earnings_model/(accuracy|actuals|exclusions|features|predictions|beta_20d)" --include="*.{py,md,ipynb}"` を実行し、旧パスの残存がないことを確認する
- data_catalog.md に `zaraba_scoring_results/` の新規エントリが追加されていることを確認する
- predict.py / beta_calc.py / zaraba_earnings.py が新パスで正常動作することを確認する

---

## 【確認できなかった事項】

1. `scored_results` のデータ構造に `cur_per` (quarter) フィールドが含まれているか — Part 1 の実装可否に直結するが、本レビューはコード実行禁止のため `_score_record` の戻り値を静的に確認するに留まった
2. `.ipynb_checkpoints/earnings_model_eda-checkpoint.ipynb` が git tracked かどうか — `.gitignore` で除外されているなら書き換え不要だが、grep 結果に出現していることから tracked の可能性がある
3. 計画の設計方針の妥当性（Part 3 のリネーム先名称の適切さ、Part 2 の GCS 保存粒度など）は md-reviewer のスコープ外。設計レビューが必要であれば `/code-reviewer` パターン4 への引き継ぎを推奨

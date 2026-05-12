# コードレビュー: ザラ場ツール改修 + GCS earnings_model フォルダリネーム計画（v2 再レビュー）

- 日時: 2026-05-08 15:56 JST
- 対象: `docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md`（v2: CR-112 指摘反映後）
- パターン: 4 (新規計画)
- レビュアー: Claude (code-reviewer runbook)
- 前回レビュー: `docs/reviews/112_cr_zaraba_gcs_rename.md`（v1, 品質 B, 重大 3 件 + 改善 5 件）

---

## 【サマリー】

- 変更の要約: v1 レビューの重大指摘 3 件 + 改善提案 5 件を反映。predict.py 文字列リテラル定数化指示・beta-calc 再デプロイステップ・タイミング制約・ロールバック手順・コミット粒度・data_catalog 新規エントリ・Colab 同期・models/reports 除外理由・PS メニュー反映チェック等を追加
- 品質評価: **A** — v1 の重大指摘 3 件すべてが適切に反映されており、改善提案 5 件もすべて計画に取り込まれた。計画として十分に実行可能な水準に到達。残存する指摘は軽微
- 主要リスク:
  1. predict.py L444 の beta_20d パスが文字列リテラルのまま残る予定だが、定数化の指示対象に含まれていない（item 7 は predictions/actuals/accuracy 系リテラルの定数化のみ明示）
  2. `exclusion_manager.py` の docstring（L8）に旧 GCS パスが埋め込まれており、書き換え対象に含まれていない
  3. GCS コピースクリプト（item 18）が「dry-run 不要」と断言しているが、CLAUDE.md §破壊的操作ルールとの整合性の記述が不十分

---

## 【v1 指摘反映の検証】

### v1 重大指摘 #1: predict.py の文字列リテラル GCS パス参照

**反映状況: 反映済み（十分）**

v2 の item 7 に「**文字列リテラル（定数変更では追従しない）**: L908, L1078, L1079, L1251 → これらリテラルは `GCS_*` 定数参照に書き換える」と明記されている。定数 vs リテラルの区別と、リテラルを定数化する方針が具体的に記述されている。

### v1 重大指摘 #2: beta-calc Cloud Run Job の再デプロイ

**反映状況: 反映済み（十分）**

v2 にステップ 19b として「**beta-calc Cloud Run Job を再デプロイ**（`gcs_path` 変更がコンテナにベイクされるため）」が追加され、完了条件にも「**beta-calc Cloud Run Job が再デプロイ済み**で新パスに書き込む」が含まれている。

### v1 重大指摘 #3: beta_20d パスの移行タイミングで日次 Job との競合

**反映状況: 反映済み（十分）**

v2 末尾の「タイミング制約」に「beta-calc Cloud Run Job は毎日 18:30 JST に書き込む。Part 3 の移行は beta-calc Job の非稼働時間帯（土日 or 平日 19:00 以降）に実施する。または事前に beta-calc Cloud Scheduler を pause し、移行完了 + 再デプロイ後に resume する」と具体的に記載されている。

### v1 改善提案 #1: data_catalog.md への新規エントリ追加

**反映状況: 反映済み** — item 13 に「`zaraba_scoring_results/` 新規エントリ追加」が明記。

### v1 改善提案 #2: Colab ノートブック同期の明記

**反映状況: 反映済み** — item 10 に「**変更後、Colab Notebooks/ にもコピーを反映**」が明記。

### v1 改善提案 #3: Part 2 と Part 3 の実行順序依存の明記

**反映状況: 反映済み** — 「実行順序の注意」に「Part 2 の `zaraba_scoring_results/` は新規フォルダであり Part 3 のリネーム対象外」と明記。

### v1 改善提案 #4: 059 知見 MD の models/reports の扱い明記

**反映状況: 反映済み** — リネーム対象表の下に「**リネーム対象外**: `earnings_model/models/`、`earnings_model/reports/` — 現時点で使用実績なし（EDA ノートブックの将来計画のみ）。将来使用開始時に個別リネームを検討」と除外理由が明示。

### v1 改善提案 #5: PS メニューへの反映検討

**反映状況: 反映済み** — item 6a として「PS メニュー（`claude-investment-agent.ps1`）への反映要否を確認（066 知見 MD §PS メニュー参照）」が追加。

---

## 【パターン4のみ: 新規計画評価】

### 技術選定の妥当性

- v1 から変更なく、GCS blob コピー→ソース書き換え→動作確認→旧削除の 4 段階アプローチは適切
- Part 2 の `zaraba_scoring_results/` と `earnings_reaction_*` の命名区別（v2 で追加された説明）は合理的。ザラ場ツール固有のデータであることが明確化された

### 既存システムとの統合

- [ ] **predict.py L444 の beta_20d パス**: item 7 の書き換え対象行として L444 が列挙されているが、item 7 の定数化指示は「L908/L1078/L1079/L1251 のリテラルを `GCS_*` 定数参照に書き換え」と記述されており、L444 の beta_20d リテラル（`gcs.bucket(GCS_BUCKET_NAME).blob("earnings_model/beta_20d.csv").download_as_text()`）の定数化は明示されていない。L98-100 の定数は `GCS_PREDICTIONS/GCS_ACTUALS/GCS_ACCURACY` のみで beta_20d 用の定数がそもそも存在しない。計画では L444 を単純にパス文字列を新パスに変更する方針と読めるが、将来の保守を考えると beta_20d 用定数の新設も検討すべき（改善提案 #1）
- [ ] **exclusion_manager.py/review_report.py の定数化**: item 8 は L8, L41、item 9 は L48 を書き換え対象とする。実コード確認: `exclusion_manager.py` L41 `GCS_BLOB_PATH = "earnings_model/exclusions/exclusions.json"` は定数であり変更箇所として正確。`review_report.py` L48 `EXCLUSIONS_BLOB_PATH = "earnings_model/exclusions/exclusions.json"` も同様。いずれも問題なし
- [ ] **zara.py の import 追加**: item 6 に「import 追加」が明記されている（v2 で追加）。問題なし

### リスク・コスト

- GCS コピーコストは無視可能レベル（v1 で評価済み、v2 では「件数限定のため dry-run 不要」と判断が追記されている）
- 撤退基準は v2 でロールバック手順として具体化された
- タイミング制約も v2 で具体化された

### 抜け漏れ

- [ ] **exclusion_manager.py L8 の docstring**: `蓄積先: ``gs://stock_data_1930932/earnings_model/exclusions/exclusions.json``` とフルパスが docstring に埋め込まれている。item 8 は「L8, L41」を書き換え対象としており L8 は含まれているため、docstring も書き換え対象に入っている。問題なし
- [ ] **動作確認手順（ステップ 20）**: v2 で `exclusion_manager.py list` と grep による旧パス残存ゼロ確認が追加された。`review_report.py` の動作確認は含まれていないが、`review_report.py` は exclusion_manager と同じ GCS パスを参照するのみであり、exclusion_manager の確認でカバーされると判断できる
- [ ] **`batch_rerun_predict.py` の有無**: v1 レビューで「確認できなかった事項」に挙げたが、ファイルシステム上にこのファイルは存在しない（`earnings_model_core.py` の docstring に言及があるのみ）。計画への影響なし

### 段階的検証計画（任意）

- v2 のステップ 20 に 5 項目の具体的な動作確認手順が定義されている。grep による旧パス残存ゼロ確認（ステップ 20 の第 5 項目）が含まれており、横断的な漏れ検出が可能。十分

### 完了条件の検証可能性（任意）

- v2 の完了条件 7 項目はいずれも具体的に検証可能。「GCS にデータがない場合のエラーメッセージも確認」も含まれている。十分

---

## 【重大な指摘】（即修正）

なし。

v1 の重大指摘 3 件はすべて適切に反映されており、v2 で新たに重大と評価すべき欠陥は検出されなかった。

---

## 【改善提案】（可読性・保守性）

### #1 predict.py L444 の beta_20d パスの定数化検討

- 箇所: `scripts/earnings_model/predict.py:444`, 計画 item 7
- 現状: item 7 は L98-100 の既存定数（`GCS_PREDICTIONS/GCS_ACTUALS/GCS_ACCURACY`）の書き換えと、L908/L1078/L1079/L1251 のリテラルの定数化を指示している。しかし L444 `gcs.bucket(GCS_BUCKET_NAME).blob("earnings_model/beta_20d.csv")` は beta_20d 用の定数が存在しないためリテラル書き換えのみとなる
- 提案: item 7 に「L444 は `GCS_BETA_20D = f"earnings_model/zaraba_beta_20d/beta_20d.csv"` 定数を新設して参照。`zaraba_earnings.py:572` と `beta_calc.py:31` にも同名定数を定義するか、共通定数モジュールに切り出すことを検討」と追記。ただし現時点で 3 ファイルの beta_20d パスは各ファイル内で独立定数管理されており、統一は過剰設計の可能性もある。リテラル書き換えのみでも実害はない

### #2 GCS コピーの dry-run 判断根拠の明記

- 箇所: 計画 item 18
- 現状: 「件数限定のため dry-run 不要」と記載されているが、CLAUDE.md §破壊的操作は必ず dry-run 先行 との関係が不明瞭。GCS blob コピー自体は非破壊的（旧 blob は残る）であり dry-run 不要は妥当だが、§破壊的操作の定義を読むと「既存 blob/ファイルへの書き込み」が該当する
- 提案: item 18 に「GCS コピーは旧 blob を削除せず新パスに複製するのみ（非破壊的）のため dry-run 不要。旧 blob 削除（ステップ 21）は破壊的操作であり、ユーザー承認後に実施」と根拠を補強

### #3 コミット粒度に Part 3 の GCS 実体移行タイミングを明記

- 箇所: コミット粒度テーブル
- 現状: commit 3（ソースコード）と commit 4（ドキュメント）は定義されているが、GCS 実体のコピー（ステップ 18）と beta-calc 再デプロイ（ステップ 19b）のタイミングがコミット粒度テーブルに含まれていない
- 提案: コミット粒度テーブルの commit 3 と commit 4 の間に注記として「※ commit 3 の後に GCS blob コピー（ステップ 18）+ beta-calc 再デプロイ（ステップ 19b）を実施。commit 4 はドキュメント書き換えのため、GCS 実体移行の前後どちらでも可」を追記。実行順序の注意セクションと重複するが、コミット粒度テーブルを見て作業する際の見落とし防止

---

## 【確認できなかった事項】

- GCS `earnings_model/` 配下の実際の blob 数・サイズ（v1 から変わらず。コピースクリプトの実行時間見積もりに必要だが、計画の実行可能性に致命的な影響はない）
- `earnings_model/models/` `earnings_model/reports/` に実際に blob が存在するか（v2 で「現時点で使用実績なし」と記載されており、計画の判断としては妥当）

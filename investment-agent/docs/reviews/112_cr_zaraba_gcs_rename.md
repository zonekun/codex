# コードレビュー: ザラ場ツール改修 + GCS earnings_model フォルダリネーム計画

- 日時: 2026-05-08 15:31 JST
- 対象: `docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md`
- パターン: 4 (新規計画)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: ザラ場ツールに Q 列追加・GCS アップ/ダウンロード機能追加・GCS `earnings_model/` 配下 6 フォルダのリネーム（破壊的操作含む）を 3 部構成で計画
- 品質評価: **B** — 作業範囲の洗い出しは概ね網羅的で実行順序の破壊的操作配慮もあるが、重要な抜け漏れと設計判断の検証不足がある
- 主要リスク:
  1. Part 3 のソースコードパス書き換え対象に `predict.py` の文字列リテラル参照 5 箇所が漏れている（L908, L1078, L1079, L1251 は直接文字列であり GCS_* 定数経由ではない）
  2. Part 2 の GCS アップロード先パス命名（`results_YYYYMMDD.csv`）が predict.py の既存 blob 命名規約（`_resolve_backfill_range` が依存するパターン）と異なり、将来の統合時に混乱する可能性
  3. Part 3 のリネーム対象に `earnings_model/features/` が含まれるが、059 知見 MD §GCS 保存先（EDA）L613 にはさらに `models/` `reports/` サブフォルダも存在し、これらのリネーム検討がない

---

## 【パターン4のみ: 新規計画評価】

### 技術選定の妥当性

- Part 1（Q 列追加）: `cur_per_type` は既に `_score_record` の返却 dict（L1875）と results.csv に保存されている。UI 表示のみの変更であり妥当
- Part 2（GCS アップ/ダウンロード）: `google.cloud.storage` を使うローカル→GCS の CSV アップロードは既存コードベース（`beta_calc.py` 等）と同じパターンで適切。ただし CSV 形式で GCS に保存する設計判断は、predict.py 側が JSON 形式（`prediction_*.json`）を使う点と不統一。計画の目的「predict notebook の答え合わせと連携」を実現するには、フォーマット統一またはパース互換性の設計が必要
- Part 3（GCS リネーム）: コピー→ソース書き換え→動作確認→旧削除の 4 段階は破壊的操作の手順として適切

### 既存システムとの統合

- [ ] **predict.py の GCS パス参照（文字列リテラル）**: 計画 item 7 は `predict.py` の L98-100, L444, L908, L1078-1079, L1251 を書き換え対象としている。L98-100 は `GCS_PREDICTIONS`, `GCS_ACTUALS`, `GCS_ACCURACY` 定数で定義されており、ここを変更すれば L908, L1078-1079, L1251 の `gcs_list_blobs("earnings_model/predictions/")` 等は **自動的には追従しない**。これらの箇所は GCS_* 定数を使わず直接文字列リテラルで `"earnings_model/predictions/"` 等を渡している。計画は行番号を列挙しているため意識はしているようだが、定数変更と文字列リテラル変更が混在する点を明示していない
- [ ] **beta_calc.py の Cloud Run Job**: `beta_calc.py` L31 の `GCS_PATH = "earnings_model/beta_20d.csv"` を `"earnings_model/zaraba_beta_20d/beta_20d.csv"` に変更するが、この変更は Cloud Run Job `beta-calc`（`docker/Dockerfile.beta-calc`）の再デプロイが必要。計画に Cloud Run Job 再デプロイの手順がない
- [ ] **zara.py の import 追加**: Part 2 で `cmd_upload_results` と `cmd_gcs_review` を新設し、`zara.py` にメニュー 8/9 を追加する。`zara.py` L8-17 の import ブロックにこれらの関数を追加する必要があるが、計画 item 6 は「メニュー追加」のみで import 変更を明示していない（自明ではあるが）
- [ ] **PS メニュー（`claude-investment-agent.ps1`）**: 066 知見 MD 冒頭に `C:\Users\zonekun\Dropbox\stock\script\claude-investment-agent.ps1`（Windows 専用メニュー。CLI 引数変更時は同期必須）とある。新サブコマンド `upload` / `gcs-review` の追加時に PS メニューへの反映要否を検討していない

### リスク・コスト

- **GCS コピーコスト**: `earnings_model/` 配下の blob 数・総サイズの見積もりがない。predictions/ と actuals/ は日次で JSON ファイルが蓄積されるため数百〜数千 blob になり得る。GCS の blob コピーは Class A 操作（$0.05/10,000 ops）でコストは無視可能だが、コピースクリプトの実行時間と進捗表示の設計が計画にない
- **撤退基準**: 記載なし。Part 3 のコピー完了後にソース書き換えで不具合が見つかった場合の切り戻し手順は、「旧 blob はまだ残っている」ことで暗黙にカバーされるが、明示化が望ましい
- **beta_20d のパス変更と日次 Job の整合**: `beta-calc` Cloud Run Job は毎日 18:30 JST に `earnings_model/beta_20d.csv` に書き込む。リネーム移行中にこの Job が走ると、旧パスに新データが書き込まれ新パスとデータ齟齬が発生する。移行は平日 18:30 以降〜翌朝までか、Job を一時停止して実施すべき

### 抜け漏れ

- [ ] **059 知見 MD §GCS 保存先（EDA）L613 の `models/` `reports/` サブフォルダ**: 計画のリネーム対象に含まれていない。EDA ノートブックが `earnings_model/models/` や `earnings_model/reports/` に保存する設計があるが、これらは `earnings_reaction_models/` `earnings_reaction_reports/` にリネームしないのか。意図的な非スコープなら計画に明記すべき
- [ ] **`batch_rerun_predict.py`**: 059 知見 MD L185 に記載のスクリプト。predict.py と同じ GCS パスを使う可能性があるが、grep では直接の GCS パス参照が見つからなかった。predict.py の関数を import して使っている可能性がある（確認不能だが、predict.py 側の定数変更で追従する可能性が高い）
- [ ] **data_catalog.md のリネーム先パスの追記**: 計画 item 13 は既存の L1080-1084 の書き換えのみだが、Part 2 で新設する `earnings_model/zaraba_scoring_results/` のエントリを data_catalog.md に追加する必要がある。計画に明記なし
- [ ] **Colab 上のノートブックコピー**: CLAUDE.md memory に「ノートブック修正時 Colab 同期」ルールがある。item 10 で `earnings_model_eda.ipynb` の GCS パスを変更する場合、Colab Notebooks/ 上のコピーにも反映が必要。計画に明記なし

### 目的・スコープの明確性（任意）

- 目的は 3 点に明確に分離されており、各 Part が独立して理解できる
- 非スコープの明示はないが、`docs/reviews/` `docs/plans/` 配下の旧パス参照は「歴史記録のため書き換え不要」と明記されており良い
- Part 1 と Part 2 の間に依存関係がないことが読み取れるが、Part 2 と Part 3 の間には依存がある（Part 2 の GCS パスは Part 3 のリネーム後のパス `zaraba_scoring_results/` を使う）。この依存関係と実行順序を計画に明記すべき

### 段階的検証計画（任意）

- 計画末尾の「実行順序の注意」で Part 3 の 4 段階手順（コピー→書き換え→動作確認→ユーザー承認→削除）が定義されており、破壊的操作の段階的アプローチとして適切
- ただし「動作確認」の内容が `predict.py today` / `beta_calc.py` / `zaraba_earnings.py` の各パス正常動作のみで、`exclusion_manager.py list` / `review_report.py` の動作確認が含まれていない

### 完了条件の検証可能性（任意）

- 5 つの完了条件はいずれも具体的に検証可能
- ただし Part 2 の完了条件「メニュー 9 で GCS の results が watch と同じレイアウトで表示される」は、GCS にデータがない初回実行時のエラーハンドリングも検証に含めるべき

### データカタログ整合（任意）

- item 13 で既存エントリの書き換えを計画しているが、前述の通り新規パス `earnings_model/zaraba_scoring_results/` の data_catalog.md 追加が欠落

---

## 【重大な指摘】（即修正）

### #1 predict.py の文字列リテラル GCS パス参照が定数変更だけでは追従しない

- 箇所: `scripts/earnings_model/predict.py:908`, `scripts/earnings_model/predict.py:1078-1079`, `scripts/earnings_model/predict.py:1251`
- 事象: これらの行は `gcs_list_blobs("earnings_model/predictions/...")` のように文字列リテラルで GCS パスを指定している。計画 item 7 は L98-100 の定数（`GCS_PREDICTIONS` 等）と合わせてこれらの行番号を列挙しているが、定数変更が自動的にリテラル箇所に伝播するわけではない
- トリガー: Part 3 実施時に L98-100 の定数のみを変更し、L908, L1078-1079, L1251 のリテラルを変更し忘れた場合
- 影響: `answer` サブコマンドが旧パスの prediction ファイルを探しに行き 0 件 → エラー終了。`backfill` が旧パスの日付範囲を算出して空 → 全期間を再処理（コスト増）。`accuracy` が旧パスの actual を読みに行き 0 件 → 精度集計が空
- 根拠: L908 `gcs_list_blobs(f"earnings_model/predictions/prediction_{predict_date}_")`, L1078 `gcs_list_blobs("earnings_model/predictions/")`, L1079 `gcs_list_blobs("earnings_model/actuals/")`, L1251 `gcs_list_blobs("earnings_model/actuals/")`
- 推奨対応: 計画の item 7 の修正方針に「L98-100 の定数変更 + L908/L1078/L1079/L1251 の文字列リテラルを定数参照に書き換え（例: `gcs_list_blobs(f"{GCS_PREDICTIONS_PREFIX}prediction_{predict_date}_")`）」と明記する。リテラルを定数化すれば同種の漏れを防止できる

### #2 beta-calc Cloud Run Job の再デプロイが計画に含まれていない

- 箇所: `scripts/beta_calc.py:31`, `docker/Dockerfile.beta-calc`
- 事象: `beta_calc.py` の `GCS_PATH` を変更しても、Cloud Run Job にデプロイ済みのコンテナイメージは旧コードのまま。日次 18:30 JST の Job が旧パス `earnings_model/beta_20d.csv` に書き続ける
- トリガー: Part 3 実施後、翌営業日に beta-calc Job が実行された時
- 影響: 新パス `earnings_model/zaraba_beta_20d/beta_20d.csv` には古いデータが残り、predict.py / zaraba_earnings.py が古い beta を使ってスコアリングする。データ欠損ではないが精度劣化
- 根拠: `docker/Dockerfile.beta-calc` が `scripts/beta_calc.py` をコピーしており、デプロイ時のイメージにパスがベイクされる
- 推奨対応: 計画のステップ 19-20 の間に「beta-calc Cloud Run Job の再デプロイ」を追加。また、移行中に Job が走らないようスケジューラの一時停止/タイミング調整を検討

### #3 beta_20d パスの移行タイミングで日次 Job との競合リスク

- 箇所: `scripts/beta_calc.py:31`, `scripts/zaraba_earnings.py:572`, `scripts/earnings_model/predict.py:444`
- 事象: beta-calc Job（毎日 18:30 JST）が旧パスに書き込む間にリネーム作業を行うと、コピー済みの新パスと Job が書いた旧パスでデータが乖離する
- トリガー: リネーム作業を平日 18:30 JST 前後に実施した場合
- 影響: 新旧パスのデータ不整合。predict.py が新パスから古い beta を読み、zaraba_earnings.py が新パスから古い beta を読む
- 根拠: beta_20d.csv は日次更新されるため、static なファイルではない
- 推奨対応: 計画に「Part 3 は beta-calc Job の非稼働時間帯（土日 or 平日 19:00 以降）に実施する。または事前に beta-calc Cloud Scheduler を pause し、移行完了+再デプロイ後に resume」を追記

---

## 【改善提案】（可読性・保守性）

### #1 data_catalog.md への新規エントリ追加

- 箇所: `data_catalog.md`（計画 item 13）
- 現状: 計画は既存 L1080-1084 の書き換えのみ。Part 2 で新設する `earnings_model/zaraba_scoring_results/` の data_catalog エントリが計画にない
- 提案: item 13 に「`zaraba_scoring_results/` エントリを追加（カラム: results CSV フォーマット、生成元: `zaraba_earnings.py cmd_upload_results`）」を追記

### #2 Colab ノートブック同期の明記

- 箇所: 計画 item 10
- 現状: `earnings_model_eda.ipynb` の GCS パスを変更するが、Colab Notebooks/ 上のコピーへの反映が計画に含まれていない
- 提案: item 10 に「変更後、Colab Notebooks/ にもコピーを反映」を追記

### #3 Part 2 と Part 3 の実行順序依存の明記

- 箇所: 計画全体構成
- 現状: Part 2 の GCS パス `earnings_model/zaraba_scoring_results/` は Part 3 のリネーム対象テーブルの「(新規)」行に記載されているが、Part 2 を先に実装すると新パスが Part 3 のリネーム前に使われる（問題なし。新規フォルダなので）。ただし Part 3 のリネーム「対象」ではないことを明確にすべき
- 提案: 計画末尾の「実行順序の注意」に「Part 1/2 は Part 3 に先行して実装可能。Part 2 の `zaraba_scoring_results/` は新規フォルダであり Part 3 のリネーム対象外」と追記

### #4 059 知見 MD の `models/` `reports/` の扱い明記

- 箇所: 計画 Part 3 リネーム対応表
- 現状: `earnings_model/features/` はリネーム対象だが、同じ §GCS 保存先（EDA）に記載の `models/` `reports/` は対象外
- 提案: 計画のリネーム対応表の後に「※ `earnings_model/models/` `earnings_model/reports/` は現時点で未使用（or 使用頻度が低い）ため今回のスコープ外」等の注記を追加。意図的な除外を明示する

### #5 PS メニューへの反映検討

- 箇所: 計画 Part 2
- 現状: 新サブコマンド `upload` / `gcs-review` は CLI 直接実行だが、066 知見 MD に PS メニュー同期義務が記載されている
- 提案: Part 2 に「PS メニューへの反映要否を確認（066 知見 MD §PSメニュー依存パッケージチェック 参照）」を追記

---

## 【確認できなかった事項】

- GCS `earnings_model/` 配下の実際の blob 数・サイズ（コピースクリプトの実行時間見積もりに必要）
- `earnings_model/models/` `earnings_model/reports/` に実際に blob が存在するか（EDA ノートブックの保存先として定義されているが、実際に使われているかはコードの実行状態に依存）
- `batch_rerun_predict.py` が predict.py の GCS 定数を import 経由で参照しているか（直接の文字列リテラル参照は grep で確認できなかったが、import 経由の依存は閲読範囲で確認不能）
- `earnings_model_core.py` は GCS パスを持たないことを確認済み。ただし将来このファイルに GCS 関連ロジックが追加された場合のリスクは、定数一元化（指摘 #1）で軽減される

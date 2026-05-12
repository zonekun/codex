# 作業計画: ザラ場ツール改修 + GCS earnings_model フォルダリネーム

**作成日時**: 2026-05-08 15:20 (JST)
**ステータス**: 全完了（2026-05-08 16:25 JST）。Part 1-3 実装・コミット済み、beta-calc 再デプロイ済み、旧 blob 129 件削除済み
**分類**: (b) 継続改修型
**親知見 MD**: `docs/knowledges/tools/066_zaraba_tool.md`, `docs/knowledges/tools/059_earnings_model_eda.md`

## 目的

1. ザラ場ツールの watch/review テーブルに Q 列（1Q/2Q/3Q/FY）を追加
2. results.csv の GCS アップロード/ダウンロード表示機能を追加（メニュー8/9）
3. GCS `earnings_model/` 配下のフォルダ名を直感的にリネーム

## 背景・動機

- watch/review テーブルに決算期の区別がなく、FY と 1Q の判読ができない
- results.csv がローカルのみで、predict notebook の答え合わせと連携できない（066知見MD §将来TODO）
- GCS `earnings_model/` 配下のフォルダ名が `accuracy/` `actuals/` 等で汎用的すぎ、将来モデルが増えた際に判別不能

## 作業ステップ

### Part 1: ザラ場ツール UI 改修

**Q値の取得元**: `_score_record()` の戻り値 dict に `cur_per_type` キーが含まれている（L1875）。値は `"1Q"` / `"2Q"` / `"3Q"` / `"FY"` のいずれか。watch/review テーブルではこのフィールドを参照する。

1. [ ] `zaraba_earnings.py` — watch テーブル（`_build_table`）に Q 列追加（Cap の横）。`scored_results` の `cur_per_type` を表示
2. [ ] `zaraba_earnings.py` — review テーブル（`cmd_review`）に Q 列追加（同上）

### Part 2: GCS アップロード/ダウンロード

3. [ ] `zaraba_earnings.py` — `cmd_upload_results()` 新規関数: ローカル results.csv → GCS アップロード
4. [ ] `zaraba_earnings.py` — `cmd_gcs_review()` 新規関数: GCS から results を取得し watch と同じレイアウトで表示
5. [ ] `zaraba_earnings.py` — CLI サブコマンド `upload` / `gcs-review` 追加
6. [ ] `zara.py` — メニュー 8（結果保存）/ 9（結果表示）追加 + import 追加
6a. [ ] PS メニュー（`claude-investment-agent.ps1`）への反映要否を確認（066 知見 MD §PS メニュー参照）

**GCS パス**: `gs://stock_data_1930932/earnings_model/zaraba_scoring_results/results_YYYYMMDD.csv`

**`zaraba_scoring_results/` の命名について**: 他のリネーム先は `earnings_reaction_*` プレフィックスだが、`zaraba_scoring_results/` はザラ場ツール固有のデータ（決算反応モデルの出力ではなくスコアリング結果）であるため `zaraba_` プレフィックスで意図的に区別する。

### Part 3: GCS フォルダリネーム

既存 blob をコピー → 旧 blob 削除。ソースコード・ドキュメント内のパス参照を一括置換。

**リネーム対応表**:

| 旧パス | 新パス |
|--------|--------|
| `earnings_model/accuracy/` | `earnings_model/earnings_reaction_accuracy/` |
| `earnings_model/actuals/` | `earnings_model/earnings_reaction_actuals/` |
| `earnings_model/exclusions/` | `earnings_model/earnings_reaction_exclusions/` |
| `earnings_model/features/` | `earnings_model/earnings_reaction_features/` |
| `earnings_model/predictions/` | `earnings_model/earnings_reaction_predictions/` |
| `earnings_model/beta_20d.csv` | `earnings_model/zaraba_beta_20d/beta_20d.csv` |
| (新規) | `earnings_model/zaraba_scoring_results/` |

**リネーム対象外**: `earnings_model/models/`、`earnings_model/reports/` — 059 MD §GCS保存先(EDA) に記載あるが、現時点で使用実績なし（EDA ノートブックの将来計画のみ）。将来使用開始時に個別リネームを検討。

#### ソースコード（パス参照の書き換え）

7. [ ] `scripts/earnings_model/predict.py` — **定数**: L98-100（`GCS_PREDICTIONS`, `GCS_ACTUALS`, `GCS_ACCURACY`）, L444（beta_20d パス）。**文字列リテラル（定数変更では追従しない）**: L908（`gcs_list_blobs(f"earnings_model/predictions/...")`）, L1078（`gcs_list_blobs("earnings_model/predictions/")`）, L1079（`gcs_list_blobs("earnings_model/actuals/")`）, L1251（`gcs_list_blobs("earnings_model/actuals/")`）→ これらリテラルは `GCS_*` 定数参照に書き換える
8. [ ] `scripts/earnings_model/exclusion_manager.py` — L8, L41
9. [ ] `scripts/earnings_model/review_report.py` — L48
10. [ ] `scripts/earnings_model/earnings_model_eda.ipynb` — L866。**変更後、Colab Notebooks/ にもコピーを反映**（CLAUDE.md memory ルール準拠）。※ `.ipynb_checkpoints/` は `.gitignore` 対象の場合は書き換え不要（次回ノートブック保存時に自動更新）
11. [ ] `scripts/beta_calc.py` — L31
12. [ ] `scripts/zaraba_earnings.py` — L572（`_load_beta_20d`）

#### ドキュメント（パス参照の書き換え）

13. [ ] `data_catalog.md` — L1080-1084（既存 GCS パス 5 行の書き換え）+ `zaraba_scoring_results/` 新規エントリ追加（カラム: results CSV フォーマット、生成元: `zaraba_earnings.py cmd_upload_results`）
14. [ ] `docs/knowledges/tools/059_earnings_model_eda.md` — L71-72, L159, L529-536（§GCS保存先コードブロック全体: predictions/actuals/accuracy 各行）, L544, L610-617（§GCS保存先(EDA)コードブロック全体: features/models/reports 各行）
15. [ ] `docs/knowledges/tools/066_zaraba_tool.md` — beta_20d パス、zaraba_scoring_results 追記
16. [ ] `docs/knowledges/tools/072_beta_calc.md` — L48
17. [ ] `docs/knowledges/tools/076_earnings_exclusion_mechanism.md` — L26

※ `docs/reviews/` `docs/plans/` 配下の旧パス参照は歴史記録のため書き換え不要

#### GCS 実体の移行

18. [ ] GCS blob コピー（旧 → 新）— Bash ワンライナーで `gcs_copy_rename.py` 一時スクリプトを実行（6 フォルダ + beta_20d.csv、件数限定のため dry-run 不要。コピー結果をログ出力して目視確認）
19. [ ] コピー完了後、ソースコード変更を commit（`feat: GCS earnings_model/ フォルダリネーム + パス参照更新`）
19a. [ ] ドキュメント変更を commit（`docs: GCS earnings_model/ リネームに伴うドキュメントパス更新`）
19b. [ ] **beta-calc Cloud Run Job を再デプロイ**（`gcs_path` 変更がコンテナにベイクされるため。手順は `docs/knowledges/tools/005_cloudrun_job_deploy.md` + `docs/knowledges/tools/072_beta_calc.md` 参照）
20. [ ] 動作確認:
    - `predict.py predict --date <直近営業日>` → GCS 新パスへの保存を確認
    - `beta_calc.py`（dry-run or 限定実行）→ 新パス `zaraba_beta_20d/beta_20d.csv` からの読み書き確認
    - `zaraba_earnings.py review --date <直近営業日>` → beta_20d が新パスから読み込まれることを確認
    - `exclusion_manager.py list` → 新パスから除外リスト取得確認
    - `grep -r "earnings_model/(accuracy|actuals|exclusions|features|predictions|beta_20d)" --include="*.{py,md,ipynb}"` → 旧パス残存ゼロ確認
21. [ ] 旧 blob 削除（動作確認 + ユーザー承認後）

## 成果物

- `zaraba_earnings.py` — Q 列追加 + upload/gcs-review サブコマンド
- `zara.py` — メニュー 8/9 追加
- GCS `earnings_model/` 配下フォルダリネーム（全 6 フォルダ）
- ソースコード・ドキュメントのパス参照更新

## 完了条件

- watch/review テーブルに Q 列が表示される（`cur_per_type` から取得）
- メニュー 8 で results が GCS にアップロードされる
- メニュー 9 で GCS の results が watch と同じレイアウトで表示される（GCS にデータがない場合のエラーメッセージも確認）
- `predict.py today` / `beta_calc.py` が新パスで正常動作
- **beta-calc Cloud Run Job が再デプロイ済み**で新パスに書き込む
- 旧パスの blob が全て新パスに移行済み
- `data_catalog.md` に `zaraba_scoring_results/` 新規エントリが追加済み

## 見積もり

- 想定所要時間: 1.5 時間
- 難易度: 中（ファイル数多いが機械的置換が主）

## コミット粒度

| commit | 内容 |
|--------|------|
| 1 | Part 1: Q 列追加（`zaraba_earnings.py`） |
| 2 | Part 2: GCS アップ/ダウン（`zaraba_earnings.py` + `zara.py`） |
| 3 | Part 3 ソースコード: GCS パス書き換え（`predict.py` / `beta_calc.py` / `exclusion_manager.py` 等） |
| 4 | Part 3 ドキュメント: GCS パス書き換え（`data_catalog.md` / 知見 MD 群） |

## 実行順序の注意

**Part 間の依存関係**: Part 1 / Part 2 は Part 3 に先行して実装可能（独立）。Part 2 の `zaraba_scoring_results/` は新規フォルダであり Part 3 のリネーム対象外。Part 1 → Part 2 → Part 3 の順で実施する。

**Part 3（GCS リネーム）は破壊的操作**。以下の順序を厳守:
1. 旧 blob → 新 blob にコピー（この時点で両方存在）
2. ソースコード・ドキュメントを新パスに書き換え
3. beta-calc Cloud Run Job を再デプロイ
4. 動作確認（ステップ 20 の具体的手順に従う）
5. ユーザー承認後に旧 blob 削除

**タイミング制約**: beta-calc Cloud Run Job は毎日 18:30 JST に `earnings_model/beta_20d.csv` に書き込む。Part 3 の移行は **beta-calc Job の非稼働時間帯（土日 or 平日 19:00 以降）** に実施する。または事前に beta-calc Cloud Scheduler を pause し、移行完了 + 再デプロイ後に resume する。

## ロールバック手順

- **動作確認失敗時**: ソースコード・ドキュメントを `git checkout -- <対象ファイル>` で復元。新 blob はそのまま残す（旧 blob も残っているため両方共存）
- **旧 blob 削除後に問題発覚した場合**: 新 blob → 旧パスにコピーバック + `git revert`

---

## レビュー追記

### 2026-05-08 15:31 JST — code-reviewer（v1）

→ `docs/reviews/112_cr_zaraba_gcs_rename.md`（品質 B、重大 3 件 + 改善 5 件）

### 2026-05-08 15:31 JST — md-reviewer（v1）

→ `docs/reviews/112_mr_zaraba_gcs_rename_plan.md`（AI可読性 B、重大 4 件 + 改善 4 件）

### 2026-05-08 16:xx JST — 指摘反映（v2）

code-reviewer 重大 #1-#3 + md-reviewer 重大 #1-#4 + 両レビュー改善提案を反映。主な変更:
- Part 1: Q値取得元（`cur_per_type`）を明記
- Part 2: PS メニュー反映チェック追加、`zaraba_scoring_results/` 命名理由を明記
- Part 3 item 7: predict.py の定数 vs 文字列リテラルを明示、リテラルの定数化を指示
- Part 3 item 14: 059 MD の行番号を網羅列挙（「等」を除去）
- リネーム対象外（models/reports）の除外理由を追記
- beta-calc Cloud Run Job 再デプロイステップ追加
- タイミング制約（18:30 JST beta-calc Job）を追記
- 動作確認手順の具体化（5 項目）
- ロールバック手順追加
- コミット粒度定義追加
- data_catalog.md 新規エントリ追加を明記
- Colab ノートブック同期を明記

### 2026-05-08 15:56 JST — code-reviewer（v2 再レビュー）

→ `docs/reviews/113_cr_zaraba_gcs_rename_v2.md`（品質 A、重大 0 件 + 改善 3 件。v1 重大 3 件すべて反映確認済み）

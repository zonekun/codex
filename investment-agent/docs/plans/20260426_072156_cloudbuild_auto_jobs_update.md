# Cloud Build 後の `jobs update` 自動化（005 ルール見落とし再発防止）

**作成日時**: 2026-04-26 07:21 JST
**対象ファイル**: `cloudbuild/cloudbuild.tdnet-load-daily.yaml`（13行、commit 734f61d 時点）、`docs/knowledges/tools/005_cloudrun_job_deploy.md`（316行）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: `gcloud builds submit` 後に `gcloud run jobs update --image` を忘れると旧イメージが使われ続ける問題の再発防止。ドキュメント上のルール（005 §⑥ L177）は既に存在するが見落としで事故が発生したため、cloudbuild.yaml にビルド後の自動 `jobs update` ステップを追加して人間の記憶に依存しない仕組みにする。スコープは共有イメージを使う tdnet 系 cloudbuild.yaml の変更と 005 の知見更新のみ。

---

## 前提サマリ

- 過去修正: なし（ルールは 005 §⑥ L177 に記載済みだが自動化されていなかった）
- 残存: 本プランで 1 件（自動化）+ 1 件（知見更新）対処
- 実機検証: prod で再現済み（2026-04-25、サロゲート修正が Cloud Build 後に反映されず ai-prepare が旧イメージで再失敗）
- 関連 incident: Workflow `0a9be078` — Cloud Build でレジストリ `:latest` を更新したが、3 Job とも digest 固定で旧イメージのまま実行。修正コードが反映されず同一エラーで再失敗。手動で `gcloud run jobs update --image` を 3 Job に実行して解消

---

## 優先度の定義

- **P0**: 次回 Cloud Build 時に同じ見落としが起きることをブロックする

---

## 指摘項目

### P0-1. `cloudbuild.tdnet-load-daily.yaml` にビルド後 `jobs update` ステップがない 🚨

**症状**: `gcloud builds submit` でレジストリの `:latest` タグを更新しても、Cloud Run Job 側は作成/更新時に解決した digest で固定されているため自動追従しない。`gcloud run jobs update --image` を手動実行し忘れると、旧イメージで実行され続ける。

**該当**: `cloudbuild/cloudbuild.tdnet-load-daily.yaml:L1-L13`

```yaml:L1-L13
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args:
      - build
      - -f
      - docker/Dockerfile.tdnet-load-daily
      - -t
      - us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-load-daily:latest
      - .

images:
  - us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-load-daily:latest
```

**根本原因**: ビルドとデプロイが分離しており、デプロイ（`jobs update`）が手動ステップとして人間の記憶に依存している。ドキュメント（005 §⑥）にルールがあっても、Cloud Build コマンドだけコピペして `jobs update` を飛ばすミスは構造的に排除されていない。特に `tdnet-load-daily` は 1 イメージを 3 Job（`tdnet-load-daily`, `tdnet-ai-prepare`, `tdnet-ai-finalize`）が共有するため、更新漏れの影響範囲が大きい。

**修正方針**: cloudbuild.yaml に Cloud SDK ステップを追加し、ビルド成功後に自動で全対象 Job のイメージを更新する。

```yaml
# before
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args:
      - build
      - -f
      - docker/Dockerfile.tdnet-load-daily
      - -t
      - us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-load-daily:latest
      - .

images:
  - us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-load-daily:latest

# after
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args:
      - build
      - -f
      - docker/Dockerfile.tdnet-load-daily
      - -t
      - us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-load-daily:latest
      - .
  - name: 'gcr.io/google.com/cloudsdktool/cloud-sdk'
    entrypoint: 'bash'
    args:
      - '-c'
      - |
        for job in tdnet-load-daily tdnet-ai-prepare tdnet-ai-finalize; do
          echo "Updating job: $$job"
          gcloud run jobs update "$$job" --region=us-west1 \
            --image=us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-load-daily:latest
        done

images:
  - us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-load-daily:latest
```

**呼び出し側への波及**:
- `docs/knowledges/tools/013_tdnet_load.md:L249-L259` — 「ビルド後に必須」の手動 `jobs update` ループのコメントを更新（自動化済みの旨を追記、手動コマンドはフォールバックとして残す）
- `docs/knowledges/tools/005_cloudrun_job_deploy.md:L177` — §⑥ に「共有イメージの場合は cloudbuild.yaml に自動 update ステップを組み込むべき」を追記

**前提条件**: Cloud Build サービスアカウント（`<project-number>@cloudbuild.gserviceaccount.com`）に `roles/run.developer` または `roles/run.admin` が付与されていること。未付与の場合は事前に `gcloud projects add-iam-policy-binding` で付与が必要。

**検証**: Cloud Build 実行後、`gcloud run jobs describe tdnet-ai-prepare --region=us-west1 --format='value(template.template.containers[0].image)'` で新しい digest に更新されていることを確認。

**ロールバック**: cloudbuild.yaml から追加ステップを削除し、従来の手動 `jobs update` 運用に戻す。データ影響なし。

---

### P1-1. `cloudbuild.tdnet-gemma-runner.yaml` も同様に自動化が必要 ⚠️

**症状**: `tdnet-gemma-runner` も独自イメージ・独自 Job を持つが、同様に `jobs update` が手動。

**該当**: `cloudbuild/cloudbuild.tdnet-gemma-runner.yaml:L1-L13`

**修正方針**: P0-1 と同パターンで、ビルド後に `gcloud run jobs update tdnet-gemma-runner` を自動実行するステップを追加。

```yaml
# after（追加ステップのみ）
  - name: 'gcr.io/google.com/cloudsdktool/cloud-sdk'
    entrypoint: 'bash'
    args:
      - '-c'
      - |
        echo "Updating job: tdnet-gemma-runner"
        gcloud run jobs update tdnet-gemma-runner --region=us-west1 \
          --image=us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-gemma-runner:latest
```

**呼び出し側への波及**: なし（個別イメージ、個別 Job）

**検証**: P0-1 と同様

**ロールバック**: ステップ削除で従来運用に戻る

---

### P1-2. 005 §⑥ に「共有イメージ自動化パターン」を追記 ⚠️

**症状**: 005 §⑥ は「3ステップが1セット」と書いているが、手動運用しか示していない。共有イメージ（1 image → N jobs）のケースで自動化パターンを示さないと、新規 Job 追加時に同じ見落としが繰り返される。

**該当**: `docs/knowledges/tools/005_cloudrun_job_deploy.md:L175-L188`

**修正方針**: §⑥ に「共有イメージパターン」のサブセクションを追加。cloudbuild.yaml に jobs update ステップを組み込む方法を記載。

**呼び出し側への波及**: なし（ドキュメント変更のみ）

**検証**: 目視確認

**ロールバック**: 不要

---

## 対応アンチパターン

| plan ID | 004 | T-x | G-x |
|---|---|---|---|
| P0-1 | — | — | — |
| P1-1 | — | — | — |
| P1-2 | — | — | — |

> 既存アンチパターン分類には直接該当なし。「ビルドとデプロイの分離による手動ステップ漏れ」は CI/CD パイプライン設計の問題であり、コーディング規約やスクリプトアンチパターンとは異なるカテゴリ。

---

## 検証戦略

1. **smoke test**: P0-1 修正後の `cloudbuild.tdnet-load-daily.yaml` を `gcloud builds submit` で実行。ビルドログで 3 Job の `jobs update` 成功を確認。`gcloud run jobs describe` で各 Job の image digest が新しいものに更新されていることを確認
2. **権限確認**: Cloud Build SA に `run.jobs.update` 権限が無い場合、ビルドの docker step は成功するが jobs update step で失敗する。この場合は先に IAM 付与が必要
3. **本番適用判断基準**: ビルド完了後に 3 Job すべてが新 digest を参照していること
4. **回収手順**: cloudbuild.yaml から追加ステップを削除 → 再ビルド → 手動 `jobs update` で復旧。データ影響なし

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/tools/005_cloudrun_job_deploy.md`（§⑥ スクリプト変更時の更新）
- 知見 MD: `docs/knowledges/tools/013_tdnet_load.md`（ビルド手順セクション）
- 関連 incident: 2026-04-25 サロゲート修正未反映事故（Workflow `0a9be078`、手動 `jobs update` で解消）
- フォーマット正本: `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット

---

## レビュー追記: 2026-04-26 08:00 JST — code-reviewer

# コードレビュー: Cloud Build 後の `jobs update` 自動化

- 日時: 2026-04-26 08:00 JST
- 対象: `docs/plans/20260426_072156_cloudbuild_auto_jobs_update.md`
- パターン: 2 (改修)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: Cloud Build YAML に `gcloud run jobs update` ステップを追加し、Docker ビルド後に自動で Cloud Run Job のイメージを最新 digest に更新する。ドキュメント上のルール（005 §⑥）を YAML レベルで強制し、手動ステップ漏れを構造的に排除する
- 品質評価: **A** — 真因（ビルドとデプロイの分離＋手動依存）に正しく対処しており、修正範囲が小さくロールバックも容易。ただし IAM 権限の事前確認手順と `set -e` 欠落が実装上の穴
- 主要リスク:
  1. Cloud Build SA に `run.jobs.update` 権限が未付与の場合、ビルド自体は成功するが jobs update ステップで失敗しビルド全体が FAILURE になる（docker push 済みだがジョブ未更新という中途半端な状態）
  2. `set -e` がないため、for ループ内の 1 Job 目の update 失敗が無視されて 2, 3 Job 目だけ更新される部分更新リスク
  3. 他の cloudbuild YAML（42 本中 40 本）にも同じ「手動 `jobs update` 漏れ」問題が潜在するが、プランのスコープ外

## 【パターン2: 改修プラン評価】

### 妥当性

プランは真因（`:latest` タグが digest 固定で自動追従しない＋手動ステップが人間の記憶に依存）に正しく対処している。YAML レベルでの強制は対症療法ではなく構造的解決であり、適切。ただし、「共有イメージ」問題に特化しており、1 image = 1 job のケース（既存 40 本の大半）は依然として手動運用が残る点は認識すべき。

### 副作用・デグレードチェック

- [x] **既存の Docker ビルド成功フローへの影響**: 追加ステップは Docker ビルド・push 完了後に実行されるため、ビルド自体のフローは変わらない。問題なし
- [x] **`images:` セクションとの干渉**: `images:` ディレクティブは Docker push を行うだけで jobs update とは独立。干渉なし
- [ ] **ビルド時間の増加**: `gcr.io/google.com/cloudsdktool/cloud-sdk` イメージの pull（初回は数百 MB）+ 3 回の `jobs update` API 呼び出しでビルド全体が 1-2 分延長する。実害は小さいが認識しておくべき
- [x] **過去の緩和策**: 特になし。これまで完全に手動依存だった

### 抜け漏れ（類似観点での横展開含む）

- [ ] **他の全 cloudbuild YAML（40 本超）も同じ問題を抱える**: 005 §⑥ のルールは全 Job に適用される。プランが `tdnet-load-daily` と `tdnet-gemma-runner` のみにスコープを絞っているのは意図的と思われるが、横展開の方針（将来的に全 YAML に適用するか、テンプレ §③ を更新するか）の言及がない
- [ ] **005 §③ cloudbuild.yaml テンプレート未更新**: 新規 Job 作成時にテンプレートをコピーするフローで、`jobs update` ステップが含まれていなければ同じ問題が再発する。§③ テンプレートの更新もスコープに入れるべき
- [ ] **IAM 権限付与の具体的コマンドが未記載**: 前提条件に `roles/run.developer` or `roles/run.admin` が必要と書いてあるが、付与コマンドの具体例がない。事前確認コマンド（`gcloud projects get-iam-policy ... --flatten ... --filter ...`）も示されていない
- [ ] **`images:` と jobs update の実行順序**: Cloud Build は `steps:` を順次実行した後に `images:` を処理する。P0-1 の after では jobs update が step 2（最後の step）に配置されており、`images:` による push はその後に実行される。つまり **jobs update が docker push より先に走る可能性がある**。ただし、step 1 の `docker build -t` でローカルにタグ付けされたイメージは step 2 で参照可能であり、`images:` は Artifact Registry への push を行うだけなので、`gcloud run jobs update --image <tag>` は Artifact Registry 上の `:latest` を解決する。したがって**step 1 で build したイメージが Artifact Registry に push されるのは `images:` の処理時（steps の後）であり、step 2 の `jobs update` は旧イメージの digest を解決してしまう危険がある**。これは重大な問題 (**下記【重大な指摘】#1** で詳述)

### 新規リスク

- **部分更新**: for ループ中に 1 Job の update が失敗した場合、残りの Job は更新されるが失敗した Job は旧イメージのまま残る。3 Job の一貫性が崩れる
- **ビルド全体の成功/失敗判定の変化**: 従来は Docker ビルドの成功のみで Cloud Build は SUCCESS だったが、追加ステップにより jobs update 失敗でもビルド全体が FAILURE になる。これが望ましい挙動か否かの判断が必要（望ましいと考えるが、CI 通知等に影響する可能性）

## 【重大な指摘】（即修正）

### #1 `images:` と `steps:` の実行順序により jobs update が旧 digest を参照する可能性

- 箇所: `docs/plans/20260426_072156_cloudbuild_auto_jobs_update.md` P0-1 after YAML
- 事象: Cloud Build の実行順序は `steps:` (順次) → `images:` (push)。P0-1 の修正案では step 2 で `gcloud run jobs update --image ...tdnet-load-daily:latest` を実行するが、この時点では新しいイメージはまだ **Artifact Registry に push されていない**（`images:` ディレクティブによる push は全 steps 完了後に行われる）。`gcloud run jobs update --image <tag>` は Artifact Registry 上のタグを解決して digest を取得するため、**まだ push されていない新イメージではなく、前回ビルドの旧 digest を解決してしまう**
- トリガー: 毎回のビルドで発生する（初回ビルド時を除き、常に旧 digest が解決される）
- 影響: jobs update は成功するが、実質的に旧イメージへの再固定になり、本プランの目的が達成されない。見かけ上は正常動作だがイメージは更新されないという、検出困難な不具合になる
- 根拠: Cloud Build ドキュメント "The `images` field is used to push images to Artifact Registry after all build steps have completed." Step 2 は `images:` push の前に実行される
- 推奨対応: 2 つの選択肢がある:
  - **(A) step 1 内で `docker push` を明示し、`images:` を削除する**: step 1 に `docker push` コマンドを追加して Artifact Registry への push を step 内で完結させる。その後 step 2 で `jobs update` を実行すれば正しい digest が解決される
  - **(B) step 2 内で `docker push` してから `jobs update` を実行する**: step 2 の bash スクリプト内で push → update の順序を制御する
  - いずれの場合も `images:` ディレクティブは不要になるか、冪等な再 push として残す

### #2 `set -e` が step 2 の bash スクリプトに無い

- 箇所: `docs/plans/20260426_072156_cloudbuild_auto_jobs_update.md` P0-1 after YAML step 2
- 事象: for ループ内の `gcloud run jobs update` が 1 Job 目で失敗しても、bash は次の iteration に進む。3 Job 中 1 Job だけ更新失敗する部分更新が発生し、Cloud Build step 自体は exit 0 で成功扱いになる
- トリガー: 特定の Job 名のタイプミス、IAM 権限の部分的付与、Job が削除された場合
- 影響: 3 Job の image 一貫性が崩れ、一部 Job が旧イメージで実行される。デバッグ困難な不整合の原因になる
- 根拠: bash のデフォルト動作。`cloudbuild.setup-ollama-model.yaml:L7` では `set -e` が明示されている
- 推奨対応: step 2 の bash スクリプト冒頭に `set -e` を追加する

### #3 P1-1 の `tdnet-gemma-runner` step にも同じ `images:` 順序問題と `set -e` 欠落

- 箇所: `docs/plans/20260426_072156_cloudbuild_auto_jobs_update.md` P1-1 after YAML
- 事象: #1 と #2 の問題が P1-1 にも同様に存在する
- トリガー: #1, #2 と同一
- 影響: #1, #2 と同一
- 根拠: 同一のパターン
- 推奨対応: P0-1 と同様に修正

## 【改善提案】（可読性・保守性）

### #1 `cloud-sdk:slim` の使用を検討

- 箇所: P0-1 / P1-1 の step 2 で使用するイメージ名
- 現状: `gcr.io/google.com/cloudsdktool/cloud-sdk` (フルイメージ、約 2.5 GB)
- 提案: `gcr.io/google.com/cloudsdktool/cloud-sdk:slim` (約 600 MB) に変更。`gcloud run jobs update` コマンドのみ使用するため slim で十分。既存の `cloudbuild.setup-ollama-model.yaml:L34` でも `:slim` が使用されている。ビルド時間短縮（イメージ pull 時間の削減）に寄与

### #2 005 §③ テンプレートへの反映

- 箇所: `docs/knowledges/tools/005_cloudrun_job_deploy.md:L81-L94` (§③ cloudbuild.yaml テンプレート)
- 現状: テンプレートに `jobs update` ステップが含まれていない
- 提案: §③ テンプレートに `jobs update` ステップを含めた形に更新する。新規 Job 作成時にテンプレートからコピーする運用で、同じ見落としが再発しないようにする。1 image = 1 job のケースでも適用可能

### #3 検証戦略の強化: digest 比較による確認

- 箇所: 検証戦略 §1 smoke test
- 現状: `gcloud run jobs describe ... --format='value(template.template.containers[0].image)'` で新しい digest に更新されていることを確認
- 提案: ビルド前後で digest を比較する手順を明示する。ビルド前に `gcloud run jobs describe` で旧 digest を記録 → ビルド実行 → 再度 describe で新 digest を取得 → 両者が異なることを確認。digest が同一なら #1 の問題（旧 digest への再固定）が発生している

## 【改修プラン フォーマット適合性チェック】

- [x] 冒頭に対象ファイルの基準 commit hash が書かれているか → **commit `734f61d` 記載あり**
- [x] 前提サマリで過去修正と残件数が明示されているか → 記載あり
- [ ] 優先度の定義（P0/P1/P2 昇格基準）が冒頭にあるか → **P0 のみ定義、P1 の定義が欠落**。P1-1, P1-2 があるが P1 の昇格基準が書かれていない
- [ ] 各項目が「症状 / 該当 / 根本原因 / 修正方針 / 呼び出し側波及 / 検証 / ロールバック」7 フィールドを揃えているか → **P1-2 に「根本原因」フィールドが欠落**（症状から直接修正方針に飛んでいる）
- [x] 修正方針に before/after の両方が書かれているか → P0-1 は before/after あり
- [x] 呼び出し側への波及が該当行リストで明示されているか → P0-1 は `013:L249-L259`, `005:L177` と具体的
- [x] 「既に〜がある」系の前提記述を実コードと照合し、食い違いが無いか → `cloudbuild.tdnet-load-daily.yaml:L1-L13` の引用は実ファイル（13行）と完全一致
- [x] アンチパターン対応表が末尾にあるか → あり（全項目「該当なし」だが表自体は存在）
- [ ] 検証戦略が smoke / dev / prod / 回収手順の 4 段を網羅しているか → **dev 環境での検証ステップが欠落**。smoke → 本番の 2 段のみ
- [x] ロールバック手順が書かれているか → 各項目に記載あり
- [x] 読みづらさ・デッドコードだけで P0 に置かれている項目が無いか → P0-1 は実 incident 由来で適切
- [x] 関連 commit・知見 MD・incident ログへのリンクがあるか → あり

**フォーマット違反サマリ**: 2 件の軽微な違反（P1 定義欠落、P1-2 根本原因フィールド欠落）と 1 件の検証戦略の不足（dev ステップ欠落）

## 【確認できなかった事項】

- Cloud Build SA (`<project-number>@cloudbuild.gserviceaccount.com`) に `roles/run.developer` が現時点で付与されているかどうか。実機で `gcloud projects get-iam-policy gmailpj-357912 --flatten="bindings[].members" --filter="bindings.members:cloudbuild.gserviceaccount.com"` を実行しないと判定不能
- `images:` ディレクティブによる push と steps の厳密な実行順序について、Cloud Build のバージョンや設定による挙動差異があるかどうか。公式ドキュメントでは「steps 完了後に images を push」と記載されているが、実機確認が望ましい
- `tdnet-load-daily` / `tdnet-ai-prepare` / `tdnet-ai-finalize` の 3 Job が実際に存在し、リージョンが `us-west1` であることの確認（`gcloud run jobs list --region=us-west1` での実機確認が必要）

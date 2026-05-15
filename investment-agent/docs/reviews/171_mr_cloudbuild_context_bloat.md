# MD AI可読性レビュー: Cloud Build ビルドコンテキスト肥大化事故 (MR-171)

- **日時**: 2026-05-14 JST
- **対象ファイル**:
  - `cloudbuild/cloudbuild.edinet-load-parallel.yaml`
  - `docker/Dockerfile.edinet-load-parallel`
  - `.gcloudignore`
  - `docs/knowledges/tools/005_cloudrun_job_deploy.md`
- **パターン**: 4（運用事故 — 自己の行動不備記録）
- **レビュアー**: Claude (md-reviewer)
- **出力先**: `docs/reviews/171_mr_cloudbuild_context_bloat.md`
- **モード**: 通常モード（Step 3 で B 判定あり）

---

## 【サマリー】

- **AI可読性評価**: B（005知見MDに `.gcloudignore` メンテナンス手順が完全欠落。ビルドコンテキスト確認ステップなし）
- **誤読リスク評価**: B（テンプレートの `.` コンテキスト指定をそのまま踏襲し、`.gcloudignore` 確認を省略する誤作業パターンが再現しうる）
- **主要リスク**:
  1. 005知見MDにビルドコンテキスト管理（`.gcloudignore`）の記載が一切ない — AIがデプロイ手順に従っても肥大化を検知・防止できない
  2. cloudbuild.yaml テンプレート（005 §③）がコンテキスト `.` を標準パターンとして提示しており、AIはこれを正解として模倣する
  3. CLAUDE.md §6 の「Cloud Buildは知見MDからコマンドコピー。手打ち禁止」ルールが、`.gcloudignore` 確認をスキップさせる方向に作用する（コマンドコピーだけで完了と判断）

---

## 【Markdown 品質評価】

| 軸 | 評価 | 根拠 |
|----|------|------|
| **Accuracy** | A | 005のコマンド・パス・手順は正確。cloudbuild.yaml テンプレートも動作する |
| **Completeness** | B | `.gcloudignore` メンテナンス手順が完全欠落。ビルドコンテキストの確認・最適化に関する記述ゼロ。「やるべきこと」の情報が不足 |
| **Relevance** | A | 不要な情報混在なし。既存 Job 一覧も参照価値あり |
| **Actionability** | B | AIが005に従ってデプロイした場合、ビルドコンテキスト肥大化に気づく導線がない。「次に何をするか」にコンテキスト確認が含まれていない |

→ **通常モード**で全ステップ実行。

---

## 【AI 誤読リスク】

### Risk-1: テンプレートの `.` コンテキストを無条件に正しいと判断する

- **箇所**: `005_cloudrun_job_deploy.md` §③ cloudbuild.yaml テンプレート L82-107、特に L90 の `- .`
- **問題**: テンプレートがビルドコンテキストを `.`（プロジェクトルート全体）で指定。AIはテンプレートを「正解パターン」として模倣するため、`.gcloudignore` の内容を検証せず `.` をそのまま使う
- **誤読パターン**: 「005テンプレートに `.` と書いてある → これが標準 → `.gcloudignore` は考慮不要」
- **トリガー**: 新規 Job 作成時、既存 cloudbuild.yaml コピー時、スクリプト変更後の再ビルド時
- **影響**: 全 Job（43個の cloudbuild.yaml が存在）で同一の肥大化が発生しうる。今回の edinet-load-parallel 固有ではなくプロジェクト全体の問題

### Risk-2: 「コマンドコピー。手打ち禁止」がコンテキスト確認省略を正当化する

- **箇所**: CLAUDE.md L118「Cloud Build: `docs/knowledges/` 内の該当ドキュメントからコマンドをコピー。手打ち禁止」
- **問題**: この指示は「テンプレートに書いてあるコマンドをそのまま実行すれば安全」と解釈される。`.gcloudignore` の事前確認はコマンドコピーの範囲外のため、AIの行動チェーンから脱落する
- **誤読パターン**: 「CLAUDE.mdに手打ち禁止 → テンプレートをコピーすれば正しい → 周辺設定ファイル確認は指示されていない → 不要」

### Risk-3: `gcloud builds submit` 出力の「3216 file(s) total」を異常と判定するルールがない

- **問題**: `gcloud builds submit` はアップロードファイル数・サイズをログ出力するが、005知見MDにしきい値や確認手順がないため、AIは出力を通過させる
- **影響**: 事後検知の機会も失われ、肥大化が定常状態として定着する

---

## 【MD 構成リスク】

### Structure-1: 005 §⑥「スクリプト変更時の更新」にビルドコンテキスト確認が欠落

- **箇所**: `005_cloudrun_job_deploy.md` L190-207（§⑥）
- **問題**: §⑥は「スクリプト修正 → 再ビルド → jobs update」の3ステップを定義しているが、再ビルド前の `.gcloudignore` 確認が手順に含まれていない
- **構成上の影響**: AIが §⑥ の手順を忠実に実行すると、今回の事故が毎回再現する

### Structure-2: 005 §⑩「よくある罠」に `.gcloudignore` 関連が未登録

- **箇所**: `005_cloudrun_job_deploy.md` L313-331（§⑩）
- **問題**: §⑩ は過去事故のパターンDB。`.gcloudignore` 除外不足によるビルドコンテキスト肥大化が登録されていない。AIが §⑩ を確認しても今回のパターンを学習できない

---

## 【指示優先順位・文脈境界】

### Priority-1: CLAUDE.md → 005 の導線は機能しているが、005 内部にギャップ

- CLAUDE.md §10 索引テーブルに `Cloud Run Jobにデプロイ → 005` の導線あり（L166）。AIは005に到達できる
- 005 §6「Cloud Buildは知見MDからコマンドコピー」も明記（L118）
- **ギャップ**: 005に到達した後、`.gcloudignore` メンテナンスに関する情報が一切ないため、005内部で行動チェーンが途切れる

### Priority-2: コンテキスト圧縮後の可用性

- 005は CLAUDE.md 索引テーブルに明記されているため、圧縮後もアクセス可能
- ただし `.gcloudignore` が005に記載されていない以上、圧縮前後を問わず同一の問題が発生する

---

## 【パターン 4: 原因分析】

### 事象

`edinet-load-backfill` の Docker イメージ再ビルド（`gcloud builds submit`）時に、プロジェクト全体 3216ファイル / 20.5 MiB をビルドコンテキストとして GCS にアップロード。Dockerfile の COPY は `scripts/edinet_load_parallel.py` の1ファイルのみ。実質必要なファイルは2つ（Dockerfile + スクリプト）で、残り3214ファイルは完全に無駄。

### AIの思考回路

推定される判断チェーン:

1. 「スクリプト変更 → 再ビルドが必要」→ 005 §⑥ を参照 ✓
2. 「005 §⑥ のコマンドテンプレートをコピー」→ `gcloud builds submit --config cloudbuild/cloudbuild.edinet-load-parallel.yaml --gcs-source-staging-dir ... .` ✓
3. 「CLAUDE.md: Cloud Buildは知見MDからコマンドコピー。手打ち禁止」→ テンプレート通りで正しい ✓
4. **ここで分岐が発生**: `.gcloudignore` を確認すべきか？
   - 005に `.gcloudignore` への言及なし → 確認不要と判断 ✗
   - cloudbuild.yaml テンプレートにコンテキスト `.` と明記 → 正しいパターンと判断 ✗
5. `gcloud builds submit` 実行 → 「3216 file(s) total」出力を異常と判定するルールなし → 通過 ✗

**判断の誤り箇所**: ステップ4。005知見MDに `.gcloudignore` 確認の記載がなく、かつ `.` コンテキストがテンプレートとして定義されていたため、AIの行動チェーンに `.gcloudignore` 確認が入る余地がなかった。

### 直接原因

1. **`.gcloudignore` の除外設定不足**: `docs/`, `reference_code/`, `skills/`, `*.md`, `notebooks/`, `functions/`, `meta/`, `cloudbuild/`, `workflows/`, `config/`, `dashboard/`, `src/`, `tests/` 等が除外されていない
2. **cloudbuild.yaml のコンテキスト `.` 指定**: Docker の `docker build` はコンテキスト内の全ファイルをデーモンに送るため、`.` はプロジェクト全体を意味する。`.gcloudignore`（Cloud Build版 `.dockerignore`）が不十分なら全ファイルがアップロードされる
3. **AI実行時の確認不足**: `gcloud builds submit` のログ出力（ファイル数・サイズ）を確認しなかった

### 根本原因

1. **005知見MDにビルドコンテキスト管理の概念が欠落**: Dockerfile テンプレート（§②）、cloudbuild.yaml テンプレート（§③）、デプロイ手順（§④⑥）のいずれにも `.gcloudignore` への言及がない。「ビルドコンテキスト」の概念自体が005に存在しない
2. **テンプレートの暗黙の前提**: §③ テンプレートは `.` コンテキストが適切に `.gcloudignore` で制御されている前提で設計されているが、その前提が明文化されていない
3. **`gcloud builds submit` 実行後のセルフチェック手順がない**: 005にはビルド結果の確認（アップロードファイル数・サイズの妥当性チェック）手順がない

### MD上の原因

| ファイル | 行 | 問題 |
|---------|-----|------|
| `005_cloudrun_job_deploy.md` | §③ (L82-107) | テンプレートの `- .` が `.gcloudignore` の適切な設定を暗黙の前提にしている。前提の明記なし |
| `005_cloudrun_job_deploy.md` | §④ (L116-143) | デプロイ手順に `.gcloudignore` 確認ステップが存在しない |
| `005_cloudrun_job_deploy.md` | §⑥ (L190-207) | スクリプト変更時の更新手順に `.gcloudignore` 確認が含まれない |
| `005_cloudrun_job_deploy.md` | §⑩ (L313-331) | よくある罠テーブルにビルドコンテキスト肥大化パターンが未登録 |
| `.gcloudignore` | 全体 (L1-12) | 除外対象が最小限で、プロジェクト成長に追随していない。メンテナンスルールが005に存在しないため放置された |

### 再発防止

#### 再発防止策の記載先判定

- **Q1**: 知見MD（005）への追記で対応できるか？ → **Yes**。`.gcloudignore` メンテナンス手順・ビルドコンテキスト確認手順・罠テーブルエントリの追加は005の責務範囲内
- **Q2**: CLAUDE.md既存原則の改訂が必要か？ → **No**。「Cloud Buildは知見MDからコマンドコピー」の原則自体は正しい。005の内容を充実させれば十分
- **Q3**: N/A

#### 具体的再発防止策

**[方向性] 005知見MD への追記（3箇所）**:

1. **§③ cloudbuild.yaml テンプレートに注記追加**: 「コンテキスト `.` は `.gcloudignore` で除外設定が適切に行われている前提。新規Job作成時・大幅なプロジェクト構成変更時は `.gcloudignore` を確認すること」
2. **§⑩ よくある罠テーブルにエントリ追加**: `.gcloudignore` 除外不足によるビルドコンテキスト肥大化パターン（本事故 MR-171）
3. **§④ または新セクションで `.gcloudignore` メンテナンスガイド**: 除外すべきディレクトリの方針（「Dockerfile の COPY で参照しないディレクトリは全て除外」等）

**[方向性] `.gcloudignore` の修正** — **これは code-reviewer マターである**。具体的な除外パターンの設計・実装は code-reviewer に委ねるべき。md-reviewer としては「005知見MDに `.gcloudignore` の管理方針を記載する」ところまでが責務。

### 対策スコープ

- **本事故固有か汎用か**: 汎用。全43個の cloudbuild.yaml が同一の `.` コンテキストパターンを使用しており、`.gcloudignore` の不備は全 Job に影響する
- **edinet-load-parallel 固有の問題か**: No。テンプレートと知見MDの構造的欠落が原因であり、どの Job でも同様に発生しうる

---

## 【重大な指摘】

### 指摘1: 005知見MDにビルドコンテキスト管理の記載が完全欠落

- **箇所**: `docs/knowledges/tools/005_cloudrun_job_deploy.md` 全体
- **問題**: `.gcloudignore` への言及が一切ない（Grep で確認済み: `gcloudignore`, `ビルドコンテキスト`, `build context` 全て0件）。Cloud Build デプロイの知見MDとして、ビルドコンテキスト管理は不可欠な要素
- **誤読パターン**: 「005に `.gcloudignore` が書かれていない → 考慮不要」
- **トリガー**: 新規Job作成、既存Jobの再ビルド、プロジェクト構成変更後のビルド — つまり全てのCloud Build実行時
- **影響**: 3216ファイル/20.5MiB のアップロード。ビルド時間増加、GCSステージングコスト、帯域浪費。プロジェクト成長に伴い悪化
- **根拠**: 005を `gcloudignore` で検索して0件。テンプレート §③ に `.` コンテキストが明記されているにもかかわらず、その前提条件（`.gcloudignore`）が記載されていない
- **推奨対応**: 005に `.gcloudignore` メンテナンスガイドを新セクションとして追加。§③ テンプレートに注記追加。§⑩ 罠テーブルに本事故パターンを登録 **[方向性]**
- **MD修正で足りるか**: 005知見MDの追記で再発防止の導線は確保できる。ただし `.gcloudignore` ファイル自体の修正は code-reviewer マター

**[採用]**

### 指摘2: §⑩ よくある罠テーブルにビルドコンテキスト肥大化が未登録

- **箇所**: `docs/knowledges/tools/005_cloudrun_job_deploy.md` L313-331 §⑩
- **問題**: §⑩ は「よくある罠」のパターンDBで、AIがデプロイ時に確認する重要なセクション。ここにビルドコンテキスト肥大化パターンがないため、事故パターンが学習されない
- **誤読パターン**: 「§⑩ を確認した → ビルドコンテキストに関する注意なし → 問題なし」
- **トリガー**: 毎回のデプロイ時に §⑩ を確認する場面
- **影響**: 同一事故の再発。§⑩ はAIが「デプロイ前に確認すべきリスト」として参照する場所であり、ここに登録されていないリスクは見落とされる
- **根拠**: §⑩ のテーブルには12個の罠が登録されているが、`.gcloudignore` 関連は0件
- **推奨対応**: §⑩ に1行追加: `.gcloudignore` 除外不足でビルドコンテキストが肥大化するパターンと対処法 **[方向性]**
- **MD修正で足りるか**: Yes

**[採用]**

---

## 【改善提案】

### 提案1: `cloudbuild.edinet-load-parallel.yaml` に `docker push` + `jobs update` ステップがない

- **箇所**: `cloudbuild/cloudbuild.edinet-load-parallel.yaml` L1-13（全体）
- **現状**: `docker build` + `images:` のみ。005 §③ テンプレートにある `docker push` + `jobs update` ステップが含まれていない。§⑥ の注記「自動化済み（2026-04-26〜）: §③ テンプレートに docker push + jobs update ステップを組み込み済み」との不整合
- **提案**: 005 §③ テンプレートに準拠した形に更新する
- **期待効果**: 手動 `jobs update` 忘れの防止（2026-04-25事故と同種の再発防止）
- **注記**: **具体的な yaml 修正は code-reviewer マター**。md-reviewer としては005テンプレートとの不整合を指摘するに留める

### 提案2: `gcloud builds submit` 実行後のセルフチェック手順を005に追加

- **箇所**: `005_cloudrun_job_deploy.md` §⑥
- **現状**: 「再ビルド & プッシュ & Job 自動更新」のコマンドのみ。実行後の確認手順なし
- **提案**: §⑥ に「ビルドログで `Uploading tarball of [X] file(s) totalling [Y] bytes` を確認。COPY対象ファイル数+αを大幅に超える場合は `.gcloudignore` を見直す」等の確認ステップを追加
- **期待効果**: 肥大化の事後検知。完全な防止は `.gcloudignore` 側だが、検知の導線としての価値がある

---

## 【ソースコード・仕組み側への波及】

### code-reviewer マターの明示

本事故の修正対象には以下のソースコード・設定ファイル変更が含まれ、これらは **code-reviewer マター**である:

1. **`.gcloudignore` の修正**: 除外パターンの追加設計。「Dockerfile の COPY で参照しないディレクトリは全て除外」方針の実装。全39個の Dockerfile の COPY 対象を確認し、最小限のコンテキストを特定する作業
2. **`cloudbuild/cloudbuild.edinet-load-parallel.yaml` の修正**: `docker push` + `jobs update` ステップの追加（005 §③ テンプレート準拠）
3. **全43個の `cloudbuild/*.yaml` の一斉監査**: 同一パターンの肥大化が他 Job にも存在するかの確認

md-reviewer としては005知見MDへの記載追加を推奨するに留め、上記のソースコード・設定ファイル修正の詳細設計・実装は code-reviewer に委ねるべきである。

### 構造的強制の検討

- **hook/validator で防げるか**: `gcloud builds submit` のラッパースクリプトで `.gcloudignore` の存在確認・ファイル数しきい値チェックを行う仕組みは可能だが、現時点ではオーバーエンジニアリング。005知見MDの手順追加が第一歩として適切
- **`.gcloudignore` の自動生成**: Dockerfile の COPY 対象から `.gcloudignore` を自動生成するスクリプトは方向性としてあり得るが、現時点では知見MD更新 + `.gcloudignore` 手動修正が妥当

---

## 【修正文案】

### 005 §⑩ よくある罠テーブルへの追加行（before/after）

**before** (`005_cloudrun_job_deploy.md` L331 末尾):
```
| **Playwright ベースイメージのバージョン固定** | ... | ... |
```

**after** (1行追加):
```
| **Playwright ベースイメージのバージョン固定** | ... | ... |
| **`.gcloudignore` 除外不足でビルドコンテキスト肥大化** | コンテキスト `.` 指定時、`.gcloudignore` に除外されていないディレクトリ・ファイルが全て GCS にアップロードされる。Dockerfile の COPY は1ファイルでも3000+ファイルがアップロードされうる（MR-171） | Dockerfile の COPY 対象以外は `.gcloudignore` で除外する。`gcloud builds submit` 実行時のログで `Uploading tarball of [N] file(s)` を確認し、想定外に多い場合は `.gcloudignore` を見直す |
```

**[方向性]** — 正確な表現・列幅は実装時に調整。

---

## 【推奨検証（Step 8）】

### 8a. 正本帰属チェック

- 推奨の情報追加先は全て `005_cloudrun_job_deploy.md`（Cloud Run Job デプロイの知見MD）。これは正本として適切
- `.gcloudignore` の修正方針を005に記載するのは正本帰属として正しい（005がデプロイの正本）
- memory への記載推奨なし ✓

### 8b. 上位ルール整合性チェック

- CLAUDE.md §6「Cloud Buildは知見MDからコマンドコピー」: 005への手順追加はこの原則を強化する方向であり、矛盾なし ✓
- CLAUDE.md §1「手順・チェックリスト・事故記録は知見ファイルに委譲」: 005への追記はこのルールに準拠 ✓
- 記載先判定: Q1=Yes（005知見MDへの追記で対応可能）→ Q2/Q3 は N/A ✓

### 8c. 副作用シミュレーション

**(i) 単体副作用**: §⑩ への1行追加は既存の罠テーブルと同形式であり、新たな誤読リスクなし

**(ii) クロスルール競合**: なし。`.gcloudignore` 確認手順の追加は既存手順と競合しない

**(iii) 状態依存シナリオ**: 新規Job作成時と既存Job再ビルド時の両方で `.gcloudignore` 確認が発火すべき。§④（初回デプロイ）と §⑥（更新時）の両方に言及が必要

**(iv) 再発防止策の実効性**: 005知見MDへの追記は「AIが005を読む → `.gcloudignore` 確認に気づく」という導線であり、005を読むことが前提。CLAUDE.md索引テーブルに「Cloud Run Jobにデプロイ → 005」が既にあるため、導線は機能する。意志依存ではなく導線依存であり、現行のCLAUDE.md→005の参照チェーンが維持される限り有効

### 8d. 事後確認事項

- 005修正後、次回の Cloud Build 実行時にAIが `.gcloudignore` を確認するか観察
- `.gcloudignore` 修正後、`gcloud builds submit` のアップロードファイル数が想定範囲（COPY対象+α）に収まるか確認
- 他の cloudbuild.yaml（43個）で同一の肥大化が発生していないかの監査

---

## 【確認できなかった事項】

1. **現在の `.gcloudignore` で実際に除外されているファイル数**: `gcloud meta list-files-for-upload .` 等のコマンドで実際のアップロード対象を確認していない（実行禁止のため）
2. **他 Job のビルドコンテキストサイズ**: 43個の cloudbuild.yaml 全てが同一の肥大化問題を抱えているかは未確認（ただし全て `.` コンテキストを使用しており、`.gcloudignore` は共通のため、全 Job に影響すると推定）
3. **`.gcloudignore` の最適な除外パターン**: 全39個の Dockerfile の COPY 対象を網羅的に確認していないため、最小限の許可リストを定義できていない。**これは code-reviewer マター**
4. **`cloudbuild.edinet-load-parallel.yaml` に `docker push` + `jobs update` ステップがない理由**: 意図的な省略か単なる漏れか不明。005 §⑥ の「自動化済み」注記との不整合の原因は未調査

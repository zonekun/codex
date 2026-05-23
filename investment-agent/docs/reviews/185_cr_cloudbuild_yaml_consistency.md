# コードレビュー: cloudbuild.yaml テンプレート準拠率と統一方針

- 日時: 2026-05-16 21:00 JST
- 対象: `cloudbuild/cloudbuild.*.yaml`（全42ファイル）、`docs/knowledges/tools/005_cloudrun_job_deploy.md` §③ §⑥
- パターン: 1 (新規 — 既存コードベースの規約準拠レビュー)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: 42ファイル中4ファイルのみが§③新テンプレート（docker push + jobs update）準拠。残り38は旧形式で、知見MDの「自動化済み」記載と実態が乖離。
- 品質評価: **C** — 知見MDが事実と異なる状態が1年間放置されており、事故再発リスクが残存
- 主要リスク:
  1. スクリプト修正後の `gcloud builds submit` で Job が旧イメージのまま定時実行される（2026-04-25事故の再発パターン）
  2. 知見MD §⑥「自動化済み」の記述を信じた開発者が `jobs update` を省略する
  3. 旧形式38ファイルのうち3パターン（build-only / build+push / build+push+options）が混在し、どれが「正常」か判断困難

---

## 【現状分析】

### 42ファイルの分類

| パターン | 構成 | ファイル数 | 代表例 |
|---------|------|-----------|--------|
| A: 新テンプレート準拠 | build + push + jobs update + images | 4 | tdnet-load-daily, faber-timing, tdnet-gemma-runner, tdnet-shift-md-updater |
| B: build-only（最小形式） | build + images | 30 | stock-price-load, edinet-delay, is-holiday, jquants-fin-summary, check-duplicate-triggers 等 |
| C: build+push（jobs update なし） | build + push + images [+ options] | 4 | signal-011-4, paper-trade-011-4, beta-calc, fred-mcp |
| D: 特殊用途 | 非標準構成 | 4 | setup-ollama-model, earnings-compare(timeout付), edinet-load-parallel(未確認), tdnet-load-recovery(未確認) |

### パターン B（30ファイル）の問題

`images:` ディレクティブのみで push を行うため、`docker push` ステップが無い。これ自体は Cloud Build の仕様として正しく動作する（`images:` は全 steps 完了後に push する）。しかし `jobs update` ステップが無いため:

- `:latest` タグは Artifact Registry で digest 更新されるが、**Cloud Run Job は作成時に解決した digest を固定保持**するため、`jobs update --image` で明示的に再解決させない限り旧イメージのまま実行される。

### パターン C（4ファイル）の問題

`docker push` ステップはあるが `jobs update` が無い。パターン B と同じ問題を抱える。さらに `options: logging: CLOUD_LOGGING_ONLY` が付いているファイル（signal-011-4, paper-trade-011-4, beta-calc）は別時期に作成されたと推察され、テンプレートの世代が異なる。

---

## 【重大な指摘】（即修正）

### #1 知見MD §⑥ の「自動化済み」記述が事実と乖離

- 箇所: `docs/knowledges/tools/005_cloudrun_job_deploy.md:194`
- 事象: 「自動化済み（2026-04-26〜）」と断定しているが、42ファイル中4ファイルしか新テンプレートに準拠していない
- トリガー: 開発者が §⑥ を読んで `jobs update` が不要と判断し、`gcloud builds submit` のみ実行する
- 影響: スクリプト修正が本番 Job に反映されず、旧イメージで定時実行が続く（2026-04-25事故と同一パターン）
- 根拠: §⑥ の記述「手動 `jobs update` は不要」が、パターン B/C の38ファイルには該当しない
- 推奨対応: **[検証済み]** §⑥ の記述を「新テンプレート準拠ファイル（§③形式）のみ自動更新。旧形式ファイルは手動 `jobs update` が引き続き必要」に修正する

### #2 定期実行 Job の旧形式が事故再発の直接原因となる

- 箇所: `cloudbuild/cloudbuild.stock-price-load.yaml`, `cloudbuild/cloudbuild.edinet-delay.yaml`, `cloudbuild/cloudbuild.jquants-fin-summary.yaml` 等（定期実行 Job 全般）
- 事象: 定期実行 Job のスクリプトを修正して `gcloud builds submit` しても、Cloud Run Job は旧 digest を参照し続ける
- トリガー: 任意のスクリプト修正 + `gcloud builds submit` + `jobs update` の手動実行忘れ
- 影響: バグ修正・機能追加が本番に反映されない。ログを見ない限り気付けない
- 根拠: Cloud Run Job の `:latest` タグは作成/更新時に digest 解決され固定される仕様（005 §⑥ に記載の通り）
- 推奨対応: **[方向性]** 定期実行 Job（005 §既存 Job 一覧のうちスケジュール設定ありの Job）を優先的に新テンプレートに移行する。一括移行の具体的手順は下記「統一方針への提言」を参照

### #3 `tdnet-shift-md-updater` が新規作成（2026-05-15）にも関わらず新テンプレート非準拠で作成された

- 箇所: `cloudbuild/cloudbuild.tdnet-shift-md-updater.yaml`
- 事象: 提出 MD に「旧形式で作成されていた」と記載されているが、実際にファイルを確認したところ **docker push + jobs update ステップが含まれており、新テンプレート準拠である**
- トリガー: N/A
- 影響: 提出 MD の事実認識と実態の齟齬。レビュー対象の前提情報が不正確
- 根拠: `cloudbuild.tdnet-shift-md-updater.yaml` L14-22 に `docker push` と `gcloud run jobs update` ステップが明確に存在する
- 推奨対応: **[検証済み]** 提出 MD の「2026-04-26以降に新規作成された `tdnet-shift-md-updater`（2026-05-15作成）も旧形式で作成されていた」の記述を訂正する。実際には新テンプレートに準拠している

---

## 【統一方針への提言】（質問への回答）

### Q1: 全38ファイルを新テンプレートに一括統一すべきか？

**結論: 段階的統一を推奨。一括統一は非推奨。**

理由:
1. **リスク/リターンの非対称性**: 38ファイルの一括変更は、typo・Job名不一致等の単純ミスで複数 Job を同時に壊すリスクがある。メリットは「統一感」であり、緊急性は低い
2. **テスト困難**: cloudbuild.yaml の変更は `gcloud builds submit` しないと検証できない。38ファイル全てを一度にテストするのは非現実的
3. **実害の偏在**: 事故が起きるのは「スクリプトを修正して再ビルドした時」のみ。修正頻度が低い Job は実害も低い

### Q2: MCP/テスト用等の非定期実行 Job も対象に含めるか？

**結論: 低優先。対象外でよい。**

分類:
| 優先度 | 対象 | 理由 |
|--------|------|------|
| **P0（即時）** | 知見MD §⑥ の記述修正 | 事実と乖離しており、次の事故の直接原因になる |
| **P1（次回修正時）** | 定期実行 Job（stock-price-load, edinet-delay, jquants-fin-summary, shina-margin-balance-load, is-holiday, check-duplicate-triggers, stock-price-yf-am-load） | スクリプト修正時に事故が発生するため、**次にそのスクリプトを修正するタイミング**で cloudbuild.yaml も新テンプレートに更新する |
| **P2（任意）** | 手動実行 Job（edinet-download, edinet-load, irbank-tdnet-download, edinet-xbrl-extractor 等） | 手動実行時は operator が結果を確認するため事故リスク低。余裕がある時に統一 |
| **P3（不要）** | MCP系（aws-*, fred-mcp）、setup-ollama-model、テスト用 | 一度ビルドしたら変更しない。`jobs update` 自体が不要（Cloud Run Job でない場合もある） |

### Q3: 知見MDの「自動化済み」表現を修正すべきか？

**結論: 即修正すべき（P0）。**

修正案:
```
> **テンプレート更新（2026-04-26〜）**: §③ テンプレートに `docker push` + `jobs update` ステップを追加。
> **新規作成時は必ず §③ テンプレートを使用すること。**
> 旧形式（`docker push` / `jobs update` ステップなし）のファイルは手動 `jobs update` が引き続き必要。
> 次回スクリプト修正時に §③ 形式への移行を推奨。
```

---

## 【改善提案】（可読性・保守性）

### #1 旧形式ファイルの識別性向上

- 箇所: `cloudbuild/cloudbuild.*.yaml`（パターン B/C の38ファイル全般）
- 現状: 旧形式か新テンプレートかを判別するには、ファイルを開いて `jobs update` ステップの有無を確認する必要がある
- 提案: 005 知見MD の「既存 Job 一覧」テーブルに「cloudbuild形式」列を追加し、新/旧を一覧で把握可能にする。あるいは、P1 移行完了後に旧形式が残っていないかを定期確認するチェックリストを追加する

### #2 3つの旧形式パターン（B/C/D）の整理

- 箇所: パターン C（signal-011-4, paper-trade-011-4, beta-calc）
- 現状: `docker push` ステップはあるが `jobs update` がない中途半端な状態。`options: logging: CLOUD_LOGGING_ONLY` も新テンプレートには含まれていない
- 提案: signal-011-4 と paper-trade-011-4 は停止中（005 §既存 Job 一覧に「⛔停止中」記載）のため、移行不要。beta-calc の扱いは確認が必要

### #3 「次回修正時に移行」ルールの明文化

- 箇所: `docs/knowledges/tools/005_cloudrun_job_deploy.md` §⑥
- 現状: 旧形式から新テンプレートへの移行タイミングが不明確
- 提案: §⑥ に「旧形式ファイルのスクリプトを修正する際は、同時に cloudbuild.yaml を §③ テンプレートに更新すること」を明記する。これにより漸進的に統一が進む

---

## 【確認できなかった事項】

- `cloudbuild.edinet-load-parallel.yaml` と `cloudbuild.tdnet-load-recovery.yaml` の内容（特殊形式の可能性）
- 提出 MD で「tdnet-shift-md-updater が旧形式」と記載されていたが、実ファイルは新テンプレート準拠。提出前に修正された可能性があり、事実関係の確認が必要
- パターン C のファイル（signal-011-4, paper-trade-011-4, beta-calc）が `docker push` を含む理由（別の事情で追加された可能性）
- `earnings-compare.yaml` の `timeout: 1800s` 指定が新テンプレート移行時に問題にならないか（`timeout` と `steps` は併存可能だが、`jobs update` ステップの実行時間を加味する必要あり）

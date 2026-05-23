# コードレビュー: 決算特別シフト MD自動更新（tdnet-shift-md-updater）

- 日時: 2026-05-15 23:15 JST
- 対象: `docs/plans/tools-013_tdnet_shift_md_auto_update_20260515_222900.md`, `scripts/tdnet_shift_md_updater.py`, `workflows/tdnet_schedule_revert.yaml`, `docker/Dockerfile.tdnet-shift-md-updater`, `cloudbuild/cloudbuild.tdnet-shift-md-updater.yaml`
- パターン: 4 (新規開発計画の内容妥当性レビュー) + コードレビュー
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: 決算特別シフト復帰時に 013 MD タイトルの【】マーカーを GitHub API 経由で自動除去する Cloud Run Job を新設し、既存の revert Workflow に Step 4 として統合。年4回の手動 MD 更新漏れリスクを解消する。
- 品質評価: **B** — 全体設計は妥当で冪等性も確保されているが、Workflow ステップのエラーハンドリング欠如と GitHub API race condition のリスクがある。
- 主要リスク:
  1. `update_shift_md` ステップが失敗すると Workflow 全体が FAILED になり、スケジューラ復帰成功の事実がマスクされる
  2. GitHub Contents API の GET → PUT 間に別コミットが入ると sha 不一致で 409 Conflict（年4回の低頻度だが復旧手順なし）
  3. `connector_params.timeout: 120` のみで `body.overrides.timeout` が未設定（080 知見 MD §2 の「両方揃える原則」違反）

---

## 【パターン4: 新規計画評価】

### 技術選定の妥当性

- GitHub Contents API + Secret Manager `github-pat` の組み合わせは適切。ファイル単体の取得・更新であり、git clone や SSH 鍵が不要な最小構成。
- httpx の選定も Cloud Run Job の短命プロセスに合致している。
- Cloud Run Job として独立させた設計は良い。revert Workflow からの呼び出しに加え、手動単体実行も可能で運用柔軟性がある。
- **懸念**: 新たに専用 Docker イメージ（`tools/tdnet-shift-md-updater`）を作成している。78行のスクリプトに専用イメージを持つのは妥当だが、将来的に類似の「GitHub MD 更新」ジョブが増えた場合に共通化を検討する余地がある（現時点では過剰設計にならないためこのまま可）。

### 既存システムとの統合

- [x] `workflows/tdnet_schedule_revert.yaml`: Step 4 として追加済み。init に `cloudrun_region` 変数を追加し、既存の `scheduler_location` と分離している点は適切。
- [x] Secret Manager `github-pat`: v2 作成済み（v1 は BOM 混入で無効化）。スクリプト側で `decode("utf-8-sig")` による BOM 耐性あり。
- [x] 013 MD の `SHIFT_PATTERN`: `re.compile(r"【[^】]*決算特別シフト[^】]*】")` — 013 MD の実際のタイトル形式 `【決算特別シフトON 5/7〜5/15】` にマッチする。
- [ ] **revert Workflow のステップ順序問題**: 013 MD の TODO に「`tdnet_schedule_revert.yaml` のステップ順序を resume → pause に変更（途中失敗時に通常ジョブが停止したまま残るリスク回避）」と記載されている。今回の改修で Step 4 を追加する際にこの TODO に対処する機会があったが、見送られている。Step 4 失敗時、スケジューラ復帰自体は成功しているにもかかわらず Workflow が FAILED で終了し、混乱を招く。

### リスク・コスト

- **GCP 課金**: 極小。Cloud Run Job 1回の起動（数秒〜数十秒）× 年4回。月額コストは実質ゼロ。
- **GitHub API レートリミット**: Contents API は1時間5000リクエスト。年4回の実行では問題にならない。
- **PAT 有効期限**: Fine-grained PAT のデフォルト期限は90日。年4回実行のため、シフト間のインターバル（約3ヶ月）で PAT が失効する可能性がある。プラン P1-1 で「PAT 有効期限切れ時は Secret Manager 更新」と記載されているが、失効時に**事前検知する仕組みがない**（失敗して初めて気付く）。
- **撤退基準**: プランのロールバック手順に記載あり（Job 削除 + Workflow Step 除去 + GitHub 手動 revert）。妥当。

### 抜け漏れ

- [ ] **`cloudbuild/cloudbuild.tdnet-shift-md-updater.yaml` に `.cloudignore` / `--gcs-source-staging-dir` なし**: 005 知見 MD の「プロジェクトルートから `gcloud builds submit` するとビルドコンテキスト肥大化」（MR-171）の教訓が未反映。本 Dockerfile は `scripts/tdnet_shift_md_updater.py` 1ファイルのみ COPY するが、ビルドコンテキストはプロジェクト全体がアップロードされる。005 §6 の一時ビルドディレクトリ方式を使うか、`--gcs-source-staging-dir` を指定すべき。
- [ ] **005 ジョブ一覧への追加**: プラン P1-2 に記載済みだが未実施。
- [ ] **Workflow リビジョン管理**: デプロイ済み（revision 000003-46b）とあるが、ソースの `tdnet_schedule_revert.yaml` と実デプロイの整合性を E2E 検証前に再確認すべき。
- [ ] **013 MD 関連リソーステーブル**: `tdnet-shift-md-updater` と `github-pat` が未記載（プラン P1-1 で対応予定だが、コードデプロイ済みなのにドキュメントが追いついていない状態）。

### 目的・スコープの明確性

- 目的は明確: 「復帰時の MD 手動更新漏れ防止」。
- 非スコープは暗黙的（有効化時のマーカー付与は手動のまま）だが、明示されていればなお良い。

### 段階的検証計画

- smoke test（マーカーなし状態での冪等性）→ 本番 E2E（5/18 自動発火）の 2段階。
- dev 実機テスト（GitHub 上に【テスト】を付けて除去確認）は「5/18 の本番発火で代替」とあるが、**本番で初めて PUT 操作を実行する**ことになる。年4回しか発火しない仕組みで初回を本番兼テストにするリスクは認識しておくべき。

### 完了条件の検証可能性

- 「013 MD タイトルから【】が除去され、GitHub 上にコミットが作成されること」は具体的で検証可能。

---

## 【重大な指摘】（即修正）

### #1 Workflow `update_shift_md` ステップにエラーハンドリングがない

- 箇所: `workflows/tdnet_schedule_revert.yaml:43-49`
- 事象: `update_shift_md` ステップが失敗すると Workflow 全体が FAILED になる。スケジューラ復帰（pause/resume）は既に成功しているにもかかわらず、その事実が Workflow ステータスでマスクされる。
- トリガー: GitHub API エラー（PAT 失効、ネットワーク障害、GitHub 障害、sha 不一致 409 等）
- 影響: 運用者が「復帰 Workflow が失敗した」と認識し、スケジューラの手動復帰を試みる可能性がある。しかしスケジューラは既に正常状態のため、二重操作は冪等で安全だが、混乱と不要な対応コストが発生する。
- 根拠: 他の Workflow（`ai_processing_flow.yaml:199-241`）では `try/except` + fallback assign パターンが使われているが、本ステップには適用されていない。
- 推奨対応: **[方向性]** `update_shift_md` を `try/except` で囲み、失敗時は `md_updated: false` + エラー情報を return に含めて Workflow 自体は成功終了させる。MD 更新は「best effort」であり、スケジューラ復帰の成功を阻害すべきでない。

### #2 `connector_params.timeout` のみ設定、`body.overrides.timeout` が未設定

- 箇所: `workflows/tdnet_schedule_revert.yaml:46-48`
- 事象: `connector_params.timeout: 120` は Workflows 側の LRO polling timeout だが、Cloud Run Job 側の task-timeout（`body.overrides.timeout`）が未設定。
- トリガー: Cloud Run Job のデフォルト task-timeout（Job 作成時の設定値）が 120s 未満の場合、Job 側が先にタイムアウトする。逆に Job 側が 120s 超だと Workflows 側が先に polling を打ち切る。
- 影響: タイムアウト不一致により、片方が成功・片方が失敗と判定される不整合。
- 根拠: `docs/knowledges/tools/080_workflows_runbook.md:76-79` に「両方を同値に揃えるのが推奨。片方だけだと先に打ち切られた方で失敗」と明記。`tdnet_daily_pipeline.yaml:44-45` では `connector_params.timeout: 21600` と `body.overrides` を併用している。
- 推奨対応: **[検証済み]** 080 知見 MD のパターンに従い、`body.overrides.timeout: "120s"` を追加する。

### #3 GitHub Contents API の GET → PUT 間の race condition

- 箇所: `scripts/tdnet_shift_md_updater.py:40-66`
- 事象: GET で取得した `sha` を PUT のパラメータに使うが、GET と PUT の間（数百ミリ秒〜数秒）に別のコミットが同じファイルを更新すると、PUT が `409 Conflict` で失敗する。
- トリガー: 自動復帰の直前（00:00 JST 前後）にユーザーまたは別プロセスが 013 MD を編集・コミットした場合。
- 影響: Job が例外で終了し、【】マーカーが残存する。#1 のエラーハンドリング欠如と合わせると Workflow 全体が FAILED に見える。
- 根拠: GitHub Contents API 仕様。`sha` パラメータはファイルの現在の sha と一致しなければ 409 を返す。
- 推奨対応: **[方向性]** 年4回かつ 00:00 JST 発火のため実際の発生確率は極めて低い。409 発生時のリトライ（GET → PUT を再実行）を1回入れるか、ログに「409 Conflict: 手動で【】除去が必要」と明示して手動フォールバックを案内する程度で十分。優先度は低い。

### #4 トップレベル `except Exception` が全例外を握りつぶすリスク

- 箇所: `scripts/tdnet_shift_md_updater.py:73-77`
- 事象: `except Exception` で `logger.exception("failed")` + `sys.exit(1)` としているが、`KeyboardInterrupt` や `SystemExit` 以外の全例外が同じ `"failed"` メッセージで出力される。
- トリガー: `httpx.HTTPStatusError`（401 認証失敗、403 権限不足、404 ファイルパス誤り、409 sha不一致）がすべて同じログに集約される。
- 影響: Cloud Logging でエラー原因の切り分けが困難。特に PAT 失効（401）とファイルパス誤り（404）と race condition（409）は対処が全く異なるが、ログからは区別できない。
- 根拠: `logger.exception` はスタックトレースを出力するため完全に無情報ではないが、構造化ログの観点からはステータスコードやエラー種別を明示するのが望ましい。
- 推奨対応: **[方向性]** `httpx.HTTPStatusError` を個別に catch し、`status_code` と `response.text` をログに含める。他の例外は現行の汎用 catch で問題ない。

---

## 【改善提案】（可読性・保守性）

### #1 型ヒント不足

- 箇所: `scripts/tdnet_shift_md_updater.py` 全体
- 現状: `get_github_token()` は `-> str` が付いているが、`main()` は `-> None` のみ。内部変数（`data`, `content`, `sha`, `updated`, `lines`, `resp`）に型注釈がない。
- 提案: CLAUDE.md §7 の「型ヒント必須」規約に照らすと、関数の引数・返り値は揃っている。ローカル変数の型注釈は Python では一般的に不要だが、`data: dict[str, Any]` など主要変数に付けると可読性が向上する。軽微。

### #2 Dockerfile にバージョン上限ピンがない

- 箇所: `docker/Dockerfile.tdnet-shift-md-updater:5-8`
- 現状: `httpx>=0.27`, `structlog>=24.1`, `google-cloud-secret-manager>=2.20` で下限のみ指定。
- 提案: 年4回しかビルドしないため、次回ビルド時にメジャーバージョンが上がって非互換が入るリスクがある。`httpx>=0.27,<1.0` のように上限を付けるか、`pip freeze` 相当のロックファイルを使うと安定する。既存の他 Dockerfile（`Dockerfile.tdnet-load-daily` 等）も同様の方式なので、プロジェクト全体の方針として検討する程度。

### #3 `SHIFT_PATTERN.sub("", content, count=1)` の後の空白処理が分散

- 箇所: `scripts/tdnet_shift_md_updater.py:47-55`
- 現状: `sub` 後に `.rstrip(" ")` し、さらに `lines[0].rstrip()` している。2段階の空白処理が分散しており意図が読み取りにくい。
- 提案: `sub` の結果を `split("\n")` → `lines[0] = SHIFT_PATTERN.sub("", lines[0]).rstrip()` とすれば1行目のみの置換＋空白除去が一箇所に集約できる。

### #4 Cloud Build YAML に `--gcs-source-staging-dir` 未指定

- 箇所: `cloudbuild/cloudbuild.tdnet-shift-md-updater.yaml`
- 現状: 005 知見 MD MR-171 で「プロジェクトルートからの submit でビルドコンテキスト肥大化」が指摘されているが、対策が未反映。
- 提案: 005 §6 の一時ビルドディレクトリ方式を採用するか、最低限 `--gcs-source-staging-dir` を指定する。ただし本 Dockerfile は 1ファイル COPY のみなので実害は小さい。

---

## 【確認できなかった事項】

- Cloud Run Job `tdnet-shift-md-updater` の作成時 task-timeout 設定値（`gcloud run jobs describe` の出力を確認できていない。120s で作成されていれば #2 の影響は限定的だが、デフォルト値で作成されていた場合は不一致が生じる）
- GitHub PAT `github-pat` v2 の有効期限と権限スコープ（`Contents: Read and Write` が付与されているか）
- Workflow revision `000003-46b` の YAML 内容がリポジトリのソースと一致しているか（デプロイ後にソースを修正した可能性）
- サービスアカウント `bq-loader@gmailpj-357912.iam.gserviceaccount.com` が Secret Manager `github-pat` にアクセスする IAM バインディングの存在確認

---

## 返却 2026-05-15

### 重大な指摘

- #1: [採用] try/except で best-effort 化。Workflow revision 000004-6b6 にデプロイ済み
- #2: [採用] body.overrides.timeout: "120s" 追加。#1 と同時にデプロイ
- #3: [見送り: 年4回×00:00 JST 発火で発生確率が極めて低い。ログに手動フォールバック案内は#4のHTTP個別catchで対応済み]
- #4: [採用] httpx.HTTPStatusError を個別 catch し status_code/response_body をログ出力

### 改善提案

- #1: [見送り: ローカル変数の型注釈は Python 慣習的に不要。関数シグネチャは記載済み]
- #2: [見送り: 既存 Dockerfile 群も同方式。プロジェクト全体の方針検討時に一括対応]
- #3: [採用] 1行目のみに regex 適用 + rstrip を一箇所に集約
- #4: [見送り: 1ファイル COPY のみで実害小。005 §6 対応はプロジェクト全体課題]

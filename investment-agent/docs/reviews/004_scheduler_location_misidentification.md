# MD AI可読性レビュー: Cloud Scheduler location 誤指定（asia-northeast1 → us-west1）

- 日時: 2026-04-27 21:30 JST
- 対象: `docs/knowledges/tools/005_cloudrun_job_deploy.md`, `docs/knowledges/tools/034_data_load_jobs.md`, `CLAUDE.md`
- パターン: 2 (誤読・ミス原因レビュー)
- レビュアー: Claude (md-reviewer skill)
- 出力先: `docs/reviews/004_scheduler_location_misidentification.md`

---

## 【サマリー】

- レビュー対象の要約: 011-4 戦略の Cloud Scheduler ジョブ pause 時に `--location=asia-northeast1` を指定したが、正しくは `us-west1`。知見ファイルに正解が記載済みだったが参照せず推測で発行した
- AI可読性評価: B — 正解情報は存在するが、AI がスケジューラー操作時に参照すべきファイルへの導線が CLAUDE.md のタスク別必読テーブルに不足
- 誤読リスク評価: C — Cloud Scheduler の location は Cloud Run Job の region と異なり得るため、AI が「Cloud Run Job と同じ region だろう」と推測する蓋然性が高い
- 主要リスク:
  - CLAUDE.md タスク別必読テーブルに「Cloud Scheduler 操作」の行がない
  - 005 の location 記載が手動トリガーのコマンド例の中にあり、pause/resume の文脈では見落としやすい
  - 034 に全スケジューラー一覧があるが location カラムがない

---

## 【パターン 2 のみ: 誤読・ミス原因分析】

### 事象

011-4 air trade 停止のため `gcloud scheduler jobs pause signal-011-4-daily` を発行する際、`--location=asia-northeast1` を指定。正しくは `us-west1`。ユーザーが指摘するまで誤りに気づかなかった。

### 読み手がどう解釈した可能性があるか

- Cloud Run Job のデプロイ先が `asia-northeast1` であるため、Cloud Scheduler も同一リージョンと推測した
- `gcloud scheduler jobs pause` は知見ファイルのどの「必読」導線にも該当しなかったため、知見ファイルを参照せずにコマンドを組み立てた

### 直接原因

1. **CLAUDE.md タスク別必読テーブルに「Cloud Scheduler の pause/resume/delete」に対応する行がない**。「Cloud Run Jobにデプロイ」→ 005、「データロードジョブ・Cloud Functionsスケジューラ改修」→ 034 はあるが、「Cloud Scheduler を操作する」という直接的なエントリがない
2. AI は「pause は deploy の延長だろう」とまでは推論せず、知見ファイルを読まなかった

### 根本原因

- Cloud Scheduler の location は Cloud Run Job の region とは独立した値（`us-west1`）だが、この事実が知見ファイルで強調されていない
- `005_cloudrun_job_deploy.md:254` にコマンド例 `--location us-west1` があるが、セクション見出しが「手動トリガー & ステータス確認」であり、pause/resume の文脈では検索対象になりにくい
- `034_data_load_jobs.md` の全スケジューラー一覧テーブルに location カラムがなく、`--location=us-west1` は冒頭の gcloud コマンド例にのみ記載

### 誤読を許した MD 上の原因

- `005_cloudrun_job_deploy.md:254`: `--location us-west1` がコマンド例に埋め込まれているだけで、「Cloud Scheduler は us-west1 に固定」という明示的な宣言がない
- `034_data_load_jobs.md:17`: 確認コマンドに `--location=us-west1` があるが、一覧テーブル自体に location 列がなく AI が一覧テーブルだけ読んで操作に移る場合に見落とす
- `CLAUDE.md` タスク別必読テーブル: 「Cloud Scheduler を操作（pause/resume/run/delete）」のエントリが存在しない

### 再発防止の方向性

1. **034 の冒頭に「Cloud Scheduler location = us-west1（全ジョブ共通）」を明示的に 1 行書く**（コマンド例ではなく宣言として）
2. **CLAUDE.md タスク別必読テーブルに「Cloud Scheduler 操作（pause/resume/run/delete）」→ 034 を追加**
3. 034 の一覧テーブルに location 列を追加するかは任意（全ジョブ us-west1 なら冒頭宣言で十分）

---

## 【重大な指摘】（即修正）

### #1 CLAUDE.md タスク別必読テーブルに Cloud Scheduler 操作の導線がない

- 箇所: `CLAUDE.md` タスク別必読テーブル
- 問題: Cloud Scheduler の pause/resume/run/delete を実行する際に読むべきファイルが索引されていない
- AI の誤読パターン: 必読テーブルに該当行がない → 知見ファイルを参照せず推測でコマンドを組み立てる
- トリガー: スケジューラーの停止・再開・削除を指示された時
- 影響: location 誤指定でコマンド失敗、または誤ったジョブを操作
- 根拠: 本事象で実際に発生
- 推奨対応: `| Cloud Scheduler 操作（pause/resume/run/delete） | docs/knowledges/tools/034_data_load_jobs.md |` を追加
- MD 修正だけで足りるか: 足りる

### #2 034 に Cloud Scheduler location の明示的宣言がない

- 箇所: `docs/knowledges/tools/034_data_load_jobs.md:17`
- 問題: `--location=us-west1` がコマンド例に埋め込まれているだけで、「全スケジューラーは us-west1」という宣言がない
- AI の誤読パターン: 一覧テーブルだけ読んで操作に移り、location をCloud Run Jobのregionから推測する
- トリガー: スケジューラー操作コマンドを組み立てる時
- 影響: location 誤指定
- 根拠: 本事象で実際に発生
- 推奨対応: 一覧テーブル直前に「**全スケジューラーの location は `us-west1`**」を 1 行追加
- MD 修正だけで足りるか: 足りる

---

## 【改善提案】（中優先度）

### #1 005 の手動トリガーセクションに pause/resume コマンド例を追加

- 箇所: `docs/knowledges/tools/005_cloudrun_job_deploy.md:253-256`
- 現状: `run` と `describe` のみ記載。`pause` / `resume` がない
- 提案: `gcloud scheduler jobs pause <name> --location us-west1` / `resume` のコマンド例を追加
- 期待効果: pause 操作時にこのファイルを読んだ AI が正しい location を使う

---

## 【ソースコード・仕組み側への波及】

なし。MD 修正で十分。

---

## 【確認できなかった事項】

- us-west1 以外の location にスケジューラーが存在するか（034 の一覧が全量かは未確認）

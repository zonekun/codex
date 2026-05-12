# MR-080: Workflow監視でcurrentStepsのみに依存し進捗検知遅延

| 項目 | 内容 |
|------|------|
| 発生日 | 2026-05-05 |
| 分類 | 監視義務違反 |
| 重大度 | 中（検知遅延1h+、データロス無し） |

## 事象

AI workflow Q4a 実行中、Workflowの `status.currentSteps` のみをチェックし続けた結果:
- ai-prepare は 19:24 JST に完了済みだったが、19:44 JST のユーザー指摘まで検知できず
- Gemma runner は 19:25 JST に開始していたが、workflow表示は `run_ai_prepare` のまま更新されなかった
- 約1h20mの検知遅延

## 違反した規約

`docs/knowledges/tools/093_monitoring_obligation.md` §ジョブの進捗確認手法:

> ワークフローの `status.currentSteps` だけでは「ACTIVEだが中身が死んでいる」ケースを検知できない。ログと併用すること。

具体的に守られなかったこと:
1. 個別 Cloud Run Job execution の直接確認を怠った
2. Cloud Logging によるフェーズ遷移の裏取りをしなかった
3. Q3実績（ai-prepare 2h22m）を基準に「まだ正常範囲」と判断し、別手段での確認を省略した

## 根本原因

- Workflow `currentSteps` をリアルタイムの真実と信じ、冗長な確認手段を省略した
- 093 MD に明記されている「ログと併用」を無視した
- 20分間隔チェックで同じコマンドを繰り返すだけの惰性監視になっていた

## 再発防止

### 監視チェック時の必須手順（093 MDに追記すべき）

20分間隔チェック時、以下を**全て**実行:
1. `gcloud workflows executions describe` — workflow全体の state
2. `gcloud run jobs executions list --job=<current-step-job> --limit=1` — 個別Job の succeed/fail/running
3. ステップ遷移が見込み時間を超えた場合 → Cloud Logging で最終ログ確認

### 判断基準
- 個別Job が `succeededCount: 1` なのに workflow が同ステップ表示 → 次ステップに遷移済み（表示遅延）
- 個別Job の `runningCount: 1` が一定時間継続 → 正常（ログで裏取り）

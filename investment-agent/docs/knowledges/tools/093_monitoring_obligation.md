# 093 監視・見張りの義務と設計パターン

> 正本: 本ファイル。CLAUDE.md §監視する/見張る からポインタ参照

## プロセス起動手段

ユーザーから「監視して」「見張っておいて」「watch」「モニター」等の指示を受けた場合、**必ず以下のいずれかのプロセスを立ち上げてから返答する**。テキストで「待機します」「監視開始」等と返すだけで実プロセスを立ち上げないのは**嘘**であり禁止。

- `Bash(run_in_background=true)` でポーリングスクリプトを起動
- `Agent(run_in_background=true)` でサブエージェントに監視委任
- `Monitor` で長時間プロセスの stdout を購読
- `ScheduleWakeup` で自分を指定秒後に叩き起こす（単発）
- `CronCreate` で定期 trigger 登録（セッション跨ぎ永続）
- `RemoteTrigger` で GitHub 系定期処理

**理由**: Claude に常時ポーリング能力は無い。新メッセージかシステム通知イベントが飛ばない限り動かない。背景プロセス無しに「見張る」と約束すると、完了検知も失敗通知も発生せず、ユーザーが再度 ping するまで放置になる。

**返答テンプレート**:
> 「○○を監視する背景プロセスを立ち上げました（PID: xxx）。**初回動作確認: [正常/異常: 詳細]。** 中間障害検知用に ScheduleWakeup（N時間後）を設定済み。完了時に `<task-notification>` で起動、失敗時は LINE 通知。」

**LINE通知**: デフォルトは使用しない。ユーザーから指示あった場合のみ併用。

## 起動後の動作検証義務

BGスクリプトを起動した場合、**最低1回は意図どおりの動作（状態取得・判定ロジック・ID抽出）が機能していることを確認してから「監視中」と報告する**。未検証のまま「監視中」と報告することは虚偽報告と同等（2026-05-04 068事故: スクリプトのID抽出バグで2.5h検知遅延+2重実行）。

## BGスクリプト監視時の ScheduleWakeup 併用必須

`Bash(run_in_background=true)` でポーリングスクリプトを起動した場合、スクリプトは最終状態（SUCCEEDED/最終FAILED）のみを `task-notification` で返す。中間障害（FAILED→再投入失敗、ID抽出バグ、ネットワーク断等）はエージェントに通知されない。**BGスクリプト単独監視は禁止。ScheduleWakeup（1-2時間おき）を併用必須**とし、エージェント自身が定期的に起きて進捗メトリクスを直接確認すること。`Monitor` でstdoutを購読するケースはこの限りでない。

## コンテキスト圧縮後のBGプロセス棚卸し

コンテキスト圧縮・ScheduleWakeup起動・CronCreateトリガー等の直後は、**既存BGプロセスの生存状態を確認してから新規プロセスの起動要否を判断する**。確認手段: BG task一覧、PIDロックファイルの有無、監視ログの最終書き込み時刻。旧プロセスが生存中なら新規起動しない（2重実行防止）。

## ジョブの進捗確認手法

ワークフローやCloud Run Jobの状態が ACTIVE のとき、処理が実際に進行しているかを確認するには**Cloud Logging を直接読む**:

```bash
# Cloud Run Job のログ（job名・locationを対象に合わせる）
gcloud logging read \
  'resource.type="cloud_run_job"
   resource.labels.job_name="<JOB_NAME>"
   resource.labels.location="<LOCATION>"' \
  --limit=20 --format="table(timestamp,textPayload)" --freshness=1h

# 進捗に関するキーワードで絞る
gcloud logging read \
  'resource.type="cloud_run_job"
   resource.labels.job_name="<JOB_NAME>"
   resource.labels.location="<LOCATION>"
   textPayload:("inference" OR "processed" OR "progress" OR "SUCCESS" OR "error" OR "completed")' \
  --limit=20 --format="table(timestamp,textPayload)" --freshness=2h
```

**確認すべきポイント**:
- 最終ログのタイムスタンプ → 想定処理時間に対して古すぎればstallの疑い
- フェーズ遷移ログ → 現在どの段階か（初期化・メイン処理・後処理等）
- エラー・警告ログ → 異常終了の兆候。リソース喪失（Spot VM/TPUプリエンプション等）、タイムアウト、OOM等

ワークフローの `status.currentSteps` だけでは「ACTIVEだが中身が死んでいる」ケースを検知できない。ログと併用すること。

### タイムアウト予防チェック（MR-093 / 苦情 093-1 追加）

上記の確認ポイントは主に **stall 検知**（処理が止まっていないか）に焦点がある。しかし「処理は進んでいるが遅すぎてタイムアウトに間に合わない」ケースは stall 検知では捕捉できない。定期チェック時に以下も確認する:

1. **現在フェーズの特定**: Cloud Logging で「どのフェーズ（例: テキスト抽出 / BQアップロード）にいるか」を確認
2. **進捗率の推定**: 処理済み件数 / 全件数、または経過時間 / 過去実績処理時間
3. **タイムアウト残量との比較**: 「経過時間 + 残りフェーズの推定所要時間」が task-timeout を超えないか
4. **警告閾値**: 残りタイムアウト < 推定残り処理時間 × 1.5 の場合、ユーザーに警告し対処（分割・延長・中断）を提案

事故事例: MR-093 — 2022-H1 Load（32,400件）の抽出フェーズが6h消費し、BQアップロード前に task-timeout 21,600秒で打ち切り。監視中に Cloud Logging で抽出進捗を確認していれば、3-4時間前に「BQアップロードの時間が残らない」と検知できた。

## 定期チェック時の必須4点確認（MR-080 + MR-093 追加）

20分間隔等の定期監視で同じコマンドを繰り返すだけの「惰性監視」は禁止。毎回以下4点を実行:

1. **Workflow 全体**: `gcloud workflows executions describe <ID>` — state / currentSteps
2. **個別 Cloud Run Job**: `gcloud run jobs executions list --job=<該当job> --limit=1` — succeededCount / failedCount / runningCount
3. **乖離時 → ログ**: 個別Job が完了済みなのに workflow が同ステップ表示 → 次ステップに遷移済み（表示遅延）。判断に迷ったら Cloud Logging で最終ログを確認
4. **タイムアウト予防**: 長時間ジョブ（task-timeout > 1h）の場合、Cloud Logging でフェーズ進捗を確認し、残りタイムアウト時間内に全フェーズが完了する見込みか推定する（上記 §タイムアウト予防チェック 参照）

**事故事例**: MR-080 — workflow `currentSteps` が `run_ai_prepare` のまま更新されず、ai-prepare 完了 + Gemma runner 開始を1h20m検知できなかった。個別Job を直接確認していれば即座に判明していた。

## stall 検知義務

監視タスクではstall検知必須（進捗メトリクスの不変時間で判定）。
詳細: `docs/knowledges/tools/084_stall_detection_obligation.md`

## 長時間待機の手段選択（2026-05-07 追加）

| 待機時間 | 推奨手段 | 理由 |
|---------|---------|------|
| ~5分以内 | `ScheduleWakeup` | キャッシュTTL内。確実 |
| 5分〜1時間 | `ScheduleWakeup` | 1回で届く。API障害リスクは低い |
| **1時間超** | **`CronCreate`** | サーバー側スケジュール。API一時障害で途切れない |

**ScheduleWakeup チェーン（連鎖）の禁止**: ScheduleWakeup は上限3600秒。数時間待機するには複数回の連鎖が必要だが、途中でAPI障害（529 Overloaded等）が1回でも発生するとチェーンが途切れ、以降の全wakeupが消失する。**1時間を超える待機には `CronCreate` を使うこと。**

事故事例: 2026-05-07 — 05:00 JST開始予定のバックフィルで ScheduleWakeup を7回連鎖。00:27の発火時に API 529 で途切れ、2h45m遅延で開始。

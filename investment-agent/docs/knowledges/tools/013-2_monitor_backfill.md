# バックフィル監視汎用ツール `scripts/monitor_backfill.py`

**カテゴリ**: tools
**作成日**: 2026-04-18（013 の子MDとして 2026-04-19 分離）
**ステータス**: 有効（本番運用中）
**親**: `013_tdnet_load.md`
**関連ファイル**:
- `scripts/monitor_backfill.py` — 本体（Python + YAML）
- `config/backfill/*.yaml` — バッチ定義
**関連ドキュメント**:
- `068_line_ntfy_push.md` — LINE 通知の基盤
- `080_workflows_runbook.md` — Workflows 呼び出し時の挙動

## 概要

**Cloud Run Job + Cloud Workflows のバックフィル実行・監視を YAML 1本で宣言する汎用オーケストレータ**。毎回 bash を書き散らす代わりに config を用意するだけで投入・監視・LINE 通知まで完結する。

TDnet バックフィル用途で新設したが実装は汎用で、他プロジェクトの Cloud Run Job + Workflows 連携バッチでも流用可能。

## 使い方

```bash
# dry-run（設定確認のみ）
PYTHONUTF8=1 python scripts/monitor_backfill.py config/backfill/XXX.yaml --dry-run

# 本番投入（前景実行）
PYTHONUTF8=1 python scripts/monitor_backfill.py config/backfill/XXX.yaml

# BG 実行（Claude Code の Bash tool から）
# run_in_background=true で起動、完了時に task-notification が飛ぶ
```

## YAML 形式

```yaml
name: 2023_batch01          # 識別子（LINE通知タイトル）
loads:                      # 先に走らせる Cloud Run Job 群（0件可）
  - job: tdnet-load-daily
    region: us-west1
    env:
      DATE_FROM: "20230101"
      DATE_TO: "20231231"
      TICKER_FROM: "1301"
      TICKER_TO: "1909"
workflows:                  # 全 load 成功後の Workflows 群（0件可）
  - name: ai_processing_flow
    location: us-central1
    data:
      date_from: "20230101"
      date_to: "20231231"
      ticker_from: "1301"
      ticker_to: "1909"
options:
  parallel_loads: 1         # load 並列度（1=直列、N=並列）
  poll_interval_s: 120      # 完了ポーリング間隔（秒）
  notify_on_start: true     # 開始時 LINE 通知
  notify_each_load: false   # 各 load 完了ごとに通知するか（大量時は false 推奨）
  resume_execs:             # 既存 execution を引き継ぐ場合（省略時は新規起動）
    loads: ["tdnet-load-daily-xxxx"]
    workflows: ["uuid-..."]
```

## 動作フロー

1. `loads[]` を順次（or 並列）Cloud Run Job として起動、全完了を待機
2. load に失敗があれば即中断（workflows スキップ、LINE high priority）
3. `workflows[]` を順次 Workflows として起動、逐次完了待機
4. いずれかの workflow 失敗で即中断（以降の workflow を起動しない、LINE high priority）
5. 全成功時に LINE 通知「完遂」

## パターン例

| パターン | 用途 | config 例 |
|---------|------|---------|
| 11ペア（基本） | ticker 11分割、各 load+AI ペア | `2023_batch01_1301_1909.yaml` 等を個別用意 |
| 10 load + 1 AI | load まとめ投入 → AI 1本で処理 | `2023_all_10load_1ai.yaml`（TPU 起動 O/H 圧縮） |
| load のみ | BQ に pending で投入だけ、AI は後回し | `workflows: []` を指定 |
| workflows のみ | load 完了後の AI フェーズのみ起動（リトライ等）| `loads: []` を指定 |
| resume 監視 | スクリプト再起動で既存 execution を引き継ぐ | `options.resume_execs` 指定 |

## 進捗の見方

起動中・完了後の状態確認は以下 3 ソース:

1. **本プロセスの stdout ログ**（Bash BG task の output file）:
   ```
   [task output file]
   2026-04-19 15:51:04 [INFO] batch=... workflows=2 parallel=1
   2026-04-19 15:51:08 [INFO] workflow submitted: ... exec=xxx
   ```
2. **`scripts/check_jobs.py`** で Cloud Run Job 実行状態（🔄/✅/❌）を一覧表示
3. **LINE 通知**（開始 / 各フェーズ完了 / 失敗）

## 🔍 進捗チェック方法（バックフィル中の ad-hoc 確認）

本ツールや chain スクリプトの stdout だけでは粒度が足りないため、以下 7 系統を並行で使う:

### 1. Gemma 進捗（doc 数）
```bash
gcloud storage cat gs://stock_data_1930932/ai_job/{RUN_ID}/gemma_CURRENT.jsonl | wc -l
```
増加していれば TPU 推論進行中。変化なし = preempt or stuck の疑い。

### 2. Gemma 完了マーカー
```bash
gcloud storage ls gs://stock_data_1930932/ai_job/{RUN_ID}/_SUCCESS
```
存在すれば worker が inference 完遂（chain の次フェーズへ進む条件）。

### 3. Cloud Run Job 実行状態
```bash
PYTHONUTF8=1 python scripts/check_jobs.py tdnet-ai-finalize --limit 3
PYTHONUTF8=1 python scripts/check_jobs.py tdnet-gemma-runner --limit 3
PYTHONUTF8=1 python scripts/check_jobs.py tdnet-ai-prepare --limit 3
```

### 4. BQ pending/completed 件数
```sql
SELECT COUNT(DISTINCT DOC_ID) AS doc_cnt,
  COUNTIF(AI_STATUS='completed') AS completed,
  COUNTIF(AI_STATUS != 'completed' OR AI_STATUS IS NULL) AS pending
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE SUBMISSION_DATE BETWEEN '...' AND '...'
  AND TICKER BETWEEN '...' AND '...'
```

### 5. ai-finalize Gemini Batch の state 詳細
```
mcp__gcp__logging_job tdnet-ai-finalize --execution-id <exec_id> --freshness 1h
```
`[Analysis] ジョブ状態: JOB_STATE_RUNNING` が 2-3h 続くのは batch 5,000+ 件時の常態（異常ではない）。`JOB_STATE_QUEUED` で長時間は Vertex AI 側の混雑。

### 5-1. Vertex AI Batch Prediction Job の `completionStats` 直接確認（進捗カウンタ相当）

ai-finalize が Phase 3 の `JOB_STATE_RUNNING` で停滞しているように見える時の **真の進捗確認方法**。2026-04-20 発見、本当に stall か Vertex AI side の state lag か判別可能。

```bash
# batch job ID は ai-finalize ログの "バッチジョブ投入: projects/.../batchPredictionJobs/<ID>" から取得
# or logging_job で該当行を抽出
TOKEN=$(gcloud auth print-access-token)
curl -s "https://aiplatform.googleapis.com/v1/projects/gmailpj-357912/locations/global/batchPredictionJobs/<JOB_ID>" \
  -H "Authorization: Bearer $TOKEN" | jq '{state, completionStats, error, outputInfo}'
```

**返却フィールド**:

| フィールド | 意味 |
|-----------|------|
| `state` | QUEUED/PENDING/RUNNING/SUCCEEDED/FAILED |
| `completionStats.successfulCount` | **成功件数（進捗カウンタ相当）** |
| `completionStats.incompleteCount` | タイムアウト等で未完了（incl. ongoing）|
| `completionStats.failedCount` | エラー失敗 |
| `error` | 致命的失敗時の詳細 |

**実例（2026-04-20 batch A ai-finalize 58hnk）**:
- 投入 5,446件、3h 後: `state=RUNNING, successfulCount=5,442, incompleteCount=4`
- **compute は実質完了**、state は Vertex AI 側 finalize 処理（GCS 出力書込 + bookkeeping）で RUNNING 継続
- `successfulCount + incompleteCount = 投入数` なら、あとは state 遷移待ち（数分〜30分）

### 6. OOM 検知（signal 9）
```
mcp__gcp__logging_read filter="textPayload=~\"terminated on signal 9\"" --freshness 2h
```
ヒットあり = Cloud Run Job memory 超過 or 他インフラ kill。

### 7. chain プロセス稼働確認
```bash
ps -ef | grep recover_batch  # or monitor_backfill
```
死んでいれば再起動、または仕掛かり分の手動リカバリ。

## 🚨 インシデント事例（2026-04-19 〜 20、再発防止用）

### #1 15時間 stall（TPU preempt + runner exit 0 握りつぶし）

**事象**: batch#5-#11 WF が 15h ACTIVE のまま。TPU spot が preempt → SSH 切断でエラー → `gemma_tpu_runner.sh` 末尾の `echo ... | gcloud ssh ... | tail -100` がパイプ最終コマンド (`tail`) の exit 0 を返し、runner は「成功」扱い → Workflows は `await_callback` で永久待機。

**対処**:
- runner の最後に GCS `_SUCCESS` マーカー存在チェックを追加、なければ `exit 1`
- runner 内の `emit_callback "failed" "worker-no-success-marker"` 追加で Workflows retry 発動

**教訓**: bash の `cmd1 | tail` は cmd1 の exit code を握りつぶす（pipefail 未設定時）。`cmd1 > /tmp/log 2>&1; rc=$?; tail /tmp/log` で分離。

### #2 ai-finalize OOM → BQ DELETE 済・INSERT 未で 19K doc 消失

**事象**: batch A ai-finalize (19,156 doc) が 1CPU/4Gi で `phase5_bq_insert_finalize` 実行中 signal 9。既に `_delete_pending_gemma_rows` で 19,166 行 DELETE 完遂、BQ Load Job INSERT 未実行のため **BQ から消失**。

**対処**: ai-finalize を **2 CPU / 8Gi** に昇格、同 RUN_ID で再実行（Gemini Batch 再投入の追加コスト ~$1 発生、不可避）。

**根本原因**: numpy embedding + BQ Load Job の upload buffer で peak 3+ GB、4Gi 上限に衝突。

**恒久対策**:
- ai-finalize は 2 CPU / 8Gi を標準設定（013 のリソース表更新済）
- DELETE と INSERT の順序を逆転（先に INSERT → 成功確認 → DELETE）する改修案は要検討

### #3 chain script の pipe mask bug（#1 と同パターン）

**事象**: `recover_batch05_11_and_gap.sh` の `run_ai_finalize` 内 `gcloud ... --wait 2>&1 | tail -5; then` で、gcloud の失敗を tail 成功が隠蔽。batch A ai-finalize OOM 失敗を「成功」扱いし、batch B Gemma を誤って起動。

**対処**: `gcloud ... > /tmp/log 2>&1; rc=$?; tail /tmp/log; [ "$rc" -eq 0 ]` のパターンに書き換え済。

**予防**: bash スクリプトで外部コマンド exit code 判定する時は `set -o pipefail` も併用推奨。

### #4 Gemini Flash Batch の compute 時間（large batch 時の遅延）

**観測**: batch A（19K doc、決算短信 5,446 件）の Gemini Flash Batch が `JOB_STATE_RUNNING` のまま 2-3h。Flash 単体で 5,446 req は 10-30min 想定だが Vertex AI Batch の実処理時間は長め。

**教訓**: 1 batch で 5,000+ 件を Gemini Batch に投げる場合、**3h 程度の compute 時間を想定**。stall 判定閾値は 4-6h が妥当（CLAUDE.md デフォルト 3h は短い）。

### #6 ai-finalize OOM 第2波 — `load_table_from_file` の HTTP upload buffer（2026-04-20）

**事象**: batch A (19,156 doc) で ai-finalize が **8Gi に昇格後も再び OOM**。
- Phase 5 BQ Load Job 投入時 RSS = 2,155 MB
- 5 分後に 8Gi 突破 → signal 9 kill
- 1 度目の pd5n5 (1CPU/4Gi) だけでなく 58hnk (2CPU/8Gi) も同パターンで死亡
- pending rows 19,166 が DELETE 完遂後 INSERT 未実行で **BQ から 19K doc が消失**

**根本原因**: `bq.load_table_from_file(open(tmpfile, 'rb'))` が内部で HTTP upload buffer に tempfile 全体（1.4GB）をロード。前回の numpy+NDJSON stream 対策では防げない別経路。

**対処**: `phase5_bq_insert_finalize` および `phase5_bq_insert_load` を **GCS upload 経由**に変更:
```python
bucket.blob(gcs_path).upload_from_filename(tmp_path)  # streaming upload
bq.load_table_from_uri(gcs_uri, TABLE_ID, ...)         # BQ が GCS 直接読込
try: _blob.delete()                                     # 完了後即削除
except: pass
```
Python プロセス memory footprint から upload buffer を完全除去。

**リソース設定**: ai-finalize 標準 **2CPU/8Gi**（20K doc まで）、30K+ 想定時は 4CPU/16Gi。

**恒久ルール（013 本体「落とし穴」にも記載）**: BQ Load Job で row 数 > 10K 想定なら必ず `load_table_from_uri` を使う。`from_json` / `from_file` は Python メモリ消費大。

### #5 Vertex AI Batch: compute 完了後も state=RUNNING が継続（finalize lag）

**観測**（2026-04-20 58hnk で発見）: 5,446件投入の Gemini Batch が 3h後に `successfulCount=5,442, incompleteCount=4`（投入数と完全一致＝実質完了）なのに **state は JOB_STATE_RUNNING のまま**。Vertex AI 側の GCS 出力書込 + bookkeeping が残存。

**判別方法**: 上記セクション「5-1」の REST API で `completionStats` を確認。`successfulCount + incompleteCount ≈ 投入数` なら compute 実質完了、あとは数分〜30分で state=SUCCEEDED 遷移を待つだけ。

**教訓**: Python 側 `_poll_batch_job` は `state` しか見ないので finalize lag 中は延々 poll し続ける。ai-finalize が長時間 Phase 3 RUNNING だったら、**まず API で completionStats を確認**してから stall 判定する。

## 機能

| 機能 | 説明 |
|------|------|
| load → workflows 自動チェーン | 全 load 成功後に workflows 起動 |
| load 並列度可変 | `parallel_loads: N` で並列化 |
| resume 対応 | 既存 execution を監視のみ（再起動時の誤二重投入防止） |
| LINE 通知制御 | 開始/完了/失敗 + 各 load 個別通知の on/off |
| 失敗時即中断 | 後続の Gemini/Embedding Batch 重複課金回避 |
| 長時間ジョブ対応 | poll_interval で制御、timeout はジョブ側で管理 |

## 落とし穴

- **`status.` prefix 必須**: gcloud `--format="value(completionTime)"` は空返答で stuck する → **必ず `status.completionTime`** を指定（同 `status.succeededCount`, `status.failedCount` も）。過去バグ事例（2026-04-18）
- **セッション exit で死ぬ**: Claude Code セッション終了で Bash BG プロセスも終了。長時間ジョブには `CronCreate` で Windows Task Scheduler 経由起動が本来正道、または LINE 通知で欠損検知
- **Workflows は sequential**: `workflows[]` 複数指定時、本ツールは **全件 submit→順次 wait** のため、TPU quota=1 環境では実質 serial 動作（batch#2+#3 で検証済、問題なし）
- **Python subprocess の cp932 混入**（Windows 並列実行時）: gcloud 実行時の `UnicodeDecodeError` 対策として `errors='replace'` + `result.stdout or ""` ガード済（2026-04-19 修正）
- **gcloud.cmd 発見**: `shutil.which("gcloud")` で Windows 上の `.cmd` 拡張子を解決済
- **bash スクリプトの pipe で exit code 握りつぶし**（2026-04-20 再発、旧 `gemma_tpu_runner.sh` と同じ class bug）: `gcloud ... 2>&1 | tail -5; then` が tail 成功で gcloud 失敗を隠蔽。恒久対策は `gcloud ... > /tmp/log 2>&1; rc=$?; tail /tmp/log; [ "$rc" -eq 0 ]` のリダイレクト + rc 保存パターン。または `set -o pipefail` 併用

## 既存 config リスト（2026-04-19 時点）

- `config/backfill/2023_batch01_1301_1909.yaml` — batch#1 (ticker 1301-1909)
- `config/backfill/2023_batch01_finalize_retry.yaml` — batch#1 ai-finalize 再投入
- `config/backfill/2023_batch01_finalize_resume.yaml` — batch#1 既存 exec resume
- `config/backfill/2023_batch02_03.yaml` — batch#2+#3 (load+AI 通し)
- `config/backfill/2023_batch04_ai.yaml` — batch#4 AI のみ
- `config/backfill/2023_batch04_11_load_only.yaml` — batch#4-#11 load のみ
- `config/backfill/2023_batch04_11_resume.yaml` — batch#4-#11 既存 exec resume
- `config/backfill/2023_batch05_11_ai.yaml` — batch#5-#11 AI 2バッチ集約
- `config/backfill/2023_all_10load_1ai.yaml` — 参考: load 10 shot + AI 1本
- `config/backfill/2026_gap_recovery_jan_feb.yaml` — 2026-01-19〜02-27 漏れリカバリ（予約）

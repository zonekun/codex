# TDnet 適時開示 ETL スクリプト（tdnet_load_parallel.py）

**カテゴリ**: tools
**作成日**: 2026-03-01
**更新日**: 2026-05-21
**計画**: `docs/plans/tools-013_gemma_preemption_resilience_20260504_212000.md`（Gemma TPU プリエンプション耐性改善）
**計画**: `docs/plans/tools-013_tdnet_shift_md_auto_update_20260515_222900.md`（決算特別シフト MD自動更新）
**計画**: `docs/plans/archive/202605/tools-013_tdnet_chunk_index_column_20260517_223000.md`（CHUNK_INDEX 列追加 / 順序保証 — 完了 2026-05-18）
**計画**: `docs/plans/tools-013_gemma_only_pipeline_20260520_220510.md`（Gemini廃止 → Gemma専用パイプライン化 / PyMuPDF換装）
**計画**: `docs/plans/tools-013_chunk_embed_eval_20260521_155500.md`（決算説明資料 スライド単位チャンク化 + gemini-embedding-001 移行評価）
**計画**: `docs/plans/tools-013_qwen3_32b_tpu_trial_20260522_001314.md`（Qwen3-32B dense TPU v6e-4 試行評価 — Gemma 4 31B 代替可否）

---

## 決算特別シフト

ユーザーが「決算特別シフト」と指示した場合、以下の手順で通常スケジュールを決算繁忙期用に切り替える。

### 概要

通常スケジュール（DL 23:50 → Load 翌02:00 → AI 週次土07:00）を、同日20:00起動のチェーンパイプライン（DL → Load → AI、各ステップ前段完了次第）に切り替える。決算集中期（年4回: 2月/5月/8月/11月）に適用。

### 発動条件

- ユーザーが「決算特別シフト」と指示
- **終了日を必ず確認する**。ユーザーが指定していなければ聞くこと
- 終了日は「5/15まで」等の形式。翌営業日（例: 5/18月）の00:00 JSTに自動復帰

### 有効化手順

1. **パイプラインスケジューラ有効化**:
   ```bash
   gcloud scheduler jobs resume tdnet-daily-pipeline-scheduler --location=us-west1
   ```

2. **個別スケジューラ停止**（二重実行防止）:
   ```bash
   gcloud scheduler jobs pause tdnet-download-daily --location=us-west1
   gcloud scheduler jobs pause tdnet-load-daily-daily --location=us-west1
   ```

3. **自動復帰スケジューラ作成**（終了翌営業日 00:00 JST）:
   ```bash
   gcloud scheduler jobs create http tdnet-schedule-revert-YYYYMMDD \
     --location=us-west1 \
     --schedule="0 0 <翌営業日> <月> *" \
     --time-zone="Asia/Tokyo" \
     --uri="https://workflowexecutions.googleapis.com/v1/projects/gmailpj-357912/locations/us-central1/workflows/tdnet-schedule-revert/executions" \
     --http-method=POST --message-body='{}' \
     --oauth-service-account-email=bq-loader@gmailpj-357912.iam.gserviceaccount.com \
     --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
   ```

4. **本ファイル1行目のタイトルに「決算特別シフトON」を記載**:
   - 形式: `# TDnet 適時開示 ETL スクリプト（...）【決算特別シフトON M/DD〜M/DD】`
   - **開始日・終了日の両方を必ず記載**する（開始日不明だと復旧ジョブの対象期間が特定できない）
   - 1行目を見ればジョブスケジュールが特別シフト中と即座に判別できるようにする
   - 通常復帰時に【】を除去する

5. **013_tdnet_load.md 現況サマリを更新**（開始日・終了日・スケジューラ状態の反映）

6. **AI週次（tdnet-ai-weekly）は ENABLED 維持**（安全網: 日次で取りこぼした pending を土曜に回収）

### 無効化（通常復帰）

自動: `tdnet-schedule-revert` ワークフローが復帰スケジューラ経由で自動実行。以下を行う:
- `tdnet-daily-pipeline-scheduler` → PAUSE
- `tdnet-download-daily` → RESUME
- `tdnet-load-daily-daily` → RESUME
- 013 MD タイトルの【決算特別シフトON ...】を自動除去（`tdnet-shift-md-updater` Cloud Run Job 経由、GitHub Contents API で PUT）
  - best-effort: 失敗しても上記スケジューラ復帰は完遂する（Workflow の try/except で分離）
  - GitHub PAT 失効時は Secret Manager `github-pat` を更新して手動再実行

手動: `gcloud workflows execute tdnet-schedule-revert --location=us-central1`

復帰後、使い終わった復帰スケジューラを**必ず削除**すること:
```bash
gcloud scheduler jobs delete tdnet-schedule-revert-YYYYMMDD --location=us-west1
```
> **⚠ 削除忘れ注意**: Cloud Scheduler の cron 式は年フィールドを持たないため、削除しないと**翌年の同月同日に誤発火**し、通常運用中にスケジューラ状態が意図せず変更される。

### 関連リソース

| リソース | 種別 | 用途 |
|---------|------|------|
| `tdnet-daily-pipeline` | Cloud Workflows (us-central1) | DL→Load(DATE_MODE=t)→AI チェーン |
| `tdnet-daily-pipeline-scheduler` | Cloud Scheduler (us-west1) | 月〜金 20:03 JST |
| `tdnet-schedule-revert` | Cloud Workflows (us-central1) | スケジューラ状態を通常に復帰 |
| `workflows/tdnet_daily_pipeline.yaml` | ソース | パイプライン定義 |
| `workflows/tdnet_schedule_revert.yaml` | ソース | 復帰ワークフロー定義 |
| `tdnet-shift-md-updater` | Cloud Run Job (us-west1) | 013 MD タイトルの【】自動除去（GitHub API） |
| `github-pat` | Secret Manager | GitHub Contents API 用 PAT（Fine-grained, repo scope） |

### 運用実績

- **2026-05-07**: 初回実行。DL 2m45s + Load 4m19s(276件) + AI 52min = 合計59min。実処理全成功。マスターWFのtrigger_ai LROタイムアウト修正済み。CR-094でDL timeout 7200→10800に拡大（rev 000003-917）
  - **事故1**: 同日、2022年バックフィル Load（6h timeout）と同時実行。`tdnet-daily-pipeline` の trigger_ai が 1800s タイムアウトで FAILED。LRO timeout は 43200s に修正済みだが、共存制約の明文化がなかった → §共存制約 新設
  - **事故2**: `ai_processing_flow`（`recent_only: true`）が 4月の残存 pending を優先し、5/7 の 275行が未処理。`resolve_date_range` の月単位選択が原因 → 日単位化で修正済み（2026-05-08）

**TODO**:
- `run_download` に `body.overrides.timeout: "10800s"` 追加（現在 connector_params のみ。080 §2 の両方揃える原則違反）
- `tdnet_schedule_revert.yaml` のステップ順序を resume → pause に変更（途中失敗時に通常ジョブが停止したまま残るリスク回避）

---

## ✅ AI処理 週次スケジューラ設定済み（2026-05-02）

- **ジョブ名**: `tdnet-ai-weekly`（Cloud Scheduler、location=us-west1）
- **スケジュール**: 毎週土曜 07:00 JST
- **動作**: `ai_processing_flow` を `recent_only: true` で起動 → 直近14日以内の `AI_STATUS='pending'` のみ処理
- **バックフィル安全**: `recent_only` パラメータにより過去年バックフィルの pending を横取りしない（※ これはデータレベルの安全性のみ。Cloud Run Job レベルの競合リスクは §バックフィルと日次パイプラインの共存制約 を参照）
- **`recent_only` モードの使い分け**: `recent_only: true`（週次/日次）= 14日ガード、`recent_only: false`（手動/monitor_backfill）= 全期間最古、`date_from`/`date_to` 明示 = 指定期間のみ
- **コスト**: ~$1.6/週（TPU $1 + Embedding $0.1 + Gemini $0.5）→ **2026-05-21 以降 ~$1.1/週（Gemini 廃止）**

**2026-04-20 修正済み**: `phase5_bq_insert_finalize` NameError / exit(0) 偽陽性 / 例外握り潰し / `_cleanup_ai_state` gate / DELETE→INSERT 非原子 / ai-prepare DB status 更新順 — 計 6 件修正。詳細は「TDnet ETL 固有の再発防止ルール」（T-1〜T-5）および `078_gemma4_operation.md §7`（G-1〜G-3）参照。インシデント詳細: `013-2_monitor_backfill.md`。

---

## 📌 現況サマリ（2026-05-21）

**Gemini Flash Batch 廃止・Gemma専用パイプライン化完了（2026-05-21）**:
- **PDF抽出**: PyPDF2/pdfminer → **PyMuPDF（fitz）** + pdfminer フォールバック に換装
- **Vision OCR**: 廃止（画像PDF は `text=""` で AI処理スキップ、`AI_STATUS='skipped_image_pdf'` に遷移）
- **Gemini Flash Batch**: 廃止（`phase_gemini_tanshin_batch` / `_merge_gemini_juchu` 削除）
- **Gemma 2-pass**: 追加（Pass 1: 全件、Pass 2: 決算短信+決算説明資料 → 受注高/受注残高 判定）
- **詳細**: `docs/plans/tools-013_gemma_only_pipeline_20260520_220510.md`
- **コスト**: ~$1.1/週（TPU $1 + Embedding $0.1、Gemini $0.5 廃止）

| コンポーネント | 役割（2026-05-21〜） |
|-------------|------|
| `tdnet-load-daily` | GCS PDF → PyMuPDF/pdfminer 抽出 → BQ Load Job（AI_STATUS='pending'） |
| `ai_processing_flow` Workflows | ai-prepare → Gemma TPU 2-pass 推論 → ai-finalize オーケストレーション |
| `tdnet-ai-prepare` | PyMuPDF抽出 + 正規表現月次補正 + state.json 保存（pending_gemma へ） |
| `tdnet-gemma-runner` | bash: TPU v6e-4 作成 → vLLM + worker.py（2-pass） → 削除 |
| `tdnet-ai-finalize` | Gemma Pass 1+2 適用 + Embedding + BQ Insert（completed へ） |

**スモークテスト・本番デプロイ完了（2026-05-21）。E-2〜E-5 全完了。**

---

## 📌 現況サマリ（2026-05-20）

**FILER_NAME 遡及修正完了（2026-05-20）**:
- 原因: `tdnet-ai-prepare` が `state.json` に `filer_name` を保存しておらず、`tdnet-ai-finalize` が空文字で INSERT していた（commit `8459c94a` で修正済み）。
- 修正内容: アルファベット ticker（40,073行/338銘柄、2024-09-24〜）→ FILE_NAME REGEXP 抽出、数字 ticker（9,315,255行/4,999銘柄、2017〜）→ STOCK_CODE_LIST JOIN + REGEXP fallback。修正後残存=0。
- 詳細プラン: `docs/plans/tools-013_bq_past_data_recovery_20260518_232030.md`

---

## 📌 現況サマリ（2026-05-10）

**本番稼働中のアーキ**: BQロード / AI判定分離、Cloud Workflows 主導（2026-04-17〜）
**決算特別シフト**: `tdnet-daily-pipeline-scheduler` ENABLED（月〜金 20:03 JST、〜5/15）→ 5/18 自動復帰
**日次ロード**: `tdnet-load-daily-daily` **PAUSED**（決算特別シフト中）
**日次DL**: `tdnet-download-daily` **PAUSED**（決算特別シフト中）
**AI処理**: `tdnet-ai-weekly` ENABLED（毎週土 07:00 JST、安全網として維持）
**バックフィル**: 2017-2022年 ✅ / 2024年 ✅ / 2025年 ✅ / 2026年1-2月漏れ ✅ — **全年（2016-2026）pending=0**（2022年のみ確認済み 05/10、他年は04/27確認）
**P0-1 修正**: `_content_length()` 導入（commit `19a7c7f`）— ページマーカーが閾値判定をすり抜けるバグ修正
**P1-1 リカバリ**: TEXT_LENGTH < 300 の 2,077件を再抽出 → 2,065件成功（99.4%）、BQ MERGE 完了（2026-05-06）。リカバリ対象は AI_STATUS='pending' にリセット済み → 次回 `tdnet-ai-weekly` で自動処理

| コンポーネント | 役割 |
|-------------|------|
| `tdnet-load-daily` | GCS PDF → テキスト抽出 → BQ Load Job（AI_STATUS='pending'、日次 02:00 JST） |
| `ai_processing_flow` Workflows | ai-prepare → Gemma TPU 推論 → ai-finalize オーケストレーション |
| `tdnet-ai-prepare` | OCR + 正規表現月次補正 + state.json 保存（pending_gemma へ） |
| `tdnet-gemma-runner` | bash: TPU v6e-4 作成 → vLLM + worker.py → 削除 |
| `tdnet-ai-finalize` | Gemma結果適用 + Gemini Flash Batch（決算短信のみ受注判定） + Embedding + BQ Insert（completed へ） |

**リソース**:
- tdnet-load-daily: 1 CPU / 2Gi
- tdnet-ai-prepare: 1 CPU / 2Gi
- tdnet-ai-finalize: **現状 4 CPU / 16Gi（過剰）**
- tdnet-gemma-runner: 1 CPU / 2Gi

**TODO: ai-finalize ダウンサイズ**（0425 随時実行完了後に実施）:
- 実測 peak RSS 0.13 KB/doc、3万 doc でも 4Gi 十分。4CPU/16Gi は旧 `load_table_from_file` HTTP upload buffer 対策の残骸
- `gcloud run jobs update tdnet-ai-finalize --cpu=1 --memory=2Gi --region=us-west1`

**コスト**:
- 月次運用: **~$11.5**
- 2023年バックフィル（46,125 doc）: 想定 $100 前後
- 詳細: `013-1_ai_cost_and_gemma_poc.md`（AI コスト分析 & PoC 実績）

**関連ファイル**:
- `scripts/tdnet_load_parallel.py` — 1本の Python スクリプト、`--job-mode` で切替
- `scripts/gemma_tpu_worker.py` — TPU VM 上で vLLM 推論 + GCS append + Callback
- `scripts/gemma_tpu_runner.sh` — Cloud Run Job 内で TPU 起動〜削除
- `workflows/ai_processing_flow.yaml` — Cloud Workflows オーケストレーション
- `scripts/monitor_backfill.py` — バックフィル監視汎用ツール（YAML 宣言）

**参照ドキュメント**:
- **`013-1_ai_cost_and_gemma_poc.md`** — AI コスト分析、Gemma プロンプト、PoC 履歴
- **`078_gemma4_operation.md`** — TPU / vLLM セットアップ手順、動作確認済み構成
- **`080_workflows_runbook.md`** — Cloud Workflows 汎用ノウハウ（parallel shared / connector_params / Callback 認証）
- `api/002_bigquery.md` — Load Job vs streaming の使い分け
- **改修プラン**: `docs/plans/20260417_091112_tdnet_load_ai_split.md`

**パイプライン前段**: `docs/knowledges/tools/003_tdnet_download.md`（PDF を GCS へ保存）
**書き込み先テーブル**: `data_catalog.md` > `STOCK.TDNET_DOCUMENTS_ENHANCED`

### GCS バケット構造（`gs://stock_data_1930932/`）

```
gs://stock_data_1930932/
├── tdnet/                              ← 【永続】TDnet PDF 本体
│   ├── {TICKER4桁}/
│   │   └── {YYYYMMDD}_{TICKER}_...pdf  TDnet 開示書類 (30-90日で TDnet 公式から消えるため永続保管)
│   └── index_{from}_{to}.csv           日付別 index CSV
│
├── ai_job/                             ← 【AI 中間】GCS Lifecycle 14日で自動削除
│   └── {run_id}/
│       ├── state.json                   ai-prepare 出力（doc 一覧 + テキスト）
│       ├── gemma_CURRENT.jsonl          Gemma 推論結果 (continuous append)
│       └── _SUCCESS                     worker 完了マーカー
│
├── batch_prediction/tdnet/             ← 【AI 中間】Vertex AI Batch I/O + BQ Load Job 受け渡し
│   ├── analysis_{ts}_input.jsonl        Gemini Flash Batch 入力
│   ├── analysis_{ts}_output/            Gemini Flash Batch 出力
│   ├── embed_{ts}_input.jsonl           Embedding Batch 入力
│   ├── embed_{ts}_output/               Embedding Batch 出力
│   ├── ai_finalize_upload_{ts}.jsonl    BQ Load Job 用 (ai-finalize、使用後即削除)
│   ├── load_upload_{ts}.jsonl           BQ Load Job 用 (load-daily、使用後即削除、2026-04-20 新設)
│   └── backfill_state_*.json            旧 backfill state
│
├── log/                                ← 【運用】実行ログ
│   └── tdnet_load_to_bq_*.txt
│
└── docker/                             ← 【運用】Docker image save (vllm-tpu 等)
    └── vllm-tpu-*.tar.gz
```

各 prefix は互いに干渉せず、ライフサイクルも独立。

---

## 新アーキ（BQロード/AI判定分離、2026-04-17〜）

### 目的
- BQロード（低レイテンシ）と AI判定（重処理）を完全分離
- Phase 3（カテゴリ判定）を Gemma 4 31B TPU v6e-4 + vLLM に移行
- 受注判定は決算短信のみ Gemini Flash Batch で上書き
- Cloud Run を長時間待機させない（Workflows 主導）
- 7年バックフィル ~$154（集約時）/ 月次運用 ~$11.5（Gemini 単独比 41%、詳細 `013-1`）

### ジョブ構成（2026-04-19 実測後リソース確定）

| コンポーネント | 実体 | モード | CPU | メモリ | タイムアウト |
|-------------|------|--------|-----|--------|-------------|
| `tdnet-load-daily` | Cloud Run Job CPU | `--job-mode=load` | 1 | 2Gi | 3600s |
| `ai_processing_flow` | Cloud Workflows | - | - | - | 1年 |
| `tdnet-ai-prepare` | Cloud Run Job CPU | `--job-mode=ai-prepare` | 1 | 2Gi | 3600s |
| TPU v6e-4 spot VM | Compute Engine + vLLM | - | - | - | spot |
| `tdnet-gemma-runner` | Cloud Run Job CPU | bash (gcloud wrapper) | 1 | 2Gi | 3600s |
| `tdnet-ai-finalize` | Cloud Run Job CPU | `--job-mode=ai-finalize` | **2** | **8Gi** | 3600s |

### Cloud Run Job リソース設計の根拠（2026-04-20 実測で確定版）

**ai-finalize は 2 CPU / 8Gi が標準**（初期 1CPU/4Gi を試みたが 19K doc 級で OOM 事故発生）。

**経緯**:
1. 2026-04-18 batch#1（3,091 doc）: 初期 1 CPU / 3Gi → `rows_buffer: list[dict]` + Python list 保持の embedding で OOM
2. 暫定対策: 2 CPU / 8Gi に増強 + numpy float32 保持 + NDJSON stream write 実装
3. 2026-04-19 batch#2-#4 で RSS 実測:
   - batch#2 (2,401 doc, 39,807 chunk): peak **386 MB**
   - batch#3 (4,709 doc, 82,279 chunk): peak **623 MB**
   - batch#4 (6,306 doc, 121,842 chunk): peak **845 MB**
4. ここで 1 CPU / 4Gi にダウン → ~15K doc まで余裕の試算
5. **2026-04-20 batch A (19,156 doc, 349,371 chunk) ai-finalize で 1 CPU / 4Gi が SIGKILL**: phase5 BQ Load Job 直前 RSS 2.15 GB、DELETE 完遂後 INSERT 未実行で 19,166 doc 消失
6. **結論: 2 CPU / 8Gi に戻す**（BQ Load Job の upload buffer で peak +1-2 GB 読み誤り）

**スケーリング実測（2CPU/8Gi 想定）**:
- 1 doc あたり約 130-180 KB（numpy embedding + json 一時）
- 6K doc → ~1 GB
- 19K doc → ~3 GB（4Gi ではギリ OOM 再現、8Gi なら余裕）
- 30K doc → ~5 GB（8Gi 収まる、ただしマージン小）

**CPU 2 の理由**: Cloud Run Jobs の CPU↔Memory 制約（1CPU→4Gi上限、2CPU→8Gi上限）。8Gi 必要 = 2CPU 必須。なお ai-finalize は I/O 律速なので CPU は性能向上より制約開放目的。

**バッチ分割方針**: 19K doc で要注意（4Gi OOM 実績あり）、推奨は **1 batch ≤ 15K doc** or **2CPU/8Gi を必ず維持**。

**他ジョブ据え置き**: load/ai-prepare/gemma-runner は embedding 保持しないため 1 CPU / 2Gi 維持。

### フロー

```
[tdnet-load-daily]  02:00 JST 毎日（Cloud Scheduler）
  --job-mode=load
  Phase 0: 開示時刻マッピング（GCS index CSV）
  Phase 1: テキスト抽出（PyPDF2 → pdfminer）
  Phase 4: チャンク分割のみ（Embedding なし）
  Phase 5: BQ Insert（AI_STATUS='pending', MAIN/SUB/EMBEDDING=NULL）

              ↓（独立して後刻）

[ai_processing_flow]  Cloud Workflows
  Step 1: tdnet-ai-prepare (Cloud Run Job)
    - BQ SELECT: AI_STATUS='pending' AND 日付範囲
    - GCS PDF → PyPDF2/pdfminer でテキスト再抽出（全文取得）
    - Vision OCR (Vertex Batch, 抽出失敗分のみ)
    - 正規表現月次補正 → pre_main_category
    - GCS state.json 保存（gs://stock_data_1930932/ai_job/{run_id}/）
    - AI_STATUS → 'pending_gemma'

  Step 2: TPU v6e-4 VM 作成（HTTP で TPU API POST）
    - metadata: run_id, callback_url, bucket_name
    - startup-script-url で tpu_vm_startup_gemma.sh を指定
    - vllm-tpu Docker 起動（固定 digest、`013-1` / `078` 参照）
    - transformers>=4.58 upgrade
    - gemma_tpu_worker.py 取得 → 実行

  Step 3: Gemma 完了待機（Workflows Callback）
    - worker が処理完了時 CALLBACK_URL に POST
    - 1件ごとに gemma_CURRENT.jsonl に GCS append（preemption 耐性）
    - preemption 再起動時は CURRENT.jsonl から resume

  Step 4: tdnet-ai-finalize (Cloud Run Job)
    - state.json + gemma_CURRENT.jsonl 読込
    - Gemma 結果を DocInfo に適用（MAIN/SUB）
    - Gemini Flash Batch（MAIN='決算短信' のみ、現行 phase3 プロンプト流用）
    - 受注判定を Gemma SUB に差分マージ（他は温存）
    - Embedding Batch（3カテゴリ: 決算短信/決算説明資料/月次開示）
    - BQ DELETE（pending_gemma 既存行）+ INSERT（AI_STATUS='completed'）
    - GCS state cleanup
```

### プロンプト

| モデル | プロンプト |
|-------|----------|
| Gemma 4 31B (TPU v6e-4) | baseline（案A: 配当/特損/特利 PL数値 vs 開示イベント区別）+ 中計ルール（本番採用確定、詳細 `013-1`） |
| Gemini Flash Batch | 現行の統合プロンプト流用（is_monthly + sub_categories） |

### 結果マージロジック（Gemma × Gemini）

```python
# 決算短信以外: Gemma の MAIN/SUB をそのまま採用
# 決算短信のみ: Gemini の「受注高/受注残高」判定を差分適用
if doc.main_category == "決算短信":
    if "受注高/受注残高" in gemini_sub:
        gemma_sub.add("受注高/受注残高")
    else:
        gemma_sub.discard("受注高/受注残高")
doc.sub_categories = sorted(gemma_sub)
```

### AI_STATUS 状態遷移

| 状態 | 意味 |
|------|------|
| `pending` | load 完了、AI処理未着手 |
| `pending_gemma` | ai-prepare 完了、Gemma推論中 |
| `pending_finalize` | Gemma完了、Embedding/UPDATE 待ち（短時間状態、クラッシュ検出用） |
| `skipped_image_pdf` | 画像PDF（PyMuPDF+pdfminer 両者テキスト不足）、AI処理スキップ（2026-05-21〜） |
| `completed` | AI処理完了 |

### GCS 状態ファイル

```
gs://stock_data_1930932/ai_job/{run_id}/
  ├─ state.json                  — ai-prepare 出力（対象doc_id, テキスト, pre_main_category 等）
  ├─ gemma_CURRENT.jsonl         — Gemma Pass 1 推論結果（全件、1件ずつappend、preemption resume）
  ├─ gemma_pass2_CURRENT.jsonl   — Gemma Pass 2 推論結果（決算短信+決算説明資料のみ）（2026-05-21〜）
  └─ _SUCCESS                    — worker 完了マーカー（Pass 2 完了後に生成）

gs://stock_data_1930932/ai_job/scripts/
  ├─ gemma_tpu_worker.py          — TPU VM 上で動く worker
  └─ tpu_vm_startup_gemma.sh      — TPU VM startup-script
```

### Cloud Workflows 起動

```bash
# 手動
gcloud workflows execute ai_processing_flow --location=us-central1 \
  --data='{"date_from":"20230101","date_to":"20230131"}'

# date 省略時は BQ から最古 pending 月を自動選択
gcloud workflows execute ai_processing_flow --location=us-central1
```

### イメージ

新アーキは3 Cloud Run Job とも共通イメージ `us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-load-daily:latest` を使い、`--args=--job-mode=...` で切り替える。

```bash
# ビルド + docker push + 3 Job 自動 update（cloudbuild.yaml に組み込み済み）
gcloud builds submit --config cloudbuild/cloudbuild.tdnet-load-daily.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source .
# → docker build → docker push → gcloud run jobs update × 3 Job が自動実行される
# 過去事故: 2026-04-25 手動 jobs update 忘れでサロゲート修正が未反映 → 自動化で再発防止（2026-04-26）

# フォールバック: cloudbuild.yaml の自動ステップが失敗した場合のみ手動実行
for job in tdnet-load-daily tdnet-ai-prepare tdnet-ai-finalize; do
  gcloud run jobs update "$job" --region=us-west1 \
    --image=us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-load-daily:latest
done
```

### バックフィル load 投入コマンド

```bash
# DATE_FROM / DATE_TO / ALLOW_FULL_BACKFILL は環境変数。CLI引数ではない
# mcp__gcp__cloudrun_execute は env vars を渡せないため gcloud CLI 必須
gcloud run jobs execute tdnet-load-daily --region us-west1 \
  --args="--job-mode=load" \
  --update-env-vars DATE_FROM=YYYYMMDD,DATE_TO=YYYYMMDD,ALLOW_FULL_BACKFILL=1
```

**禁止**: `mcp__gcp__cloudrun_execute` で `--from`/`--date-from`/`--allow-full-backfill` をargs渡しする（2026-05-03 に2回連続失敗）

### 注意事項・落とし穴

- **Embedding 単価は文字課金** $0.025/1M chars（`013-1` で訂正、トークン課金誤認）
- **vllm-tpu:latest はタグドリフトで壊れる** → 固定 digest で pin 必須（`078` セクション 2-1）
- **TPU spot preemption** → `gemma_CURRENT.jsonl` 継続 append + resume 必須（`013-1` / `078` セクション 2-7）
- **SUB_CATEGORIES (ARRAY<STRING>)** は NULL 不可 → load モードは row から key 省略（empty array になる）
- **BQ streaming buffer** insert 直後 30-90分は UPDATE/DELETE 不可。運用では load → ai-prepare の間に経過するので問題なし
- **Workflows at-least-once 配信** → ai-finalize 側で AI_STATUS チェックにより冪等性担保
- **既存 `tdnet-load-parallel` / `tdnet-load-recovery` は段階的廃止**（新アーキ稼働3ヶ月後）
- **ai-finalize OOM 対策**（2026-04-18 発覚）: Python `list[list[float]]` での embedding 保持は 1 float=24-32B で爆発する（3,000 doc × 117 chunk × 768 × 32B ≈ 8 GB）。`phase4_chunk_and_embed` は **numpy float32** で保持、`phase5_bq_insert_finalize` は **NDJSON stream write + `load_table_from_file`** で rows_buffer を廃止して ~8倍圧縮。詳細は「Cloud Run Job リソース設計の根拠」セクション参照
- **Cloud Run Jobs の CPU↔Memory 制約**: 1 CPU → 4Gi 上限、2 CPU → 8Gi、4 CPU → 16Gi。メモリだけ増やすことは不可、CPU と連動
- **BQ Load Job の API 選択（2026-04-20 確定）**: 大量 row insert（目安 10K row 超）では **必ず `load_table_from_uri`（GCS 経由）を使う**。`load_table_from_json(list)` は Python メモリで list + JSON 文字列を 2 倍保持、`load_table_from_file(fp)` は HTTP upload buffer で数 GB 膨張しうる。いずれも OOM 原因。`phase5_bq_insert_finalize` / `phase5_bq_insert_load` とも GCS 経由に統一済。詳細 `013-2` インシデント #6 / `api/002_bigquery.md`

### バックフィルと日次パイプラインの共存制約（2026-05-08 新設）

> **事故起源**: 2026-05-07 に発生した 2 件の運用事故（レビュー 107/108）から導出。

**前提**: `tdnet-load-daily` Cloud Run Job はバックフィルと日次パイプライン（`tdnet-daily-pipeline`）で**共有**されている。同一 Job に対して複数 execution が同時に走行可能。

**制約1 — Cloud Run Job リソース競合**:
- バックフィル Load（`DATE_FROM/DATE_TO` 指定、数時間〜6h）と日次パイプラインの Load（数分）が同時に走ると、日次パイプラインの trigger_ai ステップが前段 Load の長時間実行により遅延する可能性がある
- **決算特別シフト期間中**（パイプライン 20:03 JST 起動）にバックフィル Load を投入する場合、**19:00 JST 前に完了が見込めない場合は投入を避ける**
- 通常スケジュール期間（Load 02:00 JST）も同様に、バックフィル Load が 01:00 JST 前に完了しない場合は日次側と衝突する

**制約2 — `resolve_date_range` の日付選択**:
- `ai_processing_flow` の `recent_only: true` モードは、直近14日以内の pending を**日単位**（MIN〜MAX）で処理する（2026-05-08 改修済み。旧仕様は月単位で最古月のみ選択し、月境界で当日分が後回しになる問題があった）
- バックフィル AI は `date_from`/`date_to` を**明示指定**で投入し、`resolve_date_range` を経由しないこと。`recent_only: false` + 日付未指定だと全 pending の最古月が対象になり、意図しない範囲が処理される

**制約3 — pending 残存による連鎖**:
- 前月の pending が残存していると（ai-finalize 部分失敗、text 取得失敗の pending 保持等）、`recent_only` の14日ウィンドウに入り込み、処理範囲が広がる
- pending 残存は `tdnet-ai-weekly`（土 07:00 JST）で週次回収されるが、決算繁忙期は日次での即時回収が望ましい。パイプライン失敗時は `ai_processing_flow` を手動で `recent_only: true` 再投入すること

### バックフィル運用

**→ 実績・再利用ナレッジは `013-3_tdnet_backfill_archive.md` に集約**（全年バックフィル完了サマリ、整合性チェックSQL、OOM分割基準、preemptionタイミング、task-timeout設定値、Phase I設計ポイント）

バックフィル監視ツール: `scripts/monitor_backfill.py` + `config/backfill/*.yaml`（詳細は `013-2_monitor_backfill.md`）

---

## 設計判断履歴: Gemini Flash 廃止 (2026-05-20)

**目的**: 本セクションは将来の code-reviewer / md-reviewer が「Gemma 過剰検知 vs Gemini コスト」問題を繰り返し指摘しないために、意思決定の根拠を恒久記録する。

### 判断内容

Gemini Flash Batch（受注判定）を**廃止**し、Gemma Pass 2（`build_prompt_pass2()`）に置き換えた。

### 背景となる過去 PoC 知見 (`013-1_ai_cost_and_gemma_poc.md`)

- Gemma は受注高/受注残高を過剰検知する傾向がある
- PoC v2（+ 受注ルール強化）は正解率 40.0% で逆効果 → 不採用
- このため「ハイブリッド（決算短信受注のみ Gemini）」が採用されていた

### 廃止の判断根拠

- Gemini Flash Batch コスト: ~$0.5/週（月次 ~$2）
- Gemma 過剰検知補正コスト（spike/PoC 実施工数）> Gemini 継続コスト という従来の判断から、**Gemini 廃止 + 過剰検知補正を別タスクでカバー** に方針転換
- Pass 2 Gemma vs Gemini の精度比較 spike は**実施しない**（時間コスト > 期待便益）

### 意思決定

- **承認**: ユーザー意思決定 (2026-05-20)
- **トレードオフ受容**: Gemma 過剰検知の事実を承知の上で廃止。過剰検知が問題化した場合は別タスクで後段フィルタ・軽量モデル等で補正する
- **参照**: `docs/plans/tools-013_gemma_only_pipeline_20260520_220510.md §設計判断・トレードオフ`

> **⚠ reviewer へ**: この判断はユーザーが意識的に行ったコスト vs 精度のトレードオフです。「PoC と矛盾する」「Gemma 精度が懸念」という指摘はすでに考慮済みです。再指摘は不要です。

---

## BQ Vector Index 設定

ETL 完了後（`processed > 0` の場合のみ）に自動作成：

```sql
CREATE VECTOR INDEX IF NOT EXISTS tdnet_doc_vector_index
ON `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`(EMBEDDING)
OPTIONS(
    index_type    = 'IVF',
    distance_type = 'COSINE',
    ivf_options   = '{"num_lists": 1000}'
);
```

---

## GCS パス構造

実際のパス: **`tdnet/{TICKER4桁}/{YYYYMMDD}_{TICKER}_{会社名}_{カテゴリ}_{タイトル}_{元ファイル名}.pdf`**

```
gs://stock_data_1930932/tdnet/7203/20260313_7203_トヨタ自動車_決算短信_xxx.pdf
```

`tdnet/{TICKER}/` プレフィックスで TICKER 単位に直接アクセスできる。

---

## 検証スクリプト

`scripts/tdnet_batch_verify.py` — 既存 BQ データと新プログラム出力の MAIN_CATEGORY / SUB_CATEGORIES を比較。

```bash
PYTHONUTF8=1 python scripts/tdnet_batch_verify.py --from 20260301 --to 20260331
```

結果 CSV: `C:\tmp\tdnet_batch_verify_{timestamp}.csv`

---

## SDK ルール（重要）

### google-cloud-aiplatform（旧SDK）は使用禁止

Vertex AI Batch Prediction のジョブ操作には **`google-genai`（新SDK）** を使う。`google-cloud-aiplatform` の `BatchPredictionJob` は旧SDKであり、新規コード・既存コード問わず **使用禁止**。

```python
# ✅ 正しい（google-genai SDK）
from google import genai
client = genai.Client(vertexai=True, project=PROJECT, location="global")
job = client.batches.get(name=job_name)
state = job.state.name if hasattr(job.state, "name") else str(job.state)

# ❌ 禁止（google-cloud-aiplatform 旧SDK）
from google.cloud.aiplatform import BatchPredictionJob  # 使ってはいけない
```

### バッチジョブ状態値のバリエーション

`client.batches.get()` の返す `job.state` は SDK バージョン・タイミングにより複数の表現がある。判定時は以下すべてを考慮すること:

| 意味 | 状態値（いずれかにマッチ） |
|------|------------------------|
| 成功 | `JOB_STATE_SUCCEEDED`, `SUCCEEDED`, `completed` |
| 失敗 | `JOB_STATE_FAILED`, `FAILED` |
| キャンセル | `JOB_STATE_CANCELLED`, `CANCELLED` |
| 実行中 | 上記以外（`JOB_STATE_RUNNING`, `PENDING` 等） |

### 適用範囲

このルールは以下すべてに適用される:
- `scripts/tdnet_load_parallel.py`（メインETLスクリプト）
- `functions/tdnet_backfill_poller/main.py`（ポーラー Cloud Functions）
- 今後作成する Vertex AI バッチ関連コードすべて

---

## 注意事項

- **`VALID_CATEGORIES` はカタログと同期させること**: `data_catalog.md` > `TDNET_DOCUMENTS_ENHANCED` のカテゴリ値一覧が正
- **ログ保存先**: `gs://stock_data_1930932/log/tdnet_load_to_bq_{timestamp}_batch_log.txt`
- **バッチジョブのポーリング間隔**: 60秒（`BATCH_POLL_INTERVAL`）
- **Batch Embedding の task_type**: 指定不可（API制限）。既存データの `RETRIEVAL_DOCUMENT` ベクトルとは互換性なし

---

## 月次開示 MAIN_CATEGORY 分類漏れ（再発検知 SQL）

`_MONTHLY_DOC_PATTERN` regex（`scripts/tdnet_load_parallel.py`）で吸収済み。新パターンを見つけたら同ファイルの `_MONTHLY_DOC_PATTERN` に追記 → Docker rebuild。`_TICKER_SPECIFIC_MONTHLY` に ticker 個別例外（例: 3086 J.フロント `連結売上収益報告`）も追加済み。

**再発検知 SQL（月次に分類すべきタイトルが漏れていないか検査）:**

```sql
-- 月次パターンに該当するタイトルが「月次開示」以外のカテゴリにあるか検査
SELECT MAIN_CATEGORY, DOC_TITLE, COUNT(*) AS cnt
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE SUBMISSION_DATE >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
  AND MAIN_CATEGORY != '月次開示'
  AND (REGEXP_CONTAINS(DOC_TITLE, r'月次|月度|主要ＫＰＩ|主要KPI|Monthly Report|マンスリーレポート|月末運用資産|運用資産概況|前年比速報|ポートフォリオ運営実績|ポートフォリオ稼働率|DATA FILE|月度IR|月度ＩＲ|ホテル運営状況|売上報告|前年対比')
       OR (TICKER = '3086' AND REGEXP_CONTAINS(DOC_TITLE, r'連結売上収益報告')))
GROUP BY MAIN_CATEGORY, DOC_TITLE
ORDER BY cnt DESC
LIMIT 100;
```
新パターンを見つけたら `scripts/tdnet_load_parallel.py` の `_MONTHLY_DOC_PATTERN` に追記 → Docker rebuild。

---

## TDnet ETL 固有の再発防止ルール

**前提**: 汎用バッチ・ETL ルールは `004_coding_conventions.md §バッチジョブ・ETL アンチパターン集`（A-1〜E-2, C-5）に、Gemma/Gemini Batch 結合部は `078_gemma4_operation.md §7`（G-1〜G-3）に集約済み。本節は `scripts/tdnet_load_parallel.py` 固有の設計課題のみ扱う。

### T-1〜T-5（TDnet 固有）

| # | 対象 | 禁則（やってはいけない） | 予防策 |
|---|---|---|---|
| T-1 | `phase5_bq_insert_finalize` / Phase 5 系関数全般 | GCS `bucket` を署名で受け取らず、モジュールや呼出元スコープの変数に暗黙依存 | 署名に `bucket` を追加。同一ファイル内 `phase5_bq_insert_load` は引数で受けている — **対称性の崩れを放置しない**。Phase 5 を触ったら `python -m py_compile` + 数件スモーク（BQ Load Job 実行確認まで）を deploy 前に必ず通す（004 F-1）。**過去事故**: 2026-04-20 batch A 7bd8f で 19,156 doc ロスト |
| T-2 | `parse_tdnet_filename` | 不正ファイル名の fallback で `datetime.now()` / `uuid.uuid4()` を返して処理継続 | fallback は `raise ValueError(...)`、呼出側 (`phase1_scan_and_extract`) で `except → count + log + skip`。誤日付 BQ 挿入と retry 時重複を両方塞ぐ（004 A-5 の TDnet 実装） |
| T-3 | `phase4_chunk_and_embed` | chunk → doc のマッピング key に `chunk_text` 文字列を使う | `(doc_id, chunk_index)` tuple を key に。同一テキスト chunk の衝突で embedding が紛失しない。**2026-05-18 追記**: `chunk_index` は BQ 列 `CHUNK_INDEX INT64` としても露出済み（CR-203 / tools-013_tdnet_chunk_index_column プラン）。新規ロード分は `ORDER BY CHUNK_INDEX` で本文順復元可、過去分は NULL |
| T-4 | Phase 5 系（`phase5_bq_insert_finalize` / `phase5_bq_insert_load`）のエラー集計 | バッチ insert 失敗時に `errors += 1` を doc 単位で加算 | `errors += len(failed_rows)` で行数単位加算（004 A-6 の TDnet 実装）。サマリが実データ損失量を過小報告しない |
| T-5 | mode 選択（`--job-mode` / `RUN_MODE`） | 旧 `phase5_bq_insert`（streaming insert 版）を resume / full モードから呼ぶ | 新 `phase5_bq_insert_load` / `phase5_bq_insert_finalize` に寄せ、`insert_rows_json` 経路を段階的に削除（004 C-5）。暫定的に残す場合は用途をコードコメントで限定 |
| T-6 | `phase2_vision_batch` のテキスト代入 | Gemini Vision 結果を `doc.text = text` で直接セットし `_normalize_page_text` を経由しない（L804-807） | **TODO**: 次回 Phase 2 改修時に `_normalize_page_text` または同等のサロゲート除去を通す。現時点で Gemini API がサロゲートを返す実績はないが、3rd-party API の挙動変更で `_save_ai_prepare_state` が同じ `UnicodeEncodeError` で死ぬリスクあり。**過去事故**: 2026-04-25 PyPDF2 経由で同エラー発生（`tdnet-ai-prepare-28cd5`） |
| T-7 | テキスト品質の閾値判定（`_MIN_TEXT_LEN` 比較） | `len(text)` で直接比較する（`[PAGE N]` マーカー文字列が実コンテンツ長を水増しし、pdfminer / Vision フォールバックが発動しない） | `_content_length(text)` を使い、`PAGE_MARKER_PATTERN` 除去後の非空白トークン数で判定する。Phase 2 Vision 結果はマーカーを含まないため `len(text)` で判定してよい（コメント明記済み）。**過去事故**: 2026-05-06 全カテゴリ約 2,100 DOC がマーカーのみで BQ 格納 |
| T-8 | `MAIN_CATEGORY` のカテゴリ名正規化（ファイル名由来 `/` 欠落） | `_sanitize()` が `/` を除去するため、ファイル名から復元した `main_category` が `受注高受注残高` のような不正値になる。その値がそのまま BQ に保存される | `_FILENAME_ALIASES` dict（`VALID_CATEGORIES` から自動生成）を `_correct_category_by_title` の先頭で適用し正規形に戻す。`VALID_CATEGORIES` に `/` を含む新カテゴリを追加すれば自動的に対応される。**過去事故**: 2026-05-18 `受注高受注残高`（スラッシュなし）が 30 DOC / 46 rows BQ に蓄積（BQ UPDATE で修正済み）。根本は `make_filename()` の `_sanitize(category)` 呼出し — DL側の修正は今後の検討課題 |

### 汎用ルールの TDnet 適用ポイント

ルール本体は 004 / 078 を参照。以下は `tdnet_load_parallel.py` で特に注意すべき該当箇所。

| 汎用ルール | TDnet での該当箇所 |
|---|---|
| 004 A-3 silent except continue | `phase_ai_image_vision_ocr`（Phase 2）/ ベクトルインデックス作成（Phase 4 後段）の individual continue-on-error は、main の error counter に合流させる。合流していないと A-1/A-2（exit 0 嘘）と重なって完全ブラックアウト |
| 004 A-8 下流 verify | chain script は `gcloud` exit code だけで次 batch を発火せず、BQ `AI_STATUS='completed'` 件数 / GCS `_SUCCESS` マーカー / 出力 blob 数で実測 verify。`013-2_monitor_backfill.md` の stall 検知と併用 |
| 004 B-4 多段 write 非原子 | `phase_ai_prepare` の state.json 保存 → `_update_ai_status(pending→pending_gemma)` は非原子。state 保存後に status update が失敗すると、次回実行で state は拾えるが DB は pending のまま → **二重 gemma 推論**。DB update を先、state 保存を後の順序に（再実行で自己修復可能に） |
| 004 B-5 cleanup 分離 | `_cleanup_ai_state` は state（resume 用）なので **成功パス限定**（B-1 と同じ）。一方、`analysis_*_input.jsonl` / `ai_finalize_upload_*.jsonl` / `load_upload_*.jsonl` のような一時 upload ファイルは **`finally` で即削除**（14日 lifecycle に任せない） |
| 004 C-2 大量 I/O ストリーム化 | `_save_ai_prepare_state` / `_load_ai_state` / `_save_backfill_state` / `_load_backfill_state` は NDJSON stream write → GCS upload、読込は `blob.open("r")` 行ストリーム。19K doc で 400MB 級、一括は OOM 確定。Gemma worker 側の `gemma_CURRENT.jsonl` は 078 G-1 参照 |
| 004 C-3 `list_blobs` prefix 絞り | `phase1_scan_and_extract` の `list_blobs("tdnet/")` は階層 prefix（`tdnet/{YYYYMM}/` 等）に絞るか、BQ に blob metadata index を別途持つ。backfill で数百万 blob の list を避ける |
| 004 C-4 I/O 並列化 | `phase1_extract_for_docs`（ai-prepare 側 PDF DL）は `ThreadPoolExecutor(max_workers=20-50)` で並列化。daily / backfill どちらもコンテナ実行上限に収める |
| 004 A-7 summary 出力 | `full` / `submit` / `resume` / `load` / `ai-prepare` / `ai-finalize` 各モード終了時に `processed` / `skipped` / `errors` を logger 出力 |
| 004 F-1 deploy 前スモーク | Phase 5 のような signature 依存関数を触ったら `py_compile` + 1件スモーク。T-1 の過去事故（7bd8f `NameError`）はこれで検出可能だった |

### 改修時チェックリスト

`scripts/tdnet_load_parallel.py` を変更する時に必ず確認する。

- [ ] T-1: Phase 5 系を触ったら `py_compile` + 数件スモーク（BQ Load Job 実行確認まで）
- [ ] T-2: ファイル名 parser の失敗 fallback で値を捏造せず raise
- [ ] T-3: chunk ↔ doc マッピング key は `(doc_id, chunk_index)` tuple
- [ ] T-4: バッチ insert の error カウントは行数単位（`len(failed_rows)`）で加算
- [ ] T-5: 新 Phase 5 系（load / finalize）を呼び、旧 `phase5_bq_insert` を新規経路で使わない
- [ ] 004 §バッチジョブ・ETL アンチパターン集 A-1〜F-1 全項目（特に A-3 / A-8 / B-4 / B-5 / F-1 の TDnet 該当箇所）
- [ ] 078 §7 Gemma ↔ Gemini 結合部アンチパターン G-1〜G-3（ai-prepare / ai-finalize を触る場合）

### 関連

- `004_coding_conventions.md §バッチジョブ・ETL アンチパターン集` — 汎用ルール（本節の前提）
- `078_gemma4_operation.md §7 Gemma ↔ Gemini 結合部アンチパターン` — Gemma worker / Gemini Batch 結合部

---

## PDF処理戦略

### ライブラリ構成（PDF前処理）

用途別に使い分ける：

| ライブラリ | 用途 |
|-----------|------|
| **PyMuPDF** | 本文テキスト抽出・ページMarkdown化・座標付き抽出 |
| **pdfplumber** | 罫線あり表の抽出（PyMuPDFより高精度） |
| **OCRmyPDF** | スキャンPDF・画像PDF専用（中小企業開示で出現）|

extract_adapter の抽出0件バグ調査時、まずスキャンPDFかどうか確認し、該当ならOCRmyPDFを前段に挟む。表抽出精度改善時はpdfplumberを試す。

### PyMuPDF 選定理由

**TDnet ロード（決算短信・決算説明資料・月次開示）の主抽出ライブラリに PyMuPDF を採用した根拠**（2026-05-21 Gemma専用パイプライン化時に PyPDF2 → PyMuPDF 換装）。

#### TDnet ロードの主抽出に PyMuPDF を使う理由

| 比較軸 | PyMuPDF | pdfplumber | 選定根拠 |
|--------|---------|-----------|---------|
| テキスト抽出速度 | ◎ 高速 | △ 遅い | バックフィル 13M 行規模で速度差が顕在化 |
| ページ境界マーカー | ◎ `[PAGE N]` を自前で挿入しやすい | △ page オブジェクト単位で取得は同等 | チャンク分割の `separators` 先頭に `\n\n[PAGE` を指定する設計と親和性が高い |
| フォントサイズ・bbox 取得 | ◎ `get_text("dict")` でブロック単位に構造化 | △ `chars` 属性から自前集計が必要 | スライド見出し検出ヘルパー（A-1）の実装コストが低い |
| 既存 Docker イメージへの収録 | ◎ 換装時に収録済み | — | 追加 Dockerfile 修正不要 |

#### 月次開示が pdfplumber を使う理由（用途分担）

月次開示（`extract_monthly_data.py`、`build_monthly_extractor.py`）は **数値テーブル（罫線表）の正確な列・行抽出** が主目的であり、`pdfplumber.extract_tables()` の罫線認識精度が PyMuPDF より高い。両者は競合ではなく PDF の性質による用途分担：

- **スライド型 PDF**（決算説明資料）: テキスト + レイアウト構造 → PyMuPDF `get_text("dict")`
- **罫線表 PDF**（月次開示・財務諸表）: テーブル抽出精度重視 → pdfplumber

#### `get_text("text")` vs `get_text("dict")` の使い分け

| モード | 取得内容 | 用途 |
|--------|---------|------|
| `get_text("text")` | プレーンテキストのみ | 現行の本文抽出（決算短信・月次開示など全般） |
| `get_text("dict")` | `{"blocks": [{"lines": [{"spans": [{"size": 18.0, "bbox": (x0,y0,x1,y1), "text": "..."}]}]}]}` — フォントサイズ・位置情報付き | スライド見出し検出（A-1）。大きいフォント + 上部 1/4 bbox → 見出し判定 |

A-1 実装は「ライブラリ新規追加」ではなく「既存 PyMuPDF の別 API モードを追加利用」にすぎない。

### 難易度別ルーティング（LLM呼び出し戦略）

```
① PyMuPDF でテキスト抽出成功 → regex で数値取得（コスト最小）
② regex 失敗 → Gemini 2.5 Flash（速い・安い）
③ 画像PDF or 表崩れ → Gemini 2.5 Pro または Claude PDF（精度重視）
```

現状「失敗→Gemini一律」だが、PDF種別判定ロジック付きの3段構成への改修候補。`build_monthly_extractor.py` / `tdnet_load_parallel.py` のGeminiフォールバック部分が対象。
- `api/002_bigquery.md` — Load Job vs streaming insert の選択指針

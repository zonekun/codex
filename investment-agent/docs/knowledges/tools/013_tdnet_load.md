# TDnet 適時開示 ETL スクリプト（tdnet_load_parallel.py）

**カテゴリ**: tools
**作成日**: 2026-03-01
**更新日**: 2026-04-20（日次 scheduler 一時停止中 — 下記 🚨 参照）
**ステータス**: 有効（ただし日次 scheduler PAUSED）

---

## 🚨 日次 scheduler 一時停止中（2026-04-20 JST〜）

### 何を止めたか

- **Cloud Scheduler**: `tdnet-load-daily-daily`（us-west1、Tue-Sat 02:00 JST）を **PAUSED**
- 影響: 日次 TDnet ロード + AI 処理（ai-prepare → Gemma → ai-finalize）が自動実行されない
- 手動でロードが必要な場合は 1回ずつ手動実行 or scheduler RESUME

### 停止理由（2026-04-20 backfill batch A 7bd8f 事故で発覚）

`scripts/tdnet_load_parallel.py` の新アーキ（2026-04-17 導入）に設計課題が複数あり、日次ジョブが初回稼働（2026-04-21 02:00 JST 予定）した場合にデータ損失する可能性が高い。主要な 6 件:

| # | 箇所 | 症状 |
|---|---|---|
| 1 | `phase5_bq_insert_finalize` (1926行) | `NameError: bucket` 未定義 — ai-finalize が確実に落ちる |
| 2 | `run_tdnet_batch_etl` 全 3 モード | 例外を内部キャッチして `errors=1` 設定のみ、`sys.exit()` 無し → Cloud Run は **SUCCESS 扱いで exit 0** |
| 3 | Phase 5 内部 try/except (1940, 1468) | 例外を返り値に変換、呼び出し側に投げない |
| 4 | `_cleanup_ai_state` (2201行) | INSERT 失敗後も gate 無しで GCS state 削除 → **リジューム不能・データ消失** |
| 5 | DELETE → INSERT 非トランザクション (2190→2192) | INSERT 失敗時に pending_gemma 行が完全消失 |
| 6 | ai-prepare の DB status 更新順 (2124) | state 保存成功 + status update 失敗で二重 gemma 実行 |

上記 6 件 + TDnet ETL 固有 4 件 (T-7〜T-10) + Gemma/Gemini 結合部 3 件 = 計 13 件。詳細は本ファイル末尾「TDnet ETL 固有の再発防止ルール」（T-1〜T-10）および `078_gemma4_operation.md §7`（G-1〜G-3）参照。

### 再開条件（これを満たすまで RESUME しない）

- [ ] #1 `bucket` NameError 修正（引数追加 or `storage.Client().bucket(BUCKET_NAME)` を関数内で作る）
- [ ] #2 全モードで例外時 `sys.exit(1)` に変更 → Cloud Run が FAILED ステータスになる
- [ ] #3 Phase 5 内部の try/except を撤去 or 再 raise
- [ ] #4 `_cleanup_ai_state` を `if errors == 0 and processed > 0` で gate
- [ ] #5 INSERT 失敗時のロールバック（INSERT 先行 → 成功確認後に DELETE、or 旧データは別カラムで退避）
- [ ] #6 DB status update を state 保存より先に実行、かつ失敗時 state 削除
- [ ] ローカルで数件 doc で 3 モード全部スモークテスト（NameError 類をコンパイル+1件実行で検出）
- [ ] `phase5_bq_insert_load` 側も同じ設計欠陥がないか棚卸し済み（bucket 受け取りは OK だが exit code 問題は残る）
- [ ] 2CPU/8Gi でリソース再確認（4CPU/16Gi は `load_table_from_file` の HTTP upload buffer 問題への対症療法だった。`load_table_from_uri` 化後は 2CPU/8Gi で RSS 2.5GB 収まる実測あり）

### RESUME コマンド

```bash
# 上記チェックリスト全部クリア → deploy 完了後に実行
gcloud scheduler jobs resume tdnet-load-daily-daily --location=us-west1
# 次回起動: 直近の Tue-Sat 02:00 JST
```

### 背景インシデント

- 2026-04-20 backfill batch A ai-finalize 7bd8f: Phase 5 で NameError → 19,156 doc が BQ に入らずロスト
- コンテナは exit(0) で「成功」扱い → chain script が誤検知して batch B gemma-runner 起動
- batch B gemma-runner 5n8pz キャンセル、chain process kill で収拾
- バックフィル自体も停止（歯抜け方式は取りやめ）
- 詳細: `013-2_monitor_backfill.md`（インシデント追記予定）

---

## 📌 現況サマリ（2026-04-19）

**本番稼働中のアーキ**: BQロード / AI判定分離、Cloud Workflows 主導（2026-04-17〜）

| コンポーネント | 役割 |
|-------------|------|
| `tdnet-load-daily` | GCS PDF → テキスト抽出 → BQ Load Job（AI_STATUS='pending'、日次 02:00 JST） |
| `ai_processing_flow` Workflows | ai-prepare → Gemma TPU 推論 → ai-finalize オーケストレーション |
| `tdnet-ai-prepare` | OCR + 正規表現月次補正 + state.json 保存（pending_gemma へ） |
| `tdnet-gemma-runner` | bash: TPU v6e-4 作成 → vLLM + worker.py → 削除 |
| `tdnet-ai-finalize` | Gemma結果適用 + Gemini Flash Batch（決算短信のみ受注判定） + Embedding + BQ Insert（completed へ） |

**リソース**（実測後確定）:
- ai-finalize: **1 CPU / 4Gi**（peak RSS 実測 0.13 KB/doc、3万 doc でも 4Gi 収まる）
- 他ジョブ: 1 CPU / 2Gi

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
| `tdnet-gemma-runner` | Cloud Run Job CPU | bash (gcloud wrapper) | 1 | 2Gi | 14400s |
| `tdnet-ai-finalize` | Cloud Run Job CPU | `--job-mode=ai-finalize` | **2** | **8Gi** | 21600s |

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
| `pending_finalize` | Gemma完了、Gemini/Embedding/UPDATE 待ち（短時間状態、クラッシュ検出用） |
| `completed` | AI処理完了 |

### GCS 状態ファイル

```
gs://stock_data_1930932/ai_job/{run_id}/
  ├─ state.json           — ai-prepare 出力（対象doc_id, OCR結果, 正規表現結果, 全文テキスト）
  ├─ gemma_CURRENT.jsonl  — Gemma推論結果（1件ずつappend、preemption resume）
  └─ _SUCCESS             — worker 完了マーカー（callback 不達時の fallback）

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
# ビルド
gcloud builds submit --config cloudbuild/cloudbuild.tdnet-load-daily.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source .
```

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

### バックフィル運用（2026-04-17 確定）

**投入単位: 3ヶ月/回** — TPU 起動オーバーヘッド（~15分 × $4/hr = $1/起動）を最小化しつつ、preemption 長距離走行リスクを抑制。7年バックフィル合計 TPU コスト ~$187（1日単位なら $2,679、1年一括なら $166）。

**task-timeout 要件（3ヶ月単位で必須）:**

| Job | 現状 | 必要 |
|-----|------|-----|
| `tdnet-load-daily` | 3600s | **21600s (6h)** |
| `tdnet-ai-prepare` | 3600s | **21600s (6h)** |
| `tdnet-ai-finalize` | 21600s | 21600s のまま |

**投入時間帯（preemption 最小化、us-central1-b の米中西部需要回避）:**

| 時間帯（JST） | 米中西部現地時間 | 推奨 |
|-------------|---------------|------|
| **平日 15:00〜24:00 JST** | 深夜〜早朝 CT | ✅ **最安全** |
| **週末（土日 全日 JST）** | 週末 | ✅ **最安全** |
| 平日 00:00〜09:00 JST | 業務時間帯 CT | ❌ **避ける** |

Cloud Scheduler での `ai_processing_flow` 起動時刻は **平日 15:00 JST** or **土曜 08:00 JST** が推奨。手動投入時も同様。

根拠: Google 公式「夜・週末は負荷低」、US AI 企業の ML 訓練ピーク時間帯（米西部時間 日中）の回避。ゾーン別 preemption 率の公表データは無く、実測ログでの継続補正を想定。

### バックフィル監視ツール

**→ 詳細は `docs/knowledges/tools/013-2_monitor_backfill.md`**

`scripts/monitor_backfill.py` + `config/backfill/*.yaml` で load → workflows の通し実行を YAML 宣言で発火・監視する。

```bash
# 本番投入（2026-04-19 時点で 10本の config が存在、詳細は 081 参照）
PYTHONUTF8=1 python scripts/monitor_backfill.py config/backfill/XXX.yaml
```

---

## Phase I 完遂記録（2026-04-18 JST）

新アーキ Phase I（初期実装）の検証を 2023-01-04〜06 の 507 doc で完遂。本番稼働可能状態へ。

### 確定した設計ポイント（旧アーキ準拠）

| 観点 | 決定内容 |
|------|---------|
| **チャンク化タイミング** | load は **チャンク化しない**（メタ1行 insert、CHUNK_TEXT=NULL）。ai-finalize で 3カテゴリ（決算短信/決算説明資料/月次開示）のみチャンク分割+Embedding、他カテゴリはメタ1行を維持。旧 `phase5_bq_insert` と同じ構造 |
| **MAIN_CATEGORY 決定** | ai-prepare の `phase1_extract_for_docs` 内で `parse_tdnet_filename()` によりファイル名からカテゴリ取得 → `_correct_category_by_title()` で月次補正 → `state.json` の `pre_main_category` として保存。ai-finalize の `_apply_gemma_results` で `_AMBIGUOUS_OVERWRITE` ({"その他（未分類）"} かつ is_monthly=True のとき月次開示へ上書き) + `_MONTHLY_SUB_CATEGORIES` ルール（SUB に月次開示追加）を適用 |
| **SUB_CATEGORIES 決定** | Gemma 出力（VALID_CATEGORIES でフィルタ）をベースに、旧ロジックの月次開示追加を適用。決算短信のみ Gemini Flash Batch の「受注高/受注残高」判定を差分マージ（他の SUB は Gemma 維持） |
| **BQ 書き込み方式** | `insert_rows_json`（streaming API）を廃止し、**`load_table_from_json`（BQ Load Job）** に統一。streaming buffer に入らず DML（DELETE/UPDATE）即時可、かつ Load Job 自体は無料 |
| **BQ DELETE** | ai-finalize の `_delete_pending_gemma_rows` に `SUBMISSION_DATE BETWEEN` を追加して **partition prune** を効かせる。対象条件は `AI_STATUS != 'completed'`（pending / pending_gemma / pending_finalize / NULL 全て） |
| **state.json 衝突回避** | ai-prepare 冒頭で同 run_id の `gemma_CURRENT.jsonl` / `_SUCCESS` を削除（state.json 自体は `upload_from_string` で上書き）。手動実行時の run_id フォールバックは `uuid.uuid4()`（秒単位 timestamp は衝突リスク） |
| **GCS Lifecycle** | `ai_job/*` を 14 日経過で自動削除（`gs://stock_data_1930932` のバケット lifecycle rule） |
| **Workflows parallel shared 変数** | branch 内の `result: gemma_callback` は **local scope のみ** に書かれる（公式ドキュメント明記）。shared 変数に反映するには別途 `assign` step が必要。現 YAML は `local_callback` / `local_runner_result` に受けてから shared に `assign` する形 |
| **Workflows LRO polling timeout** | `googleapis.run.v2.projects.locations.jobs.run` のデフォルト polling timeout は 1800s (30分)。長時間ジョブは `connector_params.timeout` で明示（本番は 21600s = 6h） |
| **Workflows Callback 認証** | worker.py から `workflowexecutions.googleapis.com` の Callback URL へ POST する際は **OAuth2 Bearer 必須**。bq-loader SA に `roles/workflows.invoker` 付与 + access_token 付与。access_token 失敗時は OIDC ID token (audience=CALLBACK_URL) にフォールバック（`_get_metadata_id_token`） |
| **TPU preempt retry** | Workflows の `gemma_runner_branch` に `retry: max_retries=3, exponential backoff 60→600s`。worker.py は GCS `gemma_CURRENT.jsonl` の done_doc_ids で resume するので冪等 |
| **Cloud Run Job 共有イメージ** | `tdnet-load-daily` / `tdnet-ai-prepare` / `tdnet-ai-finalize` の 3 Job は **同一イメージ**（`us-west1-docker.pkg.dev/.../tdnet-load-daily:latest`）を使い、Cloud Run Job 作成時に `--args=--job-mode=load/ai-prepare/ai-finalize` で切替 |

### 廃棄した設計

| 廃棄 | 理由 |
|------|------|
| TPU VM startup-script 方式（旧 `tpu_vm_startup_gemma.sh`） | TPU v6e-4 VM で startup-script が実行されない / GCS ログが出ない現象。PoC (`poc_gemma4_phaseD_orchestrator.sh`) は最初から SSH 直接方式だった。Cloud Run Job + IAP SSH 方式（`gemma_tpu_runner.sh`）に全面切り替え（詳細: `078_gemma4_operation.md` セクション 5） |
| load 時のチャンク化（旧 `phase4_chunk_only`） | 3カテゴリ判定が load 時点で不可能のため。全 doc をチャンク化すると 3カテゴリ外でも残骸が残り、ai-finalize で上書きされない落とし穴を産んだ。旧アーキ通り「ai-finalize で 3カテゴリのみチャンク化」が正解 |
| worker.py での MAIN_CATEGORY 推定 | `sub_categories[0]` は出力順依存で不安定。MAIN は pre_main_category（ファイル名由来）をベースに ai-finalize 側で決定。worker は `is_monthly` / `sub_categories` のみ返す |
| `insert_rows_json` 方式 | streaming buffer 30-90分 DML 不可 → ai-finalize DELETE が失敗する事故。Load Job へ全面切替 |

### 2026-04-18 実測（507 doc / 2023-01-04〜06）

| フェーズ | 結果 |
|---------|------|
| load (tdnet-load-daily) | 507 doc → **507 row**（メタ1行のみ、CHUNK_TEXT=NULL）✅ |
| ai-prepare | state.json 保存、pre_main_category 付与、AI_STATUS='pending_gemma' ✅ |
| Gemma TPU 推論 | 507/507 成功、エラー0 件、~22分 ✅ |
| Gemini Flash Batch（決算短信のみ） | 27 doc 対象、受注判定マージ ✅ |
| Embedding Batch（3カテゴリ） | チャンク分割 + 768次元ベクトル付与 ✅ |
| ai-finalize（BQ DELETE+INSERT） | Load Job で 1,936 row insert、Workflows 自動完了 ✅ |
| 最終 BQ (completed) | **507 doc / 1,936 row**、MAIN 20+カテゴリ分布（自己株式取得 200 / 月次開示 87 / 決算短信 27 等）、EMBEDDING=1,542 行、CHUNK_TEXT=1,543 行 |
| コスト | TPU spot ~$2.3 + Cloud Run ~$0.1 + Workflows ~$0 + Embedding < $0.01 = **~$2.4 / 507 doc** |

### Phase I で解消した事故

| # | 事故 | 解消 |
|---|------|------|
| 1 | startup-script 未実行 → TPU 不動 | Cloud Run Job + SSH IAP 方式に切替 |
| 2 | `--tunnel-through-iap` が効かない | `gcloud alpha compute tpus tpu-vm ssh` に変更 |
| 3 | Callback URL POST が 401/403 | access_token + workflows.invoker + OIDC フォールバック |
| 4 | BQ DELETE が streaming buffer で失敗 | Load Job に切替 |
| 5 | parallel shared 変数が反映されない（gemma_summary=null） | branch 内で `local_*` に受けて `assign` で shared へ |
| 6 | Workflows succeeded なのに finalize 失敗 | finalize 内例外が try/except で握り潰されていた。Load Job 化で根絶 |
| 7 | 3カテゴリ外 doc が複数チャンク行で残存 | load 時チャンク化廃止、旧アーキ準拠のメタ1行 |
| 8 | MAIN=`sub_categories[0]` で不安定 | ファイル名由来 pre_main_category + `_AMBIGUOUS_OVERWRITE` ルール |

### 関連ドキュメント

- `078_gemma4_operation.md` セクション 5「Phase I 本番実装ノウハウ」— Cloud Run Job + SSH IAP 詳細、必要 IAM 権限、Windows 落とし穴、実測
- `080_workflows_runbook.md` — Workflows の parallel shared / connector_params / callback 等の汎用知見
- `api/002_bigquery.md` — Load Job vs streaming の使い分け、streaming buffer 回避

---

## 📋 保留中の TODO

### 2023 バックフィル完遂後の全件整合性チェック（必須、2026-04-20 追加）

各 batch ai-finalize 完了時（特に 11 batch 全完遂時）に以下 SQL で `AI_STATUS != 'completed'` 残存を確認:

```sql
SELECT
  CASE
    WHEN TICKER BETWEEN '1301' AND '1909' THEN '01'
    WHEN TICKER BETWEEN '190A' AND '232A' THEN '02'
    WHEN TICKER BETWEEN '2330' AND '3023' THEN '03'
    WHEN TICKER BETWEEN '3024' AND '3690' THEN '04'
    WHEN TICKER BETWEEN '3691' AND '4410' THEN '05'
    WHEN TICKER BETWEEN '4412' AND '5288' THEN '06'
    WHEN TICKER BETWEEN '5290' AND '6366' THEN '07'
    WHEN TICKER BETWEEN '6367' AND '7130' THEN '08'
    WHEN TICKER BETWEEN '7131' AND '7902' THEN '09'
    WHEN TICKER BETWEEN '7906' AND '9063' THEN '10'
    WHEN TICKER BETWEEN '9064' AND '9997' THEN '11'
  END AS batch,
  COUNT(DISTINCT DOC_ID) AS unprocessed_docs
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE SUBMISSION_DATE BETWEEN '2023-01-01' AND '2023-12-31'
  AND (AI_STATUS != 'completed' OR AI_STATUS IS NULL)
GROUP BY batch ORDER BY batch
```

**期待値**: 全 batch `unprocessed_docs = 0`。

**非ゼロの場合の対処**:
1. 原因調査: ai-prepare ログで「抽出失敗」「blob not found」等を grep
2. 該当 ticker + 日付範囲を `tdnet-load-daily` で再 load（dedup で既存完了行はスキップされる）
3. `ai_processing_flow` 再起動で救済

2017-2022 バックフィルでも同様のチェックを年毎 or ticker range 毎に実施。

### 2026-01-19〜02-27 漏れ分リカバリ（約 2,900 件、今日中実施予定）

**背景**: 初期バックフィル時に TICKER 絞り込み実行（`TICKER_FROM=5000,TICKER_TO=9999` 等）で途中切れ仮説。02-10 の漏れ 177件は ticker **4356〜8132** に集中（1000〜3000番台は漏れゼロ）。

**区間別漏れ**（合計 ~2,924 件）:
- 01-19〜01-26: 34 件
- 01-27〜01-30: 185 件
- 02-02〜02-06: 525 件
- 02-09〜02-13: **1,295 件**（ワースト日 02-13 -643, 02-12 -298, 02-10 -218）
- 02-16〜02-20: 198 件
- 02-24〜02-27: 187 件

**前提**（完了済）:
- Phase I の Callback 403 修正検証 ✅（2023-01-04〜06 の 507 doc で確認）
- 新アーキ `ai_processing_flow` 本番稼働 ✅（batch#1〜#4 完遂）

**実施プラン**（2026 バックフィル #5-#11 完遂後に着手）:

0. **GCS PDF 残存確認**: `gs://stock_data_1930932/tdnet/<ticker>/20260119〜20260227_*.pdf` をサンプリング確認（TDnet 公式 30-90日保持、境界付近）
1. **load 実行**: `tdnet-load-daily` で DATE_FROM=20260119, DATE_TO=20260227。dedup で既存 completed 行はスキップ、漏れ分のみ BQ insert
2. **AI 実行**: `ai_processing_flow` で date_from/date_to 同範囲。想定 ~2,900 doc、peak RSS ~500 MB、TPU O/H 込みで ~$3
3. **完遂確認**: `SELECT COUNT(*) WHERE AI_STATUS != 'completed'` が 0 になるまで

**config**: `config/backfill/2026_gap_recovery_jan_feb.yaml` 事前作成済

```bash
# 実行コマンド
PYTHONUTF8=1 python scripts/monitor_backfill.py config/backfill/2026_gap_recovery_jan_feb.yaml
```

**完遂基準**: GCS PDF 数 = BQ DOC_ID 数（差 0〜1件）

---

### `ai_processing_flow` 定期スケジューラ化（2026-04-19 時点、保留）

**現状**: scheduler 未設定。バックフィル中（2023年）は手動起動のみ。
**保留理由**: 2023 バックフィル完遂までは scheduler 動かさない（横取りリスク回避）。

**着手条件**: 2023 バックフィル完遂後。

**必要な workflow 改修**（scheduler 起動前に必須）:

現在 `resolve_date_range` step が `MIN(SUBMISSION_DATE) WHERE AI_STATUS='pending'` で最古pending月を自動選択する挙動 → scheduler が args なしで起動すると過去深堀りバックフィル分を横取りする。

**修正案**: `resolve_date_range` の SQL に 30日以内ガードを入れる。

```yaml
# workflows/ai_processing_flow.yaml - find_oldest_pending step
query: |
  WITH candidate AS (
    SELECT MIN(SUBMISSION_DATE) AS min_d
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE AI_STATUS = 'pending'
      AND SUBMISSION_DATE >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
  )
  SELECT
    FORMAT_DATE('%Y%m%d', DATE_TRUNC(min_d, MONTH)) AS dfrom,
    FORMAT_DATE('%Y%m%d', LAST_DAY(DATE_TRUNC(min_d, MONTH))) AS dto
  FROM candidate WHERE min_d IS NOT NULL
```

これで:
- **weekly scheduler（args なし）**: 30日以内の最古pending月を自動選択（直近 1-2 週間）
- **手動バックフィル（args で 2023 指定）**: 明示指定なのでそのまま処理
- **過去バックフィル pending**: 30日以前なので scheduler は触らない

**推奨スケジュール（候補）**:
- 毎週火 15:00 JST（`0 15 * * 2`）: 月〜金の load 分を週明けで一気に処理
- 毎週土 08:00 JST（`0 8 * * 6`）: 週末の preemption 低時間帯

週次 1 WF で 1週間 pending (~2,500 doc) 処理、TPU 起動 O/H $1 のみでコスト最安。

---

# 🗄️ 履歴アーカイブ（参考、直接参照不要）

以下は過去の実装・調査・廃止予定の内容。運用時は冒頭の現況サマリと「新アーキ」本文で完結する。

---

## 旧アーキ（tdnet-load-parallel、段階的廃止予定）

以下は従来の 5フェーズ一気通貫 ETL。`--job-mode=full` 指定時に走る（後方互換）。

### 概要

GCS 上の TDnet 適時開示 PDF を読み込み、テキスト抽出 → Gemini Batch Prediction（カテゴリ分析）→ Batch Embedding → BigQuery ETL を行うスクリプト。

Vertex AI Batch Prediction を利用し、オンライン予測比 **約50%コスト削減** + RPM制限なし。

## ETL パイプライン全体像

```
[TDnet 公式サイト]
  ↓
scripts/tdnet_download.py   ... PDF を GCS へダウンロード
  gs://stock_data_1930932/tdnet/{コード4桁}/{ファイル名}.pdf
  ↓
scripts/tdnet_load_parallel.py  ... GCS PDF → BQ ETL（5フェーズ構成）
  Phase 0: GCS index CSV → DOC_ID:開示時刻(HH:MM) マッピング構築
  Phase 1: GCS blob 走査 → テキスト抽出（PyPDF2 → pdfminer フォールバック）
  Phase 2: Gemini Vision Batch（テキスト抽出失敗PDF の OCR）
  Phase 3: Gemini 分析 Batch（統合プロンプト: 月次判定 + サブカテゴリ抽出）
  Phase 4: チャンク化 → Batch Embedding（text-embedding-004）
  Phase 5: BQ STOCK.TDNET_DOCUMENTS_ENHANCED へ insert_rows_json
```

---

## 実行方法

```bash
# 日次（JST昨日）— デフォルト / Cloud Run 日次バッチ
PYTHONUTF8=1 python scripts/tdnet_load_parallel.py

# モード指定（t=JST今日, y=JST昨日）
PYTHONUTF8=1 python scripts/tdnet_load_parallel.py --mode t

# 日付範囲指定（--from 指定時は --mode 不要）
PYTHONUTF8=1 python scripts/tdnet_load_parallel.py --from 20260301 --to 20260331

# 単日指定（--to 省略時は --from と同日）
PYTHONUTF8=1 python scripts/tdnet_load_parallel.py --from 20260330

# TICKER 範囲を絞って実行
PYTHONUTF8=1 python scripts/tdnet_load_parallel.py --from 20250101 --to 20251231 \
    --ticker-from 1000 --ticker-to 4999
```

引数の優先順位: `--from`/`--to` > `--mode` > ファイル冒頭の `DATE_MODE`

### 日付モード

| モード | 動作 | 備考 |
|--------|------|------|
| `y` | JST 昨日 | デフォルト。Cloud Run 日次バッチ用 |
| `t` | JST 今日 | |
| `1` | `DATE_SINGLE` 固定日 | ファイル冒頭で設定 |
| `r` | `DATE_FROM` 〜 `DATE_TO` | ファイル冒頭で設定 |

**タイムゾーン**: `datetime.now(JST).date()` で常に JST 基準。Cloud Run（UTC）でもローカル（JST）でも同じ結果。

---

## 5フェーズ処理詳細

### Phase 0: 開示時刻マッピング構築

- GCS 上の `tdnet/index_{from}_{to}.csv` を走査し、対象期間と重なる index CSV を読み込む
- `pubdate` 列（`YYYY-MM-DD HH:MM:SS`）から DOC_ID → `HH:MM` のマッピングを構築
- Phase 1 で各 DocInfo の `disclosure_time` にセット → Phase 5 で BQ `DISCLOSURE_TIME` カラムに格納
- index CSV が存在しない場合（古いデータ等）は `DISCLOSURE_TIME = NULL` になる

### Phase 1: スキャン & テキスト抽出（同期）

- GCS blob を走査し、日付・TICKER でフィルタ
- BQ 取込済みファイルをスキップ（重複防止）
- テキスト抽出: PyPDF2 → pdfminer.six フォールバック（3段階）
- `_correct_category_by_title()` で正規表現による月次開示補正（Gemini 呼び出し前）

### Phase 2: Gemini Vision Batch（OCR）

- Phase 1 でテキスト抽出失敗（< 50文字）の PDF を Gemini Vision でバッチ OCR
- GCS 上の PDF を `fileData.fileUri` で直接参照（ダウンロード不要）
- モデル: `gemini-3-flash-preview`

### Phase 3: Gemini 分析 Batch（統合プロンプト）

**統合プロンプト**: 月次判定 + サブカテゴリ抽出を1回のリクエストで実行。

**モデル**: `gemini-3-flash-preview`（全カテゴリ統一、グローバルエンドポイント）

| MAIN_CATEGORY | 備考 |
|--------------|------|
| 決算短信 | サブカテゴリ抽出あり |
| 決算説明資料 | サブカテゴリ抽出あり |
| その他（未分類）, 業績予想, 大型受注・契約 等 | 月次判定のみ採用（サブカテゴリは破棄） |

- **単一バッチジョブ**として投入（flash/pro 分割なし）
- `generation_config`: `response_mime_type="application/json"`, `temperature=0.1`
- 出力: `{ "is_monthly": bool, "sub_categories": [...] }`
- **sub_categories を採用するのは決算短信・決算説明資料のみ**（既存ロジック維持）

### Phase 4: チャンク化 → Batch Embedding

**Embedding 対象カテゴリ**: 決算短信・決算説明資料・月次開示のみ。それ以外のカテゴリはメタデータのみ（CHUNK_TEXT=NULL, EMBEDDING=NULL の1行）を BQ に書き込む。

| 項目 | 値 |
|------|----|
| モデル | `text-embedding-004` |
| 次元数 | 768 |
| API | Vertex AI Batch Prediction（`google-genai` SDK） |
| 入力形式 | `{"content": "text"}` |
| task_type | 指定不可（Batch API 制限）|
| 結果マッチング | `instance.content` でテキスト照合 |

チャンク設定:
```python
RecursiveCharacterTextSplitter(
    chunk_size=400, chunk_overlap=50,
    separators=["\n\n", "\n", "。", "、", " "]
)
```

### Phase 5: BQ Insert

- `insert_rows_json` で 100件ずつバッチ挿入
- `DISCLOSURE_TIME`（`HH:MM` or NULL）を含む全カラムを書き込み
- 成功時にベクトルインデックスを作成/更新

---

## 月次開示カテゴリの判定ロジック

### 1段目: 正規表現（`_correct_category_by_title`）

ファイル名パース時に即座に補正。コストゼロ。

```python
_MONTHLY_DOC_PATTERN = re.compile(
    r"月次|月度売上|売上速報|売上高速報|売上推移速報|月度業績|受注速報"
    r"|月度連結|月度販売|月次売上|月次業績|月次報告|月次データ|月次速報"
)
```

### 2段目: Gemini 統合プロンプト

正規表現で拾えなかったものを Gemini で判定。プロンプト定義:

> 以下のTDnet適時開示のタイトルは「月次開示」（月次売上・月次業績・月次受注、月次顧客数等の、企業業績に影響ある定期的な月次報告）ですか？

### カテゴリ補正ルール

| MAIN_CATEGORY | Gemini=月次と判定した場合の動作 |
|--------------|-------------------------------|
| `その他（未分類）` | MAIN_CATEGORY を `月次開示` に**上書き** |
| `業績予想`, `大型受注・契約`, `受注・契約`, `業績の重要な先行指標`, `受注高/受注残高` | MAIN_CATEGORY は**保持**し、`月次開示` を SUB_CATEGORIES に追加 |
| 上記以外 | **何もしない** |

---

## マルチプラットフォーム対応

| 環境 | GCS / BQ 認証 | Gemini Batch 認証 |
|------|-------------|--------------|
| `colab_personal` | Colab Secrets `GCP_SA_KEY`（SA キー JSON） | 同左 |
| `colab_enterprise` | ADC | ADC |
| `cloudrun` | Attached SA の ADC | ADC |
| `local` | SA キーファイル（`settings.google_application_credentials`） | 同左（scopes: `cloud-platform`） |

---

## Cloud Run Job 設定

| 項目 | 値 |
|------|----|
| Job 名 | `tdnet-load-parallel` |
| Image | `us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-load-parallel:latest` |
| SA | `bq-loader@gmailpj-357912.iam.gserviceaccount.com` |
| リージョン | `us-west1` |
| タイムアウト | 43200秒（12時間）— バックフィル用。日次は3600秒で十分 |
| メモリ | 3Gi |
| maxRetries | 1 |
| スケジューラー | `tdnet-load-parallel-daily`（火〜土 02:00 JST） |

### 日次フロー

```
tdnet-download-daily（23:50 JST）→ GCS保存
  ↓
tdnet-load-parallel-daily（翌02:00 JST）→ DATE_MODE="y" → JST昨日分を処理（full mode）
```

### バックフィルフロー（submit/resume モード）

```
Cloud Run Job (submit): Phase 1→2→3(投入のみ) → GCS に state 保存 → exit (CPU 30-40分)
  ↓
Cloud Functions (tdnet-backfill-poller): 5分おきにバッチ状態ポーリング
  ↓ SUCCEEDED 検知
Cloud Run Job (resume): state 読込 → Phase 3(結果適用)→4→5 (CPU 8-10分)
```

**環境変数（`--update-env-vars` で指定）:**

| 変数 | 説明 | 例 |
|------|------|----|
| `DATE_FROM` | 開始日 | `20240101` |
| `DATE_TO` | 終了日 | `20241231` |
| `RUN_MODE` | `full`(default) / `submit` / `resume` | `submit` |
| `TICKER_FROM` | ticker 下限 | `1301` |
| `TICKER_TO` | ticker 上限 | `174A` |

**投入コマンド例:**
```bash
# submit（バッチ投入→即exit）
gcloud run jobs execute tdnet-load-parallel --region us-west1 \
  --update-env-vars DATE_FROM=20240101,DATE_TO=20241231,RUN_MODE=submit,TICKER_FROM=1301,TICKER_TO=174A

# resume（手動。通常はポーラーが自動起動）
gcloud run jobs execute tdnet-load-parallel --region us-west1 \
  --update-env-vars DATE_FROM=20240101,DATE_TO=20241231,RUN_MODE=resume
```

**Cloud Functions ポーラー:**
- 名前: `tdnet-backfill-poller`
- スケジューラ: `tdnet-backfill-poller`（5分おき）— バックフィル中のみ resume。不要時は pause。
- ロック機構: `.resume_triggered` ファイルで2重起動防止
- state ファイル: `gs://stock_data_1930932/batch_prediction/tdnet/backfill_state_{from}_{to}.json`

### バックフィル実績

**推奨設定: 3000件/バッチ、3Gi メモリ**

| # | 件数 | メモリ設定 | 最大使用 | 利用率 | submit CPU | resume CPU |
|---|------|----------|---------|--------|-----------|-----------|
| #1 | 1,904 | 3Gi | ~430MB | ~14% | 42分 | 8分 |
| #2 | 2,389 | 2Gi | 1,812MB | 88.5% | 35分 | 9分 |
| #3 | 2,807 | 3Gi | 2,452MB | 79.8% | 37分 | 10分 |

| フェーズ | submit | resume |
|---------|--------|--------|
| Phase 1 (blob走査+PDF抽出) | ~0.8秒/件 | — |
| Phase 3 (Gemini バッチ待ち) | Vertex AI側 40-120分（キュー依存） | 0 |
| Phase 4+5 (Embedding+BQ Insert) | — | ~0.2秒/件 |

**メモリの支配要因**: Embedding ベクトル保持（件数 × チャンク数 × 768次元 × 4bytes）。3000件で約2.5GB。

### 2024年バックフィル計画（#4〜#23）

**残り20バッチ / 58,852件（3000件/バッチ）:**

| # | ticker FROM | ticker TO | 件数 |
|---|-----------|----------|------|
| #4 | 2503 | 2914 | 3,009 |
| #5 | 2915 | 3221 | 3,006 |
| #6 | 3222 | 3547 | 3,022 |
| #7 | 3548 | 3891 | 3,000 |
| #8 | 3892 | 4194 | 3,007 |
| #9 | 4196 | 4488 | 3,006 |
| #10 | 4489 | 4814 | 3,004 |
| #11 | 4816 | 5255 | 3,004 |
| #12 | 5256 | 5938 | 3,018 |
| #13 | 5939 | 6247 | 3,015 |
| #14 | 6248 | 6573 | 3,034 |
| #15 | 6574 | 6957 | 3,005 |
| #16 | 6958 | 7242 | 3,000 |
| #17 | 7244 | 7610 | 3,013 |
| #18 | 7611 | 7949 | 3,004 |
| #19 | 7950 | 8334 | 3,003 |
| #20 | 8336 | 8958 | 3,012 |
| #21 | 8960 | 9284 | 3,010 |
| #22 | 9285 | 9719 | 3,014 |
| #23 | 9720 | 9997 | 1,666 |

**AI料金概算（gemini-3-flash-preview + Embedding 3カテゴリ限定）:**

| モデル | リクエスト数 | コスト |
|--------|------------|--------|
| Flash Vision (Phase 2) | ~18,800 | ~$12 |
| Flash 分析 (Phase 3) | ~16,400 | ~$20 |
| Embedding text-embedding-004 | ~329,000チャンク | ~$49 |
| **合計** | | **~$81（約¥12,000）** |

※ Pro→Flash切替で$325→$20（94%削減）、Embedding 3カテゴリ限定で$224→$49（78%削減）

**所要時間:** 1バッチ約1.5-3時間、全逐次で30-60時間（並列投入で短縮可）

### Dockerfile 依存パッケージ

```
PyPDF2, google-cloud-storage, google-cloud-bigquery,
google-genai, langchain-text-splitters, pdfminer.six
```

> **注意**: `google-cloud-aiplatform` は旧SDKのため不要。バッチ操作は `google-genai` で行う（→「SDK ルール」セクション参照）。

### ビルド & デプロイ

```bash
# ビルド
gcloud builds submit --config cloudbuild/cloudbuild.tdnet-load-parallel.yaml

# Job 更新
gcloud run jobs update tdnet-load-parallel \
  --image us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-load-parallel:latest \
  --region us-west1 --task-timeout 3600

# 手動実行
gcloud run jobs execute tdnet-load-parallel --region us-west1
```

---

## TICKER 範囲絞り込み（`--ticker-from` / `--ticker-to`）

年単位バックフィル時に TICKER を分割して並列実行可能。

```bash
gcloud run jobs update tdnet-load-parallel --region us-west1 \
    --args="--from=20250101,--to=20251231,--ticker-from=1000,--ticker-to=4999"
gcloud run jobs execute tdnet-load-parallel --region us-west1 --async
```

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

## バッチ予測中間ファイル（GCS）

バッチジョブの入出力 JSONL は以下に保存される:

```
gs://stock_data_1930932/batch_prediction/tdnet/
  vision_{timestamp}_input.jsonl / output/
  analysis_{timestamp}_input.jsonl / output/
  embed_{timestamp}_input.jsonl / output/
```

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

## 取り込み漏れ調査（2026-04-17 実施）

**対象**: 2026年の `STOCK.TDNET_DOCUMENTS_ENHANCED`
**比較基準**: GCS `tdnet/{TICKER4桁}/{YYYYMMDD}_*.pdf` のファイル名から日付別に集計（全 604,053 blob）
**BQ側**: `COUNT(DISTINCT DOC_ID) GROUP BY SUBMISSION_DATE`

### 未ロード期間（2つに分かれる）

**① 2026-01-19 〜 2026-02-27 — 累計約 2,900件**

| 区間 | GCS | BQ | 漏れ |
|---|---:|---:|---:|
| 01-19〜01-26 | 545 | 511 | 34 |
| 01-27〜01-30 | 1,418 | 1,233 | 185 |
| 02-02〜02-06 | 2,252 | 1,727 | 525 |
| 02-09〜02-13 | 5,215 | 3,920 | 1,295 |
| 02-16〜02-20 | 1,027 | 829 | 198 |
| 02-24〜02-27 | 921 | 734 | 187 |

ワースト日: 02-13 (-643), 02-12 (-298), 02-10 (-218), 02-06 (-169)。

**② 2026-03-02 〜 2026-04-03 — 正常（差0〜1件）**

日次ジョブが安定稼働。

**③ 2026-04-06 〜 2026-04-16 — 累計約 1,100件（継続中）**

| 日付 | GCS | BQ | 漏れ |
|---|---:|---:|---:|
| 04-06 | 139 | 46 | 93 |
| 04-07 | 110 | 43 | 67 |
| 04-08 | 158 | 36 | 122 |
| 04-09 | 158 | 75 | 83 |
| 04-10 | 280 | 155 | 125 |
| 04-13 | 245 | 116 | 129 |
| 04-14 | 615 | 336 | 279 |
| 04-15 | 160 | 45 | 115 |
| 04-16 | 121 | 35 | 86 |

### 推定原因

- **①**: 1〜2月の初期バックフィル時の Gemini Batch 失敗 or Phase 5 insert の部分失敗
- **③**: 新アーキ `tdnet-load-daily`（2026-04-17 本採用）移行過渡期の未ロード。ちょうど切替タイミングと一致

### 1月5日〜16日（営業日）について

GCS PDF にも存在しない（TDnet 公式の PDF 保持期間 30〜90日のため、取得開始前にサイトから消えていた）。復旧は irbank.net 等の別ソースが必要。

### 次アクション候補

1. **③ を先に**: `tdnet-load-daily` の実行ログで未ロード doc_id の原因特定 → 再投入
2. **①**: 原因特定後に `--from/--to` 指定でバックフィル再投入（`RUN_MODE=submit/resume`）

### 調査手順の再現コマンド

```bash
# GCS PDF カウント（全期間スキャン、fields='items(name),nextPageToken' でページング必須）
PYTHONUTF8=1 python -c "
from google.cloud import storage
from google.oauth2 import service_account
from collections import Counter
import os, re
creds = service_account.Credentials.from_service_account_file(os.environ['GOOGLE_APPLICATION_CREDENTIALS'])
client = storage.Client(credentials=creds, project='gmailpj-357912')
bkt = client.bucket('stock_data_1930932')
cnt = Counter()
pat = re.compile(r'^tdnet/[0-9A-Z]{4}/(20\d{6})_')
for blob in client.list_blobs(bkt, prefix='tdnet/', fields='items(name),nextPageToken'):
    m = pat.match(blob.name)
    if m and m.group(1).startswith('2026'): cnt[m.group(1)] += 1
for d in sorted(cnt): print(d, cnt[d])
"

# BQ カウント
# SELECT SUBMISSION_DATE, COUNT(DISTINCT DOC_ID)
# FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
# WHERE SUBMISSION_DATE >= '2026-01-01' GROUP BY SUBMISSION_DATE ORDER BY 1;
```

### 落とし穴

- `client.list_blobs(..., fields='items(name)')` 単独だと `nextPageToken` が返らずページング打ち切り。必ず `fields='items(name),nextPageToken'` を指定する
- index CSV との比較は不正確（index は `disclosure_time` マップ用で、scrape したが GCS アップロード失敗した PDF も含む）。真の「GCSにあるがBQにない」を見るには **GCS blob 名から日付集計** が正

### 原因特定（2026-04-17）

#### ③ 04-06〜04-16 漏れ（約1,100件）: Phase 5 バグ
- `phase5_bq_insert` (line 1191) の filter `[d for d in docs if d.text and d.chunks]` で **Embedding 対象外カテゴリ（決算短信/決算説明資料/月次開示 以外）が除外**される
- `phase4_chunk_only_with_embedding` は embed 対象にしか `doc.chunks` を設定しないため、meta doc は d.chunks=空 → line 1232-1248 の「メタデータ1行 insert」分岐に到達できない（デッドコード）
- BQ 04-06〜04-16 のデータを確認すると MAIN_CATEGORY が決算短信/決算説明資料/月次開示 の3つのみ → バグ確定
- **新アーキ `phase4_chunk_only` (line 1279-1290) は全 doc に chunks を付ける**ためこのバグなし。旧 full モード独自
- **修正**: line 1191 を `[d for d in docs if d.text]` に変更（commit `80889ef`, 2026-04-18 JST）
- **再投入**: `DATE_FROM=20260406,DATE_TO=20260416,RUN_MODE=full` で Cloud Run Job 実行
- **Dockerfile 副次バグ**: `docker/Dockerfile.tdnet-load-parallel` に `src/` の COPY が抜けていて `from src.llm.truncation import truncate_for_model` が ModuleNotFoundError → `COPY src/ src/` 追加（同 commit）
- **再投入結果（2026-04-17 23:47 JST 確認）**: GCS PDF数 = BQ DOC_ID数 **完全一致**。04-06〜04-16 全9日で差 0 件。③は **クローズ**

#### ① 01-19〜02-27 漏れ（約2,900件）: TICKER 絞り込み実行の途中切れ（仮説）
- BQ には非Embedカテゴリも insert 済み → ③と別原因
- 02-10 の漏れ177件を ticker 分布で見ると **全て4356〜8132 に集中**（1000〜3000番台の漏れゼロ）
- カテゴリ分布は広く薄く → 特定カテゴリ集中ではない
- 仮説: 初期バックフィル時に `TICKER_FROM=5000,TICKER_TO=9999` 等の絞り込みで実行 → タイムアウトまたは Gemini Batch 失敗で途中切れ
- **リカバリ方針（2026-04-18 決定）**: 新アーキ `ai_processing_flow` に委任。旧 `tdnet-load-parallel` は使わない。詳細な作業依頼は `docs/plans/20260417_091112_tdnet_load_ai_split.md` の「2026-01-19〜02-27 漏れ分リカバリ」セクション参照。前提として #19 Callback 403 修正の検証（2023-01-04〜06 再テスト）を完了させてから投入すること

---

## 終了済み作業アーカイブ

### ✅ 月次開示 MAIN_CATEGORY 分類漏れ対応（2026-04-19 完了）

**問題発見（他セッションから）**: 月次 BC 突合パイプライン (`extract_monthly_data.py`) で欠落銘柄調査したところ、月次KPI 開示のはずが `MAIN_CATEGORY='月次開示'` に入っていない文書が **41 銘柄 / 2,279 件**。誤分類先は主に `その他（未分類）`、`業績予想`（7545/9904/9327 前年比速報）、`業績の重要な先行指標`（8975 REIT）、`株主優待`。

**応急処置（他セッション）**: 2,279件を BQ 手動 UPDATE 済み（`SET MAIN_CATEGORY='月次開示'`）。

**恒久対応（本セッション、2026-04-19）**: `_MONTHLY_DOC_PATTERN` regex を拡充（phase1_extract_for_docs の `_correct_category_by_title` で効く）+ ticker 個別例外 `_TICKER_SPECIFIC_MONTHLY`。以下12パターン追加。最終 Docker image digest `259f655a...`。

| 追加 regex / 例外 | 代表銘柄 | 実データ FP 確認 |
|-------------------|----------|---------------|
| `主要ＫＰＩ\|主要KPI` | 2998/5580/9163 | ✅ |
| `速報数値` | 4666 | ✅ |
| `Monthly\s*Report\|マンスリーレポート` | 9616 | ✅ |
| `月末運用資産\|運用資産概況` | 165A/8739 REIT | ✅ |
| `前年比速報` | 7545/9904/9327 | ✅ |
| `ポートフォリオ運営実績\|ポートフォリオ稼働率` | 3287/8975 REIT | ✅ |
| `DATA\s*FILE` | 7455 | ✅ |
| `月度(?:IR\|ＩＲ)レポート` | 9223 | ✅ |
| `ホテル運営状況` | 3010/3463/3468/401A | ✅ |
| `売上報告` | 複数銘柄 | ✅ BQ 実測で FP 0件 |
| `(?:\d+月\|月度\|月次).{0,30}前年対比` | 3032/3221/6036/7678/7562/7502 等 | ✅ 月コンテキスト必須で「通期前年対比」等回避 |
| ticker 例外 `3086`: `連結売上収益報告` | 3086 J.フロント専用 | ✅ 他銘柄は 0 件、専用表記 |

**保留（追加せず）**: 「業績速報」単独 → BQ 実測で「2024年3月期 第3四半期連結業績速報値」19件が四半期決算系で FP 化する → 見送り。既存 `月次` `月度業績` regex で月次分は既に吸収。

**アプローチ採用理由**: Gemma プロンプト修正は他カテゴリ副作用リスクあり（Phase D 経験）。タイトル正規表現は前段で確実、かつ Gemma 依存ゼロ。

**削除済み成果物**（恒久対応完了に伴い不要化）:
- `data/logs/miscategorized_monthly_20260419_V4.csv`
- `scripts/find_miscategorized_monthly_bg.py`
- `scripts/apply_main_category_correction_bg.py`

#### 🔵 優先度低の宿題: Gemini/Gemma プロンプトで拾えるようにする（将来対応）

今回は正規表現ポストプロセス（`_correct_category_by_title`）で対応したが、**本来は Phase 3 の AI モデル（現行 Gemma 4 31B、または Gemini Flash Batch）がタイトルから月次と判定できるべき**。以下 10 パターンは regex で吸収しているが、プロンプト改修で AI 側が自力で月次と推論できれば regex は不要になる（regex は保険として残す想定）。

| # | パターン | 代表銘柄 |
|---|---------|---------|
| 1 | 主要KPI / KPI 単体タイトル | 2998/5580/9163 |
| 2 | 速報数値 | 4666 |
| 3 | Monthly Report | 9616 |
| 4 | 月末運用資産残高 / 運用資産概況 | 165A/8739 |
| 5 | 前年比速報（月度付き）← 現状 `業績予想` 扱い | 7545/9904/9327 |
| 6 | ポートフォリオ運営実績 | 3287 REIT |
| 7 | ポートフォリオ稼働率速報値 ← 現状 `業績の重要な先行指標` 扱い | 8975 REIT |
| 8 | DATA FILE YYYY年N月期速報 | 7455 |
| 9 | N月の業績速報 / N月売上報告 / マンスリーレポート / 月度IRレポート | 7157/8237/9223/3197 |
| 10 | ホテル運営状況のお知らせ（N月度） | 3010/3463/3468/401A |

**紐づく今日の作業**:
- `_MONTHLY_DOC_PATTERN` への 11 pattern 追加 + `_TICKER_SPECIFIC_MONTHLY` 新設（3086 J.フロント例外）
- 最終 Docker digest: `259f655a87284bdadb4d5a1e352f146284eca5dc11a1605ee621e45381b85175`

**着手条件**: プロンプト再チューニング機会が来た時（Gemma プロンプト v4 以降、または Gemini Flash プロンプト更新時）。**regex は保険として維持**、AI 側で吸収できるようになっても削除しない方針（FP 0 が実証されているため）。

#### 再発検知 SQL（必要時のみ使用）

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
| T-3 | `phase4_chunk_and_embed` | chunk → doc のマッピング key に `chunk_text` 文字列を使う | `(doc_id, chunk_index)` tuple を key に。同一テキスト chunk の衝突で embedding が紛失しない |
| T-4 | Phase 5 系（`phase5_bq_insert_finalize` / `phase5_bq_insert_load`）のエラー集計 | バッチ insert 失敗時に `errors += 1` を doc 単位で加算 | `errors += len(failed_rows)` で行数単位加算（004 A-6 の TDnet 実装）。サマリが実データ損失量を過小報告しない |
| T-5 | mode 選択（`--job-mode` / `RUN_MODE`） | 旧 `phase5_bq_insert`（streaming insert 版）を resume / full モードから呼ぶ | 新 `phase5_bq_insert_load` / `phase5_bq_insert_finalize` に寄せ、`insert_rows_json` 経路を段階的に削除（004 C-5）。暫定的に残す場合は用途をコードコメントで限定 |

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
- `api/002_bigquery.md` — Load Job vs streaming insert の選択指針

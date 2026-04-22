# AI コスト分析 & Gemma 4 PoC（統合アーカイブ）

**カテゴリ**: tools
**作成日**: 2026-04-12（074 / 074-1 を 2026-04-19 統合）
**ステータス**: 有効（本番採用確定、PoC完了）
**親**: `013_tdnet_load.md`（TDnet load 運用本体）
**関連**: `078_gemma4_operation.md`（TPU/vLLM セットアップ手順）

このファイルは旧 `074_vertex_ai_cost_analysis.md` と `074-1_gemma4_tpu_monthly_poc.md` を統合し、013 の枝番（013-1）として再編したもの。

---

## 📌 現況サマリ（2026-04-19）

**本番採用アーキテクチャ**:
- **Gemma 4 31B (TPU v6e-4 spot + vLLM)** — 全カテゴリ分類
  - プロンプト: baseline（案A: 配当/特損/特利 PL数値 vs 開示イベント区別）+ 中計ルール
- **Gemini 3 Flash Batch** — 決算短信のみ受注高/受注残高 判定で上書き

**実測コスト**:

| 対象 | コスト | 内訳 |
|------|-------:|------|
| Phase I（507 doc / 2023-01-04〜06） | **~$2.4** | TPU $2.3 + Cloud Run $0.1 |
| 1 バッチ ~3,000 doc | ~$14 | TPU起動O/H $1 + 推論 + Gemini/Embedding |
| 7年バックフィル想定 | **~$154** | 11バッチ集約時（`013` 分割方針参照） |
| 月次運用 | **~$11.5** | Gemma $6 + Gemini $5.5 |

**採用理由**:
- Gemini 3 Flash Batch 単独比 **41%** に削減
- 精度は Gemma 4 31B が Gemini を上回る（配当/特損/特利で Claude 自己判定 96.7% 勝率、Phase D 本番実測）
- 決算短信の受注高/受注残高だけ Gemini が優位（Gemma は過剰検知）→ ハイブリッド

**ベンチマーク基準**: Gemini 3 Flash Batch（1年 $180、10年 $1,800）を全コスト比較で先頭に置く。

---

## 本番採用までの PoC 実績

### 2024年1月 PoC（507 → 2,090 doc、2026-04-16 v1）

- TPU v6e-4 on-demand (us-central1-b)、vLLM OpenAI互換API
- モデル: `google/gemma-4-31B-it`
- 入力 2,090件 / OK 1,981件 (94.8%) / エラー 109件（うち 106件は max-model-len 16384 超過、truncate_for_model で解消見込み）
- 1件 4.08秒、スループット 1.95件/秒、稼働 ~48分、$2.49
- **SUB カテゴリ Jaccard 平均（vs Gemini 正解ラベル）**: Gemma 31B = 0.558（26B MaaS の 0.510 を +4.8pt 上回る）

### v2 改修（`truncate_for_model` 統合 + 案Aプロンプト）

- 入力 2,090件 / OK 1,953件
- Jaccard 0.461（v1 の 0.557 から -9.6pt 低下に見える）
- **しかし 120 PDF Claude 自己判定で v2 が 96.7%正解 vs Gemini 3.3%** → v2 が真に高精度、Jaccard 低下は Gemini 正解ラベル自体の誤りに由来

### Phase D 最終テスト（2,359 doc × 3プロンプト版、2026-04-17）

3 プロンプトの Jaccard（Gemini 基準）と 75 PDF サンプルチェック結果:

| version | Jaccard | サンプル正解率 | 採否 |
|---------|--------:|---------------:|------|
| baseline（案A: 配当/特損/特利） | 0.486 | 57.1% | ✅ 採用 |
| v2（+ 受注ルール） | 0.469 | 40.0% | ❌ 不採用（逆効果） |
| v3（+ 中計/業績予想/先行指標/業績修正） | 0.418 | カテゴリ混在 | 部分採用 |

**採用**: baseline + 中計ルールのみ（中計は v3 で 10/10 正解、他 v3 ルールは悪化）。
**受注判定**: Gemma は過剰検知傾向、Gemini に委譲（決算短信のみ）。

### プロンプトチューニングの教訓

1. 二値で明確なルールは効く（中計「新規策定・改定のみ、進捗は False」→ 10/10）
2. 条件が曖昧なルールは Gemma を保守的にし誤判定（受注「定量的数値があれば True」→ 数値があっても False に）
3. プロンプト肥大化は HTTP 400 を増やし全体精度を下げる（16K context 圧迫）
4. ルール追加ではなく別アプローチ（fine-tuning / few-shot / post-processing）を今後検討

---

## Phase I 本番実装完遂（2026-04-18）

TDnet load 改修 Phase I として **Cloud Workflows + Cloud Run Job + TPU v6e-4 spot** の本番パイプライン構築、**2023-01-04〜06 の 507 doc で統合テスト SUCCEEDED**（全自動完遂、エラー0、preemption 0）。

**実装詳細**: `013_tdnet_load.md` 「新アーキ」セクション、`078_gemma4_operation.md` セクション5「Phase I 本番実装ノウハウ」。

---

## 落とし穴（恒久ルール）

- **text-embedding-004 は文字課金 $0.025/1M chars**（トークン課金誤認に注意）
- **Batch Prediction Job は投入後キャンセル困難** → 投入前に件数確認を習慣化
- **Vertex AI Batch Prediction API は partner models のうち Llama/gpt-oss/Qwen/DeepSeek のみ対応**。Mistral/Claude/Grok は online のみ（50%割引なし、sync loop 必須）
- **Llama 4 MaaS は DSQ 429 頻発**で実運用不可
- **Gemma 4 31B Dense は Model Garden UI 未出現**（2026-04-14時点）、MaaS 未提供、自前デプロイ経路のみ（GKE+TPU / GCE+GPU / Cloud Run GPU）
- **「27,000円」= 2024年1年分の実績**。7年・10年の見積に誤用しない
- **TPU/GCE spot preemption で走行中ジョブ全ロスト** → 推論結果を1件ごとに GCS append + 起動時 `done_doc_ids` で残件処理する resume 設計が必須
- **`vllm/vllm-tpu:latest` は Google が push 更新でタグドリフト**（2026-04-17 発覚）→ 固定 digest で pin（成功した digest は `078` セクション4 参照）
- **`fullモード` バックフィルは禁止ガード実装済**（31日超 + RUN_MODE=full で拒否、`ALLOW_FULL_BACKFILL=1` で override）

---

## 🗄️ 履歴アーカイブ（検討経緯、参考）

### 発端: 2026-04-10 Vertex AI $27,000 課金事故

TDnet 2024年バックフィル（#4〜#23）で Phase 3 Gemini 分析バッチが ~26,500円（97%）。直接原因は #6/#9〜#17 の 10 バッチを fullモード（全件再処理）でリカバリ投入、21時台に 8 バッチが同時 Phase 3 到達。

**実施済み対策**: `tdnet_load_parallel.py` に fullモード禁止ガード実装（日付範囲 > 31日 かつ `RUN_MODE=full` → 実行ブロック、`ALLOW_FULL_BACKFILL=1` で強制実行可）。

### Gemma 4 MaaS 移行検討（2026-04-12〜14）

方針: Phase 3 を MaaS API (`gemma-4-26b-a4b-it-maas`) に移行（Cloud Run GPU は代替案）。

PoC結果（2024年1月 2,090件）:
- is_monthly 精度 99.8%（プロンプト改善で 100%）
- sub_categories Jaccard 平均 67.1%
- 処理時間 ~30分、API失敗 0件
- 4件の is_monthly 不一致は全て Gemini 誤認（四半期KPI開示を月次と誤判定）→ プロンプト改修で 100%

**判断結果**: Mistral Medium 3 MaaS は Batch 非対応で現行より高額（$2,880/10年）→ 不採用。Llama 4 Maverick は DSQ 429 で実運用不可 → 不採用。

### 4案/5案コスト比較（1年 ~18,700件 想定）

| 案 | コスト | 処理時間 | 構築工数 | 備考 |
|----|-------:|---------|---------|------|
| Gemini 3 Flash Batch | $180 | — | 済み | 現行基準 |
| MaaS Gemma 26B | $10 | 4h | ゼロ | Flash Batch比 1/18 |
| Cloud Run 26B 4bit L4 | $7 | 5.7h | 数日 | Scale-to-Zero |
| Cloud Run 31B 4bit L4 | $12 | 8.9h | 数日 | Scale-to-Zero |
| Model Garden 31B L4×2 | $24 | 8.9h | 小 | クォータ要 |
| OpenAI o3s Batch | $72 | 2.5h | 小 | Batch 50%割引 |
| **TPU v5e-4 batch=8** | $72 | — | 1日 | **最安、本番採用** |

### GPU クォータ申請履歴（2026-04-13〜14）

| クォータ | 申請結果 |
|---------|---------|
| `GPUS_ALL_REGIONS` | 即却下（請求履歴不足） |
| `CustomModelServingL4GPUsPerProjectPerRegion` (us-west1) | 承認（4台） |
| `CustomModelServingH100GPUsPerProjectPerRegion` | 即却下 |

Model Garden gemma4 コンテナは NCCL 設定が H100 (a3) 専用で L4 (g2) マルチGPU非対応 → Model Garden 経由での 31B デプロイは不可、TPU 経路に切替。

### v5e-4 → v6e-4 採用変更（2026-04-15）

Gemma 4 31B BF16 (~62GB) は v5e-4 (64GB HBM) で OOM → v6e-4 (128GB HBM) に変更。spot $4/hr（v5e の 2.5倍）だが 2.5秒/件想定で 10年分 $520 → 最安を維持。

### A100 vs L4 運用コスト比較（2026-04-14）

| 構成 | 10年バックフィル spot | 年間運用 spot |
|------|---------------------:|-------------:|
| Gemini 3 Flash Batch（基準） | $1,800 | $180 |
| L4 自前 | $1,425 | $150 |
| **TPU v5e-4 batch=8** | **$666** | **$72** |

→ TPU 経路が最安、本番採用決定（Phase I で実証）。

---

## 落とし穴（2024年1月 PoC）

- `vllm-tpu:latest` タグドリフト（2026-04-17 発覚、Google が 0.12→0.13 に更新、Gemma 4 非互換）
- `transformers` バージョン不整合（4.57.6 は `model_type=gemma4` 未対応、5.x 必須）
- `--max-num-batched-tokens` 衝突（既定 2048 は Gemma 4 multimodal encoder 2496 と衝突、4096 明示必須）
- `--disable-log-requests` フラグ削除済（指定すると起動失敗）
- vLLM 二重起動で HBM 競合 RESOURCE_EXHAUSTED
- `--tensor-parallel-size 4` 必須（tp=1 は HBM 不足、v6e-4 は 4チップで分散）
- HTTP 400 max-model-len 超過（J-REIT 帳票型は数字密度高く token 爆発、`truncate_for_model` で対処）
- spot TPU preemption で推論結果全ロスト（GCS 都度 append 必須）

**詳細と再発防止チェックリスト**: `078_gemma4_operation.md` セクション 2・3 参照。

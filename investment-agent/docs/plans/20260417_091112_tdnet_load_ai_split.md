# tdnet_load 改修プラン v2（BQロード/AI判定分離 + Cloud Workflows 主導）

**作成日**: 2026-04-17 JST
**ステータス**: ✅ **Phase I 完遂（2026-04-18 JST）**。統合テスト 507 doc で全自動 SUCCEEDED、本番バックフィル突入可能
**関連知見**: `docs/knowledges/tools/013_tdnet_load.md`（Phase I 完遂記録）, `013-1_ai_cost_and_gemma_poc.md`（実測 ~$2.4/507doc）, `013-1_ai_cost_and_gemma_poc.md`, `078_gemma4_operation.md`（セクション5 本番実装ノウハウ）, `080_workflows_runbook.md`

## 目的

- BQロード（低レイテンシ）と AI判定（重処理）を完全分離
- Phase 3 を Gemma 4 31B TPU v6e-4 + vLLM に移行（074採用済み）
- 受注判定は決算短信のみ Gemini Flash Batch で上書き
- Cloud Run を長時間待機させない（Workflows 主導）
- コスト: 7年バックフィル $518 / 月次運用 $11.5（現行 Gemini Flash のみ比 41%）

## アーキテクチャ

```
[tdnet-load-daily]  Cloud Run Job CPU、02:00 JST 毎日
  --mode=load
  Phase 0: 開示時刻マッピング（GCS index CSV走査）
  Phase 1: テキスト抽出（PyPDF2 → pdfminer）
  Phase 5: BQ Insert
    - TEXT/CHUNK_TEXT = Phase 1 抽出結果（失敗分は空）
    - MAIN/SUB/EMBEDDING = NULL
    - AI_STATUS = 'pending'

              ↓（独立して後刻）

[ai_processing_flow]  Cloud Workflows（Cloud Scheduler 自動）
  起動時: AI_STATUS='pending' の最古 SUBMISSION_DATE から1ヶ月分を対象化
  pending ゼロなら即 exit

  Step 1: tdnet-ai-prepare (Cloud Run Job CPU)
    - BQ Select: AI_STATUS='pending' AND date range
    - Vision OCR (Vertex Batch, 抽出失敗分のみ)
    - 正規表現月次補正
    - GCS state.json 保存
    - AI_STATUS → 'pending_gemma'

  Step 2: TPU VM 起動 + Gemma 推論キック
    - startup-script で vllm-tpu:<固定digest> 起動
    - supervisor が state.json 読込 → gemma_CURRENT.jsonl append

  Step 3: Gemma 完了待ち（Callback パターン）
    - TPU supervisor が Workflows Callback URL に POST で完了通知
    - preemption 時: Workflows retry で TPU 再起動ステップ
      supervisor が gemma_CURRENT.jsonl から resume

  Step 4: tdnet-ai-finalize (Cloud Run Job CPU)
    - Gemini Flash Batch（MAIN='決算短信' のみ、現行プロンプト流用）
    - 受注判定を Gemma SUB に差分マージ（他は温存）
    - Embedding Batch（3カテゴリ限定: 決算短信/決算説明資料/月次開示）
    - BQ UPDATE: TEXT/CHUNK_TEXT/EMBEDDING/MAIN/SUB/
                 AI_STATUS='completed'/AI_PROCESSED_AT
    - GCS state/CURRENT クリーンアップ
```

## Job / サービス一覧

| コンポーネント | 実体 | メモリ | タイムアウト | トリガー |
|-------------|------|--------|-------------|---------|
| `tdnet-load-daily` | Cloud Run Job CPU | 2Gi | 3600s | Scheduler 火〜土 02:00 JST |
| `ai_processing_flow` | Cloud Workflows | - | 1年 | Scheduler 自動 |
| `tdnet-ai-prepare` | Cloud Run Job CPU | 2Gi | 3600s | Workflows Step 1 |
| TPU v6e-4 spot | Compute Engine + vLLM | - | spot | Workflows Step 2 |
| `tdnet-ai-finalize` | Cloud Run Job CPU | 3Gi | 21600s | Workflows Step 4 |

## BQ スキーマ拡張

| カラム | 型 | 値 |
|-------|-----|------|
| `AI_STATUS` | STRING | `pending` / `pending_gemma` / `pending_finalize` / `completed` |
| `AI_PROCESSED_AT` | TIMESTAMP | AI判定完了時刻 |

既存 4,166,812 行は一括 `AI_STATUS='completed', AI_PROCESSED_AT=現在時刻` で UPDATE。

## GCS 状態ファイル

```
gs://stock_data_1930932/ai_job/{run_id}/
  ├─ state.json           # 対象doc_id, OCR結果, 正規表現結果
  └─ gemma_CURRENT.jsonl  # Gemma推論結果（1件ずつappend、resume用）
```

run_id = Workflows execution ID を使う。

## プロンプト

| モデル | プロンプト |
|-------|----------|
| Gemma 4 31B (TPU v6e-4) | baseline（案A: 配当/特損/特利 の PL数値 vs 開示イベント区別）+ 中計ルール（074採用済み）、**受注プロンプトも残す** |
| Gemini Flash Batch | 現行の統合プロンプト流用（is_monthly + sub_categories）、決算短信のみ対象、**受注高/受注残高** だけマージ対象 |

## 結果マージロジック

```python
# 決算短信以外: Gemma の MAIN/SUB をそのまま採用
# 決算短信のみ: Gemini の「受注高/受注残高」判定を差分適用
final_main = gemma_main
final_sub = set(gemma_sub)
if gemma_main == "決算短信":
    if "受注高/受注残高" in gemini_sub:
        final_sub.add("受注高/受注残高")
    else:
        final_sub.discard("受注高/受注残高")
    # 他のSUBは Gemma のまま温存、MAIN も Gemma のまま
```

## Workflows（Scheduler自動起動）

- Cloud Scheduler 1本で毎日起動（時刻は後で決定）
- Workflows 起動時に `SELECT MIN(SUBMISSION_DATE) FROM ... WHERE AI_STATUS='pending'` で最古月取得
- その月の pending を1ヶ月分処理
- pending ゼロなら即 exit

手動起動はデバッグ/例外対応のみ。

## 実装タスク

| # | タスク | 粒度 | 依存 |
|---|--------|------|------|
| A | BQスキーマ拡張（`AI_STATUS`, `AI_PROCESSED_AT`）+ 既存416万行一括UPDATE | 小 | なし |
| B | `tdnet_load_parallel.py` に `--mode=load/ai-prepare/ai-finalize` 実装 | 大 | A |
| C | load モード: Phase 0/1/5 のみ、AI_STATUS='pending' で insert | 中 | B |
| D | ai-prepare モード: OCR/正規表現/state.json保存/AI_STATUS='pending_gemma' | 中 | B |
| E | ai-finalize モード: Gemini Batch + 受注マージ + Embedding Batch + BQ UPDATE | 中 | B |
| F | Gemma TPU supervisor（`gemma_tpu_worker.py` 新規）: state 読込 + vLLM 推論 + Callback POST | 大 | A, D |
| G | TPU VM startup-script + vLLM Docker digest pin（074-1 教訓） | 中 | F |
| H | Cloud Workflows YAML（4 step + TPU起動/停止 + retry/callback + pending 月算出） | 大 | D, F, E |
| I | Cloud Scheduler: `tdnet-load-daily` 火〜土 02:00 JST | 小 | C |
| J | Cloud Scheduler: `ai_processing_flow`（時刻後決定） | 小 | H |
| K | data_catalog.md 更新（スキーマ、パイプライン図、ETL実装済みに修正） | 小 | A |
| L | 013_tdnet_load.md 更新（新アーキ反映） | 中 | H |
| M | 旧 `tdnet-load-parallel` / `tdnet-load-recovery` は新アーキ稼働3ヶ月後に廃止 | 小 | 全て |

## コスト見通し（074 踏襲）

| 項目 | 7年バックフィル | 月次運用 |
|------|---------------|---------|
| Gemma TPU v6e-4 spot | $159 | ~$6 |
| Gemini Flash Batch（決算短信のみ） | $358 | ~$5.5 |
| Cloud Workflows | ~$0 | ~$0 |
| Embedding（text-embedding-004） | ~$1 | ~$0 |
| **合計** | **~$518** | **~$11.5** |

## バックフィル計画

### 対象と処理方法

| 対象 | 実行方法 |
|------|---------|
| 2017-2023（7年分、未投入 = BQに行無し） | まず `tdnet-load-daily` で行を入れる（BQロード） → 次に `ai_processing_flow` が自動で最古月から消化 |
| 2024 残り（#4〜#23、BQ投入済み・AI未処理想定） | 現行ステータスを確認の上、新アーキで AI 処理 |
| **2026-01-19〜02-27 漏れ分（約2,900件）** | **新アーキ `ai_processing_flow` で date_from=20260119, date_to=20260227 を実行。`_load_processed_file_names` dedup で既存行は自動skip、漏れ分のみ処理** |
| 既存 2016 / 2024-2026（BQに入ってる行） | `AI_STATUS='completed'` 一括UPDATE（再処理なし） |

※ 2017-2023 は BQ に1行も無い。まず load Job でPDFをBQに入れる必要あり。ただし TDnet PDF は30-90日で消えるため、実PDFの取得可否を別途確認（`003_tdnet_download.md` 参照）。

### 2026-01-19〜02-27 漏れ分リカバリ（作業依頼 2026-04-18 JST 起票）

**背景:**
- 初期バックフィル時に ticker 絞り込み実行（`TICKER_FROM=XXXX,TICKER_TO=YYYY` 等）で途中切れ発生と推定
- 02-10 の漏れ177件は ticker **4356〜8132 に集中**（1000〜3000番台は漏れゼロ）、カテゴリ分布は広く薄く → ticker範囲仮説が有力
- 詳細: `docs/knowledges/tools/013_tdnet_load.md:687-703`（取り込み漏れ調査 / 原因特定セクション）

**対象期間・件数:**
- 2026-01-19〜02-27、累計約 2,900件
- ワースト日: 02-13 (-643), 02-12 (-298), 02-10 (-218), 02-06 (-169)
- 区間別漏れ: 01-19〜01-26=34, 01-27〜01-30=185, 02-02〜02-06=525, 02-09〜02-13=1,295, 02-16〜02-20=198, 02-24〜02-27=187

**実行前提（新アーキ側の改修完遂後に投入）:**
- #16 Workflows gemma_runner_branch retry（max 3, exponential backoff）✅ 済
- #17 task-timeout 拡張（load-daily / ai-prepare → 6h）✅ 済
- #19 Callback 403 修正（OIDC ID token フォールバック）→ Cloud Build 完了、**要検証**（2023-01-04〜06 再テストで callback 疎通確認）
- ai-prepare SELECT 範囲拡張（`pending` OR `pending_gemma`）✅ 済

**分割方法は実行担当（次セッション）に委任:**
- 一発投入 / 2分割 / 月単位 / 新アーキの既定 3ヶ月単位 いずれも可
- ピーク日 2026-02-13（単日2,043 doc）のメモリ負荷を考慮すること
- preemption 最小化のため **平日 15:00〜24:00 JST or 週末** に投入（本プラン「投入タイミング」セクション参照）

**完了条件:**
- `SUBMISSION_DATE BETWEEN '2026-01-19' AND '2026-02-27'` の GCS PDF数 と BQ DOC_ID数 が一致（差 0〜1件）
- AI_STATUS='completed' で全件 finalize 済み
- `013_tdnet_load.md:687-703` セクションを「完了」に更新、本セクションも「done」化

### 投入単位（3ヶ月単位、2026-04-17 確定）

**1回の Workflows execution で 3ヶ月分** を処理する。

**根拠（時間無制約前提のコスト最適化）:**

| 戦略 | 投入回数（7年） | TPU起動O/H | 合計 TPU コスト |
|------|-------------|-----------|---------------|
| 1日単位 | 2,520 | $2,520 | $2,679 |
| 1週間単位 | 336 | $336 | $495 |
| 1ヶ月単位 | 84 | $84 | $243 |
| **3ヶ月単位（採用）** | **28** | **$28** | **$187** |
| 1年単位 | 7 | $7 | $166 |
| 7年一括 | 1 | $1 | $160 |

- TPU 起動オーバーヘッド（Docker pull + vLLM + モデルロード = ~15分/起動 × $4/hr = $1/起動）を最小化
- preemption 長距離走行リスクを抑制するため 1年/7年一括より 3ヶ月に抑える
- 中間結果を段階的に確認できる粒度（精度・エラー傾向の早期検出）

### task-timeout 要件（3ヶ月単位に合わせる）

| Job | 現状 | 必要 | 備考 |
|-----|------|-----|------|
| `tdnet-load-daily` | 3600s (1h) | **21600s (6h)** | 3ヶ月 ~6,000 docs × 2秒/件 = ~4h |
| `tdnet-ai-prepare` | 3600s (1h) | **21600s (6h)** | 再抽出 + Vision OCR Batch 待機 = ~5h |
| `tdnet-ai-finalize` | 21600s (6h) | 21600s のまま | Gemini Batch + Embedding Batch = ~3h |

### 2023 バックフィル ticker 分割（2026-04-18 算出、最新方針 = 3-5 バッチ）

> **2026-04-18 更新**: ai-finalize の numpy化 + stream write 実装により peak メモリ ~8倍圧縮。
> 2 CPU / 8Gi で 1バッチ ~11,000 doc まで安全処理可能となったため、**11分割 → 3-5 分割に集約**。
> 以下の 11分割表は初期算出値として保持（参考用）。実運用では 3-5 に統合すること。

**前提:** 2023-01-04〜06 の 507 doc は Phase I 完遂で既ロード（`AI_STATUS='completed'`）。残り 2023 全期間を **ticker 範囲で 11 バッチ均等分割**。

**算出方法:** `STOCK.TDNET_DOCUMENTS_ENHANCED` の 2024年データ（~34,000 doc）を近似分布として使用。ticker昇順で累積件数をとり 11等分。2023年の実分布は BQ に未投入のため直接算出不可だが、年次でticker別開示頻度は大きく変わらない前提。

| # | ticker_from | ticker_to | docs(2024近似) |
|---|------------|-----------|---------------|
| 1 | 1301 | 1909 | 3,091 |
| 2 | 190A | 232A | 3,079 |
| 3 | 2330 | 3023 | 3,104 |
| 4 | 3024 | 3690 | 3,090 |
| 5 | 3691 | 4410 | 3,091 |
| 6 | 4412 | 5288 | 3,094 |
| 7 | 5290 | 6366 | 3,091 |
| 8 | 6367 | 7130 | 3,086 |
| 9 | 7131 | 7902 | 3,094 |
| 10 | 7906 | 9063 | 3,089 |
| 11 | 9064 | 9997 | 3,098 |

**注意:**
- Phase I 既ロード分（`TICKER BETWEEN '1301' AND '9997' AND SUBMISSION_DATE BETWEEN '2023-01-04' AND '2023-01-06'`）は BQ dedup（`_load_processed_file_names`）で自動 skip
- 2024近似のため 2023 実件数は ±10% 程度のブレ想定。件数ベースで均等になるよう再計算したい場合、**2023 の load 完了後** に 2023 実データで rebalance 可能
- 2024 で ticker 範囲別 ~3,000 doc / 3ヶ月（旧アーキ #4〜#23 実績）と同等粒度 → **task-timeout 21600s (6h) で1バッチ安全完遂**

**12分割にする場合:** 上記SQL（`docs/plans` 内）の `11.0` を `12.0` に変えて再計算、各 ~2,830 doc。preemption リスクは多少軽減。

### 2017-2022 バックフィル計画（2026-04-20 確定）

2023 で確立した **11 ticker 範囲 × 6年分を1 batch で一括** 処理する方式:

| # | ticker range | 2024近似 doc | ×6 (2017-2022 予想) | 構成 |
|--:|--------------|------------:|-------------------:|:----:|
| 1 | 1301-1909 | 3,091 | ~18,500 | 2CPU/8Gi |
| 2 | 190A-232A | 3,079 | ~18,500 | 2CPU/8Gi |
| 3 | 2330-3023 | 3,104 | ~18,600 | 2CPU/8Gi |
| 4 | 3024-3690 | 3,090 | ~18,500 | 2CPU/8Gi |
| 5 | 3691-4410 | 3,091 | ~18,500 | 2CPU/8Gi |
| 6 | 4412-5288 | 3,094 | ~18,600 | 2CPU/8Gi |
| 7 | 5290-6366 | 3,091 | ~18,500 | 2CPU/8Gi |
| 8 | 6367-7130 | 3,086 | ~18,500 | 2CPU/8Gi |
| 9 | 7131-7902 | 3,094 | ~18,600 | 2CPU/8Gi |
| 10 | 7906-9063 | 3,089 | ~18,500 | 2CPU/8Gi |
| 11 | 9064-9997 | 3,098 | ~18,600 | 2CPU/8Gi |

**推定 doc 総数**: ~204,000（年 ~34,000 × 6年）
**batch 数**: 11
**TPU 起動 O/H**: ~$11
**所要（1 batch ~4-5h × 11 batch 直列）**: ~50h / 週末集中で 5-7 日
**総コスト概算**: ~$150（2023 同等）

**理由**:
- 30K/batch 基準を常に満たす（±50% ブレでも ~28K で上限内）
- 2023 ticker 分割ロジック・config を流用可能（`date_from`/`date_to` だけ変更）
- 失敗時の再実行粒度も同じ
- 年単位分割は 34-46K/batch で 30K 超え、OOM リスク（2026-04-20 事故）再燃のため却下

**config 命名規則**: `config/backfill/2017_2022_batch{01..11}_{TICKER_FROM}_{TICKER_TO}.yaml`

**実行順序**: 01 → 02 → ... → 11（TPU quota=1 で実質直列）

### 投入タイミング（preemption 最小化）

us-central1-b（アイオワ、米中西部 CT）のスポット需要を避ける時間帯に投入。

**最安全（preemption 最小）:**
- **平日 15:00〜24:00 JST**（米中西部 00:00-09:00 CT = 深夜〜早朝）
- **週末（土日 全日 JST）**

**避けるべき:**
- **平日 00:00〜09:00 JST**（米中西部 09:00-18:00 CT = 業務時間帯、最大混雑）

実運用では Cloud Scheduler の起動時刻を **平日 15:00 JST** or **土曜 08:00 JST** に設定。手動投入の際も同様の時間帯を使う。

（根拠: Google 公式「夜・週末は負荷低」、US AI 企業の ML 訓練ピーク時間帯回避。具体的 zone 別 preemption 率は非公開のため実測ログで継続補正）

## 実装順序

1. **A**（BQスキーマ拡張）→ 既存行一括 completed UPDATE
2. **B+C**（load モード実装 + デプロイ）→ 日次ロード先行稼働（AI未投入状態）
3. **F+G**（TPU supervisor + VM startup）→ 独立PoC から発展
4. **D**（ai-prepare モード実装）
5. **E**（ai-finalize モード実装）
6. **H**（Workflows YAML） → エンドツーエンドテスト
7. **J**（Scheduler 本番化、バックフィル開始）
8. **K+L**（MD更新）

## 注意事項・落とし穴

- Embedding 単価は文字課金 $0.025/1M chars（旧メモリのトークン課金誤認を訂正、074 参照）
- TPU `vllm-tpu:latest` は壊れるので **固定 digest で pin**（074-1 line 182 教訓）
- TPU spot preemption は発生する。`gemma_CURRENT.jsonl` 継続 append + resume 必須（074 line 420-428）
- Eventarc/Pub/Sub/Workflows すべて at-least-once 配信 → 冪等性は AI_STATUS で担保
- Cloud Run Job の task-timeout 最大 12時間（43200s）。finalize は 6h で設計、超える場合は分割
- 現行の `_correct_category_by_title`、`_EMBED_CATEGORIES`、統合プロンプト等は ai-prepare/finalize 側に移植
- 現行 `tdnet-load-parallel` は新アーキ稼働3ヶ月後に廃止（段階的移行）

---

## Phase II: Gemma専用パイプライン化（2026-05-21 実装完了）

**プラン**: `docs/plans/tools-013_gemma_only_pipeline_20260520_220510.md`
**コミット**: `92d5113d`（A〜E 全実装完了）

### 主要変更（Phase I からの差分）

| 変更点 | Phase I | Phase II |
|-------|---------|---------|
| PDF テキスト抽出 | PyPDF2 → pdfminer フォールバック | **PyMuPDF (fitz)** → pdfminer フォールバック |
| 画像PDF | Vision OCR Batch（Gemini） | **廃止** → `AI_STATUS='skipped_image_pdf'`（pending 滞留防止） |
| 受注高/受注残高判定 | Gemini Flash Batch（決算短信のみ） | **Gemma Pass 2**（決算短信 + 決算説明資料） |
| Gemma 処理 | Pass 1 のみ | Pass 1（全件）+ **Pass 2**（`_PASS2_CATEGORIES` 対象） |
| Pass 2 GCS 出力 | なし | `ai_job/{run_id}/gemma_pass2_CURRENT.jsonl` |
| コスト | ~$11.5/週（Gemini $5.5 含む） | **~$6/週**（Gemini 廃止） |

### 完了（E-2〜E-5 全完了 2026-05-21）

- **E-2** ✅: load モード smoke test — 既存247件スキップ確認、exit(0)
- **E-3** ✅: ai-prepare smoke test — PyMuPDF抽出確認、skipped_image_pdf遷移確認
- **E-4** ✅: ai-finalize smoke test（mock JSONL）— Pass 2 マージ確認、BQ Insert成功
- **E-5** ✅: Cloud Build + 3 Job デプロイ（Build: `c3152a7b`）

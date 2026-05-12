# TDnet バックフィル実績・再利用ナレッジ

**カテゴリ**: tools
**作成日**: 2026-05-10
**ステータス**: アーカイブ（全年バックフィル完了）
**親ドキュメント**: `013_tdnet_load.md`

---

## 全年バックフィル完了サマリ

| 年 | docs | BQ rows | 状態 | プラン |
|----|------|---------|------|--------|
| 2016 | 47,591 | - | 既存（irbank経由） | - |
| 2017 | 51,147 | 1,149,126 | ✅ 完了 | `20260427_140000_tdnet_2017_2022_backfill.md` |
| 2018 | 51,668 | 1,146,682 | ✅ 完了 | 同上 |
| 2019 | 52,712 | 1,166,745 | ✅ 完了 | 同上 |
| 2020 | 56,720 | 1,202,286 | ✅ 完了 | 同上 |
| 2021 | 62,935 | 1,236,526 | ✅ 完了 | 同上 |
| 2022 | 60,415 | 1,259,912 | ✅ 完了 | 同上 |
| 2023 | 62,612 | 1,230,333 | ✅ 既存 | - |
| 2024 | 66,326 | 1,359,989 | ✅ 完了 | `20260425_001000_tdnet_2024_gap_backfill.md` |
| 2025 | 69,400 | 1,727,800 | ✅ 完了 | `20260425_091000_tdnet_2025_gap_backfill.md` |
| 2026 | 18,863+ | 509,306+ | 日次運用 | - |

---

## 再利用ナレッジ

### 整合性チェック SQL（年・バッチ別）

任意の年・期間で pending 残存を検査する汎用テンプレート。

```sql
SELECT
  EXTRACT(YEAR FROM SUBMISSION_DATE) AS yr,
  COUNT(DISTINCT DOC_ID) AS total_docs,
  COUNTIF(AI_STATUS = 'completed') AS completed_rows,
  COUNTIF(AI_STATUS != 'completed' OR AI_STATUS IS NULL) AS pending_rows
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE SUBMISSION_DATE BETWEEN @start AND @end
GROUP BY yr
ORDER BY yr
```

非ゼロ時の対処: ai-prepare ログで「抽出失敗」「blob not found」を grep → 該当 ticker+日付を `tdnet-load-daily` で再 load（dedup で既存はスキップ）→ `ai_processing_flow` 再起動。

### OOM 分割基準

ai-finalize は 2CPU/8Gi で **15K docs/batch が上限**。超過する四半期は GCS index CSV の日別 docs 数から累積 50% 地点で 2ヶ月+1ヶ月に分割。リソース増は行わない。

### TPU preemption 回避タイミング

| 時間帯（JST） | 推奨 |
|-------------|------|
| 平日 15:00〜24:00 | ✅ 最安全（米中西部深夜） |
| 週末 全日 | ✅ 最安全 |
| 平日 00:00〜09:00 | ❌ 避ける（米中西部業務時間） |

### task-timeout バックフィル時設定値

```bash
gcloud run jobs update tdnet-load-daily --region us-west1 --task-timeout 21600s
gcloud run jobs update tdnet-ai-prepare --region us-west1 --task-timeout 21600s
# 終了後に日次値に戻す
gcloud run jobs update tdnet-load-daily --region us-west1 --task-timeout 3600s
gcloud run jobs update tdnet-ai-prepare --region us-west1 --task-timeout 3600s
```

---

## Phase I 確定設計ポイント（2026-04-18）

新アーキ初期実装で確定した設計判断。013本体の仕様記述の背景として参照。

| 観点 | 決定内容 |
|------|---------|
| チャンク化タイミング | load はチャンク化しない（メタ1行）。ai-finalize で3カテゴリのみチャンク分割+Embedding |
| MAIN_CATEGORY 決定 | ファイル名由来 `pre_main_category` + `_AMBIGUOUS_OVERWRITE` ルール |
| SUB_CATEGORIES 決定 | Gemma 出力ベース + 決算短信のみ Gemini 受注判定を差分マージ |
| BQ 書き込み方式 | streaming API 廃止 → `load_table_from_uri`（GCS 経由）に統一 |
| state.json 衝突回避 | ai-prepare 冒頭で同 run_id の既存ファイルを削除 |
| GCS Lifecycle | `ai_job/*` を 14日で自動削除 |
| Workflows parallel shared | branch 内 local → shared に assign（直接書き込み不可） |
| Workflows LRO timeout | `connector_params.timeout` で明示（本番 21600s） |
| Callback 認証 | OAuth2 Bearer + OIDC フォールバック |
| TPU preempt retry | max_retries=3, exponential backoff 60→600s + GCS checkpoint resume |
| 共有イメージ | 3 Job 同一イメージ、`--job-mode` で切替 |

### Phase I で解消した事故一覧

| # | 事故 | 解消方法 |
|---|------|---------|
| 1 | startup-script 未実行 | Cloud Run Job + SSH IAP |
| 2 | IAP tunnel 不動 | `gcloud alpha compute tpus tpu-vm ssh` |
| 3 | Callback 401/403 | access_token + workflows.invoker + OIDC |
| 4 | BQ DELETE streaming buffer 失敗 | Load Job 切替 |
| 5 | parallel shared 変数 null | local → assign パターン |
| 6 | finalize 失敗が握り潰し | try/except 根絶 |
| 7 | 3カテゴリ外 doc の不正チャンク行 | load 時チャンク化廃止 |
| 8 | MAIN 不安定 | ファイル名由来 + AMBIGUOUS_OVERWRITE |

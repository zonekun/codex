# 016 VCP インサイダー日次スクリーナー — Cloud Run Job

**カテゴリ**: analysis / tools
**作成日**: 2026-05-22
**ステータス**: 稼働中
**関連知見**: [`015_tob_insider_screener.md`](015_tob_insider_screener.md) — TOBインサイダー疑い検出スクリーナー（静止スコア×発火スコア）

---

## 概要

TSE 全銘柄（約4,500銘柄）を毎営業日 VCP スクリーニングし、
アクショナブルな銘柄（Pre-breakout / Breakout / Early-post-breakout）を
Dropbox Excel に追記する日次 Cloud Run Job。

**目的**: Stage 2 上昇トレンド中のヨコヨコ収縮→ブレイク候補を毎日捕捉し、
TOB公表前インサイダー的動きや注目銘柄の継続シグナルを蓄積する。

**VCP バックテスト結果**（2026-05-22 実施、289 TOBケース）:

| 窓 | TPR | Lift |
|----|----:|-----:|
| 30営業日前 | 11.07% | **72.7x** |
| 60営業日前 | 9.34% | 61.4x |
| 90営業日前 | 9.69% | 63.6x |

→ TOB前の株は「ランダムな株・ランダムな日」と比べ約73倍 VCP シグナルに現れやすい。

---

## Cloud Run Job 仕様

| 項目 | 値 |
|------|-----|
| Job名 | `vcp-insider-daily` |
| Artifact Registry | `tob/vcp-insider-daily` |
| リージョン | `us-west1` |
| スケジュール | **毎営業日 19:00 JST**（`vcp-insider-daily-scheduler`） |
| タイムアウト | 3600s（全銘柄スキャン 約35分） |
| メモリ/CPU | 2Gi / 2 CPU |
| max-retries | 1 |
| サービスアカウント | `bq-loader@gmailpj-357912.iam.gserviceaccount.com` |

**スケジュール根拠**: `stock-price-jquants-load`（18:00 JST 起動）の完了後。

---

## 出力

| 項目 | 値 |
|------|-----|
| Dropbox パス | `/stock/AI分析優待/インサイダーVCP.xlsx` |
| ローカルパス | `C:\Users\zonekun\Dropbox\stock\AI分析優待\インサイダーVCP.xlsx` |
| 追記列 | scan_date, symbol, company_name, execution_state, composite_score, price, valid_vcp, entry_ready, distance_from_pivot_pct, pattern_type, quality_rating, sector |
| フィルタ | actionable のみ（Pre-breakout / Breakout / Early-post-breakout） |

---

## スクリプト依存関係

```
vcp_daily_cloud.py          ← Cloud Run エントリポイント
  └─ subprocess: screen_vcp_jp.py   ← VCP スクリーナー本体（--full-sp500）
       ├─ jquants_bq_client.py      ← BQ → FMPClient 互換ラッパー
       │    └─ screen_tob_insider.py ← fetch_ohlcv / fetch_topix
       └─ VCP skills (git clone)
            /tmp/claude-trading-skills/skills/vcp-screener/scripts/
```

**Dockerfile**: `docker/Dockerfile.vcp-insider-daily`
- `python:3.12-slim` + `git` + `db-dtypes` + `google-cloud-bigquery` + `dropbox` + `openpyxl`
- `RUN git clone --depth 1 https://github.com/tradermonty/claude-trading-skills /tmp/claude-trading-skills`

---

## Cloud Run 対応変更点（`screen_tob_insider.py`）

| 変更箇所 | ローカル | Cloud Run |
|---------|--------|-----------|
| `_get_bq()` BQ認証 | サービスアカウントキー (`keys/gcp-service-account.json`) | ADC自動適用（`CLOUD_RUN_JOB` 環境変数で判定） |
| `CACHE_DIR` | `C:/tmp/tob_insider_screener` | `/tmp/tob_insider_screener` |

---

## 更新・再デプロイ手順

```bash
# 1. 一時ビルドディレクトリ作成
BUILD_DIR=$(mktemp -d /c/tmp/cloudbuild-XXXXXX)
mkdir -p "$BUILD_DIR/docker" "$BUILD_DIR/scripts/tob_prediction" "$BUILD_DIR/cloudbuild"
cp docker/Dockerfile.vcp-insider-daily "$BUILD_DIR/docker/"
cp scripts/tob_prediction/screen_tob_insider.py "$BUILD_DIR/scripts/tob_prediction/"
cp scripts/tob_prediction/jquants_bq_client.py "$BUILD_DIR/scripts/tob_prediction/"
cp scripts/tob_prediction/screen_vcp_jp.py "$BUILD_DIR/scripts/tob_prediction/"
cp scripts/tob_prediction/vcp_daily_cloud.py "$BUILD_DIR/scripts/tob_prediction/"
cp cloudbuild/cloudbuild.vcp-insider-daily.yaml "$BUILD_DIR/cloudbuild/"

# 2. ビルド & push & jobs update（cloudbuild.yaml が自動実行）
gcloud builds submit "$BUILD_DIR" \
  --config "$BUILD_DIR/cloudbuild/cloudbuild.vcp-insider-daily.yaml" \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source \
  --project gmailpj-357912

# 3. 後片付け
rm -rf "$BUILD_DIR"
```

## 手動実行・ログ確認

```bash
# 手動実行
gcloud run jobs execute vcp-insider-daily --region us-west1 --project gmailpj-357912

# ログ確認
gcloud logging read \
  "resource.type=cloud_run_job AND resource.labels.job_name=vcp-insider-daily" \
  --limit=30 --format="value(textPayload)" --project=gmailpj-357912

# スケジューラ停止/再開
gcloud scheduler jobs pause  vcp-insider-daily-scheduler --location us-west1
gcloud scheduler jobs resume vcp-insider-daily-scheduler --location us-west1
```

---

## 015 TOBスクリーナーとの関係

| 観点 | 015 TOBインサイダー | 016 VCP日次 |
|------|-------------------|------------|
| アルゴリズム | 静止スコア×発火スコア（独自実装） | Minervini VCP（Stage2+収縮+ピボット） |
| スクリプト | `screen_tob_insider.py` | `screen_vcp_jp.py`（`vcp_daily_cloud.py` 経由） |
| 実行形態 | ローカル手動 | Cloud Run Job 日次自動 |
| 出力 | `data/output/tob_insider_screen_*.csv` | Dropbox `インサイダーVCP.xlsx`（追記） |
| 目的 | 初動の「静止→発火」瞬間を検出 | 継続的な Stage2 上昇局面を追跡 |
| 補完関係 | 短期シグナル（急変検出） | 中期シグナル（トレンド継続確認） |

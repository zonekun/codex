# 20日β計算ジョブ（beta-calc）

**カテゴリ**: tools
**作成日**: 2026-04-10
**ステータス**: 有効
**関連ファイル**:
  - `scripts/beta_calc.py`
  - `docker/Dockerfile.beta-calc`
  - `cloudbuild/cloudbuild.beta-calc.yaml`

## 概要

全上場銘柄の直近20営業日β（TOPIX対比）を計算し、GCS に CSV キャッシュとして保存する。決算反応モデル（predict ノートブック）の因子9（テーマブースト）で使用。

## 背景・導入理由

決算反応モデルで「個人人気×テーマ性のある高β銘柄は、地合いがリスクオンのときに決算翌日リターンが上振れする」ことが統計的に確認された（EDA検証: 高β×TOPIX+ → mean +0.42% vs それ以外 -0.20%、p=0.009）。

yfinance の BETA（5年月次）は鈍すぎて直近のテーマ株化を捉えられない（例: 古野電気 yf β=0.16 vs 20日β=1.50）。自前20日βをBQで計算してキャッシュする方式を採用。

## Cloud Run Job

| 項目 | 値 |
|------|----|
| ジョブ名 | `beta-calc` |
| イメージ | `us-west1-docker.pkg.dev/gmailpj-357912/stock/beta-calc:latest` |
| スケジューラ | `beta-calc-trigger`（月〜金 18:30 JST）|
| 依存 | `stock-price-jquants-load`（18:00 JST）完了後 |
| メモリ | 512Mi |
| タイムアウト | 300s |
| 所要時間 | 約5秒 |

## 使い方

```bash
# ローカル実行
PYTHONUTF8=1 python scripts/beta_calc.py

# ドライラン（GCS書き込みなし）
PYTHONUTF8=1 python scripts/beta_calc.py --dry-run

# Cloud Run 手動実行
gcloud run jobs execute beta-calc --region=us-west1 --project=gmailpj-357912
```

## 出力

**GCS**: `gs://stock_data_1930932/earnings_model/zaraba_beta_20d/beta_20d.csv`

| カラム | 型 | 説明 |
|-------|----|------|
| TICKER | STRING | 銘柄コード（4桁） |
| beta_20d | FLOAT | 直近20営業日β（TOPIX対比） |
| calc_date | DATE | 計算日（JST） |

- 毎回全量上書き（WRITE_TRUNCATE相当）
- 約4,200銘柄
- ウィンドウ15日以上のデータがある銘柄のみ

## β計算ロジック

```
β = Cov(stock_ret, market_ret) / Var(market_ret)
```

- stock_ret: STOCK_PRICE_JQUANTS の ADJ_CLOSE 日次リターン
- market_ret: INDEX_PRICE の CLOSE 日次リターン（INDEX_CODE='0000' = TOPIX）
- ウィンドウ: 直近20営業日（ROWS BETWEEN 19 PRECEDING AND CURRENT ROW）
- BQスキャン範囲: 直近60日分のみ（コスト抑制）

## predict ノートブックでの使用（因子9）

```python
# セル5: GCSからβ読み込み
beta_map = {ticker: beta_20d}  # CSV → dict

# セル7: スコアリング
if beta_20d > 1.0 and topix_ret > 0:
    score += 1  # テーマブースト
```

## ビルド・デプロイ

```bash
# イメージビルド
gcloud builds submit \
  --config=cloudbuild/cloudbuild.beta-calc.yaml \
  --gcs-source-staging-dir=gs://gmailpj-357912-cloudbuild/source \
  --project=gmailpj-357912 \
  --region=us-west1 .
```

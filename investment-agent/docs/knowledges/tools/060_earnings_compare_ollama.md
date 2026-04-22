---
カテゴリ: tools
作成日: 2026-03-30
ステータス: 有効
---

# 決算資料比較分析 Cloud Run Job（Ollama + Qwen2.5 7B）

## 概要

GCS に保存済みの TDnet 決算 PDF を読み込み、最新Q と前Q を定性比較する Cloud Run Job。
Gemini コスト削減を目的に Ollama (Qwen2.5 7B) をローカル LLM として使用する。

## スクリプト構成

| ファイル | 役割 |
|---------|------|
| `scripts/earnings_compare/main.py` | エントリーポイント（環境変数受取・Ollama起動・結果出力） |
| `scripts/earnings_compare/quarter_calc.py` | 前Q算出（最新日 ±30日 / 前Q = -90日中心 ±45日） |
| `scripts/earnings_compare/pdf_loader.py` | BQ検索 → GCSフォールバック → pdfplumber テキスト抽出 |
| `scripts/earnings_compare/prompts.py` | 日本語定性比較プロンプト（5観点） |
| `scripts/earnings_compare/ollama_analyzer.py` | Ollama HTTP API クライアント（tenacity リトライ付き） |
| `docker/Dockerfile.earnings-compare` | Ollama + Python 3.12 slim |
| `scripts/setup_qwen_gcs.py` | 初回のみ: Qwen2.5 7B → GCS アップロード |
| `scripts/run_earnings_compare.sh` | Claude からのトリガー + ログ取得 |

## インフラ構成

- **実行形式**: Cloud Run Job（起動→分析→終了）
- **スペック**: 4 vCPU / 8 GiB RAM / GPU なし
- **モデル格納**: `gs://stock_data_1930932/test/ollama-models/` → FUSE マウント `/root/.ollama`
- **結果確認**: Cloud Logging（`gcloud run jobs executions logs`）

## モデル選定

| 比較 | 理由 |
|------|------|
| Llama 3.2 3B → **Qwen2.5 7B** に変更 | 3B は日本語が弱すぎる（★★☆）。Qwen2.5 7B は日本語訓練量が多く★★★★☆ |
| GPU なし CPU 実行 | 8 GiB RAM に ~4.7 GB (Q4_K_M) が収まる。FastAPI 不要で Job 形式が最安 |

## BQ カテゴリ値（重要）

`STOCK.TDNET_DOCUMENTS_ENHANCED` の `MAIN_CATEGORY` に `'決算'` という値は**存在しない**。

```sql
MAIN_CATEGORY IN ('決算短信', '決算説明資料', '業績修正', '業績予想')
```

件数（2026-03-30 確認）: 決算短信 153万件 / 決算説明資料 38万件 / 業績修正 3.3万件 / 業績予想 0.9万件

## GCS FUSE マウント（重要）

バケット内サブパスをマウントするには `only-dir` オプションを使う。

```bash
# Cloud Run Job 作成時
gcloud run jobs create earnings-compare \
  --image us-west1-docker.pkg.dev/gmailpj-357912/stock/earnings-compare:latest \
  --region us-west1 \
  --cpu 4 --memory 8Gi \
  --task-timeout 3600 \
  --add-volume name=ollama-models,type=cloud-storage,bucket=stock_data_1930932,mount-options=only-dir=test/ollama-models \
  --add-volume-mount volume=ollama-models,mount-path=/root/.ollama
```

- `mount-options=only-dir=<サブパス>` でバケット内の特定ディレクトリのみをマウント
- マウント先 `/root/.ollama` が Ollama のデフォルトホームと一致する

## Dockerfile の必須環境変数

```dockerfile
ENV PYTHONUTF8=1
ENV PYTHONUNBUFFERED=1
ENV OLLAMA_MODELS=/root/.ollama/models   # GCS FUSE マウント先と一致させる
```

## 初回セットアップ手順

```bash
# 1. モデルを GCS にアップロード（Linux 環境推奨・約4.7GB）
PYTHONUTF8=1 python scripts/setup_qwen_gcs.py

# 2. Docker イメージをビルド
gcloud builds submit --config cloudbuild/cloudbuild.earnings-compare.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source .

# 3. Cloud Run Job 作成（上記 gcloud run jobs create コマンド）

# 4. 実行（Claude コンソールから）
bash scripts/run_earnings_compare.sh 7203 2025-11-14
```

## 実行コマンド（Claude から呼び出す）

```bash
bash scripts/run_earnings_compare.sh <TICKER> <LATEST_DATE>
# 例: bash scripts/run_earnings_compare.sh 7203 2025-11-14
```

- `jobs update --update-env-vars` で環境変数を設定してから `jobs execute --wait` する
  （`execute` に `--set-env-vars` は使えない：MEMORY.md 参照）

## 前Q算出ロジック

| 最新Q | 前Q | 期間 |
|------|-----|------|
| Q2（11月開示） | Q1（8月開示） | 約3ヶ月前 |
| Q1（8月開示） | 前会計年度Q4（5月開示） | 約3ヶ月前 |
| Q3（2月開示） | Q2（11月開示） | 約3ヶ月前 |
| Q4/本決算（5月開示） | Q3（2月開示） | 約3ヶ月前 |

実装: `latest_date - 90日` を中心に ±45日のウィンドウで BQ 検索。

## プロンプト設計（5観点）

1. 事業環境認識の変化
2. 重点施策・戦略の変化
3. リスク認識の変化
4. 前向き・後ろ向きトーンの変化
5. 特記事項（前Qにない新規記述）

数値比較は行わない。経営陣のトーン・言葉の変化・強調点の移動に着目。

---

## 実験結論（2026-04-02 終了）

Cloud Run で Ollama + ローカルLLM を試したが、**有益ではなさそう**とユーザーが判断し実験終了。

### 試したモデル

| モデル | 環境 | 結果 |
|-------|------|------|
| Qwen2.5 7B (CPU) | us-west1, 4 vCPU / 8 GiB | 日本語品質が実用レベル未満 |
| Gemma3 12B (GPU) | us-east4, L4 GPU | 品質向上するもコスト面でGemini Flashに劣る |

### 削除済みリソース

- Cloud Run Jobs: `earnings-compare`, `earnings-compare-gemma3`, `tdnet-gemma3-benchmark` — 全削除
- GCS: `test/ollama-models/`, `test/ollama-models-gemma3/`, `benchmarks/tdnet_gemma3/` — 全削除

### 残した成果物（コード・ドキュメント）

- `cloudbuild/cloudbuild.earnings-compare*.yaml`, `cloudbuild.tdnet-gemma3-benchmark.yaml`
- `docker/Dockerfile.earnings-compare*`, `Dockerfile.tdnet-gem*`
- `scripts/earnings_compare/`, `scripts/tdnet_gemma3_benchmark/`

**結論:** Gemini Flash で十分な精度・コストパフォーマンス。Cloud Run ローカルLLM の再提案は不要。

# EDINET 有価証券報告書 ETL パイプライン (GCS → BigQuery)

**カテゴリ**: tools
**作成日**: 2026-03-01
**更新日**: 2026-04-08（submit/resume バッチ処理対応追加）
**ステータス**: 有効
**関連ファイル**: `scripts/edinet_load_parallel.py`
**パイプライン前段**: `docs/knowledges/tools/009_edinet_download.md`（EDINET API から HTML を GCS に保存するスクリプト。本スクリプトの入力元）
**動作確認環境**: ローカル / Cloud Run

---

## 概要

GCS `gs://stock_data_1930932/edinet/` に格納された EDINET の HTML ファイル（有価証券報告書等）を読み込み、
テキスト抽出・メタデータ解析・チャンク分割・Vertex AI **Batch Prediction** によるベクトル化を行い、
BigQuery `gmailpj-357912.STOCK.ir_documents_enhanced` へ格納する ETL プログラム。

**アーキテクチャ**: Batch Prediction（旧 ThreadPoolExecutor から移行。OOM 対策としてストリーミング結果処理を採用）
**対応環境**: ローカル / Cloud Run（マルチプラットフォーム）

---

## システム構成

| 項目 | 値 |
|------|-----|
| 実行環境 | ローカル / Cloud Run |
| 入力元 | GCS `gs://stock_data_1930932/edinet/` |
| 出力先 | BigQuery `gmailpj-357912.STOCK.ir_documents_enhanced` |
| AI/ML | Vertex AI Batch Prediction `text-embedding-004`（768次元ベクトル） |
| 主要ライブラリ | `google-cloud-storage`, `google-cloud-bigquery`, `google-genai`, `beautifulsoup4`, `langchain-text-splitters` |

---

## BigQuery テーブルスキーマ（ir_documents_enhanced）

| カラム | 型 | 説明 |
|--------|-----|------|
| `doc_id` | STRING NOT NULL | UUID |
| `security_code` | STRING NOT NULL | 4桁の証券コード |
| `filer_name` | STRING | 提出者名（会社名、ファンド名等） |
| `filer_id` | STRING | 提出者の EDINET コード（例: E10670） |
| `submission_date` | DATE | 提出日 |
| `doc_type` | STRING | 資料種別（有価証券報告書 / 公開買付関連 / 四半期・半期報告書） |
| `section_category` | STRING | 章名 |
| `chunk_text` | STRING | 本文（チャンク化済み） |
| `embedding` | ARRAY\<FLOAT64\> | ベクトルデータ（768次元） |
| `file_name` | STRING | GCS の元ファイルパス |
| `extracted_at` | TIMESTAMP | 抽出日時（DEFAULT CURRENT_TIMESTAMP） |

**パーティション**: `submission_date`
**クラスタリング**: `security_code`, `filer_id`, `doc_type`

---

## 処理フロー詳細

### ① 対象ファイルのフィルタリング

- 対象: `.htm` または `.html` 拡張子
- 除外1: ファイル名に「大量保有」が含まれるもの
- 除外2: HTML 本文の先頭 2000 文字以内に「大量保有報告書」が含まれるもの

### ② メタデータ抽出（BeautifulSoup + 正規表現）

| メタデータ | 抽出ルール |
|-----------|-----------|
| 証券コード | GCS パス `edinet/XXXX/...` から4桁数字を抽出 |
| 提出日 | `【提出日】` の記述を正規表現で取得。令和・平成（全角数字含む）→ `YYYY-MM-DD` に変換。取得不能時は実行日 |
| 提出者名 | `【会社名】` `【ファンド名】` `【発行者名】` `【提出者】` `【届出者】` `【氏名又は名称】` `【公開買付者】` のいずれかに続く文字列 |
| EDINET コード | `【EDINETコード】` に続く `E + 数字5桁`、またはテキスト中の `E + 数字5桁`（フォールバック） |
| 資料種別 | ファイル名に「公開買付」→ `公開買付関連`、「四半期」「半期」→ `四半期・半期報告書`、それ以外 → `有価証券報告書` |

### ③ チャンク分割（LangChain）

- `RecursiveCharacterTextSplitter(chunk_size=400, chunk_overlap=50)`
- セパレータ: `["\n\n", "\n", "。", "、", " "]`
- 章名の文脈保持: `【章名】` パターンを検知して現在章名を状態保持し、各チャンク先頭に `章名: {current_section}\n` を付与

### ④ Embedding フィルタ（doc_type 別）

| doc_type | Embedding 処理 | 備考 |
|----------|---------------|------|
| 有価証券報告書 | あり（セクションフィルタあり） | 下記15セクション除外 |
| 公開買付関連 | あり | 全セクション対象 |
| 四半期・半期報告書 | **なし（メタデータのみ）** | CHUNK_TEXT=NULL, EMBEDDING=NULL の1行 |

**有価証券報告書のセクションフィルタ（15セクション除外）:**
Embedding 対象外: 経理の状況（連結/個別財務諸表、注記含む）、提出会社の保証会社等の情報、独立監査人の報告書、確認書 等の定型セクション。

**チャンクサイズ調整:**

| セクション | chunk_size | 理由 |
|-----------|-----------|------|
| コーポレートガバナンスの状況等 | 200（半分） | 定型文が多くベクトル効率が悪い |
| 事業等のリスク | 100（1/4） | 冗長な記述が多い |
| その他 | 400（標準） | — |

### ⑤ ベクトル化（Vertex AI Batch Prediction）

- モデル: `text-embedding-004`（768次元）
- API: Vertex AI Batch Prediction（`google-genai` SDK）
- **ストリーミング結果処理**: 結果 JSONL を1行ずつ読み込み、即座に BQ insert（OOM 対策。全結果をメモリに保持しない）

### ⑥ BigQuery ロード（ストリーミングインサート）

- `insert_rows_json` を使用
- **100件（チャンク）ごと**のバッチサイズ（通信断絶 SSL EOF 対策）
- ファイル単位でインサート成否を判定
- エラー発生時は残チャンクを中断、ファイル全体をエラー扱い

### ⑦ ロギングとリラン（冪等性）制御

**ログ出力:**
- 処理の開始・スキップ理由・抽出完了・BQ ロード成功・各種エラーを標準出力と配列に同時保持
- 処理終了時（`finally` 句）に GCS へ保存: `edinet/bq_load_YYYYMMDD_HHMMSS_log.txt`
- タイムゾーン: JST

**リラン制御（冪等性）:**
- 処理開始時に GCS 上の過去ログ群を読み込む
- サイズ 0 バイトのログは無視
- 過去ログから `ロード成功: edinet/xxx.html` を抽出 → 「成功済」としてスキップ
- 過去ログで `エラー` と記録されたファイル、または成功記録がないファイルのみ処理対象

---

## 実行方法

### 3モード対応（full / submit / resume）

| モード | フロー | 用途 |
|--------|--------|------|
| **full**（デフォルト） | Phase 1→2→3 一気通貫 | 日次ロード |
| **submit** | Phase 1→2(Embedding投入のみ)→state保存→exit | バックフィル開始 |
| **resume** | state読込→2(Embedding結果適用)→3(BQ Insert)→state削除 | バックフィル完了 |

### ローカル実行

```bash
# full モード（日次・既存動作）
PYTHONUTF8=1 C:\venvs\investment-agent\Scripts\python.exe scripts/edinet_load_parallel.py --from 20260407 --to 20260407

# submit モード（バックフィル開始）
PYTHONUTF8=1 RUN_MODE=submit C:\venvs\investment-agent\Scripts\python.exe scripts/edinet_load_parallel.py --from 20240101 --to 20240630

# resume モード（バックフィル完了）
PYTHONUTF8=1 RUN_MODE=resume C:\venvs\investment-agent\Scripts\python.exe scripts/edinet_load_parallel.py --from 20240101 --to 20240630
```

### Cloud Run 実行

```bash
# full モード（日次）
gcloud run jobs execute edinet-load --region us-west1 \
    --args="--from=20260407,--to=20260407"

# submit モード（バックフィル開始）
gcloud run jobs execute edinet-load --region us-west1 \
    --args="--from=20240101,--to=20240630" \
    --update-env-vars RUN_MODE=submit

# resume モード（Cloud Functions で自動 or 手動）
gcloud run jobs execute edinet-load --region us-west1 \
    --update-env-vars RUN_MODE=resume,DATE_FROM=20240101,DATE_TO=20240630
```

### 環境変数（Cloud Run `--update-env-vars` 対応）

| 変数 | デフォルト | 説明 |
|------|-----------|------|
| `RUN_MODE` | `full` | 実行モード: `full` / `submit` / `resume` |
| `DATE_FROM` | — | 開始日 YYYYMMDD（`--from` 引数のフォールバック） |
| `DATE_TO` | — | 終了日 YYYYMMDD（`--to` 引数のフォールバック） |
| `DATE_MODE` | — | 日付モード: `t`=今日, `y`=昨日, `1`=固定日, `r`=範囲 |
| `TICKER_FROM` | — | ticker 範囲フィルタ（開始） |
| `TICKER_TO` | — | ticker 範囲フィルタ（終了） |

### submit/resume のバックフィル運用フロー

```
edinet-load (submit) [CPU 30-40分] → Embedding バッチ投入して exit
  ↓ state を GCS に保存: gs://stock_data_1930932/batch_prediction/edinet/backfill_state_*.json
  ↓
edinet-backfill-poller (Cloud Functions, 5分ごと) → ジョブ状態監視
  ↓ [SUCCEEDED 検知]
edinet-load (resume) [CPU 8-10分] → Embedding 結果取得 → BQ insert → state 削除
```

**Cloud Functions ポーラー**: `functions/edinet_backfill_poller/main.py`

---

## 定数・設定値

| 定数 | 値 | 説明 |
|------|-----|------|
| `PROJECT_ID` | `gmailpj-357912` | GCP プロジェクト |
| `LOCATION` | `us-central1` | Vertex AI のリージョン |
| `BUCKET_NAME` | `stock_data_1930932` | GCS バケット |
| `PREFIX` | `edinet/` | GCS 内プレフィックス |
| `TABLE_ID` | `gmailpj-357912.STOCK.ir_documents_enhanced` | BQ テーブル |
| `BQ_BATCH_SIZE` | `100` | BQ ストリーミングインサートのバッチ件数 |

---

## ETL対象判定ロジック（2段階）

| 判定 | 方法 | 優先度 |
|------|------|--------|
| **一次（BQ直接確認）** | `SELECT DISTINCT file_name FROM ir_documents_enhanced` で取得したロード済みset。ヒットしたら即スキップ | 高 |
| **補助（過去ログ解析）** | GCSのログファイルからエラーファイルを抽出。BQ未登録かつログエラーのファイルを優先リランとして識別 | 補助 |

> BQが正規の源泉。ログ解析はエラーファイルの識別にのみ使用する。

## 環境別の動作差異

| 項目 | colab_personal | colab_enterprise | cloudrun |
|------|----------------|-----------------|---------|
| 環境判別 | `google.colab` import 可 & `GOOGLE_CLOUD_PROJECT` 未設定 | `google.colab` import 可 & `GOOGLE_CLOUD_PROJECT` 設定あり | `CLOUD_RUN_JOB` or `K_SERVICE` 設定あり |
| GCP認証 | `auth.authenticate_user()` でインタラクティブ認証 | ADC（追加処理不要） | Attached Service Account の ADC |
| クライアント生成タイミング | `setup_environment()` 後（遅延初期化） | 同左 | 同左 |

## 注意事項・制約

- **マルチプラットフォーム対応済み**: Colab personal / enterprise / Cloud Run で動作
- **クライアントはすべて遅延初期化**: モジュール import 時には GCP 接続しない。`run_etl()` 内で初回利用時に作成
- **Batch Prediction**: Vertex AI Batch Prediction を使用。オンライン予測比で約50%コスト削減 + RPM制限なし
- **ストリーミングインサートのコスト**: BQ ストリーミングインサートは課金対象。大量処理時はコストに注意
- **冪等性**: BQ直接確認が一次判定のため、ログファイルがなくても再実行で重複インサートを防げる
- **エンコーディング**: 元ファイルは CP932 で作成されていたが、`scripts/edinet_load.py` は UTF-8 に変換済み
- **Cloud Run デプロイ済み**: Job名 `edinet-load`、リージョン `us-west1`、メモリ 2Gi、タイムアウト 36000s。ビルド設定: `docker/Dockerfile.edinet-load` / `cloudbuild/cloudbuild.edinet-load.yaml`
- **メインスクリプト**: `scripts/edinet_load_parallel.py`（Batch Prediction アーキテクチャ。旧 ThreadPoolExecutor 版から移行済み）
- **OOM 対策**: Embedding 結果を全件メモリ保持せず、GCS 上の結果 JSONL をストリーミングで1行ずつ処理して即 BQ insert

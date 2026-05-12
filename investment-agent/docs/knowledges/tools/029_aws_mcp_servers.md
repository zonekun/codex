# AWS MCP サーバー（Cloud Run）

**カテゴリ**: tools
**作成日**: 2026-03-08
**ステータス**: 有効（おまけ扱い）
**関連ファイル**:
- `scripts/aws_pricing_mcp_server.py` — aws-pricing-mcp Cloud Run エントリーポイント
- `scripts/aws_documentation_mcp_server.py` — aws-documentation-mcp Cloud Run エントリーポイント
- `docker/Dockerfile.aws-pricing-mcp` — aws-pricing-mcp イメージ定義
- `docker/Dockerfile.aws-documentation-mcp` — aws-documentation-mcp イメージ定義
- `cloudbuild/cloudbuild.aws-pricing-mcp.yaml` — Cloud Build 設定（pricing）
- `cloudbuild/cloudbuild.aws-documentation-mcp.yaml` — Cloud Build 設定（docs）
- `scripts/aws_mcp_search_colab.py` — Colab Personal 用検索スクリプト

---

## 概要

> **注意**: このサーバーは「おまけ」扱いであり、本プロジェクト（株式投資エージェント）の主要ツールではない。
> AWS のコスト見積もり・ドキュメント参照が必要な場合に任意で利用する。

[awslabs/mcp](https://github.com/awslabs/mcp) の AWS公式 MCP サーバー2本を Cloud Run Service として稼働させる。
J-Quants MCP サーバー（`027_jquants_mcp_server.md`）と同じ SSE transport / IAM 認証パターンで構築。

---

## ローカル直接実行: aws-api-mcp-server（汎用 AWS CLI ラッパー）

**追加日**: 2026-04-30

旧 `awslabs.core-mcp-server` は PyPI から yanked（廃止）。AWS は個別サービス別パッケージに分割した。
ローカルから EC2 Spot placement score 等の AWS CLI 操作を行うには **`awslabs.aws-api-mcp-server`** を使う。

### .mcp.json 設定（Windows）

```json
{
  "aws": {
    "command": "uvx",
    "args": [
      "--from",
      "awslabs.aws-api-mcp-server@latest",
      "awslabs.aws-api-mcp-server.exe"
    ],
    "env": {
      "AWS_ACCESS_KEY_ID": "（.env参照）",
      "AWS_SECRET_ACCESS_KEY": "（.env参照）",
      "AWS_REGION": "us-east-1"
    }
  }
}
```

> **Windows では `--from` + `.exe` サフィックスが必須**。Linux/macOS は `"args": ["awslabs.aws-api-mcp-server@latest"]` のみで可。

### 提供ツール

| ツール | 用途 |
|--------|------|
| `call_aws` | 任意の AWS CLI コマンド実行（`aws ec2 get-spot-placement-scores` 等） |
| `suggest_aws_commands` | 自然言語から AWS CLI コマンドを提案 |

### 廃止パッケージ

| パッケージ | 状態 | 代替 |
|-----------|------|------|
| `awslabs.core-mcp-server` | yanked（全バージョン） | サービス別パッケージに分割 |

### 主要な個別パッケージ一覧

| パッケージ | 用途 |
|-----------|------|
| `awslabs.aws-api-mcp-server` | 汎用 AWS CLI ラッパー（EC2, S3, IAM 等） |
| `awslabs.aws-pricing-mcp-server` | 料金照会（※Cloud Run版は別途上記参照） |
| `awslabs.aws-documentation-mcp-server` | ドキュメント検索 |
| `awslabs.cloudwatch-mcp-server` | CloudWatch メトリクス/ログ |
| `awslabs.ecs-mcp-server` | ECS コンテナ管理 |
| `awslabs.eks-mcp-server` | EKS Kubernetes |
| `awslabs.dynamodb-mcp-server` | DynamoDB 操作 |
| `awslabs.s3-tables-mcp-server` | S3 Tables |

---

## Cloud Run デプロイ版サービス情報

### aws-pricing-mcp（AWS サービス料金情報）

| 項目 | 値 |
|------|-----|
| Cloud Run Service 名 | `aws-pricing-mcp` |
| リージョン | `us-west1` |
| URL | `https://aws-pricing-mcp-480182964684.us-west1.run.app` |
| SSE エンドポイント | `/sse` |
| メッセージエンドポイント | `/messages/` |
| メモリ / CPU | 512Mi / 1 |
| イメージ | `us-west1-docker.pkg.dev/gmailpj-357912/tools/aws-pricing-mcp:latest` |
| 認証 | 必須（`--no-allow-unauthenticated`） |
| 依存 | `awslabs.aws-pricing-mcp-server` (PyPI) + boto3 |
| AWS 認証 | boto3 が必要。現状は環境変数 `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` で注入 |

### aws-documentation-mcp（AWS 公式ドキュメント検索）

| 項目 | 値 |
|------|-----|
| Cloud Run Service 名 | `aws-documentation-mcp` |
| リージョン | `us-west1` |
| URL | `https://aws-documentation-mcp-480182964684.us-west1.run.app` |
| SSE エンドポイント | `/sse` |
| メッセージエンドポイント | `/messages/` |
| メモリ / CPU | 512Mi / 1 |
| イメージ | `us-west1-docker.pkg.dev/gmailpj-357912/tools/aws-documentation-mcp:latest` |
| 認証 | 必須（`--no-allow-unauthenticated`） |
| 依存 | `awslabs.aws-documentation-mcp-server` (PyPI) |
| AWS 認証 | 不要（公開 AWS ドキュメント URL をスクレイピング） |

---

## アクセス制御（IAM）

| アカウント | 権限 | 用途 |
|----------|------|------|
| `zonekun@gmail.com` | `roles/run.invoker` | Claude Code ローカル / gcloud CLI |
| `bq-loader@gmailpj-357912.iam.gserviceaccount.com` | `roles/run.invoker` | Colab Personal（GCP_SA_KEY 経由） |

---

## 提供ツール

### aws-pricing-mcp
- `get_pricing`: AWS サービスの料金取得（boto3 pricing.get_products ラッパー）

### aws-documentation-mcp
- `search_documentation`: AWS ドキュメント全文検索
- `read_documentation`: 指定 URL の AWS ドキュメント取得・Markdown 変換

---

## イメージ更新手順

```bash
cd /c/gdrive/claude/investment-agent

# pricing サーバー更新
gcloud builds submit --config cloudbuild/cloudbuild.aws-pricing-mcp.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source .
gcloud run deploy aws-pricing-mcp \
  --image us-west1-docker.pkg.dev/gmailpj-357912/tools/aws-pricing-mcp:latest \
  --region us-west1 --project gmailpj-357912

# documentation サーバー更新
gcloud builds submit --config cloudbuild/cloudbuild.aws-documentation-mcp.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source .
gcloud run deploy aws-documentation-mcp \
  --image us-west1-docker.pkg.dev/gmailpj-357912/tools/aws-documentation-mcp:latest \
  --region us-west1 --project gmailpj-357912
```

---

## Colab Personal での使い方

`scripts/aws_mcp_search_colab.py` を Colab にアップロードして実行する。

**前提条件**: Colab Secrets に `GCP_SA_KEY` を登録済みであること（jquants 等と共通）

```python
# ツール一覧確認
show_tools()

# EC2 t3.micro の料金確認
result = search_pricing("AmazonEC2", [
    {"Type": "TERM_MATCH", "Field": "instanceType", "Value": "t3.micro"},
    {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
])

# AWS ドキュメント検索
result = search_aws_docs("S3 lifecycle rules")

# 特定ドキュメント取得
result = get_aws_doc("https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-configuration-examples.html")
```

---

## 実装上のポイント

J-Quants MCP サーバーと同一パターン（`027_jquants_mcp_server.md` の「実装上のポイント」を参照）。

- `mcp.sse_app()` + uvicorn で SSE サーバーとして起動
- `TransportSecurityMiddleware._validate_host/origin` を無効化して Cloud Run ホスト名を許可
- インストール方法: `pip install "awslabs.aws-pricing-mcp-server @ git+https://github.com/awslabs/mcp.git#subdirectory=src/aws-pricing-mcp-server"`

### aws-pricing-mcp の AWS 認証について

boto3 は AWS 認証情報が必要。Cloud Run の環境変数に設定する場合:
```bash
gcloud run services update aws-pricing-mcp \
  --region us-west1 \
  --set-env-vars "AWS_ACCESS_KEY_ID=xxx,AWS_SECRET_ACCESS_KEY=yyy,AWS_DEFAULT_REGION=us-east-1"
```
または AWS Pricing API は `us-east-1` リージョンにのみ存在するため `AWS_DEFAULT_REGION=us-east-1` が必要。

---

## aws_mcp_search_colab.py の改善履歴・設計ポイント

### Gemini 2.5 対応（2026-03-09）

- モデル: `gemini-2.5-flash`（`gemini-2.0-flash-001` から変更。日付サフィックスなしが正しい ID）
- `ThinkingConfig(thinking_budget=0)` で thinking を無効化（thinking content が JSON に混入してパースエラーになるのを防ぐ）
- `response_mime_type="application/json"` で JSON 出力を強制
- `ThinkingConfig` は SDK バージョンによって importできない場合があるため try/except でフォールバック

```python
from google.genai import types
config = types.GenerateContentConfig(
    response_mime_type="application/json",
    thinking_config=types.ThinkingConfig(thinking_budget=0),
)
```

- レスポンスから thinking パートを除外: `part.thought == True` のパートをスキップ
- JSON 抽出はネスト対応の括弧カウント方式（`re.search(r"\{.*\}")` はネスト構造に弱い）

### MCP 横断ルール（2026-03-09）

- Pricing MCP で RI が見つからなくても即 `done` にしない → Cost Explorer MCP も試みる
- 1サーバーで情報不足の場合は別サーバーを必ず試みてから回答させる
- システムプロンプトと `_CONTINUE_PROMPT` 両方に明示

### その他

- ツール結果の上限: 5000 → 10000 文字に拡張

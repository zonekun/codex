# FRED MCP サーバー（Cloud Run）

**カテゴリ**: tools
**作成日**: 2026-03-07
**ステータス**: 有効
**関連ファイル**:
- `scripts/fred_mcp_server_sse.ts` — SSE エントリーポイント（Cloud Run 用）
- `docker/Dockerfile.fred-mcp` — Docker イメージ定義
- `cloudbuild/cloudbuild.fred-mcp.yaml` — Cloud Build 設定
- `.mcp.json` — Claude Code MCP 接続設定

---

## 概要

FRED（Federal Reserve Economic Data）API の MCP サーバーを Cloud Run Service として稼働させ、
Claude Code からリモート SSE 接続でマクロ経済データを検索・取得できるようにした。

ベースリポジトリ: `https://github.com/kablewy/fred-mcp-server`（TypeScript + Node.js）

---

## デプロイ済みサービス情報

| 項目 | 値 |
|------|-----|
| Cloud Run Service 名 | `fred-mcp` |
| リージョン | `us-west1` |
| URL | `https://fred-mcp-480182964684.us-west1.run.app` |
| SSE エンドポイント | `/sse` |
| メッセージエンドポイント | `/messages/` |
| ヘルスチェック | `/health` |
| 最小インスタンス | 0（リクエストなし時は課金なし） |
| CPU 割当 | リクエスト処理中のみ（`--cpu-throttling`） |
| メモリ / CPU | 512Mi / 1 |
| イメージ | `us-west1-docker.pkg.dev/gmailpj-357912/fred-mcp/fred-mcp:latest` |
| 認証 | 必須（`--no-allow-unauthenticated`） |
| サービスアカウント | `bq-loader@gmailpj-357912.iam.gserviceaccount.com` |
| FRED API KEY | Cloud Run 環境変数 `FRED_API_KEY` に設定済み |

---

## 提供ツール（MCP Tools）

| ツール名 | 機能 |
|---------|------|
| `search` | FRED データシリーズをキーワード検索（フィルタ・ソート対応） |
| `get_series` | 指定シリーズの観測値を取得（日付範囲・頻度・集計方法・ページネーション対応） |

### search の主要パラメータ
- `searchText` (必須): 検索テキスト（例: "GDP", "CPI", "unemployment"）
- `limit`: 取得件数上限
- `orderBy`: ソート基準（`popularity`, `last_updated` 等）
- `tagNames`: タグフィルタ（例: `["japan"]`, `["monthly"]`）

### get_series の主要パラメータ
- `seriesId` (必須): シリーズID（例: `"GDP"`, `"UNRATE"`, `"CPIAUCSL"`）
- `startDate` / `endDate`: 期間指定（YYYY-MM-DD）
- `frequency`: 頻度変換（`d`=日次, `w`=週次, `m`=月次, `q`=四半期, `a`=年次）
- `aggregationMethod`: 集計方法（`avg`, `sum`, `eop`）

---

## アクセス制御（IAM）

| アカウント | 用途 |
|----------|------|
| `bq-loader@gmailpj-357912.iam.gserviceaccount.com` | Colab / Cloud Run からのアクセス |
| `zonekun@gmail.com` | Claude Code ローカルからのアクセス |

---

## Claude Code での使い方

`.mcp.json` に設定済み。Claude Code 起動時に自動で SSE 接続する。

```json
{
  "mcpServers": {
    "fred": {
      "command": "bash",
      "args": [
        "-c",
        "TOKEN=$(gcloud auth print-identity-token 2>/dev/null); exec uvx mcp-remote https://fred-mcp-480182964684.us-west1.run.app/sse --header \"Authorization: Bearer ${TOKEN}\""
      ]
    }
  }
}
```

---

## イメージ更新手順

```bash
cd /c/gdrive/claude/investment-agent

# 再ビルド & プッシュ
gcloud builds submit --config cloudbuild/cloudbuild.fred-mcp.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source .

# Cloud Run に反映
gcloud run services update fred-mcp \
  --image us-west1-docker.pkg.dev/gmailpj-357912/fred-mcp/fred-mcp:latest \
  --region us-west1 \
  --project gmailpj-357912
```

---

## 実装上のポイント

### TypeScript MCP SDK の SSE 対応

元のリポジトリは `StdioServerTransport` で stdio 通信のみ。
Cloud Run で SSE エンドポイントを公開するため `scripts/fred_mcp_server_sse.ts` を作成し、
Express + `SSEServerTransport` に切り替えた。

複数の同時接続に対応するため、セッションIDで transport を管理：

```typescript
const transports = new Map<string, SSEServerTransport>();

app.get("/sse", async (_req, res) => {
  const transport = new SSEServerTransport("/messages/", res);
  transports.set(transport.sessionId, transport);
  const server = createServer();  // 接続ごとに新しい Server インスタンス
  await server.connect(transport);
});

app.post("/messages/", async (req, res) => {
  const transport = transports.get(req.query.sessionId as string);
  await transport.handlePostMessage(req, res);
});
```

### Docker ビルド時の SSL 証明書エラー

`node:20-slim` は `ca-certificates` を含まないため、
`git clone https://github.com/...` が SSL 検証エラーで失敗する。

```dockerfile
RUN apt-get install -y --no-install-recommends git ca-certificates
```

### Artifact Registry クリーンアップポリシー

`fred-mcp` リポジトリに設定済み:
- 最新3バージョンを保持
- 30日以上古いイメージを自動削除

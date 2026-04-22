# J-Quants MCP サーバー（ローカル直接起動）

**カテゴリ**: tools
**作成日**: 2026-03-07
**更新日**: 2026-03-28
**ステータス**: 有効
**関連ファイル**:
- `.mcp.json` — Claude Code MCP 接続設定

---

## 概要

J-Quants 公式 MCP サーバー（`j-quants-doc-mcp`）を uv tool でローカルインストールし、
Claude Code から直接起動する方式。Node.js 不要。

- 公式リポジトリ: https://github.com/J-Quants/j-quants-doc-mcp
- 公式ドキュメント: https://jpx-jquants.com/ja/spec/mcp-server

---

## インストール

```bash
uv tool install git+https://github.com/J-Quants/j-quants-doc-mcp.git
```

更新時:
```bash
uv tool upgrade j-quants-doc-mcp
```

---

## `.mcp.json` 設定

```json
{
  "mcpServers": {
    "jquants-doc": {
      "command": "j-quants-doc-mcp"
    }
  }
}
```

---

## 提供ツール（MCP Tools）

| ツール名 | 機能 |
|---------|------|
| `search_endpoints` | J-Quants エンドポイントをキーワード検索 |
| `describe_endpoint` | エンドポイント詳細（パス・パラメータ・レスポンス・対応プラン）取得 |
| `generate_sample_code` | エンドポイントの実行可能 Python サンプルコード生成 |
| `get_pattern` | 実装パターン取得（認証・ページネーション等） |
| `answer_question` | FAQ 回答 |
| `lookup_property` | プロパティ参照データ検索（列名から有効値一覧を取得） |

---

## 旧構成（廃止済み）

以前は Cloud Run Service `jquants-mcp`（us-west1）に `mcp-remote`（Node.js）経由で接続していたが、
2026-03-28 に廃止。公式パッケージのローカル直接起動に移行。

削除済みファイル:
- `scripts/jquants_mcp_server.py`
- `docker/Dockerfile.jquants-mcp`
- `cloudbuild/cloudbuild.jquants-mcp.yaml`

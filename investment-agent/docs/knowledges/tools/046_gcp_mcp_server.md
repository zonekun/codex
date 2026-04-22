# GCP MCP サーバー（ローカル stdio 版）

**カテゴリ**: tools
**作成日**: 2026-03-15
**ステータス**: 有効
**関連ファイル**: `scripts/gcp_mcp_server.py`, `.mcp.json`

## 概要

BigQuery クエリ・GCS ファイル操作・Cloud Logging 取得を MCP ツールとして提供する
ローカル stdio 型 MCP サーバー。Claude Code のサブプロセスとして起動する。

## 設計方針

- **jquants-doc / fred との違い**: それらは外部 Cloud Run SSE サービス。GCP MCP は認証キーがローカルにあるため **ローカル stdio 方式** が適切。
- **Cloud Run 不要**: コスト不要、デプロイ不要。Claude Code 起動時に自動起動・終了時に自動停止。
- **SSL 回避パッチ**: ローカル BQ 接続の SSL 問題に対応（スクリプト先頭に `requests.Session` パッチ実装済み）。
- **Cloud Logging は gcloud CLI 経由**: SDK の SSL 問題を根本回避するため `subprocess` で `gcloud logging read` を実行。

## ツール一覧

| ツール | 引数 | 説明 |
|--------|------|------|
| `bq_query(sql, max_rows=500)` | SQL文 | BQ SQL 実行 → CSV 返却 |
| `bq_schema(table, dataset="STOCK")` | テーブル名 | スキーマ・行数確認 |
| `bq_list_tables(dataset="STOCK")` | データセット名 | テーブル一覧 |
| `gcs_list(prefix="", max_results=200)` | プレフィックス | GCS ファイル一覧 |
| `gcs_read(blob_name, encoding="utf-8")` | ブロブパス | テキストファイル読み込み（500KB超は先頭200行） |
| `logging_read(filter_str, limit=50, freshness="1d")` | フィルター文字列 | Cloud Logging 汎用取得 |
| `logging_job(job_name, limit=100, execution_id="", freshness="2d")` | ジョブ名 | Cloud Run Job ログ簡単取得 |

## .mcp.json 設定

```json
"gcp": {
  "command": "bash",
  "args": [
    "-c",
    "cd /c/gdrive/claude/investment-agent && PYTHONUTF8=1 python scripts/gcp_mcp_server.py"
  ]
}
```

## インストール

```bash
# mcp ライブラリ（必須）
pip install mcp

# google-cloud-logging（必須）
pip install google-cloud-logging
```

## 使用例

```
# BQ クエリ
bq_query("SELECT TICKER, COUNT(*) FROM STOCK.TDNET_DOCUMENTS_ENHANCED WHERE SUBMISSION_DATE >= '2025-01-01' GROUP BY 1 ORDER BY 2 DESC LIMIT 10")

# GCS ファイル確認
gcs_list("config/")
gcs_read("config/monthly_disclosure_master.csv")

# Cloud Run Job ログ確認
logging_job("tdnet-load-recovery")
logging_job("tdnet-load-parallel", execution_id="tdnet-load-parallel-xcj5r")
logging_read("resource.type=cloud_run_job AND textPayload:\"エラー\"", limit=20)
```

## 旧方法との対応表（切り替え済み）

GCP MCP が使えるようになったため、以下の旧方法は **GCP MCP に置き換える**。

| 旧方法 | GCP MCP ツール |
|--------|---------------|
| `gcloud run jobs executions describe ... \| grep tasks` | `logging_job("job名", execution_id="xxx")` |
| `python scripts/check_exec_log.py job exec-id` | `logging_job("job名", execution_id="xxx")` |
| ad-hoc BQ クエリ用 Python 一時スクリプト | `bq_query(sql)` |
| `gcloud logging read ...` | `logging_read(filter_str)` |
| GCS ファイル確認（月次開示マスタ等） | `gcs_read("config/monthly_disclosure_master.csv")` |
| BQ テーブルスキーマ確認 | `bq_schema("STOCK_PRICE")` |

> **ルール**: 上記の旧方法を使おうとしたら、まず GCP MCP ツールで代替できないか確認すること。

## 根拠・出典

- 2026-03-15 セッションで実装
- jquants-doc / fred と同じ `.mcp.json` に追記する形で統合
- 2026-03-15 切り替え表を追加（旧方法からの移行完了）

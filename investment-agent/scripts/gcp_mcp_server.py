"""
Google Cloud MCP サーバー（ローカル stdio 版）

Claude Code のサブプロセスとして起動し、BQ / GCS / Cloud Logging / Cloud Run への
アクセスを MCP ツールとして提供する。gcloud CLI 不要。

ツール一覧:
  bq_query(sql)                     - BigQuery SQL 実行（結果を CSV 形式で返す）
  bq_schema(dataset, table)         - テーブルスキーマ取得
  bq_list_tables(dataset)           - テーブル一覧
  gcs_list(prefix, max_results)     - GCS blobs 一覧
  gcs_read(blob_name)               - GCS テキストファイル読み込み
  logging_read(filter_str, limit)   - Cloud Logging 取得
  logging_job(job_name, limit)      - Cloud Run Job のログを簡単取得
  cloudrun_execute(job_name, args)  - Cloud Run Job を実行する
  cloudrun_executions(job_name)     - Cloud Run Job の実行一覧を取得する

設定:
  - 認証: keys/gcp-service-account.json
  - プロジェクト: gmailpj-357912
  - GCS バケット: stock_data_1930932
"""

import os
import sys
import urllib3
import requests as _req
from requests.adapters import HTTPAdapter as _HA

# ── SSL 回避パッチ（BQ ローカル接続問題対策）──────────────────────────────
urllib3.disable_warnings()

class _NoVerify(_HA):
    def send(self, req, **kw):
        kw["verify"] = False
        return super().send(req, **kw)

_orig = _req.Session.__init__
def _patched(self, *a, **kw):
    _orig(self, *a, **kw)
    self.mount("https://", _NoVerify())
    self.verify = False
_req.Session.__init__ = _patched
# ─────────────────────────────────────────────────────────────────────────────

from google.oauth2 import service_account
from google.cloud import bigquery, storage
from google.cloud import logging as cloud_logging
from google.cloud.run_v2 import JobsClient, ExecutionsClient
from google.cloud.run_v2.types import RunJobRequest
import google.protobuf.timestamp_pb2
from mcp.server.fastmcp import FastMCP

# ── 設定 ──────────────────────────────────────────────────────────────────────
PROJECT_ID  = "gmailpj-357912"
BUCKET_NAME = "stock_data_1930932"
BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY_FILE    = os.path.join(BASE_DIR, "keys", "gcp-service-account.json")

creds = service_account.Credentials.from_service_account_file(KEY_FILE)

mcp = FastMCP("gcp")


# ── BigQuery ──────────────────────────────────────────────────────────────────

@mcp.tool()
def bq_query(sql: str, max_rows: int = 500) -> str:
    """BigQuery SQL を実行して結果を返す。

    Args:
        sql: 実行する SQL 文（SELECT のみ推奨）
        max_rows: 取得する最大行数（デフォルト 500）

    Returns:
        CSV 形式の結果文字列。行数が多い場合は先頭 max_rows 行のみ。
    """
    bq = bigquery.Client(project=PROJECT_ID, credentials=creds)
    try:
        df = bq.query(sql).to_dataframe()
        if len(df) > max_rows:
            result = df.head(max_rows).to_csv(index=False)
            return f"[先頭 {max_rows} 行を表示。全 {len(df)} 行]\n{result}"
        return df.to_csv(index=False)
    except Exception as e:
        return f"エラー: {e}"


@mcp.tool()
def bq_schema(table: str, dataset: str = "STOCK") -> str:
    """BigQuery テーブルのスキーマを取得する。

    Args:
        table: テーブル名（例: STOCK_PRICE, TDNET_DOCUMENTS_ENHANCED）
        dataset: データセット名（デフォルト: STOCK）

    Returns:
        カラム名・型・説明の一覧
    """
    bq = bigquery.Client(project=PROJECT_ID, credentials=creds)
    try:
        tbl = bq.get_table(f"{PROJECT_ID}.{dataset}.{table}")
        lines = [f"テーブル: {PROJECT_ID}.{dataset}.{table}"]
        lines.append(f"行数（推定）: {tbl.num_rows:,}")
        lines.append("")
        lines.append("カラム:")
        for field in tbl.schema:
            desc = f"  # {field.description}" if field.description else ""
            lines.append(f"  {field.name}: {field.field_type}{desc}")
        return "\n".join(lines)
    except Exception as e:
        return f"エラー: {e}"


@mcp.tool()
def bq_list_tables(dataset: str = "STOCK") -> str:
    """BigQuery データセット内のテーブル一覧を取得する。

    Args:
        dataset: データセット名（デフォルト: STOCK）
    """
    bq = bigquery.Client(project=PROJECT_ID, credentials=creds)
    try:
        tables = list(bq.list_tables(f"{PROJECT_ID}.{dataset}"))
        lines = [f"データセット: {PROJECT_ID}.{dataset}", ""]
        for t in tables:
            lines.append(f"  {t.table_id}")
        return "\n".join(lines)
    except Exception as e:
        return f"エラー: {e}"


# ── GCS ───────────────────────────────────────────────────────────────────────

@mcp.tool()
def gcs_list(prefix: str = "", max_results: int = 200) -> str:
    """GCS バケット内のファイル一覧を取得する。

    Args:
        prefix: 絞り込みプレフィックス（例: "tdnet/1301/", "config/", "log/"）
        max_results: 最大取得件数（デフォルト 200）

    Returns:
        ファイルパス一覧（1行1ファイル）
    """
    gcs = storage.Client(project=PROJECT_ID, credentials=creds)
    try:
        blobs = gcs.list_blobs(BUCKET_NAME, prefix=prefix, max_results=max_results)
        names = [b.name for b in blobs]
        header = f"バケット: gs://{BUCKET_NAME}/ (prefix={prefix!r}, {len(names)} 件)\n"
        return header + "\n".join(names)
    except Exception as e:
        return f"エラー: {e}"


@mcp.tool()
def gcs_read(blob_name: str, encoding: str = "utf-8") -> str:
    """GCS のテキストファイルを読み込む。

    Args:
        blob_name: ファイルパス（例: "config/monthly_disclosure_master.csv"）
        encoding: 文字コード（デフォルト: utf-8）

    Returns:
        ファイルの内容（テキスト）
    """
    gcs = storage.Client(project=PROJECT_ID, credentials=creds)
    try:
        blob = gcs.bucket(BUCKET_NAME).blob(blob_name)
        if not blob.exists():
            return f"ファイルが見つかりません: gs://{BUCKET_NAME}/{blob_name}"
        content = blob.download_as_text(encoding=encoding)
        size_kb = len(content.encode(encoding)) / 1024
        if size_kb > 500:
            # 大きすぎる場合は先頭のみ
            lines = content.splitlines()
            preview = "\n".join(lines[:200])
            return f"[ファイルサイズ {size_kb:.0f}KB のため先頭200行を表示]\n{preview}"
        return content
    except Exception as e:
        return f"エラー: {e}"


# ── Cloud Logging ─────────────────────────────────────────────────────────────

def _freshness_to_filter(freshness: str) -> str:
    """例: "1d" → 'timestamp>="2026-03-16T..."' 形式のフィルター文字列を返す。"""
    import datetime
    units = {"h": 3600, "d": 86400, "m": 60}
    unit = freshness[-1]
    num = int(freshness[:-1])
    delta = datetime.timedelta(seconds=num * units.get(unit, 3600))
    since = (datetime.datetime.utcnow() - delta).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f'timestamp>="{since}"'


def _fetch_logs(filter_str: str, limit: int, freshness: str) -> str:
    """google-cloud-logging SDK でログを取得する（gcloud 不要）。"""
    log_client = cloud_logging.Client(project=PROJECT_ID, credentials=creds)
    full_filter = filter_str + " AND " + _freshness_to_filter(freshness)
    entries = log_client.list_entries(
        filter_=full_filter,
        order_by=cloud_logging.DESCENDING,
        max_results=limit,
        page_size=min(limit, 1000),
    )
    lines = []
    for entry in entries:
        ts = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S") if entry.timestamp else ""
        payload = entry.payload if isinstance(entry.payload, str) else str(entry.payload)
        if payload:
            lines.append(f"{ts}  {payload}")
    return "\n".join(lines) if lines else "（ログなし）"


@mcp.tool()
def logging_read(filter_str: str, limit: int = 50, freshness: str = "1d") -> str:
    """Cloud Logging からログを取得する（gcloud 不要）。

    Args:
        filter_str: ログフィルター（例: 'resource.type=cloud_run_job AND textPayload:"エラー"'）
        limit: 取得件数（デフォルト 50）
        freshness: 取得期間（例: "1h", "6h", "1d", "3d"）

    Returns:
        タイムスタンプ + メッセージの一覧
    """
    try:
        return _fetch_logs(filter_str, limit, freshness)
    except Exception as e:
        return f"エラー: {e}"


@mcp.tool()
def logging_job(job_name: str, limit: int = 100, execution_id: str = "", freshness: str = "2d") -> str:
    """Cloud Run Job のログを取得する（よく使うパターンの簡略版。gcloud 不要）。

    Args:
        job_name: ジョブ名（例: "tdnet-load-parallel", "find-monthly-page-urls"）
        limit: 取得件数（デフォルト 100）
        execution_id: 特定の実行IDに絞る場合（例: "tdnet-load-parallel-xcj5r"）
        freshness: 取得期間（デフォルト: "2d"）

    Returns:
        ログ行の一覧
    """
    filter_parts = [f'resource.type="cloud_run_job" AND resource.labels.job_name="{job_name}"']
    if execution_id:
        filter_parts.append(f'labels."run.googleapis.com/execution_name"="{execution_id}"')
    filter_str = " AND ".join(filter_parts)
    try:
        return _fetch_logs(filter_str, limit, freshness)
    except Exception as e:
        return f"エラー: {e}"


# ── Cloud Run ──────────────────────────────────────────────────────────────────

REGION = "us-west1"


@mcp.tool()
def cloudrun_execute(job_name: str, args: list[str] | None = None) -> str:
    """Cloud Run Job を実行する（gcloud 不要）。

    Args:
        job_name: ジョブ名（例: "update-monthly-adapters"）
        args: コンテナへの引数リスト（例: ["--categories", "no_links_confirmed"]）

    Returns:
        実行ID（execution name）
    """
    try:
        client = JobsClient(credentials=creds)
        parent = f"projects/{PROJECT_ID}/locations/{REGION}/jobs/{job_name}"
        req = RunJobRequest(name=parent)
        if args:
            from google.cloud.run_v2.types import RunJobRequest as RJR
            override = RJR.Overrides(
                container_overrides=[
                    RJR.Overrides.ContainerOverride(args=args)
                ]
            )
            req = RunJobRequest(name=parent, overrides=override)
        op = client.run_job(request=req)
        # operation の metadata から execution name を取得
        meta = op.metadata
        exec_name = getattr(meta, "name", str(meta)) if meta else "（取得中）"
        return f"実行開始: {exec_name}\n完了を logging_job('{job_name}') で確認してください。"
    except Exception as e:
        return f"エラー: {e}"


@mcp.tool()
def cloudrun_executions(job_name: str, limit: int = 10) -> str:
    """Cloud Run Job の直近実行一覧を取得する（gcloud 不要）。

    Args:
        job_name: ジョブ名（例: "update-monthly-adapters"）
        limit: 取得件数（デフォルト 10）

    Returns:
        実行ID・状態・開始/完了時刻の一覧
    """
    try:
        client = ExecutionsClient(credentials=creds)
        parent = f"projects/{PROJECT_ID}/locations/{REGION}/jobs/{job_name}"
        executions = client.list_executions(parent=parent)
        lines = [f"ジョブ: {job_name}"]
        count = 0
        for ex in executions:
            if count >= limit:
                break
            name = ex.name.split("/")[-1]
            conds = ex.conditions
            status = conds[0].type_ if conds else "UNKNOWN"
            start = ex.start_time.strftime("%Y-%m-%d %H:%M:%S") if ex.start_time else "-"
            end = ex.completion_time.strftime("%Y-%m-%d %H:%M:%S") if ex.completion_time else "実行中"
            lines.append(f"  {name}  {status}  {start} → {end}")
            count += 1
        return "\n".join(lines) if count > 0 else f"実行履歴なし: {job_name}"
    except Exception as e:
        return f"エラー: {e}"


if __name__ == "__main__":
    mcp.run(transport="stdio")

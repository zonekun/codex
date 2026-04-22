"""決算資料比較分析 Cloud Run Job エントリーポイント.

環境変数:
    TICKER       : 銘柄コード（4桁）例: 7203
    LATEST_DATE  : 最新決算開示日 (YYYY-MM-DD) 例: 2025-11-14
    GCP_KEY_FILE : サービスアカウントキーパス（ローカル実行時のみ）

実行:
    PYTHONUTF8=1 TICKER=7203 LATEST_DATE=2025-11-14 python scripts/earnings_compare/main.py
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone, timedelta

import structlog
from google.cloud import bigquery, storage

from scripts.earnings_compare.ollama_analyzer import (
    start_ollama_server,
    wait_for_ollama,
    check_model_loaded,
    generate,
)
from scripts.earnings_compare.pdf_loader import load_quarter_documents
from scripts.earnings_compare.prompts import SYSTEM_PROMPT, build_comparison_prompt
from scripts.earnings_compare.quarter_calc import get_quarter_date_ranges

# ──────────────────────────────────────────
# 設定
# ──────────────────────────────────────────
PROJECT = "gmailpj-357912"
JST = timezone(timedelta(hours=+9), "JST")


# ──────────────────────────────────────────
# ロギング設定
# ──────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger()


# ──────────────────────────────────────────
# 実行環境判別
# ──────────────────────────────────────────
def detect_runtime() -> str:
    """実行環境を判別する.

    Returns:
        "cloudrun" | "local"
    """
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    return "local"


RUNTIME = detect_runtime()


# ──────────────────────────────────────────
# SSL パッチ（ローカルのみ）
# ──────────────────────────────────────────
def _apply_ssl_patch() -> None:
    """Windows ローカル環境の SSLEOFError 回避パッチ."""
    import urllib3
    import requests as _req
    from requests.adapters import HTTPAdapter

    urllib3.disable_warnings()

    class _NoVerify(HTTPAdapter):
        def send(self, req, **kw):
            kw["verify"] = False
            return super().send(req, **kw)

    _orig = _req.Session.__init__

    def _patched(self, *a, **kw):
        _orig(self, *a, **kw)
        self.mount("https://", _NoVerify())
        self.verify = False

    _req.Session.__init__ = _patched


if RUNTIME == "local":
    _apply_ssl_patch()


# ──────────────────────────────────────────
# GCP クライアント初期化
# ──────────────────────────────────────────
def build_gcp_clients() -> tuple[bigquery.Client, storage.Client]:
    """実行環境に応じた BQ・GCS クライアントを生成する.

    Returns:
        (bq_client, gcs_client)
    """
    if RUNTIME == "local":
        key_file = os.environ.get("GCP_KEY_FILE", "keys/gcp-service-account.json")
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(key_file)
        bq_client = bigquery.Client(project=PROJECT, credentials=creds)
        gcs_client = storage.Client(project=PROJECT, credentials=creds)
    else:
        bq_client = bigquery.Client(project=PROJECT)
        gcs_client = storage.Client(project=PROJECT)

    return bq_client, gcs_client


# ──────────────────────────────────────────
# テキスト結合（複数書類対応）
# ──────────────────────────────────────────
def combine_texts(docs: list[dict], max_total_chars: int = 10_000) -> str:
    """複数書類のテキストを結合して上限文字数に収める.

    Args:
        docs: load_quarter_documents の返り値。
        max_total_chars: 結合後の最大文字数。

    Returns:
        結合テキスト。
    """
    parts: list[str] = []
    total = 0
    for doc in docs:
        header = f"【{doc['main_category']} / {doc['submission_date']}】\n"
        text = doc.get("text", "")
        remaining = max_total_chars - total - len(header)
        if remaining <= 0:
            break
        parts.append(header + text[:remaining])
        total += len(header) + min(len(text), remaining)

    return "\n\n".join(parts)


# ──────────────────────────────────────────
# メイン処理
# ──────────────────────────────────────────
def main() -> None:
    """Cloud Run Job のメイン処理."""
    # 環境変数から入力取得
    ticker = os.environ.get("TICKER", "").strip()
    latest_date_str = os.environ.get("LATEST_DATE", "").strip()

    if not ticker or not latest_date_str:
        logger.error("missing_env_vars",
                     message="TICKER と LATEST_DATE を環境変数で指定してください")
        sys.exit(1)

    try:
        latest_date = date.fromisoformat(latest_date_str)
    except ValueError:
        logger.error("invalid_date_format",
                     latest_date=latest_date_str,
                     message="YYYY-MM-DD 形式で指定してください")
        sys.exit(1)

    logger.info("job_start", ticker=ticker, latest_date=str(latest_date),
                runtime=RUNTIME)

    # 四半期検索期間算出
    latest_range, prev_range = get_quarter_date_ranges(latest_date)
    logger.info("quarter_ranges",
                latest_range=str(latest_range),
                prev_range=str(prev_range))

    # GCP クライアント
    bq_client, gcs_client = build_gcp_clients()

    # PDF 取得・テキスト抽出
    logger.info("loading_latest_quarter_docs")
    latest_docs = load_quarter_documents(
        bq_client, gcs_client, ticker,
        latest_range.date_from, latest_range.date_to,
    )

    logger.info("loading_prev_quarter_docs")
    prev_docs = load_quarter_documents(
        bq_client, gcs_client, ticker,
        prev_range.date_from, prev_range.date_to,
    )

    if not latest_docs:
        logger.error("no_latest_docs", ticker=ticker, range=str(latest_range))
        sys.exit(1)

    if not prev_docs:
        logger.error("no_prev_docs", ticker=ticker, range=str(prev_range))
        sys.exit(1)

    logger.info("docs_loaded",
                latest_count=len(latest_docs),
                prev_count=len(prev_docs))

    # テキスト結合
    latest_text = combine_texts(latest_docs)
    prev_text = combine_texts(prev_docs)

    latest_label = f"{latest_date_str} 開示分（{', '.join(d['main_category'] for d in latest_docs[:3])}）"
    prev_label_date = str(prev_docs[0]["submission_date"]) if prev_docs else "不明"
    prev_label = f"{prev_label_date} 開示分（{', '.join(d['main_category'] for d in prev_docs[:3])}）"

    # Ollama サーバー起動
    ollama_proc = start_ollama_server()
    try:
        if not wait_for_ollama():
            logger.error("ollama_not_ready")
            sys.exit(1)

        if not check_model_loaded():
            logger.error("model_not_found",
                         model=os.environ.get("MODEL_NAME", "qwen2.5:7b"),
                         message="GCS FUSE マウントを確認してください")
            sys.exit(1)

        # プロンプト組み立て・推論
        prompt = build_comparison_prompt(
            latest_text=latest_text,
            prev_text=prev_text,
            latest_quarter_label=latest_label,
            prev_quarter_label=prev_label,
        )

        logger.info("analysis_start",
                    prompt_chars=len(prompt),
                    ticker=ticker)

        result = generate(prompt, system_prompt=SYSTEM_PROMPT)

        # 結果出力（Cloud Logging で確認）
        separator = "=" * 60
        print(f"\n{separator}")
        print(f"  決算資料比較分析レポート")
        print(f"  銘柄: {ticker}  最新決算: {latest_date_str}")
        print(f"  最新Q: {latest_label}")
        print(f"  前Q:   {prev_label}")
        print(separator)
        print(result)
        print(separator + "\n")

        logger.info("job_complete", ticker=ticker, result_chars=len(result))

    finally:
        ollama_proc.terminate()
        logger.info("ollama_server_stopped")


if __name__ == "__main__":
    main()

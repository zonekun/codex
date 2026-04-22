"""GCS PDF取得・テキスト抽出モジュール.

BQ STOCK.TDNET_DOCUMENTS_ENHANCED を検索して対象書類を特定し、
GCS からPDFをダウンロードして pdfplumber でテキスト抽出する。
BQ にレコードがない場合は GCS ファイル一覧からフォールバック検索する。
"""

from __future__ import annotations

import io
import re
from datetime import date
from typing import Optional

import pdfplumber
import structlog
from google.cloud import bigquery, storage

logger = structlog.get_logger()

GCS_BUCKET = "stock_data_1930932"
GCS_TDNET_PREFIX = "tdnet"
BQ_TABLE = "gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED"
CATEGORIES = ("決算短信", "決算説明資料", "業績修正", "業績予想")

# 1書類あたりの最大文字数（Qwen2.5 7B の 32K コンテキストに収める）
MAX_TEXT_CHARS = 4_000


def get_documents_from_bq(
    bq_client: bigquery.Client,
    ticker: str,
    date_from: date,
    date_to: date,
) -> list[dict]:
    """BQ から指定銘柄・期間・カテゴリの書類メタデータを取得する.

    Args:
        bq_client: BigQuery クライアント。
        ticker: 銘柄コード（4桁）。
        date_from: 検索開始日。
        date_to: 検索終了日。

    Returns:
        [{"submission_date": date, "doc_title": str, "file_name": str,
          "main_category": str}, ...]
        空リストの場合はフォールバックを使用する。
    """
    query = """
        SELECT
            SUBMISSION_DATE AS submission_date,
            DOC_TITLE       AS doc_title,
            FILE_NAME       AS file_name,
            MAIN_CATEGORY   AS main_category
        FROM `{table}`
        WHERE TICKER = @ticker
          AND SUBMISSION_DATE BETWEEN @date_from AND @date_to
          AND MAIN_CATEGORY IN UNNEST(@categories)
        GROUP BY 1, 2, 3, 4
        ORDER BY SUBMISSION_DATE DESC
    """.format(table=BQ_TABLE)

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("ticker", "STRING", ticker),
            bigquery.ScalarQueryParameter("date_from", "DATE", date_from.isoformat()),
            bigquery.ScalarQueryParameter("date_to", "DATE", date_to.isoformat()),
            bigquery.ArrayQueryParameter("categories", "STRING", list(CATEGORIES)),
        ]
    )

    try:
        rows = list(bq_client.query(query, job_config=job_config).result())
        logger.info("bq_search_done", ticker=ticker, count=len(rows),
                    date_from=str(date_from), date_to=str(date_to))
        return [dict(row) for row in rows]
    except Exception as e:
        logger.warning("bq_search_failed", error=str(e))
        return []


def get_documents_fallback_gcs(
    gcs_client: storage.Client,
    ticker: str,
    date_from: date,
    date_to: date,
) -> list[dict]:
    """BQ が空の場合 GCS ファイル一覧からフォールバック検索する.

    Args:
        gcs_client: Cloud Storage クライアント。
        ticker: 銘柄コード（4桁）。
        date_from: 検索開始日。
        date_to: 検索終了日。

    Returns:
        [{"submission_date": date, "doc_title": str, "file_name": str,
          "main_category": str}, ...]
    """
    prefix = f"{GCS_TDNET_PREFIX}/{ticker}/"
    bucket = gcs_client.bucket(GCS_BUCKET)

    results: list[dict] = []
    try:
        blobs = bucket.list_blobs(prefix=prefix)
        for blob in blobs:
            file_name = blob.name.split("/")[-1]
            if not file_name.endswith(".pdf"):
                continue

            # ファイル名パターン: YYYYMMDD_TICKER_会社名_カテゴリ_タイトル_DOCID.pdf
            date_match = re.match(r"^(\d{8})_", file_name)
            if not date_match:
                continue

            file_date = date(
                int(file_name[:4]),
                int(file_name[4:6]),
                int(file_name[6:8]),
            )
            if not (date_from <= file_date <= date_to):
                continue

            # カテゴリ判定（ファイル名から推定）
            category = _infer_category_from_filename(file_name)
            if category not in CATEGORIES:
                continue

            results.append({
                "submission_date": file_date,
                "doc_title": file_name,
                "file_name": file_name,
                "main_category": category,
            })

        logger.info("gcs_fallback_done", ticker=ticker, count=len(results))
    except Exception as e:
        logger.error("gcs_fallback_failed", error=str(e))

    return sorted(results, key=lambda x: x["submission_date"], reverse=True)


def _infer_category_from_filename(file_name: str) -> str:
    """ファイル名からカテゴリを推定する（フォールバック用）."""
    if "決算短信" in file_name:
        return "決算短信"
    if "説明資料" in file_name or "補足" in file_name:
        return "決算説明資料"
    if "業績修正" in file_name or "業績予想の修正" in file_name:
        return "業績修正"
    if "業績予想" in file_name:
        return "業績予想"
    return "その他"


def extract_text_from_pdf(
    gcs_client: storage.Client,
    file_name: str,
    max_chars: int = MAX_TEXT_CHARS,
) -> Optional[str]:
    """GCS から PDF をダウンロードしてテキスト抽出する.

    Args:
        gcs_client: Cloud Storage クライアント。
        file_name: GCS 上のファイル名（バケット内パス）。
        max_chars: 最大文字数（コンテキスト節約）。

    Returns:
        抽出テキスト。失敗時は None。
    """
    # file_name が GCS フルパス（tdnet/TICKER/xxx.pdf）の場合はそのまま使用
    # そうでない場合は ticker なしのため呼び出し元で正しいパスを渡す必要あり
    bucket = gcs_client.bucket(GCS_BUCKET)
    blob = bucket.blob(file_name)

    try:
        pdf_bytes = blob.download_as_bytes()
        logger.info("pdf_downloaded", file_name=file_name, size_bytes=len(pdf_bytes))
    except Exception as e:
        logger.error("pdf_download_failed", file_name=file_name, error=str(e))
        return None

    try:
        text_parts: list[str] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text_parts.append(page_text)

        full_text = "\n".join(text_parts).strip()
        if len(full_text) > max_chars:
            full_text = full_text[:max_chars] + "\n...(以下省略)"
        logger.info("pdf_extracted", file_name=file_name, chars=len(full_text))
        return full_text
    except Exception as e:
        logger.error("pdf_extract_failed", file_name=file_name, error=str(e))
        return None


def load_quarter_documents(
    bq_client: bigquery.Client,
    gcs_client: storage.Client,
    ticker: str,
    date_from: date,
    date_to: date,
) -> list[dict]:
    """BQ検索→フォールバックの順で書類を取得し、テキスト抽出まで行う.

    Args:
        bq_client: BigQuery クライアント。
        gcs_client: Cloud Storage クライアント。
        ticker: 銘柄コード（4桁）。
        date_from: 検索開始日。
        date_to: 検索終了日。

    Returns:
        [{"submission_date": date, "doc_title": str, "file_name": str,
          "main_category": str, "text": str}, ...]
    """
    docs = get_documents_from_bq(bq_client, ticker, date_from, date_to)

    if not docs:
        logger.info("bq_empty_fallback_to_gcs", ticker=ticker)
        docs = get_documents_fallback_gcs(gcs_client, ticker, date_from, date_to)

    if not docs:
        logger.warning("no_documents_found", ticker=ticker,
                       date_from=str(date_from), date_to=str(date_to))
        return []

    results = []
    for doc in docs:
        # GCS パス組み立て: BQ の FILE_NAME は tdnet/{ticker}/{filename} 形式を期待
        file_name = doc["file_name"]
        if not file_name.startswith("tdnet/"):
            file_name = f"{GCS_TDNET_PREFIX}/{ticker}/{file_name}"

        text = extract_text_from_pdf(gcs_client, file_name)
        if text:
            results.append({**doc, "text": text})

    return results

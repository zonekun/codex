"""TDnet 2024年1月分 Phase 3対象ドキュメントを BQ から JSONL でエクスポート.

出力: C:/tmp/gemma4_monthly_input.jsonl

各行: {doc_id, ticker, filer_name, doc_title, main_category, sub_categories, text_length, full_text}
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

GCP_PROJECT = "gmailpj-357912"
SA_KEY_PATH = "keys/gcp-service-account.json"
BQ_TABLE = f"{GCP_PROJECT}.STOCK.TDNET_DOCUMENTS_ENHANCED"
OUTPUT_PATH = Path("C:/tmp/gemma4_monthly_input.jsonl")

# Phase 3 対象（poc_gemma4_comparison.py と同一）
NEEDS_GEMINI_ANALYSIS: set[str] = {
    "その他（未分類）",
    "業績予想", "大型受注・契約", "受注・契約",
    "業績の重要な先行指標", "受注高/受注残高",
    "決算短信", "決算説明資料",
}


def main() -> None:
    from google.cloud import bigquery
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        SA_KEY_PATH,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    client = bigquery.Client(project=GCP_PROJECT, credentials=creds)

    sql = f"""
    WITH doc_texts AS (
        SELECT
            DOC_ID,
            TICKER,
            FILER_NAME,
            DOC_TITLE,
            MAIN_CATEGORY,
            SUB_CATEGORIES,
            TEXT_LENGTH,
            STRING_AGG(CHUNK_TEXT, '' ORDER BY CHUNK_TEXT) AS full_text
        FROM `{BQ_TABLE}`
        WHERE SUBMISSION_DATE BETWEEN '2024-01-01' AND '2024-01-31'
        GROUP BY DOC_ID, TICKER, FILER_NAME, DOC_TITLE,
                 MAIN_CATEGORY, SUB_CATEGORIES, TEXT_LENGTH
    )
    SELECT *
    FROM doc_texts
    ORDER BY TICKER, DOC_ID
    """
    print(f"[bq] querying 2024-01 from {BQ_TABLE}...", flush=True)
    rows = list(client.query(sql).result())
    print(f"[bq] fetched {len(rows)} rows", flush=True)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    n_written = 0
    n_skip = 0
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        for row in rows:
            if row.MAIN_CATEGORY not in NEEDS_GEMINI_ANALYSIS:
                n_skip += 1
                continue
            obj = {
                "doc_id": row.DOC_ID,
                "ticker": row.TICKER,
                "filer_name": row.FILER_NAME,
                "doc_title": row.DOC_TITLE,
                "main_category": row.MAIN_CATEGORY,
                "sub_categories": list(row.SUB_CATEGORIES) if row.SUB_CATEGORIES else [],
                "text_length": row.TEXT_LENGTH,
                "full_text": row.full_text or "",
            }
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            n_written += 1
    print(f"[out] written={n_written} skipped(non-phase3)={n_skip} -> {OUTPUT_PATH}", flush=True)


if __name__ == "__main__":
    main()

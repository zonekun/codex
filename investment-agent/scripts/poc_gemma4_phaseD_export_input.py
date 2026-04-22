"""Phase D 入力 JSONL 生成: TDnet 2024年 10日分 Phase 3対象ドキュメント.

対象日:
  2024-02-05, 2024-02-07,
  2024-05-09, 2024-05-16,
  2024-08-02, 2024-08-07, 2024-08-20,
  2024-11-06, 2024-11-07, 2024-11-12

出力: C:/tmp/gemma4_phaseD_input.jsonl

各行: {doc_id, ticker, filer_name, doc_title, main_category, sub_categories,
       text_length, full_text}

CHUNK_TEXT 連結は src.llm.page_aware_text.reconstruct_from_chunks を使用する。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure repository root import
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.llm.page_aware_text import reconstruct_from_chunks  # noqa: E402

GCP_PROJECT = "gmailpj-357912"
SA_KEY_PATH = "keys/gcp-service-account.json"
BQ_TABLE = f"{GCP_PROJECT}.STOCK.TDNET_DOCUMENTS_ENHANCED"
OUTPUT_PATH = Path("C:/tmp/gemma4_phaseD_input.jsonl")

TARGET_DATES = [
    "2024-02-05", "2024-02-07",
    "2024-05-09", "2024-05-16",
    "2024-08-02", "2024-08-07", "2024-08-20",
    "2024-11-06", "2024-11-07", "2024-11-12",
]

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

    dates_sql = ", ".join(f"DATE('{d}')" for d in TARGET_DATES)

    # CHUNK_TEXT には [PAGE N] マーカー埋込み（新フォーマット）なので
    # チャンク順はマーカー依存になる。チャンクを ARRAY_AGG して取得し
    # reconstruct_from_chunks で連結する。
    # ※スキーマに CHUNK_INDEX が無いため、順序は SECTION_CATEGORY と PAGE_COUNT
    # のコンボでは安定しない。元スクリプト準拠で CHUNK_TEXT 辞書順を採用。
    sql = f"""
    WITH doc_chunks AS (
        SELECT
            DOC_ID,
            ANY_VALUE(TICKER) AS TICKER,
            ANY_VALUE(FILER_NAME) AS FILER_NAME,
            ANY_VALUE(DOC_TITLE) AS DOC_TITLE,
            ANY_VALUE(MAIN_CATEGORY) AS MAIN_CATEGORY,
            ANY_VALUE(SUB_CATEGORIES) AS SUB_CATEGORIES,
            ANY_VALUE(TEXT_LENGTH) AS TEXT_LENGTH,
            ARRAY_AGG(CHUNK_TEXT ORDER BY CHUNK_TEXT) AS CHUNKS
        FROM `{BQ_TABLE}`
        WHERE DATE(SUBMISSION_DATE) IN ({dates_sql})
        GROUP BY DOC_ID
    )
    SELECT *
    FROM doc_chunks
    ORDER BY TICKER, DOC_ID
    """
    print(f"[bq] querying 10 target dates from {BQ_TABLE}...", flush=True)
    rows = list(client.query(sql).result())
    print(f"[bq] fetched {len(rows)} rows (all MAIN_CATEGORIES)", flush=True)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    n_written = 0
    n_skip = 0
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        for row in rows:
            if row.MAIN_CATEGORY not in NEEDS_GEMINI_ANALYSIS:
                n_skip += 1
                continue
            chunks = [c or "" for c in (row.CHUNKS or [])]
            try:
                full_text = reconstruct_from_chunks(chunks)
            except Exception:  # noqa: BLE001
                # フォールバック: 単純連結
                full_text = "".join(chunks)

            # SUB_CATEGORIES は REPEATED STRING (list) または STRING の可能性。
            sub_raw = row.SUB_CATEGORIES
            sub_list: list[str] = []
            if sub_raw:
                if isinstance(sub_raw, list):
                    sub_list = [str(x) for x in sub_raw]
                elif isinstance(sub_raw, str):
                    try:
                        parsed = json.loads(sub_raw)
                        if isinstance(parsed, list):
                            sub_list = [str(x) for x in parsed]
                        else:
                            sub_list = [str(parsed)]
                    except (json.JSONDecodeError, TypeError):
                        sub_list = [s.strip() for s in sub_raw.split(",") if s.strip()]

            obj = {
                "doc_id": row.DOC_ID,
                "ticker": row.TICKER,
                "filer_name": row.FILER_NAME,
                "doc_title": row.DOC_TITLE,
                "main_category": row.MAIN_CATEGORY,
                "sub_categories": sub_list,
                "text_length": row.TEXT_LENGTH,
                "full_text": full_text,
            }
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            n_written += 1
    print(f"[out] written={n_written} skipped(non-phase3)={n_skip} -> {OUTPUT_PATH}", flush=True)


if __name__ == "__main__":
    main()

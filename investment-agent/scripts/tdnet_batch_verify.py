"""
TDnet Batch ETL 検証スクリプト

バッチ版（tdnet_load_batch.py）の Gemini 分析結果を既存 BQ データと比較し、
MAIN_CATEGORY / SUB_CATEGORIES の判定結果の確からしさを検証する。

処理フロー:
  1. BQ から既存データ（FILE_NAME, MAIN_CATEGORY, SUB_CATEGORIES）を取得
  2. バッチ版と同じ Phase 1-3 を実行（テキスト抽出 + Gemini Batch）→ BQ insert はしない
  3. FILE_NAME ベースで既存 vs バッチの MAIN_CATEGORY / SUB_CATEGORIES を比較
  4. 結果を CSV 出力 + サマリー表示

Usage:
    PYTHONUTF8=1 python scripts/tdnet_batch_verify.py --from 20260301 --to 20260331
"""

import argparse
import csv
import json
import os
from datetime import date, datetime, timedelta, timezone

from google.cloud import bigquery

# バッチ版のモジュールを再利用
from tdnet_load_parallel import (
    BUCKET_NAME,
    JST,
    PROJECT_ID,
    TABLE_ID,
    BatchLogger,
    DocInfo,
    _get_bq_client,
    _get_genai_client,
    _get_storage_client,
    _load_processed_file_names,
    detect_runtime,
    phase1_scan_and_extract,
    phase2_vision_batch,
    phase3_analysis_batch,
)

RUNTIME = detect_runtime()
OUTPUT_DIR = r"C:\tmp"


def _fetch_existing_bq_data(
    date_from: str, date_to: str,
) -> dict[str, dict]:
    """BQ から既存データを取得する. FILE_NAME → {MAIN_CATEGORY, SUB_CATEGORIES} の辞書."""
    d_from_iso = f"{date_from[:4]}-{date_from[4:6]}-{date_from[6:8]}"
    d_to_iso   = f"{date_to[:4]}-{date_to[4:6]}-{date_to[6:8]}"

    query = f"""
    SELECT
        FILE_NAME,
        ANY_VALUE(MAIN_CATEGORY) AS MAIN_CATEGORY,
        ANY_VALUE(SUB_CATEGORIES) AS SUB_CATEGORIES
    FROM `{TABLE_ID}`
    WHERE SUBMISSION_DATE BETWEEN '{d_from_iso}' AND '{d_to_iso}'
    GROUP BY FILE_NAME
    """
    bq = _get_bq_client()
    rows = bq.query(query).result()

    result: dict[str, dict] = {}
    for row in rows:
        subs = list(row.SUB_CATEGORIES) if row.SUB_CATEGORIES else []
        result[row.FILE_NAME] = {
            "MAIN_CATEGORY": row.MAIN_CATEGORY,
            "SUB_CATEGORIES": sorted(subs),
        }
    return result


def _compare_results(
    existing: dict[str, dict],
    batch_docs: list[DocInfo],
) -> list[dict]:
    """既存データとバッチ結果を比較する."""
    rows: list[dict] = []

    for doc in batch_docs:
        if not doc.text:
            continue

        existing_data = existing.get(doc.blob_name)
        if existing_data is None:
            rows.append({
                "FILE_NAME": doc.blob_name,
                "TICKER": doc.sec_code,
                "DOC_TITLE": doc.doc_title,
                "STATUS": "NEW（BQ未登録）",
                "EXISTING_MAIN_CATEGORY": "",
                "BATCH_MAIN_CATEGORY": doc.main_category,
                "MAIN_MATCH": "",
                "EXISTING_SUB_CATEGORIES": "",
                "BATCH_SUB_CATEGORIES": json.dumps(sorted(doc.sub_categories), ensure_ascii=False),
                "SUB_MATCH": "",
            })
            continue

        existing_main = existing_data["MAIN_CATEGORY"]
        batch_main    = doc.main_category
        main_match    = existing_main == batch_main

        existing_subs = existing_data["SUB_CATEGORIES"]
        batch_subs    = sorted(doc.sub_categories)
        sub_match     = existing_subs == batch_subs

        if main_match and sub_match:
            status = "OK"
        elif main_match:
            status = "SUB_DIFF"
        elif sub_match:
            status = "MAIN_DIFF"
        else:
            status = "BOTH_DIFF"

        rows.append({
            "FILE_NAME": doc.blob_name,
            "TICKER": doc.sec_code,
            "DOC_TITLE": doc.doc_title,
            "STATUS": status,
            "EXISTING_MAIN_CATEGORY": existing_main,
            "BATCH_MAIN_CATEGORY": batch_main,
            "MAIN_MATCH": str(main_match),
            "EXISTING_SUB_CATEGORIES": json.dumps(existing_subs, ensure_ascii=False),
            "BATCH_SUB_CATEGORIES": json.dumps(batch_subs, ensure_ascii=False),
            "SUB_MATCH": str(sub_match),
        })

    return rows


def _print_summary(comparison_rows: list[dict]) -> None:
    """比較結果のサマリーを表示する."""
    total = len(comparison_rows)
    if total == 0:
        print("比較対象なし")
        return

    status_counts: dict[str, int] = {}
    for row in comparison_rows:
        s = row["STATUS"]
        status_counts[s] = status_counts.get(s, 0) + 1

    print("\n=== 比較結果サマリー ===")
    print(f"総件数: {total}")
    for status, count in sorted(status_counts.items()):
        pct = count / total * 100
        print(f"  {status}: {count} 件 ({pct:.1f}%)")

    # MAIN_CATEGORY 一致率（NEW 除外）
    compared = [r for r in comparison_rows if r["STATUS"] != "NEW（BQ未登録）"]
    if compared:
        main_ok = sum(1 for r in compared if r["MAIN_MATCH"] == "True")
        sub_ok  = sum(1 for r in compared if r["SUB_MATCH"] == "True")
        print(f"\nMAIN_CATEGORY 一致率: {main_ok}/{len(compared)} ({main_ok/len(compared)*100:.1f}%)")
        print(f"SUB_CATEGORIES 一致率: {sub_ok}/{len(compared)} ({sub_ok/len(compared)*100:.1f}%)")

    # 差異の詳細（最大20件）
    diffs = [r for r in comparison_rows if r["STATUS"] in ("MAIN_DIFF", "BOTH_DIFF")]
    if diffs:
        print(f"\n--- MAIN_CATEGORY 差異（{len(diffs)} 件、最大20件表示）---")
        for row in diffs[:20]:
            print(f"  {row['TICKER']} | {row['DOC_TITLE']}")
            print(f"    既存: {row['EXISTING_MAIN_CATEGORY']}")
            print(f"    バッチ: {row['BATCH_MAIN_CATEGORY']}")

    sub_diffs = [r for r in comparison_rows if r["STATUS"] in ("SUB_DIFF", "BOTH_DIFF")]
    if sub_diffs:
        print(f"\n--- SUB_CATEGORIES 差異（{len(sub_diffs)} 件、最大20件表示）---")
        for row in sub_diffs[:20]:
            print(f"  {row['TICKER']} | {row['DOC_TITLE']}")
            print(f"    既存: {row['EXISTING_SUB_CATEGORIES']}")
            print(f"    バッチ: {row['BATCH_SUB_CATEGORIES']}")


def main() -> None:
    """エントリーポイント."""
    parser = argparse.ArgumentParser(description="TDnet Batch ETL 検証")
    parser.add_argument("--from", dest="date_from", required=True)
    parser.add_argument("--to",   dest="date_to",   required=True)
    parser.add_argument("--ticker-from", dest="ticker_from", default=None)
    parser.add_argument("--ticker-to",   dest="ticker_to",   default=None)
    args = parser.parse_args()

    date_from = args.date_from
    date_to   = args.date_to

    print(f"=== TDnet Batch ETL 検証 ===")
    print(f"期間: {date_from} ～ {date_to}")
    print()

    # 1. 既存 BQ データ取得
    print("既存 BQ データを取得中...")
    existing = _fetch_existing_bq_data(date_from, date_to)
    print(f"既存データ: {len(existing)} ファイル")

    # 2. バッチ版と同じ Phase 1-3 を実行
    storage_client = _get_storage_client()
    bucket = storage_client.bucket(BUCKET_NAME)
    genai_client = _get_genai_client()

    timestamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    log_blob_name = f"log/tdnet_batch_verify_{timestamp}_log.txt"
    logger = BatchLogger(bucket, log_blob_name)

    # Phase 1: 既存データがあるファイルも含めて処理する（比較のため skip しない）
    docs = phase1_scan_and_extract(
        bucket, date_from, date_to,
        args.ticker_from, args.ticker_to,
        set(),  # 空 = 全件処理
        logger,
    )
    logger.flush_to_gcs()

    if not docs:
        print("処理対象なし → 終了")
        return

    # Phase 2: Vision OCR
    phase2_vision_batch(docs, bucket, genai_client, logger)
    logger.flush_to_gcs()

    # Phase 3: Gemini 分析
    phase3_analysis_batch(docs, bucket, genai_client, logger)
    logger.flush_to_gcs()

    # 3. 比較
    comparison_rows = _compare_results(existing, docs)

    # 4. CSV 出力
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    csv_path = os.path.join(OUTPUT_DIR, f"tdnet_batch_verify_{timestamp}.csv")
    fieldnames = [
        "FILE_NAME", "TICKER", "DOC_TITLE", "STATUS",
        "EXISTING_MAIN_CATEGORY", "BATCH_MAIN_CATEGORY", "MAIN_MATCH",
        "EXISTING_SUB_CATEGORIES", "BATCH_SUB_CATEGORIES", "SUB_MATCH",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(comparison_rows)

    print(f"\n比較結果 CSV: {csv_path}")
    _print_summary(comparison_rows)

    logger.log(f"検証完了: CSV={csv_path}")
    logger.flush_to_gcs()


if __name__ == "__main__":
    main()

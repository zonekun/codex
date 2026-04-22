"""2024-01 の TDnet PDF をページ境界保持で再抽出する PoC スクリプト.

改修後の `_extract_text_pypdf2` / `_extract_text_pdfminer`（`scripts/tdnet_load_parallel.py`）
を使い、GCS 上の 2024 年 1 月分 PDF を再抽出する。

- BQ `STOCK.TDNET_DOCUMENTS_ENHANCED` から 2024-01-01 〜 2024-01-31 の
  doc_id / FILE_NAME（GCS blob path）一覧を取得
- ThreadPoolExecutor で並列 8 で PDF を downloadas_bytes
- 新抽出関数で `[PAGE N]` マーカー付きテキストに変換
- 1 行 1 doc_id で `C:/tmp/tdnet_202401_reextracted.jsonl` に書き出す
- 出力カラム: doc_id, file_name, pages (int), full_text_with_markers (str),
  digit_ratio_per_page (list[[page_num, ratio]]), extract_method (str)

**注意**: このスクリプトは BQ の読み込みクエリのみ実行する（書き戻さない）。
Gemini Vision フォールバックは行わない（PoC では pypdf2 / pdfminer のみ）。

Usage:
    PYTHONUTF8=1 python scripts/poc_reextract_202401.py
    PYTHONUTF8=1 python scripts/poc_reextract_202401.py --limit 100
    PYTHONUTF8=1 python scripts/poc_reextract_202401.py --workers 4 --from 20240101 --to 20240115
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

# --- 改修後の抽出関数を本番スクリプトから再利用する -----------------------
# scripts ディレクトリに sys.path を通してから import する
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

# tdnet_load_parallel は import 時に少し重い処理が走るので遅延 import にする
from tdnet_load_parallel import (  # noqa: E402
    _extract_text_pypdf2,
    _extract_text_pdfminer,
    _MIN_TEXT_LEN,
)

# page_aware_text は src/llm 配下に置く。repo ルートを sys.path に入れて import
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.llm.page_aware_text import (  # noqa: E402
    digit_ratio_per_page,
    page_count,
)

from google.cloud import bigquery, storage  # noqa: E402
from google.oauth2 import service_account  # noqa: E402

logging.getLogger("PyPDF2").setLevel(logging.ERROR)

# ============================================================
# 設定
# ============================================================

PROJECT_ID   = "gmailpj-357912"
BUCKET_NAME  = "stock_data_1930932"
TABLE_ID     = f"{PROJECT_ID}.STOCK.TDNET_DOCUMENTS_ENHANCED"
OUTPUT_PATH  = Path("C:/tmp/tdnet_202401_reextracted.jsonl")
JST          = timezone(timedelta(hours=+9), "JST")
DEFAULT_WORKERS = 8

# ============================================================
# 認証クライアント（シングルトン）
# ============================================================

_client_lock = threading.RLock()
_creds = None
_storage_client: storage.Client | None = None
_bq_client: bigquery.Client | None = None


def _get_credentials():
    """ローカル実行想定。settings.google_application_credentials から読む。"""
    global _creds
    with _client_lock:
        if _creds is not None:
            return _creds
        key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if key_path and Path(key_path).exists():
            _creds = service_account.Credentials.from_service_account_file(key_path)
        else:
            _creds = None  # ADC にフォールバック
    return _creds


def _get_bq_client() -> bigquery.Client:
    global _bq_client
    with _client_lock:
        if _bq_client is None:
            creds = _get_credentials()
            _bq_client = (
                bigquery.Client(project=PROJECT_ID, credentials=creds) if creds
                else bigquery.Client(project=PROJECT_ID)
            )
    return _bq_client


def _get_storage_client() -> storage.Client:
    global _storage_client
    with _client_lock:
        if _storage_client is None:
            creds = _get_credentials()
            _storage_client = (
                storage.Client(project=PROJECT_ID, credentials=creds) if creds
                else storage.Client(project=PROJECT_ID)
            )
    return _storage_client


# ============================================================
# BQ から doc_id / FILE_NAME 一覧を取得
# ============================================================

def fetch_target_docs(date_from: str, date_to: str, limit: int | None = None) -> list[dict]:
    """BQ から期間内の (doc_id, file_name) のユニーク一覧を取得する.

    CHUNK_INDEX 毎にレコードが複製されるので DISTINCT で 1 doc_id に集約する。
    """
    d_from_iso = f"{date_from[:4]}-{date_from[4:6]}-{date_from[6:8]}"
    d_to_iso   = f"{date_to[:4]}-{date_to[4:6]}-{date_to[6:8]}"

    query = f"""
    SELECT DISTINCT DOC_ID, FILE_NAME, TICKER, DOC_TITLE
    FROM `{TABLE_ID}`
    WHERE SUBMISSION_DATE BETWEEN '{d_from_iso}' AND '{d_to_iso}'
      AND FILE_NAME IS NOT NULL
      AND FILE_NAME != ''
    """
    if limit:
        query += f"\nLIMIT {limit}"

    client = _get_bq_client()
    rows = list(client.query(query).result())
    return [
        {
            "doc_id":    row.DOC_ID,
            "file_name": row.FILE_NAME,
            "ticker":    row.TICKER,
            "doc_title": row.DOC_TITLE,
        }
        for row in rows
    ]


# ============================================================
# 1 PDF を再抽出
# ============================================================

def reextract_one(bucket, doc: dict) -> dict:
    """1 つの PDF をダウンロード → 再抽出して結果 dict を返す."""
    file_name = doc["file_name"]
    doc_id    = doc["doc_id"]

    result = {
        "doc_id":    doc_id,
        "file_name": file_name,
        "ticker":    doc.get("ticker"),
        "doc_title": doc.get("doc_title"),
        "pages":     0,
        "full_text_with_markers":  "",
        "digit_ratio_per_page":    [],
        "extract_method":          "none",
        "error":                   None,
    }

    try:
        blob = bucket.blob(file_name)
        if not blob.exists():
            result["error"] = "blob_not_found"
            return result
        pdf_bytes = blob.download_as_bytes()

        # 1) PyPDF2 を最初に試す
        text, pc = _extract_text_pypdf2(pdf_bytes)
        method = "pypdf2"
        if len(text) < _MIN_TEXT_LEN:
            # 2) フォールバック: pdfminer
            text_pm = _extract_text_pdfminer(pdf_bytes)
            if len(text_pm) >= _MIN_TEXT_LEN:
                text = text_pm
                method = "pdfminer"
                # pdfminer は page_count を返さないので、マーカー数から算出
                pc = max(pc, page_count(text))
            else:
                # どちらも短いなら、長い方を採用
                if len(text_pm) > len(text):
                    text = text_pm
                    method = "pdfminer"
                    pc = max(pc, page_count(text))

        result["pages"]                  = pc
        result["full_text_with_markers"] = text
        result["digit_ratio_per_page"]   = [
            [p, round(r, 4)] for p, r in digit_ratio_per_page(text)
        ]
        result["extract_method"]         = method if text else "none"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"

    return result


# ============================================================
# メイン
# ============================================================

def run(date_from: str, date_to: str, workers: int, limit: int | None,
        output_path: Path) -> None:
    t0 = time.time()
    print(f"[{datetime.now(JST):%Y-%m-%d %H:%M:%S}] BQ から対象 doc 一覧を取得中...")
    docs = fetch_target_docs(date_from, date_to, limit=limit)
    print(f"[{datetime.now(JST):%Y-%m-%d %H:%M:%S}] 対象 doc 数: {len(docs)}")

    if not docs:
        print("対象なし。終了します。")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # 既存ファイルがあれば上書きする（PoC なので再現性優先）
    if output_path.exists():
        print(f"既存ファイルを上書きします: {output_path}")

    bucket = _get_storage_client().bucket(BUCKET_NAME)

    ok_count = 0
    err_count = 0
    written = 0

    lock = threading.Lock()

    with open(output_path, "w", encoding="utf-8") as f_out:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            future_to_doc = {
                ex.submit(reextract_one, bucket, doc): doc for doc in docs
            }
            for i, future in enumerate(as_completed(future_to_doc), start=1):
                doc = future_to_doc[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = {
                        "doc_id": doc["doc_id"],
                        "file_name": doc["file_name"],
                        "error": f"worker_exception: {type(e).__name__}: {e}",
                        "full_text_with_markers": "",
                        "pages": 0,
                        "digit_ratio_per_page": [],
                        "extract_method": "none",
                    }
                    traceback.print_exc()

                with lock:
                    f_out.write(json.dumps(result, ensure_ascii=False) + "\n")
                    written += 1
                    if result.get("error"):
                        err_count += 1
                    else:
                        ok_count += 1

                if i % 50 == 0 or i == len(docs):
                    elapsed = time.time() - t0
                    rate = i / elapsed if elapsed > 0 else 0
                    print(
                        f"[{datetime.now(JST):%H:%M:%S}] 進捗 {i}/{len(docs)} "
                        f"(OK={ok_count}, ERR={err_count}, {rate:.1f} docs/s)"
                    )

    elapsed = time.time() - t0
    print("=== 完了 ===")
    print(f"出力ファイル   : {output_path}")
    print(f"書き込み件数   : {written}")
    print(f"  正常         : {ok_count}")
    print(f"  エラー       : {err_count}")
    print(f"  経過時間     : {elapsed:.1f} 秒")
    print(f"  平均レート   : {written / elapsed:.1f} docs/s" if elapsed else "")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="2024-01 TDnet PDF を新フォーマット（[PAGE N] マーカー付き）で再抽出する PoC"
    )
    parser.add_argument("--from", dest="date_from", default="20240101",
                        help="YYYYMMDD 形式（既定: 20240101）")
    parser.add_argument("--to",   dest="date_to",   default="20240131",
                        help="YYYYMMDD 形式（既定: 20240131）")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"並列数（既定: {DEFAULT_WORKERS}）")
    parser.add_argument("--limit", type=int, default=None,
                        help="最大件数（動作確認用）")
    parser.add_argument("--output", default=str(OUTPUT_PATH),
                        help=f"出力 JSONL パス（既定: {OUTPUT_PATH}）")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print("=== PoC: 2024-01 TDnet PDF 再抽出（ページ境界保持版）===")
    print(f"期間     : {args.date_from} ～ {args.date_to}")
    print(f"並列数   : {args.workers}")
    print(f"件数制限 : {args.limit or '無制限'}")
    print(f"出力     : {args.output}")
    print()

    run(
        date_from=args.date_from,
        date_to=args.date_to,
        workers=args.workers,
        limit=args.limit,
        output_path=Path(args.output),
    )


if __name__ == "__main__":
    main()

"""XBRL→J-Quants互換 fin_summary 生成（本番変換スクリプト）.

全銘柄 × 全期間（FY/1Q/2Q/3Q）の有報XBRLをGCSから取得し、
J-Quants fin_summary 互換のPL5項目をCSVに書き出す。

検証ロジックは `xbrl_mapping.py` と共通（TAG_CANDIDATES / ADAPTERS /
parse_xbrl / extract_pl / find_all_xbrl_files / is_valid_context を import）。

Usage:
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
        scripts/xbrl_to_jquants/convert_to_fin_summary.py
    # 再開
    PYTHONUTF8=1 ... convert_to_fin_summary.py --resume
    # 特定銘柄のみ（カンマ区切り）
    PYTHONUTF8=1 ... convert_to_fin_summary.py --tickers 6758,7203

出力:
    C:/tmp/xbrl_fin_summary/xbrl_fin_summary_YYYYMMDD.csv
    C:/tmp/xbrl_fin_summary/checkpoint_YYYYMMDD.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# 同一フォルダの xbrl_mapping から共通部品を import
sys.path.insert(0, str(Path(__file__).parent))
from xbrl_mapping import (  # type: ignore
    ADAPTERS,
    PL_FIELDS,
    TAG_CANDIDATES,
    bucket,
    extract_pl,
    find_all_xbrl_files as _find_all_xbrl_files,
    parse_xbrl,
)


def _reclassify_doc_type(blob_name: str, current: str) -> str:
    """xbrl_mapping.find_all_xbrl_files のdoc_type誤分類を補正.

    元コードは `"半期報告書" in name` を先にチェックするが、"四半期報告書"
    にも部分一致するため四半期が全部 2Q になる。ここで再分類する。
    """
    if current == "FY":
        return "FY"
    if "四半期" in blob_name:
        if "第3四半期" in blob_name:
            return "3Q"
        if "第2四半期" in blob_name:
            return "2Q"
        if "第1四半期" in blob_name:
            return "1Q"
    if "半期報告書" in blob_name:
        return "2Q"
    return current


def find_all_xbrl_files(ticker: str) -> list[dict]:
    """find_all_xbrl_files をラップしdoc_typeを補正."""
    files = _find_all_xbrl_files(ticker)
    for f in files:
        f["doc_type"] = _reclassify_doc_type(f["blob_name"], f["doc_type"])
    return files

OUT_DIR = Path("C:/tmp/xbrl_fin_summary")
CHECKPOINT_INTERVAL = 50

OUTPUT_COLUMNS = [
    "LOCAL_CODE",
    "CURRENT_PERIOD_END_DATE",
    "DOC_TYPE",
    "NET_SALES",
    "OPERATING_PROFIT",
    "ORDINARY_PROFIT",
    "PROFIT",
    "EARNINGS_PER_SHARE",
    "EXTRACTED_TAG_NET_SALES",
    "EXTRACTED_TAG_OPERATING_PROFIT",
    "EXTRACTED_TAG_ORDINARY_PROFIT",
    "EXTRACTED_TAG_PROFIT",
    "EXTRACTED_TAG_EARNINGS_PER_SHARE",
    "CONTEXT_NET_SALES",
    "CONTEXT_OPERATING_PROFIT",
    "CONTEXT_ORDINARY_PROFIT",
    "CONTEXT_PROFIT",
    "CONTEXT_EARNINGS_PER_SHARE",
    "SOURCE_BLOB",
]


def list_all_tickers_from_gcs() -> list[str]:
    """GCSから証券コードを列挙する（edinet/ 配下のディレクトリ名）."""
    prefixes = set()
    iterator = bucket.list_blobs(prefix="edinet/", delimiter="/")
    # prefixesはiteratorを消費した後にセットされる
    for _ in iterator:
        pass
    for p in iterator.prefixes:
        # "edinet/1234/" -> "1234"
        code = p.rstrip("/").split("/")[-1]
        if code.isdigit() or (len(code) == 4 and code[:3].isdigit()):
            prefixes.add(code)
    return sorted(prefixes)


def row_from_extract(
    ticker: str,
    xbrl_info: dict,
    pl: dict,
) -> dict:
    """extract_pl()の結果からCSV1行を組み立てる."""
    row: dict = {
        "LOCAL_CODE": ticker,
        "CURRENT_PERIOD_END_DATE": xbrl_info["period_end"],
        "DOC_TYPE": xbrl_info["doc_type"],
        "SOURCE_BLOB": xbrl_info["blob_name"],
    }
    for field in PL_FIELDS:
        data = pl.get(field)
        row[field] = data["value"] if data else ""
        row[f"EXTRACTED_TAG_{field}"] = data["tag"] if data else ""
        row[f"CONTEXT_{field}"] = data["context"] if data else ""
    return row


def save_checkpoint(path: Path, last_idx: int, written_rows: int) -> None:
    """チェックポイントを保存."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"last_index": last_idx, "written_rows": written_rows}, f)


def load_checkpoint(path: Path) -> dict | None:
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true", help="チェックポイントから再開")
    parser.add_argument("--tickers", type=str, default="", help="カンマ区切り銘柄限定")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    out_csv = OUT_DIR / f"xbrl_fin_summary_{today}.csv"
    checkpoint_path = OUT_DIR / f"checkpoint_{today}.json"

    # 銘柄リスト
    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    else:
        print("GCSから銘柄一覧を取得中...", flush=True)
        tickers = list_all_tickers_from_gcs()
        print(f"  {len(tickers)}銘柄", flush=True)

    # 再開モード
    start_idx = 0
    written_rows = 0
    if args.resume:
        cp = load_checkpoint(checkpoint_path)
        if cp:
            start_idx = cp["last_index"] + 1
            written_rows = cp["written_rows"]
            print(f"[resume] idx={start_idx}から再開 ({written_rows}行書込済み)", flush=True)
        else:
            print("[resume] checkpoint なし。最初から実行", flush=True)

    # CSV open（resume時は追記）
    mode = "a" if (args.resume and out_csv.exists()) else "w"
    f = open(out_csv, mode, encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
    if mode == "w":
        writer.writeheader()

    processed = 0
    skipped = 0
    matched_files = 0

    try:
        for i, ticker in enumerate(tickers):
            if i < start_idx:
                continue

            if i % CHECKPOINT_INTERVAL == 0 and i > start_idx:
                print(
                    f"  進捗: {i}/{len(tickers)} processed={processed} "
                    f"skip={skipped} files={matched_files} rows={written_rows}",
                    flush=True,
                )
                f.flush()
                save_checkpoint(checkpoint_path, i - 1, written_rows)

            xbrl_files = find_all_xbrl_files(ticker)
            if not xbrl_files:
                skipped += 1
                continue
            processed += 1

            for xbrl_info in xbrl_files:
                blob_name = xbrl_info["blob_name"]
                try:
                    xbrl_bytes = bucket.blob(blob_name).download_as_bytes()
                    elements = parse_xbrl(xbrl_bytes)
                except Exception as e:
                    print(f"  [ERR] {ticker} {blob_name}: {e}", flush=True)
                    continue

                if not elements:
                    continue

                matched_files += 1
                pl = extract_pl(elements, ticker)
                row = row_from_extract(ticker, xbrl_info, pl)
                writer.writerow(row)
                written_rows += 1

        # 最終チェックポイント
        save_checkpoint(checkpoint_path, len(tickers) - 1, written_rows)
    finally:
        f.close()

    print(
        f"\n完了: processed={processed} skipped={skipped} files={matched_files} "
        f"rows={written_rows}",
        flush=True,
    )
    print(f"出力: {out_csv}", flush=True)

    # 正常完了ならcheckpoint削除
    if checkpoint_path.exists():
        checkpoint_path.unlink()
        print(f"[cleanup] {checkpoint_path} 削除", flush=True)


if __name__ == "__main__":
    main()

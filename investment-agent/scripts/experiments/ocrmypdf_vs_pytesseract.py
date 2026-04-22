#!/usr/bin/env python3
"""OCRmyPDF vs pytesseract 直接 の比較 PoC (P2-1, 062 MD §1).

画像 PDF 銘柄 (3034 クオールHD 等) に対して以下 2 経路を比較する:

  A. pytesseract 経路 (現行): PyMuPDF で画像化 → PIL Image → pytesseract.image_to_string
  B. OCRmyPDF 経路      : ocrmypdf で検索可能 PDF 化 → pdfplumber.extract_tables/extract_text

指標:
    (1) OCR 所要時間 [sec]
    (2) 抽出テキスト長 [chars]
    (3) pdfplumber.extract_tables() 成功行数 (B のみ、A は構造情報を失うため 0 想定)

採否判定（見送り条件）:
    - 速度: OCRmyPDF が pytesseract 直接より 2 倍以上遅い → 不採用
    - 精度: extract_tables() が OCRmyPDF 出力でも表抽出できない → 不採用
    - 環境依存: Cloud Run 用 Dockerfile に tesseract + ghostscript 追記必要

本 PoC は **PoC フェーズ用の単発スクリプト**。ユーザーが手動実行して
PoC 結果を plan MD `20260421_093823_monthly_pdf_strategy_integration.md`
の「PoC 結果」セクションに追記する想定。

使い方:
    PYTHONUTF8=1 uv run python scripts/experiments/ocrmypdf_vs_pytesseract.py \\
        --tickers 3034 --pages 3

    # 複数銘柄
    PYTHONUTF8=1 uv run python scripts/experiments/ocrmypdf_vs_pytesseract.py \\
        --tickers 3034 7872 9986

依存 (PoC 時のみユーザー判断で追加):
    - ocrmypdf: `uv add ocrmypdf` （本 PoC スクリプトから add はしない）
    - Tesseract 本体 + tesseract-ocr-jpn 言語パック (Windows: https://github.com/UB-Mannheim/tesseract/wiki)
    - Ghostscript (Windows: https://www.ghostscript.com/download/gsdnld.html)

References:
    docs/knowledges/tools/062_pdf_processing_strategy.md §1
    docs/plans/20260421_093823_monthly_pdf_strategy_integration.md P2-1
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _check_external_tools() -> None:
    """Tesseract / Ghostscript の存在確認。未導入ならエラー終了."""
    missing: list[str] = []
    if not shutil.which("tesseract"):
        missing.append("tesseract (Tesseract OCR)")
    # Windows は gswin64c.exe、Linux/Mac は gs
    gs_name = "gswin64c" if os.name == "nt" else "gs"
    if not shutil.which(gs_name) and not shutil.which("gs"):
        missing.append(f"{gs_name} (Ghostscript)")
    if missing:
        logger.error("必要な外部ツールが未導入:")
        for m in missing:
            logger.error("  - %s", m)
        logger.error("Windows: UB-Mannheim Tesseract + Ghostscript インストール後、PATH 通す必要あり")
        sys.exit(2)


def _check_python_deps() -> None:
    """Python 依存 (ocrmypdf / pytesseract / pdfplumber / fitz / PIL) の確認."""
    missing: list[str] = []
    for mod in ("ocrmypdf", "pytesseract", "pdfplumber", "fitz", "PIL"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    if missing:
        logger.error("必要な Python パッケージが未導入: %s", missing)
        logger.error("対処: uv add %s （※ ocrmypdf はユーザー判断で追加）", " ".join(missing))
        sys.exit(3)


def _fetch_latest_pdf(ticker: str) -> Optional[bytes]:
    """GCS から対象 ticker の最新 PDF を 1 件取得する.

    Returns:
        PDF bytes。該当なしなら None。
    """
    try:
        from google.cloud import storage  # type: ignore
    except ImportError:
        logger.error("google-cloud-storage が未導入。`uv add google-cloud-storage`")
        return None

    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")
    prefix = f"monthly/docs/{ticker}/"
    blobs = [b for b in bucket.list_blobs(prefix=prefix) if b.name.endswith(".pdf")]
    if not blobs:
        logger.warning("[%s] GCS に PDF 無し: %s", ticker, prefix)
        return None
    # 名前降順で最新を取得 (ファイル名に日付が含まれる前提)
    blobs.sort(key=lambda b: b.name, reverse=True)
    latest = blobs[0]
    logger.info("[%s] 最新 PDF: %s (size=%d bytes)", ticker, latest.name, latest.size or 0)
    return latest.download_as_bytes()


def run_pytesseract(pdf_bytes: bytes, max_pages: int = 0) -> tuple[float, str]:
    """経路 A: pytesseract 直接 OCR。

    Args:
        pdf_bytes: 対象 PDF バイト列。
        max_pages: 0 なら全ページ、それ以外は先頭 N ページのみ。

    Returns:
        (所要時間 [sec], 抽出テキスト)。
    """
    import fitz  # PyMuPDF
    import pytesseract
    from PIL import Image

    t0 = time.time()
    lines: list[str] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        pages = list(doc)
        if max_pages > 0:
            pages = pages[:max_pages]
        for page in pages:
            mat = fitz.Matrix(150 / 72, 150 / 72)
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            ocr_text = pytesseract.image_to_string(img, lang="jpn+eng")
            if ocr_text.strip():
                lines.append(ocr_text)
    elapsed = time.time() - t0
    return elapsed, "\n".join(lines)


def run_ocrmypdf(pdf_bytes: bytes, max_pages: int = 0) -> tuple[float, str, int]:
    """経路 B: OCRmyPDF で検索可能 PDF 化 → pdfplumber で抽出。

    Args:
        pdf_bytes: 対象 PDF バイト列。
        max_pages: 0 なら全ページ、それ以外は先頭 N ページのみ。

    Returns:
        (所要時間 [sec], 抽出テキスト, extract_tables 成功行数)。
    """
    import ocrmypdf  # type: ignore
    import pdfplumber

    t0 = time.time()
    table_rows = 0
    text = ""

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src.pdf"
        dst = Path(td) / "ocr.pdf"

        src_bytes = pdf_bytes
        if max_pages > 0:
            # 先頭 N ページだけ切り出して速度比較を公平に
            import fitz  # PyMuPDF
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                new = fitz.open()
                new.insert_pdf(doc, from_page=0, to_page=min(max_pages - 1, doc.page_count - 1))
                src_bytes = new.tobytes()
        src.write_bytes(src_bytes)

        # OCRmyPDF: force_ocr=True で既存テキスト層も上書き（PoC なので比較条件を揃える）
        ocrmypdf.ocr(
            str(src), str(dst),
            language="jpn+eng",
            force_ocr=True,
            optimize=0,   # 速度最優先
            progress_bar=False,
        )

        with pdfplumber.open(str(dst)) as pdf:
            texts: list[str] = []
            for page in pdf.pages:
                tables = page.extract_tables()
                if tables:
                    for tbl in tables:
                        table_rows += sum(1 for row in tbl if row and any(c for c in row if c))
                t = page.extract_text() or ""
                if t.strip():
                    texts.append(t)
            text = "\n".join(texts)

    elapsed = time.time() - t0
    return elapsed, text, table_rows


def compare_one(ticker: str, max_pages: int) -> dict:
    """1 銘柄で A / B を比較して結果を返す.

    Returns:
        {"ticker": ..., "a": {...}, "b": {...}}
    """
    logger.info("=" * 60)
    logger.info("[%s] 比較開始 (max_pages=%s)", ticker, max_pages or "all")
    pdf_bytes = _fetch_latest_pdf(ticker)
    if not pdf_bytes:
        return {"ticker": ticker, "error": "no_pdf"}

    # A. pytesseract 直接
    try:
        a_sec, a_text = run_pytesseract(pdf_bytes, max_pages=max_pages)
        a_result = {"elapsed_sec": round(a_sec, 2), "text_len": len(a_text), "table_rows": 0}
        logger.info("[%s][A pytesseract] %.2fs  text_len=%d", ticker, a_sec, len(a_text))
    except Exception as e:
        a_result = {"error": str(e)}
        logger.exception("[%s][A pytesseract] 例外", ticker)

    # B. OCRmyPDF → pdfplumber
    try:
        b_sec, b_text, b_rows = run_ocrmypdf(pdf_bytes, max_pages=max_pages)
        b_result = {"elapsed_sec": round(b_sec, 2), "text_len": len(b_text), "table_rows": b_rows}
        logger.info(
            "[%s][B ocrmypdf]   %.2fs  text_len=%d  table_rows=%d",
            ticker, b_sec, len(b_text), b_rows,
        )
    except Exception as e:
        b_result = {"error": str(e)}
        logger.exception("[%s][B ocrmypdf] 例外", ticker)

    return {"ticker": ticker, "a": a_result, "b": b_result}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OCRmyPDF vs pytesseract PoC (P2-1)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--tickers", nargs="+", required=True,
        help="比較対象 ticker (例: --tickers 3034 7872 9986)",
    )
    parser.add_argument(
        "--pages", type=int, default=3,
        help="PDF 先頭 N ページのみ処理 (公平な比較用、0 なら全ページ)",
    )
    args = parser.parse_args()

    _check_external_tools()
    _check_python_deps()

    results: list[dict] = []
    for t in args.tickers:
        results.append(compare_one(t, args.pages))

    # サマリ
    logger.info("=" * 60)
    logger.info("=== サマリ ===")
    header = f"{'ticker':<8}  {'A_sec':>7}  {'A_len':>8}  {'B_sec':>7}  {'B_len':>8}  {'B_rows':>6}  {'B/A':>5}"
    logger.info(header)
    for r in results:
        if "error" in r:
            logger.info("%-8s  %s", r["ticker"], r["error"])
            continue
        a = r["a"]; b = r["b"]
        if "error" in a or "error" in b:
            logger.info("%-8s  error (a=%s b=%s)", r["ticker"], a.get("error"), b.get("error"))
            continue
        ratio = (b["elapsed_sec"] / a["elapsed_sec"]) if a["elapsed_sec"] > 0 else float("inf")
        logger.info(
            "%-8s  %7.2f  %8d  %7.2f  %8d  %6d  %5.2f",
            r["ticker"],
            a["elapsed_sec"], a["text_len"],
            b["elapsed_sec"], b["text_len"], b["table_rows"],
            ratio,
        )

    logger.info("=" * 60)
    logger.info("見送り判定基準:")
    logger.info("  - B/A >= 2.0 で速度ペナルティ過大")
    logger.info("  - B_rows == 0 で表抽出メリット無し (pytesseract と同値)")
    logger.info("結果は docs/plans/20260421_093823_monthly_pdf_strategy_integration.md の PoC 結果セクションに追記")
    return 0


if __name__ == "__main__":
    sys.exit(main())

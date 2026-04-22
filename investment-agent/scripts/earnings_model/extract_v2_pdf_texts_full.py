"""v2_analysis_20260416 配下の 120 PDF から全ページテキストを抽出（最大15,000文字）。

前スクリプトは先頭5ページで打ち切っていたが、特別損失・特別利益の記述が
PL部分(後ろのページ)に出るケースを捕捉できなかったため再抽出する。

使い方:
    PYTHONUTF8=1 python scripts/earnings_model/extract_v2_pdf_texts_full.py

出力:
    C:/tmp/v2_pdf_texts_full.json
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

import pdfplumber

BASE = Path("C:/Users/zonekun/Dropbox/stock/temp/gemma4_4_31b_tpu_poc/v2_analysis_20260416")
OUT_PATH = Path("C:/tmp/v2_pdf_texts_full.json")

MAX_PAGES = 30
MAX_CHARS = 25000


def parse_filename(path: Path) -> dict[str, str]:
    name = path.stem
    parts = name.split("_")
    doc_id = parts[-1] if parts[-1].isdigit() else ""
    try:
        category = parts[0]
        subset_jp = parts[1]
        date = parts[4]
        ticker = parts[5]
        short_name = parts[6]
        doc_type = parts[7]
        title = "_".join(parts[8:-1])
    except IndexError:
        category = subset_jp = date = ticker = short_name = doc_type = title = ""
    return {
        "doc_id": doc_id,
        "ticker": ticker,
        "short_name": short_name,
        "doc_type": doc_type,
        "title": title,
        "date": date,
    }


def extract_text(pdf_path: Path, max_pages: int = MAX_PAGES, max_chars: int = MAX_CHARS) -> tuple[str, int, str | None]:
    try:
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            texts: list[str] = []
            for i, page in enumerate(pdf.pages):
                if i >= max_pages:
                    break
                try:
                    t = page.extract_text() or ""
                except Exception as e:
                    t = f"[page {i+1} extract error: {e}]"
                texts.append(t)
            full = "\n\n---PAGE---\n\n".join(texts)
            full = re.sub(r"[ \t]+", " ", full)
            full = re.sub(r"\n{3,}", "\n\n", full)
            if len(full) > max_chars:
                full = full[:max_chars]
            return full, total_pages, None
    except Exception as e:
        return "", 0, f"{type(e).__name__}: {e}"


def main() -> None:
    samples: list[dict] = []
    categories = ["配当", "特別損失", "特別利益"]
    subsets = ["detected", "missed"]

    for cat in categories:
        for subset in subsets:
            pattern = str(BASE / cat / subset / "*.pdf")
            pdfs = sorted(glob.glob(pattern))
            for pdf_path_str in pdfs:
                pdf_path = Path(pdf_path_str)
                meta = parse_filename(pdf_path)
                text, page_count, err = extract_text(pdf_path)
                samples.append(
                    {
                        "category": cat,
                        "subset": subset,
                        "pdf_path": str(pdf_path).replace("\\", "/"),
                        "file_name": pdf_path.name,
                        "doc_id": meta["doc_id"],
                        "ticker": meta["ticker"],
                        "short_name": meta["short_name"],
                        "doc_type": meta["doc_type"],
                        "title": meta["title"],
                        "date": meta["date"],
                        "page_count": page_count,
                        "text": text,
                        "text_len": len(text),
                        "error": err,
                    }
                )
                print(f"  {meta['ticker']:>5} {meta['short_name'][:15]:<15} {cat}/{subset} text_len={len(text)} pages={page_count}", file=sys.stderr)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(samples)} samples to {OUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()

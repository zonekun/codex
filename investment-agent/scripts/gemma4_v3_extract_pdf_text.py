"""Phase B: 4カテゴリ PDF からテキスト抽出し、判定用 JSON を作る.

各PDFを pdfplumber で先頭30ページ・最大25,000文字まで抽出。
出力 JSON: `C:/tmp/gemma4_v3_4cat_pdf_text.json`
スキーマ:
    [
      {
        "category": "中期経営計画",
        "pattern": "detected",
        "pdf_path": "<abs path>",
        "file_name": "<filename>.pdf",
        "doc_id": "<id>",
        "ticker": "...",
        "short_name": "...",
        "doc_title": "...",
        "gemma_main": "...",
        "gemma_subs": [...],
        "gemini_main": "...",
        "gemini_subs": [...],
        "text_head": "<extracted text>",
        "num_pages": N,
        "extract_status": "OK"|"ERR:..."
      }, ...
    ]

Usage:
    PYTHONUTF8=1 python scripts/gemma4_v3_extract_pdf_text.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DROPBOX_BASE = Path(
    "C:/Users/zonekun/Dropbox/stock/temp/gemma4_4_31b_tpu_poc/tuning_4cat_20260416"
)
JSONL_PATH = Path("C:/tmp/gemma4_tpu_monthly_results.jsonl")
OUT_JSON = Path("C:/tmp/gemma4_v3_4cat_pdf_text.json")

MAX_PAGES = 30
MAX_CHARS = 25000

CATEGORIES = ["中期経営計画", "業績予想", "業績の重要な先行指標", "業績修正"]
PATTERNS = ("detected", "missed", "over_detected")


def load_jsonl_map() -> dict[str, dict]:
    m: dict[str, dict] = {}
    with open(JSONL_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            m[r["doc_id"]] = r
    return m


def extract_text(pdf_path: Path) -> tuple[str, int, str]:
    """Return (text, num_pages, status)."""
    try:
        text_parts: list[str] = []
        total_chars = 0
        with pdfplumber.open(pdf_path) as pdf:
            num_pages = len(pdf.pages)
            for i, page in enumerate(pdf.pages[:MAX_PAGES]):
                t = page.extract_text() or ""
                if not t:
                    continue
                remaining = MAX_CHARS - total_chars
                if remaining <= 0:
                    break
                if len(t) > remaining:
                    t = t[:remaining]
                text_parts.append(t)
                total_chars += len(t)
                if total_chars >= MAX_CHARS:
                    break
        return "\n\n".join(text_parts), num_pages, "OK"
    except Exception as e:
        return "", 0, f"ERR:{type(e).__name__}:{e}"


DOC_ID_RE = re.compile(r"_(\d{16,})\.pdf$")


def parse_doc_id_from_name(name: str) -> str | None:
    m = DOC_ID_RE.search(name)
    return m.group(1) if m else None


def main() -> None:
    rec_by_id = load_jsonl_map()
    print(f"[JSONL] loaded {len(rec_by_id)} records", flush=True)

    results: list[dict] = []
    for cat in CATEGORIES:
        for pat in PATTERNS:
            pat_dir = DROPBOX_BASE / cat / pat
            if not pat_dir.exists():
                continue
            pdfs = sorted(pat_dir.glob("*.pdf"))
            print(f"[DIR] {cat}/{pat}: {len(pdfs)} PDFs", flush=True)
            for pdf_path in pdfs:
                doc_id = parse_doc_id_from_name(pdf_path.name)
                rec = rec_by_id.get(doc_id or "", {})
                text, num_pages, status = extract_text(pdf_path)
                result = {
                    "category": cat,
                    "pattern": pat,
                    "pdf_path": str(pdf_path),
                    "file_name": pdf_path.name,
                    "doc_id": doc_id,
                    "ticker": rec.get("ticker"),
                    "doc_title": rec.get("doc_title"),
                    "gemma_main": rec.get("main_cat_pred"),
                    "gemma_subs": rec.get("sub_cat_pred") or [],
                    "gemini_main": rec.get("gemini_main_category"),
                    "gemini_subs": rec.get("gemini_sub_categories") or [],
                    "text_head": text,
                    "num_pages": num_pages,
                    "extract_status": status,
                }
                results.append(result)
                print(
                    f"  [{status[:20]}] pages={num_pages} chars={len(text)} {pdf_path.name[:80]}",
                    flush=True,
                )

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False)
    print(f"[DONE] wrote {len(results)} records to {OUT_JSON}", flush=True)


if __name__ == "__main__":
    main()

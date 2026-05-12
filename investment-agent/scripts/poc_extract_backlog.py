"""POC: 決算参考資料PDFから繰越工事高を抽出する2段階パイプライン.

Stage 1: pdfplumber でページテキスト抽出 → キーワードで該当ページ特定
Stage 2: 該当ページをPNG化 → Gemini Vision で構造化抽出
"""

import json
import sys
from pathlib import Path

import pdfplumber
import pymupdf
from google import genai
from google.genai import types

GEMINI_MODEL = "gemini-3-flash-preview"
KEYWORDS = ["繰越", "手持"]


def find_relevant_pages(pdf_path: str) -> list[int]:
    """Stage 1: キーワードを含むページ番号(0-indexed)を返す."""
    relevant = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if any(kw in text for kw in KEYWORDS):
                relevant.append(i)
    return relevant


def render_page_to_png(pdf_path: str, page_num: int, dpi: int = 200) -> bytes:
    """PyMuPDFでページをPNG bytesに変換."""
    doc = pymupdf.open(pdf_path)
    page = doc[page_num]
    mat = pymupdf.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    png_bytes = pix.tobytes("png")
    doc.close()
    return png_bytes


def extract_with_gemini(png_list: list[bytes], api_key: str) -> dict:
    """Stage 2: PNG画像をGemini Visionに投入して繰越工事高を構造化抽出."""
    client = genai.Client(api_key=api_key)

    prompt = """この決算参考資料のページから「繰越工事高」（手持工事高）の数値を抽出してください。

以下のJSON形式で返してください:
{
  "company_name": "企業名",
  "unit": "百万円 or 億円",
  "periods": [
    {
      "period": "2025年3月期",
      "total": 数値,
      "breakdown": {
        "一般ビル": 数値,
        "産業施設": 数値,
        "電気工事": 数値
      }
    }
  ]
}

- 数値はカンマなしの整数で返してください
- グラフから読み取れる数値をすべて含めてください
- 見つからない場合は null としてください"""

    contents = []
    for png in png_list:
        contents.append(types.Part.from_bytes(data=png, mime_type="image/png"))
    contents.append(types.Part.from_text(text=prompt))

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
        ),
    )
    return json.loads(response.text)


def main() -> None:
    """メイン処理."""
    if len(sys.argv) < 2:
        print("Usage: python poc_extract_backlog.py <pdf_path>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    if not Path(pdf_path).exists():
        print(f"File not found: {pdf_path}")
        sys.exit(1)

    # .env から API キー取得
    from dotenv import load_dotenv
    import os
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    api_key = os.environ["GEMINI_API_KEY"]

    # Stage 1: ページ特定
    pages = find_relevant_pages(pdf_path)
    print(f"[Stage 1] キーワード該当ページ: {[p + 1 for p in pages]} (1-indexed)")

    if not pages:
        print("繰越工事高に関連するページが見つかりませんでした。")
        sys.exit(0)

    # Stage 2: PNG化 → Gemini Vision
    png_list = []
    for p in pages:
        png = render_page_to_png(pdf_path, p)
        print(f"  Page {p + 1}: {len(png):,} bytes PNG")
        png_list.append(png)

    print(f"[Stage 2] Gemini Vision に {len(png_list)} ページ送信中...")
    result = extract_with_gemini(png_list, api_key)
    print("\n=== 抽出結果 ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

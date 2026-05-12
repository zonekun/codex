# -*- coding: utf-8 -*-
"""受注残高 structure.json 生成スクリプト.

サンプルPDFをGemini Visionで解析し、各社のstructure.jsonを生成する。
Stage 1: pdfplumberでキーワードページ特定（コスト0）
Stage 2: pymupdfでPNG化 → Gemini Visionで構造解析

Usage:
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/generate_backlog_structure.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import fitz  # pymupdf
import pdfplumber
from google import genai

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = Path("C:/tmp/order_backlog_survey/cat_sample")
OUTPUT_DIR = Path("C:/tmp/order_backlog_survey/structure")
ERROR_LOG = PROJECT_ROOT / "docs" / "plans" / "088_error_log.md"

KEYWORDS = ["繰越", "手持", "受注残"]
PRIMARY_MODEL = "gemini-3-flash-preview"
FALLBACK_MODEL = "gemini-3.1-pro"

JST = ZoneInfo("Asia/Tokyo")

STRUCTURE_PROMPT = """\
あなたは日本の上場企業の決算資料を分析するアナリストです。

以下のPDFページ画像は、ある企業の決算短信・決算説明資料の中から、受注残高・繰越工事高に関連するページを抽出したものです。

このPDFを分析して、以下のJSON形式で「この企業から抽出すべき受注残高関連メトリクス」を定義してください。

## 出力JSON形式

```json
{
  "ticker": "銘柄コード",
  "company_name": "企業名",
  "data_available": true,
  "metrics": [
    {
      "name": "メトリクス名（日本語）",
      "type": "order_intake|completed|backlog|other",
      "description": "何のデータか簡潔に",
      "unit": "百万円|千円|億円|%|件|戸|棟|台|基|本|隻|機|組|口|室|人|区画|棟・区画|月|MW|kW|t|m²|ha|馬力|万総トン|千米ドル|億ドル|USDmil|万元|兆円",
      "is_total": true,
      "parent_metric": null
    }
  ],
  "breakdown_dimensions": [
    {
      "dimension_name": "ブレークダウンの軸名（例: 工事種別、セグメント）",
      "items": ["項目1", "項目2", "..."]
    }
  ],
  "presentation_format": "table|chart|text|mixed",
  "notes": "特記事項（テーブル構造の特徴、注意点等）"
}
```

## metricsのtypeの定義
- order_intake: 受注高（期中の新規受注額）
- completed: 完成工事高・売上高・売上収益（期中の完成額・売上実績）
- backlog: 繰越工事高・受注残高・手持工事高（期末時点の未完成残高）
- other: 上記に分類できないもの

## ルール
1. 合計値とブレークダウン値は別メトリクスとして定義。合計値はis_total=true
2. ブレークダウン項目はbreakdown_dimensionsに軸単位でまとめる
3. **data_available判定（重要）**: data_available=trueにするには、PDFに数値付きの受注関連データ（受注高・受注残高・繰越工事高・手持工事高の具体的金額または数量）が存在しなければならない。以下の場合はdata_available=falseとすること:
   - 「受注は好調」「受注残高が増加」等の定性記述のみで、具体的な数値テーブルや金額がない場合
   - 売上高・売上収益のみが記載されており、受注高・受注残高の数値がない場合
   - 受注関連のキーワードが文中に出現するが、それに付随する数値データがない場合
4. 数値が文章中にのみ記載されている場合（例: 「受注高は1,975億円」）はdata_available=true、presentation_format="text"
5. グラフのみの場合はpresentation_format="chart"
6. テーブルとグラフが混在する場合はpresentation_format="mixed"
7. **受注関連指標と同一ページ・同一テーブルに記載された売上高・完成工事高等のcompleted（実績）メトリクスも必ず含めること**。受注高と売上高（完成工事高）は通常ペアで開示されるため、片方だけの抽出は不完全である
8. **unitの値は以下の許容リストから選択すること（bare「円」「JPY」は禁止）**: 百万円, 千円, 億円, %, 件, 戸, 棟, 台, 基, 本, 隻, 機, 組, 口, 室, 人, 区画, 棟・区画, 月, MW, kW, t, m², ha, 馬力, 万総トン, 千米ドル, 億ドル, USDmil, 万元, 兆円
9. **同名のメトリクスが複数存在する場合（例: 合計の「売上高」とセグメント別の「売上高」）、セグメント名・事業部門名等を接頭辞として付与し一意な名称にすること**（例: 「建築事業売上高」「土木事業売上高」）
10. 非金額単位（件・戸・棟・機・組等）のメトリクスもPDFに数値として記載されていれば含めること。受注棟数・引渡戸数・受注件数等は正当なメトリクスである
"""

# response_schema で unit を列挙型に制約する
VALID_UNITS: list[str] = [
    "百万円", "千円", "億円", "%", "件", "戸", "棟", "台", "基", "本",
    "隻", "機", "組", "口", "室", "人", "区画", "棟・区画", "月",
    "MW", "kW", "t", "m²", "ha", "馬力", "万総トン",
    "千米ドル", "億ドル", "USDmil", "万元", "兆円",
]

RESPONSE_SCHEMA = genai.types.Schema(
    type="OBJECT",
    properties={
        "ticker": genai.types.Schema(type="STRING"),
        "company_name": genai.types.Schema(type="STRING"),
        "data_available": genai.types.Schema(type="BOOLEAN"),
        "metrics": genai.types.Schema(
            type="ARRAY",
            items=genai.types.Schema(
                type="OBJECT",
                properties={
                    "name": genai.types.Schema(type="STRING"),
                    "type": genai.types.Schema(
                        type="STRING",
                        enum=["order_intake", "completed", "backlog", "other"],
                    ),
                    "description": genai.types.Schema(type="STRING"),
                    "unit": genai.types.Schema(
                        type="STRING",
                        enum=VALID_UNITS,
                    ),
                    "is_total": genai.types.Schema(type="BOOLEAN"),
                    "parent_metric": genai.types.Schema(
                        type="STRING", nullable=True
                    ),
                },
                required=["name", "type", "description", "unit", "is_total"],
            ),
        ),
        "breakdown_dimensions": genai.types.Schema(
            type="ARRAY",
            items=genai.types.Schema(
                type="OBJECT",
                properties={
                    "dimension_name": genai.types.Schema(type="STRING"),
                    "items": genai.types.Schema(
                        type="ARRAY",
                        items=genai.types.Schema(type="STRING"),
                    ),
                },
                required=["dimension_name", "items"],
            ),
        ),
        "presentation_format": genai.types.Schema(
            type="STRING",
            enum=["table", "chart", "text", "mixed"],
        ),
        "notes": genai.types.Schema(type="STRING"),
    },
    required=[
        "ticker", "company_name", "data_available", "metrics",
        "breakdown_dimensions", "presentation_format", "notes",
    ],
)


def find_relevant_pages(pdf_path: Path) -> list[int]:
    """pdfplumberでキーワードを含むページ番号を特定する."""
    pages: list[int] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if any(kw in text for kw in KEYWORDS):
                pages.append(i)
    return pages


def render_pages_to_png(pdf_path: Path, page_indices: list[int], dpi: int = 200) -> list[bytes]:
    """pymupdfで指定ページをPNG化する."""
    images: list[bytes] = []
    doc = fitz.open(str(pdf_path))
    for idx in page_indices:
        page = doc[idx]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        images.append(pix.tobytes("png"))
    doc.close()
    return images


def analyze_with_gemini(
    client: genai.Client,
    images: list[bytes],
    ticker: str,
    company: str,
    model: str,
) -> dict:
    """Gemini Visionでページ画像を解析しstructure情報を抽出する."""
    contents = []
    for i, img_bytes in enumerate(images):
        contents.append(genai.types.Part.from_bytes(data=img_bytes, mime_type="image/png"))
        contents.append(f"（ページ {i + 1}/{len(images)}）")

    contents.append(
        f"\n銘柄コード: {ticker}\n企業名: {company}\n\n{STRUCTURE_PROMPT}"
    )

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=genai.types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        ),
    )

    return json.loads(response.text)


def main() -> None:
    """19社のPDFを1社ずつ解析してstructure.jsonを生成する."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
        api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        print("ERROR: GEMINI_API_KEY not found", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    error_entries: list[str] = []
    results_summary: list[dict] = []

    for pdf_path in sorted(SAMPLE_DIR.glob("*.pdf")):
        parts = pdf_path.name.split("_")
        ticker = parts[0]
        company = parts[3] if len(parts) > 3 else "unknown"

        # Stage 1: ページ特定
        relevant_pages = find_relevant_pages(pdf_path)
        if not relevant_pages:
            continue

        print(f"\n{'=' * 50}")
        print(f"{ticker} {company}: {len(relevant_pages)} relevant pages")

        # Stage 2: PNG化 + Gemini解析
        try:
            images = render_pages_to_png(pdf_path, relevant_pages)
            print(f"  PNG rendered: {len(images)} pages")

            # Primary model
            model_used = PRIMARY_MODEL
            try:
                result = analyze_with_gemini(client, images, ticker, company, PRIMARY_MODEL)
                print(f"  {PRIMARY_MODEL}: OK")
            except Exception as e1:
                print(f"  {PRIMARY_MODEL}: FAIL ({e1})")
                error_entries.append(
                    f"| {len(error_entries)+1} | {ticker} | {company} | Phase 2 | "
                    f"{PRIMARY_MODEL} 解析失敗: {str(e1)[:60]} | - | {FALLBACK_MODEL}にフォールバック | - |"
                )
                # Fallback
                model_used = FALLBACK_MODEL
                try:
                    result = analyze_with_gemini(client, images, ticker, company, FALLBACK_MODEL)
                    print(f"  {FALLBACK_MODEL}: OK")
                except Exception as e2:
                    print(f"  {FALLBACK_MODEL}: FAIL ({e2})")
                    error_entries.append(
                        f"| {len(error_entries)+1} | {ticker} | {company} | Phase 2 | "
                        f"{FALLBACK_MODEL}も失敗: {str(e2)[:60]} | - | スキップ | - |"
                    )
                    continue

            # 保存
            output_path = OUTPUT_DIR / f"{ticker}_structure.json"
            result["_generated_at"] = datetime.now(tz=JST).isoformat()
            result["_model_used"] = model_used
            result["_source_pdf"] = pdf_path.name
            result["_relevant_pages"] = [p + 1 for p in relevant_pages]

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            print(f"  Saved: {output_path.name}")
            print(f"  data_available: {result.get('data_available')}")
            print(f"  metrics: {len(result.get('metrics', []))}")
            print(f"  format: {result.get('presentation_format')}")

            results_summary.append({
                "ticker": ticker,
                "company": company,
                "data_available": result.get("data_available"),
                "metrics_count": len(result.get("metrics", [])),
                "format": result.get("presentation_format"),
                "model": model_used,
            })

            time.sleep(1)

        except Exception as e:
            print(f"  ERROR: {e}")
            error_entries.append(
                f"| {len(error_entries)+1} | {ticker} | {company} | Phase 2 | "
                f"{str(e)[:80]} | - | - | - |"
            )

    # サマリ出力
    print(f"\n{'=' * 50}")
    print(f"=== 完了: {len(results_summary)}/{19} 社 ===")
    for r in results_summary:
        print(f"  {r['ticker']} {r['company']}: {r['metrics_count']} metrics, {r['format']}, {r['model']}")

    # エラーログ追記
    if error_entries:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            for entry in error_entries:
                f.write(entry + "\n")
        print(f"\nエラーログ追記: {len(error_entries)} 件")


if __name__ == "__main__":
    main()

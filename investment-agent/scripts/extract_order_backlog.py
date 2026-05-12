# -*- coding: utf-8 -*-
"""受注残高 抽出スクリプト（Phase 3-4）.

structure.jsonに基づき、PDFからGemini Visionで受注残高データを抽出する。
Stage 1: pdfplumberでキーワードページ特定（コスト0）
Stage 2: pymupdfでPNG化 → Gemini Visionで数値抽出

Usage:
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/extract_order_backlog.py
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/extract_order_backlog.py --ticker 6330
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import fitz  # pymupdf
import pdfplumber
import structlog
from google import genai
from google.cloud import storage
from google.oauth2 import service_account

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.core.config import app_config, settings  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = Path("C:/tmp/order_backlog_survey/cat_sample")
STRUCTURE_DIR = PROJECT_ROOT / "meta" / "quarterly"
OUTPUT_DIR = Path("C:/tmp/order_backlog_survey/extracted")
ERROR_LOG = PROJECT_ROOT / "docs" / "plans" / "088_error_log.md"

KEYWORDS = ["繰越", "手持", "受注残"]
PRIMARY_MODEL = "gemini-3-flash-preview"
FALLBACK_MODEL = "gemini-3.1-pro"

JST = ZoneInfo("Asia/Tokyo")
BUCKET_NAME = app_config["datastore"]["gcs_bucket"]
RECORD_PREFIX = "quarterly/record"

log = structlog.get_logger()


def resolve_current_version(raw_structure: dict) -> dict | None:
    """バージョン配列から現行版を解決しフラット化する."""
    current_ver = raw_structure.get("current_version")
    for v in raw_structure.get("versions", []):
        if v.get("valid_from") == current_ver:
            if not v.get("data_available", True):
                return None
            return {
                "ticker": raw_structure["ticker"],
                "company_name": raw_structure["company_name"],
                "data_available": v.get("data_available", True),
                "metrics": v.get("metrics", []),
                "breakdown_dimensions": v.get("breakdown_dimensions", []),
            }
    return None


def build_extraction_prompt(structure: dict) -> str:
    """structure.jsonからGemini Vision用の抽出プロンプトを構築する."""
    metrics_list = []
    for m in structure.get("metrics", []):
        metrics_list.append(f'- "{m["name"]}" (type: {m["type"]}, unit: {m["unit"]})')

    metrics_text = "\n".join(metrics_list)

    breakdown_text = ""
    for bd in structure.get("breakdown_dimensions", []):
        breakdown_text += f'\n- {bd["dimension_name"]}: {", ".join(bd["items"])}'

    return f"""\
あなたは日本の上場企業の決算資料から数値データを正確に抽出するアナリストです。

以下のPDFページ画像から、指定されたメトリクスの数値を全て抽出してください。

## 対象企業
- 銘柄コード: {structure["ticker"]}
- 企業名: {structure["company_name"]}

## 抽出すべきメトリクス
{metrics_text}

## ブレークダウン軸
{breakdown_text if breakdown_text else "なし"}

## 出力JSON形式

```json
{{
  "ticker": "{structure["ticker"]}",
  "company_name": "{structure["company_name"]}",
  "periods": [
    {{
      "period": "YYYY年M月期 or YYYY年M月期 第NQ四半期",
      "period_type": "full_year|quarterly|semi_annual|cumulative",
      "data": {{
        "メトリクス名": {{
          "total": 数値またはnull,
          "breakdown": {{
            "項目名": 数値またはnull
          }}
        }}
      }}
    }}
  ],
  "unit": "百万円|千円|億円",
  "extraction_notes": "抽出時の注意点・不確実な点"
}}
```

## ルール
1. dataオブジェクトのキーには上記「抽出すべきメトリクス」の日本語名（引用符内の文字列）をそのまま使うこと。英語のtype値(order_intake等)は使わない
2. PDFに記載されている全ての期間のデータを抽出する（前期・当期両方あれば両方）
3. 数値はカンマを除去した整数または小数で返す
4. △や▲は負数に変換する（例: △1,234 → -1234）
5. テーブル内の数値を正確に読み取る。グラフからの読み取りは概算値であることをnotesに記載
6. 該当する数値が見つからない場合はnullを返す
7. 構成比（%）は抽出対象外。金額のみ抽出する
8. 「計」や「合計」行の値をtotalに、内訳行の値をbreakdownに入れる
"""


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


def extract_with_gemini(
    client: genai.Client,
    images: list[bytes],
    structure: dict,
    model: str,
) -> dict:
    """Gemini Visionでページ画像から数値を抽出する."""
    prompt = build_extraction_prompt(structure)

    contents = []
    for i, img_bytes in enumerate(images):
        contents.append(genai.types.Part.from_bytes(data=img_bytes, mime_type="image/png"))
        contents.append(f"（ページ {i + 1}/{len(images)}）")

    contents.append(prompt)

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=genai.types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
        ),
    )

    parsed = json.loads(response.text)
    if isinstance(parsed, list):
        parsed = parsed[0]
    return parsed


def _load_valid_metrics(ticker: str) -> set[str]:
    """structure.jsonからorder_intake/backlogメトリクス名を取得."""
    path = STRUCTURE_DIR / f"{ticker}_structure.json"
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    current_ver = data.get("current_version")
    for v in data.get("versions", []):
        if v.get("valid_from") == current_ver:
            if not v.get("data_available", True):
                return set()
            return {
                m["name"]
                for m in v.get("metrics", [])
                if m.get("type") in ("order_intake", "backlog")
            }
    return set()


def _build_record(extracted: dict, valid_metrics: set[str]) -> dict | None:
    """抽出結果から最新期レコードを構築. データなしならNone."""
    periods = extracted.get("periods", [])
    if not periods:
        return None
    latest = max(periods, key=lambda p: p.get("period", ""))

    data = {}
    period_data = latest.get("data", {})
    unit = extracted.get("unit", "")

    for metric_name, values in period_data.items():
        if metric_name not in valid_metrics:
            continue
        if values is None or not isinstance(values, dict):
            continue
        record_value = {"value": values.get("total"), "unit": unit}
        if values.get("breakdown"):
            record_value["breakdown"] = values["breakdown"]
        data[metric_name] = record_value

    if not data:
        return None

    return {
        "period": latest.get("period", ""),
        "period_type": latest.get("period_type", ""),
        "extraction_date": datetime.now(tz=JST).isoformat(),
        "source_doc_id": extracted.get("_source_pdf", "").split("/")[-1].split("_")[-1].replace(".pdf", ""),
        "data": data,
    }


def _save_record_to_gcs(bucket: storage.bucket.Bucket, ticker: str, extracted: dict) -> bool:
    """抽出結果からレコードを構築しGCS records.jsonにupsert. 成功時True."""
    valid_metrics = _load_valid_metrics(ticker)
    if not valid_metrics:
        return False

    record = _build_record(extracted, valid_metrics)
    if not record:
        return False

    gcs_path = f"{RECORD_PREFIX}/{ticker}/records.json"
    blob = bucket.blob(gcs_path)

    existing = {"ticker": ticker, "company_name": extracted.get("company_name", ""), "records": []}
    if blob.exists():
        existing = json.loads(blob.download_as_text())

    period = record["period"]
    records = existing.get("records", [])
    replaced = False
    for i, r in enumerate(records):
        if r.get("period") == period:
            records[i] = record
            replaced = True
            break
    if not replaced:
        records.append(record)
    existing["records"] = records

    blob.upload_from_string(
        json.dumps(existing, ensure_ascii=False, indent=2),
        content_type="application/json",
    )
    return True


def main() -> None:
    """structure.jsonに基づきPDFから受注残高データを抽出し、GCSに保管する."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", type=str, help="特定銘柄のみ実行")
    parser.add_argument("--no-save", action="store_true", help="GCS records.json への保管をスキップ")
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
        api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        log.error("gemini_api_key_not_found")
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    gcs_bucket = None
    if not args.no_save:
        creds = service_account.Credentials.from_service_account_file(
            settings.google_application_credentials
        )
        gcs_client = storage.Client(project=settings.gcp_project_id, credentials=creds)
        gcs_bucket = gcs_client.bucket(BUCKET_NAME)

    error_entries: list[str] = []
    results_summary: list[dict] = []

    structure_files = sorted(STRUCTURE_DIR.glob("*_structure.json"))

    for struct_path in structure_files:
        ticker = struct_path.name.split("_")[0]

        if args.ticker and ticker != args.ticker:
            continue

        with open(struct_path, encoding="utf-8") as f:
            raw_structure = json.load(f)

        structure = resolve_current_version(raw_structure)
        if not structure:
            continue

        company = structure.get("company_name", "unknown")

        # PDFを探す
        pdf_files = list(SAMPLE_DIR.glob(f"{ticker}_*.pdf"))
        if not pdf_files:
            print(f"{ticker}: PDF not found, skipping")
            continue

        pdf_path = pdf_files[0]

        print(f"\n{'=' * 50}")
        print(f"{ticker} {company}")

        # Stage 1: ページ特定
        relevant_pages = find_relevant_pages(pdf_path)
        if not relevant_pages:
            print(f"  No relevant pages found")
            continue

        print(f"  Relevant pages: {[p+1 for p in relevant_pages]}")

        # Stage 2: PNG化 + Gemini抽出
        try:
            images = render_pages_to_png(pdf_path, relevant_pages)
            print(f"  PNG rendered: {len(images)} pages")

            model_used = PRIMARY_MODEL
            try:
                result = extract_with_gemini(client, images, structure, PRIMARY_MODEL)
                print(f"  {PRIMARY_MODEL}: OK")
            except Exception as e1:
                print(f"  {PRIMARY_MODEL}: FAIL ({e1})")
                error_entries.append(
                    f"| {len(error_entries)+1} | {ticker} | {company} | Phase 3 | "
                    f"{PRIMARY_MODEL} 抽出失敗: {str(e1)[:60]} | - | {FALLBACK_MODEL}にフォールバック | - |"
                )
                model_used = FALLBACK_MODEL
                try:
                    result = extract_with_gemini(client, images, structure, FALLBACK_MODEL)
                    print(f"  {FALLBACK_MODEL}: OK")
                except Exception as e2:
                    print(f"  {FALLBACK_MODEL}: FAIL ({e2})")
                    error_entries.append(
                        f"| {len(error_entries)+1} | {ticker} | {company} | Phase 3 | "
                        f"{FALLBACK_MODEL}も失敗: {str(e2)[:60]} | - | スキップ | - |"
                    )
                    continue

            # メタデータ追加
            result["_extracted_at"] = datetime.now(tz=JST).isoformat()
            result["_model_used"] = model_used
            result["_source_pdf"] = pdf_path.name
            result["_relevant_pages"] = [p + 1 for p in relevant_pages]

            # 保存
            output_path = OUTPUT_DIR / f"{ticker}_extracted.json"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            # サマリ
            periods = result.get("periods", [])
            data_keys = set()
            for p in periods:
                data_keys.update(p.get("data", {}).keys())

            print(f"  Saved: {output_path.name}")
            print(f"  Periods: {len(periods)}")
            print(f"  Metrics extracted: {len(data_keys)}")
            if result.get("extraction_notes"):
                print(f"  Notes: {result['extraction_notes'][:100]}")

            # GCS records.json に保管
            saved_to_gcs = False
            if gcs_bucket is not None:
                try:
                    saved_to_gcs = _save_record_to_gcs(gcs_bucket, ticker, result)
                    if saved_to_gcs:
                        print(f"  GCS record saved: {RECORD_PREFIX}/{ticker}/records.json")
                except Exception as e_gcs:
                    print(f"  GCS save failed: {e_gcs}")

            results_summary.append({
                "ticker": ticker,
                "company": company,
                "periods": len(periods),
                "metrics": len(data_keys),
                "model": model_used,
                "notes": result.get("extraction_notes", ""),
                "gcs_saved": saved_to_gcs,
            })

            time.sleep(1)

        except Exception as e:
            print(f"  ERROR: {e}")
            error_entries.append(
                f"| {len(error_entries)+1} | {ticker} | {company} | Phase 3 | "
                f"{str(e)[:80]} | - | - | - |"
            )

    # サマリ出力
    print(f"\n{'=' * 50}")
    print(f"=== 完了: {len(results_summary)} 社 ===")
    for r in results_summary:
        notes_short = r["notes"][:40] if r["notes"] else ""
        print(f"  {r['ticker']} {r['company']}: {r['periods']}期, {r['metrics']}指標, {r['model']}" +
              (f" [{notes_short}]" if notes_short else ""))

    # エラーログ追記
    if error_entries:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            for entry in error_entries:
                f.write(entry + "\n")
        print(f"\nエラーログ追記: {len(error_entries)} 件")


if __name__ == "__main__":
    main()

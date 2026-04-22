#!/usr/bin/env python3
"""月次NG銘柄 詳細調査ツール.

BC突合でNGとなった銘柄を1社ずつ詳細調査する。

各社について:
1. BC突合CSVからNG詳細を取得
2. GCSから月次PDFをダウンロード（月次キーワードで選別）
3. pdfplumber でテキスト抽出 + テーブル抽出
4. 数字逆引き（BC値・抽出値をPDFテキストから検索）
5. PDF→画像化（PyMuPDF）
6. Gemini 3 Flash で画像分析（PDF構造・値の所在・原因・修正方法を特定）
7. 調査結果をJSONに保存

使い方:
  PYTHONUTF8=1 python scripts/investigate_monthly_ng.py 2670,3077
  PYTHONUTF8=1 python scripts/investigate_monthly_ng.py --csv C:/tmp/buffett_compare.csv 2670
  PYTHONUTF8=1 python scripts/investigate_monthly_ng.py  # 引数なし → CSVの全NG銘柄

知見ファイル: docs/knowledges/tools/070_monthly_ng_investigation.md
"""

import json
import io
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pdfplumber
import fitz  # PyMuPDF for image conversion

# ====================================================
# 設定
# ====================================================
JST = timezone(timedelta(hours=9))
PROJECT_ROOT = Path(r"C:\gdrive\claude\investment-agent")
DEFAULT_OUTPUT_DIR = Path(r"C:\tmp\monthly_ng_investigation")

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = "gemini-3-flash-preview"

KEY_FILE = PROJECT_ROOT / "keys" / "gcp-service-account.json"
GCS_BUCKET = "stock_data_1930932"
GCP_PROJECT = "gmailpj-357912"

BC_CSV: Path = Path(r"C:\tmp\buffett_compare_20260409_102142.csv")  # mainでargparse上書き

# ====================================================
# SSL パッチ + GCP クライアント
# ====================================================
import urllib3
import requests as _req
from requests.adapters import HTTPAdapter as _HA
urllib3.disable_warnings()
class _NV(_HA):
    def send(self, req, **kw):
        kw["verify"] = False
        return super().send(req, **kw)
_orig = _req.Session.__init__
def _p(self, *a, **kw):
    _orig(self, *a, **kw)
    self.mount("https://", _NV())
    self.verify = False
_req.Session.__init__ = _p

from google.cloud import storage
from google.oauth2 import service_account
from google import genai

creds = service_account.Credentials.from_service_account_file(str(KEY_FILE))
gcs_client = storage.Client(project=GCP_PROJECT, credentials=creds)
bucket = gcs_client.bucket(GCS_BUCKET)
genai_client = genai.Client(api_key=GEMINI_API_KEY)


def get_ng_details(ticker: str) -> list[dict]:
    """BC突合CSVからNG詳細を取得."""
    df = pd.read_csv(BC_CSV, encoding="utf-8-sig")
    d = df[(df["ticker"].astype(str) == ticker) & (df["match"] == "NG")]
    rows = []
    for _, r in d.iterrows():
        rows.append({
            "year_month": r["year_month"],
            "our_field": r["our_field"],
            "our_value": r["our_value"],
            "bc_value": r["bc_value"],
            "diff": r["diff"],
        })
    return rows


def get_monthly_pdf(ticker: str, year_month: str) -> tuple[bytes | None, str]:
    """GCSから対象年月の月次開示PDFを取得.

    月次PDFの判定: ファイル名に「月度」「月次」「概況」「売上速報」「月次売上」等を含む。
    決算短信・配当通知等は除外。
    """
    prefix = f"tdnet/{ticker}/"
    blobs = list(gcs_client.list_blobs(bucket, prefix=prefix))
    pdf_blobs = [(b.name, b) for b in blobs if b.name.endswith(".pdf")]

    if not pdf_blobs:
        return None, ""

    # 月次開示キーワード
    monthly_kw = ["月度", "月次", "概況", "売上速報", "月次売上", "月次業績", "月次連結売上",
                   "月次開示", "月次報告", "KPI"]
    # 除外キーワード
    exclude_kw = ["決算短信", "配当", "株式分割", "役員", "四半期", "有価証券", "適時開示",
                  "コーポレート", "定款", "株主総会", "自己株式", "上場維持"]

    # 月次PDF候補を絞り込み
    monthly_pdfs = []
    for name, blob in pdf_blobs:
        fname = name.split("/")[-1]
        is_monthly = any(kw in fname for kw in monthly_kw)
        is_excluded = any(kw in fname for kw in exclude_kw)
        if is_monthly and not is_excluded:
            monthly_pdfs.append((name, blob))

    if not monthly_pdfs:
        # フォールバック: 「その他（未分類）」カテゴリで月番号を含むもの
        target_month = int(year_month.split("-")[1])
        for name, blob in pdf_blobs:
            fname = name.split("/")[-1]
            if "その他" in fname or "未分類" in fname:
                if f"{target_month}月" in fname:
                    monthly_pdfs.append((name, blob))

    if not monthly_pdfs:
        return None, ""

    # 対象年月に最も近いPDFを選択
    target_ym = year_month.replace("-", "")
    target_month_str = f"{int(year_month.split('-')[1])}月"

    # 対象月を含むPDFを優先
    for name, blob in sorted(monthly_pdfs, reverse=True):
        fname = name.split("/")[-1]
        if target_month_str in fname:
            return blob.download_as_bytes(), name

    # フォールバック: 日付が最も近いPDF
    best = None
    best_name = ""
    best_dist = 999999
    for name, blob in monthly_pdfs:
        fname = name.split("/")[-1]
        m = re.match(r"(\d{8})", fname)
        if m:
            file_date = m.group(1)
            dist = abs(int(file_date[:6]) - int(target_ym))
            if dist < best_dist:
                best_dist = dist
                best = blob
                best_name = name

    if best:
        return best.download_as_bytes(), best_name
    # 最終フォールバック
    return monthly_pdfs[-1][1].download_as_bytes(), monthly_pdfs[-1][0]


def extract_pdf_text(pdf_bytes: bytes) -> dict:
    """pdfplumber でテキスト + テーブル抽出."""
    result = {"pages": [], "tables": []}

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for i, page in enumerate(pdf.pages):
            # テキスト抽出
            text = page.extract_text() or ""
            result["pages"].append({"page": i + 1, "text": text})

            # テーブル抽出
            tables = page.extract_tables()
            for ti, tbl in enumerate(tables):
                rows = []
                for row in tbl:
                    cells = [(c or "").replace("\n", " ").strip() for c in row]
                    rows.append(cells)
                result["tables"].append({
                    "page": i + 1,
                    "table_index": ti,
                    "rows": rows,
                })

    return result


def pdf_to_images(pdf_bytes: bytes) -> list[bytes]:
    """PDF→PNG画像変換."""
    images = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            images.append(pix.tobytes("png"))
    return images


def analyze_with_gemini(
    ticker: str,
    ng_details: list[dict],
    images: list[bytes],
    pdf_text: dict,
) -> str:
    """Gemini 3 Flash で画像+テキストを分析."""

    ng_desc = "\n".join(
        f"  - {ng['year_month']} {ng['our_field']}: 抽出値={ng['our_value']}, BC値={ng['bc_value']}, 差分={ng['diff']}"
        for ng in ng_details
    )

    # pdfplumber テーブルのサマリ
    table_summary = ""
    for tbl in pdf_text["tables"][:5]:  # 最大5テーブル
        header = " | ".join(tbl["rows"][0]) if tbl["rows"] else ""
        row_count = len(tbl["rows"])
        table_summary += f"  Page{tbl['page']} Table{tbl['table_index']}: {row_count}行, ヘッダー=[{header[:200]}]\n"

    prompt = f"""銘柄 {ticker} の月次開示PDFを分析してください。

# 不一致フィールド（BC突合でNGだった項目）
{ng_desc}

# pdfplumber テーブル検出結果
{table_summary}

# 分析タスク
各NGフィールドについて、以下を特定してください:

1. **BC値の所在**: BC値（正解）はPDFのどこに記載されているか（ページ番号、テーブル名、行ラベル、列ヘッダー）
2. **抽出値の所在**: 我々の抽出値はPDFのどこから来たか（なぜ間違った値を取得したか）
3. **原因**: 不一致の根本原因（単位違い/前年同月比形式差/テーブル混同/月ずれ/読み取りミス等）
4. **修正方法**: adapter修正で対応可能か、プロンプト改善が必要か

JSON形式で回答してください:
{{
  "ticker": "{ticker}",
  "pdf_structure": "PDFの全体構造の説明",
  "ng_analysis": [
    {{
      "field": "フィールド名",
      "bc_value_location": "BC値がPDF上のどこにあるか",
      "our_value_location": "抽出値がPDF上のどこから来たか",
      "cause": "不一致の原因",
      "fix_type": "unit_scale/yoy_offset/prompt改善/テーブル指定/対応不可 のいずれか",
      "fix_detail": "具体的な修正内容"
    }}
  ]
}}"""

    contents = []
    for img in images[:3]:  # 最大3ページ
        contents.append(genai.types.Part.from_bytes(data=img, mime_type="image/png"))
    contents.append(prompt)

    try:
        resp = genai_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        return resp.text
    except Exception as e:
        return f"ERROR: {e}"


def investigate_ticker(ticker: str, output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict:
    """1社の詳細調査."""
    print(f"\n{'='*60}")
    print(f"  {ticker} 調査開始")
    print(f"{'='*60}")

    # 1. NG詳細取得
    ng_details = get_ng_details(ticker)
    print(f"  NG件数: {len(ng_details)}")
    for ng in ng_details:
        print(f"    {ng['year_month']} {ng['our_field']}: our={ng['our_value']} bc={ng['bc_value']}")

    # 2. 最新PDF取得（最初のNG年月を基準）
    target_ym = ng_details[0]["year_month"] if ng_details else "2026-02"
    pdf_bytes, pdf_name = get_monthly_pdf(ticker, target_ym)
    if not pdf_bytes:
        print(f"  ❌ PDF not found")
        return {"ticker": ticker, "error": "PDF not found"}
    print(f"  PDF: {pdf_name} ({len(pdf_bytes)} bytes)")

    # 3. pdfplumber テキスト + テーブル抽出
    pdf_text = extract_pdf_text(pdf_bytes)
    print(f"  pdfplumber: {len(pdf_text['pages'])}ページ, {len(pdf_text['tables'])}テーブル")
    for tbl in pdf_text["tables"]:
        rows = tbl["rows"]
        header = " | ".join(rows[0][:5]) if rows else ""
        print(f"    Page{tbl['page']} Tbl{tbl['table_index']}: {len(rows)}行 [{header[:80]}]")

    # 4. PDF→画像
    images = pdf_to_images(pdf_bytes)
    print(f"  画像化: {len(images)}ページ")

    # 画像保存（確認用）
    for i, img in enumerate(images):
        img_path = output_dir / f"{ticker}_page{i+1}.png"
        with open(img_path, "wb") as f:
            f.write(img)

    # 4.5. 数字逆引き（BC値・抽出値をPDFテキストから検索）
    print(f"  数字逆引き:")
    full_text = "\n".join(p["text"] for p in pdf_text["pages"])
    reverse_lookup = {}
    for ng in ng_details:
        for label, val in [("bc", ng["bc_value"]), ("our", ng["our_value"])]:
            val_str = str(val)
            # 整数・小数いずれも検索
            candidates = [val_str]
            try:
                fval = float(val)
                if fval == int(fval):
                    candidates.append(str(int(fval)))
                    # カンマ付き
                    iv = int(fval)
                    if iv >= 1000:
                        candidates.append(f"{iv:,}")
                else:
                    candidates.append(f"{fval:.1f}")
            except (ValueError, TypeError):
                pass

            found = []
            for cand in candidates:
                idx = 0
                while True:
                    idx = full_text.find(cand, idx)
                    if idx == -1:
                        break
                    context = full_text[max(0, idx-40):idx+len(cand)+40].replace("\n", "|")
                    found.append(f"pos={idx}: ...{context}...")
                    idx += 1

            key = f"{ng['year_month']} {ng['our_field']} {label}={val}"
            reverse_lookup[key] = found[:3] if found else ["NOT FOUND"]
            status = f"{'✓' if found else '✗'} {len(found)}箇所"
            print(f"    {label}={val} ({ng['our_field'][:30]}): {status}")

    # 5. Gemini 3 Flash 分析
    print(f"  Gemini 3 Flash 分析中...")
    analysis = analyze_with_gemini(ticker, ng_details, images, pdf_text)
    print(f"  分析完了 ({len(analysis)} chars)")

    # 6. 結果保存
    result = {
        "ticker": ticker,
        "ng_count": len(ng_details),
        "ng_details": ng_details,
        "pdf_name": pdf_name,
        "page_count": len(pdf_text["pages"]),
        "table_count": len(pdf_text["tables"]),
        "reverse_lookup": reverse_lookup,
        "pdfplumber_text": pdf_text,
        "analysis": analysis,
        "investigated_at": datetime.now(JST).isoformat(),
    }

    out_path = output_dir / f"{ticker}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"  → {out_path}")

    # 分析結果を表示
    try:
        parsed = json.loads(analysis)
        print(f"\n  【分析結果】")
        print(f"  PDF構造: {parsed.get('pdf_structure', 'N/A')[:200]}")
        for ng_a in parsed.get("ng_analysis", []):
            print(f"  --- {ng_a.get('field', '?')} ---")
            print(f"    原因: {ng_a.get('cause', '?')}")
            print(f"    修正: {ng_a.get('fix_type', '?')} - {ng_a.get('fix_detail', '?')[:100]}")
    except Exception:
        print(f"  分析テキスト: {analysis[:500]}")

    return result


def main():
    """メインエントリポイント."""
    import argparse

    parser = argparse.ArgumentParser(description="月次NG銘柄 詳細調査ツール")
    parser.add_argument("tickers", nargs="?", default=None,
                        help="カンマ区切りの銘柄コード（省略時: CSVの全NG銘柄）")
    parser.add_argument("--csv", type=str, default=None,
                        help="BC突合CSVパス（省略時: C:/tmp/ の最新ファイル）")
    parser.add_argument("--output", type=str, default=None,
                        help="出力ディレクトリ（省略時: C:/tmp/monthly_ng_investigation）")
    args = parser.parse_args()

    # BC突合CSV
    global BC_CSV
    if args.csv:
        BC_CSV = Path(args.csv)
    else:
        # C:/tmp/ から最新の buffett_compare CSV を自動検出
        import glob
        csvs = sorted(glob.glob(r"C:\tmp\buffett_compare_*.csv"))
        if csvs:
            BC_CSV = Path(csvs[-1])
    print(f"BC CSV: {BC_CSV}")

    # 出力ディレクトリ
    output_dir = Path(args.output) if args.output else DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    # 銘柄リスト
    if args.tickers:
        tickers = args.tickers.split(",")
    else:
        # CSVから全NG銘柄を取得
        df = pd.read_csv(BC_CSV, encoding="utf-8-sig")
        tickers = sorted(df[df["match"] == "NG"]["ticker"].astype(str).unique())
        print(f"CSV全NG銘柄: {len(tickers)}社")

    print(f"=== 月次NG詳細調査 ===")
    print(f"対象: {len(tickers)}社")
    print(f"出力: {output_dir}")
    print(f"Timestamp: {datetime.now(JST).isoformat()}")

    for ticker in tickers:
        investigate_ticker(ticker, output_dir=output_dir)
        time.sleep(2)  # API rate limit

    print(f"\n=== 調査完了 ===")
    print(f"結果: {output_dir}")


if __name__ == "__main__":
    main()

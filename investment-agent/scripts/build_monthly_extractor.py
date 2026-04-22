#!/usr/bin/env python3
"""月次データ抽出アダプター構築スクリプト（Phase 1）.

【概要】
  月次開示文書（TDNET BQ テキスト / 独自ダウンロードファイル）を解析し、
  Gemini を使って extract_adapter.json を生成する。

【ファイル構成（GCS: gs://stock_data_1930932/monthly/meta/{ticker}/）】
  structure.json        → 抽出したいメトリクス定義（既存）
  extract_adapter.json  → 文書→メトリクスのマッピングルール（本スクリプトが生成）

  ※ adapter.json / download_adapter.json は既存のダウンロード用アダプターであり本スクリプトとは別
  ※ monthly_records.json の生成は extract_monthly_data.py（Phase 2）が担う

【使い方】
  # Phase 1: アダプター構築（Gemini 使用・初回のみ）
  PYTHONUTF8=1 uv run python scripts/build_monthly_extractor.py --sample 30
  PYTHONUTF8=1 uv run python scripts/build_monthly_extractor.py --tickers 3097 2705 9832

  # ローカル確認（GCS 保存スキップ・data/tmp/ に出力）
  PYTHONUTF8=1 uv run python scripts/build_monthly_extractor.py --tickers 3097 --no-gcs
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from bs4 import BeautifulSoup
from google.cloud import bigquery, storage
from google.oauth2 import service_account

# ====================================================
# SSL パッチ（Windows ローカル GCS/BQ 認証用）
# ====================================================
import urllib3
import requests as _req
from requests.adapters import HTTPAdapter as _HA

urllib3.disable_warnings()

class _NoVerify(_HA):
    def send(self, req, **kw):
        kw["verify"] = False
        return super().send(req, **kw)

_orig_init = _req.Session.__init__

def _patched_init(self, *a, **kw):
    _orig_init(self, *a, **kw)
    self.mount("https://", _NoVerify())
    self.verify = False

_req.Session.__init__ = _patched_init

# ====================================================
# 設定
# ====================================================
JST = timezone(timedelta(hours=9))
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

GCP_PROJECT = "gmailpj-357912"
GCS_BUCKET = "stock_data_1930932"
GCS_META = "monthly/meta"
GCS_DOCS = "monthly/docs"
BQ_DATASET = "STOCK"
VERTEXAI_REGION = "us-central1"
GEMINI_MODEL = "gemini-3-flash-preview"

KEY_FILE = PROJECT_ROOT / "keys" / "gcp-service-account.json"
INDEX_CSV = PROJECT_ROOT / "data" / "monthly_adapter_index.csv"
MONTHLYIR_DIR = PROJECT_ROOT / "data" / "monthlyir"
TMP_DIR = PROJECT_ROOT / "data" / "tmp"
LOG_DIR = PROJECT_ROOT / "data" / "logs"

LOG_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)

# ====================================================
# ロギング
# ====================================================

def setup_logging() -> logging.Logger:
    logger = logging.getLogger("build_monthly_extractor")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.setLevel(logging.INFO)
    logger.addHandler(sh)

    log_file = LOG_DIR / f"build_monthly_extractor_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}.log"
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    logger.addHandler(fh)

    return logger


# ====================================================
# GCP クライアント
# ====================================================

IS_CLOUD_RUN = os.environ.get("K_SERVICE") is not None or os.environ.get("CLOUD_RUN_TASK_INDEX") is not None


def get_credentials():
    if IS_CLOUD_RUN and not KEY_FILE.exists():
        import google.auth
        creds, _ = google.auth.default()
        return creds
    return service_account.Credentials.from_service_account_file(str(KEY_FILE))


def get_gcs() -> storage.Client:
    return storage.Client(project=GCP_PROJECT, credentials=get_credentials())


def get_bq() -> bigquery.Client:
    return bigquery.Client(project=GCP_PROJECT, credentials=get_credentials())


def get_gemini():
    """google-genai クライアント（ローカル個人APIキー）を返す。

    ローカル実行時は .env の GEMINI_API_KEY を使用する。
    戻り値の第2要素（旧 GenerationConfig）は後方互換のため None を返す（_call_gemini_json 内で types.GenerateContentConfig を直接生成）。
    """
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY が .env に設定されていません")
    from google import genai
    client = genai.Client(api_key=api_key)
    return client, None


# ====================================================
# GCS ヘルパー
# ====================================================

def gcs_read_json(gcs: storage.Client, path: str) -> Optional[dict]:
    try:
        blob = gcs.bucket(GCS_BUCKET).blob(path)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text(encoding="utf-8"))
    except Exception:
        return None


def gcs_write_json(gcs: storage.Client, path: str, data: dict) -> None:
    blob = gcs.bucket(GCS_BUCKET).blob(path)
    blob.upload_from_string(
        json.dumps(data, ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8",
    )


def gcs_download_monthly_files(
    gcs: storage.Client, ticker: str
) -> tuple[Optional[Path], Optional[Path], list[Path]]:
    """GCS monthly/docs/{ticker}/ からXLSX/CSV/HTML/PDFファイルをtmpにDLして
    (spreadsheet_path, html_path, pdf_paths) を返す。"""
    import tempfile
    bucket = gcs.bucket(GCS_BUCKET)
    prefix = f"{GCS_DOCS}/{ticker}/"
    blobs = list(bucket.list_blobs(prefix=prefix))
    if not blobs:
        return None, None, []

    tmp_dir = Path(tempfile.mkdtemp())
    spreadsheet_path: Optional[Path] = None
    html_path: Optional[Path] = None
    pdf_paths: list[Path] = []

    for blob in sorted(blobs, key=lambda b: b.name):
        ext = Path(blob.name).suffix.lower()
        # XLSX/XLS/CSV（最新1件）
        if ext in (".xlsx", ".xls", ".csv") and spreadsheet_path is None:
            local = tmp_dir / Path(blob.name).name
            blob.download_to_filename(str(local))
            spreadsheet_path = local
        # HTML テーブルファイル（最新1件）
        elif blob.name.endswith("_monthly_table.html") and html_path is None:
            local = tmp_dir / Path(blob.name).name
            blob.download_to_filename(str(local))
            html_path = local
        # PDF（最大3件）
        elif ext == ".pdf" and len(pdf_paths) < 3:
            local = tmp_dir / Path(blob.name).name
            blob.download_to_filename(str(local))
            pdf_paths.append(local)

    return spreadsheet_path, html_path, pdf_paths


# ====================================================
# structure.json パース
# ====================================================

def parse_structure_metrics(structure: dict) -> list[str]:
    """structure.json からメトリクス名一覧を返す（metrics / monthly_items 両対応）"""
    metrics: list[str] = []
    seen: set[str] = set()
    items = structure.get("metrics") or structure.get("monthly_items") or []
    for item in items:
        name = item.get("name", "").strip()
        if not name:
            continue
        # 年マーカー
        if re.match(r"^\d{4}年$", name):
            continue
        # 月マーカー
        if re.match(r"^\d{1,2}月$", name):
            continue
        # 純粋な数字
        if re.match(r"^\d+$", name):
            continue
        if name not in seen:
            seen.add(name)
            metrics.append(name)
    return metrics


# ====================================================
# BQ: TDNET 月次文書テキスト取得
# ====================================================

def get_tdnet_docs(ticker: str, bq: bigquery.Client, since: int = 2020, limit: int = 200) -> list[dict]:
    """TDNET_DOCUMENTS_ENHANCED から月次開示の全文テキストを取得"""
    sql = f"""
    SELECT
      TICKER, SUBMISSION_DATE, DOC_TITLE, FILE_NAME,
      STRING_AGG(CHUNK_TEXT, ' ') AS full_text
    FROM `{GCP_PROJECT}.{BQ_DATASET}.TDNET_DOCUMENTS_ENHANCED`
    WHERE TICKER = @ticker
      AND (
        MAIN_CATEGORY = '月次開示'
        OR EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) AS sc WHERE sc = '月次開示')
      )
      AND SUBMISSION_DATE >= '{since}-01-01'
    GROUP BY TICKER, SUBMISSION_DATE, DOC_TITLE, FILE_NAME
    ORDER BY SUBMISSION_DATE DESC
    LIMIT {limit}
    """
    job_cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("ticker", "STRING", ticker)]
    )
    try:
        df = bq.query(sql, job_config=job_cfg).to_dataframe()
        return df.to_dict("records")
    except Exception as e:
        return []


# ====================================================
# Gemini: TDNET テキスト用抽出アダプター構築
# ====================================================

def _fix_gemini_json(s: str) -> str:
    """Gemini が返す JSON の一般的な問題を修正する.

    - JSON 文字列値内の改行 → \\n
    - JSON 文字列値内の未エスケープのバックスラッシュ（regex等）→ \\\\
    """
    # 文字列値内の裸の改行を \\n に変換
    # （JSON パーサーは文字列内の改行を許容しない）
    result = []
    in_string = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == '"' and (i == 0 or s[i - 1] != '\\'):
            in_string = not in_string
            result.append(c)
        elif in_string and c == '\n':
            result.append('\\n')
        elif in_string and c == '\r':
            result.append('\\r')
        elif in_string and c == '\t':
            result.append('\\t')
        else:
            result.append(c)
        i += 1
    return ''.join(result)


TDNET_BUILD_PROMPT = """\
あなたは月次開示PDF文書の構造解析エキスパートです。

=== 抽出したいメトリクス（structure.json から） ===
{metrics_json}

=== サンプル文書テキスト（PDF OCR、最大3000文字） ===
タイトル: {doc_title}
---
{sample_text}
---

=== タスク ===
上記テキストを解析し、各メトリクスを抽出するためのルールをJSONで生成してください。

テキストはPDF→テキスト変換されており、月次の表データが含まれます。
典型的なパターン:
  - "売上高 108.6% 114.8% 111.1% ..." のように行ラベル+値の並び
  - または "7月 8月 9月 ..." のような月ヘッダー行の後に値行が続く

ルール:
1. nameはメトリクスリストの値と完全一致させること
2. テキストに対応データがないメトリクスはfieldsに含めない
3. value_type: パーセント値→"percentage" / 整数（店舗数等）→"integer" / 小数→"float"
4. keyはnameと完全に同じ値にすること（日本語のまま。英語スネークケース不可）
5. row_label_regexはPython re.search()で使用する（大文字小文字無視）
6. **絶対禁止**: row_label_regexに具体的な数値（例: 357, 101.4, 98.3）をハードコードしてはいけない。
   数値は毎月変わるため、次月のPDFでマッチしなくなる。行の識別にはラベル文字列のみを使うこと。
   NG例: "^全店舗数\\s+357\\s+358"  OK例: "トライアル.*全店舗数"
7. 行に複数の数値が並ぶ表形式（例: "全店計 100.2 98.5 103.1 99.8 101.5"）で
   特定の位置（例: 最新月＝5番目）の値が必要な場合は、キャプチャグループを使って
   row_label_regexに全値を捕捉し、group=N（1始まり）で取得するグループ番号を指定すること。
   例: row_label_regex="全店計\\s+([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)", group=5
8. 同一パターンが文書内に複数回出現し（累計テーブル等）、最後の出現値が最新月の値である場合は
   use_last_number=true を指定すること。
   例: 月次売上が各月ごとに行として並ぶ場合、最下行（最新月）の値を取得するために使用する。
   なお row_label_regex にキャプチャグループがある場合は、そのグループの中から最後の数値を返す。
"""

# response_schema: Gemini に出力構造を強制（C）
_FIELD_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "key":             {"type": "STRING"},
        "name":            {"type": "STRING"},
        "row_label_regex": {"type": "STRING"},
        "value_type":      {"type": "STRING"},
        "group":           {"type": "INTEGER"},
        "use_last_number": {"type": "BOOLEAN"},
        "notes":           {"type": "STRING"},
    },
    "required": ["key", "name", "row_label_regex", "value_type"],
}

TDNET_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "source":                 {"type": "STRING", "enum": ["tdnet"]},
        "format":                 {"type": "STRING", "enum": ["text"]},
        "doc_title_pattern":      {"type": "STRING"},
        "year_from_title_regex":  {"type": "STRING"},
        "month_from_title_regex": {"type": "STRING"},
        "fields":                 {"type": "ARRAY", "items": _FIELD_SCHEMA},
        "extraction_notes":       {"type": "STRING"},
    },
    "required": ["source", "format", "fields"],
}

XLSX_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "source":           {"type": "STRING", "enum": ["download"]},
        "format":           {"type": "STRING", "enum": ["xlsx", "xls", "csv"]},
        "sheet_name":       {"type": "STRING"},
        "label_col_index":  {"type": "INTEGER"},
        "month_row_index":  {"type": "INTEGER"},
        "data_start_row":   {"type": "INTEGER"},
        "fields":           {"type": "ARRAY", "items": _FIELD_SCHEMA},
        "extraction_notes": {"type": "STRING"},
    },
    "required": ["source", "format", "fields"],
}


def _call_gemini_json(prompt: str, schema: dict, gemini_model, GenConfig, logger) -> Optional[dict]:
    """Gemini を呼び出し JSON を返す。response_schema でパース失敗を最小化。

    gemini_model: google-genai Client（旧VertexAI GenerativeModelと互換名）
    GenConfig: 後方互換用（未使用）
    """
    from google.genai import types
    response = None
    try:
        response = gemini_model.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=32768,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        raw = response.text.strip()
        raw = _fix_gemini_json(raw)
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raw_preview = response.text[:200] if response is not None else "<no response>"
        logger.warning(f"  [gemini] JSON parse error: {e}  raw[:200]={raw_preview}")
        return None
    except Exception as e:
        logger.warning(f"  [gemini] エラー: {e}")
        return None


def build_tdnet_adapter_with_gemini(
    ticker: str,
    company_name: str,
    metrics: list[str],
    sample_docs: list[dict],
    gemini_model,
    GenConfig,
    logger: logging.Logger,
) -> Optional[dict]:
    """Gemini で TDNET テキスト用の抽出アダプターを構築（A+C+E）"""
    if not sample_docs:
        return None

    sample = sample_docs[0]
    full_text = (sample.get("full_text") or "")[:3000]
    doc_title = str(sample.get("DOC_TITLE", ""))

    def _try(metrics_subset: list[str]) -> Optional[dict]:
        prompt = TDNET_BUILD_PROMPT.format(
            metrics_json=json.dumps(metrics_subset, ensure_ascii=False, indent=2),
            doc_title=doc_title,
            sample_text=full_text,
        )
        return _call_gemini_json(prompt, TDNET_RESPONSE_SCHEMA, gemini_model, GenConfig, logger)

    # 全 metrics で試みる
    adapter = _try(metrics)

    # E: 失敗時は metrics を半分に分割して再試行
    if adapter is None and len(metrics) > 1:
        logger.info(f"  [build] 分割リトライ: {len(metrics)}件 → 2バッチ")
        mid = len(metrics) // 2
        a1 = _try(metrics[:mid])
        a2 = _try(metrics[mid:])
        if a1 and a2:
            a1["fields"] = a1.get("fields", []) + a2.get("fields", [])
            adapter = a1
        else:
            adapter = a1 or a2

    if adapter is None:
        logger.error(f"  [build] Gemini 失敗（分割リトライ含む）")
        return None

    # 安全弁: source/format は固定値で上書き（Gemini が誤った値を入れるケースがある）
    adapter["source"] = "tdnet"
    adapter["format"] = "text"
    adapter.update({
        "ticker": ticker,
        "company_name": company_name,
        "created_at": datetime.now(JST).isoformat(),
        "created_by": GEMINI_MODEL,
        "sample_doc_title": doc_title,
        "sample_submission_date": str(sample.get("SUBMISSION_DATE", "")),
        "report_url": "TDNET文書",
    })
    return adapter


# ====================================================
# XLSX/CSV ファイル用アダプター構築（Gemini）
# ====================================================

XLSX_BUILD_PROMPT = """\
あなたはExcel月次開示データの構造解析エキスパートです。

=== 抽出したいメトリクス ===
{metrics_json}

=== DataFrame プレビュー（先頭20行×全列） ===
ファイル: {filename}
シート: {sheet_name}
---
{df_preview}
---

=== タスク ===
上記のDataFrameから各メトリクスを抽出するルールをJSONで生成してください。

ルール:
1. nameはメトリクスリストの値と完全一致させること
2. value_type: パーセント値→"percentage" / 整数→"integer" / 小数→"float"
3. keyはnameと完全に同じ値にすること（日本語のまま。英語スネークケース不可）
4. row_label_regexはPython re.search()で使用する（大文字小文字無視）
5. **絶対禁止**: row_label_regexに具体的な数値をハードコードしてはいけない。行の識別にはラベル文字列のみを使うこと。
"""


def build_xlsx_adapter_with_gemini(
    ticker: str,
    company_name: str,
    metrics: list[str],
    file_path: Path,
    gemini_model,
    GenConfig,
    logger: logging.Logger,
) -> Optional[dict]:
    """Gemini で XLSX/CSV 用の抽出アダプターを構築（A+C+E）"""
    try:
        if file_path.suffix.lower() in (".xlsx", ".xls"):
            xl = pd.ExcelFile(file_path)
            sheet_name = xl.sheet_names[0]
            df = pd.read_excel(file_path, sheet_name=sheet_name, header=None, nrows=20)
        else:  # CSV
            sheet_name = "N/A"
            df = None
            for enc in ("utf-8-sig", "cp932", "shift_jis", "latin-1"):
                try:
                    df = pd.read_csv(file_path, header=None, nrows=20, encoding=enc)
                    break
                except (UnicodeDecodeError, Exception):
                    continue
            if df is None:
                raise ValueError(f"CSVの文字コード判定失敗: {file_path.name}")
        df_preview = df.fillna("").to_string(index=True, header=True)
    except Exception as e:
        logger.error(f"  [build_xlsx] ファイル読み込みエラー: {e}")
        return None

    def _try(metrics_subset: list[str]) -> Optional[dict]:
        prompt = XLSX_BUILD_PROMPT.format(
            metrics_json=json.dumps(metrics_subset, ensure_ascii=False, indent=2),
            filename=file_path.name,
            sheet_name=sheet_name,
            df_preview=df_preview[:3000],
        )
        return _call_gemini_json(prompt, XLSX_RESPONSE_SCHEMA, gemini_model, GenConfig, logger)

    # 全 metrics で試みる
    adapter = _try(metrics)

    # E: 失敗時は metrics を半分に分割して再試行
    if adapter is None and len(metrics) > 1:
        logger.info(f"  [build_xlsx] 分割リトライ: {len(metrics)}件 → 2バッチ")
        mid = len(metrics) // 2
        a1 = _try(metrics[:mid])
        a2 = _try(metrics[mid:])
        if a1 and a2:
            a1["fields"] = a1.get("fields", []) + a2.get("fields", [])
            adapter = a1
        else:
            adapter = a1 or a2

    if adapter is None:
        logger.error(f"  [build_xlsx] Gemini 失敗（分割リトライ含む）")
        return None

    # 安全弁: source は固定値で上書き
    adapter["source"] = "download"
    if adapter.get("format") not in ("xlsx", "xls", "csv"):
        adapter["format"] = file_path.suffix.lstrip(".").lower() or "xlsx"
    adapter.update({
        "ticker": ticker,
        "company_name": company_name,
        "created_at": datetime.now(JST).isoformat(),
        "created_by": GEMINI_MODEL,
        "sample_file": file_path.name,
    })
    return adapter


def build_html_table_adapter_with_gemini(
    ticker: str,
    company_name: str,
    metrics: list[str],
    file_path: Path,
    gemini_model,
    GenConfig,
    logger: logging.Logger,
) -> Optional[dict]:
    """Gemini で HTML テーブル用の抽出アダプターを構築"""
    try:
        with open(file_path, encoding="utf-8") as f:
            html = f.read()
        soup = BeautifulSoup(html, "html.parser")
        monthly_re = re.compile(
            r"月次|月度|monthly|売上速報|売上高|月別|受注速報|受注実績|販売台数|輸送実績|旅客数",
            re.IGNORECASE,
        )
        table = None
        for tbl in soup.find_all("table"):
            if monthly_re.search(tbl.get_text()):
                table = tbl
                break
        if not table:
            table = soup.find("table")
        if not table:
            logger.error(f"  [build_html] テーブルが見つかりません")
            return None

        rows_data = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if cells:
                rows_data.append(cells)
        max_cols = max(len(r) for r in rows_data) if rows_data else 1
        df = pd.DataFrame([r + [""] * (max_cols - len(r)) for r in rows_data])
        df_preview = df.head(20).fillna("").to_string(index=True, header=True)
    except Exception as e:
        logger.error(f"  [build_html] ファイル読み込みエラー: {e}")
        return None

    def _try(metrics_subset: list[str]) -> Optional[dict]:
        prompt = XLSX_BUILD_PROMPT.format(
            metrics_json=json.dumps(metrics_subset, ensure_ascii=False, indent=2),
            filename=file_path.name,
            sheet_name="HTML table",
            df_preview=df_preview[:3000],
        )
        return _call_gemini_json(prompt, XLSX_RESPONSE_SCHEMA, gemini_model, GenConfig, logger)

    adapter = _try(metrics)
    if adapter is None and len(metrics) > 1:
        logger.info(f"  [build_html] 分割リトライ: {len(metrics)}件 → 2バッチ")
        mid = len(metrics) // 2
        a1 = _try(metrics[:mid])
        a2 = _try(metrics[mid:])
        if a1 and a2:
            a1["fields"] = a1.get("fields", []) + a2.get("fields", [])
            adapter = a1
        else:
            adapter = a1 or a2

    if adapter is None:
        logger.error(f"  [build_html] Gemini 失敗")
        return None

    adapter["source"] = "non-tdnet(html_table)"
    adapter["format"] = "html"
    adapter.update({
        "ticker": ticker,
        "company_name": company_name,
        "created_at": datetime.now(JST).isoformat(),
        "created_by": GEMINI_MODEL,
        "sample_file": file_path.name,
    })
    return adapter


# ====================================================
# PDF ファイル用アダプター構築（Gemini）
# ====================================================

def build_pdf_adapter_with_gemini(
    ticker: str,
    company_name: str,
    metrics: list[str],
    pdf_paths: list[Path],
    gemini_model,
    GenConfig,
    logger: logging.Logger,
) -> Optional[dict]:
    """pdfplumber でテキスト抽出 → Gemini で TDNET テキスト用アダプターを構築"""
    try:
        import pdfplumber
    except ImportError:
        logger.error("  [build_pdf] pdfplumber がインストールされていません")
        return None

    all_text = ""
    doc_title = ""
    for pdf_path in pdf_paths:
        try:
            with pdfplumber.open(str(pdf_path)) as pdf:
                pages_text = "\n".join(
                    page.extract_text() or "" for page in pdf.pages[:5]
                )
            if pages_text.strip():
                all_text = pages_text
                doc_title = pdf_path.stem
                logger.info(f"  [build_pdf] テキスト抽出成功: {pdf_path.name} ({len(all_text)}文字)")
                break
        except Exception as e:
            logger.warning(f"  [build_pdf] {pdf_path.name} 読み込みエラー: {e}")
            continue

    if not all_text.strip():
        logger.warning("  [build_pdf] 有効なテキストなし")
        return None

    # TDNET テキスト用と同じ Gemini 呼び出し
    sample_docs = [{"full_text": all_text[:3000], "DOC_TITLE": doc_title, "SUBMISSION_DATE": ""}]
    adapter = build_tdnet_adapter_with_gemini(ticker, company_name, metrics, sample_docs, gemini_model, GenConfig, logger)
    if adapter:
        adapter["source"] = "non-tdnet(pdf)"
        adapter["format"] = "pdf"
        adapter["sample_file"] = doc_title
    return adapter


# ====================================================
# Gemini 抽出メソッド型アダプター構築（extraction_method: "gemini"）
# regex が機能しない複雑な文書向け
# ====================================================

TDNET_GEMINI_METHOD_PROMPT = """\
あなたは月次開示文書の構造解析エキスパートです。

=== 抽出したいメトリクス ===
{metrics_json}

=== サンプル文書テキスト ===
タイトル: {doc_title}
---
{sample_text}
---

=== タスク ===
上記テキストを解析し、各メトリクスの「値がどこにあるか」を自然言語で説明するJSONを生成してください。
この説明文（description）は後でAIが文書を読んで値を抽出するために使われます。

ルール:
1. nameはメトリクスリストの値と完全一致させること
2. descriptionは「○○表の○○行、○○列にある数値」のように具体的に記述すること
   - 単位（%、百万円、件、台など）も必ず含めること
   - 前年同月比（%）なのか実数なのかを明記すること
   - 例: "直営全店の売上高前年同月比（%）。表の「直営合計」または「全店計」行の売上高前年比の値。"
3. value_type: パーセント値→"percentage" / 整数（店舗数等）→"integer" / 小数→"float"
4. keyは英語スネークケース（例: all_store_sales_yoy, total_stores）
5. テキストに対応データがないメトリクスはfieldsに含めない
"""

_GEMINI_METHOD_FIELD_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "key":         {"type": "STRING"},
        "name":        {"type": "STRING"},
        "description": {"type": "STRING"},
        "value_type":  {"type": "STRING"},
        "notes":       {"type": "STRING"},
    },
    "required": ["key", "name", "description", "value_type"],
}

TDNET_GEMINI_METHOD_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "source":                 {"type": "STRING", "enum": ["tdnet"]},
        "format":                 {"type": "STRING", "enum": ["text"]},
        "extraction_method":      {"type": "STRING", "enum": ["gemini"]},
        "doc_title_pattern":      {"type": "STRING"},
        "year_from_title_regex":  {"type": "STRING"},
        "month_from_title_regex": {"type": "STRING"},
        "fields":                 {"type": "ARRAY", "items": _GEMINI_METHOD_FIELD_SCHEMA},
        "extraction_notes":       {"type": "STRING"},
    },
    "required": ["source", "format", "extraction_method", "fields"],
}


def build_gemini_method_adapter(
    ticker: str,
    company_name: str,
    metrics: list[str],
    sample_docs: list[dict],
    gemini_model,
    GenConfig,
    logger: logging.Logger,
) -> Optional[dict]:
    """extraction_method="gemini" のアダプターを生成する。

    regex に頼らず、各フィールドに自然言語の description を持たせる。
    extract_monthly_data.py の extract_from_text_gemini() がこの description を使って抽出する。
    """
    if not sample_docs:
        return None

    sample = sample_docs[0]
    full_text = (sample.get("full_text") or "")[:3000]
    doc_title = str(sample.get("DOC_TITLE", ""))

    def _try(metrics_subset: list[str]) -> Optional[dict]:
        prompt = TDNET_GEMINI_METHOD_PROMPT.format(
            metrics_json=json.dumps(metrics_subset, ensure_ascii=False, indent=2),
            doc_title=doc_title,
            sample_text=full_text,
        )
        return _call_gemini_json(prompt, TDNET_GEMINI_METHOD_RESPONSE_SCHEMA, gemini_model, GenConfig, logger)

    adapter = _try(metrics)

    if adapter is None and len(metrics) > 1:
        logger.info(f"  [build_gemini] 分割リトライ: {len(metrics)}件 → 2バッチ")
        mid = len(metrics) // 2
        a1 = _try(metrics[:mid])
        a2 = _try(metrics[mid:])
        if a1 and a2:
            a1["fields"] = a1.get("fields", []) + a2.get("fields", [])
            adapter = a1
        else:
            adapter = a1 or a2

    if adapter is None:
        logger.error(f"  [build_gemini] Gemini 失敗")
        return None

    adapter["source"] = "tdnet"
    adapter["format"] = "text"
    adapter["extraction_method"] = "gemini"
    adapter.update({
        "ticker": ticker,
        "company_name": company_name,
        "created_at": datetime.now(JST).isoformat(),
        "created_by": GEMINI_MODEL,
        "sample_doc_title": doc_title,
        "sample_submission_date": str(sample.get("SUBMISSION_DATE", "")),
    })
    return adapter


# ====================================================
# 対象銘柄選定
# ====================================================

def load_active_companies(
    tickers: Optional[list[str]] = None,
    sample: int = 30,
    all_mode: bool = False,
    bq: Optional[bigquery.Client] = None,
    gcs: Optional[storage.Client] = None,
) -> list[dict]:
    """対象銘柄を選定する。

    - --tickers 指定時: そのティッカーを直接使用（CSV/BQに存在しなくてもOK）
    - --all 指定時: GCS monthly/docs/ に文書がある全銘柄 + BQ TDNET月次全銘柄
    - 自動選定時: monthly_adapter_index.csv の active 銘柄 + BQ の TDNET月次あり銘柄を合算
    """
    # ティッカー指定時は CSV に依存しない
    if tickers:
        name_map: dict[str, str] = {}
        if INDEX_CSV.exists():
            try:
                df = pd.read_csv(INDEX_CSV, dtype=str, encoding="utf-8-sig")
                for _, row in df.iterrows():
                    name_map[str(row["ticker"])] = str(row.get("company_name", ""))
            except Exception:
                pass
        return [{"ticker": t, "company_name": name_map.get(t, t), "type": "unknown"} for t in tickers]

    # CSV の会社名マップを作成（会社名補完用）
    name_map_full: dict[str, dict] = {}
    if INDEX_CSV.exists():
        try:
            df = pd.read_csv(INDEX_CSV, dtype=str, encoding="utf-8-sig")
            for _, row in df.iterrows():
                t = str(row["ticker"])
                name_map_full[t] = {"ticker": t, "company_name": str(row.get("company_name", t)), "type": str(row.get("type", ""))}
        except Exception:
            pass

    companies: dict[str, dict] = {}

    if all_mode:
        # --all: GCS monthly/docs/ に文書がある全銘柄を列挙
        if gcs:
            bucket = gcs.bucket(GCS_BUCKET)
            # monthly/docs/ 配下のプレフィックスを列挙（tier=0 のみ = {ticker}/ 直下）
            seen_tickers: set[str] = set()
            for blob in bucket.list_blobs(prefix=f"{GCS_DOCS}/", delimiter="/"):
                pass  # delimiter を使うと prefixes に tier-1 フォルダが出る
            # delimiter モードで prefix 一覧を取得
            iterator = bucket.list_blobs(prefix=f"{GCS_DOCS}/", delimiter="/")
            list(iterator)  # 完全に消費して prefixes を確定
            for prefix in iterator.prefixes:
                # prefix = "monthly/docs/1234/"
                parts = prefix.rstrip("/").split("/")
                if len(parts) == 3:
                    t = parts[2]
                    if t and t not in seen_tickers:
                        seen_tickers.add(t)
                        info = name_map_full.get(t, {"ticker": t, "company_name": t, "type": "gcs_only"})
                        companies[t] = info

        # BQ の TDNET月次あり銘柄も追加
        if bq:
            try:
                sql = f"""
                SELECT DISTINCT TICKER
                FROM `{GCP_PROJECT}.{BQ_DATASET}.TDNET_DOCUMENTS_ENHANCED`
                WHERE (
                  MAIN_CATEGORY = '月次開示'
                  OR EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) AS sc WHERE sc = '月次開示')
                )
                """
                tdnet_df = bq.query(sql).to_dataframe()
                for _, row in tdnet_df.iterrows():
                    t = str(row["TICKER"])
                    if t not in companies:
                        info = name_map_full.get(t, {"ticker": t, "company_name": t, "type": "tdnet_only"})
                        companies[t] = info
            except Exception:
                pass

        return list(companies.values())

    # 通常モード: BQ の TDNET月次あり銘柄を優先選定
    name_map = {k: v for k, v in name_map_full.items() if v.get("type") in ("active", "")}
    if bq:
        try:
            sql = f"""
            SELECT TICKER, COUNT(*) as cnt
            FROM `{GCP_PROJECT}.{BQ_DATASET}.TDNET_DOCUMENTS_ENHANCED`
            WHERE (
              MAIN_CATEGORY = '月次開示'
              OR EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) AS sc WHERE sc = '月次開示')
            )
            GROUP BY TICKER
            ORDER BY cnt DESC
            LIMIT {sample * 3}
            """
            tdnet_df = bq.query(sql).to_dataframe()
            for _, row in tdnet_df.iterrows():
                t = str(row["TICKER"])
                info = name_map_full.get(t, {"ticker": t, "company_name": t, "type": "tdnet_only"})
                companies[t] = info
        except Exception:
            pass

    # 残りを CSV active 銘柄（ローカルファイルあり）で補完
    for t, info in name_map.items():
        if len(companies) >= sample:
            break
        if t not in companies:
            patterns = list(MONTHLYIR_DIR.glob(f"{t}_*/"))
            if patterns:
                companies[t] = info

    result = list(companies.values())[:sample]
    return result


# ====================================================
# Phase 1: アダプター構築
# ====================================================

def phase_build(
    companies: list[dict],
    bq: bigquery.Client,
    gcs: storage.Client,
    gemini_model,
    GenConfig,
    no_gcs: bool,
    logger: logging.Logger,
    rebuild: bool = False,
    gemini_method: bool = False,
) -> dict:
    results = {"success": [], "skip": [], "fail": []}

    for i, company in enumerate(companies, 1):
        ticker = company["ticker"]
        name = company.get("company_name", ticker)
        logger.info(f"[{i}/{len(companies)}] {ticker} {name}")

        # structure.json 取得
        structure = gcs_read_json(gcs, f"{GCS_META}/{ticker}/structure.json")
        if not structure:
            logger.info(f"  → structure.json なし → スキップ")
            results["skip"].append(ticker)
            continue

        metrics = parse_structure_metrics(structure)
        if not metrics:
            logger.info(f"  → メトリクス定義なし → スキップ")
            results["skip"].append(ticker)
            continue
        logger.debug(f"  メトリクス {len(metrics)} 件: {metrics[:3]}")

        # 既存アダプターチェック（--rebuild なければスキップ）
        adapter_gcs_path = f"{GCS_META}/{ticker}/extract_adapter.json"
        existing = gcs_read_json(gcs, adapter_gcs_path)
        if existing and not rebuild:
            logger.info(f"  → 既存アダプターあり ({existing.get('created_at','?')}) → スキップ")
            results["skip"].append(ticker)
            continue
        # manual_override: true のアダプターは rebuild でもスキップ
        if existing and existing.get("manual_override"):
            logger.info(f"  → manual_override=true → スキップ")
            results["skip"].append(ticker)
            continue

        adapter = None

        # 疑わしい文書パターン（月次でない可能性のある文書を検出・記録するだけ。処理は続行）
        MONTHLY_KEYWORDS = re.compile(r"月次|月度|monthly|売上速報|売上高速報|受注速報|受注実績|月別|旅客|輸送実績|稼働率|販売台数|直営", re.IGNORECASE)

        # --- TDNET テキスト用アダプター ---
        sample_docs = get_tdnet_docs(ticker, bq, since=2020, limit=3)
        if sample_docs:
            # 疑わしい文書チェック
            suspect = [d["DOC_TITLE"] for d in sample_docs if not MONTHLY_KEYWORDS.search(d.get("DOC_TITLE", ""))]
            if suspect:
                logger.warning(f"  [要確認] TDNET文書タイトルが月次に見えない: {suspect[:2]}")
            if gemini_method:
                logger.info(f"  TDNET サンプル {len(sample_docs)} 件 → Gemini でアダプター構築（gemini-method）")
                adapter = build_gemini_method_adapter(ticker, name, metrics, sample_docs, gemini_model, GenConfig, logger)
            else:
                logger.info(f"  TDNET サンプル {len(sample_docs)} 件 → Gemini でアダプター構築")
                adapter = build_tdnet_adapter_with_gemini(ticker, name, metrics, sample_docs, gemini_model, GenConfig, logger)
            if adapter:
                adapter["fields_count"] = len(adapter.get("fields", []))
                logger.info(f"  → TDNET アダプター生成: {adapter['fields_count']} フィールド")

        # --- ダウンロードファイル用アダプター（ローカル → GCS フォールバック） ---
        if not adapter:
            spreadsheet_path: Optional[Path] = None
            html_path: Optional[Path] = None

            # ローカルファイルを優先検索
            patterns = list(MONTHLYIR_DIR.glob(f"{ticker}_*/"))
            if patterns:
                company_dir = patterns[0]
                files = sorted(company_dir.glob("*.xlsx")) + sorted(company_dir.glob("*.xls")) + sorted(company_dir.glob("*.csv"))
                if files:
                    spreadsheet_path = files[-1]
                html_list = sorted(company_dir.glob("*_monthly_table.html"))
                if html_list:
                    html_path = html_list[-1]

            # ローカルになければ GCS から取得
            if not spreadsheet_path and not html_path:
                spreadsheet_path, html_path, pdf_paths = gcs_download_monthly_files(gcs, ticker)
                if spreadsheet_path or html_path or pdf_paths:
                    logger.info(f"  GCS から {GCS_DOCS}/{ticker}/ をDL")
            else:
                pdf_paths = []

            if spreadsheet_path:
                if not MONTHLY_KEYWORDS.search(spreadsheet_path.name):
                    logger.warning(f"  [要確認] ファイル名が月次に見えない: {spreadsheet_path.name}")
                logger.info(f"  ダウンロードファイルあり: {spreadsheet_path.name} → Gemini でアダプター構築")
                adapter = build_xlsx_adapter_with_gemini(ticker, name, metrics, spreadsheet_path, gemini_model, GenConfig, logger)
                if adapter:
                    logger.info(f"  → XLSX アダプター生成: {len(adapter.get('fields', []))} フィールド")

            # HTML テーブルファイルを検索（XLSX/CSV になければ）
            if not adapter and html_path:
                logger.info(f"  HTML テーブルファイルあり: {html_path.name} → Gemini でアダプター構築")
                adapter = build_html_table_adapter_with_gemini(ticker, name, metrics, html_path, gemini_model, GenConfig, logger)
                if adapter:
                    logger.info(f"  → HTML テーブルアダプター生成: {len(adapter.get('fields', []))} フィールド")

            # PDF フォールバック（XLSX/HTML がなければ）
            if not adapter and pdf_paths:
                suspect_pdfs = [p.name for p in pdf_paths if not MONTHLY_KEYWORDS.search(p.name)]
                if suspect_pdfs:
                    logger.warning(f"  [要確認] PDFファイル名が月次に見えない: {suspect_pdfs[:2]}")
                logger.info(f"  PDFファイル {len(pdf_paths)} 件 → pdfplumber + Gemini でアダプター構築")
                adapter = build_pdf_adapter_with_gemini(ticker, name, metrics, pdf_paths, gemini_model, GenConfig, logger)
                if adapter:
                    logger.info(f"  → PDF アダプター生成: {len(adapter.get('fields', []))} フィールド")

        if not adapter:
            logger.warning(f"  → アダプター構築失敗（TDNET文書なし・ローカルファイルなし）")
            results["fail"].append(ticker)
            continue

        # 手動修正フィールドを既存アダプターからマージ（rebuild 時の上書き防止）
        _MANUAL_FIELD_KEYS = {"yoy_offset", "unit_scale", "bc_floor", "bc_ignore",
                              "bc_ignore_reason", "_calc", "_table_match_skip_prefix"}
        _MANUAL_ADAPTER_KEYS = {"month_direction", "overwrite_past_months", "manual_override"}
        if existing:
            # アダプターレベルの手動フラグを引き継ぐ
            for mk in _MANUAL_ADAPTER_KEYS:
                if mk in existing and mk not in adapter:
                    adapter[mk] = existing[mk]
            # フィールドレベルの手動修正を引き継ぐ
            old_fields = {(f.get("key") or f.get("name", "")): f for f in existing.get("fields", [])}
            for new_f in adapter.get("fields", []):
                fk = new_f.get("key") or new_f.get("name", "")
                old_f = old_fields.get(fk)
                if old_f:
                    for mk in _MANUAL_FIELD_KEYS:
                        if mk in old_f:
                            new_f[mk] = old_f[mk]
                    # description が手動で上書きされていた場合も引き継ぐ
                    if old_f.get("_manual_description"):
                        new_f["description"] = old_f["description"]
                        new_f["_manual_description"] = True

        # bc_key 自動付与: Gemini 生成の各 field に bc_key = name を埋める
        # （structure.json.monthly_items[*].name が BC 正名 = Gemini に渡した metrics 名 = field.name）
        # key は adapter 固有の命名、bc_key は BC 突合用の明示マッピング、という役割分離を復元する
        for _f in adapter.get("fields", []):
            if "bc_key" not in _f:
                _bc_name = _f.get("name") or _f.get("key")
                if _bc_name:
                    _f["bc_key"] = _bc_name

        # month_direction 自動判定（未設定の場合のみ、GCS PDFから判定）
        if "month_direction" not in adapter:
            try:
                pdf_bytes = _fetch_latest_tdnet_pdf(gcs, ticker, logger)
                if pdf_bytes:
                    direction = _detect_month_direction(pdf_bytes)
                    if direction == "row":
                        adapter["month_direction"] = "row"
                        logger.info(f"  → month_direction='row' を自動設定（PDF テーブル構造から判定）")
                    else:
                        logger.debug(f"  → month_direction=column（デフォルト、設定不要）")
                else:
                    logger.debug(f"  → tdnet/{ticker}/ に PDF なし → month_direction 判定スキップ")
            except Exception:
                logger.warning(f"  → month_direction 判定エラー（スキップ）: {traceback.format_exc()}")

        # 保存
        _save_adapter(gcs, adapter_gcs_path, adapter, ticker, no_gcs, logger)
        results["success"].append(ticker)
        time.sleep(0.5)  # Gemini API レートリミット対策

    logger.info(f"アダプター構築: 成功={len(results['success'])} スキップ={len(results['skip'])} 失敗={len(results['fail'])}")
    logger.info(f"  成功銘柄: {results['success']}")
    return results


def _detect_month_direction(pdf_bytes: bytes) -> str:
    """PDFテーブル構造から月の配置方向を判定する.

    Args:
        pdf_bytes: PDF ファイルのバイナリデータ

    Returns:
        "row" (月が行ラベル) or "column" (月が列ヘッダー, デフォルト)
    """
    import io
    import unicodedata

    import pdfplumber

    def _n(s: str) -> str:
        s = unicodedata.normalize("NFKC", s).replace("\u3000", " ").replace("\n", " ").strip()
        return s

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for tbl in (page.extract_tables() or []):
                if len(tbl) < 3:
                    continue
                # ヘッダー行（先頭3行）に月列が2つ以上あれば COL_MONTH
                for row in tbl[:3]:
                    cells = [_n(c or "") for c in row]
                    if sum(1 for c in cells if re.match(r"\d{1,2}月$", c)) >= 2:
                        return "column"
                # 行ラベル（col0）に月パターンがあれば ROW_MONTH
                for row in tbl[2:]:
                    c0 = _n(row[0] or "") if row else ""
                    if re.search(r"\d{1,2}月", c0) and not re.search(r"四半期|累計|合計", c0):
                        return "row"
    return "column"


def _fetch_latest_tdnet_pdf(gcs, ticker: str, logger) -> Optional[bytes]:
    """GCS tdnet/{ticker}/ から最新の月次PDFを1件取得する.

    Args:
        gcs: GCS storage client
        ticker: 銘柄コード
        logger: ロガー

    Returns:
        PDF バイナリデータ。なければ None
    """
    prefix = f"tdnet/{ticker}/"
    bucket = gcs.bucket(GCS_BUCKET)
    blobs = sorted(
        (b for b in bucket.list_blobs(prefix=prefix) if b.name.lower().endswith(".pdf")),
        key=lambda b: b.name,
        reverse=True,
    )
    if not blobs:
        return None
    blob = blobs[0]
    logger.debug(f"  month_direction 判定用 PDF: {blob.name}")
    return blob.download_as_bytes()


def _save_adapter(gcs, gcs_path, adapter, ticker, no_gcs, logger):
    if no_gcs:
        local = TMP_DIR / f"extract_adapter_{ticker}.json"
        local.write_text(json.dumps(adapter, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"  ✅ ローカル保存: {local}")
    else:
        gcs_write_json(gcs, gcs_path, adapter)
        logger.info(f"  ✅ GCS保存: gs://{GCS_BUCKET}/{gcs_path}")


# ====================================================
# メイン
# ====================================================

def main():
    parser = argparse.ArgumentParser(description="月次データ抽出アダプター構築（Phase 1）")
    parser.add_argument("--tickers", nargs="+", help="対象ティッカー（省略時は --sample で自動選定）")
    parser.add_argument("--sample", type=int, default=30, help="自動選定する銘柄数（デフォルト30）")
    parser.add_argument("--all", action="store_true", dest="all_mode",
                        help="GCS monthly/docs/ に文書がある全銘柄 + BQ TDNET月次全銘柄を対象（--tickers/--sample を無視）")
    parser.add_argument("--no-gcs", action="store_true", help="GCS 保存スキップ（data/tmp/ にローカル保存）")
    parser.add_argument("--rebuild", action="store_true", help="既存アダプターがあっても再構築する")
    parser.add_argument("--gemini-method", action="store_true", dest="gemini_method",
                        help="extraction_method=gemini のアダプターを生成する（regex の代わりに自然言語 description を使う）")
    args = parser.parse_args()

    # カンマ区切りで渡された場合に対応（例: --tickers "1379,138A,1417"）
    if args.tickers and len(args.tickers) == 1 and "," in args.tickers[0]:
        args.tickers = [t.strip() for t in args.tickers[0].split(",") if t.strip()]

    logger = setup_logging()
    logger.info(f"=== build_monthly_extractor 開始 ===")

    # クライアント初期化
    gcs = get_gcs()
    bq = get_bq()
    logger.info("Gemini モデル初期化中...")
    gemini_model, GenConfig = get_gemini()
    logger.info(f"  モデル: {GEMINI_MODEL}")

    # 対象銘柄選定
    companies = load_active_companies(
        tickers=args.tickers,
        sample=args.sample,
        all_mode=args.all_mode,
        bq=bq,
        gcs=gcs if args.all_mode else None,
    )
    logger.info(f"対象: {len(companies)} 社")
    for c in companies[:5]:
        logger.debug(f"  {c['ticker']} {c['company_name']} ({c.get('type','')})")

    # Phase 1: アダプター構築
    logger.info("--- Phase 1: アダプター構築 ---")
    if args.gemini_method:
        logger.info("  モード: extraction_method=gemini（自然言語 description）")
    phase_build(companies, bq, gcs, gemini_model, GenConfig, args.no_gcs, logger,
                rebuild=args.rebuild, gemini_method=args.gemini_method)

    logger.info("=== 完了 ===")


if __name__ == "__main__":
    main()

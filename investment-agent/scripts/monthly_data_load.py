"""月次情報データ ロードスクリプト（v2）

TDnet BQ（TDNET_DOCUMENTS_ENHANCED）から月次開示文書を取得し、
Gemini で数値を抽出して GCS に構造化 JSON として保存する。

数値取得方法はアダプティブ設計（企業ごとに structure.json に記録）:
  A   : BQ の chunk_texts からテキストを取得して Gemini に渡す
  B   : GCS の PDF を直接読み込んで Gemini に渡す
  A+B : 両方のテキストを結合して Gemini に渡す

  試行順序:
    初回・記録="A": A → B → A+B
    記録="B"      : B → A → A+B
    記録="A+B"    : A+B のみ

GCS 保存先:
  gs://stock_data_1930932/monthly/meta/_progress.json
  gs://stock_data_1930932/monthly/record/{ticker}/{yyyy-mm}.json

実行:
  PYTHONUTF8=1 python scripts/monthly_data_load.py
  PYTHONUTF8=1 python scripts/monthly_data_load.py --ticker 7049 3048
  PYTHONUTF8=1 python scripts/monthly_data_load.py --dry-run --ticker 7049
  PYTHONUTF8=1 python scripts/monthly_data_load.py --from 2025-01 --to 2026-03

TODO（未実装）:
  - TDNET 非開示企業の自動検出 → IR HP からの取得
    検出方法: TDNET_DOCUMENTS_ENHANCED に ticker のレコードが存在しない、
             または一定期間新規データがない場合に tdnet_available=false を
             structure.json に記録し、次回以降 IR HP アクセスに切り替える
"""
from __future__ import annotations

import io
import json
import os
import re
import argparse
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

JST = timezone(timedelta(hours=9), "JST")

# ─────────────────────────────────────────────────────────────────────────────
# 設定
# ─────────────────────────────────────────────────────────────────────────────
GCS_BUCKET      = "stock_data_1930932"
GCS_META        = "monthly/meta"
GCS_RECORD      = "monthly/record"
PROGRESS_PATH   = f"{GCS_META}/_progress.json"
BQ_PROJECT      = "gmailpj-357912"
BQ_TABLE        = f"{BQ_PROJECT}.STOCK.TDNET_DOCUMENTS_ENHANCED"
GEMINI_LOCATION = "us-central1"
GEMINI_MODEL    = "gemini-2.0-flash-001"
LOCAL_KEY_FILE  = os.path.join(os.path.dirname(__file__), "..", "keys", "gcp-service-account.json")

# Gemini に渡すテキストの最大文字数
GEMINI_TEXT_LIMIT = 8000

# 抽出成功とみなす最低取得項目数
EXTRACTION_MIN_ITEMS = 1


# ─────────────────────────────────────────────────────────────────────────────
# ログ
# ─────────────────────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    print(f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# 実行環境判別・認証
# ─────────────────────────────────────────────────────────────────────────────
def _detect_runtime() -> str:
    if os.environ.get("CLOUD_RUN_JOB"):
        return "cloudrun"
    try:
        import google.colab  # noqa: F401
        return "colab_enterprise" if os.environ.get("GOOGLE_CLOUD_PROJECT") else "colab_personal"
    except ImportError:
        return "local"


def _get_credentials():
    rt = _detect_runtime()
    if rt == "colab_personal":
        from google.colab import userdata
        from google.oauth2 import service_account
        key_info = json.loads(userdata.get("GCP_SA_KEY"))
        return service_account.Credentials.from_service_account_info(key_info)
    elif rt in ("colab_enterprise", "cloudrun"):
        import google.auth
        creds, _ = google.auth.default()
        return creds
    else:
        from google.oauth2 import service_account
        return service_account.Credentials.from_service_account_file(LOCAL_KEY_FILE)


def get_bq():
    from google.cloud import bigquery
    return bigquery.Client(project=BQ_PROJECT, credentials=_get_credentials())


def get_gcs():
    from google.cloud import storage
    return storage.Client(project=BQ_PROJECT, credentials=_get_credentials())


# ─────────────────────────────────────────────────────────────────────────────
# 進捗管理（_progress.json）
# ─────────────────────────────────────────────────────────────────────────────
def load_progress(gcs) -> dict:
    blob = gcs.bucket(GCS_BUCKET).blob(PROGRESS_PATH)
    if blob.exists():
        data = json.loads(blob.download_as_text(encoding="utf-8"))
        log(f"進捗ファイル読み込み: {len(data.get('loaded_keys', []))} 件ロード済み")
        return data
    log("進捗ファイルなし → 新規作成")
    return {
        "schema_version": 2,
        "updated_at": "",
        "note": "loaded_keys から行を削除するとその月がリランされます。形式: ticker/yyyy-mm",
        "loaded_keys": [],
        "metadata": {},
    }


def save_progress(gcs, progress: dict) -> None:
    progress["updated_at"] = datetime.now(JST).isoformat()
    progress["loaded_keys"] = sorted(set(progress["loaded_keys"]))
    blob = gcs.bucket(GCS_BUCKET).blob(PROGRESS_PATH)
    blob.upload_from_string(
        json.dumps(progress, ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8",
    )
    log(f"進捗ファイル保存: {len(progress['loaded_keys'])} 件")


def progress_key(ticker: str, yyyymm: str) -> str:
    return f"{ticker}/{yyyymm}"


# ─────────────────────────────────────────────────────────────────────────────
# YYYYMM 抽出
# ─────────────────────────────────────────────────────────────────────────────
_YYYYMM_PATTERNS = [
    re.compile(r"(\d{4})年\s*(\d{1,2})\s*月"),
    re.compile(r"(\d{4})[/\-](\d{1,2})"),
    re.compile(r"(\d{2})年\s*(\d{1,2})\s*月"),
]


def extract_yyyymm(doc_title: str, submission_date: str) -> Optional[str]:
    for pat in _YYYYMM_PATTERNS:
        m = pat.search(doc_title or "")
        if m:
            year = int(m.group(1))
            month = int(m.group(2))
            if year < 100:
                year += 2000
            if 1 <= month <= 12:
                return f"{year:04d}-{month:02d}"
    if submission_date:
        try:
            dt = datetime.fromisoformat(str(submission_date))
            if dt.month == 1:
                return f"{dt.year - 1:04d}-12"
            return f"{dt.year:04d}-{dt.month - 1:02d}"
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# structure.json 管理
# ─────────────────────────────────────────────────────────────────────────────
def load_structure(gcs, ticker: str) -> dict:
    blob = gcs.bucket(GCS_BUCKET).blob(f"{GCS_META}/{ticker}/structure.json")
    if blob.exists():
        return json.loads(blob.download_as_text(encoding="utf-8"))
    return {}


def save_structure(gcs, ticker: str, structure: dict) -> None:
    structure["extraction_updated_at"] = datetime.now(JST).isoformat()
    blob = gcs.bucket(GCS_BUCKET).blob(f"{GCS_META}/{ticker}/structure.json")
    blob.upload_from_string(
        json.dumps(structure, ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8",
    )


# ─────────────────────────────────────────────────────────────────────────────
# BQ クエリ
# ─────────────────────────────────────────────────────────────────────────────
def fetch_monthly_docs(
    bq,
    from_yyyymm: Optional[str],
    to_yyyymm: Optional[str],
    tickers: Optional[list[str]],
) -> list[dict]:
    from google.cloud import bigquery as bq_module

    date_filter   = ""
    ticker_filter = ""
    params: list  = []

    if from_yyyymm:
        from_date = from_yyyymm.replace("-", "") + "01"
        date_filter += " AND SUBMISSION_DATE >= @from_date"
        params.append(bq_module.ScalarQueryParameter("from_date", "DATE", from_date))
    if to_yyyymm:
        y, m = int(to_yyyymm[:4]), int(to_yyyymm[5:7])
        m += 1
        if m > 12:
            m, y = 1, y + 1
        to_date = f"{y:04d}{m:02d}01"
        date_filter += " AND SUBMISSION_DATE < @to_date"
        params.append(bq_module.ScalarQueryParameter("to_date", "DATE", to_date))
    if tickers:
        placeholders = ", ".join(f"@ticker_{i}" for i in range(len(tickers)))
        ticker_filter = f" AND TICKER IN ({placeholders})"
        for i, t in enumerate(tickers):
            params.append(bq_module.ScalarQueryParameter(f"ticker_{i}", "STRING", t))

    q = f"""
    SELECT
      TICKER,
      FILER_NAME,
      SUBMISSION_DATE,
      DOC_TITLE,
      FILE_NAME,
      ARRAY_AGG(CHUNK_TEXT) AS chunk_texts
    FROM `{BQ_TABLE}`
    WHERE MAIN_CATEGORY LIKE '%月次開示%'
      AND TICKER IS NOT NULL
      AND FILE_NAME IS NOT NULL
      {date_filter}
      {ticker_filter}
    GROUP BY TICKER, FILER_NAME, SUBMISSION_DATE, DOC_TITLE, FILE_NAME
    ORDER BY SUBMISSION_DATE DESC
    """
    job_config = bq_module.QueryJobConfig(query_parameters=params)
    rows = list(bq.query(q, job_config=job_config).result())
    log(f"BQ クエリ結果: {len(rows)} 文書")
    return [
        {
            "ticker":          row.TICKER,
            "name":            row.FILER_NAME,
            "submission_date": str(row.SUBMISSION_DATE),
            "doc_title":       row.DOC_TITLE or "",
            "file_name":       row.FILE_NAME or "",
            "chunk_texts":     [t for t in (row.chunk_texts or []) if t],
        }
        for row in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Gemini 数値抽出
# ─────────────────────────────────────────────────────────────────────────────
_EXTRACTION_PROMPT = """\
以下は月次開示資料のテキストです。
企業名: {name}
対象月: {yyyymm}

【抽出対象項目】
{items_text}

【資料テキスト】
{text}

上記テキストから各項目の数値を抽出し、以下のJSON形式のみで返してください。
コードブロック・説明文は不要です。純粋なJSONのみ返してください。
値が見つからない場合はnullとしてください。
前年比（%）が記載されていれば yoy_pct に入れてください（例: 103.2）。

{{
  "項目名": {{"value": 数値またはnull, "unit": "単位文字列", "yoy_pct": 数値またはnull}},
  ...
}}
"""


def _gemini_extract(
    text: str,
    name: str,
    yyyymm: str,
    monthly_items: list[dict],
) -> dict:
    """Gemini でテキストから月次数値を抽出する。"""
    from google import genai

    if not monthly_items:
        return {}

    items_text = "\n".join(
        f"  - {item['name']}（{item.get('unit', '')}）" for item in monthly_items
    )
    prompt = _EXTRACTION_PROMPT.format(
        name=name,
        yyyymm=yyyymm,
        items_text=items_text,
        text=text[:GEMINI_TEXT_LIMIT],
    )

    rt = _detect_runtime()
    if rt in ("cloudrun", "colab_enterprise"):
        client = genai.Client(vertexai=True, project=BQ_PROJECT, location=GEMINI_LOCATION)
    else:
        client = genai.Client(vertexai=True, project=BQ_PROJECT, location=GEMINI_LOCATION, credentials=_get_credentials())

    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    raw = re.sub(r"```(?:json)?\s*", "", response.text.strip()).strip("`").strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group())
    except Exception:
        return {}


def _is_extraction_success(extracted: dict, monthly_items: list[dict]) -> bool:
    if not monthly_items:
        return bool(extracted)
    found = sum(
        1 for item in monthly_items
        if item["name"] in extracted
        and extracted[item["name"]].get("value") is not None
    )
    return found >= EXTRACTION_MIN_ITEMS


# ─────────────────────────────────────────────────────────────────────────────
# PDF テキスト取得（Method B）
# ─────────────────────────────────────────────────────────────────────────────
def _fetch_pdf_text(gcs, ticker: str, file_name: str) -> str:
    """GCS から PDF をダウンロードしてテキストを抽出する。"""
    import pdfplumber

    fn = file_name if file_name.endswith(".pdf") else f"{file_name}.pdf"
    gcs_path = f"tdnet/{ticker}/{fn}"
    blob = gcs.bucket(GCS_BUCKET).blob(gcs_path)
    if not blob.exists():
        log(f"  PDF が見つかりません: gs://{GCS_BUCKET}/{gcs_path}")
        return ""
    try:
        pdf_bytes = blob.download_as_bytes()
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as e:
        log(f"  PDF 読み込みエラー: {e}")
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# アダプティブ抽出
# ─────────────────────────────────────────────────────────────────────────────
def _method_sequence(recorded: Optional[str]) -> list[str]:
    """記録済みの取得方法に基づき試行順序を返す。"""
    if recorded == "B":
        return ["B", "A", "A+B"]
    elif recorded == "A+B":
        return ["A+B"]
    else:  # "A" or None（初回）
        return ["A", "B", "A+B"]


def _build_text(gcs, ticker: str, docs_list: list[dict], method: str) -> str:
    """指定方法でテキストを取得・結合する。"""
    text = ""
    if "A" in method:
        for d in docs_list:
            text += "\n".join(d["chunk_texts"]) + "\n"
    if "B" in method:
        for d in docs_list:
            text += _fetch_pdf_text(gcs, ticker, d["file_name"]) + "\n"
    return text.strip()


def extract_monthly_data(
    gcs,
    ticker: str,
    name: str,
    yyyymm: str,
    docs_list: list[dict],
    monthly_items: list[dict],
    recorded_method: Optional[str],
) -> tuple[dict, str]:
    """
    アダプティブ方式で月次数値を抽出する。

    Returns:
        (extracted_data, used_method)
        extracted_data : {項目名: {value, unit, yoy_pct}, ...}
        used_method    : "A" / "B" / "A+B"
    """
    sequence = _method_sequence(recorded_method)

    for method in sequence:
        log(f"  抽出方法: {method}")
        text = _build_text(gcs, ticker, docs_list, method)

        if not text:
            log(f"  テキスト取得失敗（{method}）")
            continue

        extracted = _gemini_extract(text, name, yyyymm, monthly_items)

        if _is_extraction_success(extracted, monthly_items):
            n = sum(1 for v in extracted.values() if isinstance(v, dict) and v.get("value") is not None)
            log(f"  抽出成功: {n} 項目 / {len(monthly_items)} 項目")
            return extracted, method
        else:
            log(f"  抽出不十分（{method}）→ 次の方法へ")

    log("  全方法で抽出失敗。空データで記録")
    return {}, recorded_method or "A"


# ─────────────────────────────────────────────────────────────────────────────
# GCS 保存
# ─────────────────────────────────────────────────────────────────────────────
def save_month_data(gcs, ticker: str, yyyymm: str, data: dict) -> None:
    path = f"{GCS_RECORD}/{ticker}/{yyyymm}.json"
    blob = gcs.bucket(GCS_BUCKET).blob(path)
    blob.upload_from_string(
        json.dumps(data, ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8",
    )
    log(f"  → GCS: gs://{GCS_BUCKET}/{path}")


# ─────────────────────────────────────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="月次データロード v2（Gemini 数値抽出）")
    parser.add_argument("--from",    dest="from_yyyymm", default=None,
                        help="取得開始月 (yyyy-mm)。省略時は制限なし")
    parser.add_argument("--to",      dest="to_yyyymm",   default=None,
                        help="取得終了月 (yyyy-mm)。省略時は制限なし")
    parser.add_argument("--ticker",  dest="tickers",     nargs="+",    default=None,
                        help="対象銘柄コード（複数指定可）。省略時は全企業")
    parser.add_argument("--dry-run", action="store_true",
                        help="GCS 書き込みなし・処理対象の一覧のみ表示")
    args = parser.parse_args()

    rt = _detect_runtime()
    log(f"=== 月次データロード v2 開始 (環境: {rt}) ===")
    if args.tickers:
        log(f"対象銘柄: {args.tickers}")
    if args.from_yyyymm:
        log(f"対象期間: {args.from_yyyymm} 〜 {args.to_yyyymm or '最新'}")
    if args.dry_run:
        log("*** DRY RUN モード（GCS 書き込みなし）***")

    bq  = get_bq()
    gcs = get_gcs()

    # 1. 進捗ロード
    progress = load_progress(gcs)
    loaded_set: set[str] = set(progress.get("loaded_keys", []))

    # 2. BQ から月次文書取得
    docs = fetch_monthly_docs(bq, args.from_yyyymm, args.to_yyyymm, args.tickers)

    # 3. ticker × yyyymm でグルーピング
    grouped: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    ticker_info: dict[str, dict] = {}
    skip_no_yyyymm = 0

    for doc in docs:
        ticker = doc["ticker"]
        yyyymm = extract_yyyymm(doc["doc_title"], doc["submission_date"])
        if not yyyymm:
            skip_no_yyyymm += 1
            continue
        grouped[ticker][yyyymm].append(doc)
        ticker_info[ticker] = {"name": doc["name"]}

    if skip_no_yyyymm:
        log(f"YYYYMM 抽出不可でスキップ: {skip_no_yyyymm} 文書")

    # 4. 未ロード分を絞り込み
    to_process = [
        (ticker, yyyymm, docs_list)
        for ticker, months in grouped.items()
        for yyyymm, docs_list in months.items()
        if progress_key(ticker, yyyymm) not in loaded_set
    ]
    to_process.sort(key=lambda x: (x[1], x[0]))

    log(f"処理対象: {len(to_process)} 件 / スキップ済み: {len(loaded_set)} 件")

    if args.dry_run:
        for ticker, yyyymm, dl in to_process[:20]:
            log(f"  [DRY] {ticker} {yyyymm} ({len(dl)} 文書)")
        if len(to_process) > 20:
            log(f"  ... 他 {len(to_process) - 20} 件")
        return

    success = 0
    fail    = 0

    for i, (ticker, yyyymm, docs_list) in enumerate(to_process):
        name = ticker_info[ticker]["name"]
        log(f"[{i+1}/{len(to_process)}] {ticker} {name} {yyyymm} ({len(docs_list)} 文書)")

        try:
            # structure.json 読み込み
            structure        = load_structure(gcs, ticker)
            monthly_items    = structure.get("monthly_items", [])
            recorded_method  = structure.get("extraction_method")

            # アダプティブ抽出
            docs_sorted = sorted(docs_list, key=lambda d: d["submission_date"], reverse=True)
            extracted, used_method = extract_monthly_data(
                gcs, ticker, name, yyyymm, docs_sorted, monthly_items, recorded_method
            )

            # extraction_method が変わった場合は structure.json を更新
            if used_method != recorded_method:
                structure["extraction_method"] = used_method
                save_structure(gcs, ticker, structure)
                log(f"  extraction_method 更新: {recorded_method or 'None'} → {used_method}")

            # 月次データ組み立て
            primary = docs_sorted[0]
            items_extracted = sum(
                1 for v in extracted.values()
                if isinstance(v, dict) and v.get("value") is not None
            )
            month_data = {
                "ticker":            ticker,
                "name":              name,
                "yyyymm":            yyyymm,
                "submission_date":   primary["submission_date"],
                "doc_title":         primary["doc_title"],
                "doc_count":         len(docs_list),
                "extraction_method": used_method,
                "items_extracted":   items_extracted,
                "items_total":       len(monthly_items),
                "monthly_data":      extracted,
                "loaded_at":         datetime.now(JST).isoformat(),
                "source":            "tdnet_bq",
            }

            save_month_data(gcs, ticker, yyyymm, month_data)

            # 進捗更新
            key = progress_key(ticker, yyyymm)
            loaded_set.add(key)
            progress.setdefault("metadata", {})[key] = {
                "name":              name,
                "submission_date":   primary["submission_date"],
                "doc_title":         primary["doc_title"],
                "extraction_method": used_method,
                "items_extracted":   items_extracted,
                "loaded_at":         month_data["loaded_at"],
            }
            success += 1

            # 50件ごとに中間保存（Cloud Run タイムアウト対策）
            if success % 50 == 0:
                progress["loaded_keys"] = sorted(loaded_set)
                save_progress(gcs, progress)

        except Exception as e:
            log(f"  エラー: {e}")
            fail += 1

    # 5. 進捗ファイル最終保存
    progress["loaded_keys"] = sorted(loaded_set)
    save_progress(gcs, progress)

    log(f"=== 完了: 成功 {success} 件 / 失敗 {fail} 件 ===")
    log(f"  進捗: gs://{GCS_BUCKET}/{PROGRESS_PATH}")


if __name__ == "__main__":
    main()

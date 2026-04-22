"""Gemma 4 31B TPU PoC: カテゴリ別過剰検知PDFをDropboxへコピー.

対象4カテゴリ（配当、特別損失、特別利益、子会社化・買収）について、
Gemmaが検知したがGeminiが検知しなかった過剰検知ケースを各10件サンプリングし、
GCSからPDFを取得してDropboxに保存する。

過剰検知の定義:
- Gemma側: main_cat_pred == <カテゴリ> または sub_cat_pred に <カテゴリ> 含む
- Gemini側: MAIN_CATEGORY != <カテゴリ> かつ SUB_CATEGORIES に <カテゴリ> 含まない

Usage:
    PYTHONUTF8=1 python scripts/gemma4_over_detection_pdf_copy.py
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

JST = ZoneInfo("Asia/Tokyo")

# ── 設定 ──────────────────────────────────────────
GCP_PROJECT = "gmailpj-357912"
SA_KEY_PATH = "keys/gcp-service-account.json"
BQ_TABLE = f"{GCP_PROJECT}.STOCK.TDNET_DOCUMENTS_ENHANCED"
GCS_BUCKET = "stock_data_1930932"
JSONL_PATH = Path("C:/tmp/gemma4_tpu_monthly_results.jsonl")
DATE_FROM = "2024-01-01"
DATE_TO = "2024-01-31"

TARGET_CATEGORIES = ["配当", "特別損失", "特別利益", "子会社化・買収"]
SAMPLE_COUNT = 10

TODAY_STR = datetime.now(JST).strftime("%Y%m%d")
DROPBOX_BASE = Path(
    f"C:/Users/zonekun/Dropbox/stock/temp/gemma4_4_31b_tpu_poc/over_detection_{TODAY_STR}"
)


def _get_credentials():
    from google.oauth2 import service_account
    return service_account.Credentials.from_service_account_file(
        SA_KEY_PATH,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


def _get_bq_client():
    from google.cloud import bigquery
    return bigquery.Client(
        project=GCP_PROJECT,
        credentials=_get_credentials(),
    )


def _get_gcs_bucket():
    from google.cloud import storage
    client = storage.Client(
        project=GCP_PROJECT,
        credentials=_get_credentials(),
    )
    return client.bucket(GCS_BUCKET)


def load_gemma_predictions(jsonl_path: Path) -> dict[str, dict]:
    """JSONLからGemma予測を doc_id -> record dictで読み込む."""
    preds: dict[str, dict] = {}
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            doc_id = r.get("doc_id")
            if doc_id:
                preds[doc_id] = r
    return preds


def _parse_sub_categories(raw) -> list[str]:
    """BQのSUB_CATEGORIES（STRING）を list[str] にパースする."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    s = str(raw).strip()
    if not s:
        return []
    # numpy array風の表記 "['a' 'b']" をパース
    try:
        # まず Python literal として試す
        val = ast.literal_eval(s)
        if isinstance(val, (list, tuple)):
            return [str(x) for x in val]
    except (ValueError, SyntaxError):
        pass
    # numpy風 "['a' 'b' 'c']" をカンマなしで分割
    m = re.match(r"^\[(.*)\]$", s)
    if m:
        inner = m.group(1)
        # 'xxx' or "xxx" を抽出
        parts = re.findall(r"'([^']*)'|\"([^\"]*)\"", inner)
        result = [a or b for a, b in parts]
        if result:
            return result
    return []


def fetch_bq_metadata(doc_ids: list[str]) -> dict[str, dict]:
    """BQから指定doc_idのメタデータを取得.

    Returns:
        doc_id -> {main_category, sub_categories, submission_date, doc_title,
                   filer_name, ticker, file_name}
    """
    bq_client = _get_bq_client()
    # 大量ならIN句分割だが、今回は2090件なので一発でOK
    doc_ids_str = ",".join(f"'{d}'" for d in doc_ids)
    sql = f"""
    SELECT
        DOC_ID,
        TICKER,
        FILER_NAME,
        SUBMISSION_DATE,
        MAIN_CATEGORY,
        SUB_CATEGORIES,
        DOC_TITLE,
        FILE_NAME
    FROM `{BQ_TABLE}`
    WHERE SUBMISSION_DATE BETWEEN '{DATE_FROM}' AND '{DATE_TO}'
      AND DOC_ID IN ({doc_ids_str})
    GROUP BY DOC_ID, TICKER, FILER_NAME, SUBMISSION_DATE,
             MAIN_CATEGORY, SUB_CATEGORIES, DOC_TITLE, FILE_NAME
    """
    print(f"[BQ] querying {len(doc_ids)} doc_ids...", flush=True)
    rows = list(bq_client.query(sql).result())
    result: dict[str, dict] = {}
    for row in rows:
        result[row.DOC_ID] = {
            "doc_id": row.DOC_ID,
            "ticker": row.TICKER,
            "filer_name": row.FILER_NAME or "",
            "submission_date": row.SUBMISSION_DATE,
            "main_category": row.MAIN_CATEGORY,
            "sub_categories": _parse_sub_categories(row.SUB_CATEGORIES),
            "doc_title": row.DOC_TITLE or "",
            "file_name": row.FILE_NAME or "",
        }
    print(f"[BQ] fetched {len(result)} rows", flush=True)
    return result


def is_over_detection(
    gemma_rec: dict, gemini_meta: dict, category: str,
) -> bool:
    """過剰検知判定.

    Gemma側: main_cat_pred == category or sub_cat_pred に category 含む
    Gemini側: MAIN_CATEGORY != category かつ SUB_CATEGORIES に category 含まない
    """
    gemma_main = gemma_rec.get("main_cat_pred") or ""
    gemma_subs = gemma_rec.get("sub_cat_pred") or []
    gemma_hit = (gemma_main == category) or (category in gemma_subs)
    if not gemma_hit:
        return False

    gemini_main = gemini_meta.get("main_category") or ""
    gemini_subs = gemini_meta.get("sub_categories") or []
    gemini_hit = (gemini_main == category) or (category in gemini_subs)
    return not gemini_hit


_FS_BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def sanitize_filename(s: str, max_len: int = 60) -> str:
    """ファイル名として不正な文字を_に置換し、長さを制限."""
    if not s:
        return ""
    s = _FS_BAD_CHARS.sub("_", s)
    s = s.replace("\u3000", "_").strip()
    if len(s) > max_len:
        s = s[:max_len]
    return s


def build_filename(
    category: str, gemini_meta: dict,
) -> str:
    """命名規則: <category>_過剰検知_Gemini不一致_<YYYYMMDD>_<ticker>_<shortname>_<gemini_category>_<title>_<doc_id>.pdf"""
    sub_date = gemini_meta["submission_date"]
    date_str = sub_date.strftime("%Y%m%d") if hasattr(sub_date, "strftime") else str(sub_date).replace("-", "")
    ticker = gemini_meta.get("ticker") or ""
    shortname = sanitize_filename(gemini_meta.get("filer_name") or "", 20)
    gemini_cat = sanitize_filename(gemini_meta.get("main_category") or "", 20)
    title = sanitize_filename(gemini_meta.get("doc_title") or "", 60)
    doc_id = gemini_meta.get("doc_id") or ""
    return f"{category}_過剰検知_Gemini不一致_{date_str}_{ticker}_{shortname}_{gemini_cat}_{title}_{doc_id}.pdf"


def gcs_download(bucket, gcs_path: str, local_path: Path) -> bool:
    """google-cloud-storageでGCSからPDFをDL."""
    try:
        blob = bucket.blob(gcs_path)
        if not blob.exists():
            print(f"[ERR] gcs_download not_found: {gcs_path}", flush=True)
            return False
        blob.download_to_filename(str(local_path))
        return local_path.exists() and local_path.stat().st_size > 0
    except Exception as e:
        print(f"[ERR] gcs_download exception: {gcs_path}: {e}", flush=True)
        return False


def main() -> None:
    print(f"[START] {datetime.now(JST).isoformat()}", flush=True)
    print(f"[DROPBOX] {DROPBOX_BASE}", flush=True)
    DROPBOX_BASE.mkdir(parents=True, exist_ok=True)

    # 1. JSONL読み込み
    print(f"[JSONL] loading {JSONL_PATH}", flush=True)
    gemma_preds = load_gemma_predictions(JSONL_PATH)
    print(f"[JSONL] loaded {len(gemma_preds)} records", flush=True)

    # 2. BQから同期間の全ドキュメントメタデータを取得
    #    （doc_id指定ではなく、JSONLにあるdoc_id全部）
    doc_ids = list(gemma_preds.keys())
    bq_meta = fetch_bq_metadata(doc_ids)

    # 3. 各カテゴリで過剰検知をフィルタ、doc_id昇順で先頭10件
    category_samples: dict[str, list[dict]] = {}
    for cat in TARGET_CATEGORIES:
        matched: list[tuple[str, dict, dict]] = []
        for doc_id, gemma_rec in gemma_preds.items():
            meta = bq_meta.get(doc_id)
            if meta is None:
                continue
            if is_over_detection(gemma_rec, meta, cat):
                matched.append((doc_id, gemma_rec, meta))
        matched.sort(key=lambda x: x[0])  # doc_id昇順
        print(f"[MATCH] {cat}: {len(matched)} over-detection cases total", flush=True)
        category_samples[cat] = matched[:SAMPLE_COUNT]

    # 4. GCSからPDFをDL、Dropboxへコピー
    summary_rows: dict[str, list[dict]] = {}
    copy_counts: dict[str, int] = {}

    bucket = _get_gcs_bucket()
    with tempfile.TemporaryDirectory(prefix="gemma4_pdf_") as tmpdir:
        tmpdir_path = Path(tmpdir)
        for cat, samples in category_samples.items():
            cat_dir = DROPBOX_BASE / cat
            cat_dir.mkdir(parents=True, exist_ok=True)
            count_ok = 0
            rows = []
            for doc_id, gemma_rec, meta in samples:
                file_name = meta.get("file_name") or ""
                if not file_name:
                    print(f"[SKIP] {cat} {doc_id}: FILE_NAME empty", flush=True)
                    rows.append({
                        **meta,
                        "gemma_main": gemma_rec.get("main_cat_pred"),
                        "gemma_subs": gemma_rec.get("sub_cat_pred") or [],
                        "copy_status": "NO_FILE_NAME",
                    })
                    continue

                # 一時保存 → リネームコピー
                tmp_pdf = tmpdir_path / f"{doc_id}.pdf"
                ok = gcs_download(bucket, file_name, tmp_pdf)
                if not ok:
                    rows.append({
                        **meta,
                        "gemma_main": gemma_rec.get("main_cat_pred"),
                        "gemma_subs": gemma_rec.get("sub_cat_pred") or [],
                        "copy_status": "DOWNLOAD_FAILED",
                    })
                    continue

                target_name = build_filename(cat, meta)
                target_path = cat_dir / target_name
                try:
                    shutil.copy2(tmp_pdf, target_path)
                    count_ok += 1
                    status = "OK"
                    print(f"[COPY] {cat} [{count_ok}/{len(samples)}] {target_name}", flush=True)
                except Exception as e:
                    print(f"[ERR] copy failed {target_name}: {e}", flush=True)
                    status = f"COPY_FAILED:{e}"
                rows.append({
                    **meta,
                    "gemma_main": gemma_rec.get("main_cat_pred"),
                    "gemma_subs": gemma_rec.get("sub_cat_pred") or [],
                    "copy_status": status,
                    "saved_as": target_name if status == "OK" else "",
                })
            copy_counts[cat] = count_ok
            summary_rows[cat] = rows

    # 5. _summary.md 生成
    summary_path = DROPBOX_BASE / "_summary.md"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(f"# Gemma 4 31B TPU 過剰検知サンプル ({TODAY_STR})\n\n")
        f.write(f"- 期間: {DATE_FROM} ~ {DATE_TO}\n")
        f.write(f"- JSONL: `{JSONL_PATH}`\n")
        f.write(f"- 対象カテゴリ: {', '.join(TARGET_CATEGORIES)}\n")
        f.write(f"- 過剰検知定義: Gemma検知 AND Gemini非検知\n")
        f.write(f"- サンプル方式: doc_id昇順で先頭{SAMPLE_COUNT}件\n\n")

        f.write(f"## コピー件数サマリー\n\n")
        f.write(f"| カテゴリ | コピー件数 |\n|---|---|\n")
        for cat in TARGET_CATEGORIES:
            f.write(f"| {cat} | {copy_counts.get(cat, 0)} |\n")
        f.write("\n")

        for cat in TARGET_CATEGORIES:
            f.write(f"## {cat}\n\n")
            rows = summary_rows.get(cat, [])
            if not rows:
                f.write("_該当なし_\n\n")
                continue
            f.write("| # | doc_id | ticker | filer | title | Gemma main | Gemma subs | Gemini main | Gemini subs | status |\n")
            f.write("|---|---|---|---|---|---|---|---|---|---|\n")
            for i, r in enumerate(rows, 1):
                title = (r.get("doc_title") or "").replace("|", "/")[:40]
                gemma_subs = ",".join(r.get("gemma_subs") or [])
                gemini_subs = ",".join(r.get("sub_categories") or [])
                f.write(
                    f"| {i} | {r.get('doc_id')} | {r.get('ticker')} | "
                    f"{r.get('filer_name','')[:15]} | {title} | "
                    f"{r.get('gemma_main') or ''} | {gemma_subs} | "
                    f"{r.get('main_category') or ''} | {gemini_subs} | "
                    f"{r.get('copy_status')} |\n"
                )
            f.write("\n")

    print(f"\n[SUMMARY] {summary_path}", flush=True)
    print(f"[DONE] {datetime.now(JST).isoformat()}", flush=True)
    for cat in TARGET_CATEGORIES:
        print(f"  {cat}: {copy_counts.get(cat, 0)} copied", flush=True)


if __name__ == "__main__":
    main()

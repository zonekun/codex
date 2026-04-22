"""Gemma 4 31B TPU v1 PoC: 受注高/受注残高カテゴリ プロンプトチューニング用PDF収集.

v1 推論結果(2,090件)から「受注高/受注残高」について
- detected     : Gemma=True, Gemini=True
- missed       : Gemma=False, Gemini=True
- over_detected: Gemma=True, Gemini=False
を各20件(doc_id昇順)サンプリングし、GCSからPDFを取得して
Dropboxに保存 + pdfplumberで本文抽出(最大30ページ・25,000文字)→ JSON化。

Usage:
    PYTHONUTF8=1 python scripts/gemma4_juchu_tuning_pdf_copy.py
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

TARGET_CATEGORY = "受注高/受注残高"
SAMPLE_COUNT = 20
PDF_MAX_PAGES = 30
PDF_MAX_CHARS = 25000

TODAY_STR = "20260416"
DROPBOX_BASE = Path(
    f"C:/Users/zonekun/Dropbox/stock/temp/gemma4_4_31b_tpu_poc/tuning_juchu_{TODAY_STR}"
)
KINDS = ("detected", "missed", "over_detected")
KIND_LABEL = {
    "detected": "検出",
    "missed": "漏れ",
    "over_detected": "過剰",
}
KIND_TAG = {
    "detected": "Gemma=T_Gemini=T",
    "missed": "Gemma=F_Gemini=T",
    "over_detected": "Gemma=T_Gemini=F",
}


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
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    s = str(raw).strip()
    if not s:
        return []
    try:
        val = ast.literal_eval(s)
        if isinstance(val, (list, tuple)):
            return [str(x) for x in val]
    except (ValueError, SyntaxError):
        pass
    m = re.match(r"^\[(.*)\]$", s)
    if m:
        inner = m.group(1)
        parts = re.findall(r"'([^']*)'|\"([^\"]*)\"", inner)
        result = [a or b for a, b in parts]
        if result:
            return result
    return []


def fetch_bq_metadata(doc_ids: list[str]) -> dict[str, dict]:
    bq_client = _get_bq_client()
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
        sub_cats = _parse_sub_categories(row.SUB_CATEGORIES)
        result[row.DOC_ID] = {
            "doc_id": row.DOC_ID,
            "ticker": row.TICKER,
            "short_name": row.FILER_NAME or "",
            "submission_date": row.SUBMISSION_DATE,
            "main_category": row.MAIN_CATEGORY,
            "sub_categories": sub_cats,
            "doc_title": row.DOC_TITLE or "",
            "file_name": row.FILE_NAME or "",
        }
    print(f"[BQ] fetched {len(result)} rows", flush=True)
    return result


def gemma_hit(gemma_rec: dict) -> bool:
    gemma_main = gemma_rec.get("main_cat_pred") or ""
    gemma_subs = gemma_rec.get("sub_cat_pred") or []
    return (gemma_main == TARGET_CATEGORY) or (TARGET_CATEGORY in gemma_subs)


def gemini_hit(gemini_meta: dict) -> bool:
    gemini_main = gemini_meta.get("main_category") or ""
    gemini_subs = gemini_meta.get("sub_categories") or []
    return (gemini_main == TARGET_CATEGORY) or (TARGET_CATEGORY in gemini_subs)


_FS_BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def sanitize_filename(s: str, max_len: int = 60) -> str:
    if not s:
        return ""
    s = _FS_BAD_CHARS.sub("_", s)
    s = s.replace("\u3000", "_").strip()
    if len(s) > max_len:
        s = s[:max_len]
    return s


def build_filename(kind: str, gemini_meta: dict) -> str:
    """受注_<検出|漏れ|過剰>_Gemma=<>_Gemini=<>_<YYYYMMDD>_<ticker>_<shortname>_<gemini_MAIN>_<title60>_<doc_id>.pdf"""
    label = KIND_LABEL[kind]
    tag = KIND_TAG[kind]
    sub_date = gemini_meta["submission_date"]
    date_str = sub_date.strftime("%Y%m%d") if hasattr(sub_date, "strftime") else str(sub_date).replace("-", "")
    ticker = gemini_meta.get("ticker") or ""
    shortname = sanitize_filename(gemini_meta.get("short_name") or "", 20)
    gemini_cat = sanitize_filename(gemini_meta.get("main_category") or "", 20)
    title = sanitize_filename(gemini_meta.get("doc_title") or "", 60)
    doc_id = gemini_meta.get("doc_id") or ""
    return f"受注_{label}_{tag}_{date_str}_{ticker}_{shortname}_{gemini_cat}_{title}_{doc_id}.pdf"


def gcs_download(bucket, gcs_path: str, local_path: Path) -> bool:
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


def extract_pdf_text(pdf_path: Path) -> str:
    """pdfplumberで最大PDF_MAX_PAGES・PDF_MAX_CHARS抽出."""
    import pdfplumber
    out = []
    total = 0
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages[:PDF_MAX_PAGES]):
                try:
                    t = page.extract_text() or ""
                except Exception as e:
                    t = f"[PAGE_EXTRACT_ERROR:{e}]"
                if t:
                    out.append(f"=== p.{i+1} ===\n{t}")
                    total += len(t)
                    if total >= PDF_MAX_CHARS:
                        break
    except Exception as e:
        return f"[PDF_OPEN_ERROR:{e}]"
    s = "\n\n".join(out)
    if len(s) > PDF_MAX_CHARS:
        s = s[:PDF_MAX_CHARS]
    return s


def existing_doc_ids(dir_path: Path) -> set[str]:
    ids: set[str] = set()
    if not dir_path.exists():
        return ids
    for p in dir_path.iterdir():
        if not p.is_file() or p.suffix.lower() != ".pdf":
            continue
        m = re.search(r"_(\d{16,})\.pdf$", p.name)
        if m:
            ids.add(m.group(1))
    return ids


def main() -> None:
    print(f"[START] {datetime.now(JST).isoformat()}", flush=True)
    print(f"[DROPBOX] {DROPBOX_BASE}", flush=True)
    DROPBOX_BASE.mkdir(parents=True, exist_ok=True)

    # 1. JSONL
    print(f"[JSONL] loading {JSONL_PATH}", flush=True)
    gemma_preds = load_gemma_predictions(JSONL_PATH)
    print(f"[JSONL] loaded {len(gemma_preds)} records", flush=True)

    # 2. BQ metadata
    doc_ids = list(gemma_preds.keys())
    bq_meta = fetch_bq_metadata(doc_ids)

    # 3. 3パターンの母集団抽出 & 先頭20件
    pattern_samples: dict[str, list[tuple[str, dict, dict]]] = {k: [] for k in KINDS}
    total_counts: dict[str, int] = {}

    all_detected: list[tuple[str, dict, dict]] = []
    all_missed: list[tuple[str, dict, dict]] = []
    all_over: list[tuple[str, dict, dict]] = []

    for doc_id, gemma_rec in gemma_preds.items():
        meta = bq_meta.get(doc_id)
        if meta is None:
            continue
        g = gemma_hit(gemma_rec)
        e = gemini_hit(meta)
        if g and e:
            all_detected.append((doc_id, gemma_rec, meta))
        elif (not g) and e:
            all_missed.append((doc_id, gemma_rec, meta))
        elif g and (not e):
            all_over.append((doc_id, gemma_rec, meta))

    for lst in (all_detected, all_missed, all_over):
        lst.sort(key=lambda x: x[0])

    pattern_samples["detected"] = all_detected[:SAMPLE_COUNT]
    pattern_samples["missed"] = all_missed[:SAMPLE_COUNT]
    pattern_samples["over_detected"] = all_over[:SAMPLE_COUNT]
    total_counts["detected"] = len(all_detected)
    total_counts["missed"] = len(all_missed)
    total_counts["over_detected"] = len(all_over)

    for k in KINDS:
        print(f"[MATCH] {k}: total={total_counts[k]} sampled={len(pattern_samples[k])}", flush=True)

    # 4. PDFコピー + テキスト抽出
    bucket = _get_gcs_bucket()
    pdf_text_store: dict[str, dict[str, str]] = {}  # kind -> doc_id -> text
    copy_counts: dict[str, dict] = {}
    summary_rows: dict[str, list[dict]] = {}

    with tempfile.TemporaryDirectory(prefix="gemma4_juchu_") as tmpdir:
        tmpdir_path = Path(tmpdir)
        for kind in KINDS:
            kind_dir = DROPBOX_BASE / kind
            kind_dir.mkdir(parents=True, exist_ok=True)
            existed = existing_doc_ids(kind_dir)
            print(f"[EXIST] {kind}: {len(existed)} existing PDFs", flush=True)

            rows = []
            count_new = 0
            count_skip = 0
            count_fail = 0
            pdf_text_store[kind] = {}

            for doc_id, gemma_rec, meta in pattern_samples[kind]:
                file_name = meta.get("file_name") or ""
                base_row = {
                    **meta,
                    "gemma_main": gemma_rec.get("main_cat_pred"),
                    "gemma_subs": gemma_rec.get("sub_cat_pred") or [],
                }
                # 既存のPDFパスを探す(resume用テキスト抽出)
                existing_pdf: Path | None = None
                if doc_id in existed:
                    for p in kind_dir.iterdir():
                        if p.suffix.lower() == ".pdf" and p.name.endswith(f"_{doc_id}.pdf"):
                            existing_pdf = p
                            break
                    count_skip += 1
                    status = "SKIP_EXIST"
                    saved_as = existing_pdf.name if existing_pdf else ""
                    if existing_pdf:
                        try:
                            pdf_text_store[kind][doc_id] = extract_pdf_text(existing_pdf)
                        except Exception as e:
                            pdf_text_store[kind][doc_id] = f"[TEXT_EXTRACT_ERROR:{e}]"
                    rows.append({**base_row, "copy_status": status, "saved_as": saved_as})
                    continue

                if not file_name:
                    print(f"[SKIP] {kind} {doc_id}: FILE_NAME empty", flush=True)
                    count_fail += 1
                    rows.append({**base_row, "copy_status": "NO_FILE_NAME", "saved_as": ""})
                    continue

                tmp_pdf = tmpdir_path / f"{doc_id}.pdf"
                ok = gcs_download(bucket, file_name, tmp_pdf)
                if not ok:
                    count_fail += 1
                    rows.append({**base_row, "copy_status": "DOWNLOAD_FAILED", "saved_as": ""})
                    continue

                target_name = build_filename(kind, meta)
                target_path = kind_dir / target_name
                try:
                    shutil.copy2(tmp_pdf, target_path)
                    count_new += 1
                    status = "OK"
                    print(f"[COPY] {kind} [{count_new}] {target_name}", flush=True)
                    try:
                        pdf_text_store[kind][doc_id] = extract_pdf_text(target_path)
                    except Exception as e:
                        pdf_text_store[kind][doc_id] = f"[TEXT_EXTRACT_ERROR:{e}]"
                except Exception as e:
                    print(f"[ERR] copy failed {target_name}: {e}", flush=True)
                    count_fail += 1
                    status = f"COPY_FAILED:{e}"
                rows.append({
                    **base_row,
                    "copy_status": status,
                    "saved_as": target_name if status == "OK" else "",
                })

            summary_rows[kind] = rows
            copy_counts[kind] = {
                "new": count_new,
                "skip": count_skip,
                "fail": count_fail,
                "existing_in_dir": len(existed),
            }

    # 5. _samples.md
    samples_path = DROPBOX_BASE / "_samples.md"
    with open(samples_path, "w", encoding="utf-8") as f:
        f.write(f"# 受注高/受注残高 プロンプトチューニング用サンプル ({TODAY_STR})\n\n")
        f.write(f"- カテゴリ: `{TARGET_CATEGORY}`\n")
        f.write(f"- 期間: {DATE_FROM} ~ {DATE_TO}\n")
        f.write(f"- JSONL: `{JSONL_PATH}`\n")
        f.write(f"- 抽出パターン: detected / missed / over_detected(各{SAMPLE_COUNT}件、doc_id昇順)\n\n")

        f.write("## サマリー\n\n")
        f.write("| パターン | 母集団 | 新規コピー | 既存スキップ | 失敗 | 最終 |\n")
        f.write("|---|---:|---:|---:|---:|---:|\n")
        for kind in KINDS:
            c = copy_counts[kind]
            total = total_counts[kind]
            f.write(f"| {kind} | {total} | {c['new']} | {c['skip']} | {c['fail']} | {c['new']+c['skip']} |\n")
        f.write("\n")

        for kind in KINDS:
            f.write(f"## {kind} (Gemma={'T' if kind != 'missed' else 'F'}, Gemini={'T' if kind != 'over_detected' else 'F'})\n\n")
            rows = summary_rows.get(kind, [])
            if not rows:
                f.write("_該当なし_\n\n")
                continue
            f.write("| # | doc_id | ticker | short_name | title | Gemma main | Gemma subs | Gemini main | Gemini subs | status |\n")
            f.write("|---|---|---|---|---|---|---|---|---|---|\n")
            for i, r in enumerate(rows, 1):
                title = (r.get("doc_title") or "").replace("|", "/")[:40]
                gemma_subs = ",".join(r.get("gemma_subs") or [])
                gemini_subs = ",".join(r.get("sub_categories") or [])
                f.write(
                    f"| {i} | {r.get('doc_id')} | {r.get('ticker')} | "
                    f"{(r.get('short_name') or '')[:15]} | {title} | "
                    f"{r.get('gemma_main') or ''} | {gemma_subs} | "
                    f"{r.get('main_category') or ''} | {gemini_subs} | "
                    f"{r.get('copy_status')} |\n"
                )
            f.write("\n")

    # 6. 本文テキストJSON(Claude自己判定用に別ファイル保存)
    text_json_path = DROPBOX_BASE / "_pdf_texts.json"
    text_payload = {}
    for kind in KINDS:
        text_payload[kind] = []
        for (doc_id, gemma_rec, meta) in pattern_samples[kind]:
            text = pdf_text_store.get(kind, {}).get(doc_id, "")
            text_payload[kind].append({
                "doc_id": doc_id,
                "ticker": meta.get("ticker"),
                "short_name": meta.get("short_name"),
                "submission_date": str(meta.get("submission_date")),
                "doc_title": meta.get("doc_title"),
                "gemma_main": gemma_rec.get("main_cat_pred"),
                "gemma_subs": gemma_rec.get("sub_cat_pred") or [],
                "gemini_main": meta.get("main_category"),
                "gemini_subs": meta.get("sub_categories") or [],
                "pdf_text": text,
            })
    with open(text_json_path, "w", encoding="utf-8") as f:
        json.dump(text_payload, f, ensure_ascii=False, indent=2)

    print(f"[SAMPLES] {samples_path}", flush=True)
    print(f"[TEXTS]   {text_json_path}", flush=True)
    print(f"[DONE]    {datetime.now(JST).isoformat()}", flush=True)
    for kind in KINDS:
        c = copy_counts[kind]
        total = total_counts[kind]
        print(
            f"  {kind}: match={total} new={c['new']} skip={c['skip']} "
            f"fail={c['fail']} (final={c['new']+c['skip']})",
            flush=True,
        )


if __name__ == "__main__":
    main()

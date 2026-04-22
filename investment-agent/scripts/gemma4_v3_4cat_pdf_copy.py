"""Gemma 4 31B TPU v1 PoC: 4カテゴリ × 3パターン のPDFサンプル収集.

対象4カテゴリ（中期経営計画 / 業績予想 / 業績の重要な先行指標 / 業績修正）について、
- detected     : Gemma=True  AND Gemini=True
- missed       : Gemma=False AND Gemini=True
- over_detected: Gemma=True  AND Gemini=False

を各20件サンプリング（doc_id昇順）し、GCSからPDFを取得してDropboxに保存する。

入力 JSONL は `C:/tmp/gemma4_tpu_monthly_results.jsonl` (v1, 2090件)。
既に Gemini 正解（gemini_main_category / gemini_sub_categories）が含まれているため
BQ への問い合わせは GCS パス（FILE_NAME）取得のみ。

Usage:
    PYTHONUTF8=1 python scripts/gemma4_v3_4cat_pdf_copy.py
"""

from __future__ import annotations

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

TARGET_CATEGORIES = [
    "中期経営計画",
    "業績予想",
    "業績の重要な先行指標",
    "業績修正",
]
PATTERNS = ("detected", "missed", "over_detected")
SAMPLE_COUNT = 20

TODAY_STR = "20260416"
DROPBOX_BASE = Path(
    f"C:/Users/zonekun/Dropbox/stock/temp/gemma4_4_31b_tpu_poc/tuning_4cat_{TODAY_STR}"
)


# ── GCP helpers ──────────────────────────────────────
def _get_credentials():
    from google.oauth2 import service_account
    return service_account.Credentials.from_service_account_file(
        SA_KEY_PATH,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


def _get_bq_client():
    from google.cloud import bigquery
    return bigquery.Client(project=GCP_PROJECT, credentials=_get_credentials())


def _get_gcs_bucket():
    from google.cloud import storage
    client = storage.Client(project=GCP_PROJECT, credentials=_get_credentials())
    return client.bucket(GCS_BUCKET)


# ── 入力 ─────────────────────────────────────────
def load_records(jsonl_path: Path) -> list[dict]:
    records: list[dict] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


# ── パターン判定 ─────────────────────────────────
def gemma_hit(r: dict, category: str) -> bool:
    return (r.get("main_cat_pred") or "") == category or category in (r.get("sub_cat_pred") or [])


def gemini_hit(r: dict, category: str) -> bool:
    return (r.get("gemini_main_category") or "") == category or category in (r.get("gemini_sub_categories") or [])


def classify(r: dict, category: str) -> str | None:
    g = gemma_hit(r, category)
    e = gemini_hit(r, category)
    if g and e:
        return "detected"
    if (not g) and e:
        return "missed"
    if g and (not e):
        return "over_detected"
    return None


# ── BQ メタ取得（FILE_NAME と ticker / filer_name / submission_date） ──
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
        DOC_TITLE,
        FILE_NAME
    FROM `{BQ_TABLE}`
    WHERE SUBMISSION_DATE BETWEEN '{DATE_FROM}' AND '{DATE_TO}'
      AND DOC_ID IN ({doc_ids_str})
    GROUP BY DOC_ID, TICKER, FILER_NAME, SUBMISSION_DATE,
             MAIN_CATEGORY, DOC_TITLE, FILE_NAME
    """
    print(f"[BQ] querying {len(doc_ids)} doc_ids...", flush=True)
    rows = list(bq_client.query(sql).result())
    result: dict[str, dict] = {}
    for row in rows:
        result[row.DOC_ID] = {
            "doc_id": row.DOC_ID,
            "ticker": row.TICKER,
            "short_name": row.FILER_NAME or "",
            "submission_date": row.SUBMISSION_DATE,
            "main_category": row.MAIN_CATEGORY,
            "doc_title": row.DOC_TITLE or "",
            "file_name": row.FILE_NAME or "",
        }
    print(f"[BQ] fetched {len(result)} rows", flush=True)
    return result


# ── ファイル名 ─────────────────────────────────
_FS_BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def sanitize_filename(s: str, max_len: int = 60) -> str:
    if not s:
        return ""
    s = _FS_BAD_CHARS.sub("_", s)
    s = s.replace("\u3000", "_").strip()
    if len(s) > max_len:
        s = s[:max_len]
    return s


KIND_TAG = {
    "detected": ("検出", "T", "T"),
    "missed": ("漏れ", "F", "T"),
    "over_detected": ("過剰", "T", "F"),
}


def build_filename(category: str, kind: str, meta: dict) -> str:
    label, gemma_flag, gemini_flag = KIND_TAG[kind]
    tag = f"{label}_Gemma={gemma_flag}_Gemini={gemini_flag}"
    sub_date = meta["submission_date"]
    date_str = sub_date.strftime("%Y%m%d") if hasattr(sub_date, "strftime") else str(sub_date).replace("-", "")
    ticker = meta.get("ticker") or ""
    shortname = sanitize_filename(meta.get("short_name") or "", 20)
    gemini_main = sanitize_filename(meta.get("main_category") or "", 20)
    title = sanitize_filename(meta.get("doc_title") or "", 60)
    doc_id = meta.get("doc_id") or ""
    return f"{category}_{tag}_{date_str}_{ticker}_{shortname}_{gemini_main}_{title}_{doc_id}.pdf"


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


# ── main ─────────────────────────────────────────
def main() -> None:
    print(f"[START] {datetime.now(JST).isoformat()}", flush=True)
    print(f"[DROPBOX] {DROPBOX_BASE}", flush=True)
    DROPBOX_BASE.mkdir(parents=True, exist_ok=True)

    records = load_records(JSONL_PATH)
    print(f"[JSONL] loaded {len(records)} records", flush=True)

    # doc_id -> record
    rec_by_id = {r["doc_id"]: r for r in records if r.get("doc_id")}

    # 1. 各カテゴリ × パターンでサンプル抽出
    category_samples: dict[str, dict[str, list[str]]] = {}
    total_counts: dict[str, dict[str, int]] = {}
    wanted_doc_ids: set[str] = set()

    for cat in TARGET_CATEGORIES:
        buckets: dict[str, list[str]] = {k: [] for k in PATTERNS}
        for doc_id, r in rec_by_id.items():
            k = classify(r, cat)
            if k:
                buckets[k].append(doc_id)
        category_samples[cat] = {}
        total_counts[cat] = {}
        for k in PATTERNS:
            buckets[k].sort()
            total_counts[cat][k] = len(buckets[k])
            sample = buckets[k][:SAMPLE_COUNT]
            category_samples[cat][k] = sample
            wanted_doc_ids.update(sample)
            print(f"[MATCH] {cat}/{k}: total={len(buckets[k])} sample={len(sample)}", flush=True)

    # 2. BQ メタ取得（まとめて1回）
    bq_meta = fetch_bq_metadata(sorted(wanted_doc_ids))

    # 3. PDF コピー
    summary_rows: dict[str, dict[str, list[dict]]] = {}
    copy_counts: dict[str, dict[str, dict[str, int]]] = {}

    bucket = _get_gcs_bucket()
    with tempfile.TemporaryDirectory(prefix="gemma4_v3_pdf_") as tmpdir:
        tmpdir_path = Path(tmpdir)
        for cat in TARGET_CATEGORIES:
            cat_dir = DROPBOX_BASE / cat
            cat_dir.mkdir(parents=True, exist_ok=True)
            summary_rows[cat] = {}
            copy_counts[cat] = {}
            for kind in PATTERNS:
                kind_dir = cat_dir / kind
                kind_dir.mkdir(parents=True, exist_ok=True)
                existed = existing_doc_ids(kind_dir)
                print(f"[EXIST] {cat}/{kind}: {len(existed)} existing", flush=True)
                sample_ids = category_samples[cat][kind]
                rows: list[dict] = []
                count_new = 0
                count_skip = 0
                count_fail = 0
                for doc_id in sample_ids:
                    rec = rec_by_id[doc_id]
                    meta = bq_meta.get(doc_id)
                    if meta is None:
                        print(f"[SKIP] {cat}/{kind} {doc_id}: no BQ meta", flush=True)
                        count_fail += 1
                        rows.append({
                            "doc_id": doc_id,
                            "ticker": rec.get("ticker"),
                            "short_name": "",
                            "submission_date": "",
                            "main_category": rec.get("gemini_main_category"),
                            "sub_categories": rec.get("gemini_sub_categories") or [],
                            "doc_title": rec.get("doc_title"),
                            "gemma_main": rec.get("main_cat_pred"),
                            "gemma_subs": rec.get("sub_cat_pred") or [],
                            "copy_status": "NO_BQ_META",
                            "saved_as": "",
                        })
                        continue

                    base_row = {
                        **meta,
                        "sub_categories": rec.get("gemini_sub_categories") or [],
                        "gemma_main": rec.get("main_cat_pred"),
                        "gemma_subs": rec.get("sub_cat_pred") or [],
                    }

                    if doc_id in existed:
                        count_skip += 1
                        rows.append({**base_row, "copy_status": "SKIP_EXIST", "saved_as": ""})
                        continue

                    file_name = meta.get("file_name") or ""
                    if not file_name:
                        print(f"[SKIP] {cat}/{kind} {doc_id}: FILE_NAME empty", flush=True)
                        count_fail += 1
                        rows.append({**base_row, "copy_status": "NO_FILE_NAME", "saved_as": ""})
                        continue

                    tmp_pdf = tmpdir_path / f"{doc_id}.pdf"
                    ok = gcs_download(bucket, file_name, tmp_pdf)
                    if not ok:
                        count_fail += 1
                        rows.append({**base_row, "copy_status": "DOWNLOAD_FAILED", "saved_as": ""})
                        continue

                    target_name = build_filename(cat, kind, meta)
                    target_path = kind_dir / target_name
                    try:
                        shutil.copy2(tmp_pdf, target_path)
                        count_new += 1
                        status = "OK"
                        print(f"[COPY] {cat}/{kind} [{count_new}] {target_name}", flush=True)
                    except Exception as e:
                        print(f"[ERR] copy failed {target_name}: {e}", flush=True)
                        count_fail += 1
                        status = f"COPY_FAILED:{e}"
                    rows.append({
                        **base_row,
                        "copy_status": status,
                        "saved_as": target_name if status == "OK" else "",
                    })
                summary_rows[cat][kind] = rows
                copy_counts[cat][kind] = {
                    "new": count_new, "skip": count_skip, "fail": count_fail,
                    "existing_in_dir": len(existed),
                }

    # 4. _samples.md
    samples_path = DROPBOX_BASE / "_samples.md"
    with open(samples_path, "w", encoding="utf-8") as f:
        f.write(f"# Gemma 4 31B TPU v1 4カテゴリ サンプル ({TODAY_STR})\n\n")
        f.write(f"- 期間: {DATE_FROM} ~ {DATE_TO}\n")
        f.write(f"- JSONL: `{JSONL_PATH}`\n")
        f.write(f"- 対象カテゴリ: {', '.join(TARGET_CATEGORIES)}\n")
        f.write("- パターン:\n")
        f.write("    - detected     : Gemma=True  AND Gemini=True\n")
        f.write("    - missed       : Gemma=False AND Gemini=True\n")
        f.write("    - over_detected: Gemma=True  AND Gemini=False\n")
        f.write(f"- サンプル方式: doc_id昇順で先頭{SAMPLE_COUNT}件\n\n")

        f.write("## 件数サマリー\n\n")
        f.write("| カテゴリ | パターン | 母集団 | サンプル | 新規 | 既存スキップ | 失敗 |\n")
        f.write("|---|---|---:|---:|---:|---:|---:|\n")
        for cat in TARGET_CATEGORIES:
            for kind in PATTERNS:
                total = total_counts[cat][kind]
                c = copy_counts[cat][kind]
                f.write(
                    f"| {cat} | {kind} | {total} | {min(total, SAMPLE_COUNT)} | "
                    f"{c['new']} | {c['skip']} | {c['fail']} |\n"
                )
        f.write("\n")

        for cat in TARGET_CATEGORIES:
            f.write(f"## {cat}\n\n")
            for kind in PATTERNS:
                f.write(f"### {kind}\n\n")
                rows = summary_rows.get(cat, {}).get(kind, [])
                if not rows:
                    f.write("_該当なし_\n\n")
                    continue
                f.write(
                    "| # | doc_id | ticker | short_name | title | Gemma main | Gemma subs | Gemini main | Gemini subs | status |\n"
                )
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

    print(f"\n[SAMPLES] {samples_path}", flush=True)
    print(f"[DONE] {datetime.now(JST).isoformat()}", flush=True)
    for cat in TARGET_CATEGORIES:
        for kind in PATTERNS:
            c = copy_counts[cat][kind]
            total = total_counts[cat][kind]
            print(
                f"  {cat}/{kind}: match={total} new={c['new']} "
                f"skip={c['skip']} fail={c['fail']}",
                flush=True,
            )


if __name__ == "__main__":
    main()

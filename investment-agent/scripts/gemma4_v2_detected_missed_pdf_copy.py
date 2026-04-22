"""Gemma 4 31B TPU v2 PoC: detected / missed PDFをDropboxへコピー.

対象3カテゴリ（配当、特別損失、特別利益）について、
- detected: Gemma=True かつ Gemini=True（両方検出）
- missed:   Gemma=False かつ Gemini=True（Gemma漏れ）
を各20件サンプリングし、GCSからPDFを取得してDropboxに保存する。

Usage:
    PYTHONUTF8=1 python scripts/gemma4_v2_detected_missed_pdf_copy.py
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
JSONL_PATH = Path("C:/tmp/gemma4_tpu_monthly_v2_results.jsonl")
DATE_FROM = "2024-01-01"
DATE_TO = "2024-01-31"

TARGET_CATEGORIES = ["配当", "特別損失", "特別利益"]
SAMPLE_COUNT = 20

# 固定（resume対応のため既存ディレクトリを使い続ける）
TODAY_STR = "20260416"
DROPBOX_BASE = Path(
    f"C:/Users/zonekun/Dropbox/stock/temp/gemma4_4_31b_tpu_poc/v2_analysis_{TODAY_STR}"
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
    """BQのSUB_CATEGORIES(STRING) を list[str] にパースする."""
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
    """BQから指定doc_idのメタデータを取得.

    Returns:
        doc_id -> {main_category, sub_categories, submission_date, doc_title,
                   short_name, ticker, file_name}

    NOTE: SHORT_NAMEカラムは存在しないため、FILER_NAME を short_name として扱う。
    """
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


def gemma_hit(gemma_rec: dict, category: str) -> bool:
    gemma_main = gemma_rec.get("main_cat_pred") or ""
    gemma_subs = gemma_rec.get("sub_cat_pred") or []
    return (gemma_main == category) or (category in gemma_subs)


def gemini_hit(gemini_meta: dict, category: str) -> bool:
    gemini_main = gemini_meta.get("main_category") or ""
    gemini_subs = gemini_meta.get("sub_categories") or []
    return (gemini_main == category) or (category in gemini_subs)


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
    category: str, kind: str, gemini_meta: dict,
) -> str:
    """命名規則.

    detected: <category>_検出_Gemma=T_Gemini=T_<YYYYMMDD>_<ticker>_<shortname>_<gemini_MAIN>_<title60>_<doc_id>.pdf
    missed:   <category>_漏れ_Gemma=F_Gemini=T_<YYYYMMDD>_<ticker>_<shortname>_<gemini_MAIN>_<title60>_<doc_id>.pdf
    """
    if kind == "detected":
        tag = "検出_Gemma=T_Gemini=T"
    elif kind == "missed":
        tag = "漏れ_Gemma=F_Gemini=T"
    else:
        tag = kind

    sub_date = gemini_meta["submission_date"]
    date_str = sub_date.strftime("%Y%m%d") if hasattr(sub_date, "strftime") else str(sub_date).replace("-", "")
    ticker = gemini_meta.get("ticker") or ""
    shortname = sanitize_filename(gemini_meta.get("short_name") or "", 20)
    gemini_cat = sanitize_filename(gemini_meta.get("main_category") or "", 20)
    title = sanitize_filename(gemini_meta.get("doc_title") or "", 60)
    doc_id = gemini_meta.get("doc_id") or ""
    return f"{category}_{tag}_{date_str}_{ticker}_{shortname}_{gemini_cat}_{title}_{doc_id}.pdf"


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


def existing_doc_ids(dir_path: Path) -> set[str]:
    """ディレクトリ内の既存PDFファイル名から doc_id を抽出（末尾 _<doc_id>.pdf）."""
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

    # 1. JSONL読み込み
    print(f"[JSONL] loading {JSONL_PATH}", flush=True)
    gemma_preds = load_gemma_predictions(JSONL_PATH)
    print(f"[JSONL] loaded {len(gemma_preds)} records", flush=True)

    # 2. BQメタデータ
    doc_ids = list(gemma_preds.keys())
    bq_meta = fetch_bq_metadata(doc_ids)

    # 3. 各カテゴリで detected / missed を集計、doc_id昇順で先頭20件
    # category -> kind -> list[(doc_id, gemma_rec, meta)]
    category_samples: dict[str, dict[str, list[tuple[str, dict, dict]]]] = {}
    total_counts: dict[str, dict[str, int]] = {}

    for cat in TARGET_CATEGORIES:
        detected: list[tuple[str, dict, dict]] = []
        missed: list[tuple[str, dict, dict]] = []
        for doc_id, gemma_rec in gemma_preds.items():
            meta = bq_meta.get(doc_id)
            if meta is None:
                continue
            g_hit = gemma_hit(gemma_rec, cat)
            e_hit = gemini_hit(meta, cat)
            if g_hit and e_hit:
                detected.append((doc_id, gemma_rec, meta))
            elif (not g_hit) and e_hit:
                missed.append((doc_id, gemma_rec, meta))
        detected.sort(key=lambda x: x[0])
        missed.sort(key=lambda x: x[0])
        total_counts[cat] = {"detected": len(detected), "missed": len(missed)}
        print(
            f"[MATCH] {cat}: detected={len(detected)} missed={len(missed)}",
            flush=True,
        )
        category_samples[cat] = {
            "detected": detected[:SAMPLE_COUNT],
            "missed": missed[:SAMPLE_COUNT],
        }

    # 4. PDFコピー
    summary_rows: dict[str, dict[str, list[dict]]] = {}
    copy_counts: dict[str, dict[str, dict[str, int]]] = {}

    bucket = _get_gcs_bucket()
    with tempfile.TemporaryDirectory(prefix="gemma4_v2_pdf_") as tmpdir:
        tmpdir_path = Path(tmpdir)
        for cat in TARGET_CATEGORIES:
            cat_dir = DROPBOX_BASE / cat
            cat_dir.mkdir(parents=True, exist_ok=True)
            summary_rows[cat] = {}
            copy_counts[cat] = {}
            for kind in ("detected", "missed"):
                kind_dir = cat_dir / kind
                kind_dir.mkdir(parents=True, exist_ok=True)
                existed = existing_doc_ids(kind_dir)
                print(
                    f"[EXIST] {cat}/{kind}: {len(existed)} existing PDFs in dir",
                    flush=True,
                )
                samples = category_samples[cat][kind]
                rows = []
                count_new = 0
                count_skip = 0
                count_fail = 0
                for doc_id, gemma_rec, meta in samples:
                    file_name = meta.get("file_name") or ""
                    base_row = {
                        **meta,
                        "gemma_main": gemma_rec.get("main_cat_pred"),
                        "gemma_subs": gemma_rec.get("sub_cat_pred") or [],
                    }
                    # resume: 既存スキップ
                    if doc_id in existed:
                        count_skip += 1
                        rows.append({**base_row, "copy_status": "SKIP_EXIST", "saved_as": ""})
                        continue

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
                        print(
                            f"[COPY] {cat}/{kind} [{count_new}] {target_name}",
                            flush=True,
                        )
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
                    "new": count_new,
                    "skip": count_skip,
                    "fail": count_fail,
                    "existing_in_dir": len(existed),
                }

    # 5. _summary.md
    summary_path = DROPBOX_BASE / "_summary.md"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(f"# Gemma 4 31B TPU v2 検出/漏れサンプル ({TODAY_STR})\n\n")
        f.write(f"- 期間: {DATE_FROM} ~ {DATE_TO}\n")
        f.write(f"- JSONL: `{JSONL_PATH}`\n")
        f.write(f"- 対象カテゴリ: {', '.join(TARGET_CATEGORIES)}\n")
        f.write(f"- 種類:\n")
        f.write(f"    - detected: Gemma=True AND Gemini=True\n")
        f.write(f"    - missed:   Gemma=False AND Gemini=True\n")
        f.write(f"- サンプル方式: doc_id昇順で先頭{SAMPLE_COUNT}件\n\n")

        f.write(f"## マッチ件数・コピー件数サマリー\n\n")
        f.write(
            "| カテゴリ | 種類 | 母集団 | 新規コピー | 既存スキップ | 失敗 | 最終PDF数 |\n"
            "|---|---|---:|---:|---:|---:|---:|\n"
        )
        for cat in TARGET_CATEGORIES:
            for kind in ("detected", "missed"):
                total = total_counts[cat][kind]
                c = copy_counts[cat][kind]
                total_final = c["new"] + c["skip"]
                f.write(
                    f"| {cat} | {kind} | {total} | {c['new']} | {c['skip']} | "
                    f"{c['fail']} | {total_final} |\n"
                )
        f.write("\n")

        for cat in TARGET_CATEGORIES:
            f.write(f"## {cat}\n\n")
            for kind in ("detected", "missed"):
                f.write(f"### {kind}\n\n")
                rows = summary_rows.get(cat, {}).get(kind, [])
                if not rows:
                    f.write("_該当なし_\n\n")
                    continue
                f.write(
                    "| # | doc_id | ticker | short_name | title | Gemma main | Gemma subs | Gemini main | Gemini subs | status |\n"
                )
                f.write(
                    "|---|---|---|---|---|---|---|---|---|---|\n"
                )
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

    print(f"\n[SUMMARY] {summary_path}", flush=True)
    print(f"[DONE] {datetime.now(JST).isoformat()}", flush=True)
    for cat in TARGET_CATEGORIES:
        for kind in ("detected", "missed"):
            c = copy_counts[cat][kind]
            total = total_counts[cat][kind]
            print(
                f"  {cat}/{kind}: match={total} new={c['new']} "
                f"skip={c['skip']} fail={c['fail']} (final={c['new'] + c['skip']})",
                flush=True,
            )


if __name__ == "__main__":
    main()

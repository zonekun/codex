"""Phase 3.1: asr_docid_index.csv を消費して、全有報XBRLをDL→パース→BQ ロード。

並列化: concurrent.futures.ThreadPoolExecutor で 4-8 workers。

Usage:
  PYTHONUTF8=1 python scripts/load_shareholder_from_index.py --workers 6
  PYTHONUTF8=1 python scripts/load_shareholder_from_index.py --ticker 7203 --dry-run
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import urllib3
from google.cloud import bigquery, storage
from google.oauth2 import service_account

# スレッドローカル session + GCS blob listキャッシュ
_tls = threading.local()
_gcs_list_cache: dict[str, list] = {}
_gcs_list_lock = threading.Lock()
_disk_check_lock = threading.Lock()
_last_disk_check = [0.0]


def _get_thread_session() -> requests.Session:
    if not hasattr(_tls, "session"):
        _tls.session = _session()
    return _tls.session


def _get_cached_gcs_blobs(storage_client: storage.Client, ticker: str) -> list:
    """ticker毎にlist_blobsを1回だけ。複数年のlookupで再利用。"""
    with _gcs_list_lock:
        if ticker in _gcs_list_cache:
            return _gcs_list_cache[ticker]
    prefix = f"edinet/{ticker}/"
    blobs = [b.name for b in storage_client.list_blobs("stock_data_1930932", prefix=prefix)]
    with _gcs_list_lock:
        _gcs_list_cache[ticker] = blobs
    return blobs


def _disk_free_gb() -> float:
    try:
        return shutil.disk_usage(r"C:\tmp").free / (1024 ** 3)
    except Exception:
        return 999.0


def _check_disk_periodic():
    """30秒毎にディスク空き容量を確認。5GB未満なら警告、1GB未満なら停止推奨。"""
    with _disk_check_lock:
        now = time.time()
        if now - _last_disk_check[0] < 30:
            return None
        _last_disk_check[0] = now
    free_gb = _disk_free_gb()
    if free_gb < 1.0:
        return f"DISK CRITICAL: {free_gb:.1f}GB free"
    elif free_gb < 5.0:
        return f"WARN: disk low {free_gb:.1f}GB free"
    return None

sys.path.insert(0, str(Path(__file__).parent))
from fetch_shareholder_composition import (
    SharesComposition,
    edinet_download_xbrl,
    find_gcs_xbrl,
    download_xbrl_from_gcs,
    parse_xbrl,
    ensure_table,
    insert_rows,
    LOCAL_CACHE_DIR,
    TABLE_ID,
    _session,
)
from flag_activists_in_list import load_activists

urllib3.disable_warnings()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

KEY_FILE = "keys/gcp-service-account.json"
PROJECT = "gmailpj-357912"
INDEX_CSV = Path("data/logs/asr_docid_index.csv")


def get_clients():
    creds = service_account.Credentials.from_service_account_file(
        KEY_FILE, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return (
        bigquery.Client(project=PROJECT, credentials=creds),
        storage.Client(project=PROJECT, credentials=creds),
    )


def _find_gcs_xbrl_cached(storage_client: storage.Client, ticker: str, submit_year: int) -> Optional[str]:
    """キャッシュ済みblob listから 有報年 XBRL をsubmit_year一致で検索。"""
    import re
    blobs = _get_cached_gcs_blobs(storage_client, ticker)
    for name in blobs:
        base = os.path.basename(name)
        if "_有報年_" not in base or not base.endswith(".xbrl"):
            continue
        m = re.match(r"^\d+_有報年_(\d{8})_", base)
        if not m:
            continue
        if int(m.group(1)[:4]) == submit_year:
            return name
    return None


def get_xbrl_path(ticker: str, doc_id: str, submit_year: int,
                  sess: requests.Session, storage_client: storage.Client) -> Optional[Path]:
    """GCS優先 → EDINET API DL。戻り値はXBRLファイルパス。"""
    local_subdir = LOCAL_CACHE_DIR / f"{ticker}_{submit_year}"
    if local_subdir.exists():
        for f in local_subdir.rglob("*.xbrl"):
            if "PublicDoc" in str(f):
                return f
        for f in local_subdir.rglob("*.xbrl"):
            return f

    # GCS 試行（キャッシュされたblob list活用）
    try:
        blob_name = _find_gcs_xbrl_cached(storage_client, ticker, submit_year)
        if blob_name:
            return download_xbrl_from_gcs(storage_client, blob_name, local_subdir)
    except Exception:
        pass

    # EDINET API（リトライ付き）
    for attempt in range(3):
        try:
            result = edinet_download_xbrl(sess, doc_id, local_subdir)
            if result:
                return result
        except Exception as e:
            if attempt == 2:
                break
            time.sleep(1.0 * (attempt + 1))
    return None


def process_row(row: dict, sess: requests.Session, storage_client: storage.Client,
                activists: list, cleanup: bool = True) -> tuple[Optional[SharesComposition], Optional[str]]:
    """XBRL を DL+parse、BQ insert は呼び出し側でバッチ化。parse後にローカルXBRLキャッシュ削除(ディスク節約)。"""
    ticker = str(row["TICKER"])
    doc_id = row["DOC_ID"]
    submit_year = int(row["SUBMIT_YEAR"])
    xbrl_path = get_xbrl_path(ticker, doc_id, submit_year, sess, storage_client)
    if not xbrl_path:
        return None, f"{ticker}/{submit_year}: no XBRL"
    try:
        ext = parse_xbrl(xbrl_path, ticker, activists)
        ext.doc_id = doc_id
    finally:
        # パース後にローカルキャッシュ削除（ディスク容量逼迫対策）
        if cleanup:
            local_subdir = LOCAL_CACHE_DIR / f"{ticker}_{submit_year}"
            if local_subdir.exists():
                try:
                    shutil.rmtree(str(local_subdir), ignore_errors=True)
                except Exception:
                    pass
    if ext.error:
        return None, f"{ticker}/{submit_year}: {ext.error}"
    return ext, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--ticker", help="単一ticker指定（テスト用）")
    parser.add_argument("--year-min", type=int, default=2013)
    parser.add_argument("--year-max", type=int, default=2026)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="先頭N件のみ処理")
    parser.add_argument("--skip-ensure", action="store_true")
    parser.add_argument("--no-resume", action="store_true", help="BQ既存分をスキップしない（TRUNCATE前提で全件再処理）")
    args = parser.parse_args()

    if not INDEX_CSV.exists():
        print(f"[err] {INDEX_CSV} が見つからない。build_asr_docid_index.py を先に実行")
        return

    df = pd.read_csv(INDEX_CSV)
    df = df[(df["SUBMIT_YEAR"] >= args.year_min) & (df["SUBMIT_YEAR"] <= args.year_max)]
    df["TICKER"] = df["TICKER"].astype(str)
    if args.ticker:
        df = df[df["TICKER"] == args.ticker]

    bq, storage_client = get_clients()
    if not args.skip_ensure and not args.dry_run:
        ensure_table(bq)

    # レジューム: 既にBQに存在する (TICKER, SUBMIT_YEAR) をスキップ
    if not args.dry_run and not args.no_resume:
        done_df = bq.query(
            f"SELECT DISTINCT TICKER, EXTRACT(YEAR FROM SUBMIT_DATE) AS SUBMIT_YEAR "
            f"FROM `{TABLE_ID}` WHERE SUBMIT_DATE IS NOT NULL"
        ).to_dataframe()
        done_keys = set(zip(done_df["TICKER"].astype(str), done_df["SUBMIT_YEAR"].astype(int)))
        before = len(df)
        df = df[~df.apply(lambda r: (str(r["TICKER"]), int(r["SUBMIT_YEAR"])) in done_keys, axis=1)]
        print(f"[info] resume: skipped {before - len(df)} already-done rows")

    if args.limit > 0:
        df = df.head(args.limit)

    print(f"[info] index rows: {len(df)}")

    activists = load_activists()
    print(f"[info] activists: {len(activists)}")

    done = 0
    errors = 0
    rows_to_process = df.to_dict("records")
    INSERT_BATCH = 200
    pending: list[SharesComposition] = []

    def worker_process(idx_row):
        idx, row = idx_row
        sess = _get_thread_session()  # thread-local session (requests.Session は厳密にthread-safeでないため)
        return process_row(row, sess, storage_client, activists, cleanup=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(worker_process, (i, r)): i for i, r in enumerate(rows_to_process)}
        for f in as_completed(futures):
            idx = futures[f]
            done += 1
            try:
                ext, err = f.result()
                if err:
                    errors += 1
                    if errors <= 30:
                        print(f"  [{done}] {err}")
                elif ext and not args.dry_run:
                    pending.append(ext)
                    if len(pending) >= INSERT_BATCH:
                        try:
                            insert_rows(bq, pending)
                        except Exception as e:
                            print(f"  batch insert err: {e}")
                        pending = []
            except Exception as e:
                errors += 1
                print(f"  [{done}] unexpected err: {e}")
            if done % 500 == 0:
                disk_msg = _check_disk_periodic()
                disk_str = f"  [{disk_msg}]" if disk_msg else f"  [disk_free={_disk_free_gb():.1f}GB]"
                print(f"  progress {done}/{len(rows_to_process)}  errors={errors}  pending={len(pending)}{disk_str}")
                if disk_msg and "CRITICAL" in disk_msg:
                    print(f"  CRITICAL disk exhaustion, aborting soon")
                    # 最後のバッチをflush して exit
                    if pending and not args.dry_run:
                        insert_rows(bq, pending)
                    return

    # 最後のバッチ
    if pending and not args.dry_run:
        insert_rows(bq, pending)

    print(f"\n[info] done={done}, errors={errors}")


if __name__ == "__main__":
    main()

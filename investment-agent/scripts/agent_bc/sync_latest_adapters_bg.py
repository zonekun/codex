#!/usr/bin/env python3
"""ローカル vs GCS アダプタ の最新版を判定し、古い側を上書きして両者を揃える.

安全方針:
 - adapter.json / extract_adapter.json 両方対応
 - 判定は JSON 内の `updated_at` を第一優先、無ければ GCS blob.updated or ローカル mtime
 - **差分がある時のみ**更新、**明確に新しい側のみ**を採用
 - 判定不能（両方とも updated_at 欠落 + content 異なる）は SKIP してレポート

対象:
  adapter.json         : local = data/monthly_adapters/{ticker}.json (非TDnet)
                         gcs   = monthly/meta/{ticker}/adapter.json
  extract_adapter.json : local は同 {ticker}.json に含むパターンが多い。
                         本スクリプトは gcs 側 extract_adapter.json 単体と
                         local {ticker}.json の下位フィールドを区別しないため、
                         extract_adapter.json の同期は gcs-only の last_updated
                         比較ベースで実施。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

LOCAL_DIR = ROOT / "data/monthly_adapters"
GCS_PROJECT = "gmailpj-357912"
GCS_BUCKET = "stock_data_1930932"


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _aware_jst(dt):
    """naive datetime を JST aware に補正。aware はそのまま返す。None は None。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def _parse_dt(s):
    if not s:
        return None
    if not isinstance(s, str):
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return _aware_jst(datetime.fromisoformat(s))
    except Exception:
        return None


def _resolve_updated_at(content: dict, fallback_dt):
    dt = _parse_dt(content.get("updated_at"))
    if dt is None:
        dt = _parse_dt(content.get("last_updated")) or _parse_dt(content.get("last_checked"))
    return dt or _aware_jst(fallback_dt)


def _sync_one(ticker: str, filename: str, bucket) -> dict:
    """1 ticker の adapter.json または extract_adapter.json を同期."""
    # ローカルパス
    if filename == "adapter.json":
        local_path = LOCAL_DIR / f"{ticker}.json"
    else:  # extract_adapter.json
        local_path = LOCAL_DIR / f"{ticker}_extract.json"

    gcs_blob = bucket.blob(f"monthly/meta/{ticker}/{filename}")

    # load local
    local_content = None
    local_bytes = None
    local_dt_fallback = None
    if local_path.exists():
        local_bytes = local_path.read_bytes()
        try:
            local_content = json.loads(local_bytes.decode("utf-8"))
        except Exception:
            pass
        local_dt_fallback = datetime.fromtimestamp(local_path.stat().st_mtime, tz=JST)

    # load gcs
    gcs_content = None
    gcs_bytes = None
    gcs_dt_fallback = None
    if gcs_blob.exists():
        gcs_blob.reload()
        gcs_bytes = gcs_blob.download_as_bytes()
        try:
            gcs_content = json.loads(gcs_bytes.decode("utf-8"))
        except Exception:
            pass
        if gcs_blob.updated is not None:
            gcs_dt_fallback = gcs_blob.updated.astimezone(JST)

    res = {"ticker": ticker, "file": filename, "action": "none", "reason": ""}

    if local_content is None and gcs_content is None:
        res["action"] = "skip_both_missing"
        return res
    if local_content is None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(gcs_bytes)
        res["action"] = "pulled_gcs_to_local"
        res["reason"] = "local missing"
        return res
    if gcs_content is None:
        gcs_blob.upload_from_string(local_bytes, content_type="application/json")
        res["action"] = "pushed_local_to_gcs"
        res["reason"] = "gcs missing"
        return res

    if _md5(local_bytes) == _md5(gcs_bytes):
        res["action"] = "skip_same"
        return res

    l_dt = _resolve_updated_at(local_content, local_dt_fallback)
    g_dt = _resolve_updated_at(gcs_content, gcs_dt_fallback)

    if l_dt is None and g_dt is None:
        res["action"] = "skip_ambiguous"
        res["reason"] = "both updated_at missing + content differs"
        return res
    if l_dt is None:
        local_path.write_bytes(gcs_bytes)
        res["action"] = "pulled_gcs_to_local"
        res["reason"] = f"gcs only dt={g_dt}"
        return res
    if g_dt is None:
        gcs_blob.upload_from_string(local_bytes, content_type="application/json")
        res["action"] = "pushed_local_to_gcs"
        res["reason"] = f"local only dt={l_dt}"
        return res

    if l_dt > g_dt:
        gcs_blob.upload_from_string(local_bytes, content_type="application/json")
        res["action"] = "pushed_local_to_gcs"
        res["reason"] = f"local {l_dt} > gcs {g_dt}"
    elif g_dt > l_dt:
        local_path.write_bytes(gcs_bytes)
        res["action"] = "pulled_gcs_to_local"
        res["reason"] = f"gcs {g_dt} > local {l_dt}"
    else:
        res["action"] = "skip_equal_dt_diff_content"
        res["reason"] = f"both dt={l_dt} but content differs"
    return res


def main() -> int:
    from google.cloud import storage
    bucket = storage.Client(project=GCS_PROJECT).bucket(GCS_BUCKET)

    local_tickers: set[str] = set()
    for p in LOCAL_DIR.glob("*.json"):
        stem = p.stem
        if stem.endswith("_extract"):
            continue
        local_tickers.add(stem)

    gcs_tickers: set[str] = set()
    for b in bucket.list_blobs(prefix="monthly/meta/"):
        parts = b.name.split("/")
        # blob name: monthly/meta/<ticker>/<filename>
        # parts = ['monthly', 'meta', '<ticker>', '<filename>']
        if len(parts) >= 4 and parts[3] in ("adapter.json", "extract_adapter.json"):
            gcs_tickers.add(parts[2])

    tickers = sorted(local_tickers | gcs_tickers)
    logger.info(f"対象 ticker 数: {len(tickers)} (local {len(local_tickers)} | gcs {len(gcs_tickers)})")

    results: list[dict] = []
    for i, t in enumerate(tickers, 1):
        if i % 100 == 0:
            logger.info(f"  進捗 {i}/{len(tickers)}")
        for fn in ("adapter.json", "extract_adapter.json"):
            try:
                res = _sync_one(t, fn, bucket)
                results.append(res)
            except Exception as e:
                logger.warning(f"  [{t}/{fn}] 例外: {e}")
                results.append({"ticker": t, "file": fn, "action": "error", "reason": str(e)[:120]})

    # 集計
    from collections import Counter
    by_action = Counter(r["action"] for r in results)
    logger.info("\n=== アクション集計 ===")
    for k, v in sorted(by_action.items(), key=lambda x: -x[1]):
        logger.info(f"  {k:35s}: {v}")

    ambiguous = [r for r in results if r["action"] in ("skip_ambiguous", "skip_equal_dt_diff_content", "error")]
    if ambiguous:
        out = ROOT / "data/logs/sync_adapters_ambiguous_20260420.csv"
        import csv as _csv
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8-sig", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=list(ambiguous[0].keys()))
            w.writeheader()
            w.writerows(ambiguous)
        logger.info(f"\n⚠️  要確認 {len(ambiguous)} 件: {out}")

    logger.info("\n完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())

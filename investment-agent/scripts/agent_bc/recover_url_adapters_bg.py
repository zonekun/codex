#!/usr/bin/env python3
"""Sync バグで破壊された GCS URL adapter.json を monthly_adapter_index.csv から復元.

問題: sync_latest_adapters_bg.py (v1/v2) が local extract adapter を
GCS adapter.json に上書きし、URL adapter を破壊したケースがある。

復旧ロジック:
 - 対象: monthly_adapter_index.csv の skip=False + type in (scrape_links/eir_api/html_table/pdf_table)
 - 現 GCS adapter.json を取得し、URL adapter 型か判定（ir_page_url 有無）
 - extract 型になっていたら CSV カラムから URL adapter を再構築して上書き
 - URL adapter のまま無傷なら何もしない

出力: `data/logs/recover_url_adapters_20260420.csv`
"""
from __future__ import annotations

import csv
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


def _now_jst() -> str:
    return datetime.now(JST).isoformat()


def _is_url_adapter(d: dict) -> bool:
    """URL adapter の判定基準: ir_page_url あり、または (url + type) あり."""
    return "ir_page_url" in d or ("url" in d and "type" in d)


def _is_extract_adapter(d: dict) -> bool:
    """extract adapter の判定基準: fields (list) あり."""
    return isinstance(d.get("fields"), list)


def _rebuild_url_adapter(row, now_str: str) -> dict:
    """index CSV の row から URL adapter dict を再構築."""
    return {
        "ticker": row["ticker"],
        "company_name": row["company_name"],
        "updated_at": now_str[:10],  # YYYY-MM-DD
        "ir_page_url": row.get("monthly_page_url", ""),
        "type": row.get("type", "scrape_links"),
        "css_selector": row.get("css_selector") or None,
        "link_text_pattern": row.get("link_text_pattern") or None,
        "link_href_pattern": row.get("link_href_pattern") or None,
        "table_selector": None,
        "status": "active",
        "skip_reason": None,
        "note": row.get("adapter_note", ""),
        "url_source": "recovered_from_index_csv",
        "last_checked": now_str[:10],
        "follow_links": False,
        "playwright_required": False,
        "recovered_at": now_str,
        "recovery_reason": "sync_bug_overwrite_20260420",
    }


def main() -> int:
    import pandas as pd
    from google.cloud import storage

    bucket = storage.Client(project="gmailpj-357912").bucket("stock_data_1930932")

    # 対象ロード: 非TDnet active な銘柄のみ
    df = pd.read_csv(ROOT / "data/monthly_adapter_index.csv",
                     encoding="utf-8-sig", dtype=str).fillna("")
    mask = (df["skip"].str.lower() == "false") & df["type"].isin(
        ["scrape_links", "eir_api", "html_table", "pdf_table"]
    )
    targets = df[mask]
    logger.info(f"復旧対象候補 (非TDnet active): {len(targets)} 銘柄")

    now = _now_jst()
    results: list[dict] = []
    stats = {
        "total": 0,
        "intact_url": 0,        # URL adapter 無傷、触らず
        "damaged_extract": 0,   # extract 型 → URL adapter 再構築
        "gcs_missing": 0,       # GCS に adapter.json 無し → 新規作成
        "ambiguous": 0,         # どちらでもない/両方持ち → スキップ
        "error": 0,
    }

    for _, row in targets.iterrows():
        ticker = row["ticker"]
        stats["total"] += 1
        blob = bucket.blob(f"monthly/meta/{ticker}/adapter.json")
        res = {"ticker": ticker, "company_name": row["company_name"], "action": "", "note": ""}
        try:
            if not blob.exists():
                # 新規作成
                new_adapter = _rebuild_url_adapter(row, now)
                blob.upload_from_string(
                    json.dumps(new_adapter, ensure_ascii=False, indent=2),
                    content_type="application/json",
                )
                stats["gcs_missing"] += 1
                res["action"] = "recovered_from_missing"
                res["note"] = "GCS無し、CSVから新規作成"
            else:
                d = json.loads(blob.download_as_text())
                if _is_url_adapter(d) and not _is_extract_adapter(d):
                    stats["intact_url"] += 1
                    res["action"] = "intact"
                    res["note"] = "URL adapter 無傷"
                elif _is_extract_adapter(d) and not _is_url_adapter(d):
                    # 破壊された → 再構築
                    new_adapter = _rebuild_url_adapter(row, now)
                    blob.upload_from_string(
                        json.dumps(new_adapter, ensure_ascii=False, indent=2),
                        content_type="application/json",
                    )
                    stats["damaged_extract"] += 1
                    res["action"] = "recovered_from_extract"
                    res["note"] = "sync で extract 型に書き換えられていた → CSV から復元"
                else:
                    stats["ambiguous"] += 1
                    res["action"] = "ambiguous_skip"
                    res["note"] = f"判定不能 keys={list(d.keys())[:5]}"
        except Exception as e:
            stats["error"] += 1
            res["action"] = "error"
            res["note"] = str(e)[:120]
            logger.warning(f"  [{ticker}] 例外: {e}")

        results.append(res)
        if stats["total"] % 50 == 0:
            logger.info(f"  進捗 {stats['total']}/{len(targets)}")

    # 出力
    out = ROOT / "data/logs/recover_url_adapters_20260420.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)

    logger.info("\n=== 復旧集計 ===")
    for k, v in stats.items():
        logger.info(f"  {k:25s}: {v}")
    logger.info(f"\n出力: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

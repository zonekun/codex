"""GCS の全アダプター情報をまとめた CSV インデックスを作成する。

毎回 GCS を個別に舐めるのではなく、このインデックス CSV を参照することで
全社のアダプター状況を一覧できる。

出力: meta/_index/monthly_adapter_index.csv
列: ticker, company_name, skip, category, monthly_page_url, adapter_note, updated_at,
    css_selector, link_text_pattern, link_href_pattern

使い方:
    PYTHONUTF8=1 python scripts/build_adapter_index.py        # 全件再構築
    PYTHONUTF8=1 python scripts/build_adapter_index.py --diff # 変更があった場合のみ更新

インデックスを読む例:
    import pandas as pd
    idx = pd.read_csv("meta/_index/monthly_adapter_index.csv")
    # ACTIVE 企業のみ
    active = idx[idx["skip"] == False]
    # SKIP 理由別集計
    idx[idx["skip"] == True].groupby("category").size()
"""

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

import urllib3
import requests as _req
from requests.adapters import HTTPAdapter as _HA
urllib3.disable_warnings()

class _NoVerify(_HA):
    def send(self, req, **kw): kw["verify"] = False; return super().send(req, **kw)

_orig = _req.Session.__init__
def _p(self, *a, **kw): _orig(self, *a, **kw); self.mount("https://", _NoVerify()); self.verify = False
_req.Session.__init__ = _p

from google.cloud import storage
from google.oauth2 import service_account

# ==========================================
# 設定
# ==========================================
GCS_BUCKET = "stock_data_1930932"
GCS_META = "monthly/meta"
KEY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "keys", "gcp-service-account.json")
OUT_CSV = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "meta" / "_index" / "monthly_adapter_index.csv"

DOWNLOAD_EXT = re.compile(r"\.(pdf|xlsx|xls|csv)(\?.*)?$", re.IGNORECASE)


def _get_gcs_client():
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return storage.Client(project="gmailpj-357912", credentials=creds)


def _classify(note: str, monthly_page_url: str) -> str:
    if monthly_page_url and "support.google.com" in monthly_page_url:
        return "bot_blocked"
    if monthly_page_url and DOWNLOAD_EXT.search(monthly_page_url):
        return "direct_file"
    if not note:
        return "unknown"
    note_lower = note.lower()
    if "eir" in note_lower or "spa" in note_lower or "javascript" in note_lower:
        return "eir_spa"
    if "リンクなし" in note or "no link" in note_lower or "0件" in note:
        return "no_links"
    if "table" in note_lower or "テーブル" in note:
        return "table_only"
    if "not_found" in note_lower or "404" in note or "not found" in note_lower:
        return "not_found"
    return "other"


def main():
    parser = argparse.ArgumentParser(description="GCS アダプターインデックス構築")
    parser.add_argument("--diff", action="store_true", help="変更があった場合のみ更新（未実装）")
    args = parser.parse_args()

    print("GCS アダプター読み込み中...", flush=True)
    gcs = _get_gcs_client()
    bucket = gcs.bucket(GCS_BUCKET)

    blobs = list(bucket.list_blobs(prefix=f"{GCS_META}/"))

    # #A 物理分離 (2026-04-20): adapter.json → url_adapter.json
    # 旧 download_adapter.json / ir_url.json は deprecated（レガシー読み取り互換のため残置）
    new_adapter_blobs = {b.name: b for b in blobs if b.name.endswith("/url_adapter.json")}
    old_adapter_blobs = [b for b in blobs if b.name.endswith("download_adapter.json")]
    ir_url_blobs = {b.name: b for b in blobs if b.name.endswith("ir_url.json")}

    print(f"  url_adapter.json: {len(new_adapter_blobs)}", flush=True)
    print(f"  旧 download_adapter.json: {len(old_adapter_blobs)}", flush=True)

    # 全ティッカーを収集
    all_tickers: set[str] = set()
    for name in new_adapter_blobs:
        parts = name.split("/")
        if len(parts) >= 3:
            all_tickers.add(parts[2])
    for blob in old_adapter_blobs:
        parts = blob.name.split("/")
        if len(parts) >= 3:
            all_tickers.add(parts[2])

    records = []
    for i, ticker in enumerate(sorted(all_tickers), 1):
        if i % 50 == 0:
            print(f"  処理中: {i}/{len(all_tickers)}...", flush=True)

        new_key = f"{GCS_META}/{ticker}/url_adapter.json"
        if new_key in new_adapter_blobs:
            # 新フォーマット
            try:
                adapter = json.loads(new_adapter_blobs[new_key].download_as_text())
            except Exception:
                continue
            status = adapter.get("status", "unknown")
            category = status if status != "active" else "active"
            if status == "needs_review":
                category = adapter.get("skip_reason", "needs_review") or "needs_review"
            records.append({
                "ticker": ticker,
                "company_name": adapter.get("company_name", ""),
                "skip": status != "active",
                "category": category,
                "monthly_page_url": adapter.get("ir_page_url", ""),
                "adapter_note": adapter.get("note", ""),
                "updated_at": adapter.get("updated_at", ""),
                "css_selector": adapter.get("css_selector", ""),
                "link_text_pattern": adapter.get("link_text_pattern", ""),
                "link_href_pattern": adapter.get("link_href_pattern", ""),
                "type": adapter.get("type", ""),
                "format": "v2",
            })
        else:
            # 旧フォーマット
            old_key = f"{GCS_META}/{ticker}/download_adapter.json"
            old_blob = next((b for b in old_adapter_blobs if b.name == old_key), None)
            if not old_blob:
                continue
            try:
                adapter = json.loads(old_blob.download_as_text())
            except Exception:
                continue

            ir_key = f"{GCS_META}/{ticker}/ir_url.json"
            monthly_page_url = ""
            if ir_key in ir_url_blobs:
                try:
                    ir_data = json.loads(ir_url_blobs[ir_key].download_as_text())
                    monthly_page_url = ir_data.get("monthly_page_url", "")
                except Exception:
                    pass

            skip = adapter.get("skip", False)
            note = adapter.get("note", "")
            category = _classify(note, monthly_page_url) if skip else "active"

            records.append({
                "ticker": ticker,
                "company_name": adapter.get("company_name", ""),
                "skip": skip,
                "category": category,
                "monthly_page_url": monthly_page_url,
                "adapter_note": note,
                "updated_at": adapter.get("updated_at", ""),
                "css_selector": adapter.get("css_selector", ""),
                "link_text_pattern": adapter.get("link_text_pattern", ""),
                "link_href_pattern": adapter.get("link_href_pattern", ""),
                "type": "",
                "format": "v1",
            })

    # ticker でソート
    records.sort(key=lambda r: r["ticker"])

    # CSV 保存
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["ticker", "company_name", "skip", "category", "monthly_page_url",
                  "adapter_note", "updated_at", "css_selector", "link_text_pattern",
                  "link_href_pattern", "type", "format"]

    import tempfile, shutil
    with tempfile.NamedTemporaryFile("w", newline="", encoding="utf-8-sig",
                                     suffix=".csv", delete=False) as tf:
        tmp_path = tf.name
        writer = csv.DictWriter(tf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    # GDrive同期中でロックされている場合はリトライ
    import time as _time
    for _i in range(10):
        try:
            shutil.move(tmp_path, str(OUT_CSV))
            break
        except PermissionError:
            _time.sleep(3)
    else:
        print(f"警告: GDriveロックが解除されず。{tmp_path} に保存しました。手動でコピーしてください。", flush=True)

    print(f"\n保存完了: {OUT_CSV}", flush=True)
    print(f"総件数: {len(records)} 社", flush=True)

    # サマリー
    from collections import Counter
    cat_counts = Counter(r["category"] for r in records)
    print("\n=== カテゴリ別集計 ===")
    for cat, cnt in sorted(cat_counts.items(), key=lambda x: -x[1]):
        marker = "✓" if cat == "active" else "✗"
        print(f"  {marker} {cat:20s}: {cnt:4d} 社")


if __name__ == "__main__":
    main()

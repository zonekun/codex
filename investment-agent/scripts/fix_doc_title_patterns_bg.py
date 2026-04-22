#!/usr/bin/env python3
"""lint で特定された破損 doc_title_pattern を NFKC 正規化で修復.

138A (全角ローマ数字 Ⅰ{4}) / 全角数字 0-9 / 全角 ASCII の典型的 typo を
adapter.json 側で修正して GCS 同期する。

使い方:
  PYTHONUTF8=1 python scripts/fix_doc_title_patterns_bg.py \
    --lint-csv data/logs/doc_title_pattern_lint_<ts>.csv \
    [--dry-run] [--no-gcs] [--min-safety 'has_no_match'] ...
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
ADAPTER_DIR = Path("data/monthly_adapters")


def log(msg: str) -> None:
    ts = datetime.now(JST).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def should_fix(issues: str, sample_match: str) -> bool:
    """修正すべきかの判定.

    - re.compile 失敗: 修正必須
    - 全角ローマ数字 (Ⅰ-Ⅻ): 修正必須（数字 typo の典型）
    - 全角数字 (０-９): 修正必須
    - 全角 ASCII (括弧 （）等): サンプルマッチしていれば意図的 → 修正しない
                                サンプル不マッチなら修正推奨
    """
    if "re.compile 失敗" in issues:
        return True
    if "全角ローマ数字" in issues:
        return True
    if "全角数字" in issues:
        return True
    # 全角 ASCII は sample_match=YES なら意図通り、NO なら修正
    if "全角 ASCII" in issues:
        return sample_match == "NO"
    if "NFKC 正規化で変化あり" in issues:
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lint-csv", required=True, help="lint 出力 CSV パス")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-gcs", action="store_true")
    args = parser.parse_args()

    with open(args.lint_csv, encoding="cp932") as f:
        rows = list(csv.DictReader(f))
    log(f"lint CSV 読込: {len(rows)} 行")

    gcs_client = None
    if not args.no_gcs and not args.dry_run:
        os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
        from google.cloud import storage
        gcs_client = storage.Client(project="stock-data-1930932")

    fix_count = 0
    skip_count = 0
    failed = []
    for r in rows:
        ticker = r["ticker"]
        issues = r.get("issues", "")
        if not issues:
            continue
        sample_match = r.get("sample_match", "")
        if not should_fix(issues, sample_match):
            skip_count += 1
            continue

        adapter_path = ADAPTER_DIR / f"{ticker}.json"
        if not adapter_path.exists():
            failed.append((ticker, "adapter not found"))
            continue

        try:
            with open(adapter_path, encoding="utf-8") as f:
                adapter = json.load(f)
            old_pattern = adapter.get("doc_title_pattern", "")
            new_pattern = unicodedata.normalize("NFKC", old_pattern)
            if old_pattern == new_pattern:
                # NFKC で変化しないがマッチしないケース等 → 手動対応
                skip_count += 1
                continue
            # re.compile 検証
            try:
                re.compile(new_pattern)
            except re.error as e:
                failed.append((ticker, f"compile fail after fix: {e}"))
                continue

            log(f"[{ticker}] {old_pattern!r}")
            log(f"        → {new_pattern!r}")

            if args.dry_run:
                fix_count += 1
                continue

            adapter["doc_title_pattern"] = new_pattern
            adapter["_doc_title_pattern_fixed_at"] = datetime.now(JST).isoformat()
            adapter["_doc_title_pattern_fixed_from"] = old_pattern

            with open(adapter_path, "w", encoding="utf-8") as f:
                json.dump(adapter, f, ensure_ascii=False, indent=2)

            if gcs_client:
                blob = gcs_client.bucket("stock_data_1930932").blob(
                    f"monthly/meta/{ticker}/extract_adapter.json",
                )
                blob.upload_from_filename(str(adapter_path), content_type="application/json")

            fix_count += 1
        except Exception as e:
            failed.append((ticker, str(e)))

    log(f"=== サマリ ===")
    log(f"  修正: {fix_count}")
    log(f"  スキップ (意図的 / NFKC 変化なし): {skip_count}")
    if failed:
        log(f"  失敗: {len(failed)}")
        for t, msg in failed[:10]:
            log(f"    {t}: {msg}")


if __name__ == "__main__":
    main()

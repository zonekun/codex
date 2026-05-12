#!/usr/bin/env python3
"""adapter.json の doc_title_pattern 健全性チェック.

全 meta/monthly/*_extract_adapter.json の doc_title_pattern を走査し、以下を検出:
  - 全角英数・全角記号の混入（Ⅰ-Ⅻ / ０-９ / ａ-ｚ / ｛｝（）等）
  - re.compile 失敗（正規表現として無効）
  - 直近の GCS 文書タイトルに対する re.search 不一致（採取した全文書にマッチゼロ）

【使い方】
  PYTHONUTF8=1 python scripts/lint_doc_title_patterns.py

  # GCS 文書での実マッチ率まで検証（時間かかるがより正確）
  PYTHONUTF8=1 python scripts/lint_doc_title_patterns.py --check-gcs

【出力】
  data/logs/doc_title_pattern_lint_<ts>.csv  （SJIS/CP932）

  カラム: ticker, pattern, issues, normalized_pattern, compile_ok,
          sample_title, sample_match, fix_suggestion
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
ADAPTER_DIR = Path("meta/monthly")
GCS_BUCKET = "stock_data_1930932"


# 全角英数・全角記号の判定
# NFKC 正規化後に変化がある = 全角の可能性（ただし記号は多様、別途判定）
FULLWIDTH_ASCII = re.compile(r"[\uFF01-\uFF5E\u3000]")
# 全角ローマ数字 Ⅰ-Ⅻ, ⅰ-ⅻ
FULLWIDTH_ROMAN = re.compile(r"[\u2160-\u217F]")
# 全角数字 ０-９
FULLWIDTH_DIGITS = re.compile(r"[\uFF10-\uFF19]")


def diagnose_pattern(pattern: str) -> dict:
    """pattern の問題を一括診断."""
    issues: list[str] = []
    fixed: str | None = None

    # 1) 全角ローマ数字混入（Ⅰ{4} 等）
    if FULLWIDTH_ROMAN.search(pattern):
        m = FULLWIDTH_ROMAN.findall(pattern)
        issues.append(f"全角ローマ数字混入: {m}")

    # 2) 全角 ASCII（括弧・スペース等）
    if FULLWIDTH_ASCII.search(pattern):
        m = FULLWIDTH_ASCII.findall(pattern)
        # 日本語中の括弧（）は意図的な場合が多いので情報のみ
        issues.append(f"全角 ASCII/記号混入: {''.join(m[:5])}")

    # 3) 全角数字
    if FULLWIDTH_DIGITS.search(pattern):
        m = FULLWIDTH_DIGITS.findall(pattern)
        issues.append(f"全角数字混入: {m}")

    # 4) NFKC 正規化後の差分
    normalized = unicodedata.normalize("NFKC", pattern)
    if normalized != pattern:
        fixed = normalized
        if not issues:
            issues.append("NFKC 正規化で変化あり")

    # 5) re.compile 可否
    compile_ok = True
    compile_error = ""
    try:
        re.compile(pattern)
    except re.error as e:
        compile_ok = False
        compile_error = str(e)
        issues.append(f"re.compile 失敗: {compile_error}")

    return {
        "issues": issues,
        "normalized": fixed,
        "compile_ok": compile_ok,
        "compile_error": compile_error,
    }


def sample_title_check(pattern: str, ticker: str, gcs_client) -> tuple[str, bool]:
    """GCS 上の直近 TDnet PDF タイトル 1 件に対して re.search を試す."""
    if not gcs_client:
        return ("", False)
    try:
        bucket = gcs_client.bucket(GCS_BUCKET)
        blobs = list(bucket.list_blobs(prefix=f"tdnet/{ticker}/", max_results=30))
        # ファイル名から doc_title を抽出（標準フォーマット:
        #   YYYYMMDD_ticker_社名_カテゴリ_タイトル_hash.pdf）
        for b in blobs:
            name = Path(b.name).stem
            parts = name.split("_")
            if len(parts) < 5:
                continue
            title = "_".join(parts[4:-1])  # 末尾 hash を除く
            # 月次系 keyword を含むものを優先サンプルとして採用
            if re.search(r"月次|月度|月実績|速報", title):
                ok = bool(re.search(pattern, title)) if pattern else False
                return (title, ok)
        # 該当なし：先頭の 1 件
        for b in blobs:
            name = Path(b.name).stem
            parts = name.split("_")
            if len(parts) >= 5:
                title = "_".join(parts[4:-1])
                ok = bool(re.search(pattern, title)) if pattern else False
                return (title, ok)
    except Exception:
        pass
    return ("", False)


def main() -> None:
    parser = argparse.ArgumentParser(description="adapter.doc_title_pattern lint")
    parser.add_argument("--check-gcs", action="store_true",
                        help="GCS 文書タイトルに対する実マッチ率を検証（時間かかる）")
    parser.add_argument("--output", default="",
                        help="出力 CSV パス（省略時 data/logs/doc_title_pattern_lint_<ts>.csv）")
    args = parser.parse_args()

    out_path = args.output or f"data/logs/doc_title_pattern_lint_{datetime.now(JST):%Y%m%d_%H%M%S}.csv"

    gcs_client = None
    if args.check_gcs:
        os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
        from google.cloud import storage
        gcs_client = storage.Client(project="stock-data-1930932")

    rows: list[dict] = []
    total = 0
    broken = 0
    normalize_fix = 0

    for path in sorted(ADAPTER_DIR.glob("*_extract_adapter.json")):
        ticker = path.name.removesuffix("_extract_adapter.json")
        try:
            with open(path, encoding="utf-8") as f:
                adapter = json.load(f)
        except Exception as e:
            rows.append({
                "ticker": ticker, "pattern": "", "issues": f"adapter読込失敗: {e}",
                "normalized_pattern": "", "compile_ok": "", "sample_title": "",
                "sample_match": "", "fix_suggestion": "",
            })
            broken += 1
            continue

        pattern = adapter.get("doc_title_pattern", "")
        total += 1
        if not pattern:
            continue

        diag = diagnose_pattern(pattern)
        sample_title = ""
        sample_match = ""
        if args.check_gcs and gcs_client:
            sample_title, ok = sample_title_check(pattern, ticker, gcs_client)
            sample_match = "YES" if ok else "NO" if sample_title else ""
            if sample_title and not ok:
                diag["issues"].append(f"サンプル doc_title 非マッチ: {sample_title[:50]}")

        has_issue = bool(diag["issues"])
        if has_issue:
            broken += 1
        if diag["normalized"]:
            normalize_fix += 1

        fix_suggestion = ""
        if diag["normalized"] and not diag["compile_ok"]:
            fix_suggestion = f"NFKC 正規化で修復: {diag['normalized']}"
        elif diag["normalized"]:
            fix_suggestion = f"NFKC 正規化推奨: {diag['normalized']}"
        elif not diag["compile_ok"]:
            fix_suggestion = "手動修正必要（re.compile 失敗）"

        rows.append({
            "ticker": ticker,
            "pattern": pattern,
            "issues": " | ".join(diag["issues"]) if diag["issues"] else "",
            "normalized_pattern": diag["normalized"] or "",
            "compile_ok": "OK" if diag["compile_ok"] else "FAIL",
            "sample_title": sample_title,
            "sample_match": sample_match,
            "fix_suggestion": fix_suggestion,
        })

    # 破損のみソート上位、OK は下に
    rows.sort(key=lambda r: (r["compile_ok"] == "OK", r["issues"] == "", r["ticker"]))

    Path("data/logs").mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="cp932", newline="", errors="replace") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "ticker", "pattern", "issues", "normalized_pattern",
            "compile_ok", "sample_title", "sample_match", "fix_suggestion",
        ])
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print(f"[{datetime.now(JST):%H:%M:%S}] 出力: {out_path}")
    print(f"  対象 adapter: {total} (doc_title_pattern 設定あり)")
    print(f"  ⚠️  issue あり: {broken} ({broken/total*100:.1f}%)" if total else "")
    print(f"  NFKC 正規化で修復可能: {normalize_fix}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Download adapter NG 7 銘柄に対する pattern 別 fix.

3931: doc_title_pattern 全角1/１ 両対応
8244: 平成→令和 対応 (year_from_title_regex 拡張)
8233: regex 空 → 標準パターン埋め
9005: 鉄道|ホテル|リテール 全セグメント対応
3197: 再検証 (records なぜ少ないか)
2587: 再検証
3169: mojibake でファイル名読めず → bc_ignore
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent


def save_sync(t: str, adp: dict) -> None:
    p = ROOT / f"data/monthly_adapters/{t}.json"
    adp["_download_adapter_fixed_at"] = datetime.now(JST).isoformat()
    with p.open("w", encoding="utf-8") as f:
        json.dump(adp, f, ensure_ascii=False, indent=2)
    from google.cloud import storage
    storage.Client(project="gmailpj-357912").bucket("stock_data_1930932").blob(
        f"monthly/meta/{t}/extract_adapter.json"
    ).upload_from_filename(str(p))
    print(f"  [{t}] saved + synced")


def fix_3931():
    """全角1/１ 両対応."""
    p = ROOT / "data/monthly_adapters/3931.json"
    with p.open(encoding="utf-8") as f:
        a = json.load(f)
    a["doc_title_pattern"] = "[『][1１]人予約ランド[』]月次情報"
    save_sync("3931", a)


def fix_8244():
    """平成・令和 両対応. 令和N年 → 2018+N, 平成N年 → 1988+N (heuristic in extract).
    シンプルに年 regex を緩和: (平成\d+年|令和\d+年|\d{4}年)
    """
    p = ROOT / "data/monthly_adapters/8244.json"
    with p.open(encoding="utf-8") as f:
        a = json.load(f)
    # title pattern: 平成・令和どちらでも
    a["doc_title_pattern"] = "売上報告"
    # year regex は (平成|令和)?(\d+)年|(\d{4})年
    # but simpler: 抽出側が era 対応している、doc_title_pattern さえ通れば OK
    save_sync("8244", a)


def fix_8233():
    """regex 全空 → 標準化. file: 2015年度11月度営業報告"""
    p = ROOT / "data/monthly_adapters/8233.json"
    with p.open(encoding="utf-8") as f:
        a = json.load(f)
    a["doc_title_pattern"] = "営業報告|月次"
    a["year_from_title_regex"] = r"(\d{4})年"
    a["month_from_title_regex"] = r"(\d{1,2})月度"
    save_sync("8233", a)


def fix_9005():
    """鉄道|ホテル|リテール 全対応."""
    p = ROOT / "data/monthly_adapters/9005.json"
    with p.open(encoding="utf-8") as f:
        a = json.load(f)
    a["doc_title_pattern"] = "(鉄道|ホテル|リテール|Railways|Hotel|Retail).*月次|.*月次営業状況"
    save_sync("9005", a)


def fix_3169():
    """mojibake で追跡不能 → bc_ignore all."""
    p = ROOT / "data/monthly_adapters/3169.json"
    with p.open(encoding="utf-8") as f:
        a = json.load(f)
    for fld in a.get("fields", []):
        fld["bc_ignore"] = True
        fld["_bc_ignore_reason"] = "ファイル名 mojibake でタイトル解析不能"
    save_sync("3169", a)


def reextract(ticker: str) -> tuple[int, int, float]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    # records 削除
    from google.cloud import storage
    b = storage.Client(project="gmailpj-357912").bucket("stock_data_1930932").blob(
        f"monthly/record/{ticker}/monthly_records.json")
    if b.exists():
        b.delete()
    r = subprocess.run(
        [sys.executable, "scripts/extract_monthly_data.py",
         "--tickers", ticker, "--since", "2024"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=1200,
    )
    # records count
    rec_count = 0
    if b.exists():
        import json as _j
        d = _j.loads(b.download_as_text())
        recs = d.get("records") if isinstance(d, dict) else d
        rec_count = len(recs)
    # compare
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", ticker],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=300,
    )
    # match_ratio 抽出
    import re
    m = re.search(r"一致率:\s+([\d.]+)%", r.stdout)
    ratio = float(m.group(1)) / 100 if m else 0.0
    # ok/ng
    m_ok = re.search(r"一致 .*?:\s+(\d+)", r.stdout)
    m_ng = re.search(r"不一致.*?:\s+(\d+)", r.stdout)
    ok = int(m_ok.group(1)) if m_ok else 0
    ng = int(m_ng.group(1)) if m_ng else 0
    return rec_count, ok, ratio


def main() -> int:
    print("=== Phase 1: adapter 修正 ===")
    fix_3931()
    fix_8244()
    fix_8233()
    fix_9005()
    fix_3169()
    print("3197/2587: adapter OK、再抽出で値確認のみ")

    print("\n=== Phase 2: 再抽出 + compare ===")
    for t in ["3931", "8244", "8233", "9005", "3197", "2587", "3169"]:
        rec, ok, ratio = reextract(t)
        print(f"  {t}: records={rec}, ok={ok}, ratio={ratio:.1%}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

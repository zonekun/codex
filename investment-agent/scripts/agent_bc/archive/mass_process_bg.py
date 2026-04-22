#!/usr/bin/env python3
"""残キューの全 ticker を一括処理.

各 ticker について:
  1. adapter 状態確認 + records 確認
  2. 明白な fix 候補（fy_corr / yoy+100 / 既 apply 効果反映のための re-extract）を適用
  3. reextract_and_compare 実行
  4. 結果を log_progress.py で記録
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent


def log(msg: str) -> None:
    print(f"[{datetime.now(JST):%H:%M:%S}] {msg}", flush=True)


def run(cmd: list[str], timeout: int = 900) -> tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    r = subprocess.run(cmd, capture_output=True, text=True, check=False,
                       env=env, cwd=str(ROOT), timeout=timeout)
    return r.returncode, r.stdout + "\n" + r.stderr


def parse_agent_result(out: str) -> dict | None:
    m = re.search(r"=== AGENT_RESULT_JSON ===\n(\{.*\})", out)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            return None
    return None


def diagnose_and_fix(ticker: str, bc_df, bucket) -> tuple[str, str]:
    """(applied_fix, note) を返す."""
    applied = []
    adapter_path = ROOT / f"data/monthly_adapters/{ticker}.json"
    if not adapter_path.exists():
        return "", "adapter 無し → skip"
    with adapter_path.open(encoding="utf-8") as f:
        adp = json.load(f)

    rec_blob = bucket.blob(f"monthly/record/{ticker}/monthly_records.json")
    if not rec_blob.exists():
        return "", "records 無し → skip"
    data = json.loads(rec_blob.download_as_text())
    records = data.get("records") if isinstance(data, dict) else data
    if not records:
        return "", "records 空"

    # ym_max が未来 → fy_corr
    ym_max = max(str(r.get("year_month", "")) for r in records)
    if ym_max > "2026-04" and not adp.get("use_fy_history_correction"):
        rc, out = run([sys.executable, "scripts/agent_bc/apply_adapter_patch.py",
                       "--ticker", ticker, "--patch", '{"use_fy_history_correction": true}',
                       "--no-snapshot"])
        if rc == 0:
            applied.append("B1:fy_corr")

    # records 値 < 50 で yoy+100 が必要な field を検出 → adapter fields に yoy_offset=100 設定
    # BC 側確認が必要なので bc_df と突合
    bc_sub = bc_df[bc_df["ticker"] == ticker]
    if len(bc_sub) == 0:
        return ",".join(applied), "BC CSV に ticker データ無し"

    yoy_fields: list[str] = []
    for rr in sorted(records, key=lambda r: str(r.get("year_month", "")), reverse=True)[:3]:
        ym = rr.get("year_month", "")
        for k, v in rr.get("fields", {}).items():
            if "前年同月比" not in k and "%" not in k:
                continue
            if k in yoy_fields:
                continue
            try:
                vf = float(v)
            except (TypeError, ValueError):
                continue
            if not (-30 <= vf <= 30):
                continue
            # BC 側検証
            hit = bc_sub[(bc_sub["field"] == k) & (bc_sub["year_month"] == ym)]
            if not len(hit):
                continue
            try:
                bcv = float(hit.iloc[0]["value"])
                if abs(vf + 100 - bcv) < 1.5:
                    yoy_fields.append(k)
            except ValueError:
                pass

    # yoy+100 適用
    for fk in yoy_fields:
        patch = json.dumps({"bc_key": fk, "yoy_offset": 100})
        rc, out = run([sys.executable, "scripts/agent_bc/apply_adapter_patch.py",
                       "--ticker", ticker, "--field-key", fk,
                       "--field-patch", patch, "--no-snapshot"])
        if rc == 0:
            applied.append(f"C:yoy+100 for {fk[:20]}")

    return ",".join(applied), f"yoy fields: {len(yoy_fields)}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--queue-csv", required=True)
    p.add_argument("--session-ts", required=True)
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()

    import pandas as pd
    from google.cloud import storage
    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")
    bc_df = pd.read_csv(ROOT / "data/csv/bc_monthly_kpi.csv",
                        encoding="utf-8-sig", dtype=str)

    df = pd.read_csv(args.queue_csv, encoding="cp932", dtype=str).fillna("")
    tickers_all = df["ticker"].drop_duplicates().tolist()
    if args.limit > 0:
        tickers_all = tickers_all[: args.limit]

    log(f"対象: {len(tickers_all)} 銘柄")
    summary = {"fixed": 0, "escalation": 0, "no_change": 0}

    for i, ticker in enumerate(tickers_all, 1):
        log(f"\n[{i}/{len(tickers_all)}] {ticker}")

        # snapshot
        run([sys.executable, "scripts/agent_bc/snapshot_adapter.py",
             "--ticker", ticker, "--save"])

        # diagnose + apply quick fixes
        applied, note = diagnose_and_fix(ticker, bc_df, bucket)
        log(f"  applied: {applied or '(none)'} | {note}")

        if not applied:
            run([sys.executable, "scripts/agent_bc/log_progress.py",
                 "--session-ts", args.session_ts, "--ticker", ticker,
                 "--status", "escalation", "--tried", "auto-diagnose",
                 "--note", note])
            summary["escalation"] += 1
            continue

        # reextract + compare
        rc, out = run([sys.executable, "scripts/agent_bc/reextract_and_compare.py",
                       "--ticker", ticker], timeout=1200)
        res = parse_agent_result(out)
        if not res:
            run([sys.executable, "scripts/agent_bc/log_progress.py",
                 "--session-ts", args.session_ts, "--ticker", ticker,
                 "--status", "error", "--applied-fix", applied,
                 "--note", "compare result 解析失敗"])
            summary["escalation"] += 1
            continue

        after = res.get("match_ratio", 0)
        log(f"  ratio: {after:.1%} (ok={res['ok']} ng={res['ng']} nodata={res['bc_nodata']})")

        if after >= 0.5:
            run([sys.executable, "scripts/agent_bc/log_progress.py",
                 "--session-ts", args.session_ts, "--ticker", ticker,
                 "--status", "fixed", "--after-ratio", str(after),
                 "--applied-fix", applied, "--note", note])
            summary["fixed"] += 1
        elif after > 0:
            run([sys.executable, "scripts/agent_bc/log_progress.py",
                 "--session-ts", args.session_ts, "--ticker", ticker,
                 "--status", "escalation", "--after-ratio", str(after),
                 "--tried", applied,
                 "--note", f"一部改善 ({after:.1%}) 残 NG は深堀要"])
            summary["escalation"] += 1
        else:
            run([sys.executable, "scripts/agent_bc/log_progress.py",
                 "--session-ts", args.session_ts, "--ticker", ticker,
                 "--status", "escalation", "--after-ratio", str(after),
                 "--tried", applied,
                 "--note", "quick fix で改善せず、要個別対応"])
            summary["escalation"] += 1

    log(f"\n=== サマリー ===\nfixed: {summary['fixed']}  escalation: {summary['escalation']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

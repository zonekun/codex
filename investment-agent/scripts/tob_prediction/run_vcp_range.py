#!/usr/bin/env python3
"""VCP スクリーナー 日付範囲連続実行 + 結果マージ.

指定期間の各営業日に screen_vcp_jp.py を実行し、
全日分の結果を scan_date 付きで 1 枚の CSV に統合する。

Usage:
    # 今週（5/19〜5/21）
    PYTHONUTF8=1 python scripts/tob_prediction/run_vcp_range.py --from 2026-05-19 --to 2026-05-21

    # 特定日だけマージ（既存 JSON から再マージ）
    PYTHONUTF8=1 python scripts/tob_prediction/run_vcp_range.py --merge-only --from 2026-05-19 --to 2026-05-21

    # 全 state 出力（デフォルトは Pre-breakout/Breakout/Early-post-breakout のみ）
    PYTHONUTF8=1 python scripts/tob_prediction/run_vcp_range.py --from 2026-05-19 --to 2026-05-21 --all-states

参照:
  docs/plans/analysis-015_insider_pattern_mismatch_20260521_205454.md
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_JST = timezone(timedelta(hours=9), "JST")
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parents[1]
_OUTPUT_DIR = _PROJECT_ROOT / "data" / "output"

# デフォルトの表示対象 state（actionable のみ）
_ACTIONABLE_STATES = {"Pre-breakout", "Breakout", "Early-post-breakout"}

# マージ CSV の出力列（順序）
_MERGE_COLS = [
    "scan_date",
    "symbol",
    "company_name",
    "execution_state",
    "composite_score",
    "price",
    "valid_vcp",
    "entry_ready",
    "distance_from_pivot_pct",
    "pattern_type",
    "quality_rating",
    "sector",
]


def business_days(date_from: date, date_to: date) -> list[date]:
    """date_from〜date_to の営業日リスト（日本祝日未考慮）を返す."""
    result = []
    d = date_from
    while d <= date_to:
        if d.weekday() < 5:  # Mon-Fri
            result.append(d)
        d += timedelta(days=1)
    return result


def run_vcp_for_date(scan_date: date, extra_args: list[str]) -> Path | None:
    """screen_vcp_jp.py を 1 日分実行し、生成された JSON ファイルを返す.

    Args:
        scan_date: 評価日。
        extra_args: screen_vcp_jp.py に渡す追加引数（例: ['--full-sp500']）。

    Returns:
        生成された JSON ファイルのパス。失敗時は None。
    """
    # 実行前に data/output/ の最新 JSON を記録しておく
    existing = {f.stat().st_mtime: f for f in _OUTPUT_DIR.glob("vcp_jp_*.json")}

    cmd = [
        sys.executable,
        str(_THIS_DIR / "screen_vcp_jp.py"),
        "--date", scan_date.isoformat(),
        *extra_args,
    ]
    print(f"\n{'='*60}")
    print(f"  実行: {scan_date}  ({' '.join(extra_args) or '(デフォルト)'})")
    print(f"{'='*60}")

    result = subprocess.run(cmd, env={**__import__("os").environ, "PYTHONUTF8": "1"})
    if result.returncode != 0:
        print(f"  WARN: {scan_date} の実行が失敗 (exit={result.returncode})")
        return None

    # 新しく生成された JSON を特定
    after = {f.stat().st_mtime: f for f in _OUTPUT_DIR.glob("vcp_jp_*.json")}
    new_mtimes = set(after.keys()) - set(existing.keys())
    if not new_mtimes:
        print(f"  WARN: {scan_date} の JSON が見つかりません")
        return None

    # 最も新しいファイルを返す
    newest_mtime = max(new_mtimes)
    return after[newest_mtime]


def load_json_results(json_path: Path, scan_date: date) -> list[dict]:
    """JSON ファイルから結果を読み込み scan_date を付与して返す.

    Args:
        json_path: vcp_jp_*.json のパス。
        scan_date: scan_date 列に設定する日付。

    Returns:
        行 dict のリスト。
    """
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  WARN: {json_path.name} の読み込み失敗: {exc}")
        return []

    rows = []
    for r in data.get("results", []):
        row = {
            "scan_date": scan_date.isoformat(),
            "symbol": r.get("symbol"),
            "company_name": r.get("company_name"),
            "execution_state": r.get("execution_state"),
            "composite_score": r.get("composite_score"),
            "price": r.get("price"),
            "valid_vcp": r.get("valid_vcp"),
            "entry_ready": r.get("entry_ready"),
            "distance_from_pivot_pct": r.get("distance_from_pivot_pct"),
            "pattern_type": r.get("pattern_type"),
            "quality_rating": r.get("quality_rating"),
            "sector": r.get("sector"),
        }
        rows.append(row)
    return rows


def find_existing_json(scan_date: date) -> Path | None:
    """data/output/ 内で scan_date に対応する vcp_jp_*.json を探す.

    metadata.api_stats.date_range の終端日が scan_date のファイルを返す。
    複数あれば最新（mtime 最大）を採用。
    """
    candidates = []
    for f in _OUTPUT_DIR.glob("vcp_jp_*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            date_range = d.get("metadata", {}).get("api_stats", {}).get("date_range", "")
            # "YYYY-MM-DD to YYYY-MM-DD" の形式
            end_str = date_range.split(" to ")[-1].strip()
            if end_str == scan_date.isoformat():
                candidates.append(f)
        except Exception:
            continue

    if not candidates:
        return None
    return max(candidates, key=lambda f: f.stat().st_mtime)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VCP スクリーナー 日付範囲連続実行 + CSV マージ"
    )
    parser.add_argument("--from", dest="date_from", required=True,
                        help="開始日 YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", required=True,
                        help="終了日 YYYY-MM-DD")
    parser.add_argument("--full-sp500", action="store_true",
                        help="TSE 全銘柄スキャン（省略時はデフォルト top100）")
    parser.add_argument("--merge-only", action="store_true",
                        help="既存 JSON のマージのみ（再実行しない）")
    parser.add_argument("--all-states", action="store_true",
                        help="全 execution_state を出力（省略時は actionable のみ）")
    parser.add_argument("--output-dir", default=str(_OUTPUT_DIR),
                        help=f"出力先 (default: {_OUTPUT_DIR})")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    date_from = date.fromisoformat(args.date_from)
    date_to = date.fromisoformat(args.date_to)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    days = business_days(date_from, date_to)
    print(f"対象営業日: {[d.isoformat() for d in days]}")

    extra_args: list[str] = []
    if args.full_sp500:
        extra_args.append("--full-sp500")

    # ─── 1. スキャン実行（--merge-only でなければ） ───────────────────────────
    json_map: dict[date, Path] = {}  # {scan_date: json_path}

    for d in days:
        if args.merge_only:
            # 既存 JSON を検索
            p = find_existing_json(d)
            if p:
                json_map[d] = p
                print(f"  {d}: 既存 JSON 使用 → {p.name}")
            else:
                print(f"  {d}: WARN — 対応 JSON なし（スキップ）")
        else:
            p = run_vcp_for_date(d, extra_args)
            if p:
                json_map[d] = p

    if not json_map:
        print("ERROR: マージ対象の JSON が 0 件です", file=sys.stderr)
        sys.exit(1)

    # ─── 2. JSON → DataFrame ─────────────────────────────────────────────────
    all_rows: list[dict] = []
    for d, p in sorted(json_map.items()):
        rows = load_json_results(p, d)
        print(f"  {d}: {len(rows)} 件ロード ({p.name})")
        all_rows.extend(rows)

    df = pd.DataFrame(all_rows)
    if df.empty:
        print("WARN: 結果が 0 件です")
        return

    # ─── 3. フィルタ ─────────────────────────────────────────────────────────
    if not args.all_states:
        df = df[df["execution_state"].isin(_ACTIONABLE_STATES)]
        print(f"  actionable フィルタ後: {len(df)} 件 (states={_ACTIONABLE_STATES})")

    # ─── 4. 整形 & ソート ────────────────────────────────────────────────────
    # 列順を揃える
    for col in _MERGE_COLS:
        if col not in df.columns:
            df[col] = None
    df = df[_MERGE_COLS].copy()

    # scan_date 昇順 → composite_score 降順
    df = df.sort_values(["scan_date", "composite_score"], ascending=[True, False])
    df["composite_score"] = df["composite_score"].round(1)

    # ─── 5. 出力 ─────────────────────────────────────────────────────────────
    ts = datetime.now(tz=_JST).strftime("%Y%m%d_%H%M%S")
    out_csv = output_dir / f"vcp_range_{date_from}_{date_to}_{ts}.csv"
    df.to_csv(str(out_csv), index=False, encoding="utf-8-sig")
    print(f"\n出力: {out_csv}")
    print(f"  行数: {len(df)}")

    # ─── 6. サマリ表示 ───────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("日付別 × execution_state サマリ")
    print("=" * 60)
    pivot = (
        df.groupby(["scan_date", "execution_state"])
        .size()
        .unstack(fill_value=0)
    )
    print(pivot.to_string())

    print()
    print("=" * 60)
    print("複数日登場銘柄（継続シグナル）")
    print("=" * 60)
    multi = (
        df.groupby("symbol")["scan_date"]
        .apply(list)
        .reset_index()
        .rename(columns={"scan_date": "dates"})
    )
    multi = multi[multi["dates"].apply(len) > 1].copy()
    if not multi.empty:
        # 銘柄名・最新スコアを付与
        latest = df.sort_values("scan_date").groupby("symbol").last()[
            ["company_name", "execution_state", "composite_score"]
        ].reset_index()
        multi = multi.merge(latest, on="symbol", how="left")
        multi["n_days"] = multi["dates"].apply(len)
        multi = multi.sort_values("n_days", ascending=False)
        for _, row in multi.iterrows():
            print(
                f"  {row['symbol']}  {row.get('company_name', '')[:12]:12s}  "
                f"{row['n_days']}日  dates={row['dates']}  "
                f"state={row['execution_state']}  score={row['composite_score']}"
            )
    else:
        print("  （複数日継続なし）")

    print()
    print("完了")


if __name__ == "__main__":
    main()
